"""Deterministic positive-baseline generation orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from copy import deepcopy
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote

from app.services.openapi.models import (
    ApiModel,
    Endpoint,
    LocalReference,
    MediaType,
    NormalizationResult,
    Parameter,
    ParameterReference,
    RequestBody,
    Response,
    Schema,
    SecurityRequirement,
    SecurityScheme,
)
from app.services.test_generation.models import (
    GeneratedBody,
    GeneratedParameterValue,
    GeneratedTestCase,
    GeneratedTestSuite,
    GenerationDiagnostic,
    ResponseExpectation,
)

_PRIMITIVE_TYPES = {"string", "integer", "number", "boolean", "null"}
_JSON_TYPES = _PRIMITIVE_TYPES | {"object", "array"}
_SCHEMA_KEYS = {
    "type", "nullable", "enum", "const", "example", "examples", "default",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems",
    "properties", "required", "items", "readOnly", "writeOnly", "title",
    "description", "deprecated", "format",
}
_COMPOSITIONS = {"allOf", "oneOf", "anyOf", "not"}

# The closed set of supported `format` values and the one schema type each
# requires. Any other format string stays an unsupported schema keyword.
_FORMAT_TYPES = {
    "int32": "integer",
    "int64": "integer",
    "float": "number",
    "double": "number",
    "date": "string",
    "date-time": "string",
    "uuid": "string",
    "email": "string",
}
_FLOAT_MAX = 3.4028234663852886e38
# Inclusive implicit numeric ranges. `double` only requires a finite value.
_FORMAT_RANGES = {
    "int32": (Decimal(-(2**31)), Decimal(2**31 - 1)),
    "int64": (Decimal(-(2**63)), Decimal(2**63 - 1)),
    "float": (Decimal(-_FLOAT_MAX), Decimal(_FLOAT_MAX)),
}
# Fixed string fallbacks, tried in order; the first that passes _matches wins.
_FORMAT_FALLBACKS = {
    "date": ("2000-01-01",),
    "date-time": ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00.000Z"),
    "uuid": ("00000000-0000-4000-8000-000000000000",),
    "email": ("user@example.com",),
}
_DATE_PATTERN = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DATE_TIME_PATTERN = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-]([0-9]{2}):([0-9]{2}))"
)
_UUID_PATTERN = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_EMAIL_LOCAL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+")
_EMAIL_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
_MAX_DEPTH = 32
_MAX_COLLECTION_SIZE = 100
_SCHEMA_REFERENCE_PREFIX = "#/components/schemas/"
_SCHEMA_REFERENCE_ANNOTATIONS = {"description", "title", "summary"}
# Component schemas for the generation call in progress. Set by
# generate_test_suite so the private schema helpers can follow local references
# lazily without changing their signatures.
_SCHEMA_COMPONENTS: ContextVar[Mapping[str, Schema]] = ContextVar(
    "_SCHEMA_COMPONENTS", default=MappingProxyType({})
)


# Deterministic resource limits. They count work, never time or memory, so the
# same input always produces the same output. Expansions are resolved local
# schema references, which are the only way a small document can describe an
# exponentially large schema graph.
_MAX_ENDPOINT_EXPANSIONS = 10_000
_MAX_SUITE_EXPANSIONS = 100_000
_MAX_ENDPOINT_CASES = 1_000
_MAX_SUITE_CASES = 50_000
_MAX_ENDPOINT_DIAGNOSTICS = 100
_MAX_SUITE_DIAGNOSTICS = 5_000
_EXPANSION_BUDGET_CODE = "generation_expansion_budget_exceeded"
_CASE_BUDGET_CODE = "generation_case_budget_exceeded"
_DIAGNOSTIC_BUDGET_CODE = "generation_diagnostic_budget_exceeded"


class _GenerationBudget:
    """Mutable counters for exactly one generate_test_suite call."""

    def __init__(self) -> None:
        self.suite_expansions = 0
        self.suite_cases = 0
        self.suite_diagnostics = 0
        self.suite_suppressed = 0
        self.begin_endpoint()

    def begin_endpoint(self) -> None:
        self.endpoint_expansions = 0
        self.endpoint_cases = 0
        self.endpoint_diagnostics = 0
        self.endpoint_suppressed = 0

    def charge_expansion(self, pointer: str, reference_uri: str) -> None:
        self.endpoint_expansions += 1
        self.suite_expansions += 1
        if self.endpoint_expansions > _MAX_ENDPOINT_EXPANSIONS:
            raise _BudgetExhausted(
                _EXPANSION_BUDGET_CODE, "endpoint", _MAX_ENDPOINT_EXPANSIONS,
                pointer, reference_uri,
            )
        if self.suite_expansions > _MAX_SUITE_EXPANSIONS:
            raise _BudgetExhausted(
                _EXPANSION_BUDGET_CODE, "suite", _MAX_SUITE_EXPANSIONS,
                pointer, reference_uri,
            )

    def charge_case(self) -> None:
        self.endpoint_cases += 1
        self.suite_cases += 1
        if self.endpoint_cases > _MAX_ENDPOINT_CASES:
            raise _BudgetExhausted(
                _CASE_BUDGET_CODE, "endpoint", _MAX_ENDPOINT_CASES, None, None
            )
        if self.suite_cases > _MAX_SUITE_CASES:
            raise _BudgetExhausted(
                _CASE_BUDGET_CODE, "suite", _MAX_SUITE_CASES, None, None
            )

    def case_counts(self) -> tuple[int, int]:
        return self.endpoint_cases, self.suite_cases

    def restore_case_counts(self, counts: tuple[int, int]) -> None:
        """Give back the cases of a discarded step; expansion work stays counted."""
        self.endpoint_cases, self.suite_cases = counts

    def admit(self, diagnostics: Any) -> list[GenerationDiagnostic]:
        """Keep ordinary diagnostics up to the limits and count the rest."""
        admitted: list[GenerationDiagnostic] = []
        for diagnostic in diagnostics:
            if self.endpoint_diagnostics >= _MAX_ENDPOINT_DIAGNOSTICS:
                self.endpoint_suppressed += 1
            elif self.suite_diagnostics >= _MAX_SUITE_DIAGNOSTICS:
                self.suite_suppressed += 1
            else:
                self.endpoint_diagnostics += 1
                self.suite_diagnostics += 1
                admitted.append(diagnostic)
        return admitted


class _BudgetExhausted(Exception):
    """A deterministic resource limit was reached; the running step is dropped."""

    def __init__(
        self,
        code: str,
        scope: str,
        limit: int,
        pointer: str | None,
        reference_uri: str | None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.scope = scope
        self.limit = limit
        self.pointer = pointer
        self.reference_uri = reference_uri


# Budget of the generate_test_suite call in progress. None outside such a call,
# so the private helpers stay usable on their own without any limit.
_BUDGET: ContextVar[_GenerationBudget | None] = ContextVar("_BUDGET", default=None)


def _charge_expansion(pointer: str, reference_uri: str) -> None:
    budget = _BUDGET.get()
    if budget is not None:
        budget.charge_expansion(pointer, reference_uri)


def _counted_case(**fields: Any) -> GeneratedTestCase:
    """Build a test case after charging the case budget."""
    budget = _BUDGET.get()
    if budget is not None:
        budget.charge_case()
    return GeneratedTestCase(**fields)


class _UnsupportedOperation(Exception):
    """An operation cannot safely produce a positive baseline in this stage."""

    def __init__(
        self, code: str, message: str, pointer: str, reference_uri: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.reference_uri = reference_uri


class _SecurityIssues(_UnsupportedOperation):
    """Every ordinary security validation failure of one endpoint, in order.

    It is an _UnsupportedOperation (the first failure), so existing handlers
    skip the endpoint; the endpoint-level handler reports all of `issues`.
    """

    def __init__(self, issues: list[_UnsupportedOperation]) -> None:
        first = issues[0]
        super().__init__(first.code, str(first), first.pointer, first.reference_uri)
        self.issues = tuple(issues)


def build_case_id(category: str, endpoint_path: str, method: str, variant_id: str) -> str:
    """Build a stable SHA-256 ID from length-prefixed UTF-8 input fields."""
    fields = (category, endpoint_path, method, variant_id)
    payload = b"".join(
        len(encoded).to_bytes(8, "big") + encoded
        for encoded in (field.encode("utf-8") for field in fields)
    )
    return f"case-{sha256(payload).hexdigest()}"


def generate_test_suite(result: NormalizationResult) -> GeneratedTestSuite:
    """Generate deterministic positive baselines and supported negative cases."""
    if not isinstance(result, NormalizationResult):
        raise TypeError("result must be a NormalizationResult")

    components_token = _SCHEMA_COMPONENTS.set(result.api.components.schemas)
    try:
        budget = _GenerationBudget()
        budget_token = _BUDGET.set(budget)
        try:
            return _generate_suite(result, budget)
        finally:
            _BUDGET.reset(budget_token)
    finally:
        _SCHEMA_COMPONENTS.reset(components_token)


def _run_step(
    budget: _GenerationBudget, step: Callable[[], Any]
) -> tuple[Any, _BudgetExhausted | None]:
    """Run one generation step all-or-nothing.

    A step returns its whole result at once. If a limit is reached, or the step
    fails, nothing it built is kept and its cases are not counted.
    """
    counts = budget.case_counts()
    try:
        return step(), None
    except _BudgetExhausted as exhausted:
        budget.restore_case_counts(counts)
        return None, exhausted
    except _UnsupportedOperation:
        budget.restore_case_counts(counts)
        raise


def _negative_steps(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[str, Callable[[], Any]], ...]:
    """The negative generation steps in their fixed order."""

    def required_request_body() -> tuple[tuple[GeneratedTestCase, ...], tuple[()]]:
        case = _required_request_body_negative_case(endpoint, positive, api)
        return ((case,) if case is not None else ()), ()

    return (
        ("required_property", lambda: _required_property_negative_cases(endpoint, positive, api)),
        ("numeric_boundary", lambda: _numeric_boundary_negative_cases(endpoint, positive, api)),
        ("wrong_type", lambda: _wrong_type_negative_cases(endpoint, positive, api)),
        ("string_boundary", lambda: _string_boundary_negative_cases(endpoint, positive, api)),
        (
            "required_parameter",
            lambda: (_required_parameter_negative_cases(endpoint, positive, api), ()),
        ),
        ("required_request_body", required_request_body),
        ("enum_const", lambda: _enum_const_negative_cases(endpoint, positive, api)),
        ("array_bounds", lambda: _array_bound_negative_cases(endpoint, positive, api)),
    )


def _generate_suite(result: NormalizationResult, budget: _GenerationBudget) -> GeneratedTestSuite:
    cases: list[GeneratedTestCase] = []
    diagnostics: list[GenerationDiagnostic] = []
    endpoints = result.api.endpoints
    for index, endpoint in enumerate(endpoints):
        budget.begin_endpoint()
        step_name = "positive_baseline"
        exhausted: _BudgetExhausted | None = None
        try:
            positive, exhausted = _run_step(
                budget, lambda: _generate_endpoint(endpoint, result.api)
            )
            if exhausted is None:
                positive, optional_diagnostics = positive
                cases.append(positive)
                diagnostics.extend(budget.admit(optional_diagnostics))
                for step_name, step in _negative_steps(endpoint, positive, result.api):
                    try:
                        step_result, exhausted = _run_step(budget, step)
                    except _UnsupportedOperation as issue:
                        # The step is dropped as a whole; independent steps go on.
                        diagnostics.extend(
                            budget.admit([_unsupported_diagnostic(issue, endpoint)])
                        )
                        continue
                    if exhausted is not None:
                        break
                    step_cases, step_diagnostics = step_result
                    cases.extend(step_cases)
                    diagnostics.extend(budget.admit(step_diagnostics))
        except _UnsupportedOperation as issue:
            # Only the positive baseline can fail here: the endpoint is skipped.
            diagnostics.extend(
                budget.admit(
                    [
                        _unsupported_diagnostic(item, endpoint)
                        for item in (
                            issue.issues if isinstance(issue, _SecurityIssues) else (issue,)
                        )
                    ]
                )
            )
        if exhausted is not None:
            diagnostics.append(
                _budget_diagnostic(
                    exhausted, step_name, endpoint, len(endpoints) - index - 1
                )
            )
        if budget.endpoint_suppressed:
            diagnostics.append(
                GenerationDiagnostic(
                    code=_DIAGNOSTIC_BUDGET_CODE,
                    message=(
                        f"The per-endpoint limit of {_MAX_ENDPOINT_DIAGNOSTICS} "
                        f"diagnostics was reached; {budget.endpoint_suppressed} "
                        "further diagnostics were not reported."
                    ),
                    endpoint_path=endpoint.path,
                    endpoint_method=endpoint.method,
                    source_pointer=_endpoint_pointer(endpoint),
                    reference_uri=None,
                )
            )
        if exhausted is not None and exhausted.scope == "suite":
            break

    if budget.suite_suppressed:
        diagnostics.append(
            GenerationDiagnostic(
                code=_DIAGNOSTIC_BUDGET_CODE,
                message=(
                    f"The per-suite limit of {_MAX_SUITE_DIAGNOSTICS} diagnostics "
                    f"was reached; {budget.suite_suppressed} further diagnostics "
                    "were not reported."
                ),
                endpoint_path=None,
                endpoint_method=None,
                source_pointer=None,
                reference_uri=None,
            )
        )

    return GeneratedTestSuite(
        api_title=result.api.info.title,
        api_version=result.api.info.version,
        openapi_version=result.api.openapi_version,
        cases=tuple(cases),
        diagnostics=tuple(diagnostics),
    )


def _unsupported_diagnostic(
    issue: _UnsupportedOperation, endpoint: Endpoint
) -> GenerationDiagnostic:
    return GenerationDiagnostic(
        code=issue.code,
        message=str(issue),
        endpoint_path=endpoint.path,
        endpoint_method=endpoint.method,
        source_pointer=issue.pointer,
        reference_uri=issue.reference_uri,
    )


def _budget_diagnostic(
    exhausted: _BudgetExhausted, step: str, endpoint: Endpoint, remaining: int
) -> GenerationDiagnostic:
    what = (
        "schema reference expansions"
        if exhausted.code == _EXPANSION_BUDGET_CODE
        else "generated test cases"
    )
    if exhausted.scope == "endpoint":
        message = (
            f"The per-endpoint limit of {exhausted.limit} {what} was reached during "
            f"the '{step}' step. That step produced no cases and the remaining "
            "steps for this endpoint were skipped."
        )
    else:
        message = (
            f"The per-suite limit of {exhausted.limit} {what} was reached during "
            f"the '{step}' step of this endpoint. That step produced no cases; the "
            f"remaining steps of this endpoint and {remaining} following "
            "endpoint(s) were not generated."
        )
    return GenerationDiagnostic(
        code=exhausted.code,
        message=message,
        endpoint_path=endpoint.path,
        endpoint_method=endpoint.method,
        source_pointer=exhausted.pointer or _endpoint_pointer(endpoint),
        reference_uri=exhausted.reference_uri,
    )


def _resolved_request_body(
    endpoint: Endpoint, api: ApiModel
) -> tuple[RequestBody, str] | None:
    """Resolve the endpoint request body for negative-case generation.

    Returns the RequestBody and the pointer of its declaration, or None when
    the endpoint has no body or it cannot be resolved. The positive baseline
    has already reported any resolution problem.
    """
    entry = endpoint.request_body
    if entry is None:
        return None
    pointer = _child(_endpoint_pointer(endpoint), "requestBody")
    try:
        resolved, declaration, _ = _resolve_request_body(entry, api, pointer)
    except _UnsupportedOperation:
        return None
    return resolved, declaration


def _required_parameter_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[GeneratedTestCase, ...]:
    """Omit each generated required non-path parameter once, in declaration order."""
    root = _endpoint_pointer(endpoint)
    cases: list[GeneratedTestCase] = []
    for index, endpoint_entry in enumerate(endpoint.parameters):
        pointer = _child(root, "parameters", str(index))
        # The positive baseline already resolved every entry successfully.
        entry, _, followed = _resolve_parameter(endpoint_entry, api, pointer)
        if not entry.required or entry.location not in {"query", "header", "cookie"}:
            continue

        # Only omit a parameter that was represented in the positive baseline.
        # Matching both fields preserves distinct parameters sharing a name.
        matches = [
            value
            for value in positive.parameters
            if value.name == entry.name and value.location == entry.location
        ]
        if len(matches) != 1:
            continue

        remaining = tuple(
            deepcopy(value)
            for value in positive.parameters
            if value is not matches[0]
        )
        variant = f"required-parameter:{entry.location}:{len(entry.name)}:{entry.name}"
        cases.append(
            _counted_case(
                case_id=build_case_id("negative", endpoint.path, endpoint.method, variant),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=remaining,
                request_body=deepcopy(positive.request_body),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    pointer,
                    *followed,
                    f"violated_constraint:required_parameter:{entry.location}:{entry.name}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint="required_parameter",
            )
        )
    return tuple(cases)


def _required_request_body_negative_case(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> GeneratedTestCase | None:
    """Omit a required request body when the positive baseline generated one."""
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or not resolved[0].required or positive.request_body is None:
        return None

    pointer = resolved[1]
    return _counted_case(
        case_id=build_case_id(
            "negative", endpoint.path, endpoint.method, "required-request-body"
        ),
        category="negative",
        path=positive.path,
        method=positive.method,
        parameters=tuple(deepcopy(value) for value in positive.parameters),
        request_body=None,
        security=deepcopy(positive.security),
        response_expectations=deepcopy(positive.response_expectations),
        provenance=(
            *positive.provenance,
            pointer,
            "violated_constraint:required_request_body",
        ),
        expected_outcome="request_rejection_status_unspecified",
        violated_constraint="required_request_body",
    )


def _required_property_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Omit each required writable object property from its positive body."""
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()
    if positive.request_body is None:
        return (), ()

    raw_schema = media.schema.raw
    schema_pointer = _child(body_pointer, "content", "application/json", "schema")
    property_paths, undeclared_required = _required_writable_property_paths(
        raw_schema, positive.request_body.value, schema_pointer=schema_pointer
    )
    if not property_paths and not undeclared_required:
        return (), ()

    root = _endpoint_pointer(endpoint)
    cases: list[GeneratedTestCase] = []
    for property_path, property_schema_pointer in property_paths:
        body_value = deepcopy(positive.request_body.value)
        target = body_value
        for name in property_path[:-1]:
            target = target[name]
        del target[property_path[-1]]

        property_reference = _json_pointer(property_path)
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative",
                    endpoint.path,
                    endpoint.method,
                    f"required-property:{property_reference}",
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=positive.parameters,
                request_body=GeneratedBody(
                    media_type="application/json", value=body_value
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    property_schema_pointer,
                    f"violated_constraint:required_property:{property_reference}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint="required_property",
                affected_property=property_reference,
            )
        )

    diagnostics: list[GenerationDiagnostic] = [
        GenerationDiagnostic(
            code="required_property_schema_missing",
            message=(
                f"Required property '{name}' has no declared property schema; "
                "no negative omission case was generated for it."
            ),
            endpoint_path=endpoint.path,
            endpoint_method=endpoint.method,
            source_pointer=pointer,
            reference_uri=None,
        )
        for name, pointer in undeclared_required
    ]
    if not any(_declares_client_error(status) for status in endpoint.responses):
        diagnostics.append(
            GenerationDiagnostic(
                code="negative_response_contract_unavailable",
                message=(
                    "The request is expected to be rejected, but the operation "
                    "declares no 4xx response; no HTTP status is inferred."
                ),
                endpoint_path=endpoint.path,
                endpoint_method=endpoint.method,
                source_pointer=_child(root, "responses"),
                reference_uri=None,
            )
        )
    return tuple(cases), tuple(diagnostics)


