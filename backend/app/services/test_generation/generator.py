"""Deterministic positive-baseline generation orchestration."""

from __future__ import annotations

from copy import deepcopy
import math
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any

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
    "description", "deprecated",
}
_COMPOSITIONS = {"allOf", "oneOf", "anyOf", "not"}
_MAX_DEPTH = 32
_MAX_COLLECTION_SIZE = 100


class _UnsupportedOperation(Exception):
    """An operation cannot safely produce a positive baseline in this stage."""

    def __init__(
        self, code: str, message: str, pointer: str, reference_uri: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.reference_uri = reference_uri


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

    cases: list[GeneratedTestCase] = []
    diagnostics: list[GenerationDiagnostic] = []
    for endpoint in result.api.endpoints:
        try:
            positive = _generate_endpoint(endpoint, result.api)
            cases.append(positive)
            negative_cases, negative_diagnostics = _required_property_negative_cases(
                endpoint, positive
            )
            cases.extend(negative_cases)
            diagnostics.extend(negative_diagnostics)
            numeric_cases, numeric_diagnostics = _numeric_boundary_negative_cases(
                endpoint, positive, result.api.openapi_version
            )
            cases.extend(numeric_cases)
            diagnostics.extend(numeric_diagnostics)
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

    return GeneratedTestSuite(
        api_title=result.api.info.title,
        api_version=result.api.info.version,
        openapi_version=result.api.openapi_version,
        cases=tuple(cases),
        diagnostics=tuple(diagnostics),
    )


def _required_property_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Omit each required writable object property from its positive body."""
    request_body = endpoint.request_body
    if not isinstance(request_body, RequestBody):
        return (), ()
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, dict):
        return (), ()
    if positive.request_body is None:
        return (), ()

    raw_schema = media.schema.raw
    schema_pointer = _child(
        _endpoint_pointer(endpoint),
        "requestBody",
        "content",
        "application/json",
        "schema",
    )
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
            GeneratedTestCase(
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


def _numeric_boundary_negative_cases(
    endpoint: Endpoint, positive: GeneratedTestCase, version: str
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Generate one negative body candidate per safely targetable numeric bound."""
    request_body = endpoint.request_body
    if not isinstance(request_body, RequestBody) or positive.request_body is None:
        return (), ()
    if set(request_body.content) != {"application/json"}:
        return (), ()
    media = request_body.content["application/json"]
    if media.schema is None or not isinstance(media.schema.raw, (dict, bool)):
        return (), ()

    schema = media.schema.raw
    root_pointer = _child(
        _endpoint_pointer(endpoint), "requestBody", "content", "application/json", "schema"
    )
    body_value = positive.request_body.value
    targets: list[tuple[tuple[str, ...], dict[str, Any], str, str]] = []
    _collect_numeric_targets(schema, (), root_pointer, targets)

    cases: list[GeneratedTestCase] = []
    diagnostics: list[GenerationDiagnostic] = []
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
            child_schema = current_schema.get("properties", {}).get(name)
            if not isinstance(child_schema, dict) or not isinstance(parent, dict):
                parent = None
                break
            if name not in parent:
                try:
                    child_value, _ = _select_value(
                        child_schema,
                        version,
                        [],
                        _child(current_schema_pointer, "properties", name),
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
            current_schema_pointer = _child(current_schema_pointer, "properties", name)
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
            GeneratedTestCase(
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
) -> None:
    """Walk object schemas in stable property order and collect numeric bounds."""
    if not isinstance(schema, dict):
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
        child = properties[name]
        if not isinstance(child, dict):
            continue
        if child.get("readOnly") is True:
            continue
        child_path = (*path, name)
        child_pointer = _child(pointer, "properties", name)
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
        child_schema = properties[name]
        if isinstance(child_schema, dict) and child_schema.get("readOnly") is True:
            continue
        if not isinstance(value, dict) or name not in value:
            continue
        property_path = (*path, name)
        property_schema_pointer = _child(schema_pointer, "properties", name)
        found.append((property_path, property_schema_pointer))
        child_value = value[name]
        if isinstance(child_schema, dict) and isinstance(child_value, dict):
            nested_found, nested_undeclared = _required_writable_property_paths(
                child_schema,
                child_value,
                property_path,
                schema_pointer=property_schema_pointer,
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


def _generate_endpoint(endpoint: Endpoint, api: ApiModel) -> GeneratedTestCase:
    root = _endpoint_pointer(endpoint)
    parameters: list[GeneratedParameterValue] = []
    path_names: set[str] = set()

    for index, entry in enumerate(endpoint.parameters):
        pointer = _child(root, "parameters", str(index))
        if isinstance(entry, ParameterReference):
            raise _UnsupportedOperation(
                "unsupported_parameter_reference",
                "This stage supports concrete inline parameters only.",
                pointer,
                entry.reference.uri,
            )
        if not isinstance(entry, Parameter):
            raise _UnsupportedOperation(
                "unsupported_parameter", "Unsupported parameter entry.", pointer
            )
        if entry.schema is None or entry.content is not None:
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "A direct primitive parameter schema is required.",
                pointer,
            )

        schema_pointer = _child(pointer, "schema")
        raw = entry.schema.raw
        if not _is_primitive_schema(raw, api.openapi_version, schema_pointer):
            raise _UnsupportedOperation(
                "unsupported_parameter_schema",
                "This stage supports direct primitive parameter schemas only.",
                schema_pointer,
                _reference_uri(raw),
            )
        value, candidate_pointer = _select_value(
            raw,
            api.openapi_version,
            _parameter_examples(entry, pointer),
            schema_pointer,
            direction="request",
        )
        if entry.required or entry.location == "path":
            parameters.append(
                GeneratedParameterValue(
                    name=entry.name,
                    location=entry.location,
                    value=deepcopy(value),
                    provenance=tuple(dict.fromkeys((pointer, schema_pointer, candidate_pointer))),
                )
            )
            if entry.location == "path":
                path_names.add(entry.name)

    _check_path_placeholders(endpoint.path, path_names, _child(root, "parameters"))
    body, body_provenance = _generate_body(endpoint, api.openapi_version, root)
    expectations = _response_expectations(endpoint, root)
    security = endpoint.security if endpoint.security is not None else api.security

    return GeneratedTestCase(
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


def _generate_body(
    endpoint: Endpoint, version: str, endpoint_pointer: str
) -> tuple[GeneratedBody | None, tuple[str, ...]]:
    entry = endpoint.request_body
    if entry is None:
        return None, ()
    pointer = _child(endpoint_pointer, "requestBody")
    if isinstance(entry, LocalReference):
        raise _UnsupportedOperation(
            "unsupported_request_body_reference",
            "Request Body references are not resolved in this stage.",
            pointer,
            entry.uri,
        )
    if not isinstance(entry, RequestBody):
        raise _UnsupportedOperation("unsupported_request_body", "Unsupported request body entry.", pointer)
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
    if _reference_uri(raw):
        raise _UnsupportedOperation(
            "unsupported_schema_reference",
            "Schema references are not resolved in this stage.",
            schema_pointer,
            _reference_uri(raw),
        )
    value, candidate_pointer = _select_value(
        raw,
        version,
        _media_examples(media, media_pointer),
        schema_pointer,
        direction="request",
    )
    return (
        GeneratedBody(media_type="application/json", value=deepcopy(value)),
        tuple(dict.fromkeys((pointer, schema_pointer, candidate_pointer))),
    )


def _response_expectations(
    endpoint: Endpoint, endpoint_pointer: str
) -> tuple[ResponseExpectation, ...]:
    expectations: list[ResponseExpectation] = []
    for status in sorted(endpoint.responses):
        pointer = _child(endpoint_pointer, "responses", status)
        response = endpoint.responses[status]
        if isinstance(response, LocalReference):
            raise _UnsupportedOperation(
                "unsupported_response_reference",
                "Response references are not resolved in this stage.",
                pointer,
                response.uri,
            )
        if not isinstance(response, Response):
            raise _UnsupportedOperation("unsupported_response", "Unsupported response entry.", pointer)
        media_types = tuple(sorted(response.content))
        expectations.append(
            ResponseExpectation(
                status_key=status,
                media_types=media_types,
                schemas_by_media_type={
                    media_type: deepcopy(response.content[media_type].schema)
                    for media_type in media_types
                },
                response_reference_uri=None,
            )
        )
    return tuple(expectations)


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


def _select_value(
    raw: Any,
    version: str,
    contextual: list[tuple[Any, str]],
    pointer: str,
    *,
    direction: str,
) -> tuple[Any, str]:
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
    reference = _reference_uri(raw)
    if reference:
        raise _UnsupportedOperation(
            "unsupported_schema_reference", "Schema references are not resolved in this stage.", pointer, reference
        )
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
        if direction == "request" and isinstance(child, dict) and child.get("readOnly") is True:
            continue
        _check_schema(child, version, _child(pointer, "properties", name), direction, depth + 1)
    if "items" in raw and raw.get("minItems", 0) > 0:
        _check_schema(raw["items"], version, _child(pointer, "items"), direction, depth + 1)
    return raw


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
    if "const" in schema and not _equal(value, schema["const"]):
        return False
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
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
        if "items" in schema:
            item_schema = _check_schema(schema["items"], version, "/items", direction, depth + 1)
            for item in value:
                if item_schema is False or (isinstance(item_schema, dict) and not _matches(item_schema, item, version, direction, depth + 1)):
                    return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            child = properties.get(name)
            if direction == "request" and isinstance(child, dict) and child.get("readOnly") is True:
                continue
            if name not in value:
                return False
        for name, child_value in value.items():
            child = properties.get(name)
            if child is None:
                continue
            if direction == "request" and isinstance(child, dict) and child.get("readOnly") is True:
                return False
            child_schema = _check_schema(child, version, f"/properties/{name}", direction, depth + 1)
            if child_schema is False or (isinstance(child_schema, dict) and not _matches(child_schema, child_value, version, direction, depth + 1)):
                return False
    return True


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
        if minimum > 1 and schema.get("uniqueItems") is True:
            return ()
        if "items" not in schema:
            return ()
        item_schema = _check_schema(schema["items"], version, _child(pointer, "items"), direction, 1)
        if item_schema is True:
            item = None
        elif item_schema is False:
            return ()
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
            if direction == "request" and isinstance(child, dict) and child.get("readOnly") is True:
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