def _wrong_type_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Replace one present body property with a deterministic incompatible type."""
    version = api.openapi_version
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or positive.request_body is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()

    root_pointer = _child(body_pointer, "content", "application/json", "schema")
    raw_schema = media.schema.raw
    body_value = positive.request_body.value
    targets: list[tuple[tuple[str, ...], tuple[str, ...], Any, str]] = []
    diagnostics: list[GenerationDiagnostic] = []

    def report_issue(issue: _UnsupportedOperation) -> None:
        diagnostics.append(
            GenerationDiagnostic(
                code="wrong_type_unsupported_schema",
                message=str(issue),
                endpoint_path=endpoint.path,
                endpoint_method=endpoint.method,
                source_pointer=issue.pointer,
                reference_uri=issue.reference_uri,
            )
        )

    def visit(schema: Any, value: Any, path: tuple[str, ...], pointer: str) -> None:
        followed = _follow_property_schema(schema, pointer, report_issue)
        if followed is None:
            return
        schema, pointer = followed
        if not isinstance(schema, dict) or not isinstance(value, dict):
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name in sorted(properties):
            if name not in value:
                continue
            child_pointer = _child(pointer, "properties", name)
            followed_child = _follow_property_schema(
                properties[name], child_pointer, report_issue
            )
            if followed_child is None:
                continue
            child_schema, child_pointer = followed_child
            child_path = (*path, name)
            if not isinstance(child_schema, dict):
                continue

            if "type" not in child_schema:
                diagnostics.append(
                    GenerationDiagnostic(
                        code="wrong_type_schema_missing_type",
                        message=(
                            "The property schema does not declare a type; no safe "
                            "wrong-type case was generated."
                        ),
                        endpoint_path=endpoint.path,
                        endpoint_method=endpoint.method,
                        source_pointer=child_pointer,
                        reference_uri=None,
                    )
                )
            else:
                try:
                    allowed_types = _types(
                        child_schema["type"], version, _child(child_pointer, "type")
                    )
                except _UnsupportedOperation as issue:
                    diagnostics.append(
                        GenerationDiagnostic(
                            code="wrong_type_unsupported_schema",
                            message=str(issue),
                            endpoint_path=endpoint.path,
                            endpoint_method=endpoint.method,
                            source_pointer=issue.pointer,
                            reference_uri=issue.reference_uri,
                        )
                    )
                else:
                    candidate = _wrong_type_candidate(allowed_types)
                    if candidate is None:
                        diagnostics.append(
                            GenerationDiagnostic(
                                code="wrong_type_unsupported_schema",
                                message=(
                                    "No deterministic incompatible JSON type candidate "
                                    "is available for this property schema."
                                ),
                                endpoint_path=endpoint.path,
                                endpoint_method=endpoint.method,
                                source_pointer=_child(child_pointer, "type"),
                                reference_uri=None,
                            )
                        )
                    else:
                        candidate_type = _json_value_type(candidate)
                        if candidate_type not in allowed_types:
                            targets.append(
                                (child_path, allowed_types, candidate, child_pointer)
                            )

            visit(child_schema, value[name], child_path, child_pointer)

    visit(raw_schema, body_value, (), root_pointer)

    cases: list[GeneratedTestCase] = []
    for path, _, candidate, schema_pointer in targets:
        generated_value = deepcopy(body_value)
        parent = generated_value
        for name in path[:-1]:
            parent = parent[name]
        parent[path[-1]] = candidate

        property_pointer = _json_pointer(path)
        type_pointer = _child(schema_pointer, "type")
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative", endpoint.path, endpoint.method, f"wrong-type:{property_pointer}"
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=deepcopy(positive.parameters),
                request_body=GeneratedBody(
                    media_type="application/json", value=generated_value
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    schema_pointer,
                    type_pointer,
                    f"violated_constraint:type:{property_pointer}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint="type",
                affected_property=property_pointer,
            )
        )

    return tuple(cases), tuple(diagnostics)


def _enum_const_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Violate enum/const on present primitive request-body properties."""
    version = api.openapi_version
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or positive.request_body is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()

    body_schema_pointer = _child(body_pointer, "content", "application/json", "schema")
    body_value = positive.request_body.value
    targets: list[tuple[tuple[str, ...], str, str, Any]] = []
    diagnostics: list[GenerationDiagnostic] = []

    def add_diagnostic(
        code: str, message: str, pointer: str, reference_uri: str | None = None
    ) -> None:
        diagnostics.append(
            GenerationDiagnostic(
                code=code,
                message=message,
                endpoint_path=endpoint.path,
                endpoint_method=endpoint.method,
                source_pointer=pointer,
                reference_uri=reference_uri,
            )
        )

    def report_issue(issue: _UnsupportedOperation) -> None:
        add_diagnostic(
            "enum_const_unsupported_schema",
            str(issue),
            issue.pointer,
            issue.reference_uri,
        )

    def visit(
        schema: Any,
        value: Any,
        path: tuple[str, ...],
        pointer: str,
        depth: int,
    ) -> None:
        followed = _follow_property_schema(schema, pointer, report_issue)
        if followed is None:
            return
        schema, pointer = followed
        if not isinstance(schema, dict) or not isinstance(value, dict):
            return
        if depth > _MAX_DEPTH:
            add_diagnostic(
                "enum_const_unsupported_schema",
                "Schema nesting exceeds the supported depth; enum/const cases were skipped.",
                pointer,
            )
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return

        for name in sorted(properties):
            if name not in value:
                continue
            child_value = value[name]
            child_pointer = _child(pointer, "properties", name)
            followed_child = _follow_property_schema(
                properties[name], child_pointer, report_issue
            )
            if followed_child is None:
                continue
            child_schema, child_pointer = followed_child
            child_path = (*path, name)
            if not isinstance(child_schema, dict):
                continue
            if child_schema.get("readOnly") is True:
                continue

            constraints = tuple(
                keyword for keyword in ("enum", "const") if keyword in child_schema
            )
            if constraints:
                try:
                    checked_schema = _check_schema(
                        child_schema, version, child_pointer, "request", depth + 1
                    )
                except _UnsupportedOperation as issue:
                    add_diagnostic(
                        "enum_const_unsupported_schema",
                        str(issue),
                        issue.pointer,
                        issue.reference_uri,
                    )
                else:
                    if not _is_json_primitive_value(child_value):
                        for keyword in constraints:
                            add_diagnostic(
                                "enum_const_unsupported_value",
                                "Only primitive property values are supported for enum/const negative cases.",
                                _child(child_pointer, keyword),
                            )
                    elif isinstance(checked_schema, dict):
                        for keyword in constraints:
                            candidate = _enum_const_candidate(
                                checked_schema,
                                child_value,
                                keyword,
                                version,
                                child_pointer,
                            )
                            if candidate is None:
                                add_diagnostic(
                                    "enum_const_candidate_unavailable",
                                    f"No candidate was found in the deterministic same-type candidate set that violates {keyword} while satisfying the other supported constraints.",
                                    _child(child_pointer, keyword),
                                )
                            else:
                                targets.append(
                                    (child_path, child_pointer, keyword, candidate)
                                )

            if isinstance(child_value, dict):
                visit(child_schema, child_value, child_path, child_pointer, depth + 1)

    visit(media.schema.raw, body_value, (), body_schema_pointer, 0)

    cases: list[GeneratedTestCase] = []
    for path, schema_pointer, keyword, candidate in targets:
        generated_value = deepcopy(body_value)
        parent = generated_value
        for name in path[:-1]:
            parent = parent[name]
        parent[path[-1]] = candidate

        property_pointer = _json_pointer(path)
        keyword_pointer = _child(schema_pointer, keyword)
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative",
                    endpoint.path,
                    endpoint.method,
                    f"{keyword}-violation:{property_pointer}",
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=deepcopy(positive.parameters),
                request_body=GeneratedBody(
                    media_type=positive.request_body.media_type,
                    value=generated_value,
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    schema_pointer,
                    keyword_pointer,
                    f"violated_constraint:{keyword}:{property_pointer}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint=keyword,
                affected_property=property_pointer,
            )
        )

    return tuple(cases), tuple(diagnostics)


def _is_json_primitive_value(value: Any) -> bool:
    """Whether value is a JSON scalar, excluding Python-only numeric types."""
    return value is None or type(value) in {str, bool, int, float}


def _enum_const_candidate(
    schema: dict[str, Any],
    original: Any,
    keyword: str,
    version: str,
    pointer: str,
) -> Any | None:
    """Find a bounded deterministic same-type candidate violating one keyword."""
    candidate_schema = dict(schema)
    candidate_schema.pop(keyword, None)

    if keyword == "const" and isinstance(schema.get("enum"), list):
        # Every candidate below is sourced directly from enum, so membership is
        # already established; avoid _matches() rescanning the enum each time.
        candidate_schema.pop("enum", None)
        candidates = schema["enum"]
    else:
        candidates = _same_type_candidates(original)

    targeted_values = schema.get("enum") if keyword == "enum" else schema.get("const")
    for candidate in candidates:
        if type(candidate) is not type(original):
            continue
        if keyword == "enum" and isinstance(targeted_values, list) and any(
            _equal(candidate, item) for item in targeted_values
        ):
            continue
        if keyword == "const" and _equal(candidate, targeted_values):
            continue
        try:
            if _matches(candidate_schema, candidate, version, "request", 0):
                return candidate
        except _UnsupportedOperation:
            return None
    return None


def _same_type_candidates(value: Any) -> tuple[Any, ...]:
    """Return a small stable candidate set with the exact Python JSON type."""
    if type(value) is str:
        return ("", "sentinel", "negative", "enum-negative", value + "x", value + "_negative")
    if type(value) is bool:
        return (not value,)
    if type(value) is int:
        return (0, 1, -1, 2, -2, value + 1, value - 1)
    if type(value) is float:
        return (0.0, 1.0, -1.0, 0.5, -0.5, value + 1.0, value - 1.0)
    return ()


def _wrong_type_candidate(allowed_types: tuple[str, ...]) -> Any | None:
    """Return a stable JSON value outside a supported declared type set."""
    base_type = next((item for item in allowed_types if item != "null"), None)
    if base_type == "string":
        return 0
    if base_type in {"integer", "number", "boolean", "array", "object"}:
        return "wrong-type"
    if base_type is None and allowed_types == ("null",):
        return "wrong-type"
    return None


def _json_value_type(value: Any) -> str:
    """Return the JSON type for a deterministic candidate value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _string_boundary_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Generate safe minLength and maxLength violations for present properties."""
    version = api.openapi_version
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or positive.request_body is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()

    root_pointer = _child(body_pointer, "content", "application/json", "schema")
    targets: list[tuple[tuple[str, ...], str, str, str]] = []
    diagnostics: list[GenerationDiagnostic] = []

    def report_issue(issue: _UnsupportedOperation) -> None:
        diagnostics.append(
            _string_boundary_diagnostic(
                endpoint,
                "string_boundary_unsupported_schema",
                str(issue),
                issue.pointer,
                issue.reference_uri,
            )
        )

    def visit(schema: Any, value: Any, path: tuple[str, ...], pointer: str) -> None:
        followed = _follow_property_schema(schema, pointer, report_issue)
        if followed is None:
            return
        schema, pointer = followed
        if not isinstance(schema, dict) or not isinstance(value, dict):
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name in sorted(properties):
            if name not in value:
                continue
            child_pointer = _child(pointer, "properties", name)
            followed_child = _follow_property_schema(
                properties[name], child_pointer, report_issue
            )
            if followed_child is None:
                continue
            child_schema, child_pointer = followed_child
            child_path = (*path, name)
            if not isinstance(child_schema, dict):
                continue

            constraints = tuple(
                key for key in ("minLength", "maxLength") if key in child_schema
            )
            if constraints:
                if "type" not in child_schema:
                    for constraint in constraints:
                        diagnostics.append(
                            _string_boundary_diagnostic(
                                endpoint,
                                "string_boundary_type_missing",
                                "The property schema has no declared type; no string-length case was generated.",
                                _child(child_pointer, constraint),
                            )
                        )
                else:
                    try:
                        _check_schema(child_schema, version, child_pointer, "request", 0)
                        allowed_types = _types(
                            child_schema["type"], version, _child(child_pointer, "type")
                        )
                    except _UnsupportedOperation as issue:
                        for _constraint in constraints:
                            diagnostics.append(
                                _string_boundary_diagnostic(
                                    endpoint,
                                    "string_boundary_unsupported_schema",
                                    str(issue),
                                    issue.pointer,
                                    issue.reference_uri,
                                )
                            )
                    else:
                        if "string" not in allowed_types:
                            for constraint in constraints:
                                diagnostics.append(
                                    _string_boundary_diagnostic(
                                        endpoint,
                                        "string_boundary_non_string_type",
                                        "String-length constraints do not apply to this property's declared type.",
                                        _child(child_pointer, constraint),
                                    )
                                )
                        else:
                            for constraint in constraints:
                                candidate = _string_boundary_candidate(
                                    child_schema, constraint, version
                                )
                                if candidate is None:
                                    diagnostics.append(
                                        _string_boundary_diagnostic(
                                            endpoint,
                                            "string_boundary_no_safe_candidate",
                                            f"No deterministic value safely violates '{constraint}' while satisfying the other supported constraints.",
                                            _child(child_pointer, constraint),
                                        )
                                    )
                                else:
                                    targets.append(
                                        (child_path, constraint, candidate, child_pointer)
                                    )

            visit(child_schema, value[name], child_path, child_pointer)

    visit(media.schema.raw, positive.request_body.value, (), root_pointer)

    cases: list[GeneratedTestCase] = []
    for path, constraint, candidate, schema_pointer in targets:
        generated_value = deepcopy(positive.request_body.value)
        parent = generated_value
        for name in path[:-1]:
            parent = parent[name]
        parent[path[-1]] = candidate

        property_pointer = _json_pointer(path)
        constraint_pointer = _child(schema_pointer, constraint)
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative",
                    endpoint.path,
                    endpoint.method,
                    f"string-boundary:{property_pointer}:{constraint}:{len(candidate)}",
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=deepcopy(positive.parameters),
                request_body=GeneratedBody(
                    media_type="application/json", value=generated_value
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    schema_pointer,
                    constraint_pointer,
                    f"violated_constraint:{constraint}:{property_pointer}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint=constraint,
                affected_property=property_pointer,
            )
        )

    return tuple(cases), tuple(diagnostics)


def _string_boundary_candidate(
    schema: dict[str, Any], constraint: str, version: str
) -> str | None:
    """Return the first deterministic string violating one length bound only."""
    if constraint == "minLength":
        boundary = schema[constraint]
        if boundary == 0:
            return None
        candidate_length = boundary - 1
    elif constraint == "maxLength":
        boundary = schema[constraint]
        candidate_length = boundary + 1
    else:
        return None

    if "const" in schema:
        candidates = [schema["const"]]
    elif "enum" in schema:
        candidates = schema["enum"]
    else:
        if candidate_length > _MAX_COLLECTION_SIZE:
            return None
        candidates = ["x" * candidate_length]

    other_constraints_schema = {
        key: value for key, value in schema.items() if key != constraint
    }
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        if constraint == "minLength" and len(candidate) >= boundary:
            continue
        if constraint == "maxLength" and len(candidate) <= boundary:
            continue
        try:
            if _matches(other_constraints_schema, candidate, version, "request", 0):
                return candidate
        except _UnsupportedOperation:
            continue
    return None


def _string_boundary_diagnostic(
    endpoint: Endpoint,
    code: str,
    message: str,
    pointer: str,
    reference_uri: str | None = None,
) -> GenerationDiagnostic:
    return GenerationDiagnostic(
        code=code,
        message=message,
        endpoint_path=endpoint.path,
        endpoint_method=endpoint.method,
        source_pointer=pointer,
        reference_uri=reference_uri,
    )


class _ArrayItemUnavailable(Exception):
    """The array schema gives no deterministic valid item to build an array."""


def _array_bound_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Generate minItems, maxItems and uniqueItems violations for arrays present in the body.

    Only properties that already exist in the positive request body are
    considered, and array elements are not traversed.
    """
    version = api.openapi_version
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or positive.request_body is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()

    root_pointer = _child(body_pointer, "content", "application/json", "schema")
    targets: list[tuple[tuple[str, ...], str, list[Any], str]] = []
    diagnostics: list[GenerationDiagnostic] = []

    def report(
        code: str, message: str, pointer: str, reference_uri: str | None = None
    ) -> None:
        diagnostics.append(
            _array_bound_diagnostic(endpoint, code, message, pointer, reference_uri)
        )

    def report_issue(issue: _UnsupportedOperation) -> None:
        report(
            "array_bound_unsupported_schema",
            str(issue),
            issue.pointer,
            issue.reference_uri,
        )

    def array_candidates(
        schema: dict[str, Any],
        pointer: str,
        path: tuple[str, ...],
        constraints: tuple[str, ...],
    ) -> None:
        """Collect one candidate per constraint, or explain why there is none."""
        if "minItems" in schema and "maxItems" in schema and schema["minItems"] > schema["maxItems"]:
            report(
                "array_bound_contradictory_bounds",
                "minItems is greater than maxItems; no array-length case was generated.",
                pointer,
            )
            return
        item_state: list[Any] = []
        reported_issues: list[tuple[str, str | None, str]] = []

        def select_item() -> Any:
            if "items" not in schema:
                raise _ArrayItemUnavailable("The array schema declares no items schema.")
            items_pointer = _child(pointer, "items")
            item_schema = _check_schema(schema["items"], version, items_pointer, "request", 1)
            if item_schema is False:
                raise _ArrayItemUnavailable("The items schema accepts no value.")
            if item_schema is True:
                return None
            value, _ = _select_value(
                item_schema, version, [], items_pointer, direction="request"
            )
            return value

        def item() -> Any:
            # Selected at most once per property, and only when a candidate needs
            # an item. An unsupported items schema is remembered, so one root
            # cause is reported once even when both bounds need an item.
            if not item_state:
                try:
                    item_state.append(select_item())
                except _UnsupportedOperation as issue:
                    item_state.append(issue)
            if isinstance(item_state[0], _UnsupportedOperation):
                raise item_state[0]
            return item_state[0]

        for constraint in constraints:
            constraint_pointer = _child(pointer, constraint)
            if constraint == "uniqueItems":
                if schema.get("maxItems", 2) < 2:
                    report(
                        "array_bound_no_safe_candidate",
                        f"uniqueItems cannot be violated: maxItems is {schema['maxItems']}, so no array has two items.",
                        constraint_pointer,
                    )
                    continue
                # Two equal items are the smallest violation; minItems may need more.
                length = max(2, schema.get("minItems", 0))
            elif constraint == "minItems":
                if schema["minItems"] == 0:
                    report(
                        "array_bound_no_safe_candidate",
                        "minItems is 0, so no array can have fewer items.",
                        constraint_pointer,
                    )
                    continue
                length = schema["minItems"] - 1
            else:
                length = schema["maxItems"] + 1
            if length > _MAX_COLLECTION_SIZE:
                report(
                    "array_bound_no_safe_candidate",
                    f"A violating array would need {length} items, above the limit of {_MAX_COLLECTION_SIZE}.",
                    constraint_pointer,
                )
                continue
            if constraint != "uniqueItems" and schema.get("uniqueItems") is True and length > 1:
                report(
                    "array_bound_no_safe_candidate",
                    f"No deterministic array of {length} items violates '{constraint}' without also violating uniqueItems.",
                    constraint_pointer,
                )
                continue
            try:
                candidate = _array_candidate(
                    schema, constraint, length, item, version, pointer
                )
            except _ArrayItemUnavailable as issue:
                report("array_bound_no_safe_candidate", str(issue), constraint_pointer)
                continue
            except _UnsupportedOperation as issue:
                issue_key = (issue.pointer, issue.reference_uri, str(issue))
                if issue_key not in reported_issues:
                    reported_issues.append(issue_key)
                    report(
                        "array_bound_unsupported_schema",
                        str(issue),
                        issue.pointer,
                        issue.reference_uri,
                    )
                continue
            if candidate is None:
                report(
                    "array_bound_no_safe_candidate",
                    f"No deterministic array safely violates '{constraint}' while satisfying the other supported constraints.",
                    constraint_pointer,
                )
            else:
                targets.append((path, constraint, candidate, pointer))

    def target_array(
        child_schema: dict[str, Any], child_pointer: str, child_path: tuple[str, ...]
    ) -> None:
        """Validate the array keywords of one schema and collect its candidates."""
        constraints = tuple(
            key for key in ("minItems", "maxItems") if key in child_schema
        )
        if child_schema.get("uniqueItems") is True:
            constraints = (*constraints, "uniqueItems")
        if constraints:
            try:
                # Bounds and type are validated here. The items schema is
                # validated only when a candidate actually needs an item.
                _check_schema(
                    {k: v for k, v in child_schema.items() if k != "items"},
                    version,
                    child_pointer,
                    "request",
                    0,
                )
                allowed_types = (
                    _types(child_schema["type"], version, _child(child_pointer, "type"))
                    if "type" in child_schema
                    else None
                )
            except _UnsupportedOperation as issue:
                report_issue(issue)
            else:
                if allowed_types is not None and "array" not in allowed_types:
                    for constraint in constraints:
                        report(
                            "array_bound_non_array_type",
                            (
                                "uniqueItems does not apply to this property's declared type."
                                if constraint == "uniqueItems"
                                else "Array-size constraints do not apply to this property's declared type."
                            ),
                            _child(child_pointer, constraint),
                        )
                else:
                    array_candidates(child_schema, child_pointer, child_path, constraints)

    def visit(schema: Any, value: Any, path: tuple[str, ...], pointer: str) -> None:
        followed = _follow_property_schema(schema, pointer, report_issue)
        if followed is None:
            return
        schema, pointer = followed
        if not path and isinstance(schema, dict):
            # A root-level array body; nested arrays are not traversed.
            target_array(schema, pointer, path)
        if not isinstance(schema, dict) or not isinstance(value, dict):
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name in sorted(properties):
            if name not in value:
                continue
            child_pointer = _child(pointer, "properties", name)
            followed_child = _follow_property_schema(
                properties[name], child_pointer, report_issue
            )
            if followed_child is None:
                continue
            child_schema, child_pointer = followed_child
            child_path = (*path, name)
            if not isinstance(child_schema, dict):
                continue

            target_array(child_schema, child_pointer, child_path)

            visit(child_schema, value[name], child_path, child_pointer)

    visit(media.schema.raw, positive.request_body.value, (), root_pointer)

    cases: list[GeneratedTestCase] = []
    for path, constraint, candidate, schema_pointer in targets:
        if path:
            generated_value = deepcopy(positive.request_body.value)
            parent = generated_value
            for name in path[:-1]:
                parent = parent[name]
            parent[path[-1]] = candidate
        else:
            # A root-level array replaces the whole body.
            generated_value = candidate

        # The empty JSON Pointer addresses the body root itself.
        property_pointer = _json_pointer(path) if path else ""
        constraint_pointer = _child(schema_pointer, constraint)
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative",
                    endpoint.path,
                    endpoint.method,
                    f"array-bound:{property_pointer}:{constraint}:{len(candidate)}",
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=deepcopy(positive.parameters),
                request_body=GeneratedBody(
                    media_type="application/json", value=generated_value
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    schema_pointer,
                    constraint_pointer,
                    f"violated_constraint:{constraint}:{property_pointer}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint=constraint,
                affected_property=property_pointer,
            )
        )

    return tuple(cases), tuple(diagnostics)


def _array_candidate(
    schema: dict[str, Any],
    constraint: str,
    length: int,
    item: Callable[[], Any],
    version: str,
    pointer: str,
) -> list[Any] | None:
    """Return a deterministic array violating one supported array constraint."""
    if "const" in schema or "enum" in schema:
        candidates = [schema["const"]] if "const" in schema else schema["enum"]
        other_constraints_schema = {
            key: value for key, value in schema.items() if key != constraint
        }
        items_checked = False
        for candidate in candidates:
            if not isinstance(candidate, list):
                continue
            if constraint == "minItems" and len(candidate) >= schema[constraint]:
                continue
            if constraint == "maxItems" and len(candidate) <= schema[constraint]:
                continue
            if constraint == "uniqueItems" and not _has_duplicate_items(candidate):
                continue
            if "items" in schema and not items_checked:
                # _matches checks items with a generic pointer. Check it first
                # so an unsupported items schema is reported at its real
                # location; the error reaches the caller, not a silent skip.
                _check_schema(
                    schema["items"], version, _child(pointer, "items"), "request", 1
                )
                items_checked = True
            if _matches(other_constraints_schema, candidate, version, "request", 0):
                return deepcopy(candidate)
        return None
    if length == 0:
        return []
    candidate = [deepcopy(item()) for _ in range(length)]
    if constraint == "uniqueItems" and not _has_duplicate_items(candidate):
        return None
    return candidate


def _has_duplicate_items(items: list[Any]) -> bool:
    """Whether two items are equal under the JSON Schema equality of _equal."""
    return any(
        _equal(items[i], items[j])
        for i in range(len(items))
        for j in range(i + 1, len(items))
    )


def _array_bound_diagnostic(
    endpoint: Endpoint,
    code: str,
    message: str,
    pointer: str,
    reference_uri: str | None = None,
) -> GenerationDiagnostic:
    return GenerationDiagnostic(
        code=code,
        message=message,
        endpoint_path=endpoint.path,
        endpoint_method=endpoint.method,
        source_pointer=pointer,
        reference_uri=reference_uri,
    )


def _numeric_boundary_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, api: ApiModel
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Generate one negative body candidate per safely targetable numeric bound."""
    version = api.openapi_version
    resolved = _resolved_request_body(endpoint, api)
    if resolved is None or positive.request_body is None:
        return (), ()
    request_body, body_pointer = resolved
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, (dict, bool)):
        return (), ()

    schema = media.schema.raw
    root_pointer = _child(body_pointer, "content", "application/json", "schema")
    body_value = positive.request_body.value
    targets: list[tuple[tuple[str, ...], dict[str, Any], str, str]] = []
    issues: list[_UnsupportedOperation] = []
    _collect_numeric_targets(schema, (), root_pointer, targets, issues)

    cases: list[GeneratedTestCase] = []
    diagnostics: list[GenerationDiagnostic] = [
        GenerationDiagnostic(
            code=issue.code,
            message=str(issue),
            endpoint_path=endpoint.path,
            endpoint_method=endpoint.method,
            source_pointer=issue.pointer,
            reference_uri=issue.reference_uri,
        )
        for issue in issues
    ]
    for path, target_schema, schema_pointer, constraint in targets:
        keyword_pointer = _child(schema_pointer, constraint)
        try:
            _check_schema(target_schema, version, schema_pointer, "request", 0)
        except _UnsupportedOperation as issue:
            diagnostics.append(
                GenerationDiagnostic(
                    code=issue.code,
                    message=str(issue),
                    endpoint_path=endpoint.path,
                    endpoint_method=endpoint.method,
                    source_pointer=issue.pointer,
                    reference_uri=issue.reference_uri,
                )
            )
            continue
        candidate = _numeric_boundary_candidate(
            target_schema, constraint, version
        )
        if candidate is None:
            diagnostics.append(
                GenerationDiagnostic(
                    code="numeric_boundary_no_safe_candidate",
                    message=(
                        f"No deterministic numeric value was found that violates "
                        f"'{constraint}' while satisfying the other supported constraints."
                    ),
                    endpoint_path=endpoint.path,
                    endpoint_method=endpoint.method,
                    source_pointer=keyword_pointer,
                    reference_uri=None,
                )
            )
            continue

        value, candidate_key = candidate
        generated_value = deepcopy(body_value)
        parent = generated_value
        current_schema = schema
        current_schema_pointer = root_pointer
        parent_issue: _UnsupportedOperation | None = None
        for name in path[:-1]:
            try:
                current_schema, current_schema_pointer, _ = _follow_schema_reference(
                    current_schema, current_schema_pointer
                )
                properties = (
                    current_schema.get("properties")
                    if isinstance(current_schema, dict)
                    else None
                )
                child_schema, child_pointer, _ = _follow_schema_reference(
                    properties.get(name) if isinstance(properties, dict) else None,
                    _child(current_schema_pointer, "properties", name),
                )
            except _UnsupportedOperation as issue:
                parent_issue = issue
                parent = None
                break
            if not isinstance(child_schema, dict) or not isinstance(parent, dict):
                parent = None
                break
            if name not in parent:
                try:
                    child_value, _ = _select_value(
                        child_schema,
                        version,
                        [],
                        child_pointer,
                        direction="request",
                    )
                except _UnsupportedOperation as issue:
                    parent_issue = issue
                    parent = None
                    break
                if not isinstance(child_value, dict):
                    parent = None
                    break
                parent[name] = deepcopy(child_value)
            if not isinstance(parent[name], dict):
                parent = None
                break
            parent = parent[name]
            current_schema = child_schema
            current_schema_pointer = child_pointer
        if path and parent is None:
            if parent_issue is not None:
                diagnostics.append(
                    GenerationDiagnostic(
                        code=parent_issue.code,
                        message=str(parent_issue),
                        endpoint_path=endpoint.path,
                        endpoint_method=endpoint.method,
                        source_pointer=parent_issue.pointer,
                        reference_uri=parent_issue.reference_uri,
                    )
                )
                continue
            diagnostics.append(
                GenerationDiagnostic(
                    code="numeric_boundary_parent_not_generated",
                    message=(
                        "The numeric property's ancestor object could not be safely "
                        "generated; no boundary case was generated."
                    ),
                    endpoint_path=endpoint.path,
                    endpoint_method=endpoint.method,
                    source_pointer=schema_pointer,
                    reference_uri=None,
                )
            )
            continue
        if path:
            parent[path[-1]] = value
        else:
            generated_value = value

        property_pointer = _json_pointer(path) if path else ""
        cases.append(
            _counted_case(
                case_id=build_case_id(
                    "negative",
                    endpoint.path,
                    endpoint.method,
                    f"numeric-boundary:{property_pointer}:{constraint}:{candidate_key}",
                ),
                category="negative",
                path=positive.path,
                method=positive.method,
                parameters=deepcopy(positive.parameters),
                request_body=GeneratedBody(
                    media_type="application/json", value=generated_value
                ),
                security=deepcopy(positive.security),
                response_expectations=deepcopy(positive.response_expectations),
                provenance=(
                    *positive.provenance,
                    schema_pointer,
                    keyword_pointer,
                    f"violated_constraint:{constraint}:{property_pointer}",
                ),
                expected_outcome="request_rejection_status_unspecified",
                violated_constraint=constraint,
                affected_property=property_pointer,
            )
        )

    return tuple(cases), tuple(diagnostics)


def _collect_numeric_targets(
    schema: Any,
    path: tuple[str, ...],
    pointer: str,
    targets: list[tuple[tuple[str, ...], dict[str, Any], str, str]],
    issues: list[_UnsupportedOperation],
    active: tuple[str, ...] = (),
) -> None:
    """Walk object schemas in stable property order and collect numeric bounds.

    Unusable references are appended to issues. A schema already being expanded
    (a recursive reference) is not expanded again.
    """
    followed = _follow_property_schema(schema, pointer, issues.append)
    if followed is None:
        return
    schema, pointer = followed
    if not isinstance(schema, dict) or pointer in active:
        return
    schema_type = schema.get("type")
    if schema_type in ("integer", "number"):
        for constraint in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if (
                constraint == "minimum" and schema.get("exclusiveMinimum") is True
            ) or (constraint == "maximum" and schema.get("exclusiveMaximum") is True):
                continue
            if constraint in schema and not (
                constraint in ("exclusiveMinimum", "exclusiveMaximum")
                and isinstance(schema[constraint], bool)
                and schema[constraint] is False
            ):
                targets.append((path, schema, pointer, constraint))
        return

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for name in sorted(properties):
        child_pointer = _child(pointer, "properties", name)
        followed_child = _follow_property_schema(properties[name], child_pointer, issues.append)
        if followed_child is None:
            continue
        child, child_pointer = followed_child
        if not isinstance(child, dict):
            continue
        if child.get("readOnly") is True:
            continue
        child_path = (*path, name)
        if child.get("type") in ("integer", "number"):
            if child.get("readOnly") is True:
                continue
            for constraint in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                if (
                    constraint == "minimum" and child.get("exclusiveMinimum") is True
                ) or (constraint == "maximum" and child.get("exclusiveMaximum") is True):
                    continue
                if constraint in child and not (
                    constraint in ("exclusiveMinimum", "exclusiveMaximum")
                    and isinstance(child[constraint], bool)
                    and child[constraint] is False
                ):
                    targets.append((child_path, child, child_pointer, constraint))
        elif isinstance(child.get("properties"), dict):
            _collect_numeric_targets(
                child,
                child_path,
                child_pointer,
                targets,
                issues,
                (*active, pointer),
            )


def _numeric_boundary_candidate(
    schema: dict[str, Any], constraint: str, version: str
) -> tuple[int | float, str] | None:
    """Find a decimal-lattice value violating one bound and respecting the rest."""
    try:
        kind = schema.get("type")
        boundary = _active_numeric_bound(schema, constraint, version)
        if boundary is None:
            return None

        restricted_candidates: list[Any] = []
        if "const" in schema:
            restricted_candidates.append(schema["const"])
        elif "enum" in schema:
            restricted_candidates.extend(schema["enum"])
        if restricted_candidates:
            for raw_candidate in restricted_candidates:
                restricted = _as_decimal(raw_candidate)
                if restricted is None:
                    continue
                if not _violates_numeric_constraint(schema, constraint, restricted, version):
                    continue
                if not _numeric_schema_constraints_match(schema, restricted, version, constraint):
                    continue
                converted = _decimal_to_json_number(restricted, kind)
                if converted is not None:
                    return converted, _decimal_key(restricted)
            return None

        lower: tuple[Decimal, bool] | None = None
        upper: tuple[Decimal, bool] | None = None

        def add_lower(bound: Decimal, inclusive: bool) -> None:
            nonlocal lower
            if lower is None or bound > lower[0] or (bound == lower[0] and not inclusive):
                lower = (bound, inclusive)
            elif bound == lower[0] and not inclusive:
                lower = (bound, False)

        def add_upper(bound: Decimal, inclusive: bool) -> None:
            nonlocal upper
            if upper is None or bound < upper[0] or (bound == upper[0] and not inclusive):
                upper = (bound, inclusive)
            elif bound == upper[0] and not inclusive:
                upper = (bound, False)

        for key, side, strict in _effective_numeric_bounds(schema, version):
            if key == constraint:
                continue
            bound = _numeric_bound_value(schema, key, version)
            if bound is None:
                continue
            (add_lower if side == "lower" else add_upper)(bound, not strict)

        # Implicit format ranges are enforced as an "other constraint" by
        # _numeric_schema_constraints_match. The nearest violating lattice value
        # is the only candidate, so a range check on it is sufficient.
        if constraint in ("minimum", "exclusiveMinimum"):
            add_upper(boundary, inclusive=constraint == "exclusiveMinimum")
        else:
            add_lower(boundary, inclusive=constraint == "exclusiveMaximum")

        if lower is not None and upper is not None:
            if lower[0] > upper[0] or (
                lower[0] == upper[0] and not (lower[1] and upper[1])
            ):
                return None

        multiple = _as_decimal(schema.get("multipleOf"))
        if multiple is not None and multiple <= 0:
            return None
        if multiple is None:
            if kind == "integer":
                step = Decimal(1)
            else:
                precision = max(
                    (
                        _decimal_places(bound[0])
                        for bound in (lower, upper)
                        if bound is not None
                    ),
                    default=0,
                )
                step = Decimal(1).scaleb(-precision) if precision else Decimal(1)
        else:
            if kind == "integer":
                # Integer values that are multiples of p/q are spaced by p
                # when p/q is in reduced form (e.g. multiples of 0.5 include
                # every integer, while multiples of 1.5 include every 3rd).
                numerator, _ = multiple.as_integer_ratio()
                step = Decimal(abs(numerator))
            else:
                step = multiple

        # When supported constraints bound the candidate to a narrow interval,
        # its midpoint is a deterministic interior candidate. This avoids
        # choosing a precision-derived step that jumps over that interval.
        if kind == "number" and multiple is None and lower is not None and upper is not None:
            midpoint_precision = (
                len(lower[0].as_tuple().digits)
                + len(upper[0].as_tuple().digits)
                + abs(lower[0].as_tuple().exponent)
                + abs(upper[0].as_tuple().exponent)
                + 4
            )
            with localcontext() as context:
                context.prec = max(context.prec, midpoint_precision)
                value = (lower[0] + upper[0]) / 2
        elif constraint in ("minimum", "exclusiveMinimum"):
            if upper is None:
                anchor = boundary
                inclusive = constraint == "exclusiveMinimum"
            else:
                anchor, inclusive = upper
            units = _decimal_floor_units(anchor, step)
            value = _decimal_product(units, step)
            if not inclusive and value == anchor:
                value -= step

        else:
            if lower is None:
                anchor = boundary
                inclusive = constraint == "exclusiveMaximum"
            else:
                anchor, inclusive = lower
            units = _decimal_ceil_units(anchor, step)
            value = _decimal_product(units, step)
            if not inclusive and value == anchor:
                value += step

        if kind == "integer" and value != value.to_integral_value():
            return None
        if lower is not None and (value < lower[0] or (value == lower[0] and not lower[1])):
            return None
        if upper is not None and (value > upper[0] or (value == upper[0] and not upper[1])):
            return None
        if not _violates_numeric_constraint(schema, constraint, value, version):
            return None
        if not _numeric_schema_constraints_match(schema, value, version, constraint):
            return None
        converted = _decimal_to_json_number(value, kind)
        if converted is not None:
            return converted, _decimal_key(value)
        if kind == "number":
            return _float_boundary_fallback(schema, constraint, version, boundary)
        return None
    except (InvalidOperation, OverflowError, ValueError):
        return None


def _float_boundary_fallback(
    schema: dict[str, Any], constraint: str, version: str, boundary: Decimal
) -> tuple[float, str] | None:
    """Try adjacent JSON float values when the decimal lattice is not representable."""
    try:
        rounded = float(boundary)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(rounded):
        return None
    direction = -math.inf if constraint in ("minimum", "exclusiveMinimum") else math.inf
    adjacent = math.nextafter(rounded, direction)
    values = sorted(
        {rounded, adjacent}, key=lambda item: abs(Decimal(str(item)) - boundary)
    )
    for candidate in values:
        exact_value = Decimal(str(candidate))
        if not _violates_numeric_constraint(schema, constraint, exact_value, version):
            continue
        if not _numeric_schema_constraints_match(schema, exact_value, version, constraint):
            continue
        return candidate, _decimal_key(exact_value)
    return None


def _active_numeric_bound(
    schema: dict[str, Any], constraint: str, version: str
) -> Decimal | None:
    """Return the active boundary for a specific dialect-aware keyword."""
    if version.startswith("3.0."):
        base = "minimum" if constraint == "exclusiveMinimum" else (
            "maximum" if constraint == "exclusiveMaximum" else constraint
        )
        if constraint.startswith("exclusive"):
            if schema.get(constraint) is not True or base not in schema:
                return None
            return _as_decimal(schema[base])
        if base not in schema or schema.get("exclusive" + constraint.title()) is True:
            return None
        return _as_decimal(schema[base])
    bound = _as_decimal(schema.get(constraint) if constraint.startswith("exclusive") else schema.get(constraint))
    if bound is None:
        return None
    return bound


def _effective_numeric_bounds(
    schema: dict[str, Any], version: str
) -> tuple[tuple[str, str, bool], ...]:
    bounds: list[tuple[str, str, bool]] = []
    if version.startswith("3.0."):
        if "minimum" in schema:
            bounds.append(("minimum", "lower", False))
            if schema.get("exclusiveMinimum") is True:
                bounds.append(("exclusiveMinimum", "lower", True))
        if "maximum" in schema:
            bounds.append(("maximum", "upper", False))
            if schema.get("exclusiveMaximum") is True:
                bounds.append(("exclusiveMaximum", "upper", True))
    else:
        if "minimum" in schema:
            bounds.append(("minimum", "lower", False))
        if "maximum" in schema:
            bounds.append(("maximum", "upper", False))
        if "exclusiveMinimum" in schema:
            bounds.append(("exclusiveMinimum", "lower", True))
        if "exclusiveMaximum" in schema:
            bounds.append(("exclusiveMaximum", "upper", True))
    return tuple(bounds)


def _numeric_schema_constraints_match(
    schema: dict[str, Any], value: Decimal, version: str, ignored: str
) -> bool:
    kind = schema.get("type")
    if kind == "integer" and value != value.to_integral_value():
        return False
    for key, side, strict in _effective_numeric_bounds(schema, version):
        if key == ignored:
            continue
        bound = _numeric_bound_value(schema, key, version)
        if bound is None:
            continue
        if side == "lower" and (value < bound or (strict and value == bound)):
            return False
        if side == "upper" and (value > bound or (strict and value == bound)):
            return False
    multiple = _as_decimal(schema.get("multipleOf"))
    if multiple is not None and (multiple <= 0 or not _decimal_is_multiple(value, multiple)):
        return False
    format_range = _FORMAT_RANGES.get(schema.get("format"))
    if format_range is not None and not format_range[0] <= value <= format_range[1]:
        return False
    if "const" in schema:
        const = _as_decimal(schema["const"])
        if const is None or value != const:
            return False
    if "enum" in schema:
        allowed = [_as_decimal(item) for item in schema["enum"]]
        if not any(item is not None and value == item for item in allowed):
            return False
    if kind not in ("integer", "number"):
        return False
    return True


def _violates_numeric_constraint(
    schema: dict[str, Any], constraint: str, value: Decimal, version: str
) -> bool:
    bound = _active_numeric_bound(schema, constraint, version)
    if bound is None:
        return False
    if constraint == "minimum":
        return value < bound
    if constraint == "exclusiveMinimum":
        return value <= bound
    if constraint == "maximum":
        return value > bound
    return value >= bound


def _numeric_bound_value(schema: dict[str, Any], key: str, version: str) -> Decimal | None:
    if version.startswith("3.0.") and key in ("exclusiveMinimum", "exclusiveMaximum"):
        key = "minimum" if key == "exclusiveMinimum" else "maximum"
    return _as_decimal(schema.get(key))


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _decimal_places(value: Decimal) -> int:
    return max(0, -value.as_tuple().exponent)


def _decimal_floor_units(value: Decimal, step: Decimal) -> int:
    value_numerator, value_denominator = value.as_integer_ratio()
    step_numerator, step_denominator = step.as_integer_ratio()
    return (value_numerator * step_denominator) // (value_denominator * step_numerator)


def _decimal_ceil_units(value: Decimal, step: Decimal) -> int:
    value_numerator, value_denominator = value.as_integer_ratio()
    step_numerator, step_denominator = step.as_integer_ratio()
    numerator = value_numerator * step_denominator
    denominator = value_denominator * step_numerator
    return -((-numerator) // denominator)


def _decimal_product(units: int, step: Decimal) -> Decimal:
    # Size precision from both factors so Decimal's default context cannot round.
    precision = len(str(abs(units))) + len(step.as_tuple().digits) + 4
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        return Decimal(units) * step


def _decimal_is_multiple(value: Decimal, step: Decimal) -> bool:
    value_numerator, value_denominator = value.as_integer_ratio()
    step_numerator, step_denominator = step.as_integer_ratio()
    numerator = value_numerator * step_denominator
    denominator = value_denominator * step_numerator
    return denominator != 0 and numerator % denominator == 0


def _decimal_key(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _decimal_to_json_number(value: Decimal, kind: Any) -> int | float | None:
    if kind == "integer":
        return int(value) if value == value.to_integral_value() else None
    if value == value.to_integral_value():
        return int(value)
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(converted) or Decimal(str(converted)) != value:
        return None
    return converted


def _required_writable_property_paths(
    schema: Any,
    value: Any,
    path: tuple[str, ...] = (),
    *,
    schema_pointer: str,
) -> tuple[
    tuple[tuple[tuple[str, ...], str], ...],
    tuple[tuple[str, str], ...],
]:
    """Return supported omissions and required names lacking property schemas."""
    followed_root = _follow_property_schema(schema, schema_pointer, _skip_issue)
    if followed_root is None:
        return (), ()
    schema, schema_pointer = followed_root
    if not isinstance(schema, dict):
        return (), ()
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return (), ()

    found: list[tuple[tuple[str, ...], str]] = []
    undeclared: list[tuple[str, str]] = []
    for name in sorted(set(schema.get("required", []))):
        if name not in properties:
            required_index = schema["required"].index(name)
            undeclared.append(
                (name, _child(schema_pointer, "required", str(required_index)))
            )
            continue
        property_schema_pointer = _child(schema_pointer, "properties", name)
        followed_child = _follow_property_schema(
            properties[name], property_schema_pointer, _skip_issue
        )
        if followed_child is None:
            continue
        child_schema, child_schema_pointer = followed_child
        if isinstance(child_schema, dict) and child_schema.get("readOnly") is True:
            continue
        if not isinstance(value, dict) or name not in value:
            continue
        property_path = (*path, name)
        found.append((property_path, property_schema_pointer))
        child_value = value[name]
        if isinstance(child_schema, dict) and isinstance(child_value, dict):
            nested_found, nested_undeclared = _required_writable_property_paths(
                child_schema,
                child_value,
                property_path,
                schema_pointer=child_schema_pointer,
            )
            found.extend(nested_found)
            undeclared.extend(nested_undeclared)
    return tuple(found), tuple(undeclared)


def _json_pointer(path: tuple[str, ...]) -> str:
    """Format a property path as a JSON Pointer relative to the body value."""
    return "/" + "/".join(name.replace("~", "~0").replace("/", "~1") for name in path)


def _declares_client_error(status: str) -> bool:
    """Whether an operation declares an exact or ranged 4xx response."""
    return status == "4XX" or (
        len(status) == 3 and status[0] == "4" and status[1:].isdigit()
    )


_PARAMETER_REFERENCE_PREFIX = "#/components/parameters/"


def _resolve_parameter(
    entry: Parameter | ParameterReference, api: ApiModel, pointer: str
) -> tuple[Parameter, str, tuple[str, ...]]:
    """Follow local component parameter references to a concrete Parameter.

    Returns the Parameter, the pointer of its declaration (the endpoint entry
    for an inline parameter, the component for a referenced one) and the
    reference URIs followed, in order.
    """
    if isinstance(entry, Parameter):
        return entry, pointer, ()

    followed: list[str] = []
    declaration = pointer
    current: Parameter | ParameterReference = entry
    while isinstance(current, ParameterReference):
        uri = current.reference.uri
        if uri in followed:
            raise _UnsupportedOperation(
                "unsupported_parameter_reference",
                "Parameter reference cycle.",
                pointer,
                uri,
            )
        followed.append(uri)
        segment = uri[len(_PARAMETER_REFERENCE_PREFIX) :]
        if (
            not uri.startswith(_PARAMETER_REFERENCE_PREFIX)
            or not segment
            or "/" in segment
        ):
            raise _UnsupportedOperation(
                "unsupported_parameter_reference",
                "Only #/components/parameters/<name> references are supported.",
                pointer,
                uri,
            )
        name = unquote(segment).replace("~1", "/").replace("~0", "~")
        target = api.components.parameters.get(name)
        if target is None:
            raise _UnsupportedOperation(
                "unsupported_parameter_reference",
                "Parameter reference target was not found.",
                pointer,
                uri,
            )
        declaration = _pointer("components", "parameters", name)
        current = target
    return current, declaration, tuple(followed)


def _optional_parameter_issue(
    entry: Parameter, declaration: str, api: ApiModel
) -> _UnsupportedOperation | None:
    """Why an omitted optional parameter's schema is unsupported, or None.

    Only the schema form and keywords are inspected. No value or example is
    generated, because the parameter is omitted from every generated request.
    """
    try:
        if entry.schema is None or entry.content is not None:
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "A direct primitive parameter schema is required.",
                declaration,
            )
        raw, resolved_pointer, _ = _follow_schema_reference(
            entry.schema.raw, _child(declaration, "schema")
        )
        if not _is_primitive_schema(raw, api.openapi_version, resolved_pointer):
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "This stage supports direct primitive parameter schemas only.",
                resolved_pointer,
                _reference_uri(raw),
            )
        _check_schema(raw, api.openapi_version, resolved_pointer, "request", 0)
    except _UnsupportedOperation as issue:
        return issue
    return None


def _optional_parameter_diagnostic(
    entry: Parameter, issue: _UnsupportedOperation, endpoint: Endpoint
) -> GenerationDiagnostic:
    return GenerationDiagnostic(
        code="optional_parameter_unsupported_schema",
        message=(
            f"Optional {entry.location} parameter '{entry.name}' was omitted because "
            f"its schema is unsupported: {issue} Endpoint generation continued."
        ),
        endpoint_path=endpoint.path,
        endpoint_method=endpoint.method,
        source_pointer=issue.pointer,
        reference_uri=issue.reference_uri,
    )


def _generate_endpoint(
    endpoint: Endpoint, api: ApiModel
) -> tuple[GeneratedTestCase, tuple[GenerationDiagnostic, ...]]:
    root = _endpoint_pointer(endpoint)
    parameters: list[GeneratedParameterValue] = []
    optional_diagnostics: list[GenerationDiagnostic] = []
    path_names: set[str] = set()

    for index, endpoint_entry in enumerate(endpoint.parameters):
        pointer = _child(root, "parameters", str(index))
        if not isinstance(endpoint_entry, (Parameter, ParameterReference)):
            raise _UnsupportedOperation(
                "unsupported_parameter", "Unsupported parameter entry.", pointer
            )
        entry, declaration, followed = _resolve_parameter(endpoint_entry, api, pointer)
        if not entry.required and entry.location != "path":
            # Omitted by default: report an unsupported schema, but do not skip.
            issue = _optional_parameter_issue(entry, declaration, api)
            if issue is not None:
                optional_diagnostics.append(_optional_parameter_diagnostic(entry, issue, endpoint))
            continue
        if entry.schema is None or entry.content is not None:
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "A direct primitive parameter schema is required.",
                pointer,
            )

        schema_pointer = _child(declaration, "schema")
        raw, resolved_pointer, schema_followed = _follow_schema_reference(
            entry.schema.raw, schema_pointer
        )
        if not _is_primitive_schema(raw, api.openapi_version, resolved_pointer):
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "This stage supports direct primitive parameter schemas only.",
                resolved_pointer,
                _reference_uri(raw),
            )
        value, candidate_pointer = _select_value(
            raw,
            api.openapi_version,
            _parameter_examples(entry, declaration),
            resolved_pointer,
            direction="request",
        )
        parameters.append(
            GeneratedParameterValue(
                name=entry.name,
                location=entry.location,
                value=deepcopy(value),
                provenance=tuple(
                    dict.fromkeys(
                        (
                            pointer,
                            *followed,
                            schema_pointer,
                            *schema_followed,
                            resolved_pointer,
                            candidate_pointer,
                        )
                    )
                ),
            )
        )
        if entry.location == "path":
            path_names.add(entry.name)

    _check_path_placeholders(endpoint.path, path_names, _child(root, "parameters"))
    body, body_provenance = _generate_body(endpoint, api, root)
    expectations = _response_expectations(endpoint, root, api)
    security = endpoint.security if endpoint.security is not None else api.security
    _check_security_schemes(endpoint, api, root)

    case = _counted_case(
        case_id=build_case_id("positive", endpoint.path, endpoint.method, "baseline"),
        category="positive",
        path=endpoint.path,
        method=endpoint.method,
        parameters=tuple(parameters),
        request_body=body,
        security=deepcopy(security),
        response_expectations=expectations,
        provenance=(root, *body_provenance),
    )
    return case, tuple(optional_diagnostics)


_REQUEST_BODY_REFERENCE_PREFIX = "#/components/requestBodies/"


def _resolve_request_body(
    entry: RequestBody | LocalReference, api: ApiModel, pointer: str
) -> tuple[RequestBody, str, tuple[str, ...]]:
    """Follow local component request body references to a concrete RequestBody.

    Returns the RequestBody, the pointer of its declaration and the reference
    URIs followed, in order.
    """
    if isinstance(entry, RequestBody):
        return entry, pointer, ()

    followed: list[str] = []
    declaration = pointer
    current: RequestBody | LocalReference = entry
    while isinstance(current, LocalReference):
        uri = current.uri
        if uri in followed:
            raise _UnsupportedOperation(
                "unsupported_request_body_reference",
                "Request body reference cycle.",
                pointer,
                uri,
            )
        followed.append(uri)
        segment = uri[len(_REQUEST_BODY_REFERENCE_PREFIX) :]
        if (
            not uri.startswith(_REQUEST_BODY_REFERENCE_PREFIX)
            or not segment
            or "/" in segment
        ):
            raise _UnsupportedOperation(
                "unsupported_request_body_reference",
                "Only #/components/requestBodies/<name> references are supported.",
                pointer,
                uri,
            )
        name = unquote(segment).replace("~1", "/").replace("~0", "~")
        target = api.components.request_bodies.get(name)
        if target is None:
            raise _UnsupportedOperation(
                "unsupported_request_body_reference",
                "Request body reference target was not found.",
                pointer,
                uri,
            )
        declaration = _pointer("components", "requestBodies", name)
        current = target
    return current, declaration, tuple(followed)


def _generate_body(
    endpoint: Endpoint, api: ApiModel, endpoint_pointer: str
) -> tuple[GeneratedBody | None, tuple[str, ...]]:
    version = api.openapi_version
    endpoint_entry = endpoint.request_body
    if endpoint_entry is None:
        return None, ()
    endpoint_body_pointer = _child(endpoint_pointer, "requestBody")
    if not isinstance(endpoint_entry, (RequestBody, LocalReference)):
        raise _UnsupportedOperation(
            "unsupported_request_body", "Unsupported request body entry.", endpoint_body_pointer
        )
    entry, pointer, followed = _resolve_request_body(
        endpoint_entry, api, endpoint_body_pointer
    )
    if set(entry.content) != {"application/json"}:
        raise _UnsupportedOperation(
            "unsupported_request_media_type",
            "This stage requires application/json as the only request media type.",
            _child(pointer, "content"),
        )

    media = entry.content["application/json"]
    media_pointer = _child(pointer, "content", "application/json")
    if media.encoding:
        raise _UnsupportedOperation(
            "unsupported_request_encoding",
            "Media Type encoding is not supported in this stage.",
            _child(media_pointer, "encoding"),
        )
    schema_pointer = _child(media_pointer, "schema")
    raw = media.schema.raw if media.schema is not None else {}
    raw, resolved_pointer, schema_followed = _follow_schema_reference(raw, schema_pointer)
    value, candidate_pointer = _select_value(
        raw,
        version,
        _media_examples(media, media_pointer),
        resolved_pointer,
        direction="request",
    )
    return (
        GeneratedBody(media_type="application/json", value=deepcopy(value)),
        tuple(
            dict.fromkeys(
                (
                    endpoint_body_pointer,
                    *followed,
                    pointer,
                    schema_pointer,
                    *schema_followed,
                    resolved_pointer,
                    candidate_pointer,
                )
            )
        ),
    )


_RESPONSE_REFERENCE_PREFIX = "#/components/responses/"


def _resolve_response(
    entry: Response | LocalReference, api: ApiModel, pointer: str
) -> tuple[Response, tuple[str, ...]]:
    """Follow local component response references to a concrete Response.

    Returns the Response and the reference URIs followed, in order. Every
    resolved hop is charged to the expansion budget.
    """
    if isinstance(entry, Response):
        return entry, ()

    followed: list[str] = []
    current: Response | LocalReference = entry
    while isinstance(current, LocalReference):
        uri = current.uri
        if uri in followed:
            raise _UnsupportedOperation(
                "unsupported_response_reference",
                "Response reference cycle.",
                pointer,
                uri,
            )
        followed.append(uri)
        segment = uri[len(_RESPONSE_REFERENCE_PREFIX) :]
        if (
            not uri.startswith(_RESPONSE_REFERENCE_PREFIX)
            or not segment
            or "/" in segment
        ):
            raise _UnsupportedOperation(
                "unsupported_response_reference",
                "Only #/components/responses/<name> references are supported.",
                pointer,
                uri,
            )
        name = unquote(segment).replace("~1", "/").replace("~0", "~")
        target = api.components.responses.get(name)
        if target is None:
            raise _UnsupportedOperation(
                "unsupported_response_reference",
                "Response reference target was not found.",
                pointer,
                uri,
            )
        _charge_expansion(pointer, uri)
        current = target
    if not isinstance(current, Response):
        raise _UnsupportedOperation(
            "unsupported_response", "Unsupported response entry.", pointer
        )
    return current, tuple(followed)


def _response_expectations(
    endpoint: Endpoint, endpoint_pointer: str, api: ApiModel
) -> tuple[ResponseExpectation, ...]:
    expectations: list[ResponseExpectation] = []
    for status in sorted(endpoint.responses):
        pointer = _child(endpoint_pointer, "responses", status)
        entry = endpoint.responses[status]
        if not isinstance(entry, (Response, LocalReference)):
            raise _UnsupportedOperation("unsupported_response", "Unsupported response entry.", pointer)
        response, followed = _resolve_response(entry, api, pointer)
        media_types = tuple(sorted(response.content))
        expectations.append(
            ResponseExpectation(
                status_key=status,
                media_types=media_types,
                schemas_by_media_type={
                    media_type: deepcopy(response.content[media_type].schema)
                    for media_type in media_types
                },
                response_reference_uri=followed[0] if followed else None,
            )
        )
    return tuple(expectations)


_SECURITY_SCHEME_REFERENCE_PREFIX = "#/components/securitySchemes/"
_SECURITY_SCHEME_TYPES = frozenset({"apiKey", "http", "oauth2", "openIdConnect"})


def _check_security_schemes(endpoint: Endpoint, api: ApiModel, endpoint_pointer: str) -> None:
    """Validate every scheme named by the effective security requirements.

    The effective requirements are the operation's own `security` if it is not
    None, otherwise the API-level `security`. Anonymous requirements and an
    explicitly empty list name no scheme, so nothing is looked up. Every
    ordinary failure, in requirement order and then sorted name order, is
    collected (identical pointer and reference URI once) and fails the whole
    endpoint together; no alternative or scheme is dropped. An exhausted
    expansion budget is not an ordinary failure: it propagates and stops the
    check.
    """
    if endpoint.security is not None:
        requirements: tuple[SecurityRequirement, ...] = endpoint.security
        base = _child(endpoint_pointer, "security")
    elif api.security is not None:
        requirements = api.security
        base = _pointer("security")
    else:
        return
    issues: list[_UnsupportedOperation] = []
    seen: set[tuple[str, str | None]] = set()
    for index, requirement in enumerate(requirements):
        for name in sorted(requirement.schemes):
            try:
                _resolve_security_scheme(name, api, _child(base, str(index), name))
            except _UnsupportedOperation as issue:
                key = (issue.pointer, issue.reference_uri)
                if key not in seen:
                    seen.add(key)
                    issues.append(issue)
    if issues:
        raise _SecurityIssues(issues)


def _resolve_security_scheme(name: str, api: ApiModel, pointer: str) -> SecurityScheme:
    """Look up a security scheme by name and follow local references to it.

    Only the scheme's type is validated. Every successfully followed reference
    hop is charged to the expansion budget.
    """
    entry = api.components.security_schemes.get(name)
    if entry is None:
        raise _UnsupportedOperation(
            "unsupported_security_scheme_reference",
            "Security scheme is not declared in components.securitySchemes.",
            pointer,
        )

    followed: list[str] = []
    declaration = _pointer("components", "securitySchemes", name)
    current = entry
    while isinstance(current, LocalReference):
        uri = current.uri
        if uri in followed:
            raise _UnsupportedOperation(
                "unsupported_security_scheme_reference",
                "Security scheme reference cycle.",
                pointer,
                uri,
            )
        followed.append(uri)
        segment = uri[len(_SECURITY_SCHEME_REFERENCE_PREFIX) :]
        if (
            not uri.startswith(_SECURITY_SCHEME_REFERENCE_PREFIX)
            or not segment
            or "/" in segment
        ):
            raise _UnsupportedOperation(
                "unsupported_security_scheme_reference",
                "Only #/components/securitySchemes/<name> references are supported.",
                pointer,
                uri,
            )
        target_name = unquote(segment).replace("~1", "/").replace("~0", "~")
        target = api.components.security_schemes.get(target_name)
        if target is None:
            raise _UnsupportedOperation(
                "unsupported_security_scheme_reference",
                "Security scheme reference target was not found.",
                pointer,
                uri,
            )
        _charge_expansion(pointer, uri)
        declaration = _pointer("components", "securitySchemes", target_name)
        current = target

    if not isinstance(current, SecurityScheme):
        raise _UnsupportedOperation(
            "unsupported_security_scheme",
            "Unsupported security scheme entry.",
            declaration,
            followed[-1] if followed else None,
        )
    mutual_tls = current.type == "mutualTLS"
    if current.type in _SECURITY_SCHEME_TYPES or (
        mutual_tls and api.openapi_version.startswith("3.1.")
    ):
        return current
    raise _UnsupportedOperation(
        "unsupported_security_scheme",
        (
            "The mutualTLS security scheme type requires OpenAPI 3.1."
            if mutual_tls
            else f"Security scheme type '{current.type}' is not supported."
        ),
        declaration,
        followed[-1] if followed else None,
    )


def _parameter_examples(parameter: Parameter, pointer: str) -> list[tuple[Any, str]]:
    candidates: list[tuple[Any, str]] = []
    # The current Normalizer collapses absent and explicit-null example fields.
    if parameter.example is not None:
        candidates.append((parameter.example, _child(pointer, "example")))
    for name, example in sorted(parameter.examples.items()):
        candidates.append(_example_value(example, _child(pointer, "examples", name)))
    return candidates


def _media_examples(media: MediaType, pointer: str) -> list[tuple[Any, str]]:
    candidates: list[tuple[Any, str]] = []
    if media.example is not None:
        candidates.append((media.example, _child(pointer, "example")))
    for name, example in sorted(media.examples.items()):
        candidates.append(_example_value(example, _child(pointer, "examples", name)))
    return candidates


def _example_value(example: Any, pointer: str) -> tuple[Any, str]:
    if isinstance(example, LocalReference):
        raise _UnsupportedOperation(
            "unsupported_example_reference",
            "Example references are not resolved in this stage.",
            pointer,
            example.uri,
        )
    if not isinstance(example, dict) or "value" not in example:
        raise _UnsupportedOperation(
            "unsupported_example_value",
            "Only inline Example Objects containing value are supported.",
            pointer,
        )
    return example["value"], _child(pointer, "value")


def _follow_schema_reference(raw: Any, pointer: str) -> tuple[Any, str, tuple[str, ...]]:
    """Follow local #/components/schemas references to a non-reference schema.

    Returns the schema, the pointer of its declaration and the reference URIs
    followed, in order. A schema without $ref is returned unchanged. Only
    annotation keywords may sit next to $ref.
    """
    followed: list[str] = []
    components = _SCHEMA_COMPONENTS.get()
    current_pointer = pointer
    while (uri := _reference_uri(raw)) is not None:
        if uri in followed:
            raise _UnsupportedOperation(
                "unsupported_schema_reference", "Schema reference cycle.", current_pointer, uri
            )
        followed.append(uri)
        siblings = sorted(
            key
            for key in raw
            if key != "$ref"
            and key not in _SCHEMA_REFERENCE_ANNOTATIONS
            and not key.startswith("x-")
        )
        if siblings:
            raise _UnsupportedOperation(
                "unsupported_schema_reference_siblings",
                "Only description, title, summary and x- keywords are supported next to $ref.",
                _child(current_pointer, siblings[0]),
                uri,
            )
        segment = uri[len(_SCHEMA_REFERENCE_PREFIX) :]
        if not uri.startswith(_SCHEMA_REFERENCE_PREFIX) or not segment or "/" in segment:
            raise _UnsupportedOperation(
                "unsupported_schema_reference",
                "Only #/components/schemas/<name> references are supported.",
                current_pointer,
                uri,
            )
        name = unquote(segment).replace("~1", "/").replace("~0", "~")
        target = components.get(name)
        if target is None:
            raise _UnsupportedOperation(
                "unsupported_schema_reference",
                "Schema reference target was not found.",
                current_pointer,
                uri,
            )
        _charge_expansion(current_pointer, uri)
        raw = target.raw
        current_pointer = _pointer("components", "schemas", name)
    return raw, current_pointer, tuple(followed)


def _is_request_read_only(child: Any, direction: str, pointer: str) -> bool:
    """Report whether a property schema, after following $ref, is readOnly in a request."""
    if direction != "request":
        return False
    resolved, _, _ = _follow_schema_reference(child, pointer)
    return isinstance(resolved, dict) and resolved.get("readOnly") is True


def _follow_property_schema(
    schema: Any, pointer: str, report: Callable[[_UnsupportedOperation], None]
) -> tuple[Any, str] | None:
    """Follow $ref for a negative-case walker.

    An unusable reference is passed to report and the node is skipped, so one
    bad reference does not stop the other negative cases of the endpoint.
    """
    try:
        resolved, resolved_pointer, _ = _follow_schema_reference(schema, pointer)
    except _UnsupportedOperation as issue:
        report(issue)
        return None
    return resolved, resolved_pointer


def _skip_issue(issue: _UnsupportedOperation) -> None:
    """Ignore an unusable reference where no diagnostic channel exists."""


def _select_value(
    raw: Any,
    version: str,
    contextual: list[tuple[Any, str]],
    pointer: str,
    *,
    direction: str,
) -> tuple[Any, str]:
    raw, pointer, _ = _follow_schema_reference(raw, pointer)
    schema = _check_schema(raw, version, pointer, direction, 0)
    if schema is True:
        return None, pointer
    if schema is False:
        raise _UnsupportedOperation("no_positive_schema_value", "False schema admits no value.", pointer)

    candidates: list[tuple[Any, str]] = []
    if "const" in schema:
        candidates.append((schema["const"], _child(pointer, "const")))
    elif "enum" in schema:
        candidates.extend(
            (value, _child(pointer, "enum", str(index)))
            for index, value in enumerate(schema["enum"])
        )
    candidates.extend(contextual)
    if "example" in schema:
        candidates.append((schema["example"], _child(pointer, "example")))
    candidates.extend(
        (value, _child(pointer, "examples", str(index)))
        for index, value in enumerate(schema.get("examples", []))
    )
    if "default" in schema:
        candidates.append((schema["default"], _child(pointer, "default")))

    for value, candidate_pointer in candidates:
        try:
            valid = _matches(schema, value, version, direction, 0)
        except _UnsupportedOperation:
            valid = False
        if valid:
            return value, candidate_pointer
        if "const" in schema:
            break

    if "const" in schema or "enum" in schema:
        raise _UnsupportedOperation(
            "no_positive_schema_value",
            "No const or enum candidate satisfies the supported schema constraints.",
            pointer,
        )
    for value in _fallback_values(schema, version, pointer, direction):
        if _matches(schema, value, version, direction, 0):
            return value, pointer
    raise _UnsupportedOperation(
        "no_positive_schema_value",
        "No deterministic fallback satisfies the supported schema constraints.",
        pointer,
    )


def _distinct_item_values(
    raw: Any, version: str, pointer: str, direction: str, count: int
) -> list[Any]:
    """Return `count` distinct valid values for an item schema, or [] if fewer exist.

    Candidates follow the _select_value order (const/enum, example, examples,
    default, then the existing fallbacks), are validated with _matches and
    de-duplicated with _equal. No new values are invented.
    """
    raw, pointer, _ = _follow_schema_reference(raw, pointer)
    schema = _check_schema(raw, version, pointer, direction, 0)
    if schema is True or schema is False:
        return []

    candidates: list[Any] = []
    if "const" in schema:
        candidates.append(schema["const"])
    elif "enum" in schema:
        candidates.extend(schema["enum"])
    if "example" in schema:
        candidates.append(schema["example"])
    candidates.extend(schema.get("examples", []))
    if "default" in schema:
        candidates.append(schema["default"])

    values: list[Any] = []

    def add(value: Any) -> None:
        if not any(_equal(value, known) for known in values):
            values.append(value)

    for value in candidates:
        try:
            valid = _matches(schema, value, version, direction, 0)
        except _UnsupportedOperation:
            valid = False
        if valid:
            add(value)
        if len(values) >= count or "const" in schema:
            break
    if len(values) < count and "const" not in schema and "enum" not in schema:
        for value in _fallback_values(schema, version, pointer, direction):
            if _matches(schema, value, version, direction, 0):
                add(value)
            if len(values) >= count:
                break
    return values if len(values) >= count else []


def _check_schema(
    raw: Any, version: str, pointer: str, direction: str, depth: int
) -> bool | dict[str, Any]:
    if depth > _MAX_DEPTH:
        raise _UnsupportedOperation("schema_depth_limit", "Schema nesting is too deep.", pointer)
    if isinstance(raw, bool):
        if version.startswith("3.0."):
            raise _UnsupportedOperation(
                "unsupported_schema_form", "Boolean schemas are not supported by OpenAPI 3.0.", pointer
            )
        return raw
    if not isinstance(raw, dict):
        raise _UnsupportedOperation("unsupported_schema_form", "Schema must be an object or boolean.", pointer)
    if _reference_uri(raw):
        target, target_pointer, _ = _follow_schema_reference(raw, pointer)
        return _check_schema(target, version, target_pointer, direction, depth + 1)
    composition = _COMPOSITIONS.intersection(raw)
    if composition:
        key = sorted(composition)[0]
        raise _UnsupportedOperation(
            "unsupported_schema_composition", f"Schema composition '{key}' is unsupported.", _child(pointer, key)
        )
    unsupported = sorted(
        key for key in raw if key not in _SCHEMA_KEYS and not key.startswith("x-")
    )
    if unsupported:
        key = unsupported[0]
        raise _UnsupportedOperation(
            "unsupported_schema_keyword",
            f"Schema keyword '{key}' is outside the supported subset.",
            _child(pointer, key),
        )
    if "nullable" in raw and not version.startswith("3.0."):
        raise _UnsupportedOperation(
            "unsupported_schema_keyword", "nullable is interpreted only for OpenAPI 3.0.",
            _child(pointer, "nullable")
        )
    for key in ("nullable", "readOnly", "writeOnly", "uniqueItems"):
        if key in raw and not isinstance(raw[key], bool):
            raise _UnsupportedOperation(
                "unsupported_schema_form", f"Schema {key} must be boolean.", _child(pointer, key)
            )
    for key in ("minimum", "maximum", "multipleOf"):
        value = raw.get(key)
        if key in raw and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise _UnsupportedOperation(
                "unsupported_schema_form", f"Schema {key} must be numeric.", _child(pointer, key)
            )
        if key == "multipleOf" and key in raw and value <= 0:
            raise _UnsupportedOperation(
                "unsupported_schema_form", "Schema multipleOf must be positive.", _child(pointer, key)
            )
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in raw and not _bounded_int(raw[key]):
            raise _UnsupportedOperation(
                "unsupported_schema_form", f"Schema {key} must be a non-negative integer.", _child(pointer, key)
            )
    for key in ("exclusiveMinimum", "exclusiveMaximum"):
        if key not in raw:
            continue
        value = raw[key]
        valid = isinstance(value, bool) if version.startswith("3.0.") else (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        if not valid:
            raise _UnsupportedOperation(
                "unsupported_schema_form",
                f"Schema {key} has an invalid value for OpenAPI {version}.",
                _child(pointer, key),
            )
    if "type" in raw:
        _types(raw["type"], version, _child(pointer, "type"))
    if "format" in raw:
        _check_format(raw, version, pointer)
    for key in ("enum", "examples"):
        if key in raw and not isinstance(raw[key], list):
            raise _UnsupportedOperation("unsupported_schema_form", f"Schema {key} must be an array.", _child(pointer, key))
    properties = raw.get("properties", {})
    required = raw.get("required", [])
    if not isinstance(properties, dict):
        raise _UnsupportedOperation("unsupported_schema_form", "properties must be an object.", _child(pointer, "properties"))
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise _UnsupportedOperation("unsupported_schema_form", "required must be an array of names.", _child(pointer, "required"))
    for name in sorted(set(required)):
        if name not in properties:
            # JSON Schema allows a required name without a sibling property schema.
            # It is present-but-unconstrained, so keep it in positive data and
            # report that no targeted omission case can be derived for it.
            continue
        child = properties[name]
        if _is_request_read_only(child, direction, _child(pointer, "properties", name)):
            continue
        _check_schema(child, version, _child(pointer, "properties", name), direction, depth + 1)
    if "items" in raw and raw.get("minItems", 0) > 0:
        _check_schema(raw["items"], version, _child(pointer, "items"), direction, depth + 1)
    return raw


def _check_format(raw: dict[str, Any], version: str, pointer: str) -> None:
    """Validate a `format` keyword: a supported string format on its own type."""
    format_pointer = _child(pointer, "format")
    name = raw["format"]
    if not isinstance(name, str):
        raise _UnsupportedOperation(
            "unsupported_schema_form", "Schema format must be a string.", format_pointer
        )
    expected = _FORMAT_TYPES.get(name)
    if expected is None:
        raise _UnsupportedOperation(
            "unsupported_schema_keyword",
            "Schema keyword 'format' is outside the supported subset.",
            format_pointer,
        )
    declared = (
        next((item for item in _types(raw["type"], version, _child(pointer, "type")) if item != "null"), None)
        if "type" in raw
        else None
    )
    if declared != expected:
        raise _UnsupportedOperation(
            "unsupported_schema_form",
            f"Schema format '{name}' requires the {expected} type.",
            format_pointer,
        )


def _types(value: Any, version: str, pointer: str) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, list) and version.startswith("3.1.") and all(isinstance(item, str) for item in value):
        values = tuple(value)
    else:
        raise _UnsupportedOperation("unsupported_schema_form", "Unsupported schema type declaration.", pointer)
    if not values or any(item not in _JSON_TYPES for item in values):
        raise _UnsupportedOperation("unsupported_schema_type", "Unsupported schema JSON type.", pointer)
    if version.startswith("3.0.") and "null" in values:
        raise _UnsupportedOperation(
            "unsupported_schema_type", "The null type is not supported by OpenAPI 3.0.", pointer
        )
    if sum(item != "null" for item in values) > 1:
        raise _UnsupportedOperation("unsupported_schema_type_union", "Only one non-null type is supported.", pointer)
    return values


def _is_primitive_schema(raw: Any, version: str, pointer: str) -> bool:
    if not isinstance(raw, dict) or "type" not in raw:
        return False
    return all(item in _PRIMITIVE_TYPES for item in _types(raw["type"], version, _child(pointer, "type")))


def _matches(schema: dict[str, Any], value: Any, version: str, direction: str, depth: int) -> bool:
    if depth > _MAX_DEPTH:
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    types = _types(schema["type"], version, "/type") if "type" in schema else ()
    if value is None:
        return not types or "null" in types or (version.startswith("3.0.") and schema.get("nullable") is True)
    base_type = next((item for item in types if item != "null"), None)
    if base_type and not _has_type(value, base_type):
        return False
    if types and base_type is None:
        # A schema whose only type is "null" admits no non-null value.
        return False
    if "const" in schema and not _equal(value, schema["const"]):
        return False
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        return False
    if "format" in schema and not _format_matches(schema["format"], value):
        return False

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not _number_matches(schema, value, version):
            return False
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            return False
        if schema.get("uniqueItems") is True and any(
            _equal(value[i], value[j]) for i in range(len(value)) for j in range(i + 1, len(value))
        ):
            return False
        # An empty array has no item to check, so an unsupported `items` schema
        # must not reject it (_check_schema also skips `items` when minItems is 0).
        if "items" in schema and value:
            item_schema = _check_schema(schema["items"], version, "/items", direction, depth + 1)
            for item in value:
                if item_schema is False or (isinstance(item_schema, dict) and not _matches(item_schema, item, version, direction, depth + 1)):
                    return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            child = properties.get(name)
            if _is_request_read_only(child, direction, f"/properties/{name}"):
                continue
            if name not in value:
                return False
        for name, child_value in value.items():
            child = properties.get(name)
            if child is None:
                continue
            if _is_request_read_only(child, direction, f"/properties/{name}"):
                return False
            child_schema = _check_schema(child, version, f"/properties/{name}", direction, depth + 1)
            if child_schema is False or (isinstance(child_schema, dict) and not _matches(child_schema, child_value, version, direction, depth + 1)):
                return False
    return True


def _format_matches(name: Any, value: Any) -> bool:
    """Check a non-null value against one supported format; others are annotations."""
    expected = _FORMAT_TYPES.get(name) if isinstance(name, str) else None
    if expected is None:
        return True
    if expected == "string":
        if not isinstance(value, str):
            return False
        if name == "date":
            return _valid_date(value)
        if name == "date-time":
            return _valid_date_time(value)
        if name == "uuid":
            return _UUID_PATTERN.fullmatch(value) is not None
        return _valid_email(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if name == "double":
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if name == "float" and isinstance(value, float) and not math.isfinite(value):
        return False
    low, high = _FORMAT_RANGES[name]
    return low <= Decimal(value) <= high


def _valid_date(value: str) -> bool:
    match = _DATE_PATTERN.fullmatch(value)
    if match is None:
        return False
    try:
        date(*(int(part) for part in match.groups()))
    except ValueError:
        return False
    return True


def _valid_date_time(value: str) -> bool:
    match = _DATE_TIME_PATTERN.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    try:
        date(year, month, day)
    except ValueError:
        return False
    if hour > 23 or minute > 59 or second > 59:
        return False
    offset_hour, offset_minute = match.groups()[6:]
    return offset_hour is None or (int(offset_hour) <= 23 and int(offset_minute) <= 59)


def _valid_email(value: str) -> bool:
    local, separator, domain = value.partition("@")
    if not separator or "@" in domain:
        return False
    if _EMAIL_LOCAL_PATTERN.fullmatch(local) is None:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(
        _EMAIL_LABEL_PATTERN.fullmatch(label) is not None for label in labels
    )


def _fallback_values(schema: dict[str, Any], version: str, pointer: str, direction: str) -> tuple[Any, ...]:
    types = _types(schema["type"], version, _child(pointer, "type")) if "type" in schema else ()
    kind = next((item for item in types if item != "null"), None)
    if kind is None:
        if "properties" in schema or "required" in schema:
            kind = "object"
        elif "items" in schema or "minItems" in schema:
            kind = "array"
        elif "minLength" in schema or "maxLength" in schema:
            kind = "string"
        elif any(k in schema for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")):
            kind = "number"
    if kind == "string" and schema.get("format") in _FORMAT_FALLBACKS:
        return _FORMAT_FALLBACKS[schema["format"]]
    if kind == "string":
        minimum, maximum = schema.get("minLength", 0), schema.get("maxLength", 8)
        if not _bounded_int(minimum) or not _bounded_int(maximum) or minimum > maximum or minimum > _MAX_COLLECTION_SIZE:
            return ()
        preferred = "sentinel" if minimum <= 8 else "x" * minimum
        return (preferred[:maximum], "x" * minimum, "")
    if kind in {"integer", "number"}:
        candidates: list[int | float] = [0, 1, -1]
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            bound = schema.get(key)
            if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                candidates.extend((bound, bound - 1, bound + 1))
                if kind == "integer":
                    candidates.extend((math.floor(bound), math.ceil(bound)))
        multiple = schema.get("multipleOf")
        if isinstance(multiple, (int, float)) and not isinstance(multiple, bool) and multiple > 0:
            candidates.extend((multiple, -multiple, multiple * 2, -multiple * 2))
        if kind == "integer":
            if isinstance(multiple, (int, float)) and not isinstance(multiple, bool) and multiple > 0:
                # Integer multiples of a reduced decimal p/q lie on the p-step
                # lattice. Include lattice points around each bound so the
                # fallback can find a valid value even when p itself is far
                # from the constrained interval.
                multiple_decimal = Decimal(str(multiple))
                numerator, _ = multiple_decimal.as_integer_ratio()
                lattice_step = Decimal(abs(numerator))
                candidates.extend((int(lattice_step), -int(lattice_step)))
                for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                    bound = _as_decimal(schema.get(key))
                    if bound is None:
                        continue
                    floor_units = _decimal_floor_units(bound, lattice_step)
                    ceil_units = _decimal_ceil_units(bound, lattice_step)
                    for units in sorted(
                        {
                            floor_units - 1,
                            floor_units,
                            floor_units + 1,
                            ceil_units - 1,
                            ceil_units,
                            ceil_units + 1,
                        }
                    ):
                        candidates.append(units * int(lattice_step))
            candidates = [int(value) for value in candidates if float(value).is_integer()]
        return tuple(dict.fromkeys(candidates))
    if kind == "boolean":
        return (False, True)
    if kind == "null":
        return (None,)
    if kind == "array":
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems", max(minimum, 1))
        if not _bounded_int(minimum) or not _bounded_int(maximum) or minimum > maximum or minimum > _MAX_COLLECTION_SIZE:
            return ()
        if minimum == 0:
            return ([],)
        if "items" not in schema:
            return ()
        item_schema = _check_schema(schema["items"], version, _child(pointer, "items"), direction, 1)
        unique = minimum > 1 and schema.get("uniqueItems") is True
        if item_schema is True:
            if unique:
                return ()
            item = None
        elif item_schema is False:
            return ()
        elif unique:
            values = _distinct_item_values(
                item_schema, version, _child(pointer, "items"), direction, minimum
            )
            return (values,) if values else ()
        else:
            item, _ = _select_value(item_schema, version, [], _child(pointer, "items"), direction=direction)
        return ([item] * minimum,)
    if kind == "object":
        properties = schema.get("properties", {})
        value: dict[str, Any] = {}
        for name in sorted(set(schema.get("required", []))):
            if name not in properties:
                value[name] = None
                continue
            child = properties[name]
            if _is_request_read_only(child, direction, _child(pointer, "properties", name)):
                continue
            child_value, _ = _select_value(
                child, version, [], _child(pointer, "properties", name), direction=direction
            )
            value[name] = child_value
        return (value,)
    return (None, {}, [], False, 0, "")


def _number_matches(schema: dict[str, Any], value: int | float, version: str) -> bool:
    if isinstance(value, bool):
        return False
    low, high = schema.get("minimum"), schema.get("maximum")
    ex_low, ex_high = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    if isinstance(low, (int, float)) and (value < low or (version.startswith("3.0.") and ex_low is True and value == low)):
        return False
    if isinstance(high, (int, float)) and (value > high or (version.startswith("3.0.") and ex_high is True and value == high)):
        return False
    if not version.startswith("3.0."):
        if isinstance(ex_low, (int, float)) and not isinstance(ex_low, bool) and value <= ex_low:
            return False
        if isinstance(ex_high, (int, float)) and not isinstance(ex_high, bool) and value >= ex_high:
            return False
    multiple = schema.get("multipleOf")
    if multiple is not None:
        try:
            if Decimal(str(value)) % Decimal(str(multiple)) != 0:
                return False
        except (InvalidOperation, ZeroDivisionError):
            return False
    return True


def _has_type(value: Any, expected: str) -> bool:
    return {
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "null": lambda: value is None,
    }[expected]()


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _bounded_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_path_placeholders(path: str, names: set[str], pointer: str) -> None:
    placeholders = _path_placeholders(path)
    if placeholders is None:
        raise _UnsupportedOperation("invalid_path_template", "Path template braces are malformed.", pointer)
    missing = sorted(set(placeholders) - names)
    if missing:
        raise _UnsupportedOperation(
            "missing_path_parameter",
            "Path placeholders have no generated required parameter: " + ", ".join(missing) + ".",
            pointer,
        )


def _path_placeholders(path: str) -> tuple[str, ...] | None:
    result: list[str] = []
    index = 0
    while index < len(path):
        opening = path.find("{", index)
        closing_before = path.find("}", index)
        if closing_before >= 0 and (opening < 0 or closing_before < opening):
            return None
        if opening < 0:
            break
        closing = path.find("}", opening + 1)
        if closing < 0 or "{" in path[opening + 1 : closing] or closing == opening + 1:
            return None
        result.append(path[opening + 1 : closing])
        index = closing + 1
    return tuple(result)


def _is_primitive_schema(raw: Any, version: str, pointer: str) -> bool:
    if not isinstance(raw, dict) or "type" not in raw:
        return False
    return all(
        item in _PRIMITIVE_TYPES
        for item in _types(raw["type"], version, _child(pointer, "type"))
    )


def _reference_uri(raw: Any) -> str | None:
    return raw.get("$ref") if isinstance(raw, dict) and isinstance(raw.get("$ref"), str) else None


def _endpoint_pointer(endpoint: Endpoint) -> str:
    return _pointer("paths", endpoint.path, endpoint.method)


def _pointer(*parts: str) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _child(base: str, *parts: str) -> str:
    return base.rstrip("/") + _pointer(*parts)
