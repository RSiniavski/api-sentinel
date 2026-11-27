"""Tests for deterministic test-generation foundation models and entry point."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import Field, ValidationError

import app.services.test_generation.generator as generator_module
from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
    Components,
    Endpoint,
    LocalReference,
    MediaType,
    Model,
    NormalizationResult,
    Parameter,
    ParameterOverrideDiagnostic,
    ParameterReference,
    Response,
    RequestBody,
    Schema,
    SecurityRequirement,
    SecurityScheme,
    Server,
)
from app.services.openapi import normalize_openapi_document
from app.services.test_generation import (
    GeneratedBody,
    GeneratedParameterValue,
    GeneratedTestCase,
    GeneratedTestSuite,
    GenerationDiagnostic,
    LinkRequestBody,
    ResolvedResponseSchema,
    ResponseExpectation,
    ResponseHeaderExpectation,
    ResponseLinkExpectation,
    build_case_id,
    generate_test_suite,
)


def sample_output_models() -> tuple[
    GeneratedParameterValue,
    GeneratedBody,
    ResponseExpectation,
    GenerationDiagnostic,
    GeneratedTestCase,
    GeneratedTestSuite,
]:
    parameter = GeneratedParameterValue(
        name="item_id",
        location="path",
        value="item-7",
        provenance=("/paths/~1items~1{item_id}/get/parameters/0",),
    )
    body = GeneratedBody(
        media_type="application/json",
        value={"name": "widget", "enabled": True},
    )
    expectation = ResponseExpectation(
        status_key="201",
        media_types=("application/json",),
        schemas_by_media_type={
            "application/json": Schema(raw={"type": "object", "required": ["id"]})
        },
        response_reference_uri=None,
    )
    diagnostic = GenerationDiagnostic(
        code="unsupported_schema_keyword",
        message="Schema keyword is outside the supported subset.",
        endpoint_path="/items/{item_id}",
        endpoint_method="post",
        source_pointer="/components/schemas/Item",
        reference_uri="#/components/schemas/Item",
    )
    case = GeneratedTestCase(
        case_id="case-sample",
        category="positive",
        path="/items/{item_id}",
        method="post",
        parameters=(parameter,),
        request_body=body,
        security=(SecurityRequirement(schemes={"oauth": ("items:write",)}),),
        response_expectations=(expectation,),
        provenance=("/paths/~1items~1{item_id}/post",),
    )
    suite = GeneratedTestSuite(
        api_title="Inventory API",
        api_version="2.4.0",
        openapi_version="3.1.1",
        cases=(case,),
        diagnostics=(diagnostic,),
    )
    return parameter, body, expectation, diagnostic, case, suite


def sample_normalization_result() -> NormalizationResult:
    endpoint = Endpoint(
        path="/items",
        method="get",
        responses={"200": Response(description="OK")},
        operation_id="sharedOperationId",
    )
    diagnostic = ParameterOverrideDiagnostic(
        path="/items",
        parameter_name="page",
        parameter_location="query",
        original_source_scope="path",
        overriding_source_scope="operation",
    )
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Inventory API", version="2.4.0"),
            endpoints=(endpoint,),
        ),
        diagnostics=(diagnostic,),
    )


def declared_security_schemes(*names: str) -> Components:
    """Components that declare each named scheme, as every requirement name must be."""
    return Components(
        security_schemes={
            name: SecurityScheme(type="apiKey", name=name, location="header") for name in names
        }
    )


def make_result(
    *endpoints: Endpoint, version: str = "3.1.1", components: Components | None = None
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version=version,
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=tuple(endpoints),
            **({"components": components} if components is not None else {}),
        )
    )


def make_endpoint(
    path: str,
    method: str = "get",
    *,
    parameters: tuple[Parameter | ParameterReference, ...] = (),
    request_body: RequestBody | LocalReference | None = None,
    responses: dict[str, Response | LocalReference] | None = None,
    operation_id: str | None = None,
) -> Endpoint:
    return Endpoint(
        path=path,
        method=method,  # type: ignore[arg-type]
        operation_id=operation_id,
        parameters=parameters,
        request_body=request_body,
        responses=responses or {"200": Response(description="OK")},
    )


def test_all_generator_output_models_construct_valid_values() -> None:
    parameter, body, expectation, diagnostic, case, suite = sample_output_models()

    assert parameter.name == "item_id"
    assert body.media_type == "application/json"
    assert expectation.status_key == "201"
    assert diagnostic.code == "unsupported_schema_keyword"
    assert case.parameters == (parameter,)
    assert suite.cases == (case,)


@pytest.mark.parametrize(
    "model",
    [
        GeneratedParameterValue,
        GeneratedBody,
        ResponseExpectation,
        GenerationDiagnostic,
        GeneratedTestCase,
        GeneratedTestSuite,
    ],
)
def test_generated_models_use_strict_frozen_extra_forbid_configuration(model: type) -> None:
    assert model.model_config["strict"] is True
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["frozen"] is True


def test_generated_models_reject_unexpected_fields_and_invalid_types() -> None:
    parameter, body, expectation, diagnostic, case, suite = sample_output_models()
    for model in (parameter, body, expectation, diagnostic, case, suite):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(model).model_validate({**model.model_dump(), "unexpected": "value"})

    with pytest.raises(ValidationError):
        GeneratedParameterValue(
            name=12,  # type: ignore[arg-type]
            location="path",
            value="x",
            provenance=(),
        )
    with pytest.raises(ValidationError):
        GeneratedBody(media_type="application/json", value=object())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GeneratedParameterValue(
            name="q", location="matrix", value="x", provenance=()  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        GeneratedTestCase.model_validate(
            {**case.model_dump(), "expected_outcome": "assume_success"}
        )


def test_generated_models_are_frozen() -> None:
    parameter, body, expectation, diagnostic, case, suite = sample_output_models()
    for model, field, value in (
        (parameter, "name", "changed"),
        (body, "media_type", "text/plain"),
        (expectation, "status_key", "500"),
        (diagnostic, "code", "changed"),
        (case, "path", "/changed"),
        (case, "expected_outcome", "request_rejection_status_unspecified"),
        (suite, "api_title", "Changed API"),
    ):
        with pytest.raises(ValidationError, match="frozen_instance"):
            setattr(model, field, value)


def test_generated_suite_json_round_trip_preserves_nested_schema_and_null_body() -> None:
    _, _, _, _, case, suite = sample_output_models()
    null_body = case.model_copy(
        update={"request_body": GeneratedBody(media_type="application/json", value=None)}
    )
    suite_with_null = suite.model_copy(update={"cases": (null_body,)})

    serialized = suite_with_null.model_dump_json()
    restored = GeneratedTestSuite.model_validate_json(serialized)

    assert restored == suite_with_null
    encoded = json.loads(serialized)
    assert encoded["cases"][0]["request_body"] == {
        "media_type": "application/json",
        "value": None,
    }
    assert encoded["cases"][0]["response_expectations"][0]["schemas_by_media_type"][
        "application/json"
    ]["raw"] == {"type": "object", "required": ["id"]}


def test_absent_request_body_differs_from_explicit_json_null_body() -> None:
    _, _, _, _, case, _ = sample_output_models()
    absent = case.model_copy(update={"request_body": None})
    explicit_null = case.model_copy(
        update={"request_body": GeneratedBody(media_type="application/json", value=None)}
    )

    assert absent.request_body is None
    assert explicit_null.request_body == GeneratedBody(
        media_type="application/json", value=None
    )
    assert absent != explicit_null


def test_generation_copies_api_metadata_and_generates_positive_baseline() -> None:
    result = sample_normalization_result()

    suite = generate_test_suite(result)

    assert suite.api_title == "Inventory API"
    assert suite.api_version == "2.4.0"
    assert suite.openapi_version == "3.1.1"
    assert len(suite.cases) == 1
    assert suite.cases[0].path == "/items"
    assert suite.cases[0].method == "get"
    assert suite.cases[0].category == "positive"
    assert suite.cases[0].request_body is None
    assert suite.diagnostics == ()


def test_generation_does_not_copy_normalization_diagnostics() -> None:
    result = sample_normalization_result()

    suite = generate_test_suite(result)

    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "parameter_overridden"
    assert suite.diagnostics == ()
    assert len(suite.cases) == 1


def test_repeated_generation_is_equivalent_and_does_not_mutate_input() -> None:
    result = sample_normalization_result()
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


def test_response_schema_output_is_isolated_from_input_in_both_directions() -> None:
    input_schema = Schema(
        raw={"type": "object", "properties": {"id": {"type": "integer"}}}
    )
    endpoint = make_endpoint(
        "/schema-isolation",
        responses={
            "200": Response(
                description="OK",
                content={"application/json": MediaType(schema=input_schema)},
            )
        },
    )
    result = make_result(endpoint)

    suite = generate_test_suite(result)
    output_schema = suite.cases[0].response_expectations[0].schemas_by_media_type[
        "application/json"
    ]
    assert output_schema is not input_schema
    assert output_schema.raw is not input_schema.raw
    assert output_schema.raw["properties"] is not input_schema.raw["properties"]

    output_schema.raw["properties"]["id"]["type"] = "string"
    assert input_schema.raw["properties"]["id"]["type"] == "integer"

    input_schema.raw["properties"]["id"]["type"] = "number"
    assert output_schema.raw["properties"]["id"]["type"] == "string"


def test_security_output_is_isolated_from_input_in_both_directions() -> None:
    requirement = SecurityRequirement(schemes={"oauth": ("read",)})
    endpoint = Endpoint(
        path="/security-isolation",
        method="get",
        responses={"200": Response(description="OK")},
    )
    result = NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            security=(requirement,),
            endpoints=(endpoint,),
            components=declared_security_schemes("oauth"),
        )
    )

    suite = generate_test_suite(result)
    output_requirement = suite.cases[0].security[0]
    assert output_requirement is not requirement
    assert output_requirement.schemes is not requirement.schemes

    output_requirement.schemes["oauth"] = ("write",)
    assert requirement.schemes["oauth"] == ("read",)

    requirement.schemes["oauth"] = ("admin",)
    assert output_requirement.schemes["oauth"] == ("write",)


def test_generated_body_json_is_isolated_from_input_in_both_directions() -> None:
    schema = Schema(
        raw={
            "type": "object",
            "example": {"nested": {"values": ["from-input"]}},
        }
    )
    endpoint = make_endpoint(
        "/body-isolation",
        "post",
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=schema)},
        ),
    )
    result = make_result(endpoint)

    suite = generate_test_suite(result)
    output_value = suite.cases[0].request_body.value
    input_value = schema.raw["example"]
    assert output_value == input_value
    assert output_value is not input_value
    assert output_value["nested"] is not input_value["nested"]
    assert output_value["nested"]["values"] is not input_value["nested"]["values"]

    output_value["nested"]["values"].append("from-output")
    assert input_value["nested"]["values"] == ["from-input"]

    input_value["nested"]["values"].append("later-input")
    assert output_value["nested"]["values"] == ["from-input", "from-output"]


def test_generated_parameter_values_are_primitive_in_supported_subset() -> None:
    result = make_result(
        make_endpoint(
            "/parameter-isolation",
            parameters=(
                Parameter(
                    name="q",
                    location="query",
                    required=True,
                    schema=Schema(raw={"type": "string", "example": "input-value"}),
                    source_scope="operation",
                ),
            ),
        )
    )

    suite = generate_test_suite(result)

    # The current supported parameter subset only generates primitive values,
    # so there is no mutable parameter payload to share with the input model.
    assert suite.cases[0].parameters[0].value == "input-value"
    assert not isinstance(suite.cases[0].parameters[0].value, (dict, list))


def test_generation_rejects_values_other_than_normalization_result() -> None:
    with pytest.raises(TypeError, match="NormalizationResult"):
        generate_test_suite({})  # type: ignore[arg-type]


def test_case_id_is_reproducible_and_changes_for_each_identity_field() -> None:
    original = ("positive", "/items", "get", "baseline")
    identifier = build_case_id(*original)

    assert identifier == build_case_id(*original)
    for index, replacement in enumerate(
        ("negative", "/other", "post", "variant-2")
    ):
        changed = list(original)
        changed[index] = replacement
        assert build_case_id(*changed) != identifier


def test_duplicate_operation_ids_do_not_cause_case_id_collisions() -> None:
    first_endpoint = Endpoint(
        path="/first",
        method="get",
        operation_id="duplicate",
        responses={"200": Response(description="OK")},
    )
    second_endpoint = Endpoint(
        path="/second",
        method="get",
        operation_id="duplicate",
        responses={"200": Response(description="OK")},
    )
    result = NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="API", version="1"),
            endpoints=(first_endpoint, second_endpoint),
        )
    )

    identifiers = tuple(
        build_case_id("positive", endpoint.path, endpoint.method, "baseline")
        for endpoint in result.api.endpoints
    )

    assert first_endpoint.operation_id == second_endpoint.operation_id
    assert identifiers[0] != identifiers[1]


def test_generates_one_positive_get_case_with_logical_values_for_all_parameter_locations() -> None:
    endpoint = make_endpoint(
        "/items/{item_id}",
        parameters=(
            Parameter(
                name="item_id", location="path", required=True,
                schema=Schema(raw={"type": "integer"}), source_scope="path",
            ),
            Parameter(
                name="q", location="query", required=True,
                schema=Schema(raw={"type": "string"}), source_scope="operation",
            ),
            Parameter(
                name="X-Enabled", location="header", required=True,
                schema=Schema(raw={"type": "boolean"}), source_scope="operation",
            ),
            Parameter(
                name="session", location="cookie", required=True,
                schema=Schema(raw={"type": "number"}), source_scope="operation",
            ),
            Parameter(
                name="optional", location="query", required=False,
                schema=Schema(raw={"type": "string"}), source_scope="operation",
            ),
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 4
    case = suite.cases[0]
    assert case.path == "/items/{item_id}"
    assert [(item.name, item.location, item.value) for item in case.parameters] == [
        ("item_id", "path", 0),
        ("q", "query", "sentinel"),
        ("X-Enabled", "header", False),
        ("session", "cookie", 0),
    ]
    assert all(item.provenance for item in case.parameters)
    assert "optional" not in {item.name for item in case.parameters}
    assert suite.diagnostics == ()
    parameter_negatives = [
        case for case in suite.cases if case.violated_constraint == "required_parameter"
    ]
    assert [case.provenance[-2] for case in parameter_negatives] == [
        "/paths/~1items~1{item_id}/get/parameters/1",
        "/paths/~1items~1{item_id}/get/parameters/2",
        "/paths/~1items~1{item_id}/get/parameters/3",
    ]
    assert [
        tuple((value.name, value.location) for value in case.parameters)
        for case in parameter_negatives
    ] == [
        (("item_id", "path"), ("X-Enabled", "header"), ("session", "cookie")),
        (("item_id", "path"), ("q", "query"), ("session", "cookie")),
        (("item_id", "path"), ("q", "query"), ("X-Enabled", "header")),
    ]


@pytest.mark.parametrize("location", ["query", "header", "cookie"])
def test_required_non_path_parameter_generates_one_omission_case(location: str) -> None:
    parameter = Parameter(
        name="required-value",
        location=location,  # type: ignore[arg-type]
        required=True,
        schema=Schema(raw={"type": "string", "example": "kept"}),
        source_scope="operation",
    )
    endpoint = make_endpoint("/required-parameter", parameters=(parameter,))

    suite = generate_test_suite(make_result(endpoint))

    positive, negative = suite.cases
    assert positive.parameters == (
        GeneratedParameterValue(
            name="required-value",
            location=location,  # type: ignore[arg-type]
            value="kept",
            provenance=(
                f"/paths/~1required-parameter/get/parameters/0",
                f"/paths/~1required-parameter/get/parameters/0/schema",
                f"/paths/~1required-parameter/get/parameters/0/schema/example",
            ),
        ),
    )
    assert negative.parameters == ()
    assert negative.violated_constraint == "required_parameter"
    assert negative.expected_outcome == "request_rejection_status_unspecified"
    assert negative.affected_property is None
    assert negative.provenance[-2:] == (
        "/paths/~1required-parameter/get/parameters/0",
        f"violated_constraint:required_parameter:{location}:required-value",
    )
    assert negative.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method,
        f"required-parameter:{location}:14:required-value",
    )


def test_required_parameter_cases_remove_one_parameter_preserve_metadata_and_order() -> None:
    endpoint = Endpoint(
        path="/parameter-omissions",
        method="post",
        parameters=(
            Parameter(
                name="first",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "one"}),
                source_scope="operation",
            ),
            Parameter(
                name="skip",
                location="query",
                required=False,
                schema=Schema(raw={"type": "string", "example": "optional"}),
                source_scope="operation",
            ),
            Parameter(
                name="path-id",
                location="path",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 7}),
                source_scope="path",
            ),
            Parameter(
                name="same-name",
                location="header",
                required=True,
                schema=Schema(raw={"type": "boolean", "example": True}),
                source_scope="operation",
            ),
            Parameter(
                name="same-name",
                location="cookie",
                required=True,
                schema=Schema(raw={"type": "string", "example": "cookie"}),
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(raw={"type": "object", "properties": {"ok": {"type": "boolean"}}})
                )
            },
        ),
        security=(SecurityRequirement(schemes={"apiKey": ()}),),
        responses={"400": Response(description="Invalid request")},
    )
    result = make_result(endpoint, components=declared_security_schemes("apiKey"))
    original = result.model_dump_json()

    first_suite = generate_test_suite(result)
    second_suite = generate_test_suite(result)
    positive = first_suite.cases[0]
    negatives = [
        case for case in first_suite.cases if case.violated_constraint == "required_parameter"
    ]

    assert first_suite.model_dump_json() == second_suite.model_dump_json()
    assert len(negatives) == 3
    assert [case.provenance[-2] for case in negatives] == [
        "/paths/~1parameter-omissions/post/parameters/0",
        "/paths/~1parameter-omissions/post/parameters/3",
        "/paths/~1parameter-omissions/post/parameters/4",
    ]
    assert [
        tuple((p.name, p.location, p.value) for p in case.parameters)
        for case in negatives
    ] == [
        (("path-id", "path", 7), ("same-name", "header", True), ("same-name", "cookie", "cookie")),
        (("first", "query", "one"), ("path-id", "path", 7), ("same-name", "cookie", "cookie")),
        (("first", "query", "one"), ("path-id", "path", 7), ("same-name", "header", True)),
    ]
    omitted_parameters = (
        ("query", "first"),
        ("header", "same-name"),
        ("cookie", "same-name"),
    )
    for case, omitted in zip(
        negatives,
        omitted_parameters,
        strict=True,
    ):
        assert len(case.parameters) == len(positive.parameters) - 1
        target_location, target_name = omitted
        expected_remaining = tuple(
            (value.name, value.location, value.value)
            for value in positive.parameters
            if (value.name, value.location) != (target_name, target_location)
        )
        assert tuple(
            (p.name, p.location, p.value) for p in case.parameters
        ) == expected_remaining
        assert case.request_body == positive.request_body
        assert case.security == positive.security
        assert case.response_expectations == positive.response_expectations
        assert case.path == positive.path
        assert case.method == positive.method
        assert case.category == "negative"
        assert case.violated_constraint == "required_parameter"
        assert case.expected_outcome == "request_rejection_status_unspecified"
        assert case.affected_property is None
        assert case.case_id != positive.case_id
        assert case.case_id == build_case_id(
            "negative",
            endpoint.path,
            endpoint.method,
            f"required-parameter:{target_location}:{len(target_name)}:{target_name}",
        )
        assert all(
            (value.name, value.location) != (target_name, target_location)
            for value in case.parameters
        )
    assert len({case.case_id for case in negatives}) == 3
    assert first_suite.diagnostics == ()
    assert result.model_dump_json() == original


def test_required_request_body_generates_one_deterministic_omission_case() -> None:
    endpoint = Endpoint(
        path="/body/{item_id}",
        method="post",
        parameters=(
            Parameter(
                name="item_id",
                location="path",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 5}),
                source_scope="path",
            ),
            Parameter(
                name="trace",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "trace-1"}),
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(raw={"type": "object", "properties": {"active": {"type": "boolean"}}})
                )
            },
        ),
        security=(SecurityRequirement(schemes={"apiKey": ()}),),
        responses={"400": Response(description="Invalid request")},
    )
    result = make_result(endpoint, components=declared_security_schemes("apiKey"))
    original = result.model_dump_json()

    first_suite = generate_test_suite(result)
    second_suite = generate_test_suite(result)
    positive = next(case for case in first_suite.cases if case.category == "positive")
    omissions = [
        case for case in first_suite.cases
        if case.violated_constraint == "required_request_body"
    ]

    assert len(omissions) == 1
    omission = omissions[0]
    assert omission.request_body is None
    assert omission.parameters == positive.parameters
    assert [item.name for item in omission.parameters] == ["item_id", "trace"]
    assert omission.security == positive.security
    assert omission.response_expectations == positive.response_expectations
    assert (omission.path, omission.method) == (positive.path, positive.method)
    assert omission.category == "negative"
    assert omission.violated_constraint == "required_request_body"
    assert omission.expected_outcome == "request_rejection_status_unspecified"
    assert omission.affected_property is None
    assert omission.provenance[-2:] == (
        "/paths/~1body~1{item_id}/post/requestBody",
        "violated_constraint:required_request_body",
    )
    assert omission.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method, "required-request-body"
    )
    assert omission.case_id != positive.case_id
    assert first_suite.model_dump_json() == second_suite.model_dump_json()
    assert positive.request_body == GeneratedBody(
        media_type="application/json", value={}
    )
    assert result.model_dump_json() == original


@pytest.mark.parametrize(
    ("request_body", "expected_categories"),
    [
        (None, ["positive"]),
        (
            RequestBody(
                required=False,
                content={"application/json": MediaType(schema=Schema(raw={"type": "string"}))},
            ),
            ["positive", "positive"],
        ),
    ],
)
def test_optional_or_absent_request_body_has_no_omission_case(
    request_body: RequestBody | None, expected_categories: list[str]
) -> None:
    endpoint = make_endpoint("/optional-body", "post", request_body=request_body)

    suite = generate_test_suite(make_result(endpoint))

    # An optional body only adds a positive case without the body, never a negative one.
    assert [case.category for case in suite.cases] == expected_categories
    assert all(case.violated_constraint != "required_request_body" for case in suite.cases)


def test_enum_property_generates_same_type_candidate_outside_enum() -> None:
    endpoint = make_endpoint(
        "/enum-negative",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["color", "count"],
                            "properties": {
                                "color": {
                                    "type": "string",
                                    "enum": ["sentinel"],
                                },
                                "count": {"type": "integer", "const": 3},
                            },
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )
    result = make_result(endpoint)
    original = result.model_dump_json()

    suite = generate_test_suite(result)
    positive = next(case for case in suite.cases if case.category == "positive")
    enum_case = next(case for case in suite.cases if case.violated_constraint == "enum")
    const_case = next(case for case in suite.cases if case.violated_constraint == "const")

    assert positive.request_body.value == {"color": "sentinel", "count": 3}
    assert enum_case.request_body.value == {"color": "", "count": 3}
    assert enum_case.affected_property == "/color"
    assert enum_case.provenance[-3:] == (
        "/paths/~1enum-negative/post/requestBody/content/application~1json/schema/properties/color",
        "/paths/~1enum-negative/post/requestBody/content/application~1json/schema/properties/color/enum",
        "violated_constraint:enum:/color",
    )
    assert const_case.request_body.value == {"color": "sentinel", "count": 0}
    assert const_case.affected_property == "/count"
    assert enum_case.category == const_case.category == "negative"
    assert enum_case.expected_outcome == const_case.expected_outcome == (
        "request_rejection_status_unspecified"
    )
    assert enum_case.request_body.value["count"] == positive.request_body.value["count"]
    assert const_case.request_body.value["color"] == positive.request_body.value["color"]
    assert enum_case.request_body.value is not positive.request_body.value
    assert const_case.request_body.value is not positive.request_body.value
    assert suite.diagnostics == ()
    assert result.model_dump_json() == original


@pytest.mark.parametrize(
    ("property_schema", "expected_constraint", "expected_candidate", "expected_type"),
    [
        ({"type": "boolean", "enum": [True]}, "enum", False, bool),
        ({"type": "number", "const": 2.5}, "const", 0.0, float),
        ({"type": "null", "const": None}, None, None, type(None)),
    ],
)
def test_enum_const_candidates_preserve_boolean_number_and_null_types(
    property_schema: dict[str, object],
    expected_constraint: str | None,
    expected_candidate: object,
    expected_type: type,
) -> None:
    endpoint = make_endpoint(
        "/enum-const-types",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": property_schema},
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )

    suite = generate_test_suite(make_result(endpoint))
    matching = [
        case for case in suite.cases if case.violated_constraint == expected_constraint
    ] if expected_constraint is not None else []

    if expected_constraint is None:
        assert not any(case.violated_constraint in {"enum", "const"} for case in suite.cases)
        assert suite.diagnostics[0].code == "enum_const_candidate_unavailable"
    else:
        assert len(matching) == 1
        value = matching[0].request_body.value["value"]
        assert value == expected_candidate
        assert type(value) is expected_type


def test_nested_enum_case_preserves_other_payload_and_metadata_deterministically() -> None:
    endpoint = Endpoint(
        path="/nested-enum",
        method="post",
        parameters=(
            Parameter(
                name="trace",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "trace-value"}),
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["details", "active"],
                            "properties": {
                                "details": {
                                    "type": "object",
                                    "required": ["code", "note"],
                                    "properties": {
                                        "code": {"type": "integer", "enum": [4]},
                                        "note": {"type": "string", "example": "keep"},
                                    },
                                },
                                "active": {"type": "boolean"},
                            },
                        }
                    )
                )
            },
        ),
        security=(SecurityRequirement(schemes={"oauth": ("write",)}),),
        responses={"400": Response(description="Invalid request")},
    )
    result = make_result(endpoint, components=declared_security_schemes("oauth"))
    original = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)
    positive = next(case for case in first.cases if case.category == "positive")
    negative = next(case for case in first.cases if case.violated_constraint == "enum")

    assert first.model_dump_json() == second.model_dump_json()
    assert negative.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method, "enum-violation:/details/code"
    )
    assert negative.affected_property == "/details/code"
    assert negative.request_body.value == {
        "details": {"code": 0, "note": "keep"},
        "active": False,
    }
    assert negative.parameters == positive.parameters
    assert negative.security == positive.security
    assert negative.response_expectations == positive.response_expectations
    assert (negative.path, negative.method) == (positive.path, positive.method)
    assert negative.provenance[-3:] == (
        "/paths/~1nested-enum/post/requestBody/content/application~1json/schema/properties/details/properties/code",
        "/paths/~1nested-enum/post/requestBody/content/application~1json/schema/properties/details/properties/code/enum",
        "violated_constraint:enum:/details/code",
    )
    assert negative.request_body.value is not positive.request_body.value
    assert negative.request_body.value["details"] is not positive.request_body.value["details"]
    negative.request_body.value["details"]["note"] = "changed"
    assert positive.request_body.value["details"]["note"] == "keep"
    assert result.model_dump_json() == original


def test_combined_enum_and_const_only_emit_independently_valid_const_violation() -> None:
    endpoint = make_endpoint(
        "/enum-const",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["choice"],
                            "properties": {
                                "choice": {
                                    "type": "string",
                                    "enum": ["selected", "alternate"],
                                    "const": "selected",
                                }
                            },
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )

    suite = generate_test_suite(make_result(endpoint))

    negatives = [
        case for case in suite.cases if case.violated_constraint in {"enum", "const"}
    ]
    assert [(case.violated_constraint, case.request_body.value) for case in negatives] == [
        ("const", {"choice": "alternate"})
    ]
    assert len(suite.diagnostics) == 1
    diagnostic = suite.diagnostics[0]
    assert diagnostic.code == "enum_const_candidate_unavailable"
    assert diagnostic.source_pointer.endswith("/properties/choice/enum")
    assert "satisfying the other supported constraints" in diagnostic.message


def test_const_candidate_search_includes_enum_values_after_index_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enum_values = list(range(101)) + [103, 101]
    match_checks: list[tuple[dict[str, object], int]] = []
    original_matches = generator_module._matches

    def record_candidate_validation(
        schema: dict[str, object], value: object, version: str, direction: str, depth: int, *rest: str
    ) -> bool:
        if type(value) is int and value in (103, 101) and "const" not in schema:
            match_checks.append((dict(schema), value))  # type: ignore[arg-type]
        return original_matches(schema, value, version, direction, depth, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_matches", record_candidate_validation)
    endpoint = make_endpoint(
        "/const-enum-tail",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["count"],
                            "properties": {
                                "count": {
                                    "type": "integer",
                                    "minimum": 100,
                                    "maximum": 101,
                                    "enum": enum_values,
                                    "const": 100,
                                }
                            },
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )

    suite = generate_test_suite(make_result(endpoint))

    const_case = next(
        case for case in suite.cases if case.violated_constraint == "const"
    )
    candidate = const_case.request_body.value["count"]
    assert candidate == 101
    assert candidate in enum_values
    assert candidate >= 100
    assert candidate <= 101
    assert candidate != 100
    assert [(value, "enum" in schema) for schema, value in match_checks] == [
        (103, False),
        (101, False),
    ]
    assert all(schema.get("minimum") == 100 for schema, _ in match_checks)
    assert all(schema.get("maximum") == 101 for schema, _ in match_checks)
    assert 103 > 101  # The first tail candidate is rejected by maximum.
    assert const_case.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method, "const-violation:/count"
    )


def test_bounded_primitive_search_diagnostic_describes_candidate_set() -> None:
    endpoint = make_endpoint(
        "/bounded-enum-search",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["label"],
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "enum": [
                                        "seed",
                                        "",
                                        "sentinel",
                                        "negative",
                                        "enum-negative",
                                        "seedx",
                                        "seed_negative",
                                    ],
                                }
                            },
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )

    suite = generate_test_suite(make_result(endpoint))

    assert not any(case.violated_constraint == "enum" for case in suite.cases)
    diagnostic = next(
        item
        for item in suite.diagnostics
        if item.code == "enum_const_candidate_unavailable"
    )
    assert diagnostic.source_pointer.endswith("/properties/label/enum")
    assert diagnostic.message == (
        "No candidate was found in the deterministic same-type candidate set "
        "that violates enum while satisfying the other supported constraints."
    )


def test_unavailable_enum_and_const_candidates_produce_diagnostics_without_cases() -> None:
    endpoint = make_endpoint(
        "/enum-const-unavailable",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["choice"],
                            "properties": {
                                "choice": {
                                    "type": "string",
                                    "enum": ["x"],
                                    "const": "x",
                                }
                            },
                        }
                    )
                )
            },
        ),
        responses={"400": Response(description="Invalid request")},
    )

    suite = generate_test_suite(make_result(endpoint))

    assert not any(
        case.violated_constraint in {"enum", "const"} for case in suite.cases
    )
    assert [item.source_pointer.rsplit("/", 1)[-1] for item in suite.diagnostics] == [
        "enum",
        "const",
    ]
    assert all(item.code == "enum_const_candidate_unavailable" for item in suite.diagnostics)
    assert "No candidate was found in the deterministic same-type candidate set" in (
        suite.diagnostics[0].message
    )


def test_generates_distinct_cases_for_different_methods_and_paths() -> None:
    result = make_result(
        make_endpoint("/items", "get", operation_id="same"),
        make_endpoint("/items", "post", operation_id="same"),
        make_endpoint("/other", "get", operation_id="same"),
    )

    suite = generate_test_suite(result)

    assert [(case.path, case.method) for case in suite.cases] == [
        ("/items", "get"),
        ("/items", "post"),
        ("/other", "get"),
    ]
    assert len({case.case_id for case in suite.cases}) == 3


def test_generates_required_json_object_properties_and_omits_optional_and_read_only() -> None:
    schema = Schema(
        raw={
            "type": "object",
            "required": ["name", "server_id", "secret"],
            "properties": {
                "name": {"type": "string", "minLength": 2},
                "server_id": {"type": "integer", "readOnly": True},
                "secret": {"type": "string", "writeOnly": True},
                "note": {"type": "string"},
            },
        }
    )
    endpoint = make_endpoint(
        "/items",
        "post",
        responses={
            "200": Response(description="OK"),
            "400": Response(description="Invalid request"),
        },
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=schema)},
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    # One baseline plus the string valid-edge case for minLength 2 (Step 8.42.2.1).
    assert sum(case.category == "positive" for case in suite.cases) == 2
    positive = suite.cases[0]
    body = positive.request_body
    assert body == GeneratedBody(
        media_type="application/json",
        value={"name": "sentinel", "secret": "sentinel"},
    )
    [edge] = [case for case in suite.cases if is_valid_edge_case(case)]
    assert edge.request_body == GeneratedBody(
        media_type="application/json", value={"name": "xx", "secret": "sentinel"}
    )
    negatives = [case for case in suite.cases if case.violated_constraint == "required_property"]
    assert len(negatives) == 2
    assert {case.affected_property for case in negatives} == {"/name", "/secret"}
    assert all(case.violated_constraint == "required_property" for case in negatives)
    assert all(
        case.expected_outcome == "request_rejection_status_unspecified"
        for case in negatives
    )
    assert all(case.response_expectations == positive.response_expectations for case in negatives)
    assert suite.diagnostics == ()


def test_one_required_property_produces_one_explicit_negative_case() -> None:
    security = SecurityRequirement(schemes={"oauth": ("items:write",)})
    endpoint = Endpoint(
        path="/one-required",
        method="post",
        parameters=(
            Parameter(
                name="trace",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "trace-1"}),
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string"},
                                "optional": {"type": "boolean"},
                            },
                        }
                    )
                )
            },
        ),
        responses={
            "201": Response(description="Created"),
            "400": Response(description="Invalid request"),
        },
        security=(security,),
    )
    result = NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=(endpoint,),
            components=declared_security_schemes("oauth"),
        )
    )

    suite = generate_test_suite(result)

    assert len(suite.cases) == 5
    positive = next(case for case in suite.cases if case.category == "positive")
    negative = next(case for case in suite.cases if case.violated_constraint == "required_property")
    parameter_negative = next(
        case for case in suite.cases if case.violated_constraint == "required_parameter"
    )
    assert positive.category == "positive"
    assert positive.case_id == build_case_id("positive", "/one-required", "post", "baseline")
    assert positive.request_body == GeneratedBody(
        media_type="application/json", value={"name": "sentinel"}
    )
    assert negative.category == "negative"
    assert negative.request_body == GeneratedBody(media_type="application/json", value={})
    assert negative.violated_constraint == "required_property"
    assert negative.affected_property == "/name"
    assert negative.expected_outcome == "request_rejection_status_unspecified"
    assert negative.parameters == positive.parameters
    assert negative.security == positive.security
    assert negative.parameters[0].value == "trace-1"
    assert negative.security[0] is not security
    assert negative.response_expectations == positive.response_expectations
    assert negative.provenance[-2:] == (
        "/paths/~1one-required/post/requestBody/content/application~1json/schema/properties/name",
        "violated_constraint:required_property:/name",
    )
    assert parameter_negative.parameters == ()
    assert parameter_negative.provenance[-2] == "/paths/~1one-required/post/parameters/0"
    assert suite.diagnostics == ()


def test_multiple_required_properties_each_omit_only_the_target_and_skip_read_only() -> None:
    endpoint = make_endpoint(
        "/multiple-required",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["alpha", "beta", "server_id"],
                            "properties": {
                                "alpha": {"type": "string"},
                                "beta": {"type": "integer"},
                                "server_id": {"type": "string", "readOnly": True},
                                "optional": {"type": "boolean"},
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 6
    positive = suite.cases[0]
    assert positive.request_body.value == {"alpha": "sentinel", "beta": 0}
    negatives = {
        case.affected_property: case
        for case in suite.cases
        if case.violated_constraint == "required_property"
    }
    assert set(negatives) == {"/alpha", "/beta"}
    assert negatives["/alpha"].request_body.value == {"beta": 0}
    assert negatives["/beta"].request_body.value == {"alpha": "sentinel"}
    for target, case in negatives.items():
        body = case.request_body.value
        assert target.removeprefix("/") not in body
        assert {"alpha", "beta"} - {target.removeprefix("/")} <= set(body)
        assert case.violated_constraint == "required_property"
        assert case.expected_outcome == "request_rejection_status_unspecified"
    assert suite.diagnostics == ()


def test_nested_required_property_is_omitted_and_pointer_is_preserved() -> None:
    endpoint = make_endpoint(
        "/nested-required",
        "post",
        responses={"422": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["address"],
                            "properties": {
                                "address": {
                                    "type": "object",
                                    "required": ["postal/code~"],
                                    "properties": {
                                        "postal/code~": {"type": "string"}
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 6
    negatives = {
        case.affected_property: case
        for case in suite.cases
        if case.violated_constraint == "required_property"
    }
    assert negatives["/address"].request_body.value == {}
    negative = negatives["/address/postal~1code~0"]
    assert negative.request_body.value == {"address": {}}
    assert negative.affected_property == "/address/postal~1code~0"
    assert negative.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method,
        "required-property:/address/postal~1code~0",
    )


def test_negative_cases_are_deterministic_unique_and_do_not_mutate_input() -> None:
    endpoint = make_endpoint(
        "/deterministic-negative",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "example": {"name": "from-input", "meta": {"values": [1]}},
                            "required": ["name", "meta"],
                            "properties": {
                                "name": {"type": "string"},
                                "meta": {
                                    "type": "object",
                                    "required": ["values"],
                                    "properties": {
                                        "values": {"type": "array", "items": {"type": "integer"}}
                                    },
                                },
                            },
                        }
                    )
                )
            },
        ),
    )
    result = make_result(endpoint)
    schema_raw = result.api.endpoints[0].request_body.content["application/json"].schema.raw
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert len(first.cases) == 8
    assert len({case.case_id for case in first.cases}) == len(first.cases)
    negative = next(
        case for case in first.cases
        if case.violated_constraint == "required_property" and case.affected_property == "/name"
    )
    generated_values = negative.request_body.value
    assert generated_values is not schema_raw["example"]
    generated_values["meta"]["values"].append(2)
    assert schema_raw["example"]["meta"]["values"] == [1]
    schema_raw["example"]["meta"]["values"].append(3)
    assert generated_values["meta"]["values"] == [1, 2]


def test_unsupported_request_body_schema_emits_diagnostic_without_cases() -> None:
    endpoint = make_endpoint(
        "/unsupported-negative",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string", "format": "hostname"}
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_schema_keyword"
    assert suite.diagnostics[0].endpoint_path == endpoint.path


def test_required_name_without_property_schema_is_diagnostic_not_negative_case() -> None:
    endpoint = make_endpoint(
        "/undeclared-required",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["known", "missing_schema"],
                            "properties": {"known": {"type": "string"}},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 4
    positive = next(case for case in suite.cases if case.category == "positive")
    negative = next(case for case in suite.cases if case.violated_constraint == "required_property")
    assert positive.category == "positive"
    assert positive.request_body.value == {
        "known": "sentinel",
        "missing_schema": None,
    }
    assert negative.affected_property == "/known"
    assert negative.request_body.value == {"missing_schema": None}
    assert "missing_schema" not in {
        case.affected_property for case in suite.cases[1:]
    }
    assert len(suite.diagnostics) == 1
    diagnostic = suite.diagnostics[0]
    assert diagnostic.code == "required_property_schema_missing"
    assert diagnostic.endpoint_path == endpoint.path
    assert diagnostic.source_pointer == (
        "/paths/~1undeclared-required/post/requestBody/content/"
        "application~1json/schema/required/1"
    )
    assert "missing_schema" in diagnostic.message
    assert "no negative omission case" in diagnostic.message


def test_missing_declared_4xx_is_explicit_without_inventing_a_status() -> None:
    endpoint = make_endpoint(
        "/no-error-contract",
        "post",
        responses={"200": Response(description="OK")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string"}},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 4
    negative = next(case for case in suite.cases if case.violated_constraint == "required_property")
    assert negative.expected_outcome == "request_rejection_status_unspecified"
    assert negative.response_expectations[0].status_key == "200"
    response_diagnostic = next(
        item for item in suite.diagnostics
        if item.code == "negative_response_contract_unavailable"
    )
    assert "no HTTP status is inferred" in response_diagnostic.message


def test_json_null_body_is_present_only_when_schema_allows_null() -> None:
    endpoint = make_endpoint(
        "/nullable",
        "post",
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=Schema(raw={"type": "null"}))},
        ),
    )

    case = generate_test_suite(make_result(endpoint)).cases[0]

    assert case.request_body == GeneratedBody(media_type="application/json", value=None)


def test_response_expectations_preserve_status_media_types_and_schema_references() -> None:
    endpoint = make_endpoint(
        "/items",
        responses={
            "200": Response(
                description="OK",
                content={
                    "application/json": MediaType(
                        schema=Schema(raw={"$ref": "#/components/schemas/Item", "description": "item"})
                    ),
                    "application/problem+json": MediaType(),
                },
            ),
            "2XX": Response(description="Other success"),
        },
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].response_expectations == (
        ResponseExpectation(
            status_key="200",
            media_types=("application/json", "application/problem+json"),
            schemas_by_media_type={
                "application/json": Schema(raw={"$ref": "#/components/schemas/Item", "description": "item"}),
                "application/problem+json": None,
            },
            response_reference_uri=None,
        ),
        ResponseExpectation(
            status_key="2XX",
            media_types=(),
            schemas_by_media_type={},
            response_reference_uri=None,
        ),
    )


def test_example_selection_obeys_schema_constraints_and_context_precedence() -> None:
    endpoint = make_endpoint(
        "/examples",
        "post",
        parameters=(
            Parameter(
                name="q",
                location="query",
                required=True,
                schema=Schema(
                    raw={
                        "type": "string",
                        "enum": ["enum-value", "later-value"],
                        "example": "schema-example",
                    }
                ),
                example="parameter-example",
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(raw={"type": "string", "example": "schema-example", "default": "default-value"}),
                    example="media-example",
                )
            },
        ),
    )

    case = generate_test_suite(make_result(endpoint)).cases[0]

    assert case.parameters[0].value == "enum-value"
    assert case.request_body == GeneratedBody(
        media_type="application/json", value="media-example"
    )


@pytest.mark.parametrize(
    ("endpoint", "expected_code", "reference_uri"),
    [
        (
            make_endpoint(
                "/ref-param",
                parameters=(
                    ParameterReference(
                        reference=LocalReference(uri="#/components/parameters/Id"),
                        source_scope="operation",
                    ),
                ),
            ),
            "unsupported_parameter_reference",
            "#/components/parameters/Id",
        ),
        (
            make_endpoint(
                "/ref-schema",
                "post",
                request_body=RequestBody(
                    required=True,
                    content={"application/json": MediaType(schema=Schema(raw={"$ref": "#/components/schemas/Input"}))},
                ),
            ),
            "unsupported_schema_reference",
            "#/components/schemas/Input",
        ),
        (
            make_endpoint(
                "/ref-response",
                responses={"200": LocalReference(uri="#/components/responses/Ok")},
            ),
            "unsupported_response_reference",
            "#/components/responses/Ok",
        ),
    ],
)
def test_unsupported_references_emit_diagnostic_without_case(
    endpoint: Endpoint, expected_code: str, reference_uri: str
) -> None:
    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    diagnostic = suite.diagnostics[0]
    assert diagnostic.code == expected_code
    assert diagnostic.endpoint_path == endpoint.path
    assert diagnostic.endpoint_method == endpoint.method
    assert diagnostic.source_pointer is not None
    assert diagnostic.reference_uri == reference_uri


@pytest.mark.parametrize(
    ("endpoint", "expected_code"),
    [
        (
            make_endpoint("/items/{item_id}"),
            "missing_path_parameter",
        ),
        (
            make_endpoint(
                "/unsupported",
                "post",
                request_body=RequestBody(
                    required=True,
                    content={"application/xml": MediaType(schema=Schema(raw={"type": "object"}))},
                ),
            ),
            "unsupported_request_media_type",
        ),
        (
            make_endpoint(
                "/composition",
                "post",
                request_body=RequestBody(
                    required=True,
                    content={"application/json": MediaType(schema=Schema(raw={"oneOf": [{"type": "string"}]}))},
                ),
            ),
            "unsupported_schema_composition",
        ),
    ],
)
def test_unsupported_operation_does_not_emit_positive_case(
    endpoint: Endpoint, expected_code: str
) -> None:
    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == expected_code
    assert suite.diagnostics[0].endpoint_path == endpoint.path


def test_candidate_examples_must_satisfy_supported_schema_before_use() -> None:
    endpoint = make_endpoint(
        "/invalid-example",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(raw={"type": "string", "default": "valid-fallback"}),
                    example=42,
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].request_body == GeneratedBody(
        media_type="application/json", value="valid-fallback"
    )
    assert suite.diagnostics == ()


def test_named_parameter_and_media_examples_use_sorted_names() -> None:
    endpoint = make_endpoint(
        "/named-examples",
        "post",
        parameters=(
            Parameter(
                name="q",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string"}),
                examples={
                    "zulu": {"value": "zulu-value"},
                    "alpha": {"value": "alpha-value"},
                },
                source_scope="operation",
            ),
        ),
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    examples={
                        "zulu": {"value": {"name": "zulu"}},
                        "alpha": {"value": {"name": "alpha"}},
                    }
                )
            },
        ),
    )

    case = generate_test_suite(make_result(endpoint)).cases[0]

    assert case.parameters[0].value == "alpha-value"
    assert case.request_body == GeneratedBody(
        media_type="application/json", value={"name": "alpha"}
    )


@pytest.mark.parametrize(
    ("version", "schema", "expected"),
    [
        ("3.0.3", {"type": "string", "nullable": True, "const": None}, None),
        ("3.1.1", {"type": ["string", "null"], "enum": [None, "value"]}, None),
    ],
)
def test_nullable_and_null_union_candidates_follow_document_version(
    version: str, schema: dict[str, object], expected: object
) -> None:
    endpoint = make_endpoint(
        "/nullable",
        "post",
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=Schema(raw=schema))},
        ),
    )

    case = generate_test_suite(make_result(endpoint, version=version)).cases[0]

    assert case.request_body == GeneratedBody(media_type="application/json", value=expected)


@pytest.mark.parametrize(
    ("version", "numeric_schema", "expected_constraint", "expected_value"),
    [
        ("3.1.1", {"type": "integer", "minimum": 10}, "minimum", 9),
        ("3.1.1", {"type": "number", "maximum": 10}, "maximum", 11),
        ("3.1.1", {"type": "integer", "exclusiveMinimum": 10}, "exclusiveMinimum", 10),
        ("3.1.1", {"type": "number", "exclusiveMaximum": 10}, "exclusiveMaximum", 10),
        (
            "3.0.3",
            {"type": "integer", "minimum": 10, "exclusiveMinimum": True},
            "exclusiveMinimum",
            10,
        ),
        (
            "3.0.3",
            {"type": "number", "maximum": 10, "exclusiveMaximum": True},
            "exclusiveMaximum",
            10,
        ),
    ],
)
def test_numeric_boundary_cases_support_openapi_versions_and_numeric_types(
    version: str,
    numeric_schema: dict[str, object],
    expected_constraint: str,
    expected_value: int,
) -> None:
    endpoint = make_endpoint(
        "/numeric",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": numeric_schema},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint, version=version))

    positive = suite.cases[0]
    negative = next(
        case for case in suite.cases if case.violated_constraint == expected_constraint
    )
    assert positive.category == "positive"
    assert negative.category == "negative"
    assert negative.request_body.value == {"value": expected_value}
    assert negative.violated_constraint == expected_constraint
    assert negative.affected_property == "/value"
    assert negative.expected_outcome == "request_rejection_status_unspecified"
    assert negative.case_id == build_case_id(
        "negative",
        endpoint.path,
        endpoint.method,
        f"numeric-boundary:/value:{expected_constraint}:{expected_value}",
    )
    assert not any(
        diagnostic.code == "numeric_boundary_no_safe_candidate"
        for diagnostic in suite.diagnostics
    )


def test_numeric_boundaries_support_nested_properties_and_multiple_of() -> None:
    endpoint = make_endpoint(
        "/nested-numeric",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["data"],
                            "properties": {
                                "data": {
                                    "type": "object",
                                    "required": ["amount"],
                                    "properties": {
                                        "amount": {
                                            "type": "number",
                                            "minimum": 1.0,
                                            "multipleOf": 0.1,
                                        }
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].category == "positive"
    assert suite.cases[0].request_body.value == {"data": {"amount": 1}}
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")
    assert negative.request_body.value == {"data": {"amount": 0.9}}
    assert negative.violated_constraint == "minimum"
    assert negative.affected_property == "/data/amount"
    assert negative.provenance[-3:] == (
        "/paths/~1nested-numeric/post/requestBody/content/application~1json/schema/properties/data/properties/amount",
        "/paths/~1nested-numeric/post/requestBody/content/application~1json/schema/properties/data/properties/amount/minimum",
        "violated_constraint:minimum:/data/amount",
    )


def test_overlapping_numeric_bounds_produce_values_violating_only_the_target() -> None:
    endpoint = make_endpoint(
        "/overlapping-numeric",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {
                                    "type": "integer",
                                    "minimum": 10,
                                    "maximum": 10,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    numeric_cases = [
        case for case in suite.cases
        if case.violated_constraint in {"minimum", "maximum"}
    ]
    assert [(case.violated_constraint, case.request_body.value) for case in numeric_cases] == [
        ("minimum", {"amount": 9}),
        ("maximum", {"amount": 11}),
    ]
    assert suite.diagnostics == ()


def test_optional_numeric_properties_can_be_added_for_boundary_cases() -> None:
    endpoint = make_endpoint(
        "/optional-numeric",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "properties": {
                                "optional": {"type": "integer", "minimum": 10}
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].request_body.value == {}
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")
    assert negative.request_body.value == {"optional": 9}
    assert negative.affected_property == "/optional"
    assert suite.diagnostics == ()


def test_nested_optional_object_is_safely_materialized_for_numeric_boundary() -> None:
    endpoint = make_endpoint(
        "/optional-nested-numeric",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "properties": {
                                "optional": {
                                    "type": "object",
                                    "required": ["label"],
                                    "properties": {
                                        "label": {"type": "string"},
                                        "amount": {"type": "number", "minimum": 1},
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")

    assert suite.cases[0].request_body.value == {}
    assert negative.request_body.value == {"optional": {"label": "sentinel", "amount": 0}}
    assert negative.affected_property == "/optional/amount"


def test_decimal_boundary_candidates_preserve_decimal_precision() -> None:
    endpoint = make_endpoint(
        "/decimal-boundary",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {
                                    "type": "number",
                                    "minimum": 0.3,
                                    "multipleOf": 0.1,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")

    assert negative.request_body.value == {"amount": 0.2}
    assert negative.case_id == build_case_id(
        "negative", endpoint.path, endpoint.method, "numeric-boundary:/amount:minimum:0.2"
    )


@pytest.mark.parametrize(
    ("multiple_of", "expected"),
    [(0.5, 10), (0.25, 10)],
)
def test_integer_exclusive_minimum_with_fractional_multiple_of_uses_integer_candidate(
    multiple_of: float, expected: int
) -> None:
    endpoint = make_endpoint(
        "/integer-fractional-multiple",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {
                                    "type": "integer",
                                    "exclusiveMinimum": 10.5,
                                    "multipleOf": multiple_of,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    negative = next(case for case in suite.cases if case.violated_constraint == "exclusiveMinimum")

    assert negative.request_body.value == {"amount": expected}
    assert negative.violated_constraint == "exclusiveMinimum"
    assert isinstance(negative.request_body.value["amount"], int)
    assert negative.request_body.value["amount"] < 10.5
    assert (negative.request_body.value["amount"] / multiple_of).is_integer()
    assert not any(
        diagnostic.code == "numeric_boundary_no_safe_candidate"
        for diagnostic in suite.diagnostics
    )


@pytest.mark.parametrize(
    ("multiple_of", "expected"),
    [(1.5, 3), (0.5, 1), (0.25, 1), (2.5, 5)],
)
def test_positive_integer_fallback_supports_fractional_multiple_of(
    multiple_of: float, expected: int
) -> None:
    endpoint = make_endpoint(
        "/integer-positive-multiple",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={"type": "integer", "minimum": 1, "multipleOf": multiple_of}
                    )
                )
            },
        ),
    )

    result = make_result(endpoint)
    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.cases[0].category == "positive"
    assert first.cases[0].request_body.value == expected
    assert isinstance(first.cases[0].request_body.value, int)
    assert (first.cases[0].request_body.value / multiple_of).is_integer()
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize(
    ("version", "numeric_bounds", "expected"),
    [
        ("3.1.1", {"minimum": 10.5, "maximum": 20}, 12),
        ("3.1.1", {"minimum": 500, "maximum": 510}, 501),
        ("3.0.3", {"minimum": 10, "maximum": 20, "exclusiveMinimum": True}, 12),
    ],
)
def test_positive_integer_fractional_multiple_of_respects_numeric_bounds(
    version: str, numeric_bounds: dict[str, object], expected: int
) -> None:
    endpoint = make_endpoint(
        "/integer-positive-bounded-multiple",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={"type": "integer", "multipleOf": 1.5, **numeric_bounds}
                    )
                )
            },
        ),
    )

    case = generate_test_suite(make_result(endpoint, version=version)).cases[0]

    assert case.category == "positive"
    assert case.request_body.value == expected
    assert isinstance(case.request_body.value, int)
    assert (case.request_body.value / 1.5).is_integer()


def test_positive_integer_fractional_multiple_of_reports_no_value_when_bounds_exclude_lattice() -> None:
    endpoint = make_endpoint(
        "/integer-positive-no-multiple",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "integer",
                            "multipleOf": 1.5,
                            "minimum": 10,
                            "maximum": 11,
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "no_positive_schema_value"


@pytest.mark.parametrize(
    ("numeric_schema", "expected"),
    [
        ({"minimum": 0.2, "exclusiveMinimum": 0.1}, 0.15),
        ({"minimum": 1.02, "exclusiveMinimum": 1.01}, 1.015),
    ],
)
def test_narrow_decimal_intervals_produce_an_interior_candidate(
    numeric_schema: dict[str, object], expected: float
) -> None:
    endpoint = make_endpoint(
        "/narrow-decimal-interval",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {"amount": {"type": "number", **numeric_schema}},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")
    value = negative.request_body.value["amount"]

    assert value == expected
    assert value < numeric_schema["minimum"]
    assert value > numeric_schema["exclusiveMinimum"]
    assert negative.violated_constraint == "minimum"
    assert not any(
        diagnostic.code == "numeric_boundary_no_safe_candidate"
        and diagnostic.source_pointer.endswith("/minimum")
        for diagnostic in suite.diagnostics
    )


def test_numeric_diagnostic_pointer_uses_optional_ancestor_schema_location() -> None:
    endpoint = make_endpoint(
        "/numeric-diagnostic-pointer",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "properties": {
                                "outer": {
                                    "type": "object",
                                    "required": ["label"],
                                    "properties": {
                                        "label": {"type": "string", "format": "unsupported"},
                                        "amount": {"type": "number", "minimum": 1},
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    diagnostic = next(item for item in suite.diagnostics if item.code == "unsupported_schema_keyword")

    assert diagnostic.source_pointer == (
        "/paths/~1numeric-diagnostic-pointer/post/requestBody/content/"
        "application~1json/schema/properties/outer/properties/label/format"
    )


def test_decimal_boundary_uses_verified_adjacent_float_when_lattice_value_is_unrepresentable() -> None:
    endpoint = make_endpoint(
        "/float-precision",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {
                                    "type": "number",
                                    "minimum": 0.30000000000000004,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")

    assert negative.request_body.value == {"amount": 0.3}
    assert negative.request_body.value["amount"] < 0.30000000000000004


def test_numeric_case_payload_is_isolated_from_positive_case_and_input() -> None:
    endpoint = make_endpoint(
        "/numeric-isolation",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["details"],
                            "properties": {
                                "details": {
                                    "type": "object",
                                    "required": ["amount", "tags"],
                                    "properties": {
                                        "amount": {"type": "number", "minimum": 1},
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )
    result = make_result(endpoint)
    original = result.model_dump_json()

    suite = generate_test_suite(result)
    positive = suite.cases[0]
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")
    assert negative.request_body.value is not positive.request_body.value
    assert negative.request_body.value["details"] is not positive.request_body.value["details"]

    negative.request_body.value["details"]["tags"].append("generated-only")
    assert positive.request_body.value["details"]["tags"] == []
    assert result.model_dump_json() == original


def test_numeric_negative_case_preserves_and_copies_parameters_security_and_responses() -> None:
    parameter = Parameter(
        name="trace",
        location="query",
        required=True,
        schema=Schema(raw={"type": "string"}),
        source_scope="operation",
    )
    security = SecurityRequirement(schemes={"oauth": ("write",)})
    endpoint = Endpoint(
        path="/numeric-metadata",
        method="post",
        parameters=(parameter,),
        security=(security,),
        responses={
            "400": Response(
                description="Invalid request",
                content={
                    "application/json": MediaType(
                        schema=Schema(
                            raw={"type": "object", "properties": {"error": {"type": "string"}}}
                        )
                    )
                },
            )
        },
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {"amount": {"type": "number", "minimum": 2}},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint, components=declared_security_schemes("oauth")))
    positive = suite.cases[0]
    negative = next(case for case in suite.cases if case.violated_constraint == "minimum")

    assert negative.parameters == positive.parameters
    assert negative.parameters is not positive.parameters
    assert negative.security == positive.security
    assert negative.security is not positive.security
    assert negative.response_expectations == positive.response_expectations
    assert negative.response_expectations[0].schemas_by_media_type["application/json"] is not (
        positive.response_expectations[0].schemas_by_media_type["application/json"]
    )
    assert negative.expected_outcome == "request_rejection_status_unspecified"


def test_numeric_boundary_generation_reports_unavailable_candidates_for_constraints() -> None:
    endpoint = make_endpoint(
        "/numeric-no-candidate",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {
                                    "type": "number",
                                    "minimum": 10,
                                    "exclusiveMinimum": 9.9,
                                    "multipleOf": 1,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].category == "positive"
    numeric_diagnostics = [
        diagnostic for diagnostic in suite.diagnostics
        if diagnostic.code == "numeric_boundary_no_safe_candidate"
    ]
    assert len(numeric_diagnostics) == 2
    assert all(diagnostic.endpoint_path == endpoint.path for diagnostic in numeric_diagnostics)
    assert {diagnostic.source_pointer.rsplit("/", 1)[-1] for diagnostic in numeric_diagnostics} == {
        "minimum", "exclusiveMinimum"
    }


def test_numeric_boundary_generation_skips_read_only_properties_and_diagnoses_unsupported_schema() -> None:
    endpoint = make_endpoint(
        "/numeric-unsupported",
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "properties": {
                                "server_value": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "readOnly": True,
                                },
                                "custom_value": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "format": "custom-number",
                                },
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 2
    assert suite.cases[0].category == "positive"
    assert suite.cases[0].request_body.value == {}
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_schema_keyword"
    assert suite.diagnostics[0].source_pointer.endswith("/format")


@pytest.mark.parametrize(
    ("enum_or_const", "expected_negative"),
    [
        ({"enum": [9, 10]}, {"amount": 9}),
        ({"enum": [10, 11]}, None),
        ({"const": 10}, None),
    ],
)
def test_numeric_boundary_candidates_respect_enum_and_const(
    enum_or_const: dict[str, object], expected_negative: dict[str, int] | None
) -> None:
    endpoint = make_endpoint(
        "/numeric-enum",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["amount"],
                            "properties": {
                                "amount": {"type": "integer", "minimum": 10, **enum_or_const}
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    if expected_negative is None:
        assert all(case.violated_constraint != "minimum" for case in suite.cases)
        assert any(
            diagnostic.code == "numeric_boundary_no_safe_candidate"
            for diagnostic in suite.diagnostics
        )
    else:
        negative = next(case for case in suite.cases if case.violated_constraint == "minimum")
        assert negative.request_body.value == expected_negative
        assert not any(
            diagnostic.code == "numeric_boundary_no_safe_candidate"
            for diagnostic in suite.diagnostics
        )


def test_numeric_boundary_cases_are_ordered_stable_and_do_not_mutate_input() -> None:
    endpoint = make_endpoint(
        "/numeric-deterministic",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["second", "first"],
                            "properties": {
                                "second": {"type": "integer", "maximum": 10},
                                "first": {"type": "integer", "minimum": 2},
                            },
                        }
                    )
                )
            },
        ),
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    numeric_cases = [case for case in first.cases if case.violated_constraint in {"minimum", "maximum"}]
    assert [case.affected_property for case in numeric_cases] == ["/first", "/second"]
    assert [case.request_body.value for case in numeric_cases] == [
        {"second": 0, "first": 1},
        {"second": 11, "first": 2},
    ]
    assert len({case.case_id for case in first.cases}) == len(first.cases)


@pytest.mark.parametrize(
    ("property_type", "positive_value", "wrong_value"),
    [
        ("string", "sentinel", 0),
        ("integer", 0, "wrong-type"),
        ("number", 0, "wrong-type"),
        ("boolean", False, "wrong-type"),
        ("array", [], "wrong-type"),
        ("object", {}, "wrong-type"),
    ],
)
def test_wrong_type_negative_cases_cover_supported_json_types(
    property_type: str, positive_value: object, wrong_value: object
) -> None:
    endpoint = make_endpoint(
        "/wrong-type-basic",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": property_type}},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    positive = next(case for case in suite.cases if case.category == "positive")
    wrong_type = next(case for case in suite.cases if case.violated_constraint == "type")

    assert positive.request_body.value == {"value": positive_value}
    assert wrong_type.request_body.value == {"value": wrong_value}
    assert wrong_type.category == "negative"
    assert wrong_type.expected_outcome == "request_rejection_status_unspecified"
    assert wrong_type.violated_constraint == "type"
    assert wrong_type.affected_property == "/value"
    assert wrong_type.provenance[-3:] == (
        "/paths/~1wrong-type-basic/post/requestBody/content/application~1json/schema/properties/value",
        "/paths/~1wrong-type-basic/post/requestBody/content/application~1json/schema/properties/value/type",
        "violated_constraint:type:/value",
    )


def test_wrong_type_cases_preserve_other_values_and_generate_one_case_per_property() -> None:
    endpoint = make_endpoint(
        "/wrong-type-many",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["title", "count", "active"],
                            "properties": {
                                "title": {"type": "string"},
                                "count": {"type": "integer"},
                                "active": {"type": "boolean"},
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    positive = suite.cases[0]
    wrong_cases = [case for case in suite.cases if case.violated_constraint == "type"]

    assert [case.affected_property for case in wrong_cases] == ["/active", "/count", "/title"]
    assert [case.request_body.value for case in wrong_cases] == [
        {"title": "sentinel", "count": 0, "active": "wrong-type"},
        {"title": "sentinel", "count": "wrong-type", "active": False},
        {"title": 0, "count": 0, "active": False},
    ]
    assert all(case.case_id for case in wrong_cases)
    assert len({case.case_id for case in wrong_cases}) == len(wrong_cases)
    assert all(case.parameters == positive.parameters for case in wrong_cases)
    assert all(case.security == positive.security for case in wrong_cases)
    assert all(case.response_expectations == positive.response_expectations for case in wrong_cases)


def test_wrong_type_cases_support_nested_properties_and_skip_absent_properties() -> None:
    endpoint = make_endpoint(
        "/wrong-type-nested",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["outer"],
                            "properties": {
                                "outer": {
                                    "type": "object",
                                    "required": ["label"],
                                    "properties": {
                                        "label": {"type": "string"},
                                        "optional": {"type": "integer"},
                                    },
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    positive = suite.cases[0]
    wrong_cases = [case for case in suite.cases if case.violated_constraint == "type"]

    assert positive.request_body.value == {"outer": {"label": "sentinel"}}
    assert [case.affected_property for case in wrong_cases] == ["/outer", "/outer/label"]
    assert wrong_cases[0].request_body.value == {"outer": "wrong-type"}
    assert wrong_cases[1].request_body.value == {"outer": {"label": 0}}


@pytest.mark.parametrize(
    ("version", "declared_type", "wrong_value"),
    [
        ("3.0.3", "string", 0),
        ("3.1.1", ["string", "null"], 0),
        ("3.1.1", ["integer", "null"], "wrong-type"),
        ("3.1.1", ["null"], "wrong-type"),
    ],
)
def test_wrong_type_candidates_respect_nullable_and_type_union_semantics(
    version: str, declared_type: str | list[str], wrong_value: object
) -> None:
    property_schema: dict[str, object] = {"type": declared_type}
    if version.startswith("3.0."):
        property_schema["nullable"] = True
    endpoint = make_endpoint(
        "/wrong-type-nullable",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": property_schema},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint, version=version))
    wrong_type = next(case for case in suite.cases if case.violated_constraint == "type")

    assert wrong_type.request_body.value["value"] == wrong_value
    assert wrong_type.affected_property == "/value"


def test_wrong_type_cases_are_deterministic_and_diagnose_schemas_without_type() -> None:
    endpoint = make_endpoint(
        "/wrong-type-deterministic",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["first", "untyped"],
                            "properties": {
                                "first": {"type": "integer"},
                                "untyped": {"default": "kept"},
                            },
                        }
                    )
                )
            },
        ),
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)
    wrong_cases = [case for case in first.cases if case.violated_constraint == "type"]

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert [case.affected_property for case in wrong_cases] == ["/first"]
    assert len({case.case_id for case in wrong_cases}) == len(wrong_cases)
    assert any(item.code == "wrong_type_schema_missing_type" for item in first.diagnostics)


def test_string_length_cases_violate_min_and_max_independently() -> None:
    endpoint = make_endpoint(
        "/string-length-both",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["text", "keep"],
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "minLength": 3,
                                    "maxLength": 10,
                                },
                                "keep": {"type": "integer"},
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    positive = suite.cases[0]
    cases = {case.violated_constraint: case for case in suite.cases if case.violated_constraint in {"minLength", "maxLength"}}

    assert positive.request_body.value == {"text": "sentinel", "keep": 0}
    assert set(cases) == {"minLength", "maxLength"}
    short = cases["minLength"].request_body.value
    long = cases["maxLength"].request_body.value
    assert short == {"text": "xx", "keep": 0}
    assert len(short["text"]) < 3
    assert len(short["text"]) <= 10
    assert long == {"text": "x" * 11, "keep": 0}
    assert len(long["text"]) > 10
    assert len(long["text"]) >= 3
    for constraint, case in cases.items():
        assert case.category == "negative"
        assert case.expected_outcome == "request_rejection_status_unspecified"
        assert case.affected_property == "/text"
        assert case.provenance[-3:] == (
            "/paths/~1string-length-both/post/requestBody/content/application~1json/schema/properties/text",
            f"/paths/~1string-length-both/post/requestBody/content/application~1json/schema/properties/text/{constraint}",
            f"violated_constraint:{constraint}:/text",
        )


def test_string_length_cases_support_multiple_and_nested_properties_without_mutation() -> None:
    endpoint = make_endpoint(
        "/string-length-nested",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["outer", "keep"],
                            "properties": {
                                "outer": {
                                    "type": "object",
                                    "required": ["label"],
                                    "properties": {
                                        "label": {"type": "string", "minLength": 2},
                                        "optional": {"type": "string", "maxLength": 3},
                                    },
                                },
                                "keep": {"type": "string", "maxLength": 4},
                            },
                        }
                    )
                )
            },
        ),
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)
    cases = [case for case in first.cases if case.violated_constraint in {"minLength", "maxLength"}]

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert [(case.affected_property, case.violated_constraint) for case in cases] == [
        ("/keep", "maxLength"),
        ("/outer/label", "minLength"),
    ]
    assert [case.request_body.value for case in cases] == [
        {"outer": {"label": "sentinel"}, "keep": "x" * 5},
        {"outer": {"label": "x"}, "keep": "sent"},
    ]
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.security == first.cases[0].security for case in cases)
    assert all(case.response_expectations == first.cases[0].response_expectations for case in cases)


def test_string_length_generation_skips_properties_absent_from_positive_body() -> None:
    endpoint = make_endpoint(
        "/string-length-optional",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "properties": {
                                "optional": {"type": "string", "minLength": 2},
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases[0].request_body.value == {}
    assert not any(case.violated_constraint in {"minLength", "maxLength"} for case in suite.cases)
    assert not any(item.code.startswith("string_boundary_") for item in suite.diagnostics)


@pytest.mark.parametrize(
    ("version", "property_schema"),
    [
        ("3.0.3", {"type": "string", "nullable": True, "minLength": 2, "maxLength": 3}),
        ("3.1.1", {"type": ["string", "null"], "minLength": 2, "maxLength": 3}),
    ],
)
def test_string_length_generation_supports_nullable_string_schemas(
    version: str, property_schema: dict[str, object]
) -> None:
    endpoint = make_endpoint(
        "/string-length-nullable",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": property_schema},
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint, version=version))
    cases = {case.violated_constraint: case for case in suite.cases if case.violated_constraint in {"minLength", "maxLength"}}

    assert cases["minLength"].request_body.value == {"text": "x"}
    assert cases["maxLength"].request_body.value == {"text": "x" * 4}


@pytest.mark.parametrize(
    ("restricted", "expected_constraint"),
    [
        ({"enum": ["ab", "valid"]}, "minLength"),
        ({"const": "valid"}, None),
    ],
)
def test_string_length_candidates_respect_const_and_enum(
    restricted: dict[str, object], expected_constraint: str | None
) -> None:
    endpoint = make_endpoint(
        "/string-length-restricted",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["text"],
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "minLength": 3,
                                    "maxLength": 6,
                                    **restricted,
                                }
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))
    string_cases = [case for case in suite.cases if case.violated_constraint in {"minLength", "maxLength"}]

    if expected_constraint is None:
        assert string_cases == []
        assert {item.code for item in suite.diagnostics} >= {"string_boundary_no_safe_candidate"}
    else:
        assert len(string_cases) == 1
        assert string_cases[0].violated_constraint == expected_constraint
        assert string_cases[0].request_body.value == {"text": "ab"}
        assert any(
            item.code == "string_boundary_no_safe_candidate"
            and item.source_pointer.endswith("/maxLength")
            for item in suite.diagnostics
        )


def test_string_length_zero_minimum_and_missing_type_are_diagnosed() -> None:
    endpoint = make_endpoint(
        "/string-length-unsupported",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["zero", "untyped"],
                            "properties": {
                                "zero": {"type": "string", "minLength": 0},
                                "untyped": {"default": "okay", "minLength": 2},
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert not any(case.violated_constraint in {"minLength", "maxLength"} for case in suite.cases)
    assert {item.code for item in suite.diagnostics} >= {
        "string_boundary_no_safe_candidate",
        "string_boundary_type_missing",
    }


def test_contradictory_string_length_interval_has_no_positive_or_negative_case() -> None:
    endpoint = make_endpoint(
        "/string-length-impossible",
        "post",
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["text"],
                            "properties": {
                                "text": {"type": "string", "minLength": 5, "maxLength": 3}
                            },
                        }
                    )
                )
            },
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert suite.diagnostics[0].code == "no_positive_schema_value"


def make_result_with_parameters(
    endpoint: Endpoint, parameters: dict[str, Parameter | ParameterReference]
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=(endpoint,),
            components=Components(parameters=parameters),
        )
    )


def parameter_reference(name: str) -> ParameterReference:
    return ParameterReference(
        reference=LocalReference(uri=f"#/components/parameters/{name}"),
        source_scope="operation",
    )


def test_parameter_reference_resolves_to_component_parameter() -> None:
    endpoint = make_endpoint("/items", parameters=(parameter_reference("Limit"),))
    result = make_result_with_parameters(
        endpoint,
        {
            "Limit": Parameter(
                name="limit",
                location="query",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 7}),
                source_scope="component",
            )
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    parameter = suite.cases[0].parameters[0]
    assert (parameter.name, parameter.location, parameter.value) == ("limit", "query", 7)
    assert parameter.provenance[:3] == (
        "/paths/~1items/get/parameters/0",
        "#/components/parameters/Limit",
        "/components/parameters/Limit/schema",
    )


def test_parameter_reference_alias_chain_records_every_followed_reference() -> None:
    endpoint = make_endpoint("/items", parameters=(parameter_reference("Alias"),))
    result = make_result_with_parameters(
        endpoint,
        {
            "Alias": parameter_reference("Limit"),
            "Limit": Parameter(
                name="limit",
                location="query",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 7}),
                source_scope="component",
            ),
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    assert suite.cases[0].parameters[0].provenance[:3] == (
        "/paths/~1items/get/parameters/0",
        "#/components/parameters/Alias",
        "#/components/parameters/Limit",
    )


def test_referenced_path_parameter_satisfies_path_placeholder() -> None:
    endpoint = make_endpoint(
        "/items/{item_id}", parameters=(parameter_reference("ItemId"),)
    )
    result = make_result_with_parameters(
        endpoint,
        {
            "ItemId": Parameter(
                name="item_id",
                location="path",
                required=True,
                schema=Schema(raw={"type": "string", "example": "item-1"}),
                source_scope="component",
            )
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    assert suite.cases[0].parameters[0].name == "item_id"
    assert suite.cases[0].parameters[0].value == "item-1"


def test_parameter_reference_cycle_emits_diagnostic_without_case() -> None:
    endpoint = make_endpoint("/items", parameters=(parameter_reference("A"),))
    result = make_result_with_parameters(
        endpoint,
        {"A": parameter_reference("B"), "B": parameter_reference("A")},
    )

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_parameter_reference"
    assert suite.diagnostics[0].reference_uri == "#/components/parameters/A"


@pytest.mark.parametrize(
    "uri",
    [
        "#/components/schemas/Limit",
        "#/components/parameters/Limit/schema",
        "#/components/parameters/",
    ],
)
def test_unsupported_parameter_reference_forms_emit_diagnostic(uri: str) -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(
            ParameterReference(
                reference=LocalReference(uri=uri), source_scope="operation"
            ),
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_parameter_reference"
    assert suite.diagnostics[0].reference_uri == uri


def test_referenced_required_parameter_gets_omission_case() -> None:
    endpoint = make_endpoint("/items", parameters=(parameter_reference("Limit"),))
    result = make_result_with_parameters(
        endpoint,
        {
            "Limit": Parameter(
                name="limit",
                location="query",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 7}),
                source_scope="component",
            )
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    assert [case.category for case in suite.cases] == ["positive", "negative"]
    negative = suite.cases[1]
    assert negative.violated_constraint == "required_parameter"
    assert negative.parameters == ()
    assert negative.case_id == build_case_id(
        "negative", "/items", "get", "required-parameter:query:5:limit"
    )
    assert negative.provenance[-3:] == (
        "/paths/~1items/get/parameters/0",
        "#/components/parameters/Limit",
        "violated_constraint:required_parameter:query:limit",
    )


def test_referenced_parameter_alias_chain_omission_records_followed_references() -> None:
    endpoint = make_endpoint("/items", parameters=(parameter_reference("Alias"),))
    result = make_result_with_parameters(
        endpoint,
        {
            "Alias": parameter_reference("Limit"),
            "Limit": Parameter(
                name="limit",
                location="header",
                required=True,
                schema=Schema(raw={"type": "integer", "example": 7}),
                source_scope="component",
            ),
        },
    )

    negative = generate_test_suite(result).cases[1]

    assert negative.provenance[-4:] == (
        "/paths/~1items/get/parameters/0",
        "#/components/parameters/Alias",
        "#/components/parameters/Limit",
        "violated_constraint:required_parameter:header:limit",
    )


def test_referenced_optional_and_path_parameters_get_no_omission_case() -> None:
    endpoint = make_endpoint(
        "/items/{item_id}",
        parameters=(parameter_reference("ItemId"), parameter_reference("Verbose")),
    )
    result = make_result_with_parameters(
        endpoint,
        {
            "ItemId": Parameter(
                name="item_id",
                location="path",
                required=True,
                schema=Schema(raw={"type": "string", "example": "item-1"}),
                source_scope="component",
            ),
            "Verbose": Parameter(
                name="verbose",
                location="query",
                required=False,
                schema=Schema(raw={"type": "boolean"}),
                source_scope="component",
            ),
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    assert [case.category for case in suite.cases] == ["positive"]


def make_result_with_request_bodies(
    endpoint: Endpoint, request_bodies: dict[str, RequestBody | LocalReference]
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=(endpoint,),
            components=Components(request_bodies=request_bodies),
        )
    )


def request_body_reference(name: str) -> LocalReference:
    return LocalReference(uri=f"#/components/requestBodies/{name}")


def json_request_body(raw_schema: dict[str, object]) -> RequestBody:
    return RequestBody(
        required=False,
        content={"application/json": MediaType(schema=Schema(raw=raw_schema))},
    )


def test_request_body_reference_resolves_to_component_request_body() -> None:
    endpoint = make_endpoint(
        "/items", "post", request_body=request_body_reference("NewItem")
    )
    result = make_result_with_request_bodies(
        endpoint,
        {"NewItem": json_request_body({"type": "string", "example": "widget"})},
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    body = suite.cases[0].request_body
    assert body is not None
    assert (body.media_type, body.value) == ("application/json", "widget")
    assert suite.cases[0].provenance[-5:] == (
        "/paths/~1items/post/requestBody",
        "#/components/requestBodies/NewItem",
        "/components/requestBodies/NewItem",
        "/components/requestBodies/NewItem/content/application~1json/schema",
        "/components/requestBodies/NewItem/content/application~1json/schema/example",
    )


def test_request_body_reference_alias_chain_records_every_followed_reference() -> None:
    endpoint = make_endpoint(
        "/items", "post", request_body=request_body_reference("Alias")
    )
    result = make_result_with_request_bodies(
        endpoint,
        {
            "Alias": request_body_reference("NewItem"),
            "NewItem": json_request_body({"type": "string", "example": "widget"}),
        },
    )

    suite = generate_test_suite(result)

    assert suite.diagnostics == ()
    assert suite.cases[0].provenance[1:4] == (
        "/paths/~1items/post/requestBody",
        "#/components/requestBodies/Alias",
        "#/components/requestBodies/NewItem",
    )


def test_request_body_reference_cycle_emits_diagnostic_without_case() -> None:
    endpoint = make_endpoint("/items", "post", request_body=request_body_reference("A"))
    result = make_result_with_request_bodies(
        endpoint,
        {"A": request_body_reference("B"), "B": request_body_reference("A")},
    )

    suite = generate_test_suite(result)

    # A body that is not known to be required does not skip the endpoint (Step 8.39.1).
    assert [(case.category, case.request_body) for case in suite.cases] == [("positive", None)]
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_request_body_reference"
    assert suite.diagnostics[0].reference_uri == "#/components/requestBodies/A"


@pytest.mark.parametrize(
    "uri",
    [
        "#/components/schemas/NewItem",
        "#/components/requestBodies/NewItem/content",
        "#/components/requestBodies/",
    ],
)
def test_unsupported_request_body_reference_forms_emit_diagnostic(uri: str) -> None:
    endpoint = make_endpoint(
        "/items", "post", request_body=LocalReference(uri=uri)
    )

    suite = generate_test_suite(make_result(endpoint))

    assert [(case.category, case.request_body) for case in suite.cases] == [("positive", None)]
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "unsupported_request_body_reference"
    assert suite.diagnostics[0].reference_uri == uri


BAD_REQUEST: dict[str, Response | LocalReference] = {"400": Response(description="Invalid request")}


REFERENCED_BODY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["name", "kind", "qty"],
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 10, "example": "widget"},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "qty": {"type": "integer", "minimum": 1, "maximum": 9, "example": 3},
    },
}


def required_json_request_body() -> RequestBody:
    return RequestBody(
        required=True,
        content={"application/json": MediaType(schema=Schema(raw=REFERENCED_BODY_SCHEMA))},
    )


def test_referenced_request_body_generates_same_negative_cases_as_inline_body() -> None:
    inline = generate_test_suite(
        make_result(
            make_endpoint("/items", "post", request_body=required_json_request_body(), responses=BAD_REQUEST)
        )
    )
    referenced = generate_test_suite(
        make_result_with_request_bodies(
            make_endpoint("/items", "post", request_body=request_body_reference("NewItem"), responses=BAD_REQUEST),
            {"NewItem": required_json_request_body()},
        )
    )

    def summary(suite: GeneratedTestSuite) -> list[tuple[object, ...]]:
        return [
            (case.case_id, case.category, case.violated_constraint, case.request_body)
            for case in suite.cases
        ]

    assert inline.diagnostics == referenced.diagnostics == ()
    assert len(inline.cases) > 5
    assert summary(referenced) == summary(inline)


def test_referenced_request_body_negative_cases_point_to_the_component() -> None:
    suite = generate_test_suite(
        make_result_with_request_bodies(
            make_endpoint("/items", "post", request_body=request_body_reference("NewItem"), responses=BAD_REQUEST),
            {"NewItem": required_json_request_body()},
        )
    )

    omission = next(
        case for case in suite.cases if case.violated_constraint == "required_request_body"
    )
    assert omission.provenance[-2:] == (
        "/components/requestBodies/NewItem",
        "violated_constraint:required_request_body",
    )
    schema_prefix = "/components/requestBodies/NewItem/content/application~1json/schema"
    for case in suite.cases:
        if case.violated_constraint in {None, "required_parameter", "required_request_body"}:
            continue
        assert any(item.startswith(schema_prefix) for item in case.provenance), case
        assert not any(
            item.startswith("/paths/~1items/post/requestBody/content")
            for item in case.provenance
        ), case


def test_alias_chain_request_body_gets_negative_cases_with_followed_references() -> None:
    suite = generate_test_suite(
        make_result_with_request_bodies(
            make_endpoint("/items", "post", request_body=request_body_reference("Alias"), responses=BAD_REQUEST),
            {
                "Alias": request_body_reference("NewItem"),
                "NewItem": required_json_request_body(),
            },
        )
    )

    omission = next(
        case for case in suite.cases if case.violated_constraint == "required_request_body"
    )
    assert suite.diagnostics == ()
    assert "#/components/requestBodies/Alias" in omission.provenance
    assert "#/components/requestBodies/NewItem" in omission.provenance


def make_result_with_schemas(
    endpoint: Endpoint, schemas: dict[str, object]
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=(endpoint,),
            components=Components(
                schemas={name: Schema(raw=raw) for name, raw in schemas.items()}
            ),
        )
    )


def schema_reference(name: str) -> dict[str, object]:
    return {"$ref": f"#/components/schemas/{name}"}


def body_with_schema(raw_schema: object) -> RequestBody:
    return RequestBody(
        required=True,
        content={"application/json": MediaType(schema=Schema(raw=raw_schema))},
    )


STATUS_SCHEMA: dict[str, object] = {"type": "string", "enum": ["new", "old"]}


def test_parameter_schema_reference_resolves_to_component_schema() -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(
            Parameter(
                name="status",
                location="query",
                required=True,
                schema=Schema(raw=schema_reference("Status")),
                source_scope="operation",
            ),
        ),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result_with_schemas(endpoint, {"Status": STATUS_SCHEMA}))

    assert suite.diagnostics == ()
    parameter = suite.cases[0].parameters[0]
    assert parameter.value == "new"
    assert parameter.provenance == (
        "/paths/~1items/get/parameters/0",
        "/paths/~1items/get/parameters/0/schema",
        "#/components/schemas/Status",
        "/components/schemas/Status",
        "/components/schemas/Status/enum/0",
    )


def test_schema_reference_alias_chain_records_every_followed_reference() -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(
            Parameter(
                name="status",
                location="query",
                required=True,
                schema=Schema(raw=schema_reference("Alias")),
                source_scope="operation",
            ),
        ),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(
        make_result_with_schemas(
            endpoint, {"Alias": schema_reference("Status"), "Status": STATUS_SCHEMA}
        )
    )

    assert suite.diagnostics == ()
    assert suite.cases[0].parameters[0].provenance[2:5] == (
        "#/components/schemas/Alias",
        "#/components/schemas/Status",
        "/components/schemas/Status",
    )


def test_request_body_schema_reference_resolves_to_component_schema() -> None:
    item = {
        "type": "object",
        "required": ["name", "qty"],
        "properties": {
            "name": {"type": "string", "example": "widget"},
            "qty": {"type": "integer", "minimum": 1, "maximum": 9, "example": 3},
        },
    }
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(schema_reference("Item")),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result_with_schemas(endpoint, {"Item": item}))

    inline = generate_test_suite(
        make_result(
            make_endpoint("/items", "post", request_body=body_with_schema(item), responses=BAD_REQUEST)
        )
    )
    positive = suite.cases[0]
    assert positive.request_body is not None
    assert positive.request_body.value == {"name": "widget", "qty": 3}
    assert positive.request_body == inline.cases[0].request_body
    assert positive.provenance[-3:] == (
        "/paths/~1items/post/requestBody/content/application~1json/schema",
        "#/components/schemas/Item",
        "/components/schemas/Item",
    )


def test_nested_schema_references_are_followed_and_read_only_target_is_skipped() -> None:
    body = {
        "type": "object",
        "required": ["id", "name", "tags"],
        "properties": {
            "id": schema_reference("Id"),
            "name": schema_reference("Name"),
            "tags": {"type": "array", "minItems": 1, "items": schema_reference("Name")},
        },
    }
    endpoint = make_endpoint(
        "/items", "post", request_body=body_with_schema(body), responses=BAD_REQUEST
    )

    suite = generate_test_suite(
        make_result_with_schemas(
            endpoint,
            {
                "Id": {"type": "integer", "readOnly": True},
                "Name": {"type": "string", "example": "widget"},
            },
        )
    )

    positive = suite.cases[0]
    assert positive.request_body is not None
    assert positive.request_body.value == {"name": "widget", "tags": ["widget"]}


@pytest.mark.parametrize(
    "extra",
    [{"description": "A status"}, {"title": "Status", "summary": "s"}, {"x-note": 1}],
)
def test_annotation_keywords_next_to_schema_reference_are_allowed(
    extra: dict[str, object],
) -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema({**schema_reference("Status"), **extra}),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result_with_schemas(endpoint, {"Status": STATUS_SCHEMA}))

    assert suite.diagnostics == ()
    assert suite.cases[0].request_body is not None
    assert suite.cases[0].request_body.value == "new"


def test_other_keywords_next_to_schema_reference_emit_diagnostic_without_case() -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema({**schema_reference("Status"), "maxLength": 2}),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result_with_schemas(endpoint, {"Status": STATUS_SCHEMA}))

    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    diagnostic = suite.diagnostics[0]
    assert diagnostic.code == "unsupported_schema_reference_siblings"
    assert diagnostic.reference_uri == "#/components/schemas/Status"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/schema/maxLength")


@pytest.mark.parametrize(
    "uri",
    [
        "#/components/schemas/Missing",
        "#/components/parameters/Status",
        "#/components/schemas/Status/enum",
        "#/components/schemas/",
    ],
)
def test_unsupported_schema_reference_forms_emit_diagnostic_without_case(uri: str) -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema({"$ref": uri}),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result_with_schemas(endpoint, {"Status": STATUS_SCHEMA}))

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_reference"]
    assert suite.diagnostics[0].reference_uri == uri


def test_schema_reference_alias_cycle_emits_diagnostic_without_case() -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(schema_reference("A")),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(
        make_result_with_schemas(
            endpoint, {"A": schema_reference("B"), "B": schema_reference("A")}
        )
    )

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_reference"]
    assert suite.diagnostics[0].reference_uri == "#/components/schemas/A"


def test_optional_recursive_schema_generates_and_required_recursion_is_reported() -> None:
    optional = {
        "type": "object",
        "properties": {"next": schema_reference("Node")},
    }
    required = {
        "type": "object",
        "required": ["next"],
        "properties": {"next": schema_reference("Node")},
    }
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(schema_reference("Node")),
        responses=BAD_REQUEST,
    )

    optional_suite = generate_test_suite(make_result_with_schemas(endpoint, {"Node": optional}))
    required_suite = generate_test_suite(make_result_with_schemas(endpoint, {"Node": required}))

    assert optional_suite.diagnostics == ()
    assert optional_suite.cases[0].request_body is not None
    assert optional_suite.cases[0].request_body.value == {}
    assert required_suite.cases == ()
    assert [item.code for item in required_suite.diagnostics] == ["schema_depth_limit"]


def test_schema_components_do_not_leak_between_generation_calls() -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(schema_reference("Status")),
        responses=BAD_REQUEST,
    )

    with_component = generate_test_suite(
        make_result_with_schemas(endpoint, {"Status": STATUS_SCHEMA})
    )
    without_component = generate_test_suite(make_result_with_schemas(endpoint, {}))

    assert with_component.cases[0].request_body is not None
    assert without_component.cases == ()
    assert [item.code for item in without_component.diagnostics] == [
        "unsupported_schema_reference"
    ]
    assert dict(generator_module._SCHEMA_COMPONENTS.get()) == {}


NAME_SCHEMA: dict[str, object] = {
    "type": "string",
    "minLength": 2,
    "maxLength": 10,
    "example": "widget",
}
KIND_SCHEMA: dict[str, object] = {"type": "string", "enum": ["a", "b"]}
QTY_SCHEMA: dict[str, object] = {
    "type": "integer",
    "minimum": 1,
    "maximum": 9,
    "example": 3,
}


def case_summary(suite: GeneratedTestSuite) -> list[tuple[object, ...]]:
    return [
        (
            case.case_id,
            case.category,
            case.violated_constraint,
            case.affected_property,
            case.request_body,
        )
        for case in suite.cases
    ]


def referenced_properties_body() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["name", "kind", "qty"],
        "properties": {
            "name": schema_reference("Name"),
            "kind": schema_reference("Kind"),
            "qty": schema_reference("Qty"),
        },
    }


REFERENCED_PROPERTY_COMPONENTS: dict[str, object] = {
    "Name": NAME_SCHEMA,
    "Kind": KIND_SCHEMA,
    "Qty": QTY_SCHEMA,
}


def test_top_level_schema_reference_body_generates_same_negative_cases_as_inline() -> None:
    inline = generate_test_suite(
        make_result(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(REFERENCED_BODY_SCHEMA),
                responses=BAD_REQUEST,
            )
        )
    )
    referenced = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(schema_reference("Item")),
                responses=BAD_REQUEST,
            ),
            {"Item": REFERENCED_BODY_SCHEMA},
        )
    )

    assert inline.diagnostics == referenced.diagnostics == ()
    assert len(inline.cases) > 10
    assert case_summary(referenced) == case_summary(inline)


def test_referenced_properties_generate_same_negative_cases_as_inline_properties() -> None:
    inline = generate_test_suite(
        make_result(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(REFERENCED_BODY_SCHEMA),
                responses=BAD_REQUEST,
            )
        )
    )
    referenced = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(referenced_properties_body()),
                responses=BAD_REQUEST,
            ),
            REFERENCED_PROPERTY_COMPONENTS,
        )
    )

    assert referenced.diagnostics == ()
    assert case_summary(referenced) == case_summary(inline)


def test_negative_case_provenance_points_into_the_referenced_component() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(referenced_properties_body()),
                responses=BAD_REQUEST,
            ),
            REFERENCED_PROPERTY_COMPONENTS,
        )
    )

    def provenance(constraint: str, prop: str) -> tuple[str, ...]:
        return next(
            case.provenance
            for case in suite.cases
            if case.violated_constraint == constraint and case.affected_property == prop
        )

    assert provenance("enum", "/kind")[-3:] == (
        "/components/schemas/Kind",
        "/components/schemas/Kind/enum",
        "violated_constraint:enum:/kind",
    )
    assert provenance("minimum", "/qty")[-3:] == (
        "/components/schemas/Qty",
        "/components/schemas/Qty/minimum",
        "violated_constraint:minimum:/qty",
    )
    assert provenance("minLength", "/name")[-3:] == (
        "/components/schemas/Name",
        "/components/schemas/Name/minLength",
        "violated_constraint:minLength:/name",
    )
    assert provenance("required_property", "/kind")[-2:] == (
        "/paths/~1items/post/requestBody/content/application~1json/schema/properties/kind",
        "violated_constraint:required_property:/kind",
    )


def test_read_only_property_behind_reference_gets_no_negative_cases() -> None:
    body = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
            "id": schema_reference("Id"),
            "name": schema_reference("Name"),
        },
    }

    suite = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(body),
                responses=BAD_REQUEST,
            ),
            {"Id": {"type": "integer", "readOnly": True, "minimum": 1}, "Name": NAME_SCHEMA},
        )
    )

    assert suite.diagnostics == ()
    assert not [case for case in suite.cases if case.affected_property == "/id"]
    assert any(case.affected_property == "/name" for case in suite.cases)


def test_recursive_optional_schema_expands_once_and_finishes() -> None:
    tree = {
        "type": "object",
        "properties": {
            "value": {"type": "integer", "minimum": 0, "maximum": 5, "example": 1},
            "left": schema_reference("Tree"),
            "right": schema_reference("Tree"),
        },
    }

    suite = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(schema_reference("Tree")),
                responses=BAD_REQUEST,
            ),
            {"Tree": tree},
        )
    )

    assert suite.diagnostics == ()
    numeric = [
        case for case in suite.cases if case.violated_constraint in {"minimum", "maximum"}
    ]
    assert {case.affected_property for case in numeric} == {"/value"}
    assert len(numeric) == 2


def test_unusable_optional_property_reference_does_not_stop_other_negative_cases() -> None:
    body = {
        "type": "object",
        "properties": {"ghost": schema_reference("Missing")},
    }

    suite = generate_test_suite(
        make_result_with_schemas(
            make_endpoint(
                "/items",
                "post",
                request_body=body_with_schema(body),
                responses=BAD_REQUEST,
            ),
            {},
        )
    )

    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert suite.cases[1].violated_constraint == "required_request_body"
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_reference"]
    assert suite.diagnostics[0].reference_uri == "#/components/schemas/Missing"


def make_result_with_endpoints(
    endpoints: tuple[Endpoint, ...], schemas: dict[str, object]
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=endpoints,
            components=Components(
                schemas={name: Schema(raw=raw) for name, raw in schemas.items()}
            ),
        )
    )


def post_endpoint(raw_schema: object, path: str = "/items") -> Endpoint:
    return make_endpoint(
        path,
        "post",
        request_body=body_with_schema(raw_schema),
        responses=BAD_REQUEST,
    )


def numeric_summary(suite: GeneratedTestSuite) -> list[tuple[object, ...]]:
    return [
        (case.case_id, case.affected_property, case.violated_constraint, case.request_body)
        for case in suite.cases
        if case.violated_constraint in {"minimum", "maximum"}
    ]


BUDGET_CODES = {
    "generation_expansion_budget_exceeded",
    "generation_case_budget_exceeded",
    "generation_diagnostic_budget_exceeded",
}
QTY_LEAF: dict[str, object] = {"type": "integer", "minimum": 1, "maximum": 9, "example": 3}


def test_numeric_boundaries_through_root_reference_with_one_nested_hop() -> None:
    nested = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"qty": QTY_LEAF}}},
    }
    through_ref = generate_test_suite(
        make_result_with_schemas(post_endpoint(schema_reference("Root")), {"Root": nested})
    )
    inline = generate_test_suite(make_result(post_endpoint(nested)))

    assert numeric_summary(through_ref) != []
    assert numeric_summary(through_ref) == numeric_summary(inline)
    assert through_ref.diagnostics == ()
    assert any(case.affected_property == "/a/qty" for case in through_ref.cases)


def test_numeric_boundaries_through_multiple_nested_reference_levels() -> None:
    components: dict[str, object] = {
        "Root": {"type": "object", "properties": {"a": schema_reference("A")}},
        "A": {"type": "object", "properties": {"b": schema_reference("B")}},
        "B": {"type": "object", "properties": {"c": schema_reference("C")}},
        "C": {"type": "object", "properties": {"qty": schema_reference("Qty")}},
        "Qty": QTY_LEAF,
    }
    inline = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {
                    "b": {
                        "type": "object",
                        "properties": {
                            "c": {"type": "object", "properties": {"qty": QTY_LEAF}}
                        },
                    }
                },
            }
        },
    }
    through_ref = generate_test_suite(
        make_result_with_schemas(post_endpoint(schema_reference("Root")), components)
    )
    inline_suite = generate_test_suite(make_result(post_endpoint(inline)))

    assert through_ref.diagnostics == ()
    assert numeric_summary(through_ref) == numeric_summary(inline_suite)
    assert [item[1] for item in numeric_summary(through_ref)] == ["/a/b/c/qty"] * 2
    body = numeric_summary(through_ref)[0][3]
    assert body.value == {"a": {"b": {"c": {"qty": 0}}}}


def test_numeric_boundaries_through_inline_root_with_referenced_parents() -> None:
    components: dict[str, object] = {
        "A": {"type": "object", "properties": {"b": schema_reference("B")}},
        "B": {"type": "object", "properties": {"qty": QTY_LEAF}},
    }
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint({"type": "object", "properties": {"a": schema_reference("A")}}),
            components,
        )
    )

    assert suite.diagnostics == ()
    assert [item[1] for item in numeric_summary(suite)] == ["/a/b/qty"] * 2


def test_shared_component_is_expanded_for_each_independent_use() -> None:
    zip_schema = {"type": "object", "properties": {"zip": QTY_LEAF}}
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(
                {
                    "type": "object",
                    "properties": {
                        "billing": schema_reference("Address"),
                        "shipping": schema_reference("Address"),
                    },
                }
            ),
            {"Address": zip_schema},
        )
    )

    assert suite.diagnostics == ()
    assert [item[1] for item in numeric_summary(suite)] == [
        "/billing/zip",
        "/billing/zip",
        "/shipping/zip",
        "/shipping/zip",
    ]


def diamond_components(levels: int, *, required: bool) -> dict[str, object]:
    components: dict[str, object] = {}
    for level in range(levels):
        next_ref = schema_reference(f"L{level + 1}")
        level_schema: dict[str, object] = {
            "type": "object",
            "properties": {"left": next_ref, "right": next_ref},
        }
        if required:
            level_schema["required"] = ["left", "right"]
        components[f"L{level}"] = level_schema
    leaf: dict[str, object] = {"type": "object", "properties": {"qty": QTY_LEAF}}
    if required:
        leaf["required"] = ["qty"]
    components[f"L{levels}"] = leaf
    return components


def test_small_required_diamond_generates_within_default_budgets() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(schema_reference("L0")), diamond_components(3, required=True)
        )
    )

    assert not BUDGET_CODES & {item.code for item in suite.diagnostics}
    assert suite.cases[0].category == "positive"
    assert len(suite.cases) > 1


def test_small_optional_diamond_generates_numeric_cases_within_default_budgets() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(schema_reference("L0")), diamond_components(3, required=False)
        )
    )

    assert not BUDGET_CODES & {item.code for item in suite.diagnostics}
    assert len(numeric_summary(suite)) > 0


def test_large_required_diamond_stops_with_budget_diagnostic() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(schema_reference("L0")), diamond_components(12, required=True)
        )
    )

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == [
        "generation_expansion_budget_exceeded"
    ]
    assert "'positive_baseline'" in suite.diagnostics[0].message


def test_required_diamond_case_budget_drops_the_whole_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 5)

    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(schema_reference("L0")), diamond_components(4, required=True)
        )
    )

    assert [case.category for case in suite.cases] == ["positive"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'required_property'" in suite.diagnostics[0].message


def test_large_optional_diamond_stops_with_one_budget_diagnostic() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(schema_reference("L0")), diamond_components(20, required=False)
        )
    )

    assert [item.code for item in suite.diagnostics] == [
        "generation_expansion_budget_exceeded"
    ]
    assert "'numeric_boundary'" in suite.diagnostics[0].message
    assert suite.cases[0].category == "positive"
    assert numeric_summary(suite) == []


def test_endpoint_expansion_budget_drops_the_step_and_stops_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 20)
    broken = make_endpoint(
        "/items",
        "post",
        parameters=(
            Parameter(
                name="q",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "x"}),
                source_scope="operation",
            ),
        ),
        request_body=body_with_schema(schema_reference("L0")),
        responses=BAD_REQUEST,
    )
    healthy = post_endpoint(QTY_LEAF, "/other")

    suite = generate_test_suite(
        make_result_with_endpoints(
            (broken, healthy), diamond_components(6, required=False)
        )
    )

    budget = [item for item in suite.diagnostics if item.code in BUDGET_CODES]
    assert [item.code for item in budget] == ["generation_expansion_budget_exceeded"]
    assert budget[0].endpoint_path == "/items"
    assert budget[0].source_pointer is not None
    assert budget[0].source_pointer.startswith("/components/schemas/L")
    assert budget[0].reference_uri is not None
    assert budget[0].reference_uri.startswith("#/components/schemas/L")
    assert "per-endpoint limit of 20" in budget[0].message
    broken_cases = [case for case in suite.cases if case.path == "/items"]
    assert [case.category for case in broken_cases] == ["positive"]
    assert any(case.path == "/other" and case.category == "negative" for case in suite.cases)


def count_expansions(result: NormalizationResult) -> int:
    counter = {"count": 0}
    original = generator_module._GenerationBudget.charge_expansion

    def counting(self: object, pointer: str, reference_uri: str) -> None:
        counter["count"] += 1
        original(self, pointer, reference_uri)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module._GenerationBudget, "charge_expansion", counting)
        generate_test_suite(result)
    return counter["count"]


def test_suite_expansion_budget_stops_the_remaining_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas = {"Status": STATUS_SCHEMA}
    endpoints = tuple(
        post_endpoint(schema_reference("Status"), f"/items{index}") for index in range(3)
    )
    one_endpoint = count_expansions(make_result_with_endpoints(endpoints[:1], schemas))
    assert one_endpoint > 0
    monkeypatch.setattr(generator_module, "_MAX_SUITE_EXPANSIONS", one_endpoint)

    suite = generate_test_suite(make_result_with_endpoints(endpoints, schemas))

    assert {case.path for case in suite.cases} == {"/items0"}
    assert [item.code for item in suite.diagnostics] == [
        "generation_expansion_budget_exceeded"
    ]
    assert suite.diagnostics[0].endpoint_path == "/items1"
    assert f"per-suite limit of {one_endpoint}" in suite.diagnostics[0].message
    assert "1 following endpoint(s)" in suite.diagnostics[0].message


def test_endpoint_case_budget_drops_the_whole_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 2)
    body = {
        "type": "object",
        "required": ["a", "b", "c"],
        "properties": {name: {"type": "string", "example": "x"} for name in "abc"},
    }

    suite = generate_test_suite(make_result(post_endpoint(body)))

    assert [case.category for case in suite.cases] == ["positive"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "per-endpoint limit of 2" in suite.diagnostics[0].message
    assert "'required_property'" in suite.diagnostics[0].message
    assert suite.diagnostics[0].source_pointer == "/paths/~1items/post"


def test_suite_case_budget_stops_the_remaining_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", 3)
    endpoints = tuple(make_endpoint(f"/e{index}") for index in range(5))

    suite = generate_test_suite(make_result(*endpoints))

    assert [case.path for case in suite.cases] == ["/e0", "/e1", "/e2"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert suite.diagnostics[0].endpoint_path == "/e3"
    assert "per-suite limit of 3" in suite.diagnostics[0].message
    assert "1 following endpoint(s)" in suite.diagnostics[0].message


def test_dropped_step_cases_do_not_consume_the_suite_case_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 2)
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", 2)
    body = {
        "type": "object",
        "required": ["a", "b", "c"],
        "properties": {name: {"type": "string", "example": "x"} for name in "abc"},
    }

    suite = generate_test_suite(
        make_result(post_endpoint(body), make_endpoint("/plain"))
    )

    assert [case.path for case in suite.cases] == ["/items", "/plain"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "per-endpoint" in suite.diagnostics[0].message


def missing_reference_body(count: int) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            f"p{index}": schema_reference(f"Missing{index}") for index in range(count)
        },
    }


def test_endpoint_diagnostic_budget_reports_a_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 2)

    suite = generate_test_suite(make_result(post_endpoint(missing_reference_body(5))))

    assert [item.code for item in suite.diagnostics] == [
        "unsupported_schema_reference",
        "unsupported_schema_reference",
        "generation_diagnostic_budget_exceeded",
    ]
    assert "per-endpoint limit of 2" in suite.diagnostics[2].message
    assert "3 further diagnostics" in suite.diagnostics[2].message
    assert suite.cases[0].category == "positive"


def test_suite_diagnostic_budget_reports_a_suite_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_SUITE_DIAGNOSTICS", 3)

    suite = generate_test_suite(
        make_result(
            post_endpoint(missing_reference_body(2), "/one"),
            post_endpoint(missing_reference_body(2), "/two"),
        )
    )

    assert [item.code for item in suite.diagnostics] == [
        "unsupported_schema_reference",
        "unsupported_schema_reference",
        "unsupported_schema_reference",
        "generation_diagnostic_budget_exceeded",
    ]
    summary = suite.diagnostics[-1]
    assert summary.endpoint_path is None
    assert summary.source_pointer is None
    assert "per-suite limit of 3" in summary.message
    assert "1 further diagnostics" in summary.message


def test_budget_diagnostics_stay_visible_when_the_diagnostic_budget_is_used_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 1)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 2)
    endpoint = make_endpoint(
        "/items",
        "post",
        parameters=tuple(
            Parameter(
                name=f"q{index}",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "x"}),
                source_scope="operation",
            )
            for index in range(3)
        ),
        request_body=body_with_schema(missing_reference_body(4)),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result(endpoint))

    assert [item.code for item in suite.diagnostics] == [
        "unsupported_schema_reference",
        "generation_case_budget_exceeded",
        "generation_diagnostic_budget_exceeded",
    ]
    assert [case.category for case in suite.cases] == ["positive"]


def test_budget_exhaustion_output_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 40)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 6)

    def build() -> NormalizationResult:
        return make_result_with_endpoints(
            (
                post_endpoint(schema_reference("L0"), "/optional"),
                post_endpoint(schema_reference("R0"), "/required"),
            ),
            {
                **diamond_components(6, required=False),
                **{
                    name.replace("L", "R"): _rename_refs(raw)
                    for name, raw in diamond_components(3, required=True).items()
                },
            },
        )

    first = generate_test_suite(build())
    second = generate_test_suite(build())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert BUDGET_CODES & {item.code for item in first.diagnostics}


def _rename_refs(raw: object) -> object:
    return json.loads(json.dumps(raw).replace("schemas/L", "schemas/R"))


def test_each_generation_call_has_its_own_budget_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = generator_module._generate_suite
    seen: list[object] = []
    inner = make_result(make_endpoint("/inner"))

    def wrapped(result: NormalizationResult, budget: object) -> GeneratedTestSuite:
        seen.append(budget)
        if len(seen) == 1:
            generate_test_suite(inner)
            assert generator_module._BUDGET.get() is budget
        return original(result, budget)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_generate_suite", wrapped)

    generate_test_suite(make_result(make_endpoint("/outer")))

    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert generator_module._BUDGET.get() is None


def test_budget_state_is_restored_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(result: NormalizationResult, budget: object) -> GeneratedTestSuite:
        assert generator_module._BUDGET.get() is budget
        raise RuntimeError("boom")

    monkeypatch.setattr(generator_module, "_generate_suite", failing)

    with pytest.raises(RuntimeError):
        generate_test_suite(make_result(make_endpoint("/items")))

    assert generator_module._BUDGET.get() is None
    assert dict(generator_module._SCHEMA_COMPONENTS.get()) == {}


def test_helpers_outside_generation_have_no_budget_limit() -> None:
    assert generator_module._BUDGET.get() is None
    generator_module._charge_expansion("/components/schemas/A", "#/components/schemas/A")


def test_unsupported_negative_step_does_not_stop_later_negative_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["name", "qty"],
        "properties": {
            "name": {"type": "string", "example": "x"},
            "qty": {"type": "integer", "minimum": 1, "maximum": 9, "example": 3},
        },
    }

    def build() -> NormalizationResult:
        return make_result(post_endpoint(body))

    reference = generate_test_suite(build())
    assert any(case.violated_constraint == "minimum" for case in reference.cases)
    assert any(case.violated_constraint == "type" for case in reference.cases)

    entry_counts: dict[str, tuple[int, int]] = {}
    original_wrong_type = generator_module._wrong_type_negative_cases

    def failing_numeric(*args: object) -> object:
        budget = generator_module._BUDGET.get()
        assert budget is not None
        entry_counts["failing"] = budget.case_counts()
        budget.charge_case()
        budget.charge_case()
        raise generator_module._UnsupportedOperation(
            "simulated_unsupported", "Simulated failure.", "/paths/~1items/post"
        )

    def spying_wrong_type(*args: object) -> object:
        budget = generator_module._BUDGET.get()
        assert budget is not None
        entry_counts["next"] = budget.case_counts()
        return original_wrong_type(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_numeric_boundary_negative_cases", failing_numeric)
    monkeypatch.setattr(generator_module, "_wrong_type_negative_cases", spying_wrong_type)

    suite = generate_test_suite(build())

    # The failed step left no cases and gave its case counts back.
    assert not any(case.violated_constraint in {"minimum", "maximum"} for case in suite.cases)
    assert entry_counts["next"] == entry_counts["failing"]
    # The next step still ran and produced the same cases as without the failure.
    wrong_type = [case for case in suite.cases if case.violated_constraint == "type"]
    assert wrong_type == [
        case for case in reference.cases if case.violated_constraint == "type"
    ]
    assert [item.code for item in suite.diagnostics] == ["simulated_unsupported"]
    assert suite.diagnostics[0].endpoint_path == "/items"
    assert suite.diagnostics[0].source_pointer == "/paths/~1items/post"
    expected = [case for case in reference.cases if case.violated_constraint not in {"minimum", "maximum"}]
    assert list(suite.cases) == expected


def make_result_with_responses(
    endpoints: tuple[Endpoint, ...], responses: dict[str, Response | LocalReference]
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=endpoints,
            components=Components(
                responses=responses,
                # NOT_FOUND_RESPONSE refers to it; a dangling response schema
                # reference is now reported, so the fixture declares the target.
                schemas={"Problem": Schema(raw={"type": "object"})},
            ),
        )
    )


def response_reference(name: str) -> LocalReference:
    return LocalReference(uri=f"#/components/responses/{name}")


NOT_FOUND_RESPONSE = Response(
    description="Not found",
    content={
        "application/problem+json": MediaType(),
        "application/json": MediaType(
            schema=Schema(raw={"$ref": "#/components/schemas/Problem"})
        ),
    },
)
OK_RESPONSE = Response(description="OK")
RESPONSE_REFERENCE_POINTER = "/paths/~1items/get/responses/404"


def get_with_responses(
    responses: dict[str, Response | LocalReference], path: str = "/items"
) -> Endpoint:
    return make_endpoint(path, responses=responses)


def expectation_for(suite: GeneratedTestSuite, status: str) -> ResponseExpectation:
    return next(
        item for item in suite.cases[0].response_expectations if item.status_key == status
    )


def test_direct_response_reference_is_resolved_into_the_expectation() -> None:
    suite = generate_test_suite(
        make_result_with_responses(
            (get_with_responses({"200": OK_RESPONSE, "404": response_reference("NotFound")}),),
            {"NotFound": NOT_FOUND_RESPONSE},
        )
    )

    assert suite.diagnostics == ()
    assert expectation_for(suite, "404") == ResponseExpectation(
        status_key="404",
        media_types=("application/json", "application/problem+json"),
        schemas_by_media_type={
            "application/json": Schema(raw={"$ref": "#/components/schemas/Problem"}),
            "application/problem+json": None,
        },
        response_reference_uri="#/components/responses/NotFound",
        resolved_schemas_by_media_type={
            "application/json": ResolvedResponseSchema(
                reference_chain=("#/components/schemas/Problem",),
                pointer="/components/schemas/Problem",
                resolved=Schema(raw={"type": "object"}),
            )
        },
    )
    assert expectation_for(suite, "200").response_reference_uri is None


def test_response_alias_chain_keeps_the_operation_level_reference_uri() -> None:
    suite = generate_test_suite(
        make_result_with_responses(
            (get_with_responses({"404": response_reference("NotFound")}),),
            {"NotFound": response_reference("Shared"), "Shared": NOT_FOUND_RESPONSE},
        )
    )

    assert suite.diagnostics == ()
    expectation = expectation_for(suite, "404")
    assert expectation.response_reference_uri == "#/components/responses/NotFound"
    assert expectation.media_types == ("application/json", "application/problem+json")


def test_mixed_inline_and_referenced_responses_keep_status_order() -> None:
    suite = generate_test_suite(
        make_result_with_responses(
            (
                get_with_responses(
                    {
                        "default": response_reference("NotFound"),
                        "404": response_reference("NotFound"),
                        "400": OK_RESPONSE,
                        "200": OK_RESPONSE,
                    }
                ),
            ),
            {"NotFound": NOT_FOUND_RESPONSE},
        )
    )

    expectations = suite.cases[0].response_expectations
    assert [item.status_key for item in expectations] == ["200", "400", "404", "default"]
    assert [item.response_reference_uri for item in expectations] == [
        None,
        None,
        "#/components/responses/NotFound",
        "#/components/responses/NotFound",
    ]


def single_response_reference_suite(
    entry: LocalReference, responses: dict[str, Response | LocalReference]
) -> GeneratedTestSuite:
    return generate_test_suite(
        make_result_with_responses((get_with_responses({"404": entry}),), responses)
    )


def assert_response_reference_failure(
    suite: GeneratedTestSuite, reference_uri: str, message_part: str
) -> None:
    assert suite.cases == ()
    assert len(suite.diagnostics) == 1
    diagnostic = suite.diagnostics[0]
    assert diagnostic.code == "unsupported_response_reference"
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.endpoint_method == "get"
    assert diagnostic.source_pointer == RESPONSE_REFERENCE_POINTER
    assert diagnostic.reference_uri == reference_uri
    assert message_part in diagnostic.message


def test_missing_response_reference_target_is_diagnosed() -> None:
    suite = single_response_reference_suite(response_reference("Nope"), {})

    assert_response_reference_failure(
        suite, "#/components/responses/Nope", "target was not found"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "#/paths/~1items/get/responses/200",
        "#/components/schemas/NotFound",
        "#/components/requestBodies/NotFound",
        "#Anchor",
        "#",
        "#/components/responses",
        "#/components/responses/",
        "#/components/responses/NotFound/content",
        "#/components/responsesX/NotFound",
    ],
)
def test_unsupported_response_reference_locations_are_diagnosed(uri: str) -> None:
    suite = single_response_reference_suite(
        LocalReference(uri=uri), {"NotFound": NOT_FOUND_RESPONSE}
    )

    assert_response_reference_failure(
        suite, uri, "Only #/components/responses/<name> references are supported"
    )


@pytest.mark.parametrize("uri", ["https://example.test/r.json#/Ok", "not a reference"])
def test_hand_built_external_or_invalid_response_reference_is_diagnosed(uri: str) -> None:
    with pytest.raises(ValidationError):
        LocalReference(uri=uri)
    bypassed = LocalReference.model_construct(uri=uri)

    suite = single_response_reference_suite(bypassed, {})

    assert_response_reference_failure(
        suite, uri, "Only #/components/responses/<name> references are supported"
    )


def test_response_reference_cycles_are_diagnosed() -> None:
    cycle = single_response_reference_suite(
        response_reference("A"),
        {"A": response_reference("B"), "B": response_reference("A")},
    )
    self_reference = single_response_reference_suite(
        response_reference("A"), {"A": response_reference("A")}
    )

    assert_response_reference_failure(cycle, "#/components/responses/A", "cycle")
    assert_response_reference_failure(self_reference, "#/components/responses/A", "cycle")


def test_broken_alias_chain_reports_the_failing_hop() -> None:
    suite = single_response_reference_suite(
        response_reference("A"), {"A": response_reference("Missing")}
    )

    assert_response_reference_failure(
        suite, "#/components/responses/Missing", "target was not found"
    )


@pytest.mark.parametrize(
    ("component_name", "uri_segment"),
    [("a/b", "a~1b"), ("a~b", "a~0b"), ("a b", "a%20b"), ("a~1b", "a~01b")],
)
def test_response_reference_names_follow_pointer_and_percent_conventions(
    component_name: str, uri_segment: str
) -> None:
    uri = f"#/components/responses/{uri_segment}"

    suite = single_response_reference_suite(
        LocalReference(uri=uri), {component_name: NOT_FOUND_RESPONSE}
    )

    assert suite.diagnostics == ()
    assert expectation_for(suite, "404").response_reference_uri == uri
    assert expectation_for(suite, "404").media_types == (
        "application/json",
        "application/problem+json",
    )


def referenced_response_post_endpoint() -> Endpoint:
    return make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(
            {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "example": "x"}},
            }
        ),
        responses={"400": response_reference("NotFound")},
    )


def test_referenced_response_endpoint_gets_positive_and_negative_cases() -> None:
    suite = generate_test_suite(
        make_result_with_responses(
            (referenced_response_post_endpoint(),), {"NotFound": NOT_FOUND_RESPONSE}
        )
    )

    assert suite.diagnostics == ()
    assert [case.category for case in suite.cases][0] == "positive"
    assert any(case.category == "negative" for case in suite.cases)
    positive_expectations = suite.cases[0].response_expectations
    assert positive_expectations[0].response_reference_uri == "#/components/responses/NotFound"
    assert all(case.response_expectations == positive_expectations for case in suite.cases)


def test_referenced_client_error_response_satisfies_the_negative_response_contract() -> None:
    referenced = generate_test_suite(
        make_result_with_responses(
            (referenced_response_post_endpoint(),), {"NotFound": NOT_FOUND_RESPONSE}
        )
    )
    inline_success_only = generate_test_suite(
        make_result_with_responses(
            (
                make_endpoint(
                    "/items",
                    "post",
                    request_body=referenced_response_post_endpoint().request_body,
                    responses={"200": OK_RESPONSE},
                ),
            ),
            {},
        )
    )

    assert "negative_response_contract_unavailable" not in [
        item.code for item in referenced.diagnostics
    ]
    assert "negative_response_contract_unavailable" in [
        item.code for item in inline_success_only.diagnostics
    ]


def test_failing_response_reference_skips_only_its_own_endpoint() -> None:
    suite = generate_test_suite(
        make_result_with_responses(
            (
                get_with_responses({"200": OK_RESPONSE, "404": response_reference("Nope")}, "/bad"),
                get_with_responses({"404": response_reference("NotFound")}, "/good-ref"),
                get_with_responses({"200": OK_RESPONSE}, "/good-inline"),
            ),
            {"NotFound": NOT_FOUND_RESPONSE},
        )
    )

    assert [case.path for case in suite.cases] == ["/good-ref", "/good-inline"]
    assert [item.endpoint_path for item in suite.diagnostics] == ["/bad"]
    assert [item.code for item in suite.diagnostics] == ["unsupported_response_reference"]


def test_referenced_response_generation_is_deterministic() -> None:
    def build() -> NormalizationResult:
        return make_result_with_responses(
            (referenced_response_post_endpoint(), get_with_responses({"404": response_reference("Alias")}, "/other")),
            {"NotFound": NOT_FOUND_RESPONSE, "Alias": response_reference("NotFound")},
        )

    first = generate_test_suite(build())
    second = generate_test_suite(build())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_response_reference_with_annotations_generates_cases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "404": {
                            "$ref": "#/components/responses/NotFound",
                            "summary": "Not found",
                            "description": "Override",
                            "x-note": 1,
                        },
                    }
                }
            }
        },
        "components": {"responses": {"NotFound": {"description": "Not found"}}},
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert suite.diagnostics == ()
    assert expectation_for(suite, "404").response_reference_uri == "#/components/responses/NotFound"


def alias_chain_result(extra: tuple[Endpoint, ...] = ()) -> NormalizationResult:
    return make_result_with_responses(
        (get_with_responses({"404": response_reference("A")}), *extra),
        {
            "A": response_reference("B"),
            "B": response_reference("C"),
            "C": NOT_FOUND_RESPONSE,
        },
    )


def test_response_reference_hops_are_charged_to_the_endpoint_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Three response hops plus one hop for the referenced response schema.
    assert count_expansions(alias_chain_result()) == 4
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 2)

    suite = generate_test_suite(
        alias_chain_result((get_with_responses({"200": OK_RESPONSE}, "/inline"),))
    )

    assert [case.path for case in suite.cases] == ["/inline"]
    assert [item.code for item in suite.diagnostics] == [
        "generation_expansion_budget_exceeded"
    ]
    diagnostic = suite.diagnostics[0]
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.source_pointer == RESPONSE_REFERENCE_POINTER
    assert diagnostic.reference_uri == "#/components/responses/C"
    assert "per-endpoint limit of 2" in diagnostic.message


def test_response_reference_hops_are_charged_to_the_suite_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each endpoint costs two hops: the response and its response schema.
    monkeypatch.setattr(generator_module, "_MAX_SUITE_EXPANSIONS", 4)
    endpoints = tuple(
        get_with_responses({"404": response_reference("NotFound")}, f"/e{index}")
        for index in range(4)
    )

    suite = generate_test_suite(
        make_result_with_responses(endpoints, {"NotFound": NOT_FOUND_RESPONSE})
    )

    assert [case.path for case in suite.cases] == ["/e0", "/e1"]
    assert [item.code for item in suite.diagnostics] == [
        "generation_expansion_budget_exceeded"
    ]
    assert suite.diagnostics[0].endpoint_path == "/e2"
    assert "per-suite limit of 4" in suite.diagnostics[0].message
    assert "1 following endpoint(s)" in suite.diagnostics[0].message


ITEM_SCHEMA: dict[str, object] = {"type": "string", "example": "x"}
ARRAY_BODY_POINTER = "/paths/~1items/post/requestBody/content/application~1json/schema"
ARRAY_CONSTRAINTS = {"minItems", "maxItems", "uniqueItems"}


def tags_body(tags: object, *, required: bool = True) -> dict[str, object]:
    return {
        "type": "object",
        "required": ["tags"] if required else [],
        "properties": {"tags": tags},
    }


def array_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [case for case in suite.cases if case.violated_constraint in ARRAY_CONSTRAINTS]


def array_summary(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None, int]]:
    summary = []
    for case in array_cases(suite):
        assert case.request_body is not None
        value = case.request_body.value
        assert isinstance(value, dict)
        tags = value["tags"]
        assert isinstance(tags, list)
        summary.append((case.affected_property, case.violated_constraint, len(tags)))
    return summary


def is_array_literal_conflict(item: GenerationDiagnostic) -> bool:
    """An ancestor-literal diagnostic of an Array Bounds case (other steps share the code)."""
    return item.code == "negative_case_ancestor_literal_conflict" and item.message.startswith(
        ("The minItems case", "The maxItems case")
    )


def array_diagnostics(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    return [
        (item.code, item.source_pointer)
        for item in suite.diagnostics
        if item.code.startswith("array_bound_")
        or is_array_literal_conflict(item)
    ]


def tags_schema_pointer(*parts: str) -> str:
    return "/".join((f"{ARRAY_BODY_POINTER}/properties/tags", *parts))


def run_array_step(
    body_schema: object, body_value: dict[str, object]
) -> tuple[tuple[GeneratedTestCase, ...], tuple[GenerationDiagnostic, ...]]:
    """Run only the array step against a hand-set positive body.

    Contradictory or malformed bounds on a present property cannot come from a
    successful positive baseline, so the baseline is replaced here.
    """
    result = make_result(post_endpoint(body_schema))
    baseline = generate_test_suite(make_result(post_endpoint({"type": "object"}))).cases[0]
    positive = baseline.model_copy(
        update={"request_body": GeneratedBody(media_type="application/json", value=body_value)}
    )
    return generator_module._array_bound_negative_cases(
        result.api.endpoints[0], positive, result.api
    )


def array_suite(tags: object, **options: bool) -> GeneratedTestSuite:
    return generate_test_suite(make_result(post_endpoint(tags_body(tags, **options))))


def test_min_items_violation_has_one_item_fewer() -> None:
    suite = array_suite({"type": "array", "minItems": 2, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "minItems", 1)]
    case = array_cases(suite)[0]
    assert case.request_body is not None
    assert case.request_body.value == {"tags": ["x"]}
    assert case.category == "negative"
    assert case.expected_outcome == "request_rejection_status_unspecified"
    assert case.provenance[-3:] == (
        tags_schema_pointer(),
        tags_schema_pointer("minItems"),
        "violated_constraint:minItems:/tags",
    )
    assert array_diagnostics(suite) == []


def test_max_items_violation_has_one_item_more() -> None:
    suite = array_suite({"type": "array", "maxItems": 3, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "maxItems", 4)]
    assert array_cases(suite)[0].provenance[-2:] == (
        tags_schema_pointer("maxItems"),
        "violated_constraint:maxItems:/tags",
    )


def test_both_bounds_give_independent_cases() -> None:
    suite = array_suite({"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "minItems", 1), ("/tags", "maxItems", 5)]
    assert len({case.case_id for case in array_cases(suite)}) == 2


def test_min_items_zero_has_no_case_and_max_items_zero_has_one_item() -> None:
    suite = array_suite({"type": "array", "minItems": 0, "maxItems": 0, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "maxItems", 1)]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("minItems"))
    ]


def test_min_items_one_gives_an_empty_array() -> None:
    suite = array_suite({"type": "array", "minItems": 1, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "minItems", 0)]


def test_bounds_around_the_collection_size_limit() -> None:
    at_limit = array_suite({"type": "array", "maxItems": 99, "items": ITEM_SCHEMA})
    above_limit = array_suite({"type": "array", "maxItems": 100, "items": ITEM_SCHEMA})
    large_min = array_suite({"type": "array", "minItems": 100, "items": ITEM_SCHEMA})

    assert array_summary(at_limit) == [("/tags", "maxItems", 100)]
    assert array_summary(above_limit) == []
    assert array_diagnostics(above_limit) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]
    assert array_summary(large_min) == [("/tags", "minItems", 99)]


def test_contradictory_bounds_give_one_diagnostic_and_no_case() -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": 3, "maxItems": 1, "items": ITEM_SCHEMA}),
        {"tags": ["x"]},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_contradictory_bounds", tags_schema_pointer())
    ]


@pytest.mark.parametrize("bound", [-1, 1.5, 2.0, True, "2", None])
def test_malformed_bounds_are_diagnosed_without_a_case(bound: object) -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": bound, "items": ITEM_SCHEMA}),
        {"tags": ["x"]},
    )

    assert cases == ()
    assert [item.code for item in diagnostics] == ["array_bound_unsupported_schema"]
    assert diagnostics[0].source_pointer == tags_schema_pointer("minItems")


@pytest.mark.parametrize(
    ("bounds", "malformed"),
    [
        ({"minItems": 2, "maxItems": -1}, "maxItems"),
        ({"minItems": "2", "maxItems": 4}, "minItems"),
    ],
)
def test_one_malformed_bound_gives_exactly_one_diagnostic(
    bounds: dict[str, object], malformed: str
) -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", **bounds, "items": ITEM_SCHEMA}),
        {"tags": ["a", "b"]},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer(malformed))
    ]


def test_unsupported_items_do_not_block_a_case_that_needs_no_item() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^(a)$"}}
        ),
        {"tags": ["a"]},
    )

    assert diagnostics == ()
    assert [(case.affected_property, case.violated_constraint) for case in cases] == [
        ("/tags", "minItems")
    ]
    assert cases[0].request_body is not None
    assert cases[0].request_body.value == {"tags": []}


def test_unsupported_items_are_reported_once_for_all_candidates_that_need_an_item() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string", "pattern": "^(a)$"},
            }
        ),
        {"tags": ["a", "b"]},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


def test_unsupported_items_still_allow_the_candidate_that_needs_no_item() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string", "pattern": "^(a)$"},
            }
        ),
        {"tags": ["a"]},
    )

    assert [case.violated_constraint for case in cases] == ["minItems"]
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


UNSUPPORTED_ITEMS: dict[str, object] = {"type": "string", "pattern": "^(a)$"}


@pytest.mark.parametrize(
    "literal",
    [{"enum": [[], ["a"]]}, {"const": []}],
    ids=["enum", "const"],
)
def test_unsupported_items_in_enum_or_const_candidates_are_reported_at_their_pointer(
    literal: dict[str, object],
) -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": 1, "items": UNSUPPORTED_ITEMS, **literal}),
        {"tags": ["a"]},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


def test_unsupported_items_in_enum_candidates_are_reported_once_for_both_bounds() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "enum": [[], ["a", "b", "c", "d"]],
                "items": UNSUPPORTED_ITEMS,
            }
        ),
        {"tags": ["a"]},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


def test_enum_candidates_with_usable_items_still_generate_a_case() -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": 1, "enum": [[], ["a"]], "items": ITEM_SCHEMA}),
        {"tags": ["a"]},
    )

    assert diagnostics == ()
    assert [case.violated_constraint for case in cases] == ["minItems"]
    assert cases[0].request_body is not None
    assert cases[0].request_body.value == {"tags": []}


def test_missing_type_with_array_keywords_is_identified_as_an_array() -> None:
    suite = array_suite({"minItems": 2, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "minItems", 1)]
    assert array_diagnostics(suite) == []


def test_type_list_including_array_is_accepted() -> None:
    suite = array_suite(
        {"type": ["array", "null"], "minItems": 2, "items": ITEM_SCHEMA}
    )

    assert array_summary(suite) == [("/tags", "minItems", 1)]


def test_explicit_non_array_type_is_diagnosed_per_constraint() -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "string", "minItems": 2, "maxItems": 3}),
        {"tags": "x"},
    )

    assert cases == ()
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_non_array_type", tags_schema_pointer("minItems")),
        ("array_bound_non_array_type", tags_schema_pointer("maxItems")),
    ]


def test_unique_items_allows_candidates_of_at_most_one_item() -> None:
    zero = array_suite({"type": "array", "maxItems": 0, "uniqueItems": True, "items": ITEM_SCHEMA})
    one = array_suite({"type": "array", "maxItems": 1, "uniqueItems": True, "items": ITEM_SCHEMA})
    shorter = run_array_step(
        tags_body({"type": "array", "minItems": 2, "uniqueItems": True, "items": ITEM_SCHEMA}),
        {"tags": ["a", "b"]},
    )
    too_short = run_array_step(
        tags_body({"type": "array", "minItems": 3, "uniqueItems": True, "items": ITEM_SCHEMA}),
        {"tags": ["a", "b", "c"]},
    )

    assert array_summary(zero) == [("/tags", "maxItems", 1)]
    assert array_summary(one) == []
    # The maxItems diagnostic stays; uniqueItems adds its own (see Step 6.5 tests).
    assert array_diagnostics(one) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems")),
        ("array_bound_no_safe_candidate", tags_schema_pointer("uniqueItems")),
    ]
    assert [case.violated_constraint for case in shorter[0]] == ["minItems", "uniqueItems"]
    assert [(item.code, item.source_pointer) for item in too_short[1]] == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("minItems"))
    ]
    assert [case.violated_constraint for case in too_short[0]] == ["uniqueItems"]


def test_missing_or_unusable_items_are_diagnosed_when_an_item_is_needed() -> None:
    no_items = array_suite({"type": "array", "maxItems": 1})
    false_items = array_suite({"type": "array", "maxItems": 0, "items": False})
    unsupported_items = run_array_step(
        tags_body({"type": "array", "maxItems": 1, "items": {"type": "string", "pattern": "^(a)$"}}),
        {"tags": []},
    )

    assert array_summary(no_items) == []
    assert array_diagnostics(no_items) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]
    assert array_summary(false_items) == []
    assert [code for code, _ in array_diagnostics(false_items)] == ["array_bound_no_safe_candidate"]
    assert unsupported_items[0] == ()
    assert [(item.code, item.source_pointer) for item in unsupported_items[1]] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


def test_min_items_one_needs_no_items_schema() -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": 1}), {"tags": ["x"]}
    )

    assert diagnostics == ()
    assert [case.violated_constraint for case in cases] == ["minItems"]
    assert cases[0].request_body is not None
    assert cases[0].request_body.value == {"tags": []}


def test_array_items_are_independent_copies() -> None:
    suite = array_suite(
        {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer", "example": 1}}},
        }
    )

    case = array_cases(suite)[0]
    assert case.request_body is not None
    tags = case.request_body.value["tags"]  # type: ignore[index]
    assert tags == [{"id": 1}] * 3
    assert tags[0] is not tags[1]


def test_referenced_array_and_item_schemas_are_followed() -> None:
    suite = generate_test_suite(
        make_result_with_schemas(
            post_endpoint(tags_body(schema_reference("Tags"))),
            {
                "Tags": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": schema_reference("Item"),
                },
                "Item": ITEM_SCHEMA,
            },
        )
    )

    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "maxItems", 3)]
    assert array_cases(suite)[1].provenance[-3:] == (
        "/components/schemas/Tags",
        "/components/schemas/Tags/maxItems",
        "violated_constraint:maxItems:/tags",
    )
    assert array_diagnostics(suite) == []


def test_arrays_inside_nested_objects_are_covered() -> None:
    suite = generate_test_suite(
        make_result(
            post_endpoint(
                {
                    "type": "object",
                    "required": ["outer"],
                    "properties": {
                        "outer": {
                            "type": "object",
                            "required": ["tags"],
                            "properties": {
                                "tags": {"type": "array", "minItems": 2, "items": ITEM_SCHEMA}
                            },
                        }
                    },
                }
            )
        )
    )

    cases = array_cases(suite)
    assert [(case.affected_property, case.violated_constraint) for case in cases] == [
        ("/outer/tags", "minItems")
    ]
    assert cases[0].request_body is not None
    assert cases[0].request_body.value == {"outer": {"tags": ["x"]}}


def test_nested_array_is_covered_through_its_first_element() -> None:
    nested_array = generate_test_suite(
        make_result(
            post_endpoint(
                tags_body(
                    {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "array", "minItems": 2, "items": ITEM_SCHEMA},
                    }
                )
            )
        )
    )

    assert array_summary(nested_array) == [("/tags", "minItems", 0), ("/tags/0", "minItems", 1)]


def test_a_malformed_property_does_not_stop_other_properties() -> None:
    body = {
        "type": "object",
        "required": ["bad", "good"],
        "properties": {
            "bad": {"type": "array", "minItems": -1, "items": ITEM_SCHEMA},
            "good": {"type": "array", "minItems": 2, "items": ITEM_SCHEMA},
        },
    }

    cases, diagnostics = run_array_step(body, {"bad": ["x"], "good": ["a", "b"]})

    assert [(case.affected_property, case.violated_constraint) for case in cases] == [
        ("/good", "minItems")
    ]
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        (
            "array_bound_unsupported_schema",
            f"{ARRAY_BODY_POINTER}/properties/bad/minItems",
        )
    ]


def test_a_failing_earlier_step_does_not_stop_the_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(*args: object) -> object:
        raise generator_module._UnsupportedOperation(
            "simulated_unsupported", "Simulated failure.", "/paths/~1items/post"
        )

    monkeypatch.setattr(generator_module, "_string_boundary_negative_cases", failing)

    suite = array_suite({"type": "array", "minItems": 2, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "minItems", 1)]
    assert [item.code for item in suite.diagnostics] == ["simulated_unsupported"]


def test_array_step_case_budget_drops_only_the_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = {"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA}
    reference = array_suite(tags)
    before_array = len(reference.cases) - 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 1)

    suite = array_suite(tags)

    assert array_cases(suite) == []
    assert suite.cases == reference.cases[:before_array]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


def test_array_step_expansion_budget_drops_only_the_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build() -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint(
                tags_body({"type": "array", "minItems": 2, "items": schema_reference("Item")})
            ),
            {"Item": ITEM_SCHEMA},
        )

    # The valid-edge steps run after array_bounds; their hops are not part of this test.
    monkeypatch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        hops_without_array_step = count_expansions(build())
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops_without_array_step)

    suite = generate_test_suite(build())

    assert array_cases(suite) == []
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message
    assert suite.cases[0].category == "positive"


def test_array_cases_are_deterministic_with_stable_ids() -> None:
    def build() -> NormalizationResult:
        return make_result(
            post_endpoint(
                tags_body({"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA})
            )
        )

    first = generate_test_suite(build())
    second = generate_test_suite(build())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in array_cases(first)] == [
        generator_module.build_case_id("negative", "/items", "post", "array-bound:/tags:minItems:1"),
        generator_module.build_case_id("negative", "/items", "post", "array-bound:/tags:maxItems:5"),
    ]


def test_array_step_is_appended_and_leaves_existing_cases_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = {"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA}
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        without_step = array_suite(tags)

    with_step = array_suite(tags)

    assert with_step.cases == (*without_step.cases, *array_cases(with_step))
    assert with_step.diagnostics == without_step.diagnostics
    no_bounds = array_suite({"type": "array", "items": ITEM_SCHEMA})
    assert array_cases(no_bounds) == []
    assert array_diagnostics(no_bounds) == []


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_generate_array_bound_cases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": tags_body(
                                    {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 2,
                                        "items": {"type": "string", "example": "x"},
                                    }
                                )
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "maxItems", 3)]
    assert array_diagnostics(suite) == []


# --- Step 6.5: uniqueItems violations -------------------------------------------

UNIQUE_ITEM_OBJECT: dict[str, object] = {
    "type": "object",
    "required": ["id"],
    "properties": {"id": {"type": "integer", "example": 1}},
}


def unique_cases(cases: object) -> list[GeneratedTestCase]:
    return [case for case in cases if case.violated_constraint == "uniqueItems"]  # type: ignore[attr-defined]


def tags_of(case: GeneratedTestCase) -> list[object]:
    assert case.request_body is not None
    value = case.request_body.value
    assert isinstance(value, dict)
    tags = value["tags"]
    assert isinstance(tags, list)
    return tags


def step_diagnostics(diagnostics: tuple[GenerationDiagnostic, ...]) -> list[tuple[str, str | None]]:
    return [(item.code, item.source_pointer) for item in diagnostics]


def test_array_with_only_unique_items_generates_a_duplicate_pair() -> None:
    suite = array_suite({"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA})

    assert array_summary(suite) == [("/tags", "uniqueItems", 2)]
    case = array_cases(suite)[0]
    assert tags_of(case) == ["x", "x"]
    assert case.category == "negative"
    assert case.expected_outcome == "request_rejection_status_unspecified"
    assert case.case_id == generator_module.build_case_id(
        "negative", "/items", "post", "array-bound:/tags:uniqueItems:2"
    )
    assert case.provenance[-3:] == (
        tags_schema_pointer(),
        tags_schema_pointer("uniqueItems"),
        "violated_constraint:uniqueItems:/tags",
    )
    assert suite.cases[0].request_body is not None
    assert suite.cases[0].request_body.value == {"tags": []}
    assert array_diagnostics(suite) == []


def test_unique_items_with_min_items_three_uses_three_duplicate_items() -> None:
    cases, diagnostics = run_array_step(
        tags_body({"type": "array", "minItems": 3, "uniqueItems": True, "items": ITEM_SCHEMA}),
        {"tags": ["a", "b", "c"]},
    )

    assert [case.violated_constraint for case in cases] == ["uniqueItems"]
    assert tags_of(cases[0]) == ["x", "x", "x"]
    assert step_diagnostics(diagnostics) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("minItems"))
    ]


def test_unique_items_with_both_bounds_follows_the_existing_cases() -> None:
    suite = array_suite(
        {"type": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True, "items": ITEM_SCHEMA}
    )

    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "uniqueItems", 2)]
    assert tags_of(array_cases(suite)[1]) == ["x", "x"]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]


def test_unique_items_with_max_items_below_two_keeps_both_diagnostics() -> None:
    zero = array_suite({"type": "array", "maxItems": 0, "uniqueItems": True, "items": ITEM_SCHEMA})
    one = array_suite({"type": "array", "maxItems": 1, "uniqueItems": True, "items": ITEM_SCHEMA})
    two = array_suite({"type": "array", "maxItems": 2, "uniqueItems": True, "items": ITEM_SCHEMA})

    assert array_summary(zero) == [("/tags", "maxItems", 1)]
    assert array_diagnostics(zero) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("uniqueItems"))
    ]
    assert array_summary(one) == []
    assert array_diagnostics(one) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems")),
        ("array_bound_no_safe_candidate", tags_schema_pointer("uniqueItems")),
    ]
    assert array_summary(two) == [("/tags", "uniqueItems", 2)]
    assert array_diagnostics(two) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]


@pytest.mark.parametrize("unique", [{"uniqueItems": False}, {}], ids=["false", "absent"])
def test_unique_items_false_or_absent_gives_no_unique_case(unique: dict[str, object]) -> None:
    only_flag = array_suite({"type": "array", "items": ITEM_SCHEMA, **unique})
    bounded = array_suite({"type": "array", "minItems": 2, "items": ITEM_SCHEMA, **unique})

    assert array_cases(only_flag) == []
    assert array_diagnostics(only_flag) == []
    assert array_summary(bounded) == [("/tags", "minItems", 1)]
    assert array_diagnostics(bounded) == []


def test_unique_items_false_and_absent_give_identical_suites() -> None:
    schema = {"type": "array", "minItems": 1, "maxItems": 3, "items": ITEM_SCHEMA}

    assert array_suite({**schema, "uniqueItems": False}) == array_suite(schema)


@pytest.mark.parametrize(
    "items",
    [{}, {"items": False}],
    ids=["missing", "false"],
)
def test_unique_items_without_a_usable_items_schema_has_no_safe_candidate(
    items: dict[str, object],
) -> None:
    suite = array_suite({"type": "array", "uniqueItems": True, **items})

    assert array_cases(suite) == []
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("uniqueItems"))
    ]


def test_unsupported_items_are_reported_once_across_constraints() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "uniqueItems": True,
                "items": UNSUPPORTED_ITEMS,
            }
        ),
        {"tags": ["a", "b"]},
    )

    assert cases == ()
    assert step_diagnostics(diagnostics) == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern")),
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems")),
    ]


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ({"type": "string", "enum": ["a", "b"]}, "a"),
        ({"const": "c"}, "c"),
        ({"type": "integer", "minimum": 5}, 5),
        ({"type": "string", "minLength": 3, "maxLength": 4}, "sent"),
        (UNIQUE_ITEM_OBJECT, {"id": 1}),
    ],
    ids=["enum", "const", "numeric", "string", "object"],
)
def test_unique_items_duplicates_a_valid_item(items: dict[str, object], expected: object) -> None:
    suite = array_suite({"type": "array", "uniqueItems": True, "items": items})

    assert array_summary(suite) == [("/tags", "uniqueItems", 2)]
    assert tags_of(array_cases(suite)[0]) == [expected, expected]
    assert array_diagnostics(suite) == []


def test_unique_items_copies_are_independent() -> None:
    suite = array_suite({"type": "array", "uniqueItems": True, "items": UNIQUE_ITEM_OBJECT})

    tags = tags_of(array_cases(suite)[0])
    assert tags[0] is not tags[1]
    tags[0]["id"] = 99  # type: ignore[index]
    assert tags[1] == {"id": 1}
    assert UNIQUE_ITEM_OBJECT["properties"]["id"] == {"type": "integer", "example": 1}  # type: ignore[index]


def test_array_level_enum_selects_the_first_literal_with_a_duplicate() -> None:
    suite = array_suite(
        {
            "type": "array",
            "uniqueItems": True,
            "items": ITEM_SCHEMA,
            "enum": [["a", "b"], ["a", "a"], ["z", "z"], ["c"]],
        }
    )

    assert array_summary(suite) == [("/tags", "uniqueItems", 2)]
    assert tags_of(array_cases(suite)[0]) == ["a", "a"]
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"enum": [["a", "b"], ["c"]]},
        {"const": ["a", "b"]},
        {"enum": [["a", "b"], ["a", "a", "a"]], "maxItems": 2},
        {"enum": [["a", "b"], [1, 1]]},
    ],
    ids=["no-duplicate", "const", "violates-other-bound", "invalid-items"],
)
def test_array_level_literals_without_a_usable_duplicate_have_no_safe_candidate(
    schema: dict[str, object],
) -> None:
    suite = array_suite({"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA, **schema})

    assert array_cases(suite) == []
    assert [
        pointer
        for code, pointer in array_diagnostics(suite)
        if code == "array_bound_no_safe_candidate"
    ].count(tags_schema_pointer("uniqueItems")) == 1


def test_unsupported_items_in_enum_literals_are_reported_once_at_their_pointer() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "enum": [[], ["a", "a"]],
                "items": UNSUPPORTED_ITEMS,
            }
        ),
        {"tags": ["a"]},
    )

    assert cases == ()
    assert step_diagnostics(diagnostics) == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern")),
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems")),
    ]


def test_unique_items_follows_referenced_array_and_item_schemas() -> None:
    def build(tags: dict[str, object]) -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint(tags_body(schema_reference("Tags"))),
            {"Tags": tags, "Item": ITEM_SCHEMA},
        )

    unique = build({"type": "array", "minItems": 1, "uniqueItems": True, "items": schema_reference("Item")})
    baseline = build({"type": "array", "minItems": 1, "maxItems": 1, "items": schema_reference("Item")})

    suite = generate_test_suite(unique)

    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "uniqueItems", 2)]
    assert array_cases(suite)[1].provenance[-3:] == (
        "/components/schemas/Tags",
        "/components/schemas/Tags/uniqueItems",
        "violated_constraint:uniqueItems:/tags",
    )
    assert array_diagnostics(suite) == []
    # One item selection per property, shared by every constraint that needs it.
    assert count_expansions(unique) == count_expansions(baseline)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_explicit_non_array_type_reports_unique_items_as_not_applicable(version: str) -> None:
    suite = generate_test_suite(
        make_result(
            post_endpoint(tags_body({"type": "string", "maxItems": 3, "uniqueItems": True})),
            version=version,
        )
    )

    assert array_cases(suite) == []
    assert [
        (item.code, item.source_pointer, item.message)
        for item in suite.diagnostics
        if item.code.startswith("array_bound_")
    ] == [
        (
            "array_bound_non_array_type",
            tags_schema_pointer("maxItems"),
            "Array-size constraints do not apply to this property's declared type.",
        ),
        (
            "array_bound_non_array_type",
            tags_schema_pointer("uniqueItems"),
            "uniqueItems does not apply to this property's declared type.",
        ),
    ]


def test_contradictory_bounds_still_give_one_diagnostic_with_unique_items() -> None:
    cases, diagnostics = run_array_step(
        tags_body(
            {"type": "array", "minItems": 3, "maxItems": 2, "uniqueItems": True, "items": ITEM_SCHEMA}
        ),
        {"tags": []},
    )

    assert cases == ()
    assert step_diagnostics(diagnostics) == [
        ("array_bound_contradictory_bounds", tags_schema_pointer())
    ]


def test_unique_items_respects_the_collection_size_limit() -> None:
    at_limit = run_array_step(
        tags_body({"type": "array", "minItems": 100, "uniqueItems": True, "items": ITEM_SCHEMA}),
        {"tags": []},
    )
    above_limit = run_array_step(
        tags_body({"type": "array", "minItems": 101, "uniqueItems": True, "items": ITEM_SCHEMA}),
        {"tags": []},
    )

    assert [len(tags_of(case)) for case in unique_cases(at_limit[0])] == [100]
    assert unique_cases(above_limit[0]) == []
    assert ("array_bound_no_safe_candidate", tags_schema_pointer("uniqueItems")) in step_diagnostics(
        above_limit[1]
    )


def test_unique_items_cases_are_deterministic_isolated_and_do_not_mutate_input() -> None:
    def build() -> NormalizationResult:
        return make_result(
            post_endpoint(
                tags_body({"type": "array", "minItems": 1, "uniqueItems": True, "items": UNIQUE_ITEM_OBJECT})
            )
        )

    result = build()
    before = result.model_dump_json()
    first = generate_test_suite(result)
    second = generate_test_suite(build())

    assert result.model_dump_json() == before
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in array_cases(first)] == [
        generator_module.build_case_id("negative", "/items", "post", "array-bound:/tags:minItems:0"),
        generator_module.build_case_id("negative", "/items", "post", "array-bound:/tags:uniqueItems:2"),
    ]
    assert first.cases[0].request_body is not None
    assert first.cases[0].request_body.value == {"tags": [{"id": 1}]}
    assert tags_of(array_cases(first)[1]) == [{"id": 1}, {"id": 1}]


def test_unique_items_case_follows_the_existing_cases_of_its_property() -> None:
    body = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {
            "a": {"type": "array", "minItems": 1, "uniqueItems": True, "items": ITEM_SCHEMA},
            "b": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA},
        },
    }
    without = generate_test_suite(make_result(post_endpoint({
        **body, "properties": {**body["properties"], "a": {**body["properties"]["a"], "uniqueItems": False}}  # type: ignore[index]
    })))
    with_unique = generate_test_suite(make_result(post_endpoint(body)))

    def summary(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None]]:
        return [(case.affected_property, case.violated_constraint) for case in array_cases(suite)]

    assert summary(without) == [("/a", "minItems"), ("/b", "minItems")]
    assert summary(with_unique) == [("/a", "minItems"), ("/a", "uniqueItems"), ("/b", "minItems")]
    kept = [case for case in array_cases(with_unique) if case.violated_constraint != "uniqueItems"]
    assert kept == array_cases(without)


def test_unique_items_on_a_nested_array_is_covered_through_its_first_element() -> None:
    nested_array = array_suite(
        {
            "type": "array",
            "minItems": 1,
            "items": {"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA},
        }
    )
    nested_object = generate_test_suite(
        make_result(
            post_endpoint(
                {
                    "type": "object",
                    "required": ["outer"],
                    "properties": {
                        "outer": {
                            "type": "object",
                            "required": ["tags"],
                            "properties": {"tags": {"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA}},
                        }
                    },
                }
            )
        )
    )

    assert [(case.affected_property, body_value(case)) for case in unique_cases(nested_array.cases)] == [
        ("/tags/0", {"tags": [["x", "x"]]})
    ]
    assert array_diagnostics(nested_array) == []
    assert [(case.affected_property, case.violated_constraint) for case in array_cases(nested_object)] == [
        ("/outer/tags", "uniqueItems")
    ]


def test_unique_items_case_budget_drops_the_whole_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = {"type": "array", "minItems": 1, "uniqueItems": True, "items": ITEM_SCHEMA}
    reference = array_suite(tags)
    before_array = len(reference.cases) - 2
    assert len(array_cases(reference)) == 2

    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 2)
        assert array_suite(tags).cases == reference.cases

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 1)
    suite = array_suite(tags)

    assert array_cases(suite) == []
    assert suite.cases == reference.cases[:before_array]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


def test_unique_items_expansion_budget_drops_the_whole_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build() -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint(
                tags_body({"type": "array", "uniqueItems": True, "items": schema_reference("Item")})
            ),
            {"Item": ITEM_SCHEMA},
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        hops_without_array_step = count_expansions(build())
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops_without_array_step)

    suite = generate_test_suite(build())

    assert array_cases(suite) == []
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_generate_unique_items_cases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": tags_body(
                                    {
                                        "type": "array",
                                        "minItems": 1,
                                        "uniqueItems": True,
                                        "items": {"type": "string", "example": "x"},
                                    }
                                )
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "uniqueItems", 2)]
    assert tags_of(array_cases(suite)[1]) == ["x", "x"]
    assert array_diagnostics(suite) == []


# --- Step 7.1: security scheme references --------------------------------------

SECURITY_API_KEY = SecurityScheme(type="apiKey", name="X-Key", location="header")
SECURITY_BASE = "#/components/securitySchemes/"


def security_ref(name: str) -> LocalReference:
    return LocalReference(uri=f"{SECURITY_BASE}{name}")


def requirement(*names: str) -> SecurityRequirement:
    return SecurityRequirement(schemes={name: () for name in names})


def security_endpoint(
    path: str = "/items", security: tuple[SecurityRequirement, ...] | None = None
) -> Endpoint:
    return Endpoint(
        path=path,
        method="get",
        responses={"200": Response(description="OK")},
        security=security,
    )


def security_result(
    schemes: dict[str, SecurityScheme | LocalReference],
    *endpoints: Endpoint,
    api_security: tuple[SecurityRequirement, ...] | None = None,
    version: str = "3.1.1",
) -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version=version,
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=endpoints or (security_endpoint(),),
            security=api_security,
            components=Components(security_schemes=schemes),
        )
    )


def security_diagnostics(
    suite: GeneratedTestSuite,
) -> list[tuple[str, str | None, str | None, str | None]]:
    return [
        (item.code, item.endpoint_path, item.source_pointer, item.reference_uri)
        for item in suite.diagnostics
    ]


@pytest.mark.parametrize(
    "scheme",
    [
        SecurityScheme(type="apiKey", name="X-Key", location="query"),
        SecurityScheme(type="http", scheme="bearer", bearer_format="JWT"),
        SecurityScheme(
            type="oauth2",
            flows={"clientCredentials": {"tokenUrl": "https://auth.example.test/t", "scopes": {}}},
        ),
        SecurityScheme(type="openIdConnect", openid_connect_url="https://auth.example.test/o"),
    ],
    ids=["apiKey", "http", "oauth2", "openIdConnect"],
)
def test_inline_supported_security_schemes_generate_cases(scheme: SecurityScheme) -> None:
    result = security_result({"s": scheme}, api_security=(requirement("s"),))

    suite = generate_test_suite(result)

    assert [case.security for case in suite.cases] == [(requirement("s"),)]
    assert suite.diagnostics == ()
    assert count_expansions(result) == 0


def test_mutual_tls_is_supported_only_for_openapi_3_1() -> None:
    scheme = SecurityScheme(type="mutualTLS")
    accepted = generate_test_suite(
        security_result({"m": scheme}, api_security=(requirement("m"),), version="3.1.1")
    )
    rejected = generate_test_suite(
        security_result({"m": scheme}, api_security=(requirement("m"),), version="3.0.3")
    )

    assert len(accepted.cases) == 1 and accepted.diagnostics == ()
    assert rejected.cases == ()
    assert security_diagnostics(rejected) == [
        ("unsupported_security_scheme", "/items", "/components/securitySchemes/m", None)
    ]
    assert "OpenAPI 3.1" in rejected.diagnostics[0].message


def test_single_security_scheme_reference_is_resolved_and_charged() -> None:
    result = security_result(
        {"a": security_ref("b"), "b": SECURITY_API_KEY}, api_security=(requirement("a"),)
    )

    suite = generate_test_suite(result)

    assert [case.security for case in suite.cases] == [(requirement("a"),)]
    assert suite.diagnostics == ()
    assert count_expansions(result) == 1


def test_security_scheme_alias_chain_is_resolved_and_every_hop_charged() -> None:
    result = security_result(
        {
            "a": security_ref("b"),
            "b": security_ref("c"),
            "c": security_ref("d"),
            "d": SECURITY_API_KEY,
        },
        api_security=(requirement("a"),),
    )

    suite = generate_test_suite(result)

    assert len(suite.cases) == 1 and suite.diagnostics == ()
    assert count_expansions(result) == 3


def test_undeclared_security_scheme_name_skips_the_endpoint() -> None:
    suite = generate_test_suite(
        security_result({}, api_security=(requirement("ghost"),))
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scheme_reference", "/items", "/security/0/ghost", None)
    ]


def test_dangling_security_scheme_reference_reports_the_failing_hop() -> None:
    suite = generate_test_suite(
        security_result({"a": security_ref("zzz")}, api_security=(requirement("a"),))
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/security/0/a",
            f"{SECURITY_BASE}zzz",
        )
    ]


@pytest.mark.parametrize(
    ("schemes", "failing_uri"),
    [
        ({"a": security_ref("a")}, f"{SECURITY_BASE}a"),
        ({"a": security_ref("b"), "b": security_ref("a")}, f"{SECURITY_BASE}b"),
        (
            {"a": security_ref("b"), "b": security_ref("c"), "c": security_ref("b")},
            f"{SECURITY_BASE}b",
        ),
    ],
    ids=["self", "two-step", "tail-loop"],
)
def test_security_scheme_reference_cycles_are_reported(
    schemes: dict[str, SecurityScheme | LocalReference], failing_uri: str
) -> None:
    suite = generate_test_suite(security_result(schemes, api_security=(requirement("a"),)))

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scheme_reference", "/items", "/security/0/a", failing_uri)
    ]
    assert "cycle" in suite.diagnostics[0].message


@pytest.mark.parametrize(
    "uri",
    [
        "#/components/schemas/Pet",
        "#/components/securitySchemes/a/b",
        "#/components/securitySchemes/",
        "#/components/securitySchemes",
        "#",
        "#/securitySchemes/a",
    ],
    ids=["wrong-category", "extra-segment", "empty-name", "no-name", "root", "wrong-prefix"],
)
def test_invalid_security_scheme_reference_paths_are_reported(uri: str) -> None:
    suite = generate_test_suite(
        security_result({"a": LocalReference(uri=uri)}, api_security=(requirement("a"),))
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scheme_reference", "/items", "/security/0/a", uri)
    ]
    assert "#/components/securitySchemes/<name>" in suite.diagnostics[0].message


@pytest.mark.parametrize(
    ("target", "reference_name"),
    [("a/b", "a~1b"), ("a b", "a%20b"), ("a~b", "a~0b"), ("a~1b", "a~01b"), ("a/b", "a%2Fb")],
    ids=["slash", "space", "tilde", "decode-order", "encoded-slash"],
)
def test_security_scheme_reference_names_are_decoded(target: str, reference_name: str) -> None:
    suite = generate_test_suite(
        security_result(
            {"alias": security_ref(reference_name), target: SECURITY_API_KEY},
            api_security=(requirement("alias"),),
        )
    )

    assert len(suite.cases) == 1 and suite.diagnostics == ()


def test_unknown_or_unsupported_security_scheme_types_are_reported() -> None:
    direct = generate_test_suite(
        security_result({"s": SecurityScheme(type="weird")}, api_security=(requirement("s"),))
    )
    through_alias = generate_test_suite(
        security_result(
            {"a": security_ref("s"), "s": SecurityScheme(type="APIKEY")},
            api_security=(requirement("a"),),
        )
    )

    assert direct.cases == () and through_alias.cases == ()
    assert security_diagnostics(direct) == [
        ("unsupported_security_scheme", "/items", "/components/securitySchemes/s", None)
    ]
    assert security_diagnostics(through_alias) == [
        (
            "unsupported_security_scheme",
            "/items",
            "/components/securitySchemes/s",
            f"{SECURITY_BASE}s",
        )
    ]


def test_operation_security_overrides_api_security() -> None:
    suite = generate_test_suite(
        security_result(
            {"ok": SECURITY_API_KEY},
            security_endpoint(security=(requirement("ok"),)),
            api_security=(requirement("undeclared"),),
        )
    )
    broken_operation = generate_test_suite(
        security_result(
            {"ok": SECURITY_API_KEY},
            security_endpoint(security=(requirement("bad"),)),
            api_security=(requirement("ok"),),
        )
    )

    assert [case.security for case in suite.cases] == [(requirement("ok"),)]
    assert suite.diagnostics == ()
    assert broken_operation.cases == ()
    assert security_diagnostics(broken_operation) == [
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/paths/~1items/get/security/0/bad",
            None,
        )
    ]


def test_api_security_is_inherited_when_operation_security_is_none() -> None:
    valid = generate_test_suite(
        security_result({"ok": SECURITY_API_KEY}, api_security=(requirement("ok"),))
    )
    broken = generate_test_suite(security_result({}, api_security=(requirement("bad"),)))

    assert [case.security for case in valid.cases] == [(requirement("ok"),)]
    assert broken.cases == ()
    assert security_diagnostics(broken)[0][2] == "/security/0/bad"


def test_empty_operation_security_looks_nothing_up() -> None:
    result = security_result(
        {"a": security_ref("zzz")},
        security_endpoint(security=()),
        api_security=(requirement("a"), requirement("undeclared")),
    )

    suite = generate_test_suite(result)

    assert [case.security for case in suite.cases] == [()]
    assert suite.diagnostics == ()
    assert count_expansions(result) == 0


@pytest.mark.parametrize("level", ["api", "operation"])
def test_anonymous_security_requirement_looks_nothing_up(level: str) -> None:
    anonymous = (SecurityRequirement(schemes={}),)
    endpoint = security_endpoint(security=anonymous if level == "operation" else None)
    result = security_result(
        {}, endpoint, api_security=anonymous if level == "api" else None
    )

    suite = generate_test_suite(result)

    assert [case.security for case in suite.cases] == [anonymous]
    assert suite.diagnostics == ()


def test_security_alternatives_keep_or_semantics_and_fail_the_whole_endpoint() -> None:
    schemes = {"good": SECURITY_API_KEY, "other": SECURITY_API_KEY}
    valid = generate_test_suite(
        security_result(schemes, api_security=(requirement("good"), requirement("other", "good")))
    )
    broken = generate_test_suite(
        security_result(
            schemes,
            api_security=(
                requirement("good"),
                SecurityRequirement(schemes={}),
                requirement("good", "missing"),
            ),
        )
    )

    assert [case.security for case in valid.cases] == [
        (requirement("good"), requirement("other", "good"))
    ]
    assert broken.cases == ()
    assert security_diagnostics(broken) == [
        ("unsupported_security_scheme_reference", "/items", "/security/2/missing", None)
    ]


def test_failures_follow_requirement_order_then_sorted_names() -> None:
    first = generate_test_suite(
        security_result({}, api_security=(requirement("b_missing", "a_missing"),))
    )
    second = generate_test_suite(
        security_result({}, api_security=(requirement("x"), requirement("a")))
    )

    assert [item[2] for item in security_diagnostics(first)] == [
        "/security/0/a_missing",
        "/security/0/b_missing",
    ]
    assert [item[2] for item in security_diagnostics(second)] == [
        "/security/0/x",
        "/security/1/a",
    ]


def test_missing_names_in_different_requirements_are_all_reported() -> None:
    suite = generate_test_suite(
        security_result(
            {},
            security_endpoint("/items", (requirement("one"), requirement("two"))),
        )
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/paths/~1items/get/security/0/one",
            None,
        ),
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/paths/~1items/get/security/1/two",
            None,
        ),
    ]


def test_invalid_schemes_in_one_requirement_are_reported_in_sorted_name_order() -> None:
    suite = generate_test_suite(
        security_result(
            {"zeta": SecurityScheme(type="mutualTLS"), "beta": SecurityScheme(type="mutualTLS")},
            security_endpoint("/items", (requirement("zeta", "missing", "beta"),)),
            version="3.0.3",
        )
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scheme", "/items", "/components/securitySchemes/beta", None),
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/paths/~1items/get/security/0/missing",
            None,
        ),
        ("unsupported_security_scheme", "/items", "/components/securitySchemes/zeta", None),
    ]


def test_a_valid_scheme_does_not_hide_other_invalid_schemes() -> None:
    suite = generate_test_suite(
        security_result(
            {"ok": SECURITY_API_KEY},
            security_endpoint("/items", (requirement("ok", "ghost"), requirement("ok"))),
        )
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/paths/~1items/get/security/0/ghost",
            None,
        )
    ]


def test_identical_failures_are_reported_once_per_pointer_and_reference_uri() -> None:
    bad = SecurityScheme(type="mutualTLS")
    suite = generate_test_suite(
        security_result(
            {"bad": bad, "alias": security_ref("bad"), "other": security_ref("bad")},
            security_endpoint(
                "/items",
                (
                    requirement("bad"),
                    requirement("bad"),
                    requirement("alias"),
                    requirement("other"),
                ),
            ),
            version="3.0.3",
        )
    )

    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scheme", "/items", "/components/securitySchemes/bad", None),
        (
            "unsupported_security_scheme",
            "/items",
            "/components/securitySchemes/bad",
            f"{SECURITY_BASE}bad",
        ),
    ]


def test_identical_failures_of_different_endpoints_are_not_merged() -> None:
    suite = generate_test_suite(
        security_result(
            {"bad": SecurityScheme(type="mutualTLS")},
            security_endpoint("/a", (requirement("bad"),)),
            security_endpoint("/b", (requirement("bad"),)),
            version="3.0.3",
        )
    )

    assert [item[1] for item in security_diagnostics(suite)] == ["/a", "/b"]


def test_an_endpoint_with_several_failures_generates_no_partial_cases() -> None:
    suite = generate_test_suite(
        security_result(
            {"ok": SECURITY_API_KEY},
            security_endpoint("/a", (requirement("ok"),)),
            security_endpoint("/b", (requirement("ok"), requirement("x"), requirement("y"))),
            security_endpoint("/c", (requirement("ok"),)),
        )
    )

    assert {case.path for case in suite.cases} == {"/a", "/c"}
    assert [item[:3] for item in security_diagnostics(suite)] == [
        ("unsupported_security_scheme_reference", "/b", "/paths/~1b/get/security/1/x"),
        ("unsupported_security_scheme_reference", "/b", "/paths/~1b/get/security/2/y"),
    ]


def test_budget_exhaustion_after_an_ordinary_failure_keeps_only_the_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemes: dict[str, SecurityScheme | LocalReference] = {
        "a": security_ref("b"),
        "b": security_ref("c"),
        "c": security_ref("d"),
        "d": SECURITY_API_KEY,
    }
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 2)

    suite = generate_test_suite(
        security_result(
            schemes,
            security_endpoint("/items", (requirement("ghost"), requirement("a"))),
            security_endpoint("/inline", ()),
        )
    )

    assert [case.path for case in suite.cases] == ["/inline"]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert suite.diagnostics[0].endpoint_path == "/items"
    assert suite.diagnostics[0].source_pointer == "/paths/~1items/get/security/1/a"


def test_a_broken_security_scheme_does_not_stop_other_endpoints() -> None:
    suite = generate_test_suite(
        security_result(
            {"ok": SECURITY_API_KEY},
            security_endpoint("/a", (requirement("ok"),)),
            security_endpoint("/b", (requirement("ghost"),)),
            security_endpoint("/c", (requirement("ok"),)),
        )
    )

    assert [case.path for case in suite.cases] == ["/a", "/c"]
    assert security_diagnostics(suite) == [
        (
            "unsupported_security_scheme_reference",
            "/b",
            "/paths/~1b/get/security/0/ghost",
            None,
        )
    ]


def test_security_reference_hops_are_charged_to_the_endpoint_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemes: dict[str, SecurityScheme | LocalReference] = {
        "a": security_ref("b"),
        "b": security_ref("c"),
        "c": security_ref("d"),
        "d": SECURITY_API_KEY,
    }
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 2)

    suite = generate_test_suite(
        security_result(
            schemes,
            security_endpoint("/items", (requirement("a"),)),
            security_endpoint("/inline", ()),
        )
    )

    assert [case.path for case in suite.cases] == ["/inline"]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    diagnostic = suite.diagnostics[0]
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.source_pointer == "/paths/~1items/get/security/0/a"
    assert diagnostic.reference_uri == f"{SECURITY_BASE}d"
    assert "per-endpoint limit of 2" in diagnostic.message


def test_security_reference_hops_are_charged_to_the_suite_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_SUITE_EXPANSIONS", 2)
    suite = generate_test_suite(
        security_result(
            {"a": security_ref("b"), "b": SECURITY_API_KEY},
            *(security_endpoint(f"/e{index}") for index in range(4)),
            api_security=(requirement("a"),),
        )
    )

    assert [case.path for case in suite.cases] == ["/e0", "/e1"]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert suite.diagnostics[0].endpoint_path == "/e2"
    assert "per-suite limit of 2" in suite.diagnostics[0].message
    assert "1 following endpoint(s)" in suite.diagnostics[0].message


def test_security_validation_is_deterministic_and_does_not_mutate_input() -> None:
    def build() -> NormalizationResult:
        return security_result(
            {"ok": SECURITY_API_KEY, "alias": security_ref("ok")},
            security_endpoint("/a", (requirement("alias"),)),
            security_endpoint("/b", (requirement("ghost"), requirement("ok"))),
            security_endpoint("/c"),
            api_security=(requirement("ok"),),
        )

    result = build()
    before = result.model_dump_json()
    first = generate_test_suite(result)
    second = generate_test_suite(build())

    assert result.model_dump_json() == before
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert [case.path for case in first.cases][:1] == ["/a"]
    assert first.cases[0].security is not result.api.endpoints[0].security
    assert [item[1] for item in security_diagnostics(first)] == ["/b"]


def test_security_scheme_data_is_not_copied_into_cases_or_diagnostics() -> None:
    secret = "SECRET-VALUE-123"
    schemes: dict[str, SecurityScheme | LocalReference] = {
        "good": SecurityScheme(
            type="http",
            scheme="bearer",
            description=secret,
            extensions={"x-token": secret},
        ),
        "bad": SecurityScheme(
            type="weird", description=secret, extensions={"x-token": secret}
        ),
    }

    suite = generate_test_suite(
        security_result(
            schemes,
            security_endpoint("/good", (requirement("good"),)),
            security_endpoint("/bad", (requirement("bad"),)),
        )
    )

    assert [case.path for case in suite.cases] == ["/good"]
    assert len(suite.diagnostics) == 1
    assert secret not in suite.model_dump_json()
    assert set(GeneratedTestCase.model_fields) >= {"security"}
    assert "security_schemes" not in GeneratedTestCase.model_fields


# --- OAuth2 scope validation ----------------------------------------------------------

OAUTH_FLOWS = {
    "implicit": {"authorizationUrl": "https://auth.example.test/a", "scopes": {"imp": "i", "both": "b"}},
    "authorizationCode": {
        "authorizationUrl": "https://auth.example.test/a",
        "tokenUrl": "https://auth.example.test/t",
        "scopes": {"read": "r", "both": "b"},
    },
}
OAUTH2 = SecurityScheme(type="oauth2", flows=OAUTH_FLOWS)


def scoped(**schemes: tuple[str, ...]) -> SecurityRequirement:
    return SecurityRequirement(schemes=schemes)


def scope_result(
    scheme: SecurityScheme | LocalReference = OAUTH2,
    *,
    api: tuple[SecurityRequirement, ...] | None = None,
    operation: tuple[SecurityRequirement, ...] | None = None,
    extra: dict[str, SecurityScheme | LocalReference] | None = None,
) -> NormalizationResult:
    return security_result(
        {"oauth": scheme, **(extra or {})}, security_endpoint(security=operation), api_security=api
    )


def test_declared_oauth2_scopes_are_accepted_from_any_flow_and_empty_lists_too() -> None:
    for scopes in ((), ("read",), ("imp",), ("read", "imp", "both")):
        suite = generate_test_suite(scope_result(api=(scoped(oauth=scopes),)))
        assert [case.security for case in suite.cases] == [(scoped(oauth=scopes),)], scopes
        assert suite.diagnostics == ()


def test_undeclared_oauth2_scope_skips_the_endpoint_with_an_exact_diagnostic() -> None:
    suite = generate_test_suite(scope_result(api=(scoped(oauth=("read", "write")),)))

    assert suite.cases == ()
    assert suite.diagnostics == (
        GenerationDiagnostic(
            code="unsupported_security_scope",
            message="Scope 'write' is not declared by any flow of OAuth2 security scheme 'oauth'.",
            endpoint_path="/items",
            endpoint_method="get",
            source_pointer="/security/0/oauth/1",
            reference_uri=None,
        ),
    )


def test_scope_matching_is_exact_and_case_sensitive() -> None:
    suite = generate_test_suite(scope_result(api=(scoped(oauth=("READ", "read ", "")),)))

    assert security_diagnostics(suite) == [
        ("unsupported_security_scope", "/items", f"/security/0/oauth/{index}", None)
        for index in range(3)
    ]


def test_every_invalid_scope_is_reported_in_requirement_scheme_and_scope_order() -> None:
    suite = generate_test_suite(
        scope_result(
            api=(scoped(oauth=("x", "read", "y")), scoped(other=("z",), oauth=("w",))),
            extra={"other": OAUTH2},
        )
    )

    assert suite.cases == ()
    assert [item[2] for item in security_diagnostics(suite)] == [
        "/security/0/oauth/0",
        "/security/0/oauth/2",
        "/security/1/oauth/0",
        "/security/1/other/0",
    ]


def test_duplicate_invalid_scope_items_are_reported_once_per_location() -> None:
    suite = generate_test_suite(scope_result(api=(scoped(oauth=("bad", "bad")),)))

    assert [item[2] for item in security_diagnostics(suite)] == [
        "/security/0/oauth/0",
        "/security/0/oauth/1",
    ]


def test_oauth2_scopes_are_validated_through_a_local_alias_chain() -> None:
    result = security_result(
        {"oauth": security_ref("mid"), "mid": security_ref("real"), "real": OAUTH2},
        api_security=(scoped(oauth=("read",)),),
    )
    broken = security_result(
        {"oauth": security_ref("mid"), "mid": security_ref("real"), "real": OAUTH2},
        api_security=(scoped(oauth=("nope",)),),
    )

    assert len(generate_test_suite(result).cases) == 1
    assert count_expansions(result) == 2
    suite = generate_test_suite(broken)
    assert suite.cases == ()
    assert security_diagnostics(suite) == [
        ("unsupported_security_scope", "/items", "/security/0/oauth/0", None)
    ]
    assert count_expansions(broken) == 2


def test_root_and_operation_requirements_use_the_same_validation_and_pointers() -> None:
    root = generate_test_suite(scope_result(api=(scoped(oauth=("bad",)),)))
    operation = generate_test_suite(scope_result(operation=(scoped(oauth=("bad",)),)))

    assert security_diagnostics(root) == [
        ("unsupported_security_scope", "/items", "/security/0/oauth/0", None)
    ]
    assert security_diagnostics(operation) == [
        ("unsupported_security_scope", "/items", "/paths/~1items/get/security/0/oauth/0", None)
    ]
    assert root.diagnostics[0].message == operation.diagnostics[0].message


def test_operation_security_overrides_and_root_security_is_inherited_for_scopes() -> None:
    overridden = generate_test_suite(
        scope_result(api=(scoped(oauth=("bad",)),), operation=(scoped(oauth=("read",)),))
    )
    broken_override = generate_test_suite(
        scope_result(api=(scoped(oauth=("read",)),), operation=(scoped(oauth=("bad",)),))
    )
    inherited = generate_test_suite(scope_result(api=(scoped(oauth=("read",)),)))

    assert len(overridden.cases) == 1 and overridden.diagnostics == ()
    assert broken_override.cases == ()
    assert [item[2] for item in security_diagnostics(broken_override)] == [
        "/paths/~1items/get/security/0/oauth/0"
    ]
    assert len(inherited.cases) == 1


@pytest.mark.parametrize(
    "case", ["empty-operation", "omitted", "anonymous-root", "anonymous-operation"]
)
def test_security_without_requirements_is_unchanged_for_oauth2_schemes(case: str) -> None:
    broken = SecurityScheme(type="oauth2", flows=None)
    arguments = {
        "empty-operation": {"api": (scoped(oauth=("bad",)),), "operation": ()},
        "omitted": {},
        "anonymous-root": {"api": (SecurityRequirement(schemes={}),)},
        "anonymous-operation": {"operation": (SecurityRequirement(schemes={}),)},
    }[case]

    suite = generate_test_suite(scope_result(broken, **arguments))  # type: ignore[arg-type]

    assert len(suite.cases) == 1 and suite.diagnostics == ()


def test_an_invalid_scope_in_one_alternative_fails_the_whole_endpoint_only() -> None:
    result = security_result(
        {"oauth": OAUTH2, "key": SECURITY_API_KEY},
        security_endpoint("/items", (requirement("key"), scoped(oauth=("bad",)))),
        security_endpoint("/other", (scoped(oauth=("read",)),)),
    )

    suite = generate_test_suite(result)

    assert [case.path for case in suite.cases] == ["/other"]
    assert security_diagnostics(suite) == [
        ("unsupported_security_scope", "/items", "/paths/~1items/get/security/1/oauth/0", None)
    ]


def test_scopes_of_other_scheme_types_are_not_validated() -> None:
    schemes: dict[str, SecurityScheme | LocalReference] = {
        "key": SECURITY_API_KEY,
        "http": SecurityScheme(type="http", scheme="bearer"),
        "oidc": SecurityScheme(type="openIdConnect", openid_connect_url="https://a.example/o"),
        "mtls": SecurityScheme(type="mutualTLS"),
    }
    requirement_ = SecurityRequirement(schemes={name: ("anything",) for name in schemes})

    suite = generate_test_suite(security_result(schemes, api_security=(requirement_,), version="3.1.1"))

    assert [case.security for case in suite.cases] == [(requirement_,)]
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    "flows",
    [
        None,
        {},
        [],
        "implicit",
        {"x-extension": {"scopes": {"read": "r"}}},
        {"implicit": None},
        {"implicit": {"authorizationUrl": "https://a.example/a"}},
        {"implicit": {"scopes": ["read"]}},
        {"implicit": {"scopes": {"read": "r"}}, "password": {"scopes": None}},
    ],
    ids=["none", "empty", "list", "string", "no-known-flow", "flow-none", "no-scopes", "scopes-list", "one-bad-flow"],
)
def test_malformed_oauth2_flow_metadata_fails_closed_with_the_scheme_diagnostic(
    flows: object,
) -> None:
    broken = SecurityScheme.model_construct(type="oauth2", flows=flows)
    for scopes in ((), ("read",)):
        suite = generate_test_suite(
            security_result({"oauth": broken}, api_security=(scoped(oauth=scopes),))
        )

        assert suite.cases == ()
        assert security_diagnostics(suite) == [
            ("unsupported_security_scheme", "/items", "/components/securitySchemes/oauth", None)
        ]
        assert "missing or malformed flows" in suite.diagnostics[0].message


def test_malformed_oauth2_metadata_reports_the_alias_target_and_last_reference() -> None:
    broken = SecurityScheme(type="oauth2", flows=None)
    suite = generate_test_suite(
        security_result(
            {"oauth": security_ref("real"), "real": broken},
            api_security=(scoped(oauth=("read",)),),
        )
    )

    assert security_diagnostics(suite) == [
        (
            "unsupported_security_scheme",
            "/items",
            "/components/securitySchemes/real",
            f"{SECURITY_BASE}real",
        )
    ]


def test_unknown_flow_keys_are_ignored_and_valid_flows_still_validate() -> None:
    scheme = SecurityScheme(
        type="oauth2",
        flows={**OAUTH_FLOWS, "x-vendor": {"scopes": {"vendor": "v"}}, "other": 1},
    )
    ok = generate_test_suite(scope_result(scheme, api=(scoped(oauth=("read",)),)))
    bad = generate_test_suite(scope_result(scheme, api=(scoped(oauth=("vendor",)),)))

    assert len(ok.cases) == 1
    assert security_diagnostics(bad) == [
        ("unsupported_security_scope", "/items", "/security/0/oauth/0", None)
    ]


def test_scope_validation_does_not_copy_scheme_metadata_into_cases() -> None:
    suite = generate_test_suite(
        scope_result(
            SecurityScheme(
                type="oauth2",
                description="SECRET-VALUE-123",
                flows={"implicit": {"authorizationUrl": "https://s.example/a", "scopes": {"read": "SECRET-VALUE-123"}}},
                extensions={"x-token": "SECRET-VALUE-123"},
            ),
            api=(scoped(oauth=("read",)),),
        )
    )

    assert [case.security for case in suite.cases] == [(scoped(oauth=("read",)),)]
    assert "SECRET-VALUE-123" not in suite.model_dump_json()
    assert "security_schemes" not in GeneratedTestCase.model_fields


def test_scope_validation_is_deterministic_and_does_not_mutate_inputs() -> None:
    result = scope_result(api=(scoped(oauth=("bad", "read", "worse")),))
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert result.model_dump_json() == before
    assert first.model_dump_json() == second.model_dump_json()


def test_scope_diagnostics_use_the_existing_diagnostic_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 2)

    suite = generate_test_suite(scope_result(api=(scoped(oauth=("a", "b", "c", "d")),)))

    codes = [item.code for item in suite.diagnostics]
    assert codes == ["unsupported_security_scope"] * 2 + ["generation_diagnostic_budget_exceeded"]
    assert [item.source_pointer for item in suite.diagnostics[:2]] == [
        "/security/0/oauth/0",
        "/security/0/oauth/1",
    ]
    assert "per-endpoint limit of 2" in suite.diagnostics[-1].message


def test_scope_failures_do_not_hide_an_exhausted_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 1)
    result = security_result(
        {"oauth": security_ref("mid"), "mid": security_ref("real"), "real": OAUTH2},
        api_security=(scoped(oauth=("bad",)),),
    )

    suite = generate_test_suite(result)

    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_resolve_security_scheme_aliases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    def document(target: str) -> dict[str, object]:
        return {
            "openapi": version,
            "info": {"title": "T", "version": "1"},
            "security": [{"alias": []}],
            "components": {
                "securitySchemes": {
                    "apiKey": {"type": "apiKey", "name": "X-Key", "in": "header"},
                    "alias": {"$ref": f"#/components/securitySchemes/{target}"},
                }
            },
            "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        }

    valid = generate_test_suite(normalize_openapi_document(document("apiKey")))
    dangling = generate_test_suite(normalize_openapi_document(document("missing")))

    assert [case.security for case in valid.cases] == [(requirement("alias"),)]
    assert valid.diagnostics == ()
    assert dangling.cases == ()
    assert security_diagnostics(dangling) == [
        (
            "unsupported_security_scheme_reference",
            "/items",
            "/security/0/alias",
            f"{SECURITY_BASE}missing",
        )
    ]


# --- Step 8.1: supported schema formats ---

FORMAT_FALLBACKS = {
    "int32": ("integer", 0),
    "int64": ("integer", 0),
    "float": ("number", 0),
    "double": ("number", 0),
    "date": ("string", "2000-01-01"),
    "date-time": ("string", "2000-01-01T00:00:00Z"),
    "uuid": ("string", "00000000-0000-4000-8000-000000000000"),
    "email": ("string", "user@example.com"),
}


FORMAT_SKIP_CODES = {
    "enum_const_candidate_unavailable",
    "string_boundary_no_safe_candidate",
    "numeric_boundary_no_safe_candidate",
}


def format_endpoint(schema: dict[str, object], path: str = "/formatted") -> Endpoint:
    return make_endpoint(
        path,
        "post",
        responses={"400": Response(description="Invalid request")},
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["v"],
                            "properties": {"v": schema},
                        }
                    )
                )
            },
        ),
    )


def format_suite(schema: dict[str, object], version: str = "3.1.1") -> GeneratedTestSuite:
    return generate_test_suite(make_result(format_endpoint(schema), version=version))


def format_value(schema: dict[str, object], version: str = "3.1.1") -> object:
    suite = format_suite(schema, version)
    # Negative steps may legitimately skip a case they cannot build for a formatted value.
    assert {item.code for item in suite.diagnostics} <= FORMAT_SKIP_CODES, suite.diagnostics
    return suite.cases[0].request_body.value["v"]


def format_codes(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    return [(item.code, item.source_pointer) for item in suite.diagnostics]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize("name", sorted(FORMAT_FALLBACKS))
def test_each_supported_format_generates_its_fixed_fallback(name: str, version: str) -> None:
    schema_type, expected = FORMAT_FALLBACKS[name]

    value = format_value({"type": schema_type, "format": name}, version)

    assert value == expected
    assert type(value) is type(expected)
    assert format_value({"type": schema_type, "format": name}, version) == value


def test_date_time_uses_the_second_fixed_fallback_when_the_first_is_too_short() -> None:
    value = format_value({"type": "string", "format": "date-time", "minLength": 21})

    assert value == "2000-01-01T00:00:00.000Z"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "format": "uuid", "maxLength": 10},
        {"type": "string", "format": "date", "minLength": 11},
        {"type": "string", "format": "date-time", "minLength": 25},
        {"type": "string", "format": "email", "maxLength": 5},
    ],
)
def test_length_bounds_that_no_fixed_candidate_satisfies_report_no_positive_value(
    schema: dict[str, object],
) -> None:
    suite = format_suite(schema)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string", "format": "date", "enum": ["not-a-date", "2024-02-29"]}, "2024-02-29"),
        ({"type": "string", "format": "uuid", "const": "ABCDEF01-2345-6789-abcd-ef0123456789"},
         "ABCDEF01-2345-6789-abcd-ef0123456789"),
        ({"type": "integer", "format": "int32", "enum": [2**31, 7]}, 7),
        ({"type": "string", "format": "email", "example": "bad", "default": "a@b.co"}, "a@b.co"),
        ({"type": "string", "format": "email", "example": "bad@x", "examples": ["ok@example.org"]},
         "ok@example.org"),
        ({"type": "string", "format": "date", "default": "2001-02-29"}, "2000-01-01"),
        ({"type": "integer", "format": "int64", "example": 2**63}, 0),
    ],
)
def test_candidate_order_is_kept_and_invalid_candidates_are_rejected(
    schema: dict[str, object], expected: object
) -> None:
    assert format_value(schema) == expected


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "format": "date", "enum": ["2001-02-29", "x"]},
        {"type": "string", "format": "uuid", "const": "{00000000-0000-4000-8000-000000000000}"},
        {"type": "integer", "format": "int32", "const": 2147483648},
    ],
)
def test_all_invalid_enum_or_const_values_report_no_positive_value(
    schema: dict[str, object],
) -> None:
    suite = format_suite(schema)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    ("name", "valid", "invalid"),
    [
        (
            "date",
            ["2000-01-01", "2024-02-29", "9999-12-31", "0001-01-01"],
            ["2023-02-29", "1900-02-29", "2000-13-01", "2000-00-10", "2000-01-00", "2000-04-31",
             "0000-01-01", "2000-1-01", "2000-01-01 ", "2000-01-01\n", "20000101",
             "٢٠٠٠-٠١-٠١", "2000-01-01T00:00:00Z", ""],
        ),
        (
            "date-time",
            ["2000-01-01T00:00:00Z", "2000-01-01t00:00:00z", "2000-01-01T23:59:59+05:30",
             "2000-01-01T00:00:00.1-23:59", "2000-02-29T12:00:00.123456789Z", "2000-01-01T00:00:00+00:00"],
            ["2000-01-01T00:00:00", "2000-01-01", "2000-01-01 00:00:00Z", "2000-01-01T24:00:00Z",
             "2000-01-01T00:60:00Z", "2000-01-01T23:59:60Z", "2000-01-01T00:00:00+24:00",
             "2000-01-01T00:00:00+05:60", "2000-01-01T00:00:00+0530", "2000-02-30T00:00:00Z",
             "0000-01-01T00:00:00Z", "2000-01-01T00:00:00.Z", "2000-01-01T00:00Z",
             "2000-01-01T00:00:00Z\n"],
        ),
        (
            "uuid",
            ["00000000-0000-4000-8000-000000000000", "ABCDEF01-2345-6789-ABCD-EF0123456789",
             "abcdef01-2345-6789-abcd-ef0123456789", "123e4567-E89b-12D3-a456-426614174000"],
            ["{00000000-0000-4000-8000-000000000000}", "urn:uuid:00000000-0000-4000-8000-000000000000",
             "00000000000040008000000000000000", "00000000-0000-4000-8000-00000000000",
             "0000000g-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000000\n", ""],
        ),
        (
            "email",
            ["user@example.com", "first.last+tag@sub.example.co", "a_b%c-d@x-y.io", "A@B.CD",
             "user@127.0.0.1", "user@example.123"],
            ["user", "user@", "@example.com", "a@b@c.com", "user@example", "user@example.com.",
             ".user@example.com", "user.@example.com", "us..er@example.com", "us er@example.com",
             "user@-example.com", "user@example-.com", "user@exa mple.com", "user@[127.0.0.1]",
             "üser@example.com", "user@exämple.com", "user@example..com",
             "user@example.com\n", ""],
        ),
    ],
)
def test_string_format_profiles_accept_and_reject_documented_values(
    name: str, valid: list[str], invalid: list[str]
) -> None:
    for text in valid:
        assert format_value({"type": "string", "format": name, "const": text}) == text, text
    for text in invalid:
        suite = format_suite({"type": "string", "format": name, "const": text})
        assert suite.cases == (), text
        assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"], text


@pytest.mark.parametrize(
    ("name", "valid", "invalid"),
    [
        ("int32", [-(2**31), 2**31 - 1, 0], [-(2**31) - 1, 2**31]),
        ("int64", [-(2**63), 2**63 - 1, 0], [-(2**63) - 1, 2**63]),
    ],
)
def test_integer_format_ranges_are_inclusive(
    name: str, valid: list[int], invalid: list[int]
) -> None:
    for number in valid:
        assert format_value({"type": "integer", "format": name, "const": number}) == number
    for number in invalid:
        suite = format_suite({"type": "integer", "format": name, "const": number})
        assert suite.cases == ()
        assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


def test_float_format_limits_magnitude_and_double_does_not() -> None:
    limit = 3.4028234663852886e38

    assert format_value({"type": "number", "format": "float", "const": limit}) == limit
    assert format_value({"type": "number", "format": "float", "const": -limit}) == -limit
    assert format_value({"type": "number", "format": "float", "const": 1}) == 1
    for too_large in (3.4028236692093846e38, -3.4028236692093846e38, 1e39):
        suite = format_suite({"type": "number", "format": "float", "const": too_large})
        assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]
    assert format_value({"type": "number", "format": "double", "const": 1e300}) == 1e300
    assert format_value({"type": "number", "format": "double", "const": 0.1}) == 0.1


def test_boolean_never_satisfies_a_numeric_format() -> None:
    for schema in (
        {"type": "integer", "format": "int32", "const": True},
        {"type": "number", "format": "double", "const": False},
    ):
        suite = format_suite(schema)
        assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer", "format": "int32", "minimum": 2147483647}, 2147483647),
        ({"type": "integer", "format": "int32", "maximum": -2147483648}, -2147483648),
        ({"type": "integer", "format": "int64", "minimum": 2**62}, 2**62),
        ({"type": "integer", "format": "int32", "multipleOf": 5, "minimum": 12}, 15),
        ({"type": "integer", "format": "int32", "minimum": 1, "maximum": 10, "multipleOf": 7}, 7),
    ],
)
def test_format_ranges_combine_with_explicit_numeric_constraints(
    schema: dict[str, object], expected: int
) -> None:
    assert format_value(schema) == expected


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "format": "int32", "minimum": 2147483648},
        {"type": "integer", "format": "int32", "maximum": -2147483649},
        {"type": "integer", "format": "int64", "minimum": 2**63},
        {"type": "integer", "format": "int32", "minimum": 2147483647, "exclusiveMinimum": 2147483647},
    ],
)
def test_contradictory_format_and_numeric_constraints_report_no_positive_value(
    schema: dict[str, object],
) -> None:
    suite = format_suite(schema)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


def test_openapi_30_exclusive_bound_at_the_format_edge() -> None:
    schema = {"type": "integer", "format": "int32", "maximum": 2147483647, "exclusiveMaximum": True}

    assert format_value(schema, "3.0.3") == 0
    suite = format_suite({**schema, "minimum": 2147483647, "exclusiveMinimum": True}, "3.0.3")
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


def numeric_negatives(suite: GeneratedTestSuite) -> dict[str, object]:
    return {
        case.violated_constraint: case.request_body.value["v"]
        for case in suite.cases
        if case.category == "negative"
        and case.affected_property == "/v"
        and case.violated_constraint in {
            "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"
        }
    }


def boundary_skips(suite: GeneratedTestSuite) -> list[str | None]:
    return [
        item.source_pointer
        for item in suite.diagnostics
        if item.code == "numeric_boundary_no_safe_candidate"
    ]


@pytest.mark.parametrize(
    ("name", "limit"), [("int32", 2**31 - 1), ("int64", 2**63 - 1)]
)
def test_maximum_at_the_integer_format_limit_has_no_boundary_case(name: str, limit: int) -> None:
    suite = format_suite({"type": "integer", "format": name, "maximum": limit})

    assert numeric_negatives(suite) == {}
    assert boundary_skips(suite) == ["/paths/~1formatted/post/requestBody/content/application~1json/schema/properties/v/maximum"]


@pytest.mark.parametrize(
    ("name", "limit"), [("int32", -(2**31)), ("int64", -(2**63))]
)
def test_minimum_at_the_integer_format_limit_has_no_boundary_case(name: str, limit: int) -> None:
    suite = format_suite({"type": "integer", "format": name, "minimum": limit})

    assert numeric_negatives(suite) == {}
    assert len(boundary_skips(suite)) == 1


def test_integer_boundary_cases_next_to_the_format_limits_stay_in_range() -> None:
    suite = format_suite(
        {"type": "integer", "format": "int32", "minimum": -2147483647, "maximum": 2147483646}
    )

    assert numeric_negatives(suite) == {"minimum": -2147483648, "maximum": 2147483647}
    assert boundary_skips(suite) == []


def test_maximum_above_the_format_limit_cannot_be_violated_in_range() -> None:
    suite = format_suite({"type": "integer", "format": "int32", "maximum": 5000000000})

    assert numeric_negatives(suite) == {}
    assert len(boundary_skips(suite)) == 1


def test_integer_boundary_with_multiple_of_and_format_stays_valid() -> None:
    suite = format_suite(
        {"type": "integer", "format": "int32", "minimum": 10, "multipleOf": 5}
    )

    assert numeric_negatives(suite) == {"minimum": 5}


def test_exclusive_bound_cases_stay_inside_the_integer_format_range() -> None:
    # Violating "< 2147483647" with 2147483647 itself is a valid int32 value.
    v30 = format_suite(
        {"type": "integer", "format": "int32", "maximum": 2147483647, "exclusiveMaximum": True},
        "3.0.3",
    )
    v31 = format_suite({"type": "integer", "format": "int32", "exclusiveMaximum": 2147483647})
    # Violating "< 2147483648" needs a value outside int32, so no case exists.
    beyond = format_suite({"type": "integer", "format": "int32", "exclusiveMaximum": 2147483648})
    below = format_suite({"type": "integer", "format": "int32", "exclusiveMinimum": -2147483649})

    assert numeric_negatives(v30) == {"exclusiveMaximum": 2147483647}
    assert numeric_negatives(v31) == {"exclusiveMaximum": 2147483647}
    assert numeric_negatives(beyond) == {}
    assert numeric_negatives(below) == {}
    assert len(boundary_skips(beyond)) == 1 and len(boundary_skips(below)) == 1


def test_float_boundary_candidates_respect_the_magnitude_limit() -> None:
    at_limit = format_suite({"type": "number", "format": "float", "maximum": 3.4028234663852886e38})
    inside = format_suite({"type": "number", "format": "float", "minimum": 0, "maximum": 10})

    assert numeric_negatives(at_limit) == {}
    assert len(boundary_skips(at_limit)) == 1
    values = numeric_negatives(inside)
    assert set(values) == {"minimum", "maximum"}
    assert values["minimum"] < 0 and values["maximum"] > 10


def test_double_boundary_cases_behave_like_plain_numbers() -> None:
    formatted = format_suite({"type": "number", "format": "double", "minimum": 0, "maximum": 10})
    plain = format_suite({"type": "number", "minimum": 0, "maximum": 10})

    assert numeric_negatives(formatted) == numeric_negatives(plain)


def test_boundary_enum_candidates_outside_the_format_range_are_skipped() -> None:
    suite = format_suite(
        {"type": "integer", "format": "int32", "enum": [2147483647, 2147483648], "maximum": 2147483647}
    )

    assert numeric_negatives(suite) == {}


def test_formatted_string_boundary_and_enum_violation_cases_are_skipped_not_invalid() -> None:
    bounded = format_suite({"type": "string", "format": "uuid", "minLength": 36, "maxLength": 36})
    enum = format_suite({"type": "string", "format": "date", "enum": ["2000-01-01"]})

    for suite in (bounded, enum):
        assert suite.cases[0].category == "positive"
        assert {item.code for item in suite.diagnostics} <= FORMAT_SKIP_CODES
        assert len(suite.diagnostics) >= 1
        assert not {case.violated_constraint for case in suite.cases} & {"minLength", "maxLength", "enum"}
        for case in suite.cases[1:]:
            assert case.violated_constraint in {"required_request_body", "required_property", "type"}


@pytest.mark.parametrize(
    ("schema", "code"),
    [
        ({"type": "string", "format": "int32"}, "unsupported_schema_form"),
        ({"type": "integer", "format": "uuid"}, "unsupported_schema_form"),
        ({"type": "number", "format": "date"}, "unsupported_schema_form"),
        ({"type": "integer", "format": "float"}, "unsupported_schema_form"),
        ({"type": "number", "format": "int64"}, "unsupported_schema_form"),
        ({"type": "boolean", "format": "email"}, "unsupported_schema_form"),
        ({"format": "uuid"}, "unsupported_schema_form"),
        ({"type": "null", "format": "uuid"}, "unsupported_schema_form"),
        ({"type": "string", "format": 5}, "unsupported_schema_form"),
        ({"type": "string", "format": None}, "unsupported_schema_form"),
        ({"type": "string", "format": "hostname"}, "unsupported_schema_keyword"),
        ({"type": "string", "format": "Email"}, "unsupported_schema_keyword"),
        ({"type": "string", "format": "binary"}, "unsupported_schema_keyword"),
        ({"type": "integer", "format": "custom"}, "unsupported_schema_keyword"),
    ],
)
def test_incompatible_or_unknown_formats_are_reported_at_the_format_pointer(
    schema: dict[str, object], code: str
) -> None:
    suite = format_suite(schema)

    assert suite.cases == ()
    assert format_codes(suite) == [
        (code, "/paths/~1formatted/post/requestBody/content/application~1json/schema/properties/v/format")
    ]


def test_openapi_31_type_arrays_apply_the_format_to_the_non_null_type() -> None:
    schema = {"type": ["string", "null"], "format": "uuid"}

    suite = format_suite(schema)
    null_const = format_suite({**schema, "const": None})
    bad = format_suite({**schema, "const": "nope"})

    assert suite.cases[0].request_body.value["v"] == "00000000-0000-4000-8000-000000000000"
    assert null_const.cases[0].request_body.value["v"] is None
    assert [item.code for item in bad.diagnostics] == ["no_positive_schema_value"]
    assert format_suite({"type": ["null"], "format": "uuid"}).cases == ()


def test_openapi_30_nullable_formatted_values_still_accept_null() -> None:
    schema = {"type": "string", "format": "date", "nullable": True}

    assert format_value({**schema, "const": None}, "3.0.3") is None
    assert format_value(schema, "3.0.3") == "2000-01-01"


def test_format_works_for_primitive_parameters() -> None:
    endpoint = make_endpoint(
        "/items/{id}",
        parameters=(
            Parameter(name="id", location="path", required=True,
                      schema=Schema(raw={"type": "string", "format": "uuid"}), source_scope="operation"),
            Parameter(name="since", location="query", required=True,
                      schema=Schema(raw={"type": "string", "format": "date-time"}), source_scope="operation"),
            Parameter(name="limit", location="header", required=True,
                      schema=Schema(raw={"type": "integer", "format": "int32", "minimum": 5}),
                      source_scope="operation"),
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    values = {item.name: item.value for item in suite.cases[0].parameters}
    assert values == {
        "id": "00000000-0000-4000-8000-000000000000",
        "since": "2000-01-01T00:00:00Z",
        "limit": 5,
    }
    assert suite.diagnostics == ()


def test_unknown_parameter_format_is_unsupported() -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(
            Parameter(name="q", location="query", required=True,
                      schema=Schema(raw={"type": "string", "format": "hostname"}), source_scope="operation"),
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_keyword"]


def test_formatted_array_items_and_nested_objects_generate_valid_values() -> None:
    schema = {
        "type": "object",
        "required": ["ids", "inner"],
        "properties": {
            "ids": {"type": "array", "minItems": 2, "items": {"type": "string", "format": "uuid"}},
            "inner": {"type": "object", "required": ["day"],
                      "properties": {"day": {"type": "string", "format": "date"}}},
        },
    }
    endpoint = make_endpoint(
        "/nested",
        "post",
        request_body=RequestBody(required=True, content={"application/json": MediaType(schema=Schema(raw=schema))}),
    )

    positive = generate_test_suite(make_result(endpoint)).cases[0].request_body.value

    assert positive == {
        "ids": ["00000000-0000-4000-8000-000000000000"] * 2,
        "inner": {"day": "2000-01-01"},
    }


def test_format_beside_a_reference_stays_unsupported() -> None:
    schemas = {"Id": Schema(raw={"type": "string", "format": "uuid"})}
    endpoint = format_endpoint({"$ref": "#/components/schemas/Id", "format": "uuid"})
    result = make_result(endpoint, components=Components(schemas=schemas))

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_reference_siblings"]
    assert suite.diagnostics[0].source_pointer.endswith("/properties/v/format")


def test_formatted_referenced_schema_is_supported() -> None:
    schemas = {"Id": Schema(raw={"type": "string", "format": "uuid"})}
    endpoint = format_endpoint({"$ref": "#/components/schemas/Id"})

    suite = generate_test_suite(make_result(endpoint, components=Components(schemas=schemas)))

    assert suite.cases[0].request_body.value["v"] == "00000000-0000-4000-8000-000000000000"


def test_response_schema_formats_are_preserved_without_validation() -> None:
    response_schema = Schema(raw={"type": "string", "format": "hostname"})
    endpoint = make_endpoint(
        "/r",
        responses={"200": Response(description="OK", content={"application/json": MediaType(schema=response_schema)})},
    )

    suite = generate_test_suite(make_result(endpoint))

    assert suite.diagnostics == ()
    assert suite.cases[0].response_expectations[0].schemas_by_media_type == {
        "application/json": response_schema
    }


def test_format_generation_is_deterministic_and_does_not_mutate_input() -> None:
    schema = {"type": "integer", "format": "int32", "minimum": 1, "maximum": 9}
    endpoint = format_endpoint(schema)
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


# --- Step 8.2.1: empty arrays with unsupported item schemas ---

UNSUPPORTED_ITEMS: dict[str, object] = {"type": "string", "pattern": "^(a)$"}
ITEMS_PATTERN_POINTER = tags_schema_pointer("items", "pattern")


def empty_array_suite(
    tags: object,
    *,
    version: str = "3.1.1",
    schemas: dict[str, dict[str, object]] | None = None,
    body: dict[str, object] | None = None,
) -> GeneratedTestSuite:
    components = (
        Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})
        if schemas
        else None
    )
    endpoint = post_endpoint(body if body is not None else tags_body(tags))
    return generate_test_suite(make_result(endpoint, version=version, components=components))


def positive_tags(suite: GeneratedTestSuite) -> object:
    positive = suite.cases[0]
    assert positive.category == "positive"
    assert positive.request_body is not None
    return positive.request_body.value["tags"]  # type: ignore[index]


def codes(suite: GeneratedTestSuite) -> list[str]:
    return [item.code for item in suite.diagnostics]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize("extra", [{}, {"minItems": 0}], ids=["no-minItems", "minItems-0"])
def test_required_empty_array_is_valid_despite_unsupported_items(
    extra: dict[str, object], version: str
) -> None:
    suite = empty_array_suite({"type": "array", **extra, "items": UNSUPPORTED_ITEMS}, version=version)

    assert positive_tags(suite) == []
    assert "unsupported_schema_keyword" not in codes(suite)
    assert array_cases(suite) == []
    # The other negative steps still run for the endpoint.
    assert {case.violated_constraint for case in suite.cases[1:]} >= {"required_property", "type"}


def test_empty_array_nested_in_an_object_property_is_valid() -> None:
    body = {
        "type": "object",
        "required": ["outer"],
        "properties": {"outer": tags_body({"type": "array", "items": UNSUPPORTED_ITEMS})},
    }

    suite = empty_array_suite(None, body=body)

    assert suite.cases[0].request_body.value == {"outer": {"tags": []}}  # type: ignore[union-attr]
    assert codes(suite) == []


def test_unsupported_items_with_max_items_report_one_diagnostic_at_the_real_pointer() -> None:
    suite = empty_array_suite({"type": "array", "maxItems": 2, "items": UNSUPPORTED_ITEMS})

    assert positive_tags(suite) == []
    assert array_cases(suite) == []
    assert array_diagnostics(suite) == [("array_bound_unsupported_schema", ITEMS_PATTERN_POINTER)]
    assert codes(suite) == ["array_bound_unsupported_schema"]


def test_unsupported_items_with_unique_items_report_one_diagnostic() -> None:
    suite = empty_array_suite({"type": "array", "uniqueItems": True, "items": UNSUPPORTED_ITEMS})

    assert positive_tags(suite) == []
    assert array_cases(suite) == []
    assert array_diagnostics(suite) == [("array_bound_unsupported_schema", ITEMS_PATTERN_POINTER)]


def test_unsupported_items_needed_by_several_candidates_are_reported_once() -> None:
    suite = empty_array_suite(
        {"type": "array", "maxItems": 2, "uniqueItems": True, "items": UNSUPPORTED_ITEMS}
    )

    assert positive_tags(suite) == []
    # maxItems needs three unique items (existing diagnostic); uniqueItems needs
    # an item, so the unsupported items schema is reported once, at its pointer.
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems")),
        ("array_bound_unsupported_schema", ITEMS_PATTERN_POINTER),
    ]


def test_min_items_one_with_unsupported_items_still_fails_the_positive_baseline() -> None:
    suite = empty_array_suite({"type": "array", "minItems": 1, "items": UNSUPPORTED_ITEMS})

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("unsupported_schema_keyword", ITEMS_PATTERN_POINTER)
    ]


def test_supported_items_still_produce_the_same_array_cases() -> None:
    suite = empty_array_suite({"type": "array", "minItems": 1, "maxItems": 2, "items": ITEM_SCHEMA})

    assert positive_tags(suite) == ["x"]
    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "maxItems", 3)]
    assert codes(suite) == []


def test_boolean_false_items_allow_the_empty_array_only() -> None:
    plain = empty_array_suite({"type": "array", "items": False})
    bounded = empty_array_suite({"type": "array", "maxItems": 1, "items": False})
    required_item = empty_array_suite({"type": "array", "minItems": 1, "items": False})

    assert positive_tags(plain) == [] and codes(plain) == []
    assert positive_tags(bounded) == []
    assert array_diagnostics(bounded) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]
    assert required_item.cases == ()
    assert codes(required_item) == ["no_positive_schema_value"]


def test_boolean_true_items_keep_generating_null_items() -> None:
    suite = empty_array_suite({"type": "array", "minItems": 1, "maxItems": 1, "items": True})

    assert positive_tags(suite) == [None]
    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "maxItems", 2)]


@pytest.mark.parametrize(
    "literal", [{"const": []}, {"enum": [[]]}], ids=["const", "enum"]
)
def test_empty_array_const_and_enum_are_valid_despite_unsupported_items(
    literal: dict[str, object],
) -> None:
    suite = empty_array_suite({"type": "array", **literal, "items": UNSUPPORTED_ITEMS})

    assert positive_tags(suite) == []
    assert "unsupported_schema_keyword" not in codes(suite)
    assert array_cases(suite) == []


def test_nonempty_array_literals_with_unsupported_items_are_not_accepted() -> None:
    const = empty_array_suite({"type": "array", "const": ["a"], "items": UNSUPPORTED_ITEMS})
    enum = empty_array_suite({"type": "array", "enum": [["a"]], "items": UNSUPPORTED_ITEMS})

    for suite in (const, enum):
        assert suite.cases == ()
        assert codes(suite) == ["no_positive_schema_value"]


def test_nonempty_example_with_unsupported_items_is_rejected_not_used() -> None:
    suite = empty_array_suite(
        {"type": "array", "example": ["a"], "items": UNSUPPORTED_ITEMS}
    )
    required_two = empty_array_suite(
        {"type": "array", "minItems": 2, "example": ["a", "a"], "items": UNSUPPORTED_ITEMS}
    )

    assert positive_tags(suite) == []
    assert required_two.cases == ()
    assert [(item.code, item.source_pointer) for item in required_two.diagnostics] == [
        ("unsupported_schema_keyword", ITEMS_PATTERN_POINTER)
    ]


def test_openapi_31_nullable_array_type_accepts_the_empty_array() -> None:
    schema = {"type": ["array", "null"], "maxItems": 2, "items": UNSUPPORTED_ITEMS}

    suite = empty_array_suite(schema)
    null_literal = empty_array_suite({**schema, "const": None})

    assert positive_tags(suite) == []
    assert array_diagnostics(suite) == [("array_bound_unsupported_schema", ITEMS_PATTERN_POINTER)]
    assert positive_tags(null_literal) is None


def test_unsupported_composition_in_items_allows_only_the_empty_array() -> None:
    items = {"oneOf": [{"type": "string"}, {"type": "integer"}]}

    empty = empty_array_suite({"type": "array", "maxItems": 2, "items": items})
    required_item = empty_array_suite({"type": "array", "minItems": 1, "items": items})

    assert positive_tags(empty) == []
    assert array_diagnostics(empty) == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "oneOf"))
    ]
    assert required_item.cases == ()
    assert [(item.code, item.source_pointer) for item in required_item.diagnostics] == [
        ("unsupported_schema_composition", tags_schema_pointer("items", "oneOf"))
    ]


def test_local_reference_in_items_to_an_unsupported_schema_is_reported_at_the_target() -> None:
    items = {"$ref": "#/components/schemas/Letter"}
    schemas = {"Letter": UNSUPPORTED_ITEMS}

    empty = empty_array_suite({"type": "array", "maxItems": 2, "items": items}, schemas=schemas)
    required_item = empty_array_suite(
        {"type": "array", "minItems": 1, "items": items}, schemas=schemas
    )

    assert positive_tags(empty) == []
    assert [(item.code, item.source_pointer, item.reference_uri) for item in empty.diagnostics] == [
        ("array_bound_unsupported_schema", "/components/schemas/Letter/pattern", None)
    ]
    assert required_item.cases == ()
    assert [(item.code, item.source_pointer, item.reference_uri) for item in required_item.diagnostics] == [
        ("unsupported_schema_keyword", "/components/schemas/Letter/pattern", None)
    ]


def test_local_reference_in_items_to_a_supported_schema_is_unchanged() -> None:
    suite = empty_array_suite(
        {"type": "array", "minItems": 1, "maxItems": 1, "items": {"$ref": "#/components/schemas/Name"}},
        schemas={"Name": {"type": "string", "minLength": 2}},
    )

    assert positive_tags(suite) == ["sentinel"]
    assert array_summary(suite) == [("/tags", "minItems", 0), ("/tags", "maxItems", 2)]
    assert codes(suite) == []


def test_empty_array_generation_is_deterministic_and_does_not_mutate_input() -> None:
    result = make_result(
        post_endpoint(tags_body({"type": "array", "maxItems": 2, "items": UNSUPPORTED_ITEMS}))
    )
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


# --- Step 8.2.2: distinct positive items for uniqueItems with minItems > 1 ---

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def unique_suite(
    items: object,
    minimum: int = 2,
    *,
    version: str = "3.1.1",
    schemas: dict[str, dict[str, object]] | None = None,
    **array_options: object,
) -> GeneratedTestSuite:
    return empty_array_suite(
        {"type": "array", "minItems": minimum, "uniqueItems": True, "items": items, **array_options},
        version=version,
        schemas=schemas,
    )


def assert_distinct_positive(suite: GeneratedTestSuite, expected: list[object]) -> None:
    tags = positive_tags(suite)
    assert tags == expected
    assert [type(item) for item in tags] == [type(item) for item in expected]  # type: ignore[union-attr]
    assert not generator_module._has_duplicate_items(tags)  # type: ignore[arg-type]
    assert "no_positive_schema_value" not in codes(suite)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize(
    ("items", "minimum", "expected"),
    [
        ({"type": "integer"}, 2, [0, 1]),
        ({"type": "integer"}, 3, [0, 1, -1]),
        ({"type": "integer", "format": "int32"}, 2, [0, 1]),
        ({"type": "integer", "minimum": 5, "maximum": 100}, 3, [5, 6, 100]),
        ({"type": "integer", "minimum": 0, "maximum": 1}, 2, [0, 1]),
        ({"type": "integer", "multipleOf": 5}, 3, [0, 5, -5]),
        ({"type": "number"}, 3, [0, 1, -1]),
        ({"type": "string"}, 2, ["sentinel", ""]),
        ({"type": "string", "minLength": 1}, 2, ["sentinel", "x"]),
        ({"type": "boolean"}, 2, [False, True]),
        ({"type": "string", "format": "date-time"}, 2,
         ["2000-01-01T00:00:00Z", "2000-01-01T00:00:00.000Z"]),
    ],
)
def test_unique_arrays_use_distinct_existing_fallback_values(
    items: dict[str, object], minimum: int, expected: list[object], version: str
) -> None:
    assert_distinct_positive(unique_suite(items, minimum, version=version), expected)


@pytest.mark.parametrize(
    ("items", "minimum", "expected"),
    [
        ({"type": "string", "enum": ["a", "b", "c"]}, 3, ["a", "b", "c"]),
        ({"type": "string", "enum": ["a", "b", "c"]}, 2, ["a", "b"]),
        ({"type": "string", "enum": ["a", "a", "b"]}, 2, ["a", "b"]),
        ({"type": "integer", "enum": [7, 8, 9, 10]}, 3, [7, 8, 9]),
        ({"type": "string", "examples": ["x", "y"]}, 2, ["x", "y"]),
        ({"type": "string", "example": "x", "default": "y"}, 2, ["x", "y"]),
        ({"type": "string", "example": "x"}, 2, ["x", "sentinel"]),
        ({"type": "string", "example": "x", "default": "x"}, 2, ["x", "sentinel"]),
        ({"type": "integer", "default": 7}, 2, [7, 0]),
        ({"type": "string", "examples": ["x", 5, "y"]}, 2, ["x", "y"]),
        ({"type": "string", "format": "uuid", "examples": [UUID_A, "nope", UUID_B]}, 2, [UUID_A, UUID_B]),
    ],
)
def test_unique_arrays_follow_the_candidate_order(
    items: dict[str, object], minimum: int, expected: list[object]
) -> None:
    assert_distinct_positive(unique_suite(items, minimum), expected)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_unique_arrays_with_referenced_items(version: str) -> None:
    suite = unique_suite(
        {"$ref": "#/components/schemas/Count"},
        3,
        version=version,
        schemas={"Count": {"type": "integer", "minimum": 5, "maximum": 100}},
    )

    assert_distinct_positive(suite, [5, 6, 100])


def test_unique_arrays_with_nested_reference_chain_and_enum() -> None:
    suite = unique_suite(
        {"$ref": "#/components/schemas/Outer"},
        2,
        schemas={
            "Outer": {"$ref": "#/components/schemas/Inner"},
            "Inner": {"type": "string", "enum": ["p", "q"]},
        },
    )

    assert_distinct_positive(suite, ["p", "q"])


def test_unique_arrays_may_use_null_only_when_an_existing_candidate_produces_it() -> None:
    v30 = unique_suite(
        {"type": "string", "nullable": True, "enum": ["a", None]}, 2, version="3.0.3"
    )
    v31 = unique_suite({"type": ["string", "null"], "enum": ["a", None]}, 2)
    no_candidate = unique_suite({"type": ["string", "null"]}, 3)

    assert_distinct_positive(v30, ["a", None])
    assert_distinct_positive(v31, ["a", None])
    assert positive_tags(v31)[1] is None  # type: ignore[index]
    assert no_candidate.cases == ()
    assert codes(no_candidate) == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    ("items", "minimum"),
    [
        ({"type": "string", "enum": ["a", "b"]}, 3),
        ({"type": "string", "const": "a"}, 2),
        ({"type": "integer", "minimum": 0, "maximum": 1}, 3),
        ({"type": "string", "maxLength": 0}, 2),
        ({"type": "string", "format": "uuid"}, 2),
        ({"type": "string", "format": "uuid", "examples": [UUID_A, "not-a-uuid"]}, 3),
        ({"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 1, "exclusiveMinimum": 0}, 2),
        ({"type": "boolean", "enum": [True]}, 2),
        ({"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}, 2),
        ({"type": "object"}, 2),
        ({"type": "array", "items": {"type": "integer"}}, 2),
        ({"type": "array", "minItems": 1, "items": {"type": "integer"}}, 2),
        ({"type": "null"}, 2),
        (True, 2),
        (False, 2),
    ],
)
def test_unique_arrays_with_too_few_distinct_candidates_report_one_diagnostic(
    items: object, minimum: int
) -> None:
    suite = unique_suite(items, minimum)

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("no_positive_schema_value", tags_schema_pointer())
    ]


def test_unique_arrays_above_the_collection_limit_are_not_generated() -> None:
    for minimum in (100, 101):
        suite = unique_suite({"type": "integer"}, minimum)

        assert suite.cases == ()
        assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
            ("no_positive_schema_value", tags_schema_pointer())
        ]


def test_unique_arrays_with_max_items_below_min_items_are_not_generated() -> None:
    suite = unique_suite({"type": "integer"}, 3, maxItems=2)

    assert suite.cases == ()
    assert codes(suite) == ["no_positive_schema_value"]


def test_unique_arrays_with_unsupported_item_keywords_keep_their_diagnostic() -> None:
    suite = unique_suite({"type": "string", "pattern": "^(a)$", "examples": ["a", "aa"]}, 2)

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("unsupported_schema_keyword", ITEMS_PATTERN_POINTER)
    ]


@pytest.mark.parametrize(
    ("literals", "minimum", "expected"),
    [
        ([1, 1.0, True], 2, [1, True]),
        ([1, 1.0], 2, None),
        ([0, False, 0.0], 2, [0, False]),
        ([1, True, "1"], 3, [1, True, "1"]),
        ([1, 1.0, 2], 2, [1, 2]),
        ([{"a": 1}, {"a": 1.0}, {"a": 2}], 2, [{"a": 1}, {"a": 2}]),
        ([[1], [1.0], [True]], 2, [[1], [True]]),
    ],
)
def test_distinct_item_values_use_json_schema_equality(
    literals: list[object], minimum: int, expected: list[object] | None
) -> None:
    suite = unique_suite({"enum": literals}, minimum)

    if expected is None:
        assert suite.cases == ()
        assert codes(suite) == ["no_positive_schema_value"]
    else:
        assert positive_tags(suite) == expected
        assert [type(item) for item in positive_tags(suite)] == [type(item) for item in expected]  # type: ignore[union-attr]


def test_array_level_literals_keep_their_existing_behavior() -> None:
    example = unique_suite({"type": "string"}, 2, example=["b", "a"])
    default = unique_suite({"type": "string"}, 2, default=["b", "a"])
    enum = unique_suite({"type": "string"}, 2, enum=[["x", "x"], ["b", "a"]])
    const = unique_suite({"type": "string"}, 2, const=["b", "a"])
    duplicate = unique_suite({"type": "string", "enum": ["a"]}, 2, example=["a", "a"])

    for suite in (example, default, enum, const):
        assert positive_tags(suite) == ["b", "a"]
    assert duplicate.cases == ()
    assert codes(duplicate) == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    "options",
    [{}, {"uniqueItems": False}],
    ids=["absent", "false"],
)
def test_arrays_without_unique_items_still_repeat_one_item(options: dict[str, object]) -> None:
    schema = {"type": "array", "minItems": 2, "items": {"type": "string"}}
    suite = empty_array_suite({**schema, **options})

    assert positive_tags(suite) == ["sentinel", "sentinel"]


@pytest.mark.parametrize("minimum", [0, 1])
def test_unique_arrays_with_min_items_zero_or_one_are_unchanged(minimum: int) -> None:
    suite = unique_suite({"type": "string"}, minimum)

    assert positive_tags(suite) == (["sentinel"] if minimum else [])


def test_unique_items_true_with_a_non_unique_minimum_keeps_the_single_item() -> None:
    suite = unique_suite({"type": "string", "example": "x"}, 1)

    assert positive_tags(suite) == ["x"]


def test_negative_array_cases_still_work_after_a_distinct_positive_baseline() -> None:
    two = unique_suite({"type": "integer"}, 2)
    three = unique_suite({"type": "integer"}, 3)

    assert [(case.violated_constraint, case.request_body.value["tags"]) for case in array_cases(two)] == [  # type: ignore[index, union-attr]
        ("minItems", [0]),
        ("uniqueItems", [0, 0]),
    ]
    assert [(case.violated_constraint, case.request_body.value["tags"]) for case in array_cases(three)] == [  # type: ignore[index, union-attr]
        ("uniqueItems", [0, 0, 0])
    ]
    assert array_diagnostics(three) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("minItems"))
    ]


def test_distinct_item_generation_is_deterministic_and_does_not_mutate_input() -> None:
    result = make_result(
        post_endpoint(
            tags_body(
                {
                    "type": "array",
                    "minItems": 3,
                    "uniqueItems": True,
                    "items": {"type": "integer", "enum": [3, 2, 1, 0]},
                }
            )
        )
    )
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert positive_tags(first) == [3, 2, 1]


def test_mutating_one_case_array_does_not_change_other_cases_or_the_input_model() -> None:
    enum = [{"a": [1]}, {"a": [2]}, {"a": [3]}]
    result = make_result(
        post_endpoint(
            tags_body(
                {"type": "array", "minItems": 2, "uniqueItems": True, "items": {"enum": enum}}
            )
        )
    )
    model_before = result.model_dump_json()
    suite = generate_test_suite(result)

    positive = suite.cases[0]
    by_constraint = {case.violated_constraint: case for case in array_cases(suite)}
    assert set(by_constraint) == {"minItems", "uniqueItems"}

    def tags(case: GeneratedTestCase) -> list[dict[str, list[object]]]:
        assert case.request_body is not None
        return case.request_body.value["tags"]  # type: ignore[index, return-value]

    positive_tags_list = tags(positive)
    min_items_tags = tags(by_constraint["minItems"])
    unique_tags = tags(by_constraint["uniqueItems"])
    assert positive_tags_list == [{"a": [1]}, {"a": [2]}]
    assert min_items_tags == [{"a": [1]}]
    assert unique_tags == [{"a": [1]}, {"a": [1]}]
    # Distinct list objects, and distinct item and nested-list objects across cases.
    arrays = [positive_tags_list, min_items_tags, unique_tags]
    assert len({id(array) for array in arrays}) == 3
    items = [item for array in arrays for item in array]
    assert len({id(item) for item in items}) == len(items)
    assert len({id(item["a"]) for item in items}) == len(items)

    # Mutate the positive case at array, item and nested level.
    positive_tags_list[0]["a"].append("mutated")
    positive_tags_list[0]["extra"] = True  # type: ignore[assignment]
    positive_tags_list.append({"a": ["appended"]})
    assert min_items_tags == [{"a": [1]}]
    assert unique_tags == [{"a": [1]}, {"a": [1]}]

    # Mutate a negative case: it must affect neither the positive nor the other negative case.
    unique_tags[0]["a"].append("changed")
    assert unique_tags[1] == {"a": [1]}
    assert min_items_tags == [{"a": [1]}]
    assert positive_tags_list[1] == {"a": [2]}

    # The input model (the schema enum literals) is untouched.
    assert result.model_dump_json() == model_before
    assert enum == [{"a": [1]}, {"a": [2]}, {"a": [3]}]


def test_distinct_item_expansion_budget_keeps_the_existing_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas = {
        "A": {"$ref": "#/components/schemas/B"},
        "B": {"$ref": "#/components/schemas/C"},
        "C": {"type": "integer"},
    }
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 3)

    suite = unique_suite({"$ref": "#/components/schemas/A"}, 3, schemas=schemas)

    assert suite.cases == ()
    assert codes(suite) == ["generation_expansion_budget_exceeded"]


def test_distinct_item_case_budget_keeps_the_existing_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 1)

    suite = unique_suite({"type": "integer"}, 2)

    assert [case.category for case in suite.cases] == ["positive"]
    assert "generation_case_budget_exceeded" in codes(suite)


def test_a_null_only_item_schema_admits_no_other_value() -> None:
    nullable_property = empty_array_suite(
        None,
        body={
            "type": "object",
            "required": ["tags"],
            "properties": {"tags": {"type": "null"}},
        },
    )

    assert positive_tags(nullable_property) is None
    assert generator_module._matches({"type": "null"}, None, "3.1.1", "request", 0)
    for value in ({}, [], False, 0, ""):
        assert not generator_module._matches({"type": "null"}, value, "3.1.1", "request", 0)
    assert generator_module._matches({"type": ["null"]}, None, "3.1.1", "request", 0)
    assert not generator_module._matches({"type": ["null"]}, 0, "3.1.1", "request", 0)


# --- Step 8.4: root-level array request bodies ---

ROOT_PATH = "/paths/~1items/post/requestBody/content/application~1json/schema"


def root_suite(
    root: dict[str, object],
    *,
    version: str = "3.1.1",
    schemas: dict[str, dict[str, object]] | None = None,
) -> GeneratedTestSuite:
    endpoint = post_endpoint(root)
    components = (
        Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})
        if schemas
        else None
    )
    return generate_test_suite(make_result(endpoint, version=version, components=components))


def root_array_cases(suite: GeneratedTestSuite) -> dict[str, GeneratedTestCase]:
    cases = {case.violated_constraint: case for case in array_cases(suite)}
    assert len(cases) == len(array_cases(suite))
    return cases  # type: ignore[return-value]


def body_value(case: GeneratedTestCase) -> object:
    assert case.request_body is not None
    return case.request_body.value


def assert_violates_only(
    case: GeneratedTestCase, schema: dict[str, object], version: str = "3.1.1"
) -> None:
    """The case violates its targeted constraint and is valid without it."""
    constraint = case.violated_constraint
    assert constraint in ARRAY_CONSTRAINTS
    assert not generator_module._matches(schema, body_value(case), version, "request", 0)
    relaxed = {key: value for key, value in schema.items() if key != constraint}
    assert generator_module._matches(relaxed, body_value(case), version, "request", 0)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_root_array_min_items_case(version: str) -> None:
    schema = {"type": "array", "minItems": 2, "items": ITEM_SCHEMA}

    suite = root_suite(schema, version=version)

    cases = root_array_cases(suite)
    assert set(cases) == {"minItems"}
    assert body_value(suite.cases[0]) == ["x", "x"]
    assert body_value(cases["minItems"]) == ["x"]
    assert cases["minItems"].category == "negative"
    assert_violates_only(cases["minItems"], schema, version)
    assert suite.diagnostics == ()


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_root_array_max_items_case(version: str) -> None:
    schema = {"type": "array", "maxItems": 2, "items": ITEM_SCHEMA}

    suite = root_suite(schema, version=version)

    cases = root_array_cases(suite)
    assert set(cases) == {"maxItems"}
    assert body_value(cases["maxItems"]) == ["x", "x", "x"]
    assert_violates_only(cases["maxItems"], schema, version)


def test_root_array_bounds_at_zero_and_one() -> None:
    zero = root_suite({"type": "array", "maxItems": 0, "items": ITEM_SCHEMA})
    one = root_suite({"type": "array", "minItems": 1, "maxItems": 1, "items": ITEM_SCHEMA})

    assert body_value(root_array_cases(zero)["maxItems"]) == ["x"]
    assert body_value(root_array_cases(one)["minItems"]) == []
    assert body_value(root_array_cases(one)["maxItems"]) == ["x", "x"]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_root_array_unique_items_case(version: str) -> None:
    schema = {"type": "array", "uniqueItems": True, "items": {"type": "integer"}}

    suite = root_suite(schema, version=version)

    cases = root_array_cases(suite)
    assert set(cases) == {"uniqueItems"}
    assert body_value(cases["uniqueItems"]) == [0, 0]
    assert_violates_only(cases["uniqueItems"], schema, version)


def test_root_array_unique_items_with_min_items_three_uses_three_duplicates() -> None:
    suite = root_suite({"type": "array", "minItems": 3, "uniqueItems": True, "items": {"type": "integer"}})

    assert body_value(suite.cases[0]) == [0, 1, -1]
    assert body_value(root_array_cases(suite)["uniqueItems"]) == [0, 0, 0]


def test_root_array_combining_all_constraints() -> None:
    schema = {"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True, "items": {"type": "integer"}}

    suite = root_suite(schema)

    cases = root_array_cases(suite)
    assert list(cases) == ["minItems", "uniqueItems"]
    assert body_value(suite.cases[0]) == [0, 1]
    assert body_value(cases["minItems"]) == [0]
    assert body_value(cases["uniqueItems"]) == [0, 0]
    for case in cases.values():
        assert_violates_only(case, schema)
    # maxItems needs four unique items, so it keeps the existing skip.
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("array_bound_no_safe_candidate", f"{ROOT_PATH}/maxItems")
    ]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA},
        {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 5}},
        {"type": "array", "maxItems": 1, "uniqueItems": True, "items": {"type": "integer"}},
        {"type": "array", "minItems": 3, "uniqueItems": True, "items": {"type": "integer"}},
        {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^(a)$"}},
        {"type": "array", "maxItems": 100, "items": ITEM_SCHEMA},
        {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["a", "b"]}},
    ],
)
def test_root_array_matches_the_same_array_inside_an_object_property(
    schema: dict[str, object],
) -> None:
    root = root_suite(schema)
    prop = array_suite(schema)

    assert [(case.violated_constraint, body_value(case)) for case in array_cases(root)] == [
        (case.violated_constraint, body_value(case)["tags"])  # type: ignore[index]
        for case in array_cases(prop)
    ]
    assert [item.code for item in root.diagnostics] == [item.code for item in prop.diagnostics]
    assert [item.message for item in root.diagnostics] == [item.message for item in prop.diagnostics]


def test_root_array_uses_the_empty_json_pointer() -> None:
    suite = root_suite({"type": "array", "minItems": 2, "maxItems": 3, "items": ITEM_SCHEMA})

    for constraint, case in root_array_cases(suite).items():
        assert case.affected_property == ""
        assert case.violated_constraint == constraint
        assert case.provenance[-2:] == (f"{ROOT_PATH}/{constraint}", f"violated_constraint:{constraint}:")
        assert f"array-bound::{constraint}:" not in case.provenance
    assert {case.affected_property for case in suite.cases if case.violated_constraint not in ARRAY_CONSTRAINTS} <= {None}


def test_root_array_property_cases_keep_their_slash_pointer() -> None:
    suite = array_suite({"type": "array", "minItems": 2, "items": ITEM_SCHEMA})

    assert {case.affected_property for case in array_cases(suite)} == {"/tags"}


def test_root_array_case_ids_are_unique_stable_and_ordered() -> None:
    schema = {"type": "array", "minItems": 1, "maxItems": 1, "uniqueItems": True, "items": {"type": "integer"}}

    first = root_suite(schema)
    second = root_suite(schema)

    ids = [case.case_id for case in first.cases]
    assert len(ids) == len(set(ids))
    assert ids == [case.case_id for case in second.cases]
    assert [case.violated_constraint for case in array_cases(first)] == ["minItems"]
    assert [item.code for item in first.diagnostics] == ["array_bound_no_safe_candidate"] * 2


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_root_array_through_a_local_reference(version: str) -> None:
    array = {"type": "array", "minItems": 1, "maxItems": 2, "items": {"$ref": "#/components/schemas/Name"}}
    schemas = {"Tags": array, "Name": {"type": "string", "minLength": 2}}

    suite = root_suite({"$ref": "#/components/schemas/Tags"}, version=version, schemas=schemas)

    cases = root_array_cases(suite)
    assert body_value(suite.cases[0]) == ["sentinel"]
    assert body_value(cases["minItems"]) == []
    assert body_value(cases["maxItems"]) == ["sentinel"] * 3
    assert {case.affected_property for case in cases.values()} == {""}
    assert cases["minItems"].provenance[-2] == "/components/schemas/Tags/minItems"
    for case in cases.values():
        assert_violates_only(case, {**array, "items": schemas["Name"]}, version)
    assert suite.diagnostics == ()


def test_root_array_with_unresolvable_reference_is_reported_once() -> None:
    suite = root_suite({"$ref": "#/components/schemas/Missing"}, schemas={"Other": {"type": "string"}})

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("unsupported_schema_reference", ROOT_PATH)
    ]


def test_root_array_unsupported_and_insufficient_candidates_keep_existing_diagnostics() -> None:
    unsupported = root_suite({"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string", "pattern": "^(a)$"}})
    no_items = root_suite({"type": "array", "maxItems": 2})
    limit = root_suite({"type": "array", "maxItems": 100, "items": ITEM_SCHEMA})
    non_array = root_suite({"type": "string", "minItems": 1})
    literal = root_suite({"type": "array", "const": [1, 2], "items": {"type": "integer"}, "minItems": 1})

    assert unsupported.cases == ()
    assert [(item.code, item.source_pointer) for item in unsupported.diagnostics] == [
        ("unsupported_schema_keyword", f"{ROOT_PATH}/items/pattern")
    ]
    assert array_cases(no_items) == []
    assert [(item.code, item.source_pointer) for item in no_items.diagnostics] == [
        ("array_bound_no_safe_candidate", f"{ROOT_PATH}/maxItems")
    ]
    assert array_cases(limit) == []
    assert [item.code for item in limit.diagnostics] == ["array_bound_no_safe_candidate"]
    assert "limit of 100" in limit.diagnostics[0].message
    assert array_cases(non_array) == []
    assert [(item.code, item.source_pointer) for item in non_array.diagnostics] == [
        ("array_bound_non_array_type", f"{ROOT_PATH}/minItems")
    ]
    assert array_cases(literal) == []
    assert "array_bound_no_safe_candidate" in {item.code for item in literal.diagnostics}


def test_root_array_items_unsupported_still_allow_the_candidate_without_items() -> None:
    suite = root_suite(
        {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "pattern": "^(a)$"}}
    )

    # The positive baseline needs an item here, so nothing is generated at all.
    assert suite.cases == ()
    empty_ok = root_suite({"type": "array", "maxItems": 2, "items": {"type": "string", "pattern": "^(a)$"}})
    assert body_value(empty_ok.cases[0]) == []
    assert [(item.code, item.source_pointer) for item in empty_ok.diagnostics] == [
        ("array_bound_unsupported_schema", f"{ROOT_PATH}/items/pattern")
    ]


def test_root_array_enum_literal_candidates_and_nested_arrays_use_the_first_element() -> None:
    literal = root_suite({"type": "array", "maxItems": 1, "enum": [[1], [1, 2]], "items": {"type": "integer"}})
    nested = root_suite(
        {"type": "array", "minItems": 1, "items": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "integer"}}}
    )

    assert body_value(root_array_cases(literal)["maxItems"]) == [1, 2]
    assert [(case.affected_property, case.violated_constraint, body_value(case)) for case in array_cases(nested)] == [
        ("", "minItems", []),
        ("/0", "minItems", [[0]]),
        ("/0", "maxItems", [[0, 0, 0, 0]]),
    ]
    assert [item.code for item in nested.diagnostics] == []


def test_root_array_cases_are_isolated_from_each_other_and_the_input() -> None:
    enum = [{"a": [1]}, {"a": [2]}, {"a": [3]}]
    endpoint = post_endpoint(
        {"type": "array", "minItems": 2, "uniqueItems": True, "items": {"enum": enum}}
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    suite = generate_test_suite(result)

    cases = root_array_cases(suite)
    positive = body_value(suite.cases[0])
    min_items = body_value(cases["minItems"])
    unique = body_value(cases["uniqueItems"])
    assert (positive, min_items, unique) == (
        [{"a": [1]}, {"a": [2]}],
        [{"a": [1]}],
        [{"a": [1]}, {"a": [1]}],
    )
    arrays = [positive, min_items, unique]
    assert len({id(array) for array in arrays}) == 3
    items = [item for array in arrays for item in array]  # type: ignore[union-attr]
    assert len({id(item) for item in items}) == len(items)
    positive[0]["a"].append("mutated")  # type: ignore[index]
    unique[1]["a"].append("changed")  # type: ignore[index]
    assert min_items == [{"a": [1]}]
    assert unique[0] == {"a": [1]}  # type: ignore[index]
    assert result.model_dump_json() == before
    assert enum == [{"a": [1]}, {"a": [2]}, {"a": [3]}]


def test_root_array_generation_is_deterministic_and_does_not_mutate_input() -> None:
    result = make_result(
        post_endpoint({"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True, "items": ITEM_SCHEMA})
    )
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


def test_root_array_case_budget_drops_only_the_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {"type": "array", "minItems": 2, "maxItems": 4, "items": ITEM_SCHEMA}
    reference = root_suite(schema)
    before_array = len(reference.cases) - 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 1)

    suite = root_suite(schema)

    assert array_cases(suite) == []
    assert suite.cases == reference.cases[:before_array]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


def test_root_array_expansion_budget_drops_only_the_array_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build() -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint({"type": "array", "minItems": 2, "items": schema_reference("Item")}),
            {"Item": ITEM_SCHEMA},
        )

    # The valid-edge steps run after array_bounds; their hops are not part of this test.
    monkeypatch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        hops_without_array_step = count_expansions(build())
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops_without_array_step)

    suite = generate_test_suite(build())

    assert array_cases(suite) == []
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert suite.cases[0].category == "positive"


def test_object_root_bodies_and_property_arrays_are_unchanged() -> None:
    body = {
        "type": "object",
        "required": ["tags"],
        "properties": {"tags": {"type": "array", "minItems": 2, "maxItems": 3, "items": ITEM_SCHEMA}},
    }

    suite = generate_test_suite(make_result(post_endpoint(body)))

    assert [(case.violated_constraint, case.affected_property) for case in array_cases(suite)] == [
        ("minItems", "/tags"),
        ("maxItems", "/tags"),
    ]
    assert {case.affected_property for case in suite.cases} >= {"/tags"}


# --- Step 8.7: optional parameters with unsupported schemas ---

OPTIONAL_CODE = "optional_parameter_unsupported_schema"
OPTIONAL_PARAMETER_BASE = "/paths/~1items/get/parameters"


def required_id() -> Parameter:
    return Parameter(
        name="id", location="query", required=True,
        schema=Schema(raw={"type": "string"}), source_scope="operation",
    )


def optional_parameter(
    raw: object | None, location: str = "query", name: str = "opt", **extra: object
) -> Parameter:
    return Parameter(
        name=name, location=location, required=False,  # type: ignore[arg-type]
        schema=Schema(raw=raw) if raw is not None else None,  # type: ignore[arg-type]
        source_scope="operation", **extra,
    )


def optional_suite(
    *parameters: Parameter | ParameterReference,
    components: dict[str, object] | None = None,
    parameter_components: dict[str, Parameter | ParameterReference] | None = None,
) -> GeneratedTestSuite:
    endpoint = make_endpoint("/items", parameters=tuple(parameters))
    return generate_test_suite(
        NormalizationResult(
            api=ApiModel(
                openapi_version="3.1.1",
                info=ApiInfo(title="Generated API", version="1.0"),
                endpoints=(endpoint,),
                components=Components(
                    schemas={k: Schema(raw=v) for k, v in (components or {}).items()},  # type: ignore[arg-type]
                    parameters=parameter_components or {},
                ),
            )
        )
    )


def assert_endpoint_generated_without(suite: GeneratedTestSuite, name: str = "opt") -> None:
    """The positive and the required-parameter negative case exist; `name` is never sent."""
    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert suite.cases[1].violated_constraint == "required_parameter"
    for case in suite.cases:
        assert name not in {item.name for item in case.parameters}
    assert [(item.name, item.location, item.value) for item in suite.cases[0].parameters] == [
        ("id", "query", "sentinel")
    ]


@pytest.mark.parametrize(
    ("raw", "components", "pointer", "reference_uri"),
    [
        ({"type": "array", "items": {"type": "string"}}, None, "/schema", None),
        ({"type": "string", "format": "hostname"}, None, "/schema/format", None),
        ({"$ref": "#/components/schemas/Missing"}, {"Other": {"type": "string"}}, "/schema", "#/components/schemas/Missing"),
        ({"$ref": "#/components/schemas/Arr"}, {"Arr": {"type": "array", "items": {"type": "string"}}}, None, None),
        ({"type": "object"}, None, "/schema", None),
        ({"type": "string", "oneOf": [{"type": "string"}]}, None, "/schema/oneOf", None),
        ({"allOf": [{"type": "string"}]}, None, "/schema", None),
        ({"type": "string", "pattern": "^(a)$"}, None, "/schema/pattern", None),
    ],
)
def test_optional_unsupported_schema_is_omitted_and_reported_without_skipping(
    raw: object, components: dict[str, object] | None, pointer: str | None, reference_uri: str | None
) -> None:
    suite = optional_suite(required_id(), optional_parameter(raw), components=components)

    assert_endpoint_generated_without(suite)
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == OPTIONAL_CODE
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.endpoint_method == "get"
    assert diagnostic.reference_uri == reference_uri
    if pointer is not None:
        assert diagnostic.source_pointer == f"{OPTIONAL_PARAMETER_BASE}/1{pointer}"
    assert diagnostic.message.startswith("Optional query parameter 'opt' was omitted because its schema is unsupported: ")
    assert diagnostic.message.endswith(" Endpoint generation continued.")


def test_optional_parameter_through_a_reference_to_an_unsupported_type_points_at_the_target() -> None:
    suite = optional_suite(
        required_id(),
        optional_parameter({"$ref": "#/components/schemas/Arr"}),
        components={"Arr": {"type": "array", "items": {"type": "string"}}},
    )

    assert_endpoint_generated_without(suite)
    assert suite.diagnostics[0].source_pointer == "/components/schemas/Arr"
    assert "skipped" not in suite.diagnostics[0].message


@pytest.mark.parametrize("location", ["query", "header", "cookie"])
def test_optional_unsupported_schema_behaves_the_same_for_every_location(location: str) -> None:
    suite = optional_suite(
        required_id(), optional_parameter({"type": "array", "items": {"type": "string"}}, location)
    )

    assert_endpoint_generated_without(suite)
    assert [item.code for item in suite.diagnostics] == [OPTIONAL_CODE]
    assert f"Optional {location} parameter 'opt'" in suite.diagnostics[0].message


def test_optional_parameter_without_schema_or_with_content_is_omitted_and_reported() -> None:
    no_schema = optional_suite(required_id(), optional_parameter(None))
    with_content = optional_suite(
        required_id(),
        optional_parameter(
            None, content={"application/json": MediaType(schema=Schema(raw={"type": "string"}))}
        ),
    )

    for suite in (no_schema, with_content):
        assert_endpoint_generated_without(suite)
        assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
            (OPTIONAL_CODE, f"{OPTIONAL_PARAMETER_BASE}/1")
        ]
        assert "A direct primitive parameter schema is required." in suite.diagnostics[0].message


@pytest.mark.parametrize(
    "extra",
    [
        {"example": 5},
        {"examples": {"bad": {"value": {"nested": True}}, "worse": {"value": 3}}},
        {"example": "x", "examples": {"a": {"value": "y"}}},
    ],
)
def test_optional_parameter_examples_are_not_processed_when_it_is_omitted(
    extra: dict[str, object],
) -> None:
    suite = optional_suite(
        required_id(), optional_parameter({"type": "string", "minLength": 2}, **extra)
    )

    assert_endpoint_generated_without(suite)
    assert suite.diagnostics == ()


def test_optional_parameter_with_unsatisfiable_value_constraints_no_longer_skips_the_endpoint() -> None:
    suite = optional_suite(
        required_id(), optional_parameter({"type": "string", "minLength": 5, "maxLength": 2})
    )

    assert_endpoint_generated_without(suite)
    assert suite.diagnostics == ()


def test_supported_optional_parameters_are_still_omitted_without_diagnostics() -> None:
    suite = optional_suite(
        required_id(), optional_parameter({"type": "integer"}, name="opt"),
        optional_parameter({"type": "string", "format": "uuid"}, "header", name="trace"),
    )

    assert_endpoint_generated_without(suite)
    assert "trace" not in {item.name for case in suite.cases for item in case.parameters}
    assert suite.diagnostics == ()


def test_one_unsupported_optional_among_supported_ones_reports_only_that_parameter() -> None:
    suite = optional_suite(
        required_id(),
        optional_parameter({"type": "integer"}, name="good"),
        optional_parameter({"type": "object"}, name="bad"),
        optional_parameter({"type": "boolean"}, name="fine"),
    )

    assert_endpoint_generated_without(suite, "good")
    for name in ("good", "bad", "fine"):
        assert all(name not in {item.name for item in case.parameters} for case in suite.cases)
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        (OPTIONAL_CODE, f"{OPTIONAL_PARAMETER_BASE}/2/schema")
    ]
    assert "'bad'" in suite.diagnostics[0].message


def test_optional_diagnostics_follow_declaration_order_and_come_after_the_positive_case() -> None:
    suite = optional_suite(
        optional_parameter({"type": "object"}, name="z"),
        required_id(),
        optional_parameter({"type": "array", "items": {}}, "header", name="a"),
    )

    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert [item.source_pointer for item in suite.diagnostics] == [
        f"{OPTIONAL_PARAMETER_BASE}/0/schema",
        f"{OPTIONAL_PARAMETER_BASE}/2/schema",
    ]
    assert ["'z'" in suite.diagnostics[0].message, "'a'" in suite.diagnostics[1].message] == [True, True]


def test_required_unsupported_parameter_still_skips_the_endpoint() -> None:
    required_array = Parameter(
        name="tags", location="query", required=True,
        schema=Schema(raw={"type": "array", "items": {"type": "string"}}), source_scope="operation",
    )

    suite = optional_suite(required_array, optional_parameter({"type": "object"}))

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("unsupported_parameter_schema", f"{OPTIONAL_PARAMETER_BASE}/0/schema")
    ]
    assert suite.diagnostics[0].message == "This stage supports direct primitive parameter schemas only."


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"type": "string", "format": "hostname"}, "unsupported_schema_keyword"),
        ({"type": "string", "oneOf": [{"type": "string"}]}, "unsupported_schema_composition"),
    ],
)
def test_required_unsupported_keyword_still_skips_with_the_original_diagnostic(
    raw: object, code: str
) -> None:
    required = Parameter(
        name="r", location="header", required=True, schema=Schema(raw=raw), source_scope="operation"  # type: ignore[arg-type]
    )

    suite = optional_suite(required)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == [code]


def test_required_only_endpoint_is_unchanged() -> None:
    suite = optional_suite(
        required_id(),
        Parameter(name="h", location="header", required=True, schema=Schema(raw={"type": "integer"}), source_scope="operation"),
    )

    assert [(case.category, case.violated_constraint) for case in suite.cases] == [
        ("positive", None), ("negative", "required_parameter"), ("negative", "required_parameter"),
    ]
    assert [(item.name, item.value) for item in suite.cases[0].parameters] == [("id", "sentinel"), ("h", 0)]
    assert suite.diagnostics == ()


def test_optional_parameter_does_not_change_body_negative_cases() -> None:
    body = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    with_optional = generate_test_suite(
        make_result(make_endpoint("/items", "post", parameters=(optional_parameter({"type": "object"}),), request_body=body_with_schema(body)))
    )
    without = generate_test_suite(
        make_result(make_endpoint("/items", "post", request_body=body_with_schema(body)))
    )

    assert with_optional.cases == without.cases
    assert [item.code for item in with_optional.diagnostics] == [OPTIONAL_CODE] + [
        item.code for item in without.diagnostics
    ]


def test_referenced_optional_parameter_with_unsupported_schema_points_at_the_component() -> None:
    suite = optional_suite(
        required_id(),
        parameter_reference("Tags"),
        parameter_components={"Tags": optional_parameter({"type": "array", "items": {}}, name="tags")},
    )

    assert_endpoint_generated_without(suite, "tags")
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        (OPTIONAL_CODE, "/components/parameters/Tags/schema")
    ]
    assert "parameter 'tags'" in suite.diagnostics[0].message


@pytest.mark.parametrize(
    "uri", ["#/components/parameters/Missing", "#/components/schemas/Tags", "#/components/parameters/a/b"]
)
def test_unresolved_parameter_reference_is_not_treated_as_a_safe_optional_parameter(uri: str) -> None:
    reference = ParameterReference(reference=LocalReference(uri=uri), source_scope="operation")

    suite = optional_suite(required_id(), reference)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_parameter_reference"]


def test_parameter_reference_cycle_still_skips_the_endpoint() -> None:
    suite = optional_suite(
        required_id(),
        parameter_reference("A"),
        parameter_components={"A": parameter_reference("A")},
    )

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_parameter_reference"]


def test_optional_unsupported_parameter_diagnostics_respect_the_diagnostic_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 2)

    suite = optional_suite(
        required_id(), *(optional_parameter({"type": "object"}, name=f"p{i}") for i in range(4))
    )

    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert [item.code for item in suite.diagnostics][:2] == [OPTIONAL_CODE, OPTIONAL_CODE]
    assert [item.source_pointer for item in suite.diagnostics][:2] == [
        f"{OPTIONAL_PARAMETER_BASE}/1/schema", f"{OPTIONAL_PARAMETER_BASE}/2/schema"
    ]
    assert len(suite.diagnostics) == 3


def test_optional_unsupported_parameter_generation_is_deterministic_and_keeps_the_input() -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(
            required_id(),
            optional_parameter({"type": "array", "items": {}}, name="a"),
            optional_parameter({"type": "string", "format": "hostname"}, "header", name="b"),
        ),
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert len(first.diagnostics) == 2


# --- Step 8.10: reserved header parameters are ignored ---

RESERVED_CODE = "reserved_header_parameter_ignored"


def header_parameter(name: str, raw: object = None, *, required: bool = True) -> Parameter:
    return Parameter(
        name=name, location="header", required=required,
        schema=Schema(raw=raw if raw is not None else {"type": "string"}),  # type: ignore[arg-type]
        source_scope="operation",
    )


def assert_only_the_id_parameter(suite: GeneratedTestSuite) -> None:
    """Positive plus one omission case for `id`; no reserved header anywhere."""
    assert [(case.category, case.violated_constraint) for case in suite.cases] == [
        ("positive", None), ("negative", "required_parameter"),
    ]
    assert [(p.name, p.location) for p in suite.cases[0].parameters] == [("id", "query")]
    assert suite.cases[1].parameters == ()
    assert all(p.location != "header" for case in suite.cases for p in case.parameters)


@pytest.mark.parametrize("name", ["Accept", "Content-Type", "Authorization"])
@pytest.mark.parametrize("required", [True, False])
def test_reserved_header_parameter_is_ignored_with_a_diagnostic(name: str, required: bool) -> None:
    suite = optional_suite(required_id(), header_parameter(name, required=required))

    assert_only_the_id_parameter(suite)
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == RESERVED_CODE
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.endpoint_method == "get"
    assert diagnostic.source_pointer == f"{OPTIONAL_PARAMETER_BASE}/1"
    assert diagnostic.reference_uri is None
    assert diagnostic.message == (
        f"Header parameter '{name}' was ignored because OpenAPI ignores header parameters "
        "named Accept, Content-Type or Authorization; no header value or omission case was generated."
    )


@pytest.mark.parametrize("name", ["accept", "ACCEPT", "content-TYPE", "CONTENT-type", "authorization", "AuThOrIzAtIoN"])
def test_reserved_header_names_match_case_insensitively(name: str) -> None:
    suite = optional_suite(required_id(), header_parameter(name))

    assert_only_the_id_parameter(suite)
    assert [item.code for item in suite.diagnostics] == [RESERVED_CODE]
    assert f"'{name}'" in suite.diagnostics[0].message


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "string", "format": "hostname"},
        {"$ref": "#/components/schemas/Missing"},
        {"type": "object"},
    ],
)
def test_unsupported_schema_on_a_reserved_header_does_not_skip_the_endpoint(raw: object) -> None:
    suite = optional_suite(
        required_id(), header_parameter("Authorization", raw), components={"Other": {"type": "string"}}
    )

    assert_only_the_id_parameter(suite)
    assert [item.code for item in suite.diagnostics] == [RESERVED_CODE]


def test_reserved_header_without_a_schema_or_with_content_is_ignored() -> None:
    no_schema = Parameter(name="Accept", location="header", required=True, source_scope="operation")
    with_content = Parameter(
        name="Content-Type", location="header", required=True, source_scope="operation",
        content={"application/json": MediaType(schema=Schema(raw={"type": "string"}))},
    )

    suite = optional_suite(required_id(), no_schema, with_content)

    assert_only_the_id_parameter(suite)
    assert [item.source_pointer for item in suite.diagnostics] == [
        f"{OPTIONAL_PARAMETER_BASE}/1", f"{OPTIONAL_PARAMETER_BASE}/2"
    ]


def test_referenced_reserved_header_is_ignored_and_keeps_the_reference() -> None:
    suite = optional_suite(
        required_id(),
        parameter_reference("Auth"),
        parameter_components={"Auth": header_parameter("Authorization", {"type": "array", "items": {}})},
    )

    assert_only_the_id_parameter(suite)
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == RESERVED_CODE
    assert diagnostic.source_pointer == f"{OPTIONAL_PARAMETER_BASE}/1"
    assert diagnostic.reference_uri == "#/components/parameters/Auth"


def test_referenced_reserved_header_through_an_alias_chain_reports_the_first_reference() -> None:
    suite = optional_suite(
        required_id(),
        parameter_reference("Alias"),
        parameter_components={
            "Alias": parameter_reference("Accept"),
            "Accept": header_parameter("accept"),
        },
    )

    assert_only_the_id_parameter(suite)
    assert suite.diagnostics[0].reference_uri == "#/components/parameters/Alias"


def test_non_reserved_header_parameters_are_unaffected() -> None:
    suite = optional_suite(
        required_id(),
        header_parameter("X-Accept"),
        header_parameter("Accept-Language"),
        header_parameter("Authorization-Token"),
        header_parameter("Accept", required=False),
    )

    assert [(p.name, p.location) for p in suite.cases[0].parameters] == [
        ("id", "query"), ("X-Accept", "header"), ("Accept-Language", "header"), ("Authorization-Token", "header"),
    ]
    assert [(c.category, len(c.parameters)) for c in suite.cases] == [
        ("positive", 4), ("negative", 3), ("negative", 3), ("negative", 3), ("negative", 3),
    ]
    assert [item.code for item in suite.diagnostics] == [RESERVED_CODE]


def test_same_names_in_other_locations_are_not_reserved() -> None:
    suite = optional_suite(
        Parameter(name="Authorization", location="query", required=True, schema=Schema(raw={"type": "string"}), source_scope="operation"),
        Parameter(name="Accept", location="cookie", required=True, schema=Schema(raw={"type": "string"}), source_scope="operation"),
    )

    assert [(p.name, p.location) for p in suite.cases[0].parameters] == [
        ("Authorization", "query"), ("Accept", "cookie"),
    ]
    assert suite.diagnostics == ()


def test_reserved_header_only_endpoint_still_generates_a_positive_case() -> None:
    suite = optional_suite(header_parameter("Content-Type"), header_parameter("accept"))

    assert [(c.category, c.parameters) for c in suite.cases] == [("positive", ())]
    assert [item.code for item in suite.diagnostics] == [RESERVED_CODE, RESERVED_CODE]


def test_reserved_header_does_not_hide_an_unsupported_required_parameter() -> None:
    required_array = Parameter(
        name="tags", location="query", required=True,
        schema=Schema(raw={"type": "array", "items": {}}), source_scope="operation",
    )

    suite = optional_suite(header_parameter("Accept"), required_array)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_parameter_schema"]


def test_reserved_and_optional_unsupported_diagnostics_keep_declaration_order() -> None:
    suite = optional_suite(
        optional_parameter({"type": "object"}, name="first"),
        header_parameter("Authorization"),
        required_id(),
        header_parameter("content-type", required=False),
    )

    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        (OPTIONAL_CODE, f"{OPTIONAL_PARAMETER_BASE}/0/schema"),
        (RESERVED_CODE, f"{OPTIONAL_PARAMETER_BASE}/1"),
        (RESERVED_CODE, f"{OPTIONAL_PARAMETER_BASE}/3"),
    ]


def test_reserved_header_diagnostics_respect_the_diagnostic_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 2)

    suite = optional_suite(
        required_id(), header_parameter("Accept"), header_parameter("Authorization"), header_parameter("Content-Type")
    )

    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert [item.code for item in suite.diagnostics] == [
        RESERVED_CODE, RESERVED_CODE, "generation_diagnostic_budget_exceeded"
    ]


def test_reserved_header_diagnostics_are_dropped_when_the_endpoint_is_skipped() -> None:
    suite = optional_suite(header_parameter("Accept"), Parameter(
        name="tags", location="query", required=True, schema=Schema(raw={"type": "object"}), source_scope="operation"
    ))

    assert [item.code for item in suite.diagnostics] == ["unsupported_parameter_schema"]


def test_reserved_header_generation_is_deterministic_and_keeps_the_input() -> None:
    endpoint = make_endpoint(
        "/items",
        parameters=(required_id(), header_parameter("ACCEPT"), header_parameter("Authorization", {"type": "object"})),
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert [item.code for item in first.diagnostics] == [RESERVED_CODE, RESERVED_CODE]


# --- Step 8.13: unusable example candidates are skipped, not fatal ---

EXAMPLE_REF = LocalReference(uri="#/components/examples/Shared")
UNUSABLE_EXAMPLES: dict[str, object] = {
    "reference": EXAMPLE_REF,
    "external": {"externalValue": "https://example.invalid/value.txt"},
    "no-value": {"summary": "no value here"},
}
UNUSABLE_CODES = {
    "reference": "unsupported_example_reference",
    "external": "unsupported_example_value",
    "no-value": "unsupported_example_value",
}
KINDS = ["parameter", "body"]


def example_suite(
    kind: str,
    schema: dict[str, object],
    *,
    example: object = None,
    examples: dict[str, object] | None = None,
) -> GeneratedTestSuite:
    if kind == "parameter":
        endpoint = make_endpoint(
            "/items",
            parameters=(
                Parameter(
                    name="q", location="query", required=True, schema=Schema(raw=schema),
                    example=example, examples=examples or {}, source_scope="operation",  # type: ignore[arg-type]
                ),
            ),
        )
    else:
        endpoint = make_endpoint(
            "/items",
            "post",
            request_body=RequestBody(
                required=True,
                content={
                    "application/json": MediaType(
                        schema=Schema(raw=schema), example=example, examples=examples or {}  # type: ignore[arg-type]
                    )
                },
            ),
        )
    return generate_test_suite(make_result(endpoint))


def example_result(suite: GeneratedTestSuite, kind: str) -> tuple[object, str]:
    """The generated value and the candidate pointer it came from."""
    positive = suite.cases[0]
    if kind == "parameter":
        return positive.parameters[0].value, positive.parameters[0].provenance[-1]
    assert positive.request_body is not None
    return positive.request_body.value, positive.provenance[-1]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unusable", sorted(UNUSABLE_EXAMPLES))
def test_unusable_example_before_a_valid_inline_example_is_skipped(kind: str, unusable: str) -> None:
    suite = example_suite(
        kind, {"type": "string"}, examples={"a": UNUSABLE_EXAMPLES[unusable], "b": {"value": "B"}}
    )

    value, pointer = example_result(suite, kind)
    assert value == "B"
    assert pointer.endswith("/examples/b/value")
    assert suite.diagnostics == ()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unusable", sorted(UNUSABLE_EXAMPLES))
def test_unusable_example_after_a_valid_inline_example_does_not_block_it(kind: str, unusable: str) -> None:
    suite = example_suite(
        kind, {"type": "string"}, examples={"a": {"value": "A"}, "b": UNUSABLE_EXAMPLES[unusable]}
    )

    value, pointer = example_result(suite, kind)
    assert (value, suite.diagnostics) == ("A", ())
    assert pointer.endswith("/examples/a/value")


@pytest.mark.parametrize("kind", KINDS)
def test_unusable_examples_fall_back_to_a_valid_schema_candidate(kind: str) -> None:
    examples = dict(UNUSABLE_EXAMPLES)
    by_schema_example = example_suite(
        kind, {"type": "string", "example": "SCHEMA", "default": "DEFAULT"}, examples=examples
    )
    by_schema_examples = example_suite(
        kind, {"type": "string", "examples": ["FROM-LIST"], "default": "DEFAULT"}, examples=examples
    )
    by_default = example_suite(kind, {"type": "string", "default": "DEFAULT"}, examples=examples)
    by_enum = example_suite(kind, {"type": "string", "enum": ["E1", "E2"]}, examples=examples)

    assert example_result(by_schema_example, kind)[0] == "SCHEMA"
    assert example_result(by_schema_example, kind)[1].endswith("/example")
    assert example_result(by_schema_examples, kind)[0] == "FROM-LIST"
    assert example_result(by_default, kind)[0] == "DEFAULT"
    assert example_result(by_default, kind)[1].endswith("/default")
    assert example_result(by_enum, kind)[0] == "E1"
    for suite in (by_schema_example, by_schema_examples, by_default, by_enum):
        assert suite.diagnostics == ()


@pytest.mark.parametrize("kind", KINDS)
def test_unusable_examples_fall_back_to_the_fixed_fallback_without_a_diagnostic(kind: str) -> None:
    suite = example_suite(kind, {"type": "integer"}, examples=dict(UNUSABLE_EXAMPLES))

    value, pointer = example_result(suite, kind)
    assert value == 0
    assert pointer.endswith("/schema")
    assert suite.diagnostics == ()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "minLength": 5, "maxLength": 2},
        {"type": "string", "enum": ["abc"], "maxLength": 1},
    ],
)
def test_all_candidates_unusable_keeps_the_original_example_diagnostic(
    kind: str, schema: dict[str, object]
) -> None:
    suite = example_suite(
        kind, schema, examples={"b": UNUSABLE_EXAMPLES["external"], "a": EXAMPLE_REF}
    )

    assert suite.cases == ()
    [diagnostic] = suite.diagnostics
    # Sorted by name, so "a" (the reference) is the first unusable example.
    assert diagnostic.code == "unsupported_example_reference"
    assert diagnostic.reference_uri == "#/components/examples/Shared"
    assert diagnostic.source_pointer.endswith("/examples/a")  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unusable", sorted(UNUSABLE_EXAMPLES))
def test_each_unusable_example_form_keeps_its_own_diagnostic_when_nothing_is_usable(
    kind: str, unusable: str
) -> None:
    suite = example_suite(
        kind, {"type": "string", "minLength": 5, "maxLength": 2}, examples={"x": UNUSABLE_EXAMPLES[unusable]}
    )

    assert suite.cases == ()
    assert [(item.code, item.source_pointer.rsplit("/", 2)[-2:]) for item in suite.diagnostics] == [  # type: ignore[union-attr]
        (UNUSABLE_CODES[unusable], ["examples", "x"])
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_all_candidates_invalid_without_an_unusable_example_keeps_the_schema_diagnostic(kind: str) -> None:
    suite = example_suite(
        kind, {"type": "string", "minLength": 5, "maxLength": 2}, examples={"a": {"value": "ok"}}
    )

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize("kind", KINDS)
def test_several_unusable_examples_are_skipped_in_sorted_name_order(kind: str) -> None:
    suite = example_suite(
        kind,
        {"type": "string"},
        examples={
            "d": {"value": "D"}, "c": EXAMPLE_REF, "b": {"value": "B"},
            "a": UNUSABLE_EXAMPLES["no-value"], "e": UNUSABLE_EXAMPLES["external"],
        },
    )

    assert example_result(suite, kind)[0] == "B"
    assert suite.diagnostics == ()


@pytest.mark.parametrize("kind", KINDS)
def test_example_precedence_is_unchanged_around_unusable_examples(kind: str) -> None:
    schema = {"type": "string", "example": "SCHEMA", "default": "DEFAULT"}
    with_example = example_suite(kind, schema, example="ONE", examples={"a": EXAMPLE_REF, "b": {"value": "TWO"}})
    invalid_example = example_suite(kind, schema, example=5, examples={"a": EXAMPLE_REF, "b": {"value": "TWO"}})
    const = example_suite(
        kind, {"type": "string", "const": "FIXED"}, example="ONE", examples={"a": EXAMPLE_REF}
    )

    assert example_result(with_example, kind)[0] == "ONE"
    assert example_result(invalid_example, kind)[0] == "TWO"
    assert example_result(const, kind)[0] == "FIXED"


@pytest.mark.parametrize("kind", KINDS)
def test_schema_validation_still_rejects_invalid_example_values(kind: str) -> None:
    schema = {"type": "string", "maxLength": 3, "example": "ok"}
    suite = example_suite(
        kind, schema, example="too long", examples={"a": EXAMPLE_REF, "b": {"value": 7}, "c": {"value": "also too long"}}
    )
    only_invalid = example_suite(
        kind, {"type": "string", "maxLength": 3}, examples={"a": EXAMPLE_REF, "b": {"value": "way too long"}}
    )

    assert example_result(suite, kind)[0] == "ok"
    assert example_result(only_invalid, kind)[0] == "sen"  # the fixed fallback, cut to maxLength


@pytest.mark.parametrize("kind", KINDS)
def test_unusable_example_handling_is_deterministic_and_does_not_mutate_the_input(kind: str) -> None:
    if kind == "parameter":
        endpoint = make_endpoint(
            "/items",
            parameters=(
                Parameter(
                    name="q", location="query", required=True, schema=Schema(raw={"type": "string"}),
                    examples={"a": EXAMPLE_REF, "b": {"value": "B"}, "c": UNUSABLE_EXAMPLES["external"]},  # type: ignore[arg-type]
                    source_scope="operation",
                ),
            ),
        )
    else:
        endpoint = make_endpoint(
            "/items", "post",
            request_body=RequestBody(
                required=True,
                content={"application/json": MediaType(
                    schema=Schema(raw={"type": "string"}),
                    examples={"a": EXAMPLE_REF, "b": {"value": "B"}, "c": UNUSABLE_EXAMPLES["external"]},  # type: ignore[arg-type]
                )},
            ),
        )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert example_result(first, kind)[0] == "B"
    assert first.diagnostics == ()


def test_unusable_examples_do_not_affect_other_parameters_or_the_diagnostic_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 1)
    endpoint = make_endpoint(
        "/items",
        parameters=(
            Parameter(name="a", location="query", required=True, schema=Schema(raw={"type": "string"}),
                      examples={"x": EXAMPLE_REF}, source_scope="operation"),  # type: ignore[arg-type]
            Parameter(name="b", location="header", required=True, schema=Schema(raw={"type": "integer"}),
                      example=9, source_scope="operation"),
        ),
    )

    suite = generate_test_suite(make_result(endpoint))

    assert [(p.name, p.value) for p in suite.cases[0].parameters] == [("a", "sentinel"), ("b", 9)]
    assert suite.diagnostics == ()
    assert [case.category for case in suite.cases] == ["positive", "negative", "negative"]


# --- Step 8.16: optional arrays absent from the positive body (parent present) ---

TAGS_BASE = ARRAY_BODY_POINTER + "/properties/tags"


def optional_body(properties: dict[str, object], required: tuple[str, ...] = ()) -> dict[str, object]:
    return {"type": "object", "required": list(required), "properties": properties}


def optional_array_suite(body: dict[str, object], schemas: dict[str, dict[str, object]] | None = None) -> GeneratedTestSuite:
    endpoint = post_endpoint(body)
    if schemas:
        return generate_test_suite(make_result_with_schemas(endpoint, schemas))
    return generate_test_suite(make_result(endpoint))


def array_values(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None, object]]:
    return [
        (case.affected_property, case.violated_constraint, case.request_body.value)  # type: ignore[union-attr]
        for case in array_cases(suite)
    ]


@pytest.mark.parametrize(
    ("schema", "constraint", "expected"),
    [
        ({"type": "array", "minItems": 2, "items": ITEM_SCHEMA}, "minItems", ["x"]),
        ({"type": "array", "maxItems": 2, "items": ITEM_SCHEMA}, "maxItems", ["x", "x", "x"]),
        ({"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA}, "uniqueItems", ["x", "x"]),
        ({"type": "array", "minItems": 1, "items": ITEM_SCHEMA}, "minItems", []),
    ],
)
def test_optional_root_level_array_gets_a_boundary_case_added_to_the_baseline(
    schema: dict[str, object], constraint: str, expected: list[object]
) -> None:
    suite = array_suite(schema, required=False)

    assert suite.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert array_values(suite) == [("/tags", constraint, {"tags": expected})]
    case = array_cases(suite)[0]
    assert case.category == "negative"
    assert case.provenance[-3:] == (
        tags_schema_pointer(), tags_schema_pointer(constraint), f"violated_constraint:{constraint}:/tags",
    )
    assert array_diagnostics(suite) == []


def test_optional_array_keeps_the_other_required_properties_of_the_baseline() -> None:
    body = optional_body(
        {"id": {"type": "integer"}, "name": ITEM_SCHEMA, "tags": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA}},
        required=("id", "name"),
    )

    suite = optional_array_suite(body)

    assert suite.cases[0].request_body.value == {"id": 0, "name": "x"}  # type: ignore[union-attr]
    assert array_values(suite) == [("/tags", "minItems", {"id": 0, "name": "x", "tags": []})]


def test_optional_array_combining_all_constraints_matches_the_same_required_array() -> None:
    schema = {"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True, "items": {"type": "integer"}}

    optional = array_suite(schema, required=False)
    required = array_suite(schema)

    assert [(c, v["tags"]) for _, c, v in array_values(optional)] == [  # type: ignore[index]
        (c, v["tags"]) for _, c, v in array_values(required)  # type: ignore[index]
    ] == [("minItems", [0]), ("uniqueItems", [0, 0])]
    assert array_diagnostics(optional) == array_diagnostics(required) == [
        ("array_bound_no_safe_candidate", tags_schema_pointer("maxItems"))
    ]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "maxItems": 2},
        {"type": "array", "maxItems": 100, "items": ITEM_SCHEMA},
        {"type": "string", "minItems": 1},
        {"type": "array", "minItems": 3, "uniqueItems": True, "items": {"type": "integer"}},
    ],
)
def test_optional_array_diagnostics_and_candidates_match_the_required_array(
    schema: dict[str, object]
) -> None:
    optional = array_suite(schema, required=False)
    required = array_suite(schema)

    assert [(c, v["tags"]) for _, c, v in array_values(optional)] == [  # type: ignore[index]
        (c, v["tags"]) for _, c, v in array_values(required)  # type: ignore[index]
    ]
    assert array_diagnostics(optional) == array_diagnostics(required)
    assert [d.message for d in optional.diagnostics if d.code.startswith("array_bound_")] == [
        d.message for d in required.diagnostics if d.code.startswith("array_bound_")
    ]


def test_optional_array_with_unsupported_items_reports_the_existing_diagnostic() -> None:
    suite = array_suite(
        {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "pattern": "^(a)$"}},
        required=False,
    )

    assert array_values(suite) == [("/tags", "minItems", {"tags": []})]
    assert array_diagnostics(suite) == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


def test_optional_array_inside_a_present_nested_object_is_added_to_that_object() -> None:
    body = optional_body(
        {
            "outer": optional_body(
                {
                    "label": ITEM_SCHEMA,
                    "inner": optional_body({"tags": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA}}),
                },
                ("label", "inner"),
            ),
        },
        ("outer",),
    )

    suite = optional_array_suite(body)

    assert suite.cases[0].request_body.value == {"outer": {"label": "x", "inner": {}}}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/outer/inner/tags", "minItems", {"outer": {"label": "x", "inner": {"tags": []}}})
    ]


def test_optional_array_inside_an_absent_parent_is_created_in_negative_cases_only() -> None:
    absent_parent = optional_body(
        {"a": ITEM_SCHEMA, "outer": optional_body({"tags": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA}}, ("tags",))},
        ("a",),
    )
    absent_optional_in_absent = optional_body(
        {"outer": optional_body({"tags": {"type": "array", "maxItems": 1, "items": ITEM_SCHEMA}})}
    )

    first = optional_array_suite(absent_parent)
    second = optional_array_suite(absent_optional_in_absent)

    assert array_values(first) == [("/outer/tags", "minItems", {"a": "x", "outer": {"tags": []}})]
    assert array_values(second) == [("/outer/tags", "maxItems", {"outer": {"tags": ["x", "x"]}})]
    for suite in (first, second):
        assert "outer" not in (suite.cases[0].request_body.value or {})  # type: ignore[union-attr]
        assert array_diagnostics(suite) == []


def test_optional_array_reached_through_a_property_reference() -> None:
    body = optional_body({"a": ITEM_SCHEMA, "tags": {"$ref": "#/components/schemas/Tags"}}, ("a",))
    schemas = {"Tags": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"$ref": "#/components/schemas/Name"}}, "Name": ITEM_SCHEMA}

    suite = optional_array_suite(body, schemas)

    assert array_values(suite) == [
        ("/tags", "minItems", {"a": "x", "tags": []}),
        ("/tags", "maxItems", {"a": "x", "tags": ["x", "x", "x"]}),
    ]
    assert array_cases(suite)[0].provenance[-2] == "/components/schemas/Tags/minItems"
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize("through_reference", [False, True])
def test_optional_read_only_array_is_excluded_and_write_only_is_targeted(
    through_reference: bool,
) -> None:
    read_only = {"type": "array", "minItems": 1, "items": ITEM_SCHEMA, "readOnly": True}
    write_only = {"type": "array", "minItems": 1, "items": ITEM_SCHEMA, "writeOnly": True}
    read_write = {"type": "array", "minItems": 1, "items": ITEM_SCHEMA, "readOnly": False}
    schemas = {"RO": read_only, "WO": write_only, "RW": read_write}
    pick = (lambda name, raw: {"$ref": f"#/components/schemas/{name}"}) if through_reference else (lambda name, raw: raw)
    body = optional_body({"ro": pick("RO", read_only), "wo": pick("WO", write_only), "rw": pick("RW", read_write)})

    suite = optional_array_suite(body, schemas if through_reference else None)

    assert [(case.affected_property, case.request_body.value) for case in array_cases(suite)] == [  # type: ignore[union-attr]
        ("/rw", {"rw": []}), ("/wo", {"wo": []}),
    ]
    assert array_diagnostics(suite) == []


def test_unresolvable_optional_property_reference_does_not_add_diagnostics() -> None:
    body = optional_body({"a": ITEM_SCHEMA, "ghost": {"$ref": "#/components/schemas/Missing"}}, ("a",))

    suite = optional_array_suite(body, {"Other": ITEM_SCHEMA})

    assert array_cases(suite) == []
    assert array_diagnostics(suite) == []


def test_optional_non_array_property_with_array_keywords_reports_the_existing_diagnostic() -> None:
    suite = array_suite({"type": "string", "minItems": 1}, required=False)

    assert array_cases(suite) == []
    assert array_diagnostics(suite) == [
        ("array_bound_non_array_type", tags_schema_pointer("minItems"))
    ]


def test_required_and_optional_arrays_are_ordered_by_property_name_and_required_cases_are_unchanged() -> None:
    props = {
        "c": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA},
        "b": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA},
        "a": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA},
    }
    mixed = optional_array_suite(optional_body(props, ("b",)))
    required_only = optional_array_suite(optional_body({"b": props["b"]}, ("b",)))

    assert [(c.affected_property, c.request_body.value) for c in array_cases(mixed)] == [  # type: ignore[union-attr]
        ("/a", {"b": ["x"], "a": []}), ("/b", {"b": []}), ("/c", {"b": ["x"], "c": []}),
    ]
    required_case = next(c for c in array_cases(mixed) if c.affected_property == "/b")
    assert required_case == array_cases(required_only)[0]


def test_positive_bodies_are_unchanged_and_negative_bodies_only_add_the_array() -> None:
    body = optional_body(
        {"id": {"type": "integer"}, "tags": {"type": "array", "minItems": 2, "maxItems": 3, "items": ITEM_SCHEMA}}, ("id",)
    )
    without = optional_array_suite(optional_body({"id": {"type": "integer"}}, ("id",)))

    suite = optional_array_suite(body)

    assert suite.cases[0] == without.cases[0]
    positive = suite.cases[0].request_body.value  # type: ignore[union-attr]
    assert positive == {"id": 0}
    for case in array_cases(suite):
        value = dict(case.request_body.value)  # type: ignore[union-attr, arg-type]
        assert value.pop("tags") is not None
        assert value == positive


def test_optional_array_cases_are_deterministic_isolated_and_do_not_mutate_the_input() -> None:
    enum = [{"a": [1]}, {"a": [2]}]
    body = optional_body(
        {"tags": {"type": "array", "minItems": 2, "uniqueItems": True, "items": {"enum": enum}}}
    )
    result = make_result(post_endpoint(body))
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    ids = [case.case_id for case in first.cases]
    assert len(ids) == len(set(ids))
    cases = array_cases(first)
    assert [c.violated_constraint for c in cases] == ["minItems", "uniqueItems"]
    min_items, unique = (c.request_body.value["tags"] for c in cases)  # type: ignore[union-attr, index]
    assert (min_items, unique) == ([{"a": [1]}], [{"a": [1]}, {"a": [1]}])
    min_items[0]["a"].append("mutated")
    assert unique[0] == {"a": [1]} and unique[1] == {"a": [1]}
    assert enum == [{"a": [1]}, {"a": [2]}]


def test_case_budget_is_shared_by_required_and_optional_arrays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = optional_body(
        {
            "r": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA},
            "o": {"type": "array", "minItems": 1, "maxItems": 2, "items": ITEM_SCHEMA},
        },
        ("r",),
    )
    reference = optional_array_suite(body)
    array_total = len(array_cases(reference))
    before_array = len(reference.cases) - array_total
    assert array_total == 3

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + array_total)
    fits = optional_array_suite(body)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + array_total - 1)
    overflows = optional_array_suite(body)

    assert [c.affected_property for c in array_cases(fits)] == ["/o", "/o", "/r"]
    assert array_cases(overflows) == []
    assert overflows.cases == reference.cases[:before_array]
    assert [d.code for d in overflows.diagnostics][-1] == "generation_case_budget_exceeded"
    assert "'array_bounds'" in overflows.diagnostics[-1].message


def test_optional_array_reached_through_a_reference_is_resolved_once_in_the_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build(required: bool) -> NormalizationResult:
        body = optional_body(
            {"tags": {"$ref": "#/components/schemas/Tags"}}, ("tags",) if required else ()
        )
        return make_result_with_schemas(
            post_endpoint(body),
            {"Tags": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA, "writeOnly": True}},
        )

    def array_step_hops(required: bool) -> int:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
            without_step = count_expansions(build(required))
        return count_expansions(build(required)) - without_step

    # The present (required) property path resolves the reference exactly once.
    assert array_step_hops(required=True) == 1
    assert array_step_hops(required=False) == 1

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        without_step = count_expansions(build(False))
    # One extra expansion is enough for the optional array; a second resolution would exhaust it.
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", without_step + 1)

    suite = generate_test_suite(build(False))

    assert [(c.affected_property, c.request_body.value) for c in array_cases(suite)] == [  # type: ignore[union-attr]
        ("/tags", {"tags": []})
    ]
    assert "generation_expansion_budget_exceeded" not in {d.code for d in suite.diagnostics}


# --- additionalProperties (request side) -----------------------------------

AP_VERSIONS = ("3.0.3", "3.1.1")
AP_STRING: dict[str, object] = {"type": "string"}


def additional_suite(
    schema: object,
    *,
    version: str = "3.1.1",
    example: object = None,
    schemas: dict[str, object] | None = None,
) -> GeneratedTestSuite:
    media = (
        MediaType(schema=Schema(raw=schema))
        if example is None
        else MediaType(schema=Schema(raw=schema), example=example)
    )
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=RequestBody(required=True, content={"application/json": media}),
        responses=BAD_REQUEST,
    )
    components = (
        Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})
        if schemas
        else None
    )
    return generate_test_suite(make_result(endpoint, version=version, components=components))


def additional_object(additional: object = ..., **extra: object) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": AP_STRING},
        **extra,
    }
    if additional is not ...:
        schema["additionalProperties"] = additional
    return schema


def positive_body(suite: GeneratedTestSuite) -> object:
    assert suite.cases[0].category == "positive"
    assert suite.cases[0].request_body is not None
    return suite.cases[0].request_body.value


def negative_summary(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None, object]]:
    return [
        (c.violated_constraint, c.affected_property, c.request_body.value if c.request_body else None)
        for c in suite.cases
        if c.category == "negative" and c.request_body is not None and c.affected_property is not None
    ]


@pytest.mark.parametrize("version", AP_VERSIONS)
def test_root_object_with_additional_properties_false_keeps_positive_and_negative_cases(version: str) -> None:
    schema = additional_object(
        False, properties={"a": AP_STRING, "n": {"type": "integer", "maximum": 5}}
    )

    suite = additional_suite(schema, version=version)

    assert positive_body(suite) == {"a": "sentinel"}
    assert negative_summary(suite) == [
        ("required_property", "/a", {}),
        ("maximum", "/n", {"a": "sentinel", "n": 6}),
        ("type", "/a", {"a": 0}),
    ]
    assert suite.diagnostics == ()


def test_nested_required_object_with_additional_properties_false_is_generated() -> None:
    schema = {
        "type": "object",
        "required": ["o"],
        "properties": {"o": additional_object(False)},
    }

    suite = additional_suite(schema)

    assert positive_body(suite) == {"o": {"a": "sentinel"}}
    assert {(c, p) for c, p, _ in negative_summary(suite)} >= {("required_property", "/o/a"), ("type", "/o/a")}
    assert suite.diagnostics == ()


@pytest.mark.parametrize("version", AP_VERSIONS)
def test_required_map_property_with_schema_valued_additional_properties_is_generated(version: str) -> None:
    schema = {
        "type": "object",
        "required": ["m"],
        "properties": {"m": {"type": "object", "additionalProperties": {"type": "string"}}},
    }

    suite = additional_suite(schema, version=version)

    # No arbitrary map keys are invented.
    assert positive_body(suite) == {"m": {}}
    assert ("required_property", "/m", {}) in negative_summary(suite)
    assert suite.diagnostics == ()


def test_additional_properties_true_and_absent_keep_extra_properties_of_an_example() -> None:
    example = {"a": "x", "extra": 1}

    for additional in (True, ...):
        suite = additional_suite(additional_object(additional), example=example)
        assert positive_body(suite) == example


def test_additional_properties_false_rejects_an_example_with_extra_properties() -> None:
    suite = additional_suite(additional_object(False), example={"a": "x", "extra": 1})

    assert positive_body(suite) == {"a": "sentinel"}
    assert suite.diagnostics == ()


def test_additional_properties_false_accepts_an_example_with_declared_properties_only() -> None:
    suite = additional_suite(additional_object(False), example={"a": "x"})

    assert positive_body(suite) == {"a": "x"}


def test_additional_property_values_are_validated_against_the_schema() -> None:
    schema = additional_object({"type": "integer", "minimum": 0})

    accepted = additional_suite(schema, example={"a": "x", "count": 3})
    wrong_type = additional_suite(schema, example={"a": "x", "count": "three"})
    out_of_range = additional_suite(schema, example={"a": "x", "count": -1})

    assert positive_body(accepted) == {"a": "x", "count": 3}
    assert positive_body(wrong_type) == {"a": "sentinel"}
    assert positive_body(out_of_range) == {"a": "sentinel"}


def test_additional_property_schema_can_be_a_local_reference() -> None:
    schema = additional_object({"$ref": "#/components/schemas/Count"})
    schemas = {"Count": {"type": "integer"}}

    accepted = additional_suite(schema, example={"a": "x", "n": 1}, schemas=schemas)
    rejected = additional_suite(schema, example={"a": "x", "n": "1"}, schemas=schemas)

    assert positive_body(accepted) == {"a": "x", "n": 1}
    assert positive_body(rejected) == {"a": "sentinel"}


def test_unusable_additional_property_schema_only_rejects_the_candidate() -> None:
    for unusable in ({"$ref": "#/components/schemas/Missing"}, {"type": "string", "pattern": "^(x)$"}):
        schema = additional_object(unusable)

        with_extra = additional_suite(schema, example={"a": "x", "extra": "x"})
        without_extra = additional_suite(schema, example={"a": "x"})

        assert positive_body(with_extra) == {"a": "sentinel"}
        assert positive_body(without_extra) == {"a": "x"}
        assert with_extra.diagnostics == ()


def test_additional_properties_are_validated_recursively() -> None:
    inner = {
        "type": "object",
        "required": ["k"],
        "properties": {"k": AP_STRING},
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "required": ["m"],
        "properties": {"m": {"type": "object", "additionalProperties": inner}},
    }

    accepted = additional_suite(schema, example={"m": {"first": {"k": "v"}}})
    rejected = additional_suite(schema, example={"m": {"first": {"k": "v", "extra": 1}}})
    wrong_inner = additional_suite(schema, example={"m": {"first": {"k": 1}}})

    assert positive_body(accepted) == {"m": {"first": {"k": "v"}}}
    assert positive_body(rejected) == {"m": {}}
    assert positive_body(wrong_inner) == {"m": {}}


def test_nested_declared_object_rules_apply_inside_an_example() -> None:
    schema = {
        "type": "object",
        "required": ["o"],
        "properties": {"o": additional_object(False)},
    }

    accepted = additional_suite(schema, example={"o": {"a": "x"}})
    rejected = additional_suite(schema, example={"o": {"a": "x", "extra": 1}})

    assert positive_body(accepted) == {"o": {"a": "x"}}
    assert positive_body(rejected) == {"o": {"a": "sentinel"}}


def test_required_property_without_a_schema_keeps_its_diagnostic_without_additional_properties() -> None:
    schema = {"type": "object", "required": ["a", "u"], "properties": {"a": AP_STRING}}

    suite = additional_suite(schema)

    assert positive_body(suite) == {"a": "sentinel", "u": None}
    assert [d.code for d in suite.diagnostics] == ["required_property_schema_missing"]


@pytest.mark.parametrize("additional", [False, {"type": "string"}])
def test_required_property_without_a_schema_is_not_invented_under_restrictive_additional_properties(
    additional: object,
) -> None:
    schema = {
        "type": "object",
        "required": ["a", "u"],
        "properties": {"a": AP_STRING},
        "additionalProperties": additional,
    }

    suite = additional_suite(schema)

    assert suite.cases == ()
    assert [d.code for d in suite.diagnostics] == ["no_positive_schema_value"]


def test_required_property_without_a_schema_is_kept_when_additional_properties_allow_null() -> None:
    for additional in (True, {"type": ["string", "null"]}, {}):
        suite = additional_suite(
            {
                "type": "object",
                "required": ["a", "u"],
                "properties": {"a": AP_STRING},
                "additionalProperties": additional,
            }
        )

        assert positive_body(suite) == {"a": "sentinel", "u": None}
        assert [d.code for d in suite.diagnostics] == ["required_property_schema_missing"]


@pytest.mark.parametrize("malformed", ["no", None, 1, ["x"], 0])
def test_malformed_additional_properties_is_an_unsupported_schema_form(malformed: object) -> None:
    suite = additional_suite(additional_object(malformed))

    assert suite.cases == ()
    assert [(d.code, d.source_pointer) for d in suite.diagnostics] == [
        (
            "unsupported_schema_form",
            "/paths/~1items/post/requestBody/content/application~1json/schema/additionalProperties",
        )
    ]


def test_additional_properties_is_not_a_negative_case_target() -> None:
    suite = additional_suite(additional_object(False))

    assert all(c.affected_property in (None, "/a") for c in suite.cases)
    assert {c.violated_constraint for c in suite.cases} == {
        None,
        "required_property",
        "type",
        "required_request_body",
    }


def test_additional_properties_on_a_primitive_schema_does_not_change_its_value() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string", "additionalProperties": False}},
    }

    suite = additional_suite(schema)

    assert positive_body(suite) == {"a": "sentinel"}


@pytest.mark.parametrize("version", AP_VERSIONS)
def test_additional_properties_generation_is_deterministic_and_does_not_mutate_input(version: str) -> None:
    schema = additional_object(
        {"$ref": "#/components/schemas/Count"},
        properties={"a": AP_STRING, "n": {"type": "integer", "maximum": 5}},
    )
    schemas = {"Count": {"type": "integer"}}
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=Schema(raw=schema), example={"a": "x", "extra": 2})},
        ),
        responses=BAD_REQUEST,
    )
    result = make_result(
        endpoint,
        version=version,
        components=Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()}),
    )
    before = result.model_dump_json()
    schema_before = deepcopy(schema)

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert positive_body(first) == {"a": "x", "extra": 2}
    assert result.model_dump_json() == before
    assert schema == schema_before


# --- additionalProperties: failure isolation and pointers -------------------

AP_UNSUPPORTED = {"type": "string", "pattern": "^(x)$"}
AP_ITEMS_PATH = "/paths/~1items/post/requestBody/content/application~1json/schema"


def additional_array_body(additional: object, **array: object) -> dict[str, object]:
    item = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": AP_STRING},
        "additionalProperties": additional,
    }
    return {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": AP_STRING,
            "n": {"type": "integer", "maximum": 3},
            "t": {"type": "array", "items": item, **array},
        },
    }


def test_array_candidate_with_an_unusable_additional_schema_is_rejected_without_aborting_other_steps() -> None:
    body = additional_array_body(AP_UNSUPPORTED, enum=[[{"a": "x", "z": "s"}]], minItems=2)

    suite = additional_suite(body)

    # Only the array candidate is rejected, with the ordinary "no safe candidate"
    # diagnostic at the violated keyword; no unsupported-schema diagnostic leaks.
    assert [(d.code, d.source_pointer) for d in suite.diagnostics] == [
        ("array_bound_no_safe_candidate", f"{AP_ITEMS_PATH}/properties/t/minItems")
    ]
    assert all("additionalProperties" not in (d.source_pointer or "") for d in suite.diagnostics)
    assert negative_summary(suite) == [
        ("required_property", "/a", {}),
        ("maximum", "/n", {"a": "sentinel", "n": 4}),
        ("type", "/a", {"a": 0}),
    ]


def test_array_candidate_with_a_usable_additional_schema_is_still_generated() -> None:
    body = additional_array_body({"type": "string"}, enum=[[{"a": "x", "z": "s"}]], minItems=2)

    suite = additional_suite(body)

    assert suite.diagnostics == ()
    assert ("minItems", "/t", {"a": "sentinel", "t": [{"a": "x", "z": "s"}]}) in negative_summary(suite)


def test_array_candidate_violating_the_additional_schema_is_rejected() -> None:
    body = additional_array_body({"type": "integer"}, enum=[[{"a": "x", "z": "s"}]], minItems=2)

    suite = additional_suite(body)

    assert [d.code for d in suite.diagnostics] == ["array_bound_no_safe_candidate"]
    assert [c for c, p, _ in negative_summary(suite) if p == "/t"] == []


def test_distinct_item_candidates_with_an_unusable_additional_schema_are_skipped_individually() -> None:
    item = additional_array_body(AP_UNSUPPORTED)["properties"]["t"]["items"]  # type: ignore[index]
    body = {
        "type": "object",
        "required": ["t"],
        "properties": {
            "t": {
                "type": "array",
                "uniqueItems": True,
                "minItems": 2,
                "items": {**item, "examples": [{"a": "x", "z": "s"}, {"a": "y"}]},
            }
        },
    }

    suite = additional_suite(body)

    assert positive_body(suite) == {"t": [{"a": "y"}, {"a": "sentinel"}]}


def test_required_undeclared_property_with_an_unusable_additional_schema_has_no_positive_value() -> None:
    other = make_endpoint("/other", "get")
    schema = {
        "type": "object",
        "required": ["a", "u"],
        "properties": {"a": AP_STRING},
        "additionalProperties": AP_UNSUPPORTED,
    }
    bad = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(schema),
        responses=BAD_REQUEST,
    )

    suite = generate_test_suite(make_result(bad, other))

    assert [(c.method, c.path) for c in suite.cases] == [("get", "/other")]
    assert [(d.code, d.source_pointer) for d in suite.diagnostics] == [
        ("no_positive_schema_value", AP_ITEMS_PATH)
    ]


def nested_additional_result(schemas: dict[str, object] | None, example: object) -> NormalizationResult:
    user = additional_object({"$ref": "#/components/schemas/Count"})
    body = {"type": "object", "required": ["user"], "properties": {"user": user}}
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=RequestBody(
            required=True,
            content={"application/json": MediaType(schema=Schema(raw=body), example=example)},
        ),
        responses=BAD_REQUEST,
    )
    components = Components(schemas={k: Schema(raw=v) for k, v in (schemas or {}).items()})
    return make_result(endpoint, components=components)


def test_nested_additional_schema_reference_keeps_its_source_pointer_in_the_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = nested_additional_result({"Count": {"type": "integer"}}, {"user": {"a": "x", "extra": 1}})
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 0)

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [(d.code, d.source_pointer, d.reference_uri) for d in suite.diagnostics] == [
        (
            "generation_expansion_budget_exceeded",
            f"{AP_ITEMS_PATH}/properties/user/additionalProperties",
            "#/components/schemas/Count",
        )
    ]


def test_additional_schema_reference_costs_one_expansion_per_validated_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = nested_additional_result({"Count": {"type": "integer"}}, {"user": {"a": "x", "extra": 1}})

    needed = count_expansions(result)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", needed)
    enough = generate_test_suite(result)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", needed - 1)
    short = generate_test_suite(result)

    assert needed == 1
    assert positive_body(enough) == {"user": {"a": "x", "extra": 1}}
    assert "generation_expansion_budget_exceeded" not in {d.code for d in enough.diagnostics}
    assert short.cases == ()
    assert [d.code for d in short.diagnostics] == ["generation_expansion_budget_exceeded"]


def test_unresolvable_nested_additional_schema_reference_rejects_only_the_candidate() -> None:
    suite = generate_test_suite(
        nested_additional_result(None, {"user": {"a": "x", "extra": 1}})
    )

    assert positive_body(suite) == {"user": {"a": "sentinel"}}
    assert suite.diagnostics == ()
    assert {c.affected_property for c in suite.cases if c.category == "negative"} >= {"/user/a"}


def test_additional_properties_isolation_is_deterministic_and_does_not_mutate_input() -> None:
    body = additional_array_body(AP_UNSUPPORTED, enum=[[{"a": "x", "z": "s"}]], minItems=2)
    body_before = deepcopy(body)
    endpoint = make_endpoint(
        "/items", "post", request_body=body_with_schema(body), responses=BAD_REQUEST
    )
    result = make_result(endpoint)
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before
    assert body == body_before


# --- string pattern (bounded subset, existing candidates only) -------------

PATTERN_ACCEPTED = [
    "", "^", "$", "^$", "a", "abc", "^abc$", "abc$", "^abc", r"\d", r"\w+", r"^\d{3}-\d{4}$",
    "[a-z]+", "[a-zA-Z0-9_]", "[-a]", "[a-]", "[-]", r"[\d\w]", r"[\-a]", r"[\.\]]", r"\.", r"\/",
    r"\\", r"\$", r"\^", r"^\^$", r"^\$", r"\\$", r"a\$", "a*", "a+", "a?", "a{3}", "a{2,}",
    "a{2,5}", "a{0}", "a{0,0}", "^[A-Z]{2}[0-9]{3}$", "a b", "^a+b*$", "x{2}y{3}z{4}", "a" * 128,
    "a{128}", "a{0,128}", "a{128,}", "[ -}]",
]
PATTERN_REJECTED = [
    "(a)", "(?i)a", "(?<n>a)", "a|b", ".", "a.b", "[^a]", "[]", "[a", "a]", "a}", "{", "a{",
    "a{,3}", "a{3,2}", "a{129}", "a{1,129}", "a{1,}}", "a{01}", "a{1 }", "a**", "a+?", "a*?",
    "a*+", "a{2}{3}", "a{2}*", "*a", "+", "?", "{3}", "^*", "^^", "a$b", "$a", "$$", "^$$",
    "\\", "a\\", "[a\\", "[\\", r"\b", r"\B", r"\1", r"\p{L}+", r"\n", r"\t", r"\x41", "\\" + "u0041",
    r"\-", "[z-a]", "[a--]", "[--a]", "[a-]]", r"[a-\d]", r"[\d-z]", r"[\--z]", r"[a-\-]", "[[a]",
    "[a&b]", "[a|b]", "[a~b]", "[a^b]", "[^]", "é", "a\tb", "a\nb", "a\x7f", "[é]", "[a-é]",
    "a*b*c*", "^a*b*c*$", "a*b*", "a+b?", "a{1,2}b{1,2}", "a" * 129, "a{999999999999999999999}",
    r"[\p]", r"[\q]",
]


def clear_pattern_scan_cache() -> None:
    generator_module._scan_pattern.cache_clear()


@pytest.mark.parametrize("pattern", PATTERN_ACCEPTED)
def test_supported_patterns_are_accepted_and_compile_cleanly(pattern: str) -> None:
    import re
    import warnings

    source, reason = generator_module._scan_pattern(pattern)

    assert source is not None and reason == ""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        re.compile(source, re.ASCII)


@pytest.mark.parametrize("pattern", PATTERN_REJECTED)
def test_unsupported_patterns_are_rejected_with_a_reason(pattern: str) -> None:
    source, reason = generator_module._scan_pattern(pattern)

    assert source is None
    assert reason


@pytest.mark.parametrize("value", [None, 5, 1.5, True, [], ["a"], {}, {"a": 1}, b"a", ("a",)])
def test_non_string_patterns_are_rejected_before_the_cache(value: object) -> None:
    assert generator_module._pattern_source(value) == (None, "the pattern is not a string")


def test_scan_translates_anchors_and_keeps_escaped_anchors_literal() -> None:
    scan = generator_module._scan_pattern

    assert scan("^a$") == ("\\Aa\\Z", "")
    assert scan("a") == ("a", "")
    assert scan("") == ("", "")
    assert scan("^") == ("\\A", "")
    assert scan("$") == ("\\Z", "")
    assert scan(r"\^a") == (r"\^a", "")
    assert scan(r"^\$") == (r"\A\$", "")
    assert scan(r"\\$") == (r"\\\Z", "")
    assert scan("[a-c]{2,3}") == ("[a-c]{2,3}", "")


def test_scanner_is_pure_and_its_cache_is_bounded() -> None:
    info = generator_module._scan_pattern.cache_info()

    assert info.maxsize == 256
    assert generator_module._scan_pattern("^(a)$") == generator_module._scan_pattern("^(a)$")
    assert generator_module._scan_pattern("^a$") == ("\\Aa\\Z", "")


def test_scanner_never_fails_unexpectedly_on_every_short_input() -> None:
    import itertools

    alphabet = ["^", "$", "\\", "[", "]", "-", "a", "0", "d", "w", "{", "}", "1", ",", "*", "+", "?", "."]
    scan = generator_module._scan_pattern_source
    rejected = generator_module._PatternRejected
    accepted = 0
    total = 0
    for length in range(5):
        for chars in itertools.product(alphabet, repeat=length):
            pattern = "".join(chars)
            total += 1
            try:
                source = scan(pattern)
            except rejected:
                continue
            assert isinstance(source, str)
            accepted += 1
    assert total == sum(len(alphabet) ** n for n in range(5))
    assert accepted > 1000


def test_every_accepted_short_pattern_compiles_without_warnings() -> None:
    import itertools
    import re
    import warnings

    alphabet = ["^", "$", "\\", "[", "]", "-", "a", "0", "d", "w", "{", "}", "1", ",", "*", "+", "?"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for length in range(5):
            for chars in itertools.product(alphabet, repeat=length):
                source, _ = generator_module._scan_pattern("".join(chars))
                if source is not None:
                    re.compile(source, re.ASCII)


def test_scanner_handles_truncated_and_random_inputs_without_unexpected_errors() -> None:
    import random

    samples = [r"^[a-z]{2,5}\d+$", r"[\w\-.]+", r"^\$a{3}[-x]$", r"\\\^\/"]
    for sample in samples:
        for end in range(len(sample) + 1):
            generator_module._scan_pattern(sample[:end])
            generator_module._scan_pattern(sample[end:])
    rng = random.Random(8_20)
    alphabet = "^$\\[]-a0dw{}1,*+?.()|é\n"
    for _ in range(5_000):
        pattern = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 140)))
        source, reason = generator_module._scan_pattern(pattern)
        assert (source is None) == bool(reason)


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("^a$", "a", True),
        ("^a$", "ab", False),
        ("^a$", "ba", False),
        ("^a$", "a\n", False),
        ("a$", "ba", True),
        ("a$", "ba\n", False),
        ("^a", "ab", True),
        ("^a", "ba", False),
        ("a", "xax", True),
        ("a", "xyz", False),
        ("", "anything", True),
        ("", "", True),
        ("^$", "", True),
        ("^$", "\n", False),
        (r"^\d+$", "123", True),
        (r"^\d+$", "٣", False),
        (r"^\w+$", "ab_9", True),
        (r"^\w+$", "café", False),
        (r"^[a-c]+$", "abc", True),
        (r"^[a-c]+$", "abd", False),
        (r"^[-a]+$", "-a-", True),
        (r"^\$a$", "$a", True),
        (r"^\^$", "^", True),
        (r"^a{2,3}$", "aaa", True),
        (r"^a{2,3}$", "aaaa", False),
        (r"^a{2}$", "aa", True),
    ],
)
def test_pattern_matching_semantics(pattern: str, value: str, expected: bool) -> None:
    assert generator_module._pattern_accepts(pattern, value, None) is expected


def test_pattern_candidate_length_boundary_is_128_code_points() -> None:
    accepts = generator_module._pattern_accepts

    assert accepts("^a+$", "a" * 128, None) is True
    assert accepts("^a+$", "a" * 129, None) is False
    assert accepts("", "a" * 129, None) is False
    assert accepts("^é$", "x", None) is False


def test_unsupported_patterns_never_accept_a_candidate() -> None:
    for pattern in ("(a)", "(?i)a", "a{,3}", 5, None):
        assert generator_module._pattern_accepts(pattern, "a", None) is False


def test_pattern_applies_only_to_string_candidates() -> None:
    schema = {"pattern": "^a$"}

    assert generator_module._matches(schema, 5, "3.1.1", "request", 0) is True
    assert generator_module._matches(schema, ["x"], "3.1.1", "request", 0) is True
    assert generator_module._matches(schema, "a", "3.1.1", "request", 0) is True
    assert generator_module._matches(schema, "b", "3.1.1", "request", 0) is False
    assert generator_module._matches({"type": "string", "pattern": "^a$"}, None, "3.1.1", "request", 0) is False


def pattern_object(prop: dict[str, object], **extra: object) -> dict[str, object]:
    return {"type": "object", "required": ["a"], "properties": {"a": prop}, **extra}


def pattern_codes(suite: GeneratedTestSuite) -> list[str]:
    return [item.code for item in suite.diagnostics]


@pytest.mark.parametrize("version", AP_VERSIONS)
def test_pattern_fallback_value_must_satisfy_the_pattern(version: str) -> None:
    ok = additional_suite(pattern_object({"type": "string", "pattern": "^sentinel$"}), version=version)
    none = additional_suite(pattern_object({"type": "string", "pattern": "^[0-9]+$"}), version=version)

    assert positive_body(ok) == {"a": "sentinel"}
    assert ok.diagnostics == ()
    assert none.cases == ()
    assert pattern_codes(none) == ["no_positive_schema_value"]


def test_pattern_fallbacks_include_the_empty_and_min_length_values() -> None:
    empty = additional_suite(pattern_object({"type": "string", "pattern": "^$", "maxLength": 0}))
    padded = additional_suite(pattern_object({"type": "string", "pattern": "^x{3}$", "minLength": 3}))

    assert positive_body(empty) == {"a": ""}
    assert positive_body(padded) == {"a": "xxx"}


def test_pattern_filters_examples_defaults_enum_and_const_in_order() -> None:
    example = additional_suite(
        pattern_object({"type": "string", "pattern": "^[0-9]{3}$", "example": "123"})
    )
    skipped = additional_suite(
        pattern_object(
            {"type": "string", "pattern": "^[0-9]{3}$", "example": "zzz", "default": "456"}
        )
    )
    in_list = additional_suite(
        pattern_object({"type": "string", "pattern": "^[0-9]{3}$", "examples": ["a", "789", "123"]})
    )
    enum = additional_suite(
        pattern_object({"type": "string", "pattern": "^[a-c]$", "enum": ["x", "b", "c"]})
    )
    const_ok = additional_suite(pattern_object({"type": "string", "pattern": "^b$", "const": "b"}))
    const_bad = additional_suite(pattern_object({"type": "string", "pattern": "^c$", "const": "b"}))

    assert positive_body(example) == {"a": "123"}
    assert positive_body(skipped) == {"a": "456"}
    assert positive_body(in_list) == {"a": "789"}
    assert positive_body(enum) == {"a": "b"}
    assert positive_body(const_ok) == {"a": "b"}
    assert const_bad.cases == ()
    assert pattern_codes(const_bad) == ["no_positive_schema_value"]


def test_pattern_string_boundary_candidates_must_satisfy_the_pattern() -> None:
    fits = additional_suite(
        pattern_object({"type": "string", "pattern": "^x+$", "minLength": 2, "maxLength": 3})
    )
    misfit = additional_suite(
        pattern_object(
            {"type": "string", "pattern": "^[0-9]+$", "maxLength": 3, "example": "123"}
        )
    )

    assert ("maxLength", "/a", {"a": "xxxx"}) in negative_summary(fits)
    assert ("minLength", "/a", {"a": "x"}) in negative_summary(fits)
    assert ("maxLength", "/a", {"a": "xxxx"}) not in negative_summary(misfit)
    assert "string_boundary_no_safe_candidate" in pattern_codes(misfit)


def test_pattern_negative_candidates_still_satisfy_the_pattern() -> None:
    suite = additional_suite(
        pattern_object({"type": "string", "pattern": "^[a-c]$", "enum": ["a", "b", "c"]})
    )
    for case in suite.cases:
        if case.request_body is None or not isinstance(case.request_body.value, dict):
            continue
        value = case.request_body.value.get("a")
        if isinstance(value, str):
            assert generator_module._pattern_accepts("^[a-c]$", value, None)


def test_pattern_on_array_items_applies_to_every_item_candidate() -> None:
    schema = {
        "type": "object",
        "required": ["t"],
        "properties": {
            "t": {
                "type": "array", "minItems": 1, "maxItems": 2,
                "items": {"type": "string", "pattern": "^[0-9]+$", "example": "12"},
            }
        },
    }
    suite = additional_suite(schema)
    bad = additional_suite(
        {**schema, "properties": {"t": {**schema["properties"]["t"], "items": {  # type: ignore[index]
            "type": "string", "pattern": "^[0-9]+$"}}}}
    )

    assert positive_body(suite) == {"t": ["12"]}
    assert ("maxItems", "/t", {"t": ["12", "12", "12"]}) in negative_summary(suite)
    assert bad.cases == ()
    assert "no_positive_schema_value" in pattern_codes(bad)


def test_pattern_in_a_local_reference_is_enforced() -> None:
    schemas = {"P": {"type": "string", "pattern": "^[a-z]+$", "example": "abc"}}
    bad = {"P": {"type": "string", "pattern": "^[0-9]+$"}}
    prop = {"$ref": "#/components/schemas/P"}

    assert positive_body(additional_suite(pattern_object(prop), schemas=schemas)) == {"a": "abc"}
    failing = additional_suite(pattern_object(prop), schemas=bad)
    assert failing.cases == ()
    assert pattern_codes(failing) == ["no_positive_schema_value"]
    unsupported = additional_suite(
        pattern_object(prop), schemas={"P": {"type": "string", "pattern": "^(a)$"}}
    )
    assert pattern_codes(unsupported) == ["unsupported_schema_keyword"]
    assert unsupported.diagnostics[0].source_pointer == "/components/schemas/P/pattern"


def test_pattern_combines_with_format() -> None:
    ok = additional_suite(
        pattern_object({"type": "string", "format": "date", "pattern": "^2024-", "example": "2024-01-02"})
    )
    mismatch = additional_suite(
        pattern_object({"type": "string", "format": "date", "pattern": "^2023-", "example": "2024-01-02"})
    )

    assert positive_body(ok) == {"a": "2024-01-02"}
    assert mismatch.cases == ()
    assert pattern_codes(mismatch) == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    ("pattern", "code", "pointer_tail"),
    [
        ("^(a)$", "unsupported_schema_keyword", "/pattern"),
        ("(?i)a", "unsupported_schema_keyword", "/pattern"),
        ("a{,3}", "unsupported_schema_keyword", "/pattern"),
        (r"\p{L}+", "unsupported_schema_keyword", "/pattern"),
        ("(?<n>a)", "unsupported_schema_keyword", "/pattern"),
        ("a" * 129, "unsupported_schema_keyword", "/pattern"),
        (5, "unsupported_schema_form", "/pattern"),
        (None, "unsupported_schema_form", "/pattern"),
        (["a"], "unsupported_schema_form", "/pattern"),
    ],
)
def test_unsupported_pattern_gets_a_diagnostic_at_the_keyword(
    pattern: object, code: str, pointer_tail: str
) -> None:
    suite = additional_suite(pattern_object({"type": "string", "pattern": pattern}))

    assert suite.cases == ()
    assert pattern_codes(suite) == [code]
    assert suite.diagnostics[0].source_pointer.endswith("/properties/a" + pointer_tail)  # type: ignore[union-attr]
    assert "pattern" in suite.diagnostics[0].message


def test_pattern_on_an_optional_property_keeps_the_property_omitted() -> None:
    for pattern in ("^[0-9]+$", "^(a)$"):
        suite = additional_suite(
            {"type": "object", "properties": {"o": {"type": "string", "pattern": pattern, "example": "7"}}}
        )

        assert positive_body(suite) == {}
        assert suite.diagnostics == ()


def pattern_parameter_suite(schema: dict[str, object], *, required: bool) -> GeneratedTestSuite:
    parameter = Parameter(
        name="code", location="query", required=required, schema=Schema(raw=schema),
        source_scope="operation",
    )
    return generate_test_suite(
        make_result(make_endpoint("/items", "get", parameters=(parameter,), responses=BAD_REQUEST))
    )


def test_pattern_on_parameters_filters_candidates() -> None:
    ok = pattern_parameter_suite(
        {"type": "string", "pattern": "^[0-9]{3}$", "example": "123"}, required=True
    )
    bad = pattern_parameter_suite({"type": "string", "pattern": "^[0-9]{3}$"}, required=True)
    unsupported = pattern_parameter_suite({"type": "string", "pattern": "^(a)$"}, required=True)

    assert [(p.name, p.value) for p in ok.cases[0].parameters] == [("code", "123")]
    assert bad.cases == ()
    assert pattern_codes(bad) == ["no_positive_schema_value"]
    assert pattern_codes(unsupported) == ["unsupported_schema_keyword"]
    assert unsupported.diagnostics[0].source_pointer.endswith("/schema/pattern")  # type: ignore[union-attr]


def test_pattern_on_an_optional_parameter_keeps_it_omitted_and_reports_unsupported_patterns() -> None:
    supported = pattern_parameter_suite(
        {"type": "string", "pattern": "^[0-9]{3}$", "example": "123"}, required=False
    )
    unsupported = pattern_parameter_suite({"type": "string", "pattern": "^(a)$"}, required=False)

    assert supported.cases[0].parameters == () and supported.diagnostics == ()
    assert unsupported.cases[0].parameters == ()
    assert [(d.code, d.source_pointer) for d in unsupported.diagnostics] == [
        ("optional_parameter_unsupported_schema", "/paths/~1items/get/parameters/0/schema/pattern")
    ]


def pattern_result(patterns: list[str], values: list[str]) -> NormalizationResult:
    endpoints = tuple(
        make_endpoint(
            f"/items{index}",
            "post",
            request_body=RequestBody(
                required=True,
                content={
                    "application/json": MediaType(
                        schema=Schema(
                            raw=pattern_object(
                                {"type": "string", "pattern": pattern, "enum": values}
                            )
                        )
                    )
                },
            ),
            responses=BAD_REQUEST,
        )
        for index, pattern in enumerate(patterns)
    )
    return make_result(*endpoints)


def count_pattern_evaluations(result: NormalizationResult) -> int:
    counter = {"count": 0}
    original = generator_module._GenerationBudget.charge_pattern_evaluation

    def counting(self: object, pointer: object, work: int) -> None:
        counter["count"] += 1
        original(self, pointer, work)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module._GenerationBudget, "charge_pattern_evaluation", counting)
        generate_test_suite(result)
    return counter["count"]


def with_budget() -> tuple[object, "generator_module._GenerationBudget"]:
    budget = generator_module._GenerationBudget()
    return generator_module._BUDGET.set(budget), budget


def test_pattern_budget_constants_and_code() -> None:
    assert generator_module._MAX_ENDPOINT_PATTERN_EVALUATIONS == 2_000
    assert generator_module._MAX_SUITE_PATTERN_EVALUATIONS == 20_000
    assert generator_module._PATTERN_BUDGET_CODE == "generation_pattern_budget_exceeded"


def test_identical_pattern_candidate_pairs_are_charged_once_and_distinct_pairs_separately() -> None:
    token, budget = with_budget()
    try:
        accepts = generator_module._pattern_accepts
        assert accepts("^a$", "a", None) is True
        assert accepts("^a$", "a", None) is True
        assert budget.suite_pattern_evaluations == 1
        assert accepts("^a$", "b", None) is False
        assert accepts("^b$", "a", None) is False
        assert accepts("^b$", "b", None) is True
        assert budget.suite_pattern_evaluations == 4
        assert budget.endpoint_pattern_evaluations == 4
        assert accepts("^b$", "b", None) is True
        assert budget.suite_pattern_evaluations == 4
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_rejected_patterns_and_long_candidates_are_not_charged() -> None:
    token, budget = with_budget()
    try:
        accepts = generator_module._pattern_accepts
        assert accepts("(a)", "a", None) is False
        assert accepts(5, "a", None) is False
        assert accepts("^a+$", "a" * 129, None) is False
        assert budget.suite_pattern_evaluations == 0
        assert budget.pattern_memo == {}
        assert accepts("^a+$", "a" * 128, None) is True
        assert budget.suite_pattern_evaluations == 1
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_pattern_evaluation_without_a_budget_is_unlimited_uncharged_and_unmemoized() -> None:
    assert generator_module._BUDGET.get() is None
    for index in range(generator_module._MAX_ENDPOINT_PATTERN_EVALUATIONS + 5):
        assert generator_module._pattern_accepts("^a+$", "a" * (index % 100 + 1), None) is True
    assert generator_module._matches({"type": "string", "pattern": "^a$"}, "a", "3.1.1", "request", 0)
    assert not [name for name in vars(generator_module) if "memo" in name.lower()]


def test_endpoint_pattern_limit_stops_the_step_with_a_deterministic_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", 3)
    result = pattern_result(["^ok$"], ["a1", "a2", "a3", "a4", "ok"])

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["generation_pattern_budget_exceeded"]
    diagnostic = suite.diagnostics[0]
    assert diagnostic.endpoint_path == "/items0"
    assert "per-endpoint limit of 3 pattern evaluations" in diagnostic.message
    assert "'positive_baseline' step" in diagnostic.message
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/a/pattern")
    assert generate_test_suite(result) == suite


def test_suite_pattern_limit_stops_the_remaining_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = ["no", "ok1", "ok2", "ok3"]
    patterns = ["^ok1$", "^ok2$", "^ok3$"]
    one = count_pattern_evaluations(pattern_result(patterns[:1], values))
    assert one > 0
    monkeypatch.setattr(generator_module, "_MAX_SUITE_PATTERN_EVALUATIONS", one)

    suite = generate_test_suite(pattern_result(patterns, values))

    assert {case.path for case in suite.cases} == {"/items0"}
    budget = [item for item in suite.diagnostics if item.code == "generation_pattern_budget_exceeded"]
    assert len(budget) == 1
    assert budget[0].endpoint_path == "/items1"
    assert f"per-suite limit of {one} pattern evaluations" in budget[0].message
    assert "'positive_baseline' step" in budget[0].message
    assert "1 following endpoint(s)" in budget[0].message
    assert generate_test_suite(pattern_result(patterns, values)) == suite


def test_identical_pairs_are_charged_once_across_endpoints_in_one_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = ["no", "ok"]
    single = count_pattern_evaluations(pattern_result(["^ok$"], values))
    triple = count_pattern_evaluations(pattern_result(["^ok$"] * 3, values))

    assert single > 0
    assert triple == single
    monkeypatch.setattr(generator_module, "_MAX_SUITE_PATTERN_EVALUATIONS", single)
    suite = generate_test_suite(pattern_result(["^ok$"] * 3, values))
    assert {case.path for case in suite.cases} == {"/items0", "/items1", "/items2"}
    assert "generation_pattern_budget_exceeded" not in pattern_codes(suite)


def test_dropped_step_does_not_refund_pattern_evaluations() -> None:
    token, budget = with_budget()
    try:
        def step() -> None:
            generator_module._pattern_accepts("^a$", "a", None)
            generator_module._pattern_accepts("^a$", "b", None)
            raise generator_module._BudgetExhausted("generation_case_budget_exceeded", "endpoint", 1, None, None)

        _, exhausted = generator_module._run_step(budget, step)  # type: ignore[arg-type]

        assert exhausted is not None
        assert budget.endpoint_pattern_evaluations == 2
        assert budget.suite_pattern_evaluations == 2
        assert len(budget.pattern_memo) == 2

        def failing() -> None:
            generator_module._pattern_accepts("^a$", "c", None)
            raise generator_module._UnsupportedOperation("unsupported_schema_keyword", "x", "/p")

        with pytest.raises(generator_module._UnsupportedOperation):
            generator_module._run_step(budget, failing)  # type: ignore[arg-type]
        assert budget.suite_pattern_evaluations == 3
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_budget_exhaustion_inside_matches_is_not_swallowed_by_additional_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", 1)
    token, budget = with_budget()
    try:
        schema = {"type": "object", "additionalProperties": {"type": "string", "pattern": "^a$"}}
        with pytest.raises(generator_module._BudgetExhausted) as raised:
            generator_module._matches(schema, {"p": "a", "q": "b"}, "3.1.1", "request", 0)
        assert raised.value.code == "generation_pattern_budget_exceeded"
        assert raised.value.scope == "endpoint"
        assert raised.value.limit == 1
        assert budget.endpoint_pattern_evaluations == 2
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_budgets_do_not_leak_between_generation_runs() -> None:
    result = pattern_result(["^ok$"], ["no", "ok"])
    first = count_pattern_evaluations(result)
    second = count_pattern_evaluations(result)

    assert first == second > 0
    assert generator_module._BUDGET.get() is None


def test_pattern_generation_is_deterministic_and_does_not_mutate_the_input() -> None:
    result = pattern_result(["^ok$", "^[a-z]+$"], ["no", "ok"])
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


def test_scanner_cache_clearing_or_eviction_does_not_change_output_or_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = pattern_result(["^ok$", "^[a-z]+$", "ok", "^o{1,2}k$"], ["no", "ok"])
    clear_pattern_scan_cache()
    warm = generate_test_suite(result)
    warm_count = count_pattern_evaluations(result)
    clear_pattern_scan_cache()
    cold = generate_test_suite(result)
    cold_count = count_pattern_evaluations(result)

    tiny = lru_cache_one()
    monkeypatch.setattr(generator_module, "_scan_pattern", tiny)
    evicting = generate_test_suite(result)
    evicting_count = count_pattern_evaluations(result)

    assert warm == cold == evicting
    assert warm.model_dump_json() == evicting.model_dump_json()
    assert warm_count == cold_count == evicting_count > 0


def lru_cache_one():  # type: ignore[no-untyped-def]
    from functools import lru_cache

    return lru_cache(maxsize=1)(generator_module._scan_pattern.__wrapped__)


def count_pattern_compilations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    compiled: list[str] = []
    original = generator_module.re.compile

    def counting(source: str, *args: object, **kwargs: object) -> object:
        compiled.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(generator_module.re, "compile", counting)
    return compiled


def test_repeated_pattern_pair_is_compiled_once_and_cached_results_skip_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, budget = with_budget()
    try:
        compiled = count_pattern_compilations(monkeypatch)
        accepts = generator_module._pattern_accepts

        assert [accepts("^a$", "a", None) for _ in range(5)] == [True] * 5
        assert compiled == ["\\Aa\\Z"]
        assert accepts("^a$", "b", None) is False
        assert accepts("^b$", "a", None) is False
        assert compiled == ["\\Aa\\Z", "\\Aa\\Z", "\\Ab\\Z"]
        assert accepts("^a$", "b", None) is False
        assert accepts("^b$", "a", None) is False
        assert len(compiled) == 3
        assert budget.suite_pattern_evaluations == 3
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_pair_over_the_evaluation_budget_is_not_compiled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", 1)
    token, budget = with_budget()
    try:
        compiled = count_pattern_compilations(monkeypatch)
        assert generator_module._pattern_accepts("^a$", "a", None) is True
        assert len(compiled) == 1

        with pytest.raises(generator_module._BudgetExhausted):
            generator_module._pattern_accepts("^b$", "b", None)

        assert len(compiled) == 1
        assert ("^b$", "b") not in budget.pattern_memo
        assert generator_module._pattern_accepts("^a$", "a", None) is True
        assert len(compiled) == 1
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_pattern_evaluation_without_a_budget_still_evaluates_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = count_pattern_compilations(monkeypatch)

    assert generator_module._BUDGET.get() is None
    assert generator_module._pattern_accepts("^a$", "a", None) is True
    assert generator_module._pattern_accepts("^a$", "a", None) is True
    assert generator_module._pattern_accepts("^a$", "b", None) is False
    assert len(compiled) == 3


def test_unsupported_pattern_and_long_candidate_are_not_compiled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, budget = with_budget()
    try:
        compiled = count_pattern_compilations(monkeypatch)
        assert generator_module._pattern_accepts("(a)", "a", None) is False
        assert generator_module._pattern_accepts("^a+$", "a" * 129, None) is False
        assert compiled == []
        assert budget.suite_pattern_evaluations == 0
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


# --- weighted pattern work budget -------------------------------------------

# Long, quantified and never matching candidates that start with "-": the work
# model charges by length only, so these inputs make the accounting deterministic.
SLOW_PATTERN = (
    r"^\wa\wa+a\w[ab]\waa\w{3}\w[ab]\wa\wa\w[ab]\waa\wa\wa\w[ab]\waa\wa\wa\w[ab]\waa"
)


def charged_pattern_work(result: NormalizationResult) -> list[int]:
    charged: list[int] = []
    original = generator_module._GenerationBudget.charge_pattern_evaluation

    def recording(self: object, pointer: object, work: int) -> None:
        charged.append(work)
        original(self, pointer, work)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module._GenerationBudget, "charge_pattern_evaluation", recording)
        generate_test_suite(result)
    return charged


def test_pattern_work_cost_function_values() -> None:
    work = generator_module._pattern_work

    assert work("", "") == 33
    assert work("^a$", "a") == 35
    assert work("a" * 128, "a" * 128) == 5_297
    assert work("a" * 128, "") == 32 + 64 + (1 * 160 + 511) // 512
    assert work("[0-9]{3}", "123") == 32 + 4 + (16 * 40 + 511) // 512


def test_pattern_work_depends_only_on_the_two_lengths_and_grows_with_both() -> None:
    work = generator_module._pattern_work

    assert work("^[a-z]+$", "abcdef") == work("^[0-9]+$", "zzzzzz")
    previous = 0
    for length in range(0, 129):
        current = work("a" * 16, "a" * length)
        assert current >= previous
        previous = current
    previous = 0
    for length in range(0, 129):
        current = work("a" * length, "a" * 16)
        assert current >= previous
        previous = current
    assert work("a" * 17, "a" * 16) > work("a" * 16, "a" * 16)
    assert work("a" * 16, "a" * 17) > work("a" * 16, "a" * 16)


def test_pattern_work_limits_are_ordered_and_admit_a_useful_number_of_worst_case_pairs() -> None:
    worst = generator_module._pattern_work("a" * 128, "a" * 128)

    assert generator_module._MAX_ENDPOINT_PATTERN_WORK == 500_000
    assert generator_module._MAX_SUITE_PATTERN_WORK == 4_000_000
    assert generator_module._PATTERN_WORK_UNIT == "pattern work units"
    assert generator_module._MAX_ENDPOINT_PATTERN_WORK < generator_module._MAX_SUITE_PATTERN_WORK
    assert generator_module._MAX_ENDPOINT_PATTERN_WORK // worst >= 90
    assert generator_module._MAX_SUITE_PATTERN_WORK // worst >= 700


def test_pattern_work_is_charged_once_per_distinct_pair_and_memo_hits_are_free() -> None:
    token, budget = with_budget()
    try:
        accepts = generator_module._pattern_accepts
        work = generator_module._pattern_work

        assert accepts("^a$", "a", None) is True
        assert budget.endpoint_pattern_work == budget.suite_pattern_work == work("^a$", "a")
        for _ in range(3):
            assert accepts("^a$", "a", None) is True
        assert budget.suite_pattern_work == work("^a$", "a")
        assert accepts("^a$", "bb", None) is False
        assert budget.suite_pattern_work == work("^a$", "a") + work("^a$", "bb")
        assert budget.suite_pattern_evaluations == 2
        budget.begin_endpoint()
        assert budget.endpoint_pattern_work == 0
        assert accepts("^a$", "a", None) is True
        assert budget.endpoint_pattern_work == 0
        assert budget.suite_pattern_work == work("^a$", "a") + work("^a$", "bb")
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_pair_over_the_work_limit_is_charged_but_not_compiled_or_memoized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = generator_module._pattern_work
    monkeypatch.setattr(
        generator_module, "_MAX_ENDPOINT_PATTERN_WORK", work("^a$", "a") + work("^b$", "b") - 1
    )
    token, budget = with_budget()
    try:
        compiled = count_pattern_compilations(monkeypatch)
        assert generator_module._pattern_accepts("^a$", "a", None) is True
        assert len(compiled) == 1

        with pytest.raises(generator_module._BudgetExhausted) as raised:
            generator_module._pattern_accepts("^b$", "b", None)

        assert len(compiled) == 1
        assert ("^b$", "b") not in budget.pattern_memo
        assert raised.value.code == "generation_pattern_budget_exceeded"
        assert raised.value.scope == "endpoint"
        assert raised.value.limit == generator_module._MAX_ENDPOINT_PATTERN_WORK
        assert raised.value.unit == "pattern work units"
        assert budget.suite_pattern_work == work("^a$", "a") + work("^b$", "b")
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_suite_work_limit_raises_with_suite_scope_after_the_endpoint_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = generator_module._pattern_work("^a$", "a")
    monkeypatch.setattr(generator_module, "_MAX_SUITE_PATTERN_WORK", work)
    token, budget = with_budget()
    try:
        assert generator_module._pattern_accepts("^a$", "a", None) is True
        budget.begin_endpoint()
        with pytest.raises(generator_module._BudgetExhausted) as raised:
            generator_module._pattern_accepts("^b$", "b", None)
        assert (raised.value.scope, raised.value.limit, raised.value.unit) == (
            "suite", work, "pattern work units",
        )
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_evaluation_count_limit_keeps_its_own_message_when_work_is_plentiful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", 2)
    suite = generate_test_suite(pattern_result(["^ok$"], ["a1", "a2", "a3", "ok"]))

    assert [item.code for item in suite.diagnostics] == ["generation_pattern_budget_exceeded"]
    assert "per-endpoint limit of 2 pattern evaluations" in suite.diagnostics[0].message
    assert "pattern work units" not in suite.diagnostics[0].message


def test_endpoint_work_limit_stops_the_step_with_a_work_unit_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = pattern_result(["^ok$"], ["a1", "a2", "a3", "a4", "ok"])
    used = charged_pattern_work(result)
    assert len(used) >= 4
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_WORK", sum(used[:3]))

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["generation_pattern_budget_exceeded"]
    message = suite.diagnostics[0].message
    assert f"per-endpoint limit of {sum(used[:3])} pattern work units" in message
    assert "'positive_baseline' step" in message
    assert suite.diagnostics[0].source_pointer.endswith("/properties/a/pattern")  # type: ignore[union-attr]
    assert generate_test_suite(result) == suite


def test_suite_work_limit_stops_the_remaining_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    values = ["no", "ok1", "ok2", "ok3"]
    patterns = ["^ok1$", "^ok2$", "^ok3$"]
    one = sum(charged_pattern_work(pattern_result(patterns[:1], values)))
    monkeypatch.setattr(generator_module, "_MAX_SUITE_PATTERN_WORK", one)

    suite = generate_test_suite(pattern_result(patterns, values))

    assert {case.path for case in suite.cases} == {"/items0"}
    budget = [item for item in suite.diagnostics if item.code == "generation_pattern_budget_exceeded"]
    assert len(budget) == 1 and budget[0].endpoint_path == "/items1"
    assert f"per-suite limit of {one} pattern work units" in budget[0].message
    assert "1 following endpoint(s)" in budget[0].message
    assert generate_test_suite(pattern_result(patterns, values)) == suite


def test_work_accounting_is_identical_across_runs_and_cache_states() -> None:
    result = pattern_result(["^ok$", "^[a-z]+$", "ok"], ["no", "ok"])
    clear_pattern_scan_cache()
    first = charged_pattern_work(result)
    second = charged_pattern_work(result)
    clear_pattern_scan_cache()
    third = charged_pattern_work(result)

    assert first == second == third
    assert first and all(isinstance(item, int) and item > 0 for item in first)


def test_endpoint_pattern_counters_accumulate_across_steps_and_reset_only_between_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget_class = generator_module._GenerationBudget
    original_begin = budget_class.begin_endpoint
    original_charge = budget_class.charge_pattern_evaluation
    original_run_step = generator_module._run_step
    state = {"begins": 0, "steps": 0}
    rows: list[dict[str, int]] = []

    def begin(self: object) -> None:
        state["begins"] += 1
        original_begin(self)  # type: ignore[arg-type]

    def run_step(budget: object, step: object) -> object:
        state["steps"] += 1
        return original_run_step(budget, step)  # type: ignore[arg-type]

    def charge(self: object, pointer: object, work: int) -> None:
        original_charge(self, pointer, work)  # type: ignore[arg-type]
        rows.append(
            {
                "begins": state["begins"],
                "step": state["steps"],
                "work": work,
                "endpoint_evaluations": self.endpoint_pattern_evaluations,  # type: ignore[attr-defined]
                "endpoint_work": self.endpoint_pattern_work,  # type: ignore[attr-defined]
                "suite_evaluations": self.suite_pattern_evaluations,  # type: ignore[attr-defined]
                "suite_work": self.suite_pattern_work,  # type: ignore[attr-defined]
            }
        )

    monkeypatch.setattr(budget_class, "begin_endpoint", begin)
    monkeypatch.setattr(budget_class, "charge_pattern_evaluation", charge)
    monkeypatch.setattr(generator_module, "_run_step", run_step)

    generate_test_suite(pattern_result(["^ok$", "^no$"], ["no", "ok", "oops"]))

    # begin_endpoint runs once in the constructor and once per endpoint.
    assert state["begins"] == 3
    groups = [[row for row in rows if row["begins"] == number] for number in (2, 3)]
    assert all(groups)
    total_work = 0
    total_evaluations = 0
    for group in groups:
        # The pairs are charged by more than one step of the same endpoint.
        assert len({row["step"] for row in group}) >= 2
        running_work = 0
        for position, row in enumerate(group, start=1):
            running_work += row["work"]
            total_work += row["work"]
            total_evaluations += 1
            assert row["endpoint_evaluations"] == position
            assert row["endpoint_work"] == running_work
            assert row["suite_evaluations"] == total_evaluations
            assert row["suite_work"] == total_work
    assert groups[1][0]["endpoint_evaluations"] == 1
    assert groups[1][0]["endpoint_work"] == groups[1][0]["work"]


def adversarial_result(endpoints: int, per_endpoint: int) -> NormalizationResult:
    assert generator_module._scan_pattern(SLOW_PATTERN)[0] is not None
    return make_result(
        *(
            make_endpoint(
                f"/items{index}",
                "post",
                request_body=RequestBody(
                    required=True,
                    content={
                        "application/json": MediaType(
                            schema=Schema(
                                raw=pattern_object(
                                    {
                                        "type": "string",
                                        "pattern": SLOW_PATTERN,
                                        "enum": [
                                            "-" + "a" * 121 + f"{index * per_endpoint + item:06d}"
                                            for item in range(per_endpoint)
                                        ],
                                    }
                                )
                            )
                        )
                    },
                ),
                responses=BAD_REQUEST,
            )
            for index in range(endpoints)
        )
    )


def test_large_adversarial_pattern_input_is_stopped_by_the_work_budget() -> None:
    result = adversarial_result(13, 2_000)
    worst = generator_module._pattern_work(SLOW_PATTERN, "-" + "a" * 127)

    used = charged_pattern_work(result)
    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert sum(used) <= generator_module._MAX_SUITE_PATTERN_WORK + worst
    assert len(used) <= generator_module._MAX_SUITE_PATTERN_WORK // worst + 2
    assert len(used) < generator_module._MAX_SUITE_PATTERN_EVALUATIONS // 10
    codes = {item.code for item in suite.diagnostics}
    assert "generation_pattern_budget_exceeded" in codes
    suite_messages = [
        item.message for item in suite.diagnostics if "per-suite limit" in item.message
    ]
    assert suite_messages and "pattern work units" in suite_messages[0]
    assert generate_test_suite(result) == suite
    assert charged_pattern_work(result) == used


def normal_pattern_properties(index: int) -> dict[str, dict[str, object]]:
    """Five realistic string properties whose examples differ per endpoint."""
    return {
        "id": {
            "type": "string",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            "example": f"123e4567-e89b-12d3-a456-{index:012d}",
        },
        "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$", "example": f"item-{index}"},
        "code": {"type": "string", "pattern": "^[A-Z]{2}[0-9]{3}$", "example": f"AB{index % 1000:03d}"},
        "phone": {
            "type": "string", "pattern": r"^\+?[0-9]{7,15}$", "example": f"+38044{index:07d}",
        },
        "name": {"type": "string", "pattern": "^[A-Za-z ]+$", "enum": ["Ann Lee", "Bob", "Cy"]},
    }


def normal_pattern_result(endpoints: int) -> NormalizationResult:
    return make_result(
        *(
            make_endpoint(
                f"/things{index}",
                "post",
                request_body=RequestBody(
                    required=True,
                    content={
                        "application/json": MediaType(
                            schema=Schema(
                                raw={
                                    "type": "object",
                                    "required": sorted(normal_pattern_properties(index)),
                                    "properties": normal_pattern_properties(index),
                                }
                            )
                        )
                    },
                ),
                responses=BAD_REQUEST,
            )
            for index in range(endpoints)
        )
    )


def test_representative_normal_pattern_input_stays_far_below_the_work_budget() -> None:
    result = normal_pattern_result(100)

    used = charged_pattern_work(result)
    suite = generate_test_suite(result)

    assert "generation_pattern_budget_exceeded" not in {item.code for item in suite.diagnostics}
    assert suite.diagnostics == ()
    assert {case.path for case in suite.cases} == {f"/things{index}" for index in range(100)}
    assert sum(used) < generator_module._MAX_SUITE_PATTERN_WORK // 4
    assert len(used) < generator_module._MAX_SUITE_PATTERN_EVALUATIONS // 4
    per_endpoint = sum(charged_pattern_work(normal_pattern_result(1)))
    assert per_endpoint < generator_module._MAX_ENDPOINT_PATTERN_WORK // 10


HUGE = 10**400


def huge_number_suite(schema: dict[str, object], version: str = "3.1.1") -> GeneratedTestSuite:
    body = pattern_object(schema)
    broken = make_endpoint(
        "/big",
        "post",
        request_body=RequestBody(
            required=True, content={"application/json": MediaType(schema=Schema(raw=body))}
        ),
        responses=BAD_REQUEST,
    )
    healthy = make_endpoint("/healthy", "get", responses=BAD_REQUEST)
    return generate_test_suite(make_result(broken, healthy, version=version))


def positive_number(suite: GeneratedTestSuite) -> object:
    case = next(case for case in suite.cases if case.path == "/big" and case.category == "positive")
    assert case.request_body is not None
    return case.request_body.value["a"]  # type: ignore[index]


def satisfies_exactly(schema: dict[str, object], value: object) -> bool:
    from fractions import Fraction

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = Fraction(value)
    if schema["type"] == "integer" and number.denominator != 1:
        return False
    checks = {
        "minimum": lambda bound: number >= bound,
        "maximum": lambda bound: number <= bound,
        "exclusiveMinimum": lambda bound: number > bound,
        "exclusiveMaximum": lambda bound: number < bound,
    }
    for key, check in checks.items():
        if key in schema and not check(Fraction(schema[key])):  # type: ignore[arg-type]
            return False
    if "multipleOf" in schema:
        return (number / Fraction(schema["multipleOf"])).denominator == 1  # type: ignore[arg-type]
    return True


HUGE_BOUND_CASES = [
    ("3.1.1", "minimum", HUGE),
    ("3.1.1", "minimum", -HUGE),
    ("3.1.1", "maximum", HUGE),
    ("3.1.1", "maximum", -HUGE),
    ("3.1.1", "exclusiveMinimum", HUGE),
    ("3.1.1", "exclusiveMinimum", -HUGE),
    ("3.1.1", "exclusiveMaximum", HUGE),
    ("3.1.1", "exclusiveMaximum", -HUGE),
    ("3.1.1", "multipleOf", HUGE),
    ("3.0.3", "minimum", HUGE),
    ("3.0.3", "minimum", -HUGE),
    ("3.0.3", "maximum", HUGE),
    ("3.0.3", "maximum", -HUGE),
    ("3.0.3", "multipleOf", HUGE),
]


@pytest.mark.parametrize("kind", ["integer", "number"])
@pytest.mark.parametrize(("version", "keyword", "bound"), HUGE_BOUND_CASES)
def test_huge_integer_bounds_do_not_abort_the_suite(
    kind: str, version: str, keyword: str, bound: int
) -> None:
    schema: dict[str, object] = {"type": kind, keyword: bound}
    suite = huge_number_suite(schema, version)

    assert any(case.path == "/healthy" for case in suite.cases)
    assert satisfies_exactly(schema, positive_number(suite))


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "minimum": HUGE, "multipleOf": 3},
        {"type": "integer", "maximum": -HUGE, "multipleOf": 7},
        {"type": "integer", "minimum": HUGE, "maximum": HUGE + 5},
        {"type": "integer", "minimum": HUGE, "multipleOf": HUGE},
        {"type": "integer", "multipleOf": HUGE, "maximum": 10**500},
        {"type": "integer", "exclusiveMinimum": HUGE, "multipleOf": 2.5},
        {"type": "integer", "minimum": -HUGE, "maximum": HUGE, "multipleOf": 10**30},
    ],
)
def test_huge_integer_bounds_with_multiple_of_generate_exactly_valid_values(
    schema: dict[str, object],
) -> None:
    suite = huge_number_suite(schema)

    assert any(case.path == "/healthy" for case in suite.cases)
    value = positive_number(suite)
    assert isinstance(value, int) and satisfies_exactly(schema, value)
    assert not [d for d in suite.diagnostics if d.code == "no_positive_schema_value"]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "minimum": float("inf")},
        {"type": "integer", "maximum": float("-inf")},
        {"type": "integer", "exclusiveMinimum": float("inf")},
        {"type": "integer", "exclusiveMaximum": float("-inf")},
        {"type": "integer", "multipleOf": float("inf")},
        {"type": "integer", "minimum": float("nan")},
        {"type": "integer", "multipleOf": float("nan")},
    ],
)
def test_non_finite_integer_bounds_do_not_abort_the_suite(schema: dict[str, object]) -> None:
    suite = huge_number_suite(schema)

    assert any(case.path == "/healthy" for case in suite.cases)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "minimum": float("inf")},
        {"type": "integer", "maximum": float("-inf")},
        {"type": "integer", "multipleOf": float("inf")},
    ],
)
def test_unsatisfiable_non_finite_integer_bounds_give_an_explicit_diagnostic(
    schema: dict[str, object],
) -> None:
    suite = huge_number_suite(schema)

    assert not [case for case in suite.cases if case.path == "/big"]
    codes = [d.code for d in suite.diagnostics if d.endpoint_path == "/big"]
    assert codes == ["no_positive_schema_value"]


def test_huge_integer_bound_generation_is_deterministic() -> None:
    schema: dict[str, object] = {"type": "integer", "minimum": HUGE, "multipleOf": 3}

    first = huge_number_suite(schema)
    second = huge_number_suite(schema)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_ordinary_integer_and_number_schemas_keep_their_values() -> None:
    integer = {"type": "integer", "minimum": 5, "multipleOf": 3}
    assert positive_number(huge_number_suite(integer)) == 6
    assert positive_number(huge_number_suite({"type": "integer", "minimum": 1, "maximum": 5})) == 1
    number = {"type": "number", "minimum": 1.5, "exclusiveMaximum": 4.5, "multipleOf": 0.5}
    value = positive_number(huge_number_suite(number))
    assert satisfies_exactly(number, value)
    assert not [d for d in huge_number_suite(number).diagnostics if d.endpoint_path == "/big"]


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        ({"multipleOf": 3}, 9, True),
        ({"multipleOf": 3}, 7, False),
        ({"multipleOf": 0.1}, 0.3, True),
        ({"multipleOf": 0.5}, 1.25, False),
        ({"multipleOf": 0}, 4, False),
        ({"multipleOf": 3}, float("inf"), False),
        ({"multipleOf": 3}, float("nan"), False),
        ({"multipleOf": float("inf")}, 3, False),
        ({"multipleOf": 1}, 10**30, True),
        ({"multipleOf": 3}, 3 * 10**400, True),
        ({"multipleOf": 3}, 3 * 10**400 + 1, False),
        ({"multipleOf": HUGE}, 2 * HUGE, True),
    ],
)
def test_number_multiple_of_uses_exact_arithmetic(
    schema: dict[str, object], value: int | float, expected: bool
) -> None:
    assert generator_module._number_matches(schema, value, "3.1.1") is expected


def test_is_finite_number_accepts_every_int_and_checks_floats() -> None:
    assert generator_module._is_finite_number(HUGE)
    assert generator_module._is_finite_number(-HUGE)
    assert generator_module._is_finite_number(1.5)
    assert not generator_module._is_finite_number(float("inf"))
    assert not generator_module._is_finite_number(float("nan"))


def required_body_result(*schemas: dict[str, object]) -> NormalizationResult:
    endpoints = tuple(
        make_endpoint(
            f"/e{index}",
            "post",
            request_body=RequestBody(
                required=True,
                content={"application/json": MediaType(schema=Schema(raw=schema))},
            ),
            responses=BAD_REQUEST,
        )
        for index, schema in enumerate(schemas)
    )
    return make_result(*endpoints)


FLAT_REQUIRED_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [f"p{index:02d}" for index in range(12)],
    "properties": {
        f"p{index:02d}": {"type": "string" if index % 2 else "integer"} for index in range(12)
    },
}
NESTED_REQUIRED_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["a", "b"],
    "properties": {
        "a": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "string"},
                "y": {
                    "type": "object",
                    "required": ["z", "w"],
                    "properties": {"z": {"type": "integer"}, "w": {"type": "string"}},
                },
            },
        },
        "b": {"type": "string"},
        "c": {"type": "string"},
    },
}
ARRAY_REQUIRED_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["tags", "owner"],
    "properties": {
        "tags": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["k"],
                "properties": {"k": {"type": "string"}},
            },
        },
        "owner": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "integer"},
                "history": {"type": "array", "items": {"type": "integer"}},
            },
        },
    },
}
REQUIRED_BODY_SCHEMAS = [FLAT_REQUIRED_SCHEMA, NESTED_REQUIRED_SCHEMA, ARRAY_REQUIRED_SCHEMA]


def required_property_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [case for case in suite.cases if case.violated_constraint == "required_property"]


def required_positive_body(suite: GeneratedTestSuite, path: str = "/e0") -> object:
    case = next(case for case in suite.cases if case.path == path and case.category == "positive")
    assert case.request_body is not None
    return case.request_body.value


def pointer_parts(pointer: str) -> tuple[str, ...]:
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:])


def container_ids(value: object) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        found.add(id(value))
        for item in value.values():
            found |= container_ids(item)
    elif isinstance(value, list):
        found.add(id(value))
        for item in value:
            found |= container_ids(item)
    return found


@pytest.mark.parametrize("schema", REQUIRED_BODY_SCHEMAS)
def test_required_property_bodies_are_the_positive_body_without_that_property(
    schema: dict[str, object],
) -> None:
    suite = generate_test_suite(required_body_result(schema))
    positive = required_positive_body(suite)
    cases = required_property_cases(suite)

    assert cases
    for case in cases:
        assert case.affected_property is not None and case.request_body is not None
        path = pointer_parts(case.affected_property)
        expected = deepcopy(positive)
        target = expected
        for name in path[:-1]:
            target = target[name]  # type: ignore[index]
        del target[path[-1]]  # type: ignore[arg-type]
        assert case.request_body.value == expected
        # The positive body itself still has the property.
        present = positive
        for name in path:
            present = present[name]  # type: ignore[index]


@pytest.mark.parametrize("schema", REQUIRED_BODY_SCHEMAS)
def test_path_copy_matches_the_deepcopy_reference_for_the_whole_suite(
    monkeypatch: pytest.MonkeyPatch, schema: dict[str, object]
) -> None:
    result = required_body_result(schema)
    with_path_copy = generate_test_suite(result)

    def reference(value: object, path: tuple[str, ...]) -> object:
        copied = deepcopy(value)
        target = copied
        for name in path[:-1]:
            target = target[name]  # type: ignore[index]
        del target[path[-1]]  # type: ignore[arg-type]
        return copied

    monkeypatch.setattr(generator_module, "_without_property", reference)
    with_deepcopy = generate_test_suite(result)

    assert required_property_cases(with_path_copy)
    assert with_path_copy.model_dump() == with_deepcopy.model_dump()
    assert with_path_copy.model_dump_json() == with_deepcopy.model_dump_json()


def test_required_property_cases_cover_every_depth_in_a_fixed_order() -> None:
    result = required_body_result(NESTED_REQUIRED_SCHEMA)
    suite = generate_test_suite(result)
    cases = required_property_cases(suite)

    assert [case.affected_property for case in cases] == [
        "/a",
        "/a/x",
        "/a/y",
        "/a/y/w",
        "/a/y/z",
        "/b",
    ]
    assert len({case.case_id for case in cases}) == len(cases)
    again = generate_test_suite(result)
    assert [c.case_id for c in again.cases] == [c.case_id for c in suite.cases]
    assert again == suite
    assert required_positive_body(suite) == {"a": {"x": "sentinel", "y": {"z": 0, "w": "sentinel"}}, "b": "sentinel"}
    assert cases[3].request_body is not None
    assert cases[3].request_body.value == {"a": {"x": "sentinel", "y": {"z": 0}}, "b": "sentinel"}


def test_required_property_inside_array_items_is_not_a_separate_case() -> None:
    suite = generate_test_suite(required_body_result(ARRAY_REQUIRED_SCHEMA))
    positive = required_positive_body(suite)

    assert [case.affected_property for case in required_property_cases(suite)] == [
        "/owner",
        "/owner/id",
        "/tags",
    ]
    assert isinstance(positive, dict) and isinstance(positive["tags"], list)  # type: ignore[index]
    # The array and its items are kept intact in every other case.
    for case in required_property_cases(suite):
        assert case.request_body is not None
        body = case.request_body.value
        if case.affected_property != "/tags":
            assert body["tags"] == positive["tags"]  # type: ignore[index]


@pytest.mark.parametrize("schema", [NESTED_REQUIRED_SCHEMA, ARRAY_REQUIRED_SCHEMA])
def test_required_property_bodies_share_no_mutable_objects(schema: dict[str, object]) -> None:
    suite = generate_test_suite(required_body_result(schema))
    positive = required_positive_body(suite)
    snapshot = deepcopy(positive)
    cases = required_property_cases(suite)
    positive_ids = container_ids(positive)
    id_sets = [container_ids(case.request_body.value) for case in cases if case.request_body]

    assert len(id_sets) == len(cases) > 1
    for index, ids in enumerate(id_sets):
        assert ids.isdisjoint(positive_ids)
        for other in id_sets[index + 1 :]:
            assert ids.isdisjoint(other)

    first = cases[0].request_body
    assert first is not None and isinstance(first.value, dict)
    first.value["injected"] = {"mutated": True}  # type: ignore[index]
    for nested in first.value.values():
        if isinstance(nested, dict):
            nested["mutated"] = True
        elif isinstance(nested, list):
            nested.append("mutated")
    assert positive == snapshot
    for case in cases[1:]:
        assert case.request_body is not None
        assert "injected" not in case.request_body.value  # type: ignore[operator]


def generate_flat_required_suite(
    monkeypatch: pytest.MonkeyPatch, *, endpoint_limit: int | None = None, suite_limit: int | None = None
) -> GeneratedTestSuite:
    if endpoint_limit is not None:
        monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", endpoint_limit)
    if suite_limit is not None:
        monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", suite_limit)
    return generate_test_suite(required_body_result(FLAT_REQUIRED_SCHEMA))


def test_required_property_step_fits_exactly_at_the_endpoint_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 12  # the required properties of FLAT_REQUIRED_SCHEMA
    fits = generate_flat_required_suite(monkeypatch, endpoint_limit=count + 1)

    assert len(required_property_cases(fits)) == count
    assert [d.code for d in fits.diagnostics].count("generation_case_budget_exceeded") <= 1
    assert all(d.message.find("'required_property'") < 0 for d in fits.diagnostics)


def test_required_property_step_one_case_over_the_endpoint_limit_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = generate_flat_required_suite(monkeypatch, endpoint_limit=12)

    assert [case.category for case in suite.cases] == ["positive"]
    assert [(d.code, d.message, d.endpoint_path, d.source_pointer) for d in suite.diagnostics] == [
        (
            "generation_case_budget_exceeded",
            "The per-endpoint limit of 12 generated test cases was reached during "
            "the 'required_property' step. That step produced no cases and the remaining "
            "steps for this endpoint were skipped.",
            "/e0",
            "/paths/~1e0/post",
        )
    ]


def test_required_property_step_over_the_suite_limit_reports_the_suite_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = required_body_result(FLAT_REQUIRED_SCHEMA, FLAT_REQUIRED_SCHEMA, FLAT_REQUIRED_SCHEMA)
    full = generate_test_suite(result)
    first_endpoint = [case for case in full.cases if case.path == "/e0"]
    limit = len(first_endpoint) + 1 + 3  # the second endpoint's baseline, then too few
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", limit)

    suite = generate_test_suite(result)

    assert [case.path for case in suite.cases] == [case.path for case in first_endpoint] + ["/e1"]
    assert suite.cases[-1].category == "positive"
    assert [(d.code, d.message, d.endpoint_path) for d in suite.diagnostics] == [
        (
            "generation_case_budget_exceeded",
            f"The per-suite limit of {limit} generated test cases was reached during "
            "the 'required_property' step of this endpoint. That step produced no cases; the "
            "remaining steps of this endpoint and 1 following endpoint(s) were not generated.",
            "/e1",
        )
    ]


def prepare_required_step(
    result: NormalizationResult,
) -> tuple[object, object, GeneratedTestCase]:
    suite = generate_test_suite(result)
    positive = next(case for case in suite.cases if case.category == "positive")
    return result.api.endpoints[0], result.api, positive


def run_required_step(prepared: tuple[object, object, GeneratedTestCase], budget: object) -> tuple[object, object]:
    endpoint, api, positive = prepared
    token = generator_module._BUDGET.set(budget)  # type: ignore[arg-type]
    try:
        return generator_module._run_step(  # type: ignore[arg-type]
            budget,
            lambda: generator_module._required_property_negative_cases(endpoint, positive, api),  # type: ignore[arg-type]
        )
    finally:
        generator_module._BUDGET.reset(token)


def count_required_step_work(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"copy": 0, "body": 0, "case": 0, "deepcopy": 0}
    originals = {
        "copy": generator_module._without_property,
        "body": generator_module.GeneratedBody,
        "case": generator_module.GeneratedTestCase,
        "deepcopy": generator_module.deepcopy,
    }

    def counting(name: str) -> object:
        def wrapper(*args: object, **kwargs: object) -> object:
            calls[name] += 1
            return originals[name](*args, **kwargs)  # type: ignore[operator]

        return wrapper

    monkeypatch.setattr(generator_module, "_without_property", counting("copy"))
    monkeypatch.setattr(generator_module, "GeneratedBody", counting("body"))
    monkeypatch.setattr(generator_module, "GeneratedTestCase", counting("case"))
    monkeypatch.setattr(generator_module, "deepcopy", counting("deepcopy"))
    return calls


@pytest.mark.parametrize("remaining", [0, 1, 3, 11])
def test_step_that_cannot_fit_builds_no_bodies_and_validates_nothing(
    monkeypatch: pytest.MonkeyPatch, remaining: int
) -> None:
    # FLAT_REQUIRED_SCHEMA needs 12 cases; with 11 left it is one case short.
    prepared = prepare_required_step(required_body_result(FLAT_REQUIRED_SCHEMA))
    budget = generator_module._GenerationBudget()
    budget.endpoint_cases = generator_module._MAX_ENDPOINT_CASES - remaining
    budget.suite_cases = 100
    before = budget.case_counts()
    calls = count_required_step_work(monkeypatch)

    outcome, exhausted = run_required_step(prepared, budget)

    assert outcome is None
    assert (exhausted.code, exhausted.scope, exhausted.limit) == (  # type: ignore[attr-defined]
        "generation_case_budget_exceeded",
        "endpoint",
        generator_module._MAX_ENDPOINT_CASES,
    )
    assert calls == {"copy": 0, "body": 0, "case": 0, "deepcopy": 0}
    assert budget.case_counts() == before


def test_step_that_fits_exactly_builds_every_case(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = prepare_required_step(required_body_result(FLAT_REQUIRED_SCHEMA))
    budget = generator_module._GenerationBudget()
    budget.endpoint_cases = generator_module._MAX_ENDPOINT_CASES - 12
    calls = count_required_step_work(monkeypatch)

    outcome, exhausted = run_required_step(prepared, budget)

    assert exhausted is None
    cases, _ = outcome  # type: ignore[misc]
    assert len(cases) == 12
    assert budget.endpoint_cases == generator_module._MAX_ENDPOINT_CASES
    assert (calls["copy"], calls["case"]) == (12, 12)


def test_suite_scope_pre_check_reports_the_suite_limit_without_building_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_required_step(required_body_result(FLAT_REQUIRED_SCHEMA))
    budget = generator_module._GenerationBudget()
    budget.suite_cases = generator_module._MAX_SUITE_CASES - 5
    before = budget.case_counts()
    calls = count_required_step_work(monkeypatch)

    outcome, exhausted = run_required_step(prepared, budget)

    assert outcome is None
    assert (exhausted.scope, exhausted.limit) == ("suite", generator_module._MAX_SUITE_CASES)  # type: ignore[attr-defined]
    assert calls == {"copy": 0, "body": 0, "case": 0, "deepcopy": 0}
    assert budget.case_counts() == before


def test_step_that_fits_charges_each_case_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_required_step(required_body_result(FLAT_REQUIRED_SCHEMA))
    budget = generator_module._GenerationBudget()
    budget.endpoint_cases = 7
    budget.suite_cases = 40
    calls = count_required_step_work(monkeypatch)

    outcome, exhausted = run_required_step(prepared, budget)

    assert exhausted is None
    cases, _ = outcome  # type: ignore[misc]
    assert len(cases) == 12
    assert budget.case_counts() == (7 + 12, 40 + 12)
    assert (calls["copy"], calls["body"], calls["case"]) == (12, 12, 12)


# Mutation isolation of response expectations and security between cases.
#
# Pydantic reuses an already-built model instance instead of copying it, so the
# nested dictionaries inside ``ResponseExpectation`` and ``SecurityRequirement``
# stay independent only because every case copies them explicitly. These tests
# mutate those dictionaries in place and check that no other case changes.

ISOLATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string"}, "detail": {"type": "object"}},
}

ISOLATION_STEPS = {
    "required_parameter": 1,
    "required_request_body": 1,
    "required_property": 4,
    "type": 4,
    "minLength": 1,
    "maxLength": 1,
    "minimum": 1,
    "maximum": 1,
    "enum": 1,
    "minItems": 1,
    "maxItems": 1,
}


def is_valid_edge_case(case: GeneratedTestCase) -> bool:
    """Whether a case is a positive numeric valid-edge case (Step 8.42.1)."""
    return any(entry.startswith("valid_boundary_edge:") for entry in case.provenance)


def without_valid_edge_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [case for case in suite.cases if not is_valid_edge_case(case)]


def isolation_suite() -> GeneratedTestSuite:
    endpoint = Endpoint(
        path="/isolation",
        method="post",
        parameters=(
            Parameter(
                name="trace",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string"}),
                source_scope="operation",
            ),
        ),
        security=(SecurityRequirement(schemes={"oauth": ("write",)}),),
        responses={
            "200": Response(
                description="OK",
                content={
                    "application/json": MediaType(schema=Schema(raw=ISOLATION_RESPONSE_SCHEMA))
                },
            ),
            "400": Response(
                description="Invalid request",
                content={
                    "application/json": MediaType(schema=Schema(raw=ISOLATION_RESPONSE_SCHEMA))
                },
            ),
        },
        request_body=RequestBody(
            required=True,
            content={
                "application/json": MediaType(
                    schema=Schema(
                        raw={
                            "type": "object",
                            "required": ["name", "count", "kind", "tags"],
                            "properties": {
                                "name": {"type": "string", "minLength": 2, "maxLength": 5},
                                "count": {"type": "integer", "minimum": 1, "maximum": 9},
                                "kind": {"type": "string", "enum": ["a", "b"]},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "maxItems": 3,
                                },
                            },
                        }
                    )
                )
            },
        ),
    )
    return generate_test_suite(
        make_result(endpoint, components=declared_security_schemes("oauth"))
    )


def mutate_response_expectations(case: GeneratedTestCase) -> None:
    for expectation in case.response_expectations:
        schemas = expectation.schemas_by_media_type
        schemas["application/json"].raw["properties"]["error"]["type"] = "MUTATED"
        schemas["mutated/type"] = None


def mutate_security(case: GeneratedTestCase) -> None:
    for requirement_value in case.security or ():
        requirement_value.schemes["oauth"] = ("MUTATED",)
        requirement_value.schemes["mutated"] = ("MUTATED",)


ISOLATION_MUTATIONS = {
    "response_expectations": mutate_response_expectations,
    "security": mutate_security,
}


def test_isolation_suite_covers_every_expected_negative_step() -> None:
    suite = isolation_suite()
    counts: dict[str, int] = {}
    for case in without_valid_edge_cases(suite)[1:]:
        counts[case.violated_constraint] = counts.get(case.violated_constraint, 0) + 1

    assert suite.cases[0].category == "positive"
    assert counts == ISOLATION_STEPS
    # Valid-edge cases: the maximum of "count" (the baseline holds the minimum) and the
    # minLength of "name" (the baseline "senti" holds the maximum length).
    assert [
        (case.provenance[-1], case.request_body.value["count"], case.request_body.value["name"])  # type: ignore[union-attr, index]
        for case in suite.cases
        if is_valid_edge_case(case)
    ] == [
        ("valid_boundary_edge:maximum:/count", 9, "senti"),
        ("valid_boundary_edge:minLength:/name", 1, "xx"),
    ]
    for case in suite.cases:
        assert case.security is not None and len(case.response_expectations) == 2


@pytest.mark.parametrize("structure", ISOLATION_MUTATIONS)
@pytest.mark.parametrize("step", ISOLATION_STEPS)
def test_mutating_a_negative_case_does_not_change_any_other_case(
    step: str, structure: str
) -> None:
    suite = isolation_suite()
    source = next(case for case in suite.cases if case.violated_constraint == step)
    before = [case.model_dump(mode="json") for case in suite.cases]

    ISOLATION_MUTATIONS[structure](source)

    after = [case.model_dump(mode="json") for case in suite.cases]
    assert after[suite.cases.index(source)] != before[suite.cases.index(source)]
    for index, case in enumerate(suite.cases):
        if case is not source:
            assert after[index] == before[index], case.case_id


@pytest.mark.parametrize("structure", ISOLATION_MUTATIONS)
def test_mutating_the_positive_case_does_not_change_any_negative_case(
    structure: str,
) -> None:
    suite = isolation_suite()
    positive = suite.cases[0]
    before = [case.model_dump(mode="json") for case in suite.cases]

    ISOLATION_MUTATIONS[structure](positive)

    after = [case.model_dump(mode="json") for case in suite.cases]
    assert after[0] != before[0]
    assert after[1:] == before[1:]


# --- Step 8.28: array bounds inside the first element of present arrays ----------

ELEMENT_TAGS: dict[str, object] = {"type": "array", "minItems": 2, "maxItems": 3, "items": ITEM_SCHEMA}
ELEMENT_ITEM: dict[str, object] = {
    "type": "object",
    "required": ["tags"],
    "properties": {"tags": ELEMENT_TAGS},
}
ELEMENT_ITEM_POINTER = f"{ARRAY_BODY_POINTER}/properties/rows/items"


def rows_body(rows: object) -> dict[str, object]:
    return {"type": "object", "required": ["rows"], "properties": {"rows": rows}}


def element_rows(item: object, **options: object) -> dict[str, object]:
    return rows_body({"type": "array", "minItems": 1, "items": item, **options})


def element_suite(body: object, schemas: dict[str, object] | None = None) -> GeneratedTestSuite:
    endpoint = post_endpoint(body)
    if schemas:
        return generate_test_suite(make_result_with_schemas(endpoint, schemas))
    return generate_test_suite(make_result(endpoint))


def element_values(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None, object]]:
    return array_values(suite)


def test_present_inner_array_of_an_object_element_gets_bound_cases() -> None:
    suite = element_suite(element_rows(ELEMENT_ITEM))

    assert suite.cases[0].request_body.value == {"rows": [{"tags": ["x", "x"]}]}  # type: ignore[union-attr]
    assert element_values(suite) == [
        ("/rows", "minItems", {"rows": []}),
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x", "x", "x", "x"]}]}),
    ]
    case = array_cases(suite)[1]
    assert f"{ELEMENT_ITEM_POINTER}/properties/tags" in case.provenance
    assert case.provenance[-1] == "violated_constraint:minItems:/rows/0/tags"
    assert array_diagnostics(suite) == []


def test_absent_optional_inner_array_of_an_element_follows_the_present_parent_rule() -> None:
    item = {"type": "object", "properties": {"tags": ELEMENT_TAGS, "ro": {**ELEMENT_TAGS, "readOnly": True}}}

    suite = element_suite(element_rows(item))

    assert suite.cases[0].request_body.value == {"rows": [{}]}  # type: ignore[union-attr]
    assert element_values(suite)[1:] == [
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x", "x", "x", "x"]}]}),
    ]
    assert suite.cases[0].request_body.value == {"rows": [{}]}  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("levels", "path", "minimum", "maximum"),
    [
        (1, "/rows/0", [["x"]], [["x"] * 4]),
        (2, "/rows/0/0", [[["x"]]], [[["x"] * 4]]),
    ],
)
def test_nested_arrays_get_inner_bound_cases(
    levels: int, path: str, minimum: object, maximum: object
) -> None:
    item: object = ELEMENT_TAGS
    for _ in range(levels - 1):
        item = {"type": "array", "minItems": 1, "items": item}

    suite = element_suite(element_rows(item))

    assert [value for value in element_values(suite) if value[0] == path] == [
        (path, "minItems", {"rows": minimum}),
        (path, "maxItems", {"rows": maximum}),
    ]
    assert array_diagnostics(suite) == []


def test_items_reference_is_followed_and_charged_for_the_first_element() -> None:
    def build() -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint(element_rows(schema_reference("Row"))), {"Row": ELEMENT_ITEM}
        )

    suite = generate_test_suite(build())

    assert element_values(suite)[1:] == [
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x", "x", "x", "x"]}]}),
    ]
    assert any("Row" in pointer for pointer in array_cases(suite)[1].provenance)
    assert array_diagnostics(suite) == []


def test_unresolvable_items_reference_is_reported_once_and_keeps_other_cases() -> None:
    body = rows_body(
        {"type": "array", "minItems": 1, "items": schema_reference("Missing")}
    )

    cases, diagnostics = run_array_step(body, {"rows": [{"tags": ["x", "x"]}]})

    assert [(case.affected_property, case.violated_constraint) for case in cases] == [
        ("/rows", "minItems")
    ]
    assert [(item.code, item.reference_uri) for item in diagnostics] == [
        ("array_bound_unsupported_schema", "#/components/schemas/Missing")
    ]


def test_items_reference_hop_is_charged_to_the_expansion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build() -> NormalizationResult:
        return make_result_with_schemas(
            post_endpoint(element_rows(schema_reference("Row"))), {"Row": ELEMENT_ITEM}
        )

    # The valid-edge step runs after array_bounds; its hops are not part of this test.
    monkeypatch.setattr(generator_module, "_numeric_valid_edge_cases", lambda *args: ((), ()))
    monkeypatch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module, "_array_bound_negative_cases", lambda *args: ((), ()))
        hops_without_array_step = count_expansions(build())
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops_without_array_step)

    suite = generate_test_suite(build())

    assert array_cases(suite) == []
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


def test_inner_array_case_keeps_an_outer_unique_items_array_valid() -> None:
    item = {**ELEMENT_ITEM, "examples": [{"tags": ["a", "b"]}, {"tags": ["c", "d"]}]}

    suite = element_suite(element_rows(item, minItems=2, uniqueItems=True))

    assert suite.cases[0].request_body.value == {"rows": [{"tags": ["a", "b"]}, {"tags": ["c", "d"]}]}  # type: ignore[union-attr]
    inner = [case for case in array_cases(suite) if case.affected_property == "/rows/0/tags"]
    assert [(case.violated_constraint, body_value(case)) for case in inner] == [
        ("minItems", {"rows": [{"tags": ["x"]}, {"tags": ["c", "d"]}]}),
        ("maxItems", {"rows": [{"tags": ["x", "x", "x", "x"]}, {"tags": ["c", "d"]}]}),
    ]
    for case in inner:
        assert not generator_module._has_duplicate_items(body_value(case)["rows"])  # type: ignore[index]
    outer = [case for case in array_cases(suite) if case.affected_property == "/rows"]
    assert [case.violated_constraint for case in outer] == ["minItems", "uniqueItems"]


@pytest.mark.parametrize("keyword", ["const", "enum"])
@pytest.mark.parametrize("where", ["array", "items", "outer-of-nested"])
def test_enclosing_const_or_enum_blocks_element_cases_with_a_diagnostic(
    keyword: str, where: str
) -> None:
    literal = [{"tags": ["x", "x"]}]
    if where == "array":
        rows = {"type": "array", "items": ELEMENT_ITEM}
        value: object = literal if keyword == "const" else [literal]
        rows[keyword] = value
        pointer = f"{ARRAY_BODY_POINTER}/properties/rows/{keyword}"
    elif where == "items":
        item = dict(ELEMENT_ITEM)
        item[keyword] = literal[0] if keyword == "const" else [literal[0]]
        rows = {"type": "array", "minItems": 1, "items": item}
        pointer = f"{ELEMENT_ITEM_POINTER}/{keyword}"
    else:
        inner = {"type": "array", "minItems": 1, "items": ELEMENT_ITEM}
        rows = {"type": "array", "minItems": 1, "items": inner}
        rows[keyword] = [[literal]] if keyword == "enum" else [literal]
        pointer = f"{ARRAY_BODY_POINTER}/properties/rows/{keyword}"
        literal = [literal]

    suite = element_suite(rows_body(rows))

    assert [prop for prop, _, _ in element_values(suite) if prop and "/0" in prop] == []
    messages = [item for item in suite.diagnostics if "enclosing literal" in item.message]
    expected = ["minItems", "maxItems"] if where != "outer-of-nested" else ["minItems", "minItems", "maxItems"]
    assert [item.code for item in messages] == ["array_bound_no_safe_candidate"] * len(expected)
    assert [item.source_pointer.rsplit("/", 1)[1] for item in messages] == expected  # type: ignore[union-attr]
    assert all(item.message.endswith(f"{pointer}; no case was generated.") for item in messages)


def test_empty_positive_array_gets_one_created_element_in_negative_cases_only() -> None:
    suite = element_suite(element_rows(ELEMENT_ITEM, minItems=0))

    # The positive body keeps the empty array; one element exists only in the cases.
    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x"] * 4}]}),
    ]
    # The existing "minItems is 0" explanation is unchanged.
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/properties/rows/minItems")
    ]


def test_only_element_zero_is_visited_and_a_later_only_target_is_not_covered() -> None:
    # Element 0 has an absent parent that cannot be created (unsupported required
    # sibling); element 1 has the parent present, so only element 1 could be targeted.
    parent = {
        "type": "object",
        "required": ["s"],
        "properties": {"s": {"type": "string", "pattern": "(a|b)"}, "tags": ELEMENT_TAGS},
    }
    body = rows_body({"type": "array", "items": {"type": "object", "properties": {"p": parent}}})

    later_only = run_array_step(body, {"rows": [{}, {"p": {"s": "a"}}]})
    first = run_array_step(body, {"rows": [{"p": {"s": "a"}}, {}]})

    assert later_only[0] == ()
    assert [(item.code, item.source_pointer.rsplit("/", 1)[1]) for item in later_only[1]] == [  # type: ignore[union-attr]
        ("array_bound_unsupported_schema", "pattern")
    ]
    assert [(case.affected_property, case.violated_constraint) for case in first[0]] == [
        ("/rows/0/p/tags", "minItems"),
        ("/rows/0/p/tags", "maxItems"),
    ]
    assert first[0][0].request_body.value == {  # type: ignore[union-attr]
        "rows": [{"p": {"s": "a", "tags": ["x"]}}, {}]
    }


def test_element_cases_are_appended_after_the_existing_array_cases() -> None:
    body = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {
            "a": {"type": "array", "minItems": 1, "items": ELEMENT_ITEM},
            "b": {"type": "array", "minItems": 1, "maxItems": 2, "items": ITEM_SCHEMA},
        },
    }

    suite = element_suite(body)

    summary = [(prop, constraint) for prop, constraint, _ in element_values(suite)]
    assert summary == [
        ("/a", "minItems"),
        ("/b", "minItems"),
        ("/b", "maxItems"),
        ("/a/0/tags", "minItems"),
        ("/a/0/tags", "maxItems"),
    ]
    ids = [case.case_id for case in array_cases(suite)]
    assert ids[:3] == [
        build_case_id("negative", "/items", "post", "array-bound:/a:minItems:0"),
        build_case_id("negative", "/items", "post", "array-bound:/b:minItems:0"),
        build_case_id("negative", "/items", "post", "array-bound:/b:maxItems:3"),
    ]
    assert ids[3:] == [
        build_case_id("negative", "/items", "post", "array-bound:/a/0/tags:minItems:1"),
        build_case_id("negative", "/items", "post", "array-bound:/a/0/tags:maxItems:4"),
    ]
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)


def test_element_cases_are_isolated_from_the_positive_case_and_siblings() -> None:
    result = make_result(post_endpoint(element_rows(ELEMENT_ITEM)))
    before_input = result.model_dump_json()
    suite = generate_test_suite(result)
    source = next(case for case in suite.cases if case.affected_property == "/rows/0/tags")
    before = [case.model_dump(mode="json") for case in suite.cases]

    body_value(source)["rows"][0]["tags"].append("mutated")  # type: ignore[index]
    body_value(source)["rows"].append("mutated")  # type: ignore[attr-defined,index]

    after = [case.model_dump(mode="json") for case in suite.cases]
    index = suite.cases.index(source)
    assert after[index] != before[index]
    assert [item for i, item in enumerate(after) if i != index] == [
        item for i, item in enumerate(before) if i != index
    ]
    assert result.model_dump_json() == before_input


def test_element_array_step_case_budget_keeps_the_whole_step_all_or_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = element_suite(element_rows(ELEMENT_ITEM))
    before_array = len(reference.cases) - len(array_cases(reference))
    assert len(array_cases(reference)) == 3

    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 3)
        assert element_suite(element_rows(ELEMENT_ITEM)).cases == reference.cases

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", before_array + 2)
    suite = element_suite(element_rows(ELEMENT_ITEM))

    assert array_cases(suite) == []
    assert suite.cases == reference.cases[:before_array]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'array_bounds'" in suite.diagnostics[0].message


def test_element_cases_are_deterministic_with_stable_ids() -> None:
    def build() -> GeneratedTestSuite:
        return element_suite(element_rows({"type": "array", "minItems": 1, "items": ELEMENT_ITEM}))

    first, second = build(), build()

    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in array_cases(first)] == [case.case_id for case in array_cases(second)]
    assert len({case.case_id for case in first.cases}) == len(first.cases)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_generate_element_array_cases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": element_rows(ELEMENT_ITEM)}},
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert [(prop, constraint) for prop, constraint, _ in element_values(suite)] == [
        ("/rows", "minItems"),
        ("/rows/0/tags", "minItems"),
        ("/rows/0/tags", "maxItems"),
    ]
    assert array_diagnostics(suite) == []


# --- Step 8.28 review: literals on a nested array and on its enclosing arrays ---

LITERAL_ITEM: dict[str, object] = {"type": "string"}


def nested_literal_rows(literal: dict[str, object]) -> dict[str, object]:
    """A root array whose element is itself an array declaring the given literal."""
    inner = {"type": "array", "minItems": 2, "maxItems": 3, "items": LITERAL_ITEM, **literal}
    return {"type": "array", "minItems": 1, "items": inner}


def test_nested_array_without_a_literal_keeps_its_normal_bound_cases() -> None:
    suite = element_suite(nested_literal_rows({}))

    assert suite.cases[0].request_body.value == [["sentinel", "sentinel"]]  # type: ignore[union-attr]
    assert element_values(suite) == [
        ("", "minItems", []),
        ("/0", "minItems", [["sentinel"]]),
        ("/0", "maxItems", [["sentinel"] * 4]),
    ]
    assert array_diagnostics(suite) == []


def test_nested_array_const_gives_no_case_because_no_member_violates_a_bound() -> None:
    suite = element_suite(nested_literal_rows({"const": ["x", "x"]}))

    assert suite.cases[0].request_body.value == [["x", "x"]]  # type: ignore[union-attr]
    # Only the outer array case remains; the element array is not changed.
    assert [(prop, constraint) for prop, constraint, _ in element_values(suite)] == [("", "minItems")]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/items/minItems"),
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/items/maxItems"),
    ]
    messages = [item.message for item in suite.diagnostics if item.code == "array_bound_no_safe_candidate"]
    assert messages[0].startswith("No deterministic array safely violates 'minItems'")
    assert messages[1].startswith("No deterministic array safely violates 'maxItems'")


@pytest.mark.parametrize(
    ("constraint", "other", "members", "expected"),
    [
        ("minItems", "maxItems", [["x", "x"], ["y"]], [["y"]]),
        ("maxItems", "minItems", [["x", "x"], ["y", "y", "y", "y"]], [["y", "y", "y", "y"]]),
    ],
)
def test_nested_array_enum_uses_a_member_that_violates_only_the_targeted_bound(
    constraint: str, other: str, members: list[list[str]], expected: object
) -> None:
    suite = element_suite(nested_literal_rows({"enum": members}))

    assert suite.cases[0].request_body.value == [["x", "x"]]  # type: ignore[union-attr]
    assert [value for value in element_values(suite) if value[0] == "/0"] == [("/0", constraint, expected)]
    case = next(case for case in array_cases(suite) if case.affected_property == "/0")
    changed = body_value(case)[0]  # type: ignore[index]
    # The changed element is a member of the enum, so the enum is not violated.
    assert changed in members
    assert body_value(case) == expected
    assert (len(changed) < 2) if constraint == "minItems" else (len(changed) > 3)
    # The other bound has no member that violates it, and is reported as before.
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/items/{other}")
    ]


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_targets_below_a_literal_constrained_nested_array_are_blocked(keyword: str) -> None:
    literal = [{"tags": ["x", "x"]}]
    inner = {"type": "array", "minItems": 1, "items": ELEMENT_ITEM}
    inner[keyword] = literal if keyword == "const" else [literal]
    suite = element_suite({"type": "array", "minItems": 1, "items": inner})

    assert suite.cases[0].request_body.value == [[{"tags": ["x", "x"]}]]  # type: ignore[union-attr]
    assert [(prop, constraint) for prop, constraint, _ in element_values(suite)] == [("", "minItems")]
    inner_pointer = f"{ARRAY_BODY_POINTER}/items"
    assert array_diagnostics(suite) == [
        # The nested array itself: its own literal leaves no shorter array.
        ("array_bound_no_safe_candidate", f"{inner_pointer}/minItems"),
        # The arrays below it would also change the literal.
        ("array_bound_no_safe_candidate", f"{inner_pointer}/items/properties/tags/minItems"),
        ("array_bound_no_safe_candidate", f"{inner_pointer}/items/properties/tags/maxItems"),
    ]
    blocked = [item for item in suite.diagnostics if "enclosing literal" in item.message]
    assert len(blocked) == 2
    assert all(item.message.endswith(f"at {inner_pointer}/{keyword}; no case was generated.") for item in blocked)


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ({"const": ["x", "x"]}, []),
        ({"enum": [["x", "x"], ["y"]]}, [("/a", "minItems", {"a": ["y"]})]),
    ],
    ids=["const", "enum"],
)
def test_array_property_literal_follows_the_same_candidate_selection(
    literal: dict[str, object], expected: list[tuple[str, str, object]]
) -> None:
    prop = {"type": "array", "minItems": 2, "items": LITERAL_ITEM, **literal}
    suite = element_suite({"type": "object", "required": ["a"], "properties": {"a": prop}})

    assert suite.cases[0].request_body.value == {"a": ["x", "x"]}  # type: ignore[union-attr]
    assert element_values(suite) == expected


# --- Step 8.30: array bounds below absent optional parent objects ---------------

ABSENT_PROPERTIES = f"{ARRAY_BODY_POINTER}/properties"
ABSENT_MINMAX = [("minItems", ["x"]), ("maxItems", ["x", "x", "x", "x"])]


def absent_body(properties: dict[str, object], required: tuple[str, ...] = ()) -> dict[str, object]:
    return {"type": "object", "required": list(required), "properties": properties}


def tags_parent(**extra: object) -> dict[str, object]:
    return {"type": "object", "properties": {"tags": ELEMENT_TAGS}, **extra}


def absent_cases(suite: GeneratedTestSuite, prefix: str) -> list[tuple[str | None, str | None, object]]:
    return [value for value in array_values(suite) if str(value[0]).startswith(prefix)]


def test_required_array_inside_an_absent_optional_parent_gets_bound_cases() -> None:
    parent = {"type": "object", "required": ["tags"], "properties": {"tags": ELEMENT_TAGS}}

    suite = element_suite(absent_body({"p": parent}))

    assert suite.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/p/tags", constraint, {"p": {"tags": value}}) for constraint, value in ABSENT_MINMAX
    ]
    case = array_cases(suite)[0]
    assert case.provenance[-3:] == (
        f"{ABSENT_PROPERTIES}/p/properties/tags",
        f"{ABSENT_PROPERTIES}/p/properties/tags/minItems",
        "violated_constraint:minItems:/p/tags",
    )
    assert array_diagnostics(suite) == []


def test_optional_array_inside_an_absent_optional_parent_gets_bound_cases() -> None:
    suite = element_suite(absent_body({"p": tags_parent()}))

    assert suite.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/p/tags", constraint, {"p": {"tags": value}}) for constraint, value in ABSENT_MINMAX
    ]
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize("required_top", [(), ("p",)])
def test_several_nested_absent_optional_parents_are_created_outermost_first(
    required_top: tuple[str, ...]
) -> None:
    deepest = tags_parent()
    nested = absent_body({"q": absent_body({"r": deepest})})

    suite = element_suite(absent_body({"p": nested}, required_top))

    assert [value[:2] for value in array_values(suite)] == [
        ("/p/q/r/tags", "minItems"),
        ("/p/q/r/tags", "maxItems"),
    ]
    assert array_values(suite)[0][2] == {"p": {"q": {"r": {"tags": ["x"]}}}}
    assert suite.cases[0].request_body.value == ({"p": {}} if required_top else {})  # type: ignore[union-attr]
    assert array_diagnostics(suite) == []


def test_parent_value_keeps_its_required_sibling_properties() -> None:
    parent = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "integer"}, "tags": ELEMENT_TAGS},
    }

    suite = element_suite(absent_body({"p": parent}))

    assert array_values(suite)[0] == ("/p/tags", "minItems", {"p": {"id": 0, "tags": ["x"]}})
    assert array_values(suite)[1][2] == {"p": {"id": 0, "tags": ["x", "x", "x", "x"]}}


def test_absent_parent_resolved_through_a_reference() -> None:
    suite = element_suite(
        absent_body({"p": schema_reference("Parent")}), {"Parent": tags_parent()}
    )

    assert array_values(suite) == [
        ("/p/tags", constraint, {"p": {"tags": value}}) for constraint, value in ABSENT_MINMAX
    ]
    assert any("components/schemas/Parent" in pointer for pointer in array_cases(suite)[0].provenance)
    assert array_diagnostics(suite) == []


def absent_reference_result() -> NormalizationResult:
    body = absent_body({"a": ELEMENT_TAGS, "p": schema_reference("Parent")}, ("a",))
    return make_result_with_schemas(
        post_endpoint(body),
        {"Parent": absent_body({"tags": schema_reference("Tags")}), "Tags": ELEMENT_TAGS},
    )


def test_absent_parent_references_are_charged_and_an_expansion_limit_drops_only_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hops = count_expansions(absent_reference_result())
    reference = generate_test_suite(absent_reference_result())
    assert [value[0] for value in array_values(reference)] == ["/a", "/a", "/p/tags", "/p/tags"]

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops)
    assert generate_test_suite(absent_reference_result()).cases == reference.cases

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops - 1)
    suite = generate_test_suite(absent_reference_result())

    assert [value[0] for value in array_values(suite)] == ["/a", "/a"]
    assert suite.cases == reference.cases[:-2]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    message = suite.diagnostics[0].message
    assert "per-endpoint limit" in message and "absent optional parent objects" in message
    assert generate_test_suite(absent_reference_result()) == suite


def test_absent_parent_inside_element_zero_of_an_array() -> None:
    item = absent_body({"p": tags_parent()})

    suite = element_suite(element_rows(item))

    assert array_values(suite) == [
        ("/rows", "minItems", {"rows": []}),
        ("/rows/0/p/tags", "minItems", {"rows": [{"p": {"tags": ["x"]}}]}),
        ("/rows/0/p/tags", "maxItems", {"rows": [{"p": {"tags": ["x"] * 4}}]}),
    ]
    assert suite.cases[0].request_body.value == {"rows": [{}]}  # type: ignore[union-attr]
    assert array_diagnostics(suite) == []


def test_absent_parent_inside_a_nested_array() -> None:
    item = absent_body({"p": tags_parent()})
    inner = {"type": "array", "minItems": 1, "items": item}

    suite = element_suite({"type": "array", "minItems": 1, "items": inner})

    assert [value for value in array_values(suite) if value[0] == "/0/0/p/tags"] == [
        ("/0/0/p/tags", "minItems", [[{"p": {"tags": ["x"]}}]]),
        ("/0/0/p/tags", "maxItems", [[{"p": {"tags": ["x"] * 4}}]]),
    ]


def test_read_only_parent_is_not_created_and_an_ordinary_object_gets_no_diagnostic() -> None:
    suite = element_suite(
        absent_body(
            {
                "ro": tags_parent(readOnly=True),
                "plain": absent_body({"name": {"type": "string"}, "list": {"type": "array", "items": ITEM_SCHEMA}}),
                "broken": absent_body({"s": {"type": "string", "pattern": "(a|b)"}}, ("s",)),
            }
        )
    )

    assert array_values(suite) == []
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_parent_literal_blocks_creating_it_with_one_diagnostic(keyword: str) -> None:
    literal = {"tags": ["x", "x"]}
    parent = tags_parent(**{keyword: literal if keyword == "const" else [literal]})

    suite = element_suite(absent_body({"p": parent}))

    assert array_values(suite) == []
    literal_pointer = f"{ABSENT_PROPERTIES}/p/{keyword}"
    assert array_diagnostics(suite) == [("array_bound_no_safe_candidate", literal_pointer)]
    message = suite.diagnostics[0].message
    assert "'/p'" in message and literal_pointer in message and "violate the literal" in message


def test_unsupported_required_sibling_prevents_creating_the_parent_and_is_reported_once() -> None:
    parent = {
        "type": "object",
        "required": ["s"],
        "properties": {"s": {"type": "string", "pattern": "(a|b)"}, "tags": ELEMENT_TAGS},
    }

    suite = element_suite(absent_body({"p": parent}))

    assert array_values(suite) == []
    assert array_diagnostics(suite) == [
        ("array_bound_unsupported_schema", f"{ABSENT_PROPERTIES}/p/properties/s/pattern")
    ]


def test_recursive_optional_schemas_stop_at_the_first_repeated_schema() -> None:
    node = absent_body({"tags": ELEMENT_TAGS, "child": schema_reference("Node")})

    def build() -> GeneratedTestSuite:
        return element_suite(absent_body({"node": schema_reference("Node")}), {"Node": node})

    suite = build()

    assert [value[:2] for value in array_values(suite)] == [
        ("/node/tags", "minItems"),
        ("/node/tags", "maxItems"),
    ]
    assert build() == suite


def absent_with_existing_body() -> dict[str, object]:
    return absent_body(
        {
            "a": ELEMENT_TAGS,
            "rows": {"type": "array", "minItems": 1, "items": ELEMENT_ITEM},
            "p": tags_parent(),
        },
        ("a", "rows"),
    )


def test_new_cases_are_appended_after_existing_and_element_cases() -> None:
    suite = element_suite(absent_with_existing_body())

    assert [value[:2] for value in array_values(suite)] == [
        ("/a", "minItems"),
        ("/a", "maxItems"),
        ("/rows", "minItems"),
        ("/rows/0/tags", "minItems"),
        ("/rows/0/tags", "maxItems"),
        ("/p/tags", "minItems"),
        ("/p/tags", "maxItems"),
    ]
    assert array_cases(suite)[-1] is suite.cases[-1]


def test_existing_case_ids_and_order_do_not_change_when_absent_parents_exist() -> None:
    without = absent_with_existing_body()
    del without["properties"]["p"]  # type: ignore[attr-defined]
    old = element_suite(without)
    new = element_suite(absent_with_existing_body())

    assert [case.case_id for case in new.cases[: len(old.cases)]] == [case.case_id for case in old.cases]
    assert new.cases[: len(old.cases)] == old.cases
    assert [case.case_id for case in new.cases[len(old.cases) :]] == [
        build_case_id("negative", "/items", "post", "array-bound:/p/tags:minItems:1"),
        build_case_id("negative", "/items", "post", "array-bound:/p/tags:maxItems:4"),
    ]
    assert len({case.case_id for case in new.cases}) == len(new.cases)


def test_created_parent_values_are_isolated_between_cases() -> None:
    parent = {
        "type": "object",
        "required": ["id", "obj"],
        "properties": {"id": {"type": "integer"}, "obj": {"type": "object"}, "tags": ELEMENT_TAGS},
    }
    result = make_result(post_endpoint(absent_body({"p": parent})))
    before_input = result.model_dump_json()
    suite = generate_test_suite(result)
    source = next(case for case in suite.cases if case.violated_constraint == "minItems")
    before = [case.model_dump(mode="json") for case in suite.cases]

    created = body_value(source)["p"]  # type: ignore[index]
    created["id"] = "mutated"
    created["obj"]["added"] = True
    created["tags"].append("mutated")

    after = [case.model_dump(mode="json") for case in suite.cases]
    index = suite.cases.index(source)
    assert after[index] != before[index]
    assert [item for i, item in enumerate(after) if i != index] == [
        item for i, item in enumerate(before) if i != index
    ]
    assert result.model_dump_json() == before_input


def budget_suite(endpoints: int = 1) -> GeneratedTestSuite:
    result = make_result(
        *(
            post_endpoint(absent_with_existing_body(), path=f"/items{index}")
            for index in range(endpoints)
        )
    )
    return generate_test_suite(result)


@pytest.mark.parametrize("scope", ["endpoint", "suite"])
def test_case_limit_drops_only_the_new_group_and_keeps_existing_array_cases(
    scope: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = budget_suite()
    new_count = 2
    existing = reference.cases[:-new_count]
    name = "_MAX_ENDPOINT_CASES" if scope == "endpoint" else "_MAX_SUITE_CASES"
    monkeypatch.setattr(generator_module, name, len(reference.cases) - 1)

    suite = budget_suite()

    assert suite.cases == existing
    assert [value[0] for value in array_values(suite)] == ["/a", "/a", "/rows", "/rows/0/tags", "/rows/0/tags"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    message = suite.diagnostics[0].message
    assert f"per-{scope} limit" in message and "absent optional parent objects" in message
    assert "other array-bound cases" in message
    assert budget_suite() == suite


def test_later_endpoints_continue_after_an_endpoint_scope_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = budget_suite()
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", len(single.cases) - 1)

    suite = budget_suite(endpoints=3)

    assert len(suite.cases) == 3 * (len(single.cases) - 2)
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"] * 3
    assert [item.endpoint_path for item in suite.diagnostics] == ["/items0", "/items1", "/items2"]
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)


def test_suite_scope_expansion_limit_keeps_the_existing_cases_and_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hops = count_expansions(absent_reference_result())
    monkeypatch.setattr(generator_module, "_MAX_SUITE_EXPANSIONS", hops - 1)

    suite = generate_test_suite(absent_reference_result())

    assert [value[0] for value in array_values(suite)] == ["/a", "/a"]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert "per-suite limit" in suite.diagnostics[0].message
    assert generate_test_suite(absent_reference_result()) == suite


def test_absent_parent_generation_is_deterministic() -> None:
    first = element_suite(absent_with_existing_body())
    second = element_suite(absent_with_existing_body())

    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in first.cases] == [case.case_id for case in second.cases]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_generate_absent_parent_cases(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": absent_body({"p": absent_body({"q": tags_parent()})})
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert array_values(suite) == [
        ("/p/q/tags", "minItems", {"p": {"q": {"tags": ["x"]}}}),
        ("/p/q/tags", "maxItems", {"p": {"q": {"tags": ["x"] * 4}}}),
    ]
    assert suite.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert array_diagnostics(suite) == []


def test_parent_whose_only_array_is_below_a_read_only_object_is_not_created() -> None:
    inner = absent_body({"tags": ELEMENT_TAGS})
    inner["readOnly"] = True

    broken = {"s": {"type": "string", "pattern": "(a|b)"}}
    suite = element_suite(absent_body({"p": absent_body({"inner": inner, **broken}, ("s",))}))

    assert array_values(suite) == []
    assert array_diagnostics(suite) == []


def test_case_counters_are_restored_after_the_new_group_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = budget_suite()
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", len(reference.cases) - 1)
    restored: list[tuple[tuple[int, int], tuple[int, int]]] = []
    original = generator_module._GenerationBudget.restore_case_counts

    def spy(self: object, counts: tuple[int, int]) -> None:
        original(self, counts)  # type: ignore[arg-type]
        restored.append((counts, self.case_counts()))  # type: ignore[attr-defined]

    monkeypatch.setattr(generator_module._GenerationBudget, "restore_case_counts", spy)

    suite = budget_suite()

    assert restored[-1][0] == restored[-1][1]
    assert restored[-1][0][0] == len(suite.cases)


def test_suite_limit_hit_in_the_new_group_lets_a_later_endpoint_use_the_rest_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    per_endpoint = len(budget_suite().cases)
    new_count = 2
    # Endpoint 0 fits completely. Endpoint 1 reaches the limit inside the new group.
    # After the group is rolled back, one case of budget is left for endpoint 2.
    limit = 2 * per_endpoint - new_count + 1
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", limit)

    suite = budget_suite(endpoints=4)

    def counts_by_path(value: GeneratedTestSuite) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in value.cases:
            counts[case.path] = counts.get(case.path, 0) + 1
        return counts

    # Endpoint 1 keeps everything before the new group; endpoint 2 gets the last case;
    # endpoint 3 is never generated. The limit is not exceeded.
    assert counts_by_path(suite) == {
        "/items0": per_endpoint,
        "/items1": per_endpoint - new_count,
        "/items2": 1,
    }
    assert len(suite.cases) == limit
    assert [case.path for case in suite.cases] == sorted(
        (case.path for case in suite.cases), key=lambda path: path
    )
    new_cases = [case for case in array_cases(suite) if case.affected_property == "/p/tags"]
    assert [case.path for case in new_cases] == ["/items0", "/items0"]

    assert [(item.code, item.endpoint_path) for item in suite.diagnostics] == [
        ("generation_case_budget_exceeded", "/items1"),
        ("generation_case_budget_exceeded", "/items2"),
    ]
    dropped, normal = (item.message for item in suite.diagnostics)
    assert "absent optional parent objects" in dropped
    assert "per-suite limit" in dropped
    assert "absent optional parent objects" not in normal
    assert f"per-suite limit of {limit} generated test cases" in normal
    assert "1 following endpoint(s) were not generated" in normal

    assert budget_suite(endpoints=4) == suite
    assert budget_suite(endpoints=4).model_dump_json() == suite.model_dump_json()


# --- Step 8.31: array bounds below the items of present empty arrays -------------

def empty_obj(properties: dict[str, object], required: tuple[str, ...] = ()) -> dict[str, object]:
    return {"type": "object", "required": list(required), "properties": properties}


def empty_arr(items: object, **options: object) -> dict[str, object]:
    return {"type": "array", "items": items, **options}


TAGS_ITEM = empty_obj({"tags": ELEMENT_TAGS})


def test_empty_root_array_gets_nested_bound_cases_in_the_negative_body_only() -> None:
    suite = element_suite(empty_arr(TAGS_ITEM))

    assert suite.cases[0].request_body.value == []  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/0/tags", "minItems", [{"tags": ["x"]}]),
        ("/0/tags", "maxItems", [{"tags": ["x"] * 4}]),
    ]
    assert [case.case_id for case in array_cases(suite)] == [
        build_case_id("negative", "/items", "post", "array-bound:/0/tags:minItems:1"),
        build_case_id("negative", "/items", "post", "array-bound:/0/tags:maxItems:4"),
    ]
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize("keyword", ["enum", "const"])
def test_root_literal_that_rejects_the_final_body_blocks_each_case(keyword: str) -> None:
    schema = empty_arr(TAGS_ITEM, **{keyword: [[]] if keyword == "enum" else []})

    suite = element_suite(schema)

    assert array_values(suite) == []
    pointer = f"{ARRAY_BODY_POINTER}/{keyword}"
    assert array_diagnostics(suite) == [("array_bound_no_safe_candidate", pointer)] * 2
    assert [item.message.split(" case ")[0] for item in suite.diagnostics] == [
        "The minItems",
        "The maxItems",
    ]


def test_root_literal_that_accepts_the_final_body_keeps_only_that_case() -> None:
    # The positive body [] is the first member. The second member is the exact body
    # of the minItems case, although it violates the items' own minItems.
    schema = empty_arr(TAGS_ITEM, enum=[[], [{"tags": ["x"]}]])

    suite = element_suite(schema)

    assert suite.cases[0].request_body.value == []  # type: ignore[union-attr]
    assert array_values(suite) == [("/0/tags", "minItems", [{"tags": ["x"]}])]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/enum")
    ]
    assert "maxItems" in suite.diagnostics[0].message


def test_literal_accepting_the_final_body_while_the_positive_body_is_valid() -> None:
    members = [{"rows": []}, {"rows": [{"tags": ["x"]}]}]
    schema = {**empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), "enum": members}

    suite = element_suite(schema)

    positive = suite.cases[0].request_body.value  # type: ignore[union-attr]
    assert positive == members[0]
    # The positive body satisfies the whole schema; the second member does not.
    assert generator_module._matches(schema, positive, "3.1.1", "request", 0, "/")
    assert not generator_module._matches(schema, members[1], "3.1.1", "request", 0, "/")
    assert array_values(suite) == [("/rows/0/tags", "minItems", members[1])]
    assert len(members[1]["rows"][0]["tags"]) < ELEMENT_TAGS["minItems"]  # type: ignore[index, operator]
    assert array_diagnostics(suite) == [
        ("negative_case_ancestor_literal_conflict", f"{ARRAY_BODY_POINTER}/enum")
    ]


def test_empty_array_then_absent_parent_then_empty_array_scenario_n() -> None:
    parent = empty_obj({"list": empty_arr(TAGS_ITEM)}, ("list",))
    body = empty_obj({"rows": empty_arr(empty_obj({"p": parent}))}, ("rows",))

    suite = element_suite(body)

    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/rows/0/p/list/0/tags", "minItems", {"rows": [{"p": {"list": [{"tags": ["x"]}]}}]}),
        ("/rows/0/p/list/0/tags", "maxItems", {"rows": [{"p": {"list": [{"tags": ["x"] * 4}]}}]}),
    ]
    assert array_diagnostics(suite) == []


def test_empty_array_then_empty_array_then_absent_parent_scenario_m() -> None:
    inner = empty_arr(empty_obj({"q": empty_obj({"tags": ELEMENT_TAGS})}))
    body = empty_obj({"rows": empty_arr(empty_obj({"inner": inner}, ("inner",)))}, ("rows",))

    suite = element_suite(body)

    assert array_values(suite) == [
        ("/rows/0/inner/0/q/tags", "minItems", {"rows": [{"inner": [{"q": {"tags": ["x"]}}]}]}),
        ("/rows/0/inner/0/q/tags", "maxItems", {"rows": [{"inner": [{"q": {"tags": ["x"] * 4}}]}]}),
    ]


def test_two_alternating_levels_of_empty_arrays_and_absent_parents_scenario_n2() -> None:
    level_two = empty_obj({"list2": empty_arr(TAGS_ITEM)}, ("list2",))
    level_one = empty_obj({"list": empty_arr(empty_obj({"q": level_two}))}, ("list",))
    body = empty_obj({"rows": empty_arr(empty_obj({"p": level_one}))}, ("rows",))

    suite = element_suite(body)

    assert array_values(suite)[0] == (
        "/rows/0/p/list/0/q/list2/0/tags",
        "minItems",
        {"rows": [{"p": {"list": [{"q": {"list2": [{"tags": ["x"]}]}}]}}]},
    )
    assert [value[:2] for value in array_values(suite)] == [
        ("/rows/0/p/list/0/q/list2/0/tags", "minItems"),
        ("/rows/0/p/list/0/q/list2/0/tags", "maxItems"),
    ]
    assert array_diagnostics(suite) == []


def test_recursive_reference_schema_terminates_with_unique_deterministic_cases() -> None:
    node = empty_obj(
        {
            "tags": ELEMENT_TAGS,
            "kids": empty_arr(schema_reference("Node")),
            "child": schema_reference("Node"),
        },
        ("kids",),
    )
    body = empty_obj({"root": schema_reference("Node")}, ("root",))

    first = element_suite(body, {"Node": node})
    second = element_suite(body, {"Node": node})

    # The first four cases are the existing phases; the creation of the elements
    # stops at the repeated "kids" schema, so the inner "kids" stay empty.
    deep = [", ".join(map(str, value[:2])) for value in array_values(first)]
    assert deep[-6:] == [
        "/root/kids/0/tags, minItems",
        "/root/kids/0/tags, maxItems",
        "/root/child/kids/0/tags, minItems",
        "/root/child/kids/0/tags, maxItems",
        "/root/kids/0/child/tags, minItems",
        "/root/kids/0/child/tags, maxItems",
    ]
    assert array_values(first)[-6][2] == {"root": {"kids": [{"kids": [], "tags": ["x"]}]}}
    assert array_values(first)[-2][2] == {
        "root": {"kids": [{"kids": [], "child": {"kids": [], "tags": ["x"]}}]}
    }
    ids = [case.case_id for case in first.cases]
    assert len(ids) == len(set(ids))
    assert first == second


def test_empty_array_inside_a_phase_3_created_parent_gets_its_own_insertions() -> None:
    body = empty_obj({"p": empty_obj({"tags": ELEMENT_TAGS, "rows": empty_arr(TAGS_ITEM)}, ("rows",))})

    suite = element_suite(body)

    assert suite.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/p/tags", "minItems", {"p": {"rows": [], "tags": ["x"]}}),
        ("/p/tags", "maxItems", {"p": {"rows": [], "tags": ["x"] * 4}}),
        ("/p/rows/0/tags", "minItems", {"p": {"rows": [{"tags": ["x"]}]}}),
        ("/p/rows/0/tags", "maxItems", {"p": {"rows": [{"tags": ["x"] * 4}]}}),
    ]
    assert array_diagnostics(suite) == []


def test_absent_parent_inside_an_inserted_element_is_created() -> None:
    item = empty_obj({"q": empty_obj({"tags": ELEMENT_TAGS})})

    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)))

    assert array_values(suite) == [
        ("/rows/0/q/tags", "minItems", {"rows": [{"q": {"tags": ["x"]}}]}),
        ("/rows/0/q/tags", "maxItems", {"rows": [{"q": {"tags": ["x"] * 4}}]}),
    ]


def test_independent_empty_arrays_produce_separate_cases_with_only_their_own_element() -> None:
    suite = element_suite(empty_obj({"a": empty_arr(TAGS_ITEM), "b": empty_arr(TAGS_ITEM)}, ("a", "b")))

    assert array_values(suite) == [
        ("/a/0/tags", "minItems", {"a": [{"tags": ["x"]}], "b": []}),
        ("/a/0/tags", "maxItems", {"a": [{"tags": ["x"] * 4}], "b": []}),
        ("/b/0/tags", "minItems", {"a": [], "b": [{"tags": ["x"]}]}),
        ("/b/0/tags", "maxItems", {"a": [], "b": [{"tags": ["x"] * 4}]}),
    ]


def test_required_siblings_of_the_element_and_nested_arrays() -> None:
    sibling_item = empty_obj({"id": {"type": "integer"}, "tags": ELEMENT_TAGS}, ("id",))
    siblings = element_suite(empty_obj({"rows": empty_arr(sibling_item)}, ("rows",)))
    nested = element_suite(empty_obj({"rows": empty_arr(empty_arr(TAGS_ITEM))}, ("rows",)))

    assert array_values(siblings)[0] == ("/rows/0/tags", "minItems", {"rows": [{"id": 0, "tags": ["x"]}]})
    assert array_values(siblings)[1][2] == {"rows": [{"id": 0, "tags": ["x"] * 4}]}
    assert array_values(nested) == [
        ("/rows/0/0/tags", "minItems", {"rows": [[{"tags": ["x"]}]]}),
        ("/rows/0/0/tags", "maxItems", {"rows": [[{"tags": ["x"] * 4}]]}),
    ]


def test_items_resolved_through_a_reference() -> None:
    suite = element_suite(
        empty_obj({"rows": empty_arr(schema_reference("Item"))}, ("rows",)), {"Item": TAGS_ITEM}
    )

    assert array_values(suite) == [
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x"] * 4}]}),
    ]
    assert any("components/schemas/Item" in pointer for pointer in array_cases(suite)[0].provenance)


def literal_case(body: object, schemas: dict[str, object] | None = None) -> tuple[list[object], list[tuple[str, str]]]:
    suite = element_suite(body, schemas)
    return array_values(suite), [(item.code, item.source_pointer) for item in suite.diagnostics if item.code.startswith("array_") or is_array_literal_conflict(item)]


# "outer" is an array whose only literal member holds the empty array "rows".
ENCLOSING_ARRAY_BODY = empty_obj(
    {
        "outer": empty_arr(
            empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), enum=[[{"rows": []}]]
        )
    },
    ("outer",),
)


@pytest.mark.parametrize(
    ("name", "body", "schemas", "literal_pointer"),
    [
        (
            "parent object",
            empty_obj(
                {"o": {**empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), "enum": [{"rows": []}]}},
                ("o",),
            ),
            None,
            f"{ARRAY_BODY_POINTER}/properties/o/enum",
        ),
        (
            "enclosing array",
            ENCLOSING_ARRAY_BODY,
            None,
            f"{ARRAY_BODY_POINTER}/properties/outer/enum",
        ),
        (
            "items schema",
            empty_obj(
                {"rows": empty_arr({**TAGS_ITEM, "enum": [{"tags": ["a", "b"]}]})}, ("rows",)
            ),
            None,
            f"{ARRAY_BODY_POINTER}/properties/rows/items/enum",
        ),
        (
            "reference-resolved ancestor",
            empty_obj({"w": schema_reference("W")}, ("w",)),
            {"W": {**empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), "enum": [{"rows": []}]}},
            "/components/schemas/W/enum",
        ),
    ],
)
def test_ancestor_literals_that_reject_the_final_body_block_both_cases(
    name: str, body: object, schemas: dict[str, object] | None, literal_pointer: str
) -> None:
    values, diagnostics = literal_case(body, schemas)

    # Object ancestors use the shared code; an enclosing array or items literal keeps its own.
    code = (
        "array_bound_no_safe_candidate"
        if name in ("enclosing array", "items schema")
        else CONFLICT_CODE
    )
    assert values == [], name
    assert diagnostics == [(code, literal_pointer)] * 2


def test_enclosing_array_literal_scenario_has_a_valid_positive_body_and_blocks_the_created_element() -> None:
    suite = element_suite(ENCLOSING_ARRAY_BODY)

    positive = suite.cases[0].request_body.value  # type: ignore[union-attr]
    assert positive == {"outer": [{"rows": []}]}
    assert positive["outer"] in ENCLOSING_ARRAY_BODY["properties"]["outer"]["enum"]  # type: ignore[index]
    assert generator_module._matches(ENCLOSING_ARRAY_BODY, positive, "3.1.1", "request", 0, "/")
    # The element is created below "outer/0/rows", so the body would leave the literal.
    assert array_values(suite) == []
    assert [(item.code, item.source_pointer) for item in suite.diagnostics if item.code.startswith("array_")] == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/properties/outer/enum")
    ] * 2
    assert all("'/outer/0/rows/0/tags'" in item.message for item in suite.diagnostics if item.code.startswith("array_"))


def test_literal_on_the_target_array_that_accepts_its_member_keeps_the_case() -> None:
    tags = {**ELEMENT_TAGS, "enum": [["a", "b"], ["a"]]}
    item = empty_obj({"tags": tags}, ("tags",))

    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)))

    # The enum member ["a"] violates only minItems, so the target's literal accepts it.
    assert array_values(suite) == [("/rows/0/tags", "minItems", {"rows": [{"tags": ["a"]}]})]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/properties/rows/items/properties/tags/maxItems")
    ]


def test_max_items_zero_gets_one_diagnostic_when_the_items_have_array_bounds() -> None:
    suite = element_suite(empty_obj({"rows": empty_arr(TAGS_ITEM, maxItems=0)}, ("rows",)))

    assert [value[0] for value in array_values(suite)] == ["/rows"]  # the existing maxItems case
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ARRAY_BODY_POINTER}/properties/rows/maxItems")
    ]
    assert "maxItems is 0" in suite.diagnostics[0].message


def test_max_items_zero_without_nested_array_bounds_gives_no_diagnostic() -> None:
    plain = empty_arr(empty_obj({"name": {"type": "string"}, "list": empty_arr({"type": "string"})}), maxItems=0)

    suite = element_suite(empty_obj({"rows": plain}, ("rows",)))

    assert [value[0] for value in array_values(suite)] == ["/rows"]
    assert array_diagnostics(suite) == []


def test_empty_array_without_nested_array_bounds_creates_nothing_and_reports_nothing() -> None:
    item = empty_obj({"n": {"type": "string"}, "l": empty_arr({"type": "string"})})

    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)))

    assert array_values(suite) == []
    assert array_diagnostics(suite) == []


def test_unsupported_required_sibling_of_the_element_is_reported_once() -> None:
    item = empty_obj({"s": {"type": "string", "pattern": "(a|b)"}, "tags": ELEMENT_TAGS}, ("s",))

    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)))

    assert array_values(suite) == []
    assert array_diagnostics(suite) == [
        ("array_bound_unsupported_schema", f"{ARRAY_BODY_POINTER}/properties/rows/items/properties/s/pattern")
    ]


def phase4_body() -> dict[str, object]:
    # Phase 3 creates "p" for its tags; "p.rows" is then empty, which is phase 4.
    return empty_obj(
        {"p": empty_obj({"tags": ELEMENT_TAGS, "rows": empty_arr(TAGS_ITEM)}, ("rows",))}
    )


def phase4_suite(endpoints: int = 1) -> GeneratedTestSuite:
    return generate_test_suite(
        make_result(*(post_endpoint(phase4_body(), path=f"/items{index}") for index in range(endpoints)))
    )


def test_phase_3_rollback_drops_the_empty_arrays_it_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = phase4_suite()
    total = len(reference.cases)
    assert [value[0] for value in array_values(reference)] == ["/p/tags"] * 2 + ["/p/rows/0/tags"] * 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", total - 3)

    suite = phase4_suite()

    # Phase 3 needs two cases but only one fits: its group is dropped. The empty
    # array it created must not be processed by phase 4 afterwards.
    assert array_values(suite) == []
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "absent optional parent objects" in suite.diagnostics[0].message
    assert suite.cases == reference.cases[: total - 4]


@pytest.mark.parametrize("scope", ["endpoint", "suite"])
def test_phase_4_case_limit_drops_only_its_group_and_keeps_phase_3(
    scope: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = phase4_suite()
    total = len(reference.cases)
    name = "_MAX_ENDPOINT_CASES" if scope == "endpoint" else "_MAX_SUITE_CASES"
    monkeypatch.setattr(generator_module, name, total - 1)

    suite = phase4_suite()

    assert suite.cases == reference.cases[: total - 2]
    assert [value[0] for value in array_values(suite)] == ["/p/tags", "/p/tags"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    message = suite.diagnostics[0].message
    assert f"per-{scope} limit of {total - 1}" in message
    assert "elements of present empty arrays" in message and "other array-bound cases" in message
    assert phase4_suite() == suite


def test_phase_4_rollback_restores_the_case_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    total = len(phase4_suite().cases)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", total - 1)
    restored: list[tuple[object, object]] = []
    original = generator_module._GenerationBudget.restore_case_counts

    def spy(self: object, counts: tuple[int, int]) -> None:
        original(self, counts)  # type: ignore[arg-type]
        restored.append((counts, self.case_counts()))  # type: ignore[attr-defined]

    monkeypatch.setattr(generator_module._GenerationBudget, "restore_case_counts", spy)

    suite = phase4_suite()

    assert restored[-1][0] == restored[-1][1]
    assert restored[-1][0][0] == len(suite.cases)  # type: ignore[index]


def test_suite_limit_hit_in_phase_4_lets_later_endpoints_use_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    per_endpoint = len(phase4_suite().cases)
    limit = 2 * per_endpoint - 2 + 1
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", limit)

    suite = phase4_suite(endpoints=4)

    counts: dict[str, int] = {}
    for case in suite.cases:
        counts[case.path] = counts.get(case.path, 0) + 1
    assert counts == {"/items0": per_endpoint, "/items1": per_endpoint - 2, "/items2": 1}
    assert len(suite.cases) == limit
    assert [(item.code, item.endpoint_path) for item in suite.diagnostics] == [
        ("generation_case_budget_exceeded", "/items1"),
        ("generation_case_budget_exceeded", "/items2"),
    ]
    dropped, normal = (item.message for item in suite.diagnostics)
    assert "elements of present empty arrays" in dropped
    assert "elements of present empty arrays" not in normal
    assert "1 following endpoint(s) were not generated" in normal
    assert phase4_suite(endpoints=4) == suite


def test_endpoint_limit_drop_in_phase_4_lets_later_endpoints_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    per_endpoint = len(phase4_suite().cases)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", per_endpoint - 1)

    suite = phase4_suite(endpoints=3)

    assert len(suite.cases) == 3 * (per_endpoint - 2)
    assert [item.endpoint_path for item in suite.diagnostics] == ["/items0", "/items1", "/items2"]
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)


def expansion_result() -> NormalizationResult:
    body = empty_obj({"rows": empty_arr(schema_reference("I"))}, ("rows",))
    return make_result_with_schemas(
        post_endpoint(body),
        {
            "I": empty_obj({"p": schema_reference("P")}),
            "P": empty_obj({"list": empty_arr(schema_reference("J"))}, ("list",)),
            "J": empty_obj({"tags": schema_reference("Tags")}),
            "Tags": ELEMENT_TAGS,
        },
    )


def test_expansion_limit_in_a_later_worklist_round_drops_the_group_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hops = count_expansions(expansion_result())
    reference = generate_test_suite(expansion_result())
    assert [value[0] for value in array_values(reference)] == ["/rows/0/p/list/0/tags"] * 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops)
    assert generate_test_suite(expansion_result()).cases == reference.cases

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", hops - 1)
    suite = generate_test_suite(expansion_result())

    assert suite.cases == reference.cases[:-2]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    # "J" is only reached after the second round created "p" and processed "list".
    assert suite.diagnostics[0].source_pointer == "/components/schemas/J/properties/tags"
    assert "elements of present empty arrays" in suite.diagnostics[0].message
    assert generate_test_suite(expansion_result()) == suite


def test_created_elements_and_positive_bodies_are_isolated_between_cases() -> None:
    example = {"meta": {"k": 1}, "tags": ["a", "b"]}
    item = {
        **empty_obj({"meta": {"type": "object"}, "tags": ELEMENT_TAGS}, ("meta", "tags")),
        "example": example,
    }
    result = make_result(post_endpoint(empty_obj({"rows": empty_arr(item)}, ("rows",))))
    before_input = result.model_dump_json()
    suite = generate_test_suite(result)
    cases = [case for case in array_cases(suite) if case.affected_property == "/rows/0/tags"]
    assert [case.violated_constraint for case in cases] == ["minItems", "maxItems"]
    before = [case.model_dump(mode="json") for case in suite.cases]

    created = body_value(cases[0])["rows"][0]  # type: ignore[index]
    created["meta"]["k"] = "mutated"
    created["tags"].append("mutated")

    after = [case.model_dump(mode="json") for case in suite.cases]
    index = suite.cases.index(cases[0])
    assert after[index] != before[index]
    assert [x for i, x in enumerate(after) if i != index] == [x for i, x in enumerate(before) if i != index]
    assert example == {"meta": {"k": 1}, "tags": ["a", "b"]}
    assert result.model_dump_json() == before_input
    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]


def test_existing_case_ids_and_order_do_not_change_and_phase_4_cases_come_last() -> None:
    plain_item = empty_obj({"name": {"type": "string"}})

    def build(item: object) -> GeneratedTestSuite:
        return element_suite(
            empty_obj({"a": ELEMENT_TAGS, "rows": empty_arr(item)}, ("a", "rows"))
        )

    old = build(plain_item)
    new = build(TAGS_ITEM)

    assert [case.case_id for case in new.cases[:-2]] == [case.case_id for case in old.cases]
    assert [case.case_id for case in new.cases[-2:]] == [
        build_case_id("negative", "/items", "post", "array-bound:/rows/0/tags:minItems:1"),
        build_case_id("negative", "/items", "post", "array-bound:/rows/0/tags:maxItems:4"),
    ]
    assert [case.violated_constraint for case in new.cases[-2:]] == ["minItems", "maxItems"]
    assert len({case.case_id for case in new.cases}) == len(new.cases)


def test_phase_4_generation_is_deterministic() -> None:
    level_two = empty_obj({"list2": empty_arr(TAGS_ITEM)}, ("list2",))
    level_one = empty_obj({"list": empty_arr(empty_obj({"q": level_two}))}, ("list",))
    body = empty_obj(
        {"rows": empty_arr(empty_obj({"p": level_one})), "other": empty_arr(TAGS_ITEM)},
        ("rows", "other"),
    )

    first, second = element_suite(body), element_suite(body)

    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in first.cases] == [case.case_id for case in second.cases]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_documents_generate_cases_below_empty_arrays(version: str) -> None:
    from app.services.openapi import normalize_openapi_document

    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",))
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]
    assert array_values(suite) == [
        ("/rows/0/tags", "minItems", {"rows": [{"tags": ["x"]}]}),
        ("/rows/0/tags", "maxItems", {"rows": [{"tags": ["x"] * 4}]}),
    ]
    assert array_diagnostics(suite) == []


def test_non_empty_array_inside_an_inserted_element_is_visited_too() -> None:
    item = empty_obj({"list": empty_arr(TAGS_ITEM, minItems=1)}, ("list",))

    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)))

    # The element's own array bound and, through its element 0, the nested target.
    assert array_values(suite) == [
        ("/rows/0/list", "minItems", {"rows": [{"list": []}]}),
        ("/rows/0/list/0/tags", "minItems", {"rows": [{"list": [{"tags": ["x"]}]}]}),
        ("/rows/0/list/0/tags", "maxItems", {"rows": [{"list": [{"tags": ["x"] * 4}]}]}),
    ]
    assert array_diagnostics(suite) == []


def pattern_phase4_body() -> dict[str, object]:
    code = {"type": "string", "pattern": "^[a-z]+$"}
    # A different pattern, so that the memo of the "code" evaluations cannot answer it.
    element = empty_obj({"s": {"type": "string", "pattern": "^[a-z]{1,8}$"}, "tags": ELEMENT_TAGS}, ("s",))
    return empty_obj(
        {
            "a": ELEMENT_TAGS,  # phase 1 cases
            "code": code,  # pattern charges before phase 4
            "empty_zero": empty_arr(TAGS_ITEM, maxItems=0),  # a phase 4 diagnostic, first
            "p": empty_obj({"tags": ELEMENT_TAGS}),  # phase 3 cases
            "rows": empty_arr(element),  # the pattern is evaluated only here, in phase 4
        },
        ("a", "code", "empty_zero", "rows"),
    )


def pattern_phase4_suite() -> GeneratedTestSuite:
    return generate_test_suite(make_result(post_endpoint(pattern_phase4_body())))


def test_pattern_limit_reached_while_selecting_a_phase_4_element_drops_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from_phase4: list[bool] = []
    original_charge = generator_module._GenerationBudget.charge_pattern_evaluation

    def record(self: object, pointer: str | None, work: int) -> None:
        frame, names = sys._getframe(1), set()
        while frame is not None:
            names.add(frame.f_code.co_name)
            frame = frame.f_back  # type: ignore[assignment]
        from_phase4.append("process_empty" in names)
        original_charge(self, pointer, work)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module._GenerationBudget, "charge_pattern_evaluation", record)
    reference = pattern_phase4_suite()
    monkeypatch.undo()

    phase4 = [("/rows/0/tags", "minItems"), ("/rows/0/tags", "maxItems")]
    assert [value[:2] for value in array_values(reference)][-2:] == phase4
    assert [item.code for item in reference.diagnostics] == ["array_bound_no_safe_candidate"]
    # The first pattern evaluation of phase 4 comes after earlier ones ("code").
    before_phase4 = from_phase4.index(True)
    assert before_phase4 > 0 and not any(from_phase4[:before_phase4])
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", before_phase4)

    charges: list[bool] = []
    restored: list[tuple[tuple[int, int], tuple[int, int], int, int]] = []
    original_charge = generator_module._GenerationBudget.charge_pattern_evaluation
    original_restore = generator_module._GenerationBudget.restore_case_counts

    def count_charge(self: object, pointer: str | None, work: int) -> None:
        charges.append(True)
        original_charge(self, pointer, work)  # type: ignore[arg-type]

    def spy_restore(self: object, counts: tuple[int, int]) -> None:
        original_restore(self, counts)  # type: ignore[arg-type]
        restored.append(
            (
                counts,
                self.case_counts(),  # type: ignore[attr-defined]
                self.endpoint_pattern_evaluations,  # type: ignore[attr-defined]
                self.suite_pattern_evaluations,  # type: ignore[attr-defined]
            )
        )

    monkeypatch.setattr(generator_module._GenerationBudget, "charge_pattern_evaluation", count_charge)
    monkeypatch.setattr(generator_module._GenerationBudget, "restore_case_counts", spy_restore)

    suite = pattern_phase4_suite()

    # All phase 4 cases and its own diagnostic are gone; the earlier cases are unchanged.
    assert suite.cases == reference.cases[:-2]
    assert [value[0] for value in array_values(suite)] == ["/a", "/a", "/empty_zero", "/p/tags", "/p/tags"]
    assert [item.code for item in suite.diagnostics] == ["generation_pattern_budget_exceeded"]
    message = suite.diagnostics[0].message
    assert "per-endpoint limit" in message and "pattern evaluations" in message
    assert "elements of present empty arrays" in message
    # The case counters are restored; the pattern charges, including the failed one, stay.
    counts, after, endpoint_patterns, suite_patterns = restored[-1]
    assert counts == after and counts[0] == len(suite.cases)
    assert endpoint_patterns == suite_patterns == before_phase4 + 1
    assert len(charges) == before_phase4 + 1

    monkeypatch.undo()
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", before_phase4)
    assert pattern_phase4_suite() == suite
    assert pattern_phase4_suite().model_dump_json() == suite.model_dump_json()


CONST_ENUM_ROWS = empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",))
CONST_ENUM_FINAL = {"rows": [{"tags": ["x"]}]}  # the body of the minItems case


def test_const_and_enum_on_one_node_report_the_rejecting_const_when_enum_accepts() -> None:
    # The enum lists the exact final body of the minItems case, but the const (the
    # positive body) rejects it, so the const is the violated keyword.
    schema = {**CONST_ENUM_ROWS, "const": {"rows": []}, "enum": [{"rows": []}, CONST_ENUM_FINAL]}

    suite = element_suite(schema)

    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]
    assert array_values(suite) == []
    assert array_diagnostics(suite) == [
        ("negative_case_ancestor_literal_conflict", f"{ARRAY_BODY_POINTER}/const")
    ] * 2


def test_const_and_enum_on_one_node_that_both_reject_report_the_const_once_per_case() -> None:
    schema = {**CONST_ENUM_ROWS, "const": {"rows": []}, "enum": [{"rows": []}]}

    suite = element_suite(schema)

    assert array_values(suite) == []
    assert array_diagnostics(suite) == [
        ("negative_case_ancestor_literal_conflict", f"{ARRAY_BODY_POINTER}/const")
    ] * 2


@pytest.mark.parametrize(
    ("const", "members"),
    [
        (CONST_ENUM_FINAL, [CONST_ENUM_FINAL]),  # const == final body, enum agrees
        (CONST_ENUM_FINAL, [{"rows": []}]),  # const == final body, enum rejects it
        ({"rows": []}, [CONST_ENUM_FINAL]),  # const == positive body, enum rejects it
    ],
)
def test_const_and_enum_that_accept_the_final_body_cannot_come_with_a_positive_body(
    const: object, members: list[object]
) -> None:
    # A node whose const accepts the final body, but whose enum rejects it, would need
    # a positive body that satisfies both keywords and differs from the final body.
    # No such body exists, so the endpoint has no positive baseline and Phase 4 never
    # compares the node's keywords.
    suite = element_suite({**CONST_ENUM_ROWS, "const": const, "enum": members})

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics if item.code.startswith("array_")] == []
    assert [item.code for item in suite.diagnostics][:1] == ["no_positive_schema_value"]
    assert array_values(suite) == []


def test_literal_helpers_combine_const_and_enum() -> None:
    accepts, pointer = generator_module._literal_accepts, generator_module._literal_pointer
    both = {"const": 1, "enum": [1, 2]}

    assert accepts(both, 1) is True  # both accept
    assert accepts(both, 2) is False  # const rejects, enum accepts
    assert accepts({"const": 1, "enum": [2]}, 1) is False  # const accepts, enum rejects
    assert accepts(both, 3) is False  # both reject
    assert accepts({"const": 1.0}, 1) is True and accepts({"enum": [True]}, 1) is False
    assert pointer(both, "/s") == "/s/const"  # the const is named first
    assert pointer({"enum": [1]}, "/s") == "/s/enum"
    assert pointer({}, "/s") is None


def test_phase_4_targets_with_aliases_nesting_and_recursion_never_share_a_case_key() -> None:
    # Phase 4 drops a target whose (path, constraint, candidate length) was already
    # accepted. Each array node is visited once, so that guard never fires: every
    # (path, constraint) pair is unique, even when the same $ref alias, a recursive
    # schema and the repeated worklist reach the same schema through different paths.
    level_two = empty_obj({"list2": empty_arr(schema_reference("Item"))}, ("list2",))
    level_one = empty_obj({"list": empty_arr(empty_obj({"q": level_two}))}, ("list",))
    body = empty_obj(
        {
            "a": empty_arr(schema_reference("Item")),
            "b": empty_arr(schema_reference("Item")),  # same alias, another path
            "c": empty_arr(empty_arr(schema_reference("Item"))),
            "deep": empty_arr(empty_obj({"p": level_one})),
            "root": schema_reference("Node"),
            "0": empty_arr(schema_reference("Item")),  # property named like an index
        },
        ("a", "b", "c", "deep", "root", "0"),
    )
    node = empty_obj(
        {
            "tags": ELEMENT_TAGS,
            "kids": empty_arr(schema_reference("Node")),  # recursive through an array
            "child": schema_reference("Node"),  # recursive through an object
        },
        ("kids",),
    )

    suite = element_suite(body, {"Item": TAGS_ITEM, "Node": node})
    keys = [(path, constraint) for path, constraint, _ in array_values(suite)]

    assert len(keys) == 20
    assert len(set(keys)) == len(keys)  # no (path, constraint) repeats
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)
    assert {path for path, _ in keys} >= {
        "/a/0/tags",
        "/b/0/tags",  # the alias reached twice gives two cases
        "/c/0/0/tags",
        "/0/0/tags",
        "/root/kids/0/child/tags",
        "/deep/0/p/list/0/q/list2/0/tags",
    }
    assert [item.code for item in suite.diagnostics if item.code.startswith("array_")] == []


# --- Step 8.32: absent optional arrays below an element inserted by phase 4 --------
# The behavior needs no extra traversal: the inserted element is visited like any
# present object, so its absent optional arrays and object parents are targeted by the
# same path as in phases 1-3. These tests pin it with exact bodies and case IDs.

def inserted_rows(item: object, schemas: dict[str, object] | None = None) -> GeneratedTestSuite:
    # "rows" is an empty positive array, so the element exists only in the cases.
    suite = element_suite(empty_obj({"rows": empty_arr(item)}, ("rows",)), schemas)
    assert suite.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]
    return suite


def inserted_ids(*parts: str) -> list[str]:
    return [build_case_id("negative", "/items", "post", f"array-bound:{part}") for part in parts]


UNIQUE_TAGS: dict[str, object] = {"type": "array", "uniqueItems": True, "items": ITEM_SCHEMA}


def test_absent_array_below_an_inserted_element_gets_min_max_and_unique_cases_with_stable_ids() -> None:
    bounds = inserted_rows(empty_obj({"tags": ELEMENT_TAGS}))
    unique = inserted_rows(empty_obj({"tags": UNIQUE_TAGS}))

    assert [case.case_id for case in array_cases(bounds)] == inserted_ids(
        "/rows/0/tags:minItems:1", "/rows/0/tags:maxItems:4"
    )
    assert array_values(unique) == [("/rows/0/tags", "uniqueItems", {"rows": [{"tags": ["x", "x"]}]})]
    assert [case.case_id for case in array_cases(unique)] == inserted_ids("/rows/0/tags:uniqueItems:2")
    assert array_diagnostics(bounds) == [] and array_diagnostics(unique) == []


def test_two_levels_of_absent_object_parents_below_an_inserted_element() -> None:
    inner = empty_obj({"tags": ELEMENT_TAGS})
    suite = inserted_rows(empty_obj({"m1": empty_obj({"m2": inner})}))

    assert array_values(suite) == [
        ("/rows/0/m1/m2/tags", "minItems", {"rows": [{"m1": {"m2": {"tags": ["x"]}}}]}),
        ("/rows/0/m1/m2/tags", "maxItems", {"rows": [{"m1": {"m2": {"tags": ["x"] * 4}}}]}),
    ]
    assert [case.case_id for case in array_cases(suite)] == inserted_ids(
        "/rows/0/m1/m2/tags:minItems:1", "/rows/0/m1/m2/tags:maxItems:4"
    )
    assert array_diagnostics(suite) == []


def test_references_on_the_item_parent_and_array_are_followed_below_an_inserted_element() -> None:
    schemas: dict[str, object] = {
        "Item": empty_obj({"meta": schema_reference("Meta"), "direct": schema_reference("Tags")}),
        "Meta": empty_obj({"tags": schema_reference("Tags")}),
        "Tags": ELEMENT_TAGS,
    }
    suite = inserted_rows(schema_reference("Item"), schemas)

    # Properties are visited in sorted order: "direct" before "meta".
    assert [value[:2] for value in array_values(suite)] == [
        ("/rows/0/direct", "minItems"),
        ("/rows/0/direct", "maxItems"),
        ("/rows/0/meta/tags", "minItems"),
        ("/rows/0/meta/tags", "maxItems"),
    ]
    assert array_values(suite)[0][2] == {"rows": [{"direct": ["x"]}]}
    assert array_values(suite)[3][2] == {"rows": [{"meta": {"tags": ["x"] * 4}}]}
    # The provenance names the array's own schema, reached through two references.
    assert "/components/schemas/Tags/minItems" in array_cases(suite)[2].provenance
    assert array_diagnostics(suite) == []


def test_read_only_arrays_and_parents_are_skipped_and_write_only_arrays_are_kept_below_an_inserted_element() -> None:
    item = empty_obj(
        {
            "ro": {**ELEMENT_TAGS, "readOnly": True},
            "ro_parent": {**empty_obj({"tags": ELEMENT_TAGS}), "readOnly": True},
            "wo": {**ELEMENT_TAGS, "writeOnly": True},
        }
    )

    suite = inserted_rows(item)

    assert array_values(suite) == [
        ("/rows/0/wo", "minItems", {"rows": [{"wo": ["x"]}]}),
        ("/rows/0/wo", "maxItems", {"rows": [{"wo": ["x"] * 4}]}),
    ]
    assert array_diagnostics(suite) == []


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_literal_on_an_absent_parent_below_an_inserted_element_blocks_only_that_parent(keyword: str) -> None:
    literal = {"const": {}} if keyword == "const" else {"enum": [{}]}
    item = empty_obj({"meta": {**empty_obj({"tags": ELEMENT_TAGS}), **literal}, "tags": ELEMENT_TAGS})

    suite = inserted_rows(item)

    # Creating "meta" would leave its literal; the sibling array is unaffected.
    assert [value[:2] for value in array_values(suite)] == [("/rows/0/tags", "minItems"), ("/rows/0/tags", "maxItems")]
    assert array_diagnostics(suite) == [
        ("array_bound_no_safe_candidate", f"{ELEMENT_ITEM_POINTER}/properties/meta/{keyword}")
    ]
    assert "'/rows/0/meta'" in next(item.message for item in suite.diagnostics if item.code.startswith("array_"))


@pytest.mark.parametrize(
    ("sibling", "keyword"),
    [(False, "must"), ({"allOf": [{"type": "string"}]}, "must/allOf")],
)
def test_unsupported_required_sibling_below_an_inserted_element_is_reported_once_per_group(
    sibling: object, keyword: str
) -> None:
    element = inserted_rows(empty_obj({"must": sibling, "tags": ELEMENT_TAGS}, ("must",)))
    parent = inserted_rows(
        empty_obj({"meta": empty_obj({"must": sibling, "tags": ELEMENT_TAGS}, ("must",))})
    )

    assert array_values(element) == [] and array_values(parent) == []
    assert array_diagnostics(element) == [
        ("array_bound_unsupported_schema", f"{ELEMENT_ITEM_POINTER}/properties/{keyword}")
    ]
    assert array_diagnostics(parent) == [
        ("array_bound_unsupported_schema", f"{ELEMENT_ITEM_POINTER}/properties/meta/properties/{keyword}")
    ]


def test_recursion_through_an_optional_parent_below_an_inserted_element_stops_at_the_repeat() -> None:
    node = empty_obj({"tags": ELEMENT_TAGS, "child": schema_reference("Node")})

    suite = inserted_rows(schema_reference("Node"), {"Node": node})

    assert [value[:2] for value in array_values(suite)] == [
        ("/rows/0/tags", "minItems"),
        ("/rows/0/tags", "maxItems"),
        ("/rows/0/child/tags", "minItems"),
        ("/rows/0/child/tags", "maxItems"),
    ]
    assert array_values(suite)[3][2] == {"rows": [{"child": {"tags": ["x"] * 4}}]}  # no "child/child"
    assert array_diagnostics(suite) == []


def test_absent_arrays_below_an_inserted_element_are_deterministic_and_leave_the_input_untouched() -> None:
    item = empty_obj(
        {
            "meta": empty_obj({"tags": ELEMENT_TAGS}),
            "unique": UNIQUE_TAGS,
            "wo": {**ELEMENT_TAGS, "writeOnly": True},
        }
    )
    body = empty_obj({"rows": empty_arr(item)}, ("rows",))
    before = deepcopy(body)

    first = element_suite(body)
    second = element_suite(body)

    assert body == before  # the inserted element is built on copies
    assert first == second and first.model_dump_json() == second.model_dump_json()
    # Direct absent arrays come first, in sorted order; the absent parent's arrays follow.
    ids = [case.case_id for case in array_cases(first)]
    assert ids == inserted_ids(
        "/rows/0/unique:uniqueItems:2",
        "/rows/0/wo:minItems:1",
        "/rows/0/wo:maxItems:4",
        "/rows/0/meta/tags:minItems:1",
        "/rows/0/meta/tags:maxItems:4",
    )
    assert array_values(first)[0][2] == {"rows": [{"unique": ["x", "x"]}]}  # no other property leaks in
    assert array_values(first)[3][2] == {"rows": [{"meta": {"tags": ["x"]}}]}
    assert first.cases[0].request_body.value == {"rows": []}  # type: ignore[union-attr]


# --- Ancestor literal conflict validation (all negative steps) -------------------------

CONFLICT_CODE = "negative_case_ancestor_literal_conflict"
META_POSITIVE = {"name": "abc", "n": 2, "k": "k", "r": "ok"}
META_PROPERTIES: dict[str, object] = {
    "name": {"type": "string", "minLength": 3, "maxLength": 3, "example": "abc"},
    "n": {"type": "integer", "minimum": 1, "maximum": 3, "example": 2},
    "k": {"type": "string", "enum": ["k"]},
    "r": {"type": "string", "example": "ok"},
}
META_REQUIRED = ("name", "n", "k", "r")
NUMERIC_ONLY: dict[str, object] = {"n": {"type": "integer", "minimum": 1, "maximum": 3, "example": 2}}


def meta_schema(**literal: object) -> dict[str, object]:
    return {**empty_obj(META_PROPERTIES, META_REQUIRED), **literal}


def conflicts(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    """(constraint, pointer of the blocking literal) of every ancestor-literal diagnostic."""
    return [
        (item.message.split(" ")[1], item.source_pointer)
        for item in suite.diagnostics
        if item.code == CONFLICT_CODE
    ]


def negative_keys(suite: GeneratedTestSuite) -> list[tuple[str | None, str | None]]:
    return [(c.violated_constraint, c.affected_property) for c in suite.cases if c.category == "negative"]


def value_at_or_absent(value: object, pointer: str) -> object:
    for token in [part for part in pointer.split("/") if part]:
        if isinstance(value, list):
            value = value[int(token)]
        elif isinstance(value, dict) and token in value:
            value = value[token]
        else:
            return "<absent>"
    return value


def strict_ancestor_leaks(suite: GeneratedTestSuite, literals: dict[str, dict[str, object]]) -> list[str]:
    """Negative cases whose body violates a literal that sits strictly above the target."""
    leaks = []
    for case in suite.cases:
        if case.category != "negative" or case.request_body is None:
            continue
        target = case.affected_property or ""
        for pointer, literal in literals.items():
            if not target.startswith(f"{pointer}/") and pointer:
                continue
            if target == pointer:
                continue
            found = value_at_or_absent(case.request_body.value, pointer)
            if found != "<absent>" and not generator_module._literal_accepts(literal, found):
                leaks.append(f"{case.violated_constraint}:{target}")
    return leaks


LITERAL_SCENARIOS = {
    "present enum": (
        empty_obj({"meta": meta_schema(enum=[META_POSITIVE])}, ("meta",)),
        None,
        {"/meta": {"enum": [META_POSITIVE]}},
        "/properties/meta/enum",
    ),
    "present const": (
        empty_obj({"meta": meta_schema(const=META_POSITIVE)}, ("meta",)),
        None,
        {"/meta": {"const": META_POSITIVE}},
        "/properties/meta/const",
    ),
    "root const": ({**meta_schema(), "const": META_POSITIVE}, None, {"": {"const": META_POSITIVE}}, "/const"),
    "root enum": ({**meta_schema(), "enum": [META_POSITIVE]}, None, {"": {"enum": [META_POSITIVE]}}, "/enum"),
    "nested ancestors": (
        empty_obj(
            {
                "o": {
                    **empty_obj({"i": meta_schema(enum=[META_POSITIVE])}, ("i",)),
                    "enum": [{"i": META_POSITIVE}],
                }
            },
            ("o",),
        ),
        None,
        {"/o": {"enum": [{"i": META_POSITIVE}]}, "/o/i": {"enum": [META_POSITIVE]}},
        "/properties/o/enum",
    ),
    "reference ancestor": (
        empty_obj({"meta": schema_reference("Meta")}, ("meta",)),
        {"Meta": meta_schema(enum=[META_POSITIVE])},
        {"/meta": {"enum": [META_POSITIVE]}},
        "/components/schemas/Meta/enum",
    ),
}


@pytest.mark.parametrize("name", list(LITERAL_SCENARIOS))
def test_no_object_step_leaves_a_strict_ancestor_literal(name: str) -> None:
    body, schemas, literals, pointer_suffix = LITERAL_SCENARIOS[name]
    schemas_before = deepcopy((body, schemas))

    suite = element_suite(body, schemas)

    assert strict_ancestor_leaks(suite, literals) == [], name
    assert (body, schemas) == schemas_before
    reported = conflicts(suite)
    assert reported, name
    assert any(pointer.endswith(pointer_suffix) for _, pointer in reported), (name, reported)
    constraints = {constraint for constraint, _ in reported}
    # Every object-oriented step is blocked at least once in each scenario.
    assert {"required_property", "type", "minLength", "maxLength", "minimum", "maximum", "enum"} <= constraints


def test_blocked_candidates_are_reported_once_each_and_retained_cases_keep_their_ids() -> None:
    plain = element_suite(empty_obj({"meta": meta_schema()}, ("meta",)))
    literal = element_suite(empty_obj({"meta": meta_schema(enum=[META_POSITIVE])}, ("meta",)))

    plain_cases = without_valid_edge_cases(plain)
    literal_cases = without_valid_edge_cases(literal)
    plain_ids = {(c.violated_constraint, c.affected_property): c.case_id for c in plain_cases}
    retained = [(c.violated_constraint, c.affected_property) for c in literal_cases]
    # Retained cases keep their ID and their relative order.
    assert [c.case_id for c in literal_cases] == [plain_ids[key] for key in retained]
    assert retained == [key for key in plain_ids if key in set(retained)]
    # Every other candidate was reported exactly once.
    messages = [item.message for item in literal.diagnostics if item.code == CONFLICT_CODE]
    assert len(messages) == len(set(messages)) == len(plain_cases) - len(literal_cases)


def test_a_literal_that_accepts_the_changed_body_keeps_the_case() -> None:
    without_r = {key: value for key, value in META_POSITIVE.items() if key != "r"}
    suite = element_suite(empty_obj({"meta": meta_schema(enum=[META_POSITIVE, without_r])}, ("meta",)))

    assert ("required_property", "/meta/r") in negative_keys(suite)
    assert ("required_property", "/meta/n") not in negative_keys(suite)
    blocked = [item.message for item in suite.diagnostics if item.code == CONFLICT_CODE]
    assert not any(message.startswith("The required_property case for '/meta/r'") for message in blocked)
    assert any(message.startswith("The required_property case for '/meta/n'") for message in blocked)


def test_schemas_without_ancestor_literals_report_nothing_and_are_unchanged() -> None:
    suite = element_suite(empty_obj({"meta": meta_schema()}, ("meta",)))

    assert [item for item in suite.diagnostics if item.code == CONFLICT_CODE] == []
    # Seventeen baseline and negative cases; the two valid-edge cases are Step 8.42.1's.
    assert len(without_valid_edge_cases(suite)) == 17
    assert len(suite.cases) == 19


def test_the_target_own_literal_is_excluded_for_wrong_type_and_enum() -> None:
    suite = element_suite(empty_obj({"meta": meta_schema()}, ("meta",)))
    keys = negative_keys(suite)

    # "/meta/k" carries enum ["k"]: violating it is the point of these two cases.
    assert ("type", "/meta/k") in keys
    assert ("enum", "/meta/k") in keys
    assert conflicts(suite) == []
    # Even a literal on the target itself does not block its own wrong_type case.
    own = element_suite(empty_obj({"meta": {**meta_schema(), "enum": [META_POSITIVE]}}, ("meta",)))
    assert ("type", "/meta") in negative_keys(own)


def test_numeric_targets_below_an_absent_optional_parent_respect_the_parent_literal() -> None:
    own = element_suite(empty_obj({"meta": {**empty_obj(NUMERIC_ONLY), "enum": [{"n": 2}]}}))

    assert own.cases[0].request_body.value == {}  # type: ignore[union-attr]
    assert not [key for key in negative_keys(own) if key[0] in ("minimum", "maximum")]
    assert {constraint for constraint, _ in conflicts(own)} == {"minimum", "maximum"}

    above = element_suite(
        empty_obj({"o": {**empty_obj({"meta": empty_obj(NUMERIC_ONLY)}), "enum": [{}]}}, ("o",))
    )
    assert not [key for key in negative_keys(above) if key[0] in ("minimum", "maximum")]
    assert {constraint for constraint, _ in conflicts(above)} == {"minimum", "maximum"}


def test_numeric_boundary_below_an_absent_parent_is_kept_without_a_literal() -> None:
    suite = element_suite(empty_obj({"meta": empty_obj(NUMERIC_ONLY)}))

    assert {("minimum", "/meta/n"), ("maximum", "/meta/n")} <= set(negative_keys(suite))
    assert conflicts(suite) == []


# Array Bounds: phases 1-3 now use the shared validation; phase 4 keeps its semantics.
def array_conflicts(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    return [
        (item.message.split(" ")[1], item.source_pointer)
        for item in suite.diagnostics
        if item.code == CONFLICT_CODE and item.message.startswith(("The minItems", "The maxItems"))
    ]


def test_array_phase_one_blocks_candidates_that_leave_a_present_ancestor_literal() -> None:
    body = empty_obj(
        {"o": {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "enum": [{"tags": ["x", "x"]}]}}, ("o",)
    )

    suite = element_suite(body)

    assert suite.cases[0].request_body.value == {"o": {"tags": ["x", "x"]}}  # type: ignore[union-attr]
    assert array_values(suite) == []
    assert array_conflicts(suite) == [
        ("minItems", f"{ARRAY_BODY_POINTER}/properties/o/enum"),
        ("maxItems", f"{ARRAY_BODY_POINTER}/properties/o/enum"),
    ]
    assert [c for c in array_cases(suite)] == []


def test_array_phase_one_blocks_candidates_below_a_root_literal() -> None:
    body = {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "const": {"tags": ["x", "x"]}}

    suite = element_suite(body)

    assert array_values(suite) == []
    assert array_conflicts(suite) == [
        ("minItems", f"{ARRAY_BODY_POINTER}/const"),
        ("maxItems", f"{ARRAY_BODY_POINTER}/const"),
    ]


def test_array_phase_one_keeps_candidates_that_the_literal_accepts() -> None:
    accepted = {"tags": ["x"]}
    body = {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "enum": [{"tags": ["x", "x"]}, accepted]}

    suite = element_suite(body)

    assert array_values(suite) == [("/tags", "minItems", accepted)]
    assert array_conflicts(suite) == [("maxItems", f"{ARRAY_BODY_POINTER}/enum")]


def test_array_phase_two_blocks_element_candidates_below_an_ancestor_literal() -> None:
    rows = empty_arr(empty_obj({"tags": ELEMENT_TAGS}, ("tags",)))
    body = empty_obj(
        {"o": {**empty_obj({"rows": rows}, ("rows",)), "enum": [{"rows": [{"tags": ["x", "x"]}]}]}},
        ("o",),
    )

    suite = element_suite(body)

    assert suite.cases[0].request_body.value == {"o": {"rows": [{"tags": ["x", "x"]}]}}  # type: ignore[union-attr]
    assert array_values(suite) == []
    pointer = f"{ARRAY_BODY_POINTER}/properties/o/enum"
    assert array_conflicts(suite) == [("minItems", pointer), ("maxItems", pointer)]
    assert all(
        "'/o/rows/0/tags'" in item.message
        for item in suite.diagnostics
        if item.code == CONFLICT_CODE and item.message.startswith(("The minItems", "The maxItems"))
    )


def test_array_phase_three_blocks_candidates_below_an_absent_parent_under_a_literal() -> None:
    body = empty_obj(
        {"o": {**empty_obj({"meta": empty_obj({"tags": ELEMENT_TAGS}, ("tags",))}), "enum": [{}]}}, ("o",)
    )

    suite = element_suite(body)

    assert suite.cases[0].request_body.value == {"o": {}}  # type: ignore[union-attr]
    assert array_values(suite) == []
    pointer = f"{ARRAY_BODY_POINTER}/properties/o/enum"
    assert array_conflicts(suite) == [("minItems", pointer), ("maxItems", pointer)]


def test_array_phase_three_absent_parent_own_literal_keeps_its_existing_code() -> None:
    body = empty_obj(
        {"meta": {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "enum": [{"tags": ["x", "x"]}]}}
    )

    suite = element_suite(body)

    assert array_values(suite) == []
    assert [item.code for item in suite.diagnostics if item.code.startswith("array_")] == [
        "array_bound_no_safe_candidate"
    ]
    assert array_conflicts(suite) == []


def test_enclosing_array_blocker_keeps_its_own_code_for_elements_of_present_arrays() -> None:
    body = empty_obj(
        {
            "outer": empty_arr(
                empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), enum=[[{"tags": ["x", "x"]}]]
            )
        },
        ("outer",),
    )

    suite = element_suite(body)

    assert array_values(suite) == []
    assert [item.code for item in suite.diagnostics if item.code.startswith("array_")] == [
        "array_bound_no_safe_candidate"
    ] * 2
    assert array_conflicts(suite) == []


def test_array_phase_three_keeps_candidates_below_an_absent_parent_without_a_literal() -> None:
    suite = element_suite(empty_obj({"meta": empty_obj({"tags": ELEMENT_TAGS}, ("tags",))}))

    assert {(path, constraint) for path, constraint, _ in array_values(suite)} == {
        ("/meta/tags", "minItems"),
        ("/meta/tags", "maxItems"),
    }
    assert array_conflicts(suite) == []


def test_array_phase_four_splits_object_ancestor_and_array_literal_blocks() -> None:
    # An enclosing array literal keeps the array code (final-body check of phase 4).
    assert {code for code, _ in array_diagnostics(element_suite(ENCLOSING_ARRAY_BODY))} == {
        "array_bound_no_safe_candidate"
    }
    # A root array literal is an array literal as well.
    root = element_suite(empty_arr(TAGS_ITEM, enum=[[]]))
    assert {code for code, _ in array_diagnostics(root)} == {"array_bound_no_safe_candidate"}
    # An object ancestor above the empty array gets the shared code.
    ancestor = element_suite(
        empty_obj({"w": {**empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), "enum": [{"rows": []}]}}, ("w",))
    )
    pointer = f"{ARRAY_BODY_POINTER}/properties/w/enum"
    assert array_diagnostics(ancestor) == [(CONFLICT_CODE, pointer)] * 2


def test_array_phases_one_to_three_split_object_ancestor_and_array_literal_blocks() -> None:
    items_literal = {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "enum": [{"tags": ["x", "x"]}]}
    # Phase 1/2: the items schema literal blocks the element target with the array code.
    rows = element_suite(empty_obj({"rows": empty_arr(items_literal)}, ("rows",)))
    assert {code for code, _ in array_diagnostics(rows)} == {"array_bound_no_safe_candidate"}
    # Phase 3 below an absent parent inside an element: the items literal keeps the array code ...
    absent = empty_obj({"meta": empty_obj({"tags": ELEMENT_TAGS}, ("tags",))})
    inner = element_suite(empty_obj({"outer": empty_arr({**absent, "enum": [{}]})}, ("outer",)))
    assert {code for code, _ in array_diagnostics(inner)} == {"array_bound_no_safe_candidate"}
    # ... and an object ancestor above the element gets the shared code.
    above = element_suite(
        empty_obj({"o": {**empty_obj({"outer": empty_arr(absent)}, ("outer",)), "enum": [{"outer": [{}]}]}}, ("o",))
    )
    assert {code for code, _ in array_diagnostics(above)} <= {CONFLICT_CODE}

@pytest.mark.parametrize("keyword", ["enum", "const"])
@pytest.mark.parametrize("parent", [False, True], ids=["phase-1-present", "phase-3-absent-parent"])
def test_array_phases_one_to_three_never_violate_the_target_arrays_own_literal(
    keyword: str, parent: bool
) -> None:
    positive = ["x", "x"]
    literal = [positive] if keyword == "enum" else positive
    tags = {**ELEMENT_TAGS, keyword: literal}
    body = empty_obj({"meta": empty_obj({"tags": tags})}) if parent else empty_obj({"tags": tags}, ("tags",))
    prefix = "/meta/tags" if parent else "/tags"

    suite = element_suite(body)

    # The literal accepts the positive body; no minItems/maxItems candidate may leave it.
    assert generator_module._literal_accepts(tags, positive)
    assert array_values(suite) == []
    # The selection of a candidate already honors the array's own literal, so the reason keeps
    # the existing code and the bound keyword; the ancestor-literal code is never used.
    reported = array_diagnostics(suite)
    assert [code for code, _ in reported] == ["array_bound_no_safe_candidate"] * 2
    assert [pointer.rsplit("/", 1)[1] for _, pointer in reported] == ["minItems", "maxItems"]
    assert all("/properties/tags/" in pointer for _, pointer in reported)
    assert CONFLICT_CODE not in {item.code for item in suite.diagnostics if prefix in item.message}


def test_array_phase_one_keeps_the_candidate_that_the_target_arrays_own_literal_accepts() -> None:
    tags = {**ELEMENT_TAGS, "enum": [["x", "x"], ["x"]]}

    suite = element_suite(empty_obj({"tags": tags}, ("tags",)))

    assert array_values(suite) == [("/tags", "minItems", {"tags": ["x"]})]
    assert [code for code, _ in array_diagnostics(suite)] == ["array_bound_no_safe_candidate"]


@pytest.mark.parametrize("keyword", ["enum", "const"])
def test_object_ancestor_with_an_inapplicable_items_keyword_uses_the_shared_code(keyword: str) -> None:
    literal = [{"rows": []}] if keyword == "enum" else {"rows": []}
    shape = {**empty_obj({"rows": empty_arr(TAGS_ITEM)}, ("rows",)), "items": {"type": "string"}, keyword: literal}

    # The literal rejects the created element; "items" does not make "w" an array.
    nested = element_suite(empty_obj({"w": shape}, ("w",)))
    assert nested.cases[0].request_body.value == {"w": {"rows": []}}  # type: ignore[union-attr]
    pointer = f"{ARRAY_BODY_POINTER}/properties/w/{keyword}"
    assert array_diagnostics(nested) == [(CONFLICT_CODE, pointer)] * 2

    # An object root with the same keyword behaves the same way.
    root = element_suite(shape)
    assert array_diagnostics(root) == [(CONFLICT_CODE, f"{ARRAY_BODY_POINTER}/{keyword}")] * 2


def test_items_schema_literal_of_object_elements_keeps_the_array_code() -> None:
    items = {**empty_obj({"tags": ELEMENT_TAGS}, ("tags",)), "enum": [{"tags": ["x", "x"]}]}
    suite = element_suite(empty_obj({"rows": empty_arr(items)}, ("rows",)))

    assert {code for code, _ in array_diagnostics(suite)} == {"array_bound_no_safe_candidate"}





# Budgets.
def test_blocked_candidates_do_not_consume_the_endpoint_case_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Retained: the root literal accepts nothing but the positive body, so only the three
    # wrong-type candidates ... none; the case budget of 3 would not fit the unrestricted run.
    plain = {name: {"type": "string", "example": "x"} for name in "abc"}
    body = {**empty_obj(plain, tuple("abc")), "const": {"a": "x", "b": "x", "c": "x"}}
    unrestricted = empty_obj(plain, tuple("abc"))
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 3)

    suite = generate_test_suite(make_result(post_endpoint(body)))
    full = generate_test_suite(make_result(post_endpoint(unrestricted)))

    assert "generation_case_budget_exceeded" not in {d.code for d in suite.diagnostics}
    assert conflicts(suite)
    assert "generation_case_budget_exceeded" in {d.code for d in full.diagnostics}


def test_required_property_precharge_ignores_blocked_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        **empty_obj({name: {"type": "string", "example": "x"} for name in "abc"}, tuple("abc")),
        "enum": [{"a": "x", "b": "x", "c": "x"}, {"a": "x", "b": "x"}],
    }
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 3)

    suite = generate_test_suite(make_result(post_endpoint(body)))

    # Only the "c" omission is accepted by the enum; 2 other omissions are blocked,
    # so the one retained case fits next to the positive case with no budget diagnostic.
    required = [c for c in suite.cases if c.violated_constraint == "required_property"]
    assert [c.affected_property for c in required] == ["/c"]
    assert "generation_case_budget_exceeded" not in {d.code for d in suite.diagnostics}
    assert [
        item.message.split("'")[1]
        for item in suite.diagnostics
        if item.code == CONFLICT_CODE and item.message.startswith("The required_property")
    ] == ["/a", "/b"]


def test_blocked_candidates_do_not_consume_the_suite_case_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        **empty_obj({name: {"type": "string", "example": "x"} for name in "abc"}, tuple("abc")),
        "const": {"a": "x", "b": "x", "c": "x"},
    }
    other = make_endpoint("/plain")
    plain_count = len(generate_test_suite(make_result(post_endpoint(body), other)).cases)
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", plain_count)

    suite = generate_test_suite(make_result(post_endpoint(body), other))

    assert len(suite.cases) == plain_count
    assert "generation_case_budget_exceeded" not in {d.code for d in suite.diagnostics}


def test_conflict_diagnostics_use_the_endpoint_diagnostic_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = empty_obj({"meta": meta_schema(enum=[META_POSITIVE])}, ("meta",))
    full = element_suite(body)
    assert sum(item.code == CONFLICT_CODE for item in full.diagnostics) > 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 2)

    suite = element_suite(body)

    codes = [item.code for item in suite.diagnostics]
    assert codes.count(CONFLICT_CODE) == 2
    assert codes[-1] == "generation_diagnostic_budget_exceeded"
    assert "per-endpoint limit of 2" in suite.diagnostics[-1].message
    # Truncation never affects the cases.
    assert [c.case_id for c in suite.cases] == [c.case_id for c in full.cases]


def test_conflict_diagnostics_use_the_suite_diagnostic_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_SUITE_DIAGNOSTICS", 3)
    body = empty_obj({"meta": meta_schema(enum=[META_POSITIVE])}, ("meta",))

    suite = generate_test_suite(make_result(post_endpoint(body, "/one"), post_endpoint(body, "/two")))

    codes = [item.code for item in suite.diagnostics]
    assert codes.count(CONFLICT_CODE) == 3
    assert codes[-1] == "generation_diagnostic_budget_exceeded"
    assert suite.diagnostics[-1].endpoint_path is None
    assert "per-suite limit of 3" in suite.diagnostics[-1].message


def test_ancestor_literal_validation_is_deterministic_and_does_not_mutate_inputs() -> None:
    body = empty_obj({"meta": meta_schema(enum=[META_POSITIVE])}, ("meta",))
    before = deepcopy(body)

    first = element_suite(body)
    second = element_suite(body)

    assert body == before
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize("literal", [{}, {"enum": [META_POSITIVE]}])
def test_ancestor_literal_validation_leaves_the_expansion_budget_unchanged(
    monkeypatch: pytest.MonkeyPatch, literal: dict[str, object]
) -> None:
    # The lineage reuses the schemas the traversal already followed: the smallest
    # per-endpoint expansion limit that still generates every step is 15 with or
    # without a literal (it was 15 before the validation existed).
    body = empty_obj({"meta": schema_reference("Meta")}, ("meta",))
    schemas = {"Meta": meta_schema(**literal)}
    # The valid-edge step runs last and follows its own references (Step 8.42.1).
    monkeypatch.setattr(generator_module, "_numeric_valid_edge_cases", lambda *args: ((), ()))
    monkeypatch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))

    def budget_codes(limit: int) -> set[str]:
        monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", limit)
        return {item.code for item in element_suite(body, schemas).diagnostics} & {
            "generation_expansion_budget_exceeded"
        }

    assert budget_codes(15) == set()
    assert budget_codes(14) == {"generation_expansion_budget_exceeded"}


# --- Optional request bodies (positive case without the body) ----------------------------

OPTIONAL_BODY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["a"],
    "properties": {"a": {"type": "string", "example": "x"}},
}
UNSUPPORTED_BODY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["a"],
    "properties": {"a": {"type": "string", "format": "hostname"}},
}
OMITTED_VARIANT = "request-body-omitted"


def body_of(schema: object, *, required: bool, media_type: str = "application/json") -> RequestBody:
    return RequestBody(
        required=required, content={media_type: MediaType(schema=Schema(raw=schema))}  # type: ignore[arg-type]
    )


def body_endpoint(request_body: RequestBody | LocalReference | None, path: str = "/x") -> Endpoint:
    return Endpoint(
        path=path,
        method="post",
        parameters=(
            Parameter(
                name="trace",
                location="query",
                required=True,
                schema=Schema(raw={"type": "string", "example": "t-1"}),
                source_scope="operation",
            ),
        ),
        request_body=request_body,
        security=(SecurityRequirement(schemes={"apiKey": ()}),),
        responses={"400": Response(description="Invalid request")},
    )


def body_suite(
    request_body: RequestBody | LocalReference | None,
    request_bodies: dict[str, RequestBody | LocalReference] | None = None,
) -> GeneratedTestSuite:
    endpoint = body_endpoint(request_body)
    return generate_test_suite(
        make_result(
            endpoint,
            components=Components(
                request_bodies=request_bodies or {},
                security_schemes=declared_security_schemes("apiKey").security_schemes,
            ),
        )
    )


def test_supported_optional_body_adds_a_positive_case_without_the_body_after_the_baseline() -> None:
    suite = body_suite(body_of(OPTIONAL_BODY_SCHEMA, required=False))

    baseline, omitted, *negatives = suite.cases
    assert [case.category for case in (baseline, omitted)] == ["positive", "positive"]
    assert baseline.case_id == build_case_id("positive", "/x", "post", "baseline")
    assert baseline.request_body == GeneratedBody(media_type="application/json", value={"a": "x"})
    assert omitted.case_id == build_case_id("positive", "/x", "post", OMITTED_VARIANT)
    assert omitted.case_id != baseline.case_id
    assert omitted.request_body is None
    for field in ("path", "method", "parameters", "security", "response_expectations"):
        assert getattr(omitted, field) == getattr(baseline, field), field
    assert [item.name for item in omitted.parameters] == ["trace"]
    assert omitted.provenance == ("/paths/~1x/post", "/paths/~1x/post/requestBody")
    assert omitted.provenance[0] == baseline.provenance[0]
    assert omitted.expected_outcome == baseline.expected_outcome
    assert omitted.violated_constraint is None and omitted.affected_property is None
    # Negative cases follow the two positive cases, and none of them is a body omission.
    assert negatives and all(case.category == "negative" for case in negatives)
    assert "required_request_body" not in {case.violated_constraint for case in negatives}
    assert {"required_parameter", "required_property"} <= {
        case.violated_constraint for case in negatives
    }
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)


def test_optional_body_omitted_case_does_not_change_the_other_cases() -> None:
    optional = body_suite(body_of(OPTIONAL_BODY_SCHEMA, required=False))
    required = body_suite(body_of(OPTIONAL_BODY_SCHEMA, required=True))

    without_variant = [case for case in optional.cases if case.case_id != build_case_id(
        "positive", "/x", "post", OMITTED_VARIANT
    )]
    required_negatives = [
        case for case in required.cases if case.violated_constraint != "required_request_body"
    ]
    assert [case.case_id for case in without_variant] == [case.case_id for case in required_negatives]


def test_required_body_keeps_its_baseline_and_negative_omission_without_a_positive_variant() -> None:
    suite = body_suite(body_of(OPTIONAL_BODY_SCHEMA, required=True))

    positives = [case for case in suite.cases if case.category == "positive"]
    omissions = [case for case in suite.cases if case.violated_constraint == "required_request_body"]
    assert [case.case_id for case in positives] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert [case.case_id for case in omissions] == [
        build_case_id("negative", "/x", "post", "required-request-body")
    ]
    assert omissions[0].request_body is None
    assert build_case_id("positive", "/x", "post", OMITTED_VARIANT) not in {
        case.case_id for case in suite.cases
    }


@pytest.mark.parametrize("required", [False, True])
def test_unsupported_body_outcome_depends_on_whether_the_body_is_required(required: bool) -> None:
    suite = body_suite(body_of(UNSUPPORTED_BODY_SCHEMA, required=required))

    expected = (
        "/paths/~1x/post/requestBody/content/application~1json/schema/properties/a/format"
    )
    assert [(d.code, d.source_pointer, d.reference_uri) for d in suite.diagnostics] == [
        ("unsupported_schema_keyword", expected, None)
    ]
    assert suite.diagnostics[0].message == "Schema keyword 'format' is outside the supported subset."
    if required:
        assert suite.cases == ()
        return
    # Optional: the endpoint stays, with the baseline without a body and other negatives.
    assert [case.case_id for case in suite.cases[:1]] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert suite.cases[0].request_body is None
    assert [case.violated_constraint for case in suite.cases[1:]] == ["required_parameter"]
    assert build_case_id("positive", "/x", "post", OMITTED_VARIANT) not in {
        case.case_id for case in suite.cases
    }


@pytest.mark.parametrize(
    ("request_body", "code", "pointer", "reference_uri"),
    [
        (
            RequestBody(
                required=False,
                content={
                    "application/json": MediaType(
                        schema=Schema(raw=OPTIONAL_BODY_SCHEMA), encoding={"a": {}}
                    )
                },
            ),
            "unsupported_request_encoding",
            "/paths/~1x/post/requestBody/content/application~1json/encoding",
            None,
        ),
        (
            body_of(OPTIONAL_BODY_SCHEMA, required=False, media_type="multipart/form-data"),
            "unsupported_request_media_type",
            "/paths/~1x/post/requestBody/content",
            None,
        ),
        (
            request_body_reference("Missing"),
            "unsupported_request_body_reference",
            "/paths/~1x/post/requestBody",
            "#/components/requestBodies/Missing",
        ),
        (
            request_body_reference("Loop"),
            "unsupported_request_body_reference",
            "/paths/~1x/post/requestBody",
            "#/components/requestBodies/Loop",
        ),
    ],
    ids=["encoding", "media-type", "unresolved-reference", "cyclic-reference"],
)
def test_unsupported_optional_bodies_keep_the_endpoint_and_the_original_diagnostic(
    request_body: RequestBody | LocalReference,
    code: str,
    pointer: str,
    reference_uri: str | None,
) -> None:
    suite = body_suite(request_body, {"Loop": request_body_reference("Loop")})

    assert [(d.code, d.source_pointer, d.reference_uri) for d in suite.diagnostics] == [
        (code, pointer, reference_uri)
    ]
    assert [(case.category, case.request_body) for case in suite.cases[:1]] == [("positive", None)]
    assert suite.cases[0].case_id == build_case_id("positive", "/x", "post", "baseline")
    assert [case.violated_constraint for case in suite.cases[1:]] == ["required_parameter"]


def test_a_required_body_that_cannot_be_generated_still_skips_the_endpoint() -> None:
    for request_body in (
        body_of(OPTIONAL_BODY_SCHEMA, required=True, media_type="multipart/form-data"),
        request_body_reference("Required"),
    ):
        suite = body_suite(
            request_body,
            {"Required": body_of(UNSUPPORTED_BODY_SCHEMA, required=True)},
        )
        assert suite.cases == ()
        assert len(suite.diagnostics) == 1


def test_no_body_declaration_generates_exactly_one_positive_baseline() -> None:
    suite = body_suite(None)

    assert [case.case_id for case in suite.cases[:1]] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert [case.category for case in suite.cases] == ["positive", "negative"]
    assert suite.cases[0].request_body is None
    assert suite.diagnostics == ()
    assert build_case_id("positive", "/x", "post", OMITTED_VARIANT) not in {
        case.case_id for case in suite.cases
    }


def test_requiredness_comes_from_the_resolved_target_of_an_alias_chain() -> None:
    components = {
        "Alias": request_body_reference("Mid"),
        "Mid": request_body_reference("Optional"),
        "Optional": body_of(OPTIONAL_BODY_SCHEMA, required=False),
        "Needed": body_of(OPTIONAL_BODY_SCHEMA, required=True),
        "NeededAlias": request_body_reference("Needed"),
    }

    optional = body_suite(request_body_reference("Alias"), components)
    required = body_suite(request_body_reference("NeededAlias"), components)

    omitted = optional.cases[1]
    assert omitted.case_id == build_case_id("positive", "/x", "post", OMITTED_VARIANT)
    assert omitted.request_body is None
    assert omitted.provenance == (
        "/paths/~1x/post",
        "/paths/~1x/post/requestBody",
        "#/components/requestBodies/Alias",
        "#/components/requestBodies/Mid",
        "#/components/requestBodies/Optional",
        "/components/requestBodies/Optional",
    )
    assert "required_request_body" not in {case.violated_constraint for case in optional.cases}
    assert [case.category for case in required.cases if case.category == "positive"] == ["positive"]
    assert "required_request_body" in {case.violated_constraint for case in required.cases}


def test_optional_body_generation_is_deterministic_and_does_not_mutate_the_input() -> None:
    endpoint = body_endpoint(body_of(OPTIONAL_BODY_SCHEMA, required=False))
    result = make_result(endpoint, components=declared_security_schemes("apiKey"))
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert result.model_dump_json() == before
    assert first.model_dump_json() == second.model_dump_json()
    assert [case.case_id for case in first.cases] == [case.case_id for case in second.cases]


def test_the_omitted_case_respects_the_endpoint_case_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = body_of(OPTIONAL_BODY_SCHEMA, required=False)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 1)

    only_baseline = body_suite(body)
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", 2)
    with_variant = body_suite(body)

    assert [case.case_id for case in only_baseline.cases] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert [item.code for item in only_baseline.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'optional_request_body_omitted' step" in only_baseline.diagnostics[0].message
    assert [case.request_body is None for case in with_variant.cases] == [False, True]
    assert [item.code for item in with_variant.diagnostics] == ["generation_case_budget_exceeded"]
    assert "'required_property' step" in with_variant.diagnostics[0].message


def test_the_omitted_case_respects_the_suite_case_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", 2)
    body = body_of(OPTIONAL_BODY_SCHEMA, required=False)

    suite = generate_test_suite(
        make_result(
            body_endpoint(body, "/one"),
            body_endpoint(body, "/two"),
            components=declared_security_schemes("apiKey"),
        )
    )

    assert [case.path for case in suite.cases] == ["/one", "/one"]
    assert [item.code for item in suite.diagnostics] == ["generation_case_budget_exceeded"]
    assert "per-suite limit of 2" in suite.diagnostics[0].message


def test_an_exhausted_expansion_budget_is_not_turned_into_an_optional_body_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 0)
    body = body_of({"$ref": "#/components/schemas/Body"}, required=False)
    result = make_result(
        body_endpoint(body),
        components=Components(
            schemas={"Body": Schema(raw=OPTIONAL_BODY_SCHEMA)},
            security_schemes=declared_security_schemes("apiKey").security_schemes,
        ),
    )

    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]


def test_an_exhausted_pattern_budget_is_not_turned_into_an_optional_body_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_PATTERN_EVALUATIONS", 0)
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string", "pattern": "^ok$", "example": "ok"}},
    }

    suite = body_suite(body_of(schema, required=False))

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["generation_pattern_budget_exceeded"]


def test_optional_body_diagnostics_use_the_existing_diagnostic_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_DIAGNOSTICS", 0)

    suite = body_suite(body_of(UNSUPPORTED_BODY_SCHEMA, required=False))

    assert [item.code for item in suite.diagnostics] == ["generation_diagnostic_budget_exceeded"]
    assert suite.cases and suite.cases[0].request_body is None


# --- Step 8.41.2: response schema references, headers and links ---

JSON_MEDIA = "application/json"
RESPONSES_POINTER = "/paths/~1items/get/responses"
SCHEMA_SUFFIX = "content/application~1json/schema"
ITEM_URI = "#/components/schemas/Item"
ITEM_RAW: dict[str, object] = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "tags": {"$ref": "#/components/schemas/Tag"}},
}
RESPONSE_CODES = {
    "unsupported_response_schema_reference",
    "unsupported_response_header",
    "unsupported_response_link",
}


def schema_response(raw: object, media_type: str = JSON_MEDIA) -> Response:
    return Response(description="OK", content={media_type: MediaType(schema=Schema(raw=raw))})  # type: ignore[arg-type]


def components_with(
    schemas: dict[str, object] | None = None,
    other: dict[str, dict[str, object]] | None = None,
    responses: dict[str, Response | LocalReference] | None = None,
) -> Components:
    return Components(
        schemas={name: Schema(raw=raw) for name, raw in (schemas or {}).items()},  # type: ignore[arg-type]
        responses=responses or {},
        other=other or {},  # type: ignore[arg-type]
    )


def response_suite(
    responses: dict[str, Response | LocalReference],
    components: Components | None = None,
    *,
    version: str = "3.1.1",
    **endpoint_kwargs: object,
) -> GeneratedTestSuite:
    endpoint = make_endpoint("/items", responses=responses, **endpoint_kwargs)  # type: ignore[arg-type]
    return generate_test_suite(make_result(endpoint, version=version, components=components))


def baseline_expectation(suite: GeneratedTestSuite, status: str = "200") -> ResponseExpectation:
    return next(
        item for item in suite.cases[0].response_expectations if item.status_key == status
    )


def response_diagnostics(suite: GeneratedTestSuite) -> list[GenerationDiagnostic]:
    return [item for item in suite.diagnostics if item.code in RESPONSE_CODES]


def test_inline_response_schema_gets_no_resolved_snapshot() -> None:
    suite = response_suite({"200": schema_response({"type": "string"})})

    expectation = baseline_expectation(suite)
    assert expectation.schemas_by_media_type == {JSON_MEDIA: Schema(raw={"type": "string"})}
    assert expectation.resolved_schemas_by_media_type == {}
    assert suite.diagnostics == ()


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_direct_response_schema_reference_is_resolved_and_the_declared_schema_kept(
    version: str,
) -> None:
    suite = response_suite(
        {"200": schema_response({"$ref": ITEM_URI})},
        components_with(schemas={"Item": ITEM_RAW}),
        version=version,
    )

    expectation = baseline_expectation(suite)
    assert expectation.schemas_by_media_type == {JSON_MEDIA: Schema(raw={"$ref": ITEM_URI})}
    assert expectation.resolved_schemas_by_media_type == {
        JSON_MEDIA: ResolvedResponseSchema(
            reference_chain=(ITEM_URI,),
            pointer="/components/schemas/Item",
            resolved=Schema(raw=ITEM_RAW),  # type: ignore[arg-type]
        )
    }
    assert suite.diagnostics == ()


def test_response_schema_alias_chain_keeps_every_hop_and_the_final_pointer() -> None:
    suite = response_suite(
        {"200": schema_response({"$ref": "#/components/schemas/A"})},
        components_with(
            schemas={
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": ITEM_URI},
                "Item": {"type": "string"},
            }
        ),
    )

    snapshot = baseline_expectation(suite).resolved_schemas_by_media_type[JSON_MEDIA]
    assert snapshot.reference_chain == (
        "#/components/schemas/A",
        "#/components/schemas/B",
        ITEM_URI,
    )
    assert snapshot.pointer == "/components/schemas/Item"
    assert snapshot.resolved == Schema(raw={"type": "string"})


def test_nested_references_stay_unresolved_inside_the_resolved_snapshot() -> None:
    suite = response_suite(
        {"200": schema_response({"$ref": ITEM_URI})},
        components_with(schemas={"Item": ITEM_RAW, "Tag": {"type": "string"}}),
    )

    resolved = baseline_expectation(suite).resolved_schemas_by_media_type[JSON_MEDIA].resolved
    assert resolved.raw["properties"]["tags"] == {"$ref": "#/components/schemas/Tag"}  # type: ignore[index]


def test_resolved_response_schema_hops_are_counted_once_per_traversal_hop() -> None:
    result = make_result(
        make_endpoint(
            "/items", responses={"200": schema_response({"$ref": "#/components/schemas/A"})}
        ),
        components=components_with(
            schemas={
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": ITEM_URI},
                "Item": {"type": "string"},
            }
        ),
    )

    assert count_expansions(result) == 3


@pytest.mark.parametrize(
    ("raw", "components", "pointer", "uri", "reason"),
    [
        (
            {"$ref": "#/components/schemas/Nope"},
            {},
            f"{RESPONSES_POINTER}/200/{SCHEMA_SUFFIX}",
            "#/components/schemas/Nope",
            "target was not found",
        ),
        (
            {"$ref": "#/components/schemas/A"},
            {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": "#/components/schemas/A"},
            },
            "/components/schemas/B",
            "#/components/schemas/A",
            "cycle",
        ),
        (
            {"$ref": "other.yaml#/Item"},
            {},
            f"{RESPONSES_POINTER}/200/{SCHEMA_SUFFIX}",
            "other.yaml#/Item",
            "references are supported",
        ),
        (
            {"$ref": "#/components/parameters/Limit"},
            {},
            f"{RESPONSES_POINTER}/200/{SCHEMA_SUFFIX}",
            "#/components/parameters/Limit",
            "references are supported",
        ),
    ],
)
@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_unusable_response_schema_reference_is_diagnosed_without_dropping_the_endpoint(
    version: str,
    raw: dict[str, object],
    components: dict[str, object],
    pointer: str,
    uri: str,
    reason: str,
) -> None:
    suite = response_suite(
        {"200": schema_response(raw)}, components_with(schemas=components), version=version
    )

    assert [case.category for case in suite.cases] == ["positive"]
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_schema_reference"
    assert diagnostic.endpoint_path == "/items"
    assert diagnostic.endpoint_method == "get"
    assert diagnostic.source_pointer == pointer
    assert diagnostic.reference_uri == uri
    assert reason in diagnostic.message
    expectation = baseline_expectation(suite)
    assert expectation.resolved_schemas_by_media_type == {}
    assert expectation.schemas_by_media_type == {JSON_MEDIA: Schema(raw=raw)}  # type: ignore[arg-type]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_annotation_siblings_next_to_a_response_schema_reference_are_supported(
    version: str,
) -> None:
    declared = {"$ref": ITEM_URI, "description": "d", "title": "t", "x-note": 1}
    suite = response_suite(
        {"200": schema_response(declared)},
        components_with(schemas={"Item": {"type": "string"}}),
        version=version,
    )

    expectation = baseline_expectation(suite)
    assert suite.diagnostics == ()
    # The resolved snapshot is the target; the declared schema keeps the siblings.
    assert expectation.resolved_schemas_by_media_type[JSON_MEDIA].resolved == Schema(
        raw={"type": "string"}
    )
    assert expectation.schemas_by_media_type[JSON_MEDIA] == Schema(raw=declared)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize("sibling", [("type", "string"), ("minLength", 1), ("nullable", True)])
def test_other_siblings_next_to_a_response_schema_reference_are_not_resolved(
    version: str, sibling: tuple[str, object]
) -> None:
    key, value = sibling
    declared = {"$ref": ITEM_URI, key: value}
    suite = response_suite(
        {"200": schema_response(declared)},
        components_with(schemas={"Item": {"type": "string"}}),
        version=version,
    )

    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_schema_reference"
    assert diagnostic.source_pointer == f"{RESPONSES_POINTER}/200/{SCHEMA_SUFFIX}/{key}"
    assert diagnostic.reference_uri == ITEM_URI
    assert "unsupported_schema_reference_siblings" in diagnostic.message
    expectation = baseline_expectation(suite)
    assert expectation.resolved_schemas_by_media_type == {}
    assert expectation.schemas_by_media_type[JSON_MEDIA] == Schema(raw=declared)  # type: ignore[arg-type]


def test_schema_failure_in_a_referenced_response_points_at_the_component() -> None:
    suite = response_suite(
        {"404": LocalReference(uri="#/components/responses/NotFound")},
        components_with(
            schemas={},
            responses={"NotFound": schema_response({"$ref": "#/components/schemas/Gone"})},
        ),
    )

    [diagnostic] = suite.diagnostics
    assert diagnostic.source_pointer == f"/components/responses/NotFound/{SCHEMA_SUFFIX}"
    assert diagnostic.reference_uri == "#/components/schemas/Gone"
    assert baseline_expectation(suite, "404").response_reference_uri == (
        "#/components/responses/NotFound"
    )


def test_response_diagnostics_are_ordered_by_status_then_media_type_and_deduplicated() -> None:
    shared = Response(
        description="Shared",
        content={
            "text/plain": MediaType(schema=Schema(raw={"$ref": "#/components/schemas/B"})),
            JSON_MEDIA: MediaType(schema=Schema(raw={"$ref": "#/components/schemas/A"})),
        },
    )
    suite = response_suite(
        {
            "default": LocalReference(uri="#/components/responses/Shared"),
            "404": LocalReference(uri="#/components/responses/Shared"),
            "200": schema_response({"$ref": "#/components/schemas/Z"}),
        },
        components_with(responses={"Shared": shared}),
    )

    assert [(item.source_pointer, item.reference_uri) for item in suite.diagnostics] == [
        (f"{RESPONSES_POINTER}/200/{SCHEMA_SUFFIX}", "#/components/schemas/Z"),
        (f"/components/responses/Shared/{SCHEMA_SUFFIX}", "#/components/schemas/A"),
        (
            "/components/responses/Shared/content/text~1plain/schema",
            "#/components/schemas/B",
        ),
    ]


def test_response_diagnostics_follow_the_existing_parameter_diagnostics() -> None:
    optional = Parameter(
        name="q", location="query", required=False,
        schema=Schema(raw={"type": "string", "pattern": "^(a)$"}), source_scope="operation",
    )
    suite = response_suite(
        {"200": schema_response({"$ref": "#/components/schemas/Nope"})},
        parameters=(optional,),
    )

    assert [item.code for item in suite.diagnostics] == [
        "optional_parameter_unsupported_schema",
        "unsupported_response_schema_reference",
    ]


def test_response_schema_resolution_budget_exhaustion_is_not_an_unsupported_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 2)
    components = components_with(
        schemas={
            "A": {"$ref": "#/components/schemas/B"},
            "B": {"$ref": ITEM_URI},
            "Item": {"type": "string"},
        }
    )
    suite = generate_test_suite(
        make_result(
            make_endpoint(
                "/items", responses={"200": schema_response({"$ref": "#/components/schemas/A"})}
            ),
            make_endpoint("/plain"),
            components=components,
        )
    )

    assert [case.path for case in suite.cases] == ["/plain"]
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]
    assert suite.diagnostics[0].endpoint_path == "/items"
    assert "per-endpoint limit of 2" in suite.diagnostics[0].message


def test_resolved_response_schemas_are_isolated_from_components_and_other_cases() -> None:
    query = Parameter(
        name="q", location="query", required=True,
        schema=Schema(raw={"type": "string"}), source_scope="operation",
    )
    components = components_with(schemas={"Item": ITEM_RAW})
    result = make_result(
        make_endpoint(
            "/items",
            parameters=(query,),
            responses={"200": schema_response({"$ref": ITEM_URI})},
        ),
        components=components,
    )
    before = result.model_dump_json()

    suite = generate_test_suite(result)

    assert len(suite.cases) >= 2
    snapshots = [
        case.response_expectations[0].resolved_schemas_by_media_type[JSON_MEDIA].resolved
        for case in suite.cases
    ]
    assert snapshots[0].raw is not snapshots[1].raw
    assert snapshots[0].raw is not components.schemas["Item"].raw
    snapshots[0].raw["properties"]["id"]["type"] = "string"  # type: ignore[index]
    assert snapshots[1].raw["properties"]["id"]["type"] == "integer"  # type: ignore[index]
    assert result.model_dump_json() == before
    fresh = generate_test_suite(result).cases[0].response_expectations[0]
    assert fresh.resolved_schemas_by_media_type[JSON_MEDIA].resolved.raw["properties"]["id"][  # type: ignore[index]
        "type"
    ] == "integer"


# Response headers


def header_response(headers: dict[str, object]) -> Response:
    return Response(description="OK", headers=headers)  # type: ignore[arg-type]


def test_inline_response_headers_keep_the_approved_metadata_in_name_order() -> None:
    suite = response_suite(
        {
            "200": header_response(
                {
                    "X-Rate": {
                        "description": "Remaining",
                        "required": True,
                        "deprecated": True,
                        "schema": {"$ref": "#/components/schemas/Rate"},
                        "style": "simple",
                        "explode": False,
                    },
                    "X-Alpha": {"schema": {"type": "string"}},
                }
            )
        }
    )

    headers = baseline_expectation(suite).headers
    assert [header.name for header in headers] == ["X-Alpha", "X-Rate"]
    assert headers[0] == ResponseHeaderExpectation(
        name="X-Alpha", header_schema=Schema(raw={"type": "string"})
    )
    assert headers[1] == ResponseHeaderExpectation(
        name="X-Rate",
        description="Remaining",
        required=True,
        deprecated=True,
        # A header schema is kept as declared; it is not resolved.
        header_schema=Schema(raw={"$ref": "#/components/schemas/Rate"}),
        style="simple",
        explode=False,
    )
    assert suite.diagnostics == ()


def test_component_header_reference_and_alias_chain_are_resolved() -> None:
    suite = response_suite(
        {"200": header_response({"X-Rate": LocalReference(uri="#/components/headers/A")})},
        components_with(
            other={
                "headers": {
                    "A": LocalReference(uri="#/components/headers/B"),
                    "B": {"description": "Remaining", "schema": {"type": "integer"}},
                }
            }
        ),
    )

    [header] = baseline_expectation(suite).headers
    assert header.name == "X-Rate"
    assert header.reference_chain == ("#/components/headers/A", "#/components/headers/B")
    assert header.description == "Remaining"
    assert header.header_schema == Schema(raw={"type": "integer"})
    assert suite.diagnostics == ()


def test_header_content_keeps_the_single_media_type_schema() -> None:
    suite = response_suite(
        {
            "200": header_response(
                {"X-Json": {"content": {JSON_MEDIA: {"schema": {"type": "object"}}}}}
            )
        }
    )

    [header] = baseline_expectation(suite).headers
    assert header.content_schemas == {JSON_MEDIA: Schema(raw={"type": "object"})}
    assert header.header_schema is None


@pytest.mark.parametrize(
    ("definition", "pointer_suffix"),
    [
        ({"content": {JSON_MEDIA: {}, "text/plain": {}}}, "content"),
        ({"content": {}}, "content"),
        ({"schema": {"type": "string"}, "content": {JSON_MEDIA: {}}}, ""),
        ({"content": {JSON_MEDIA: "not-an-object"}}, "content/application~1json"),
        ({"schema": "string"}, "schema"),
        ({"schema": {}, "required": "yes"}, "required"),
        ({"schema": {}, "deprecated": 1}, "deprecated"),
        ({"schema": {}, "description": 5}, "description"),
        ({"schema": {}, "style": ["simple"]}, "style"),
        ({"schema": {}, "explode": "no"}, "explode"),
        ({"schema": {}, "name": "X-Bad"}, "name"),
        ({"schema": {}, "in": "header"}, "in"),
    ],
)
def test_unsupported_or_malformed_header_is_diagnosed_and_the_rest_is_kept(
    definition: dict[str, object], pointer_suffix: str
) -> None:
    suite = response_suite(
        {
            "200": header_response(
                {"X-Bad": definition, "X-Good": {"required": True, "schema": {}}}
            ),
            "404": Response(description="Missing"),
        }
    )

    assert [case.category for case in suite.cases] == ["positive"]
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_header"
    expected = f"{RESPONSES_POINTER}/200/headers/X-Bad"
    assert diagnostic.source_pointer == (
        f"{expected}/{pointer_suffix}" if pointer_suffix else expected
    )
    assert diagnostic.reference_uri is None
    assert [header.name for header in baseline_expectation(suite).headers] == ["X-Good"]
    assert [item.status_key for item in suite.cases[0].response_expectations] == ["200", "404"]


def test_content_type_response_header_is_ignored_in_any_case_without_a_diagnostic() -> None:
    suite = response_suite(
        {
            "200": header_response(
                {
                    "Content-Type": {"required": "malformed"},
                    "content-type": LocalReference(uri="#/components/headers/Missing"),
                    "X-Kept": {"schema": {}},
                }
            )
        }
    )

    assert [header.name for header in baseline_expectation(suite).headers] == ["X-Kept"]
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    ("entry", "other", "pointer", "uri", "reason"),
    [
        (
            LocalReference(uri="#/components/headers/Nope"),
            {},
            f"{RESPONSES_POINTER}/200/headers/X-H",
            "#/components/headers/Nope",
            "target was not found",
        ),
        (
            LocalReference(uri="#/components/headers/A"),
            {
                "A": LocalReference(uri="#/components/headers/B"),
                "B": LocalReference(uri="#/components/headers/A"),
            },
            "/components/headers/B",
            "#/components/headers/A",
            "cycle",
        ),
        (
            LocalReference(uri="#/components/schemas/Item"),
            {},
            f"{RESPONSES_POINTER}/200/headers/X-H",
            "#/components/schemas/Item",
            "references are supported",
        ),
        (
            LocalReference(uri="#/components/headers/H"),
            {"H": {"schema": {}, "required": "yes"}},
            "/components/headers/H/required",
            "#/components/headers/H",
            "must be a boolean",
        ),
    ],
)
def test_header_reference_failures_use_the_most_precise_pointer(
    entry: LocalReference, other: dict[str, object], pointer: str, uri: str, reason: str
) -> None:
    suite = response_suite(
        {"200": header_response({"X-H": entry})},
        components_with(other={"headers": other} if other else {}),
    )

    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_header"
    assert diagnostic.source_pointer == pointer
    assert diagnostic.reference_uri == uri
    assert reason in diagnostic.message
    assert baseline_expectation(suite).headers == ()
    assert len(suite.cases) == 1


def test_header_diagnostics_follow_schema_diagnostics_and_header_name_order() -> None:
    response = Response(
        description="OK",
        content={JSON_MEDIA: MediaType(schema=Schema(raw={"$ref": "#/components/schemas/Nope"}))},
        headers={  # type: ignore[arg-type]
            "X-B": {"schema": {}, "required": 1},
            "X-A": {"schema": {}, "required": 1},
        },  # type: ignore[arg-type]
    )
    suite = response_suite({"200": response})

    assert [item.code for item in suite.diagnostics] == [
        "unsupported_response_schema_reference",
        "unsupported_response_header",
        "unsupported_response_header",
    ]
    assert [item.source_pointer for item in suite.diagnostics[1:]] == [
        f"{RESPONSES_POINTER}/200/headers/X-A/required",
        f"{RESPONSES_POINTER}/200/headers/X-B/required",
    ]


def test_header_reference_hops_are_charged_and_exhaustion_stays_a_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = components_with(
        other={
            "headers": {
                "A": LocalReference(uri="#/components/headers/B"),
                "B": LocalReference(uri="#/components/headers/C"),
                "C": {},
            }
        }
    )
    result = make_result(
        make_endpoint(
            "/items",
            responses={"200": header_response({"X": LocalReference(uri="#/components/headers/A")})},
        ),
        components=components,
    )
    assert count_expansions(result) == 3

    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", 2)
    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["generation_expansion_budget_exceeded"]


# Response links


def link_response(links: dict[str, object]) -> Response:
    return Response(description="OK", links=links)  # type: ignore[arg-type]


def test_inline_link_keeps_runtime_expressions_literal_and_sorts_by_name() -> None:
    suite = response_suite(
        {
            "200": link_response(
                {
                    "Zeta": {"operationRef": "#/paths/~1a/get"},
                    "GetUser": {
                        "operationId": "getUser",
                        "description": "The user",
                        "parameters": {
                            "userId": "$response.body#/id",
                            "tenant": "{$request.path.tenant}",
                            "fixed": 7,
                        },
                        "requestBody": "$request.body#/payload",
                        "server": {
                            "url": "https://{env}.example.com",
                            "description": "Env",
                            "variables": {"env": {"default": "api"}},
                            "x-note": True,
                        },
                    },
                }
            )
        }
    )

    links = baseline_expectation(suite).links
    assert [link.name for link in links] == ["GetUser", "Zeta"]
    assert links[0] == ResponseLinkExpectation(
        name="GetUser",
        operation_id="getUser",
        description="The user",
        parameters={
            "fixed": 7,
            "tenant": "{$request.path.tenant}",
            "userId": "$response.body#/id",
        },
        request_body=LinkRequestBody(value="$request.body#/payload"),
        server=Server(
            url="https://{env}.example.com",
            description="Env",
            variables={"env": {"default": "api"}},
            extensions={"x-note": True},
        ),
    )
    assert links[1] == ResponseLinkExpectation(name="Zeta", operation_ref="#/paths/~1a/get")
    assert suite.diagnostics == ()


def test_link_request_body_distinguishes_an_explicit_null_from_an_absent_body() -> None:
    suite = response_suite(
        {
            "200": link_response(
                {"Absent": {"operationId": "a"}, "Null": {"operationId": "n", "requestBody": None}}
            )
        }
    )

    absent, null = baseline_expectation(suite).links
    assert absent.request_body is None
    assert null.request_body == LinkRequestBody(value=None)


def test_component_link_reference_and_alias_chain_are_resolved() -> None:
    suite = response_suite(
        {"200": link_response({"Next": LocalReference(uri="#/components/links/A")})},
        components_with(
            other={
                "links": {
                    "A": LocalReference(uri="#/components/links/B"),
                    "B": {"operationId": "getNext"},
                }
            }
        ),
    )

    [link] = baseline_expectation(suite).links
    assert link.reference_chain == ("#/components/links/A", "#/components/links/B")
    assert link.operation_id == "getNext"
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    ("definition", "pointer_suffix"),
    [
        ({"operationId": "a", "operationRef": "#/x"}, ""),
        ({"operationId": 5}, "operationId"),
        ({"operationId": "a", "description": []}, "description"),
        ({"operationId": "a", "parameters": ["id"]}, "parameters"),
        ({"operationId": "a", "server": "https://example.com"}, "server"),
        ({"operationId": "a", "server": {"description": "no url"}}, "server/url"),
        ({"operationId": "a", "server": {"url": "u", "variables": {"v": 1}}}, "server/variables/v"),
    ],
)
def test_unsupported_or_malformed_link_is_diagnosed_and_the_rest_is_kept(
    definition: dict[str, object], pointer_suffix: str
) -> None:
    suite = response_suite(
        {"200": link_response({"Bad": definition, "Good": {"operationId": "ok"}})}
    )

    assert [case.category for case in suite.cases] == ["positive"]
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_link"
    expected = f"{RESPONSES_POINTER}/200/links/Bad"
    assert diagnostic.source_pointer == (
        f"{expected}/{pointer_suffix}" if pointer_suffix else expected
    )
    assert [link.name for link in baseline_expectation(suite).links] == ["Good"]


@pytest.mark.parametrize(
    ("entry", "other", "pointer", "uri"),
    [
        (
            LocalReference(uri="#/components/links/Nope"),
            {},
            f"{RESPONSES_POINTER}/200/links/L",
            "#/components/links/Nope",
        ),
        (
            LocalReference(uri="#/components/links/A"),
            {
                "A": LocalReference(uri="#/components/links/B"),
                "B": LocalReference(uri="#/components/links/A"),
            },
            "/components/links/B",
            "#/components/links/A",
        ),
        (
            LocalReference(uri="#/components/links/L"),
            {"L": {"operationId": 1}},
            "/components/links/L/operationId",
            "#/components/links/L",
        ),
    ],
)
def test_link_reference_failures_use_the_most_precise_pointer(
    entry: LocalReference, other: dict[str, object], pointer: str, uri: str
) -> None:
    suite = response_suite(
        {"200": link_response({"L": entry})},
        components_with(other={"links": other} if other else {}),
    )

    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_link"
    assert diagnostic.source_pointer == pointer
    assert diagnostic.reference_uri == uri
    assert baseline_expectation(suite).links == ()


def test_links_do_not_add_cases_or_change_case_identity() -> None:
    plain = response_suite({"200": Response(description="OK")})
    linked = response_suite(
        {"200": link_response({"Next": {"operationId": "getNext"}})}
    )

    assert [case.case_id for case in linked.cases] == [case.case_id for case in plain.cases]
    assert [case.category for case in linked.cases] == [case.category for case in plain.cases]
    assert linked.cases[0].request_body is None


def test_header_and_link_data_is_isolated_between_cases_and_from_the_input() -> None:
    query = Parameter(
        name="q", location="query", required=True,
        schema=Schema(raw={"type": "string"}), source_scope="operation",
    )
    result = make_result(
        make_endpoint(
            "/items",
            parameters=(query,),
            responses={
                "200": Response(
                    description="OK",
                    headers={"X": {"schema": {"type": "object"}}},  # type: ignore[arg-type]
                    links={"L": {"operationId": "x", "parameters": {"p": {"k": ["v"]}}}},  # type: ignore[arg-type]
                )
            },
        )
    )
    before = result.model_dump_json()

    suite = generate_test_suite(result)

    first, second = (case.response_expectations[0] for case in suite.cases[:2])
    first.headers[0].header_schema.raw["type"] = "string"  # type: ignore[union-attr, index]
    first.links[0].parameters["p"]["k"].append("changed")  # type: ignore[index, union-attr]
    assert second.headers[0].header_schema.raw["type"] == "object"  # type: ignore[union-attr, index]
    assert second.links[0].parameters["p"]["k"] == ["v"]  # type: ignore[index]
    assert result.model_dump_json() == before


# Compatibility


def test_response_expectation_new_fields_default_and_old_serialized_data_still_loads() -> None:
    old_json = json.dumps(
        {
            "status_key": "200",
            "media_types": [],
            "schemas_by_media_type": {},
            "response_reference_uri": None,
        }
    )

    restored = ResponseExpectation.model_validate_json(old_json)

    assert restored.resolved_schemas_by_media_type == {}
    assert restored.headers == ()
    assert restored.links == ()
    assert restored == ResponseExpectation(
        status_key="200", media_types=(), schemas_by_media_type={}, response_reference_uri=None
    )


def test_suite_with_resolved_schemas_headers_and_links_round_trips_through_json() -> None:
    suite = response_suite(
        {
            "200": Response(
                description="OK",
                content={JSON_MEDIA: MediaType(schema=Schema(raw={"$ref": ITEM_URI}))},
                headers={"X": {"description": "h", "schema": {"type": "integer"}}},  # type: ignore[arg-type]
                links={
                    "L": {
                        "operationId": "x",
                        "requestBody": None,
                        "server": {"url": "https://example.com"},
                    }
                },  # type: ignore[arg-type]
            )
        },
        components_with(schemas={"Item": {"type": "string"}}),
    )

    restored = GeneratedTestSuite.model_validate_json(suite.model_dump_json())

    assert restored == suite
    assert restored.cases[0].response_expectations[0].links[0].request_body == LinkRequestBody(
        value=None
    )


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalized_document_resolves_response_schema_header_and_link_references(
    version: str,
) -> None:
    document = {
        "openapi": version,
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {JSON_MEDIA: {"schema": {"$ref": ITEM_URI}}},
                            "headers": {
                                "X-Rate": {"$ref": "#/components/headers/Rate"},
                                "Content-Type": {"schema": {"type": "string"}},
                            },
                            "links": {"Next": {"$ref": "#/components/links/Next"}},
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {"Item": {"type": "string"}},
            "headers": {"Rate": {"schema": {"type": "integer"}}},
            "links": {"Next": {"operationId": "getItems"}},
        },
    }

    suite = generate_test_suite(normalize_openapi_document(document))

    expectation = baseline_expectation(suite)
    assert suite.diagnostics == ()
    assert expectation.resolved_schemas_by_media_type[JSON_MEDIA].resolved == Schema(
        raw={"type": "string"}
    )
    assert [header.name for header in expectation.headers] == ["X-Rate"]
    assert expectation.headers[0].reference_chain == ("#/components/headers/Rate",)
    assert [link.name for link in expectation.links] == ["Next"]
    assert expectation.links[0].operation_id == "getItems"


# --- Step 8.41.3: response metadata validation corrections ---

HEADER_POINTER = f"{RESPONSES_POINTER}/200/headers/X-H"
LINK_POINTER = f"{RESPONSES_POINTER}/200/links/L"


def only_header_diagnostic(
    definition: dict[str, object], other: dict[str, dict[str, object]] | None = None
) -> GenerationDiagnostic:
    suite = response_suite(
        {"200": header_response({"X-H": definition, "X-Ok": {"schema": {}}})},
        components_with(other=other),
    )
    assert [header.name for header in baseline_expectation(suite).headers] == ["X-Ok"]
    assert [case.category for case in suite.cases] == ["positive"]
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_header"
    return diagnostic


def only_link_diagnostic(
    definition: dict[str, object], other: dict[str, dict[str, object]] | None = None
) -> GenerationDiagnostic:
    suite = response_suite(
        {"200": link_response({"L": definition, "Ok": {"operationId": "ok"}})},
        components_with(other=other),
    )
    assert [link.name for link in baseline_expectation(suite).links] == ["Ok"]
    assert [case.category for case in suite.cases] == ["positive"]
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_link"
    return diagnostic


@pytest.mark.parametrize(
    ("definition", "expected_message"),
    [
        ({"description": "no target"}, "exactly one of operationId or operationRef"),
        ({"operationId": "a", "operationRef": "#/paths/~1a/get"}, "mutually exclusive"),
    ],
)
def test_link_without_or_with_both_operation_identifiers_is_diagnosed(
    definition: dict[str, object], expected_message: str
) -> None:
    diagnostic = only_link_diagnostic(definition)

    assert diagnostic.source_pointer == LINK_POINTER
    assert expected_message in diagnostic.message


@pytest.mark.parametrize(
    "definition", [{"operationId": "getItems"}, {"operationRef": "#/paths/~1items/get"}]
)
def test_link_with_exactly_one_operation_identifier_is_kept(
    definition: dict[str, object],
) -> None:
    suite = response_suite({"200": link_response({"L": definition})})

    [link] = baseline_expectation(suite).links
    assert (link.operation_id, link.operation_ref) == (
        definition.get("operationId"),
        definition.get("operationRef"),
    )
    assert suite.diagnostics == ()


def test_link_target_operation_is_not_resolved_or_validated() -> None:
    suite = response_suite({"200": link_response({"L": {"operationId": "doesNotExist"}})})

    assert suite.diagnostics == ()
    assert baseline_expectation(suite).links[0].operation_id == "doesNotExist"


def test_link_extensions_are_preserved_and_unknown_fields_are_reported() -> None:
    suite = response_suite(
        {"200": link_response({"L": {"operationId": "a", "x-note": {"k": [1]}}})}
    )
    assert baseline_expectation(suite).links[0].extensions == {"x-note": {"k": [1]}}

    diagnostic = only_link_diagnostic({"operationId": "a", "unknownField": 1})
    assert diagnostic.source_pointer == f"{LINK_POINTER}/unknownField"


@pytest.mark.parametrize(
    ("variable", "pointer_suffix"),
    [
        ({}, "default"),
        ({"default": 1}, "default"),
        ({"default": "a", "enum": "a"}, "enum"),
        ({"default": "a", "enum": ["a", 2]}, "enum"),
        ({"default": "a", "description": 5}, "description"),
        ({"default": "a", "unknown": 1}, "unknown"),
        ({"default": "a", "x-note": 1, "other": 1}, "other"),
    ],
)
def test_malformed_link_server_variables_are_diagnosed_with_a_precise_pointer(
    variable: dict[str, object], pointer_suffix: str
) -> None:
    diagnostic = only_link_diagnostic(
        {"operationId": "a", "server": {"url": "https://{env}.example.com", "variables": {"env": variable}}}
    )

    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/variables/env/{pointer_suffix}"


def test_valid_link_server_variables_are_kept_verbatim_with_extensions() -> None:
    variable = {"default": "api", "enum": ["api", "dev"], "description": "d", "x-note": [1]}
    suite = response_suite(
        {
            "200": link_response(
                {
                    "L": {
                        "operationId": "a",
                        "server": {"url": "https://{env}.example.com", "variables": {"env": variable}},
                    }
                }
            )
        }
    )

    server = baseline_expectation(suite).links[0].server
    assert server is not None and server.variables == {"env": variable}
    assert suite.diagnostics == ()


def test_unknown_link_server_field_is_reported_and_extensions_are_kept() -> None:
    diagnostic = only_link_diagnostic(
        {"operationId": "a", "server": {"url": "u", "unknown": 1}}
    )
    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/unknown"


def test_server_model_validation_errors_become_link_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The repository's Server model accepts any JSON object as a variable, so a
    # stricter model is substituted to prove that its ValidationError is
    # converted instead of escaping.
    class StrictVariable(Model):
        default: str
        enum: list[str]

    class StrictServer(Model):
        url: str
        description: str | None = None
        variables: dict[str, StrictVariable] = Field(default_factory=dict)
        extensions: dict[str, object] = Field(default_factory=dict)

    monkeypatch.setattr(generator_module, "Server", StrictServer)

    diagnostic = only_link_diagnostic(
        {
            "operationId": "a",
            "server": {"url": "https://{env}.example.com", "variables": {"env": {"default": "api"}}},
        }
    )

    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/variables/env/enum"
    assert diagnostic.message.startswith("Invalid link server:")


def test_server_validation_error_for_an_extension_points_inside_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StrictServer(Model):
        url: str
        description: str | None = None
        variables: dict[str, dict[str, object]] = Field(default_factory=dict)
        extensions: dict[str, int] = Field(default_factory=dict)

    monkeypatch.setattr(generator_module, "Server", StrictServer)

    diagnostic = only_link_diagnostic(
        {"operationId": "a", "server": {"url": "u", "x-count": "not-an-int"}}
    )

    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/x-count"


# Header Object requirements


def test_header_with_neither_schema_nor_content_is_diagnosed() -> None:
    diagnostic = only_header_diagnostic({"description": "no schema"})

    assert diagnostic.source_pointer == HEADER_POINTER
    assert "exactly one of schema or content" in diagnostic.message


def test_header_with_both_schema_and_content_is_diagnosed() -> None:
    diagnostic = only_header_diagnostic(
        {"schema": {}, "content": {JSON_MEDIA: {"schema": {}}}}
    )

    assert diagnostic.source_pointer == HEADER_POINTER
    assert "both schema and content" in diagnostic.message


def test_header_style_must_be_simple_when_present() -> None:
    diagnostic = only_header_diagnostic({"schema": {}, "style": "form"})
    assert diagnostic.source_pointer == f"{HEADER_POINTER}/style"
    assert "'simple'" in diagnostic.message

    suite = response_suite({"200": header_response({"X-H": {"schema": {}, "style": "simple"}})})
    assert baseline_expectation(suite).headers[0].style == "simple"
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    "field", ["allowEmptyValue", "allowReserved", "example", "examples", "unknownField"]
)
def test_unretained_header_fields_are_reported_not_silently_dropped(field: str) -> None:
    diagnostic = only_header_diagnostic({"schema": {}, field: True})

    assert diagnostic.source_pointer == f"{HEADER_POINTER}/{field}"
    assert f"'{field}'" in diagnostic.message


def test_header_extensions_are_preserved() -> None:
    suite = response_suite(
        {"200": header_response({"X-H": {"schema": {}, "x-b": 2, "x-a": {"k": [1]}}})}
    )

    [header] = baseline_expectation(suite).headers
    assert header.extensions == {"x-a": {"k": [1]}, "x-b": 2}
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("example", {"a": 1}, "'example' is not supported"),
        ("examples", {}, "'examples' is not supported"),
        ("encoding", {}, "'encoding' is not supported"),
        ("x-note", 1, "extension 'x-note' cannot be represented"),
    ],
)
def test_unsupported_fields_inside_header_content_media_type_are_reported(
    key: str, value: object, message: str
) -> None:
    diagnostic = only_header_diagnostic(
        {"content": {JSON_MEDIA: {"schema": {}, key: value}}}
    )

    assert diagnostic.source_pointer == f"{HEADER_POINTER}/content/application~1json/{key}"
    assert message in diagnostic.message


def test_only_the_first_unsupported_header_field_in_key_order_is_reported() -> None:
    diagnostic = only_header_diagnostic({"schema": {}, "z": 1, "example": 1})

    assert diagnostic.source_pointer == f"{HEADER_POINTER}/example"


# Reference Object description overrides


@pytest.mark.parametrize(
    ("version", "expected"),
    [("3.1.1", "Override"), ("3.0.3", "Declared")],
)
def test_reference_description_overrides_a_header_only_in_openapi_3_1(
    version: str, expected: str
) -> None:
    suite = response_suite(
        {
            "200": header_response(
                {
                    "X-H": LocalReference(
                        uri="#/components/headers/H", summary="Ignored", description="Override"
                    )
                }
            )
        },
        components_with(other={"headers": {"H": {"schema": {}, "description": "Declared"}}}),
        version=version,
    )

    [header] = baseline_expectation(suite).headers
    assert header.description == expected
    assert suite.diagnostics == ()


@pytest.mark.parametrize(
    ("version", "expected"),
    [("3.1.1", "Override"), ("3.0.3", "Declared")],
)
def test_reference_description_overrides_a_link_only_in_openapi_3_1(
    version: str, expected: str
) -> None:
    suite = response_suite(
        {
            "200": link_response(
                {"L": LocalReference(uri="#/components/links/L", description="Override")}
            )
        },
        components_with(
            other={"links": {"L": {"operationId": "a", "description": "Declared"}}}
        ),
        version=version,
    )

    assert baseline_expectation(suite).links[0].description == expected


def test_outermost_reference_description_wins_in_an_alias_chain() -> None:
    components = components_with(
        other={
            "headers": {
                "A": LocalReference(uri="#/components/headers/B", description="Alias"),
                "B": {"schema": {}, "description": "Declared"},
            }
        }
    )

    def description_for(entry: LocalReference) -> str | None:
        suite = response_suite({"200": header_response({"X-H": entry})}, components)
        return baseline_expectation(suite).headers[0].description

    assert description_for(LocalReference(uri="#/components/headers/A")) == "Alias"
    assert (
        description_for(LocalReference(uri="#/components/headers/A", description="Outer"))
        == "Outer"
    )


def test_reference_without_description_keeps_the_declared_description() -> None:
    suite = response_suite(
        {"200": header_response({"X-H": LocalReference(uri="#/components/headers/H")})},
        components_with(other={"headers": {"H": {"schema": {}, "description": "Declared"}}}),
    )

    assert baseline_expectation(suite).headers[0].description == "Declared"


# --- Step 8.41.4: version-aware server variable enum rules ---


def enum_link(variable: dict[str, object]) -> Response:
    return link_response(
        {
            "L": {
                "operationId": "a",
                "server": {"url": "https://{env}.example.com", "variables": {"env": variable}},
            },
            "Ok": {"operationId": "ok"},
        }
    )


@pytest.mark.parametrize(
    ("variable", "pointer_suffix", "message"),
    [
        ({"default": "a", "enum": []}, "enum", "must not be empty"),
        ({"default": "x", "enum": ["a", "b"]}, "default", "one of its 'enum' values"),
    ],
)
def test_openapi_3_1_server_variable_enum_rules_are_enforced(
    variable: dict[str, object], pointer_suffix: str, message: str
) -> None:
    suite = response_suite({"200": enum_link(variable)}, version="3.1.1")

    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_response_link"
    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/variables/env/{pointer_suffix}"
    assert message in diagnostic.message
    assert [link.name for link in baseline_expectation(suite).links] == ["Ok"]
    assert [case.category for case in suite.cases] == ["positive"]


def test_openapi_3_1_valid_enum_and_default_combination_is_kept() -> None:
    variable = {"default": "api", "enum": ["api", "dev"]}
    suite = response_suite({"200": enum_link(variable)}, version="3.1.1")

    assert suite.diagnostics == ()
    link = next(link for link in baseline_expectation(suite).links if link.name == "L")
    assert link.server is not None and link.server.variables == {"env": variable}


@pytest.mark.parametrize(
    "variable",
    [
        {"default": "a", "enum": []},
        {"default": "x", "enum": ["a", "b"]},
    ],
)
def test_openapi_3_0_keeps_accepting_empty_enum_and_default_outside_enum(
    variable: dict[str, object],
) -> None:
    suite = response_suite({"200": enum_link(variable)}, version="3.0.3")

    assert suite.diagnostics == ()
    link = next(link for link in baseline_expectation(suite).links if link.name == "L")
    assert link.server is not None and link.server.variables == {"env": variable}


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize(
    ("variable", "pointer_suffix"),
    [
        ({"default": "a", "enum": "a"}, "enum"),
        ({"default": "a", "enum": ["a", 2]}, "enum"),
        ({"enum": ["a"]}, "default"),
        ({"default": 1}, "default"),
    ],
)
def test_server_variable_type_and_required_checks_apply_to_both_versions(
    version: str, variable: dict[str, object], pointer_suffix: str
) -> None:
    suite = response_suite({"200": enum_link(variable)}, version=version)

    [diagnostic] = suite.diagnostics
    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/variables/env/{pointer_suffix}"
    assert [link.name for link in baseline_expectation(suite).links] == ["Ok"]


# --- Step 8.41.5: exact OpenAPI 3.1 detection ---

ACTIVE_VERSIONS = ["3.1.0", "3.1.1"]
INACTIVE_VERSIONS = ["3.0.0", "3.0.3", "3.10.0"]


@pytest.mark.parametrize("version", ACTIVE_VERSIONS)
def test_openapi_3_1_versions_are_detected(version: str) -> None:
    assert generator_module._is_openapi_3_1(version) is True


@pytest.mark.parametrize("version", [*INACTIVE_VERSIONS, "3.11.0", "30.1.0", "2.1.0", "3", ""])
def test_other_versions_are_not_detected_as_openapi_3_1(version: str) -> None:
    assert generator_module._is_openapi_3_1(version) is False


def description_override_result(version: str) -> str | None:
    suite = response_suite(
        {
            "200": header_response(
                {"X-H": LocalReference(uri="#/components/headers/H", description="Override")}
            )
        },
        components_with(other={"headers": {"H": {"schema": {}, "description": "Declared"}}}),
        version=version,
    )
    return baseline_expectation(suite).headers[0].description


@pytest.mark.parametrize("version", ACTIVE_VERSIONS)
def test_reference_description_override_is_active_for_openapi_3_1_x(version: str) -> None:
    assert description_override_result(version) == "Override"


@pytest.mark.parametrize("version", INACTIVE_VERSIONS)
def test_reference_description_override_is_inactive_outside_openapi_3_1_x(version: str) -> None:
    assert description_override_result(version) == "Declared"


@pytest.mark.parametrize("version", ACTIVE_VERSIONS)
def test_server_variable_enum_rules_are_active_for_openapi_3_1_x(version: str) -> None:
    suite = response_suite({"200": enum_link({"default": "a", "enum": []})}, version=version)

    [diagnostic] = suite.diagnostics
    assert diagnostic.source_pointer == f"{LINK_POINTER}/server/variables/env/enum"


@pytest.mark.parametrize("version", INACTIVE_VERSIONS)
def test_server_variable_enum_rules_are_inactive_outside_openapi_3_1_x(version: str) -> None:
    for variable in ({"default": "a", "enum": []}, {"default": "x", "enum": ["a"]}):
        suite = response_suite({"200": enum_link(variable)}, version=version)
        assert suite.diagnostics == ()


# --- Numeric valid boundary-edge cases (Step 8.42.1) -------------------------------------

EDGE_CODE = "valid_boundary_no_safe_candidate"


def edge_object(**schema: object) -> dict[str, object]:
    return {"type": "object", "required": ["v"], "properties": {"v": schema}}


def edge_suite(
    body: object, *, version: str = "3.1.1", schemas: dict[str, object] | None = None
) -> GeneratedTestSuite:
    endpoint = post_endpoint(body)
    if schemas:
        components = Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})  # type: ignore[arg-type]
        return generate_test_suite(make_result(endpoint, version=version, components=components))
    return generate_test_suite(make_result(endpoint, version=version))


def edge_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [case for case in suite.cases if is_valid_edge_case(case)]


def edge_summary(suite: GeneratedTestSuite) -> list[tuple[str, object]]:
    """(constraint:pointer, body) of each valid-edge case, in case order."""
    return [
        (case.provenance[-1].removeprefix("valid_boundary_edge:"), case.request_body.value)  # type: ignore[union-attr]
        for case in edge_cases(suite)
    ]


def edge_diagnostics(suite: GeneratedTestSuite) -> list[GenerationDiagnostic]:
    return [item for item in suite.diagnostics if item.code == EDGE_CODE]


def suite_without_edge_step(
    monkeypatch: pytest.MonkeyPatch, build: object
) -> GeneratedTestSuite:
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_numeric_valid_edge_cases", lambda *args: ((), ()))
        return build()  # type: ignore[operator]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "integer", "minimum": -3, "maximum": 10},
            [("minimum:/v", -3), ("maximum:/v", 10)],
        ),
        (
            {"type": "integer", "exclusiveMinimum": -3, "exclusiveMaximum": 10},
            [("exclusiveMinimum:/v", -2), ("exclusiveMaximum:/v", 9)],
        ),
        (
            {"type": "number", "minimum": -2.5, "maximum": 10.5},
            [("minimum:/v", -2.5), ("maximum:/v", 10.5)],
        ),
        (
            {"type": "number", "exclusiveMinimum": -2.5, "exclusiveMaximum": 10.5},
            [("exclusiveMinimum:/v", -2.4999999999999996), ("exclusiveMaximum:/v", 10.499999999999998)],
        ),
    ],
)
def test_each_numeric_bound_gets_an_edge_case_for_integer_and_number(
    schema: dict[str, object], expected: list[tuple[str, object]]
) -> None:
    suite = edge_suite(edge_object(**schema))

    assert suite.cases[0].request_body.value == {"v": 0}  # type: ignore[union-attr]
    assert edge_summary(suite) == [(name, {"v": value}) for name, value in expected]
    assert all(case.category == "positive" for case in edge_cases(suite))
    assert all(case.violated_constraint is None for case in edge_cases(suite))
    assert all(case.expected_outcome == "response_contract_unasserted" for case in edge_cases(suite))


def test_openapi_3_0_boolean_exclusive_bounds_target_the_exclusive_value() -> None:
    suite = edge_suite(
        edge_object(
            type="integer", minimum=-3, exclusiveMinimum=True, maximum=10, exclusiveMaximum=True
        ),
        version="3.0.3",
    )

    # The inclusive keywords are inactive; only the exclusive meaning is targeted.
    assert edge_summary(suite) == [
        ("exclusiveMinimum:/v", {"v": -2}),
        ("exclusiveMaximum:/v", {"v": 9}),
    ]


def test_openapi_3_0_false_exclusive_flags_keep_the_inclusive_bounds() -> None:
    suite = edge_suite(
        edge_object(
            type="integer", minimum=-3, exclusiveMinimum=False, maximum=10, exclusiveMaximum=False
        ),
        version="3.0.3",
    )

    assert edge_summary(suite) == [("minimum:/v", {"v": -3}), ("maximum:/v", {"v": 10})]


def test_openapi_3_1_numeric_exclusive_bounds_and_false_flags() -> None:
    numeric = edge_suite(edge_object(type="integer", exclusiveMinimum=-3, maximum=10))
    assert edge_summary(numeric) == [
        ("maximum:/v", {"v": 10}),
        ("exclusiveMinimum:/v", {"v": -2}),
    ]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer", "minimum": -3.5, "maximum": 10.5}, [-3, 10]),
        ({"type": "integer", "exclusiveMinimum": -3.5, "exclusiveMaximum": 10.5}, [-3, 10]),
        ({"type": "integer", "exclusiveMinimum": -3.0, "exclusiveMaximum": 10.0}, [-2, 9]),
    ],
)
def test_integer_schemas_with_fractional_bounds_use_the_nearest_integer_inside(
    schema: dict[str, object], expected: list[int]
) -> None:
    suite = edge_suite(edge_object(**schema))

    assert [body["v"] for _, body in edge_summary(suite)] == expected  # type: ignore[index]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer", "multipleOf": 5, "minimum": -7, "maximum": 23}, [-5, 20]),
        # The lower edge, 0, is the baseline value and adds no case.
        ({"type": "integer", "multipleOf": 5, "exclusiveMinimum": -5, "exclusiveMaximum": 25}, [20]),
        ({"type": "number", "multipleOf": 0.25, "minimum": -1.1, "maximum": 2.1}, [-1, 2]),
        ({"type": "number", "multipleOf": 0.3, "minimum": -1, "maximum": 2}, [-0.9, 1.8]),
    ],
)
def test_multiple_of_gives_the_nearest_multiple_inside_each_bound(
    schema: dict[str, object], expected: list[object]
) -> None:
    suite = edge_suite(edge_object(**schema))

    assert [body["v"] for _, body in edge_summary(suite)] == expected  # type: ignore[index]


def test_nested_property_edges_keep_the_rest_of_the_baseline_body() -> None:
    nested = {
        "type": "object",
        "required": ["a", "tag"],
        "properties": {
            "tag": {"type": "string", "example": "t"},
            "a": {
                "type": "object",
                "required": ["n"],
                "properties": {"n": {"type": "integer", "minimum": -3, "maximum": 10}},
            },
        },
    }

    suite = edge_suite(nested)

    assert suite.cases[0].request_body.value == {"tag": "t", "a": {"n": 0}}  # type: ignore[union-attr]
    assert edge_summary(suite) == [
        ("minimum:/a/n", {"tag": "t", "a": {"n": -3}}),
        ("maximum:/a/n", {"tag": "t", "a": {"n": 10}}),
    ]


def test_only_the_first_array_element_is_targeted() -> None:
    body = {
        "type": "array",
        "minItems": 2,
        "items": {
            "type": "object",
            "required": ["n"],
            "properties": {"n": {"type": "integer", "minimum": -3, "maximum": 10}},
        },
    }

    suite = edge_suite(body)

    assert suite.cases[0].request_body.value == [{"n": 0}, {"n": 0}]  # type: ignore[union-attr]
    assert edge_summary(suite) == [
        ("minimum:/0/n", [{"n": -3}, {"n": 0}]),
        ("maximum:/0/n", [{"n": 10}, {"n": 0}]),
    ]


def test_edges_follow_property_reference_chains() -> None:
    schemas = {
        "Outer": {"$ref": "#/components/schemas/Inner"},
        "Inner": {"type": "integer", "minimum": -3, "maximum": 10},
    }
    suite = edge_suite(edge_object(**schema_reference("Outer")), schemas=schemas)

    assert edge_summary(suite) == [("minimum:/v", {"v": -3}), ("maximum:/v", {"v": 10})]
    first = edge_cases(suite)[0]
    assert "/components/schemas/Inner" in first.provenance
    assert "/components/schemas/Inner/minimum" in first.provenance


def test_a_numeric_root_body_is_targeted() -> None:
    suite = edge_suite({"type": "integer", "minimum": -3, "maximum": 10})

    assert edge_summary(suite) == [("minimum:", -3), ("maximum:", 10)]


def test_optional_absent_properties_are_not_materialized() -> None:
    suite = edge_suite(
        {
            "type": "object",
            "required": ["v"],
            "properties": {
                "v": {"type": "integer", "minimum": 5},
                "opt": {"type": "integer", "minimum": -3, "maximum": 10},
            },
        }
    )

    assert suite.cases[0].request_body.value == {"v": 5}  # type: ignore[union-attr]
    assert edge_summary(suite) == []


def test_edge_equal_to_the_baseline_adds_no_case() -> None:
    both = edge_suite(edge_object(type="integer", minimum=5, maximum=10))
    only_minimum = edge_suite(edge_object(type="integer", minimum=5))

    assert both.cases[0].request_body.value == {"v": 5}  # type: ignore[union-attr]
    assert edge_summary(both) == [("maximum:/v", {"v": 10})]
    assert edge_summary(only_minimum) == []


def test_equal_bounds_and_equivalent_edges_give_one_case() -> None:
    equal = edge_suite(edge_object(type="integer", minimum=7, maximum=7, default=7))
    assert edge_summary(equal) == []

    # minimum -3 and exclusiveMinimum -4 both mean -3: one case, named after the first.
    twin = edge_suite(edge_object(type="integer", minimum=-3, exclusiveMinimum=-4))
    assert edge_summary(twin) == [("minimum:/v", {"v": -3})]
    assert edge_diagnostics(twin) == []


@pytest.mark.parametrize(
    "literal", [{"const": 5}, {"enum": [3, 5, 9]}, {"enum": [4, 9]}]
)
def test_const_and_enum_schemas_get_no_edge_case(literal: dict[str, object]) -> None:
    suite = edge_suite(edge_object(type="integer", minimum=4, maximum=9, **literal))

    assert edge_summary(suite) == []
    assert edge_diagnostics(suite) == []


def test_contradictory_bounds_have_no_candidate() -> None:
    candidate = generator_module._numeric_edge_candidate
    assert candidate({"type": "integer", "minimum": 10, "maximum": 5}, "minimum", "3.1.1") is None
    assert candidate({"type": "integer", "minimum": 10, "maximum": 5}, "maximum", "3.1.1") is None
    assert (
        candidate({"type": "integer", "exclusiveMinimum": 5, "exclusiveMaximum": 6}, "exclusiveMinimum", "3.1.1")
        is None
    )
    assert candidate({"type": "integer", "minimum": 1, "multipleOf": 0}, "minimum", "3.1.1") is None
    assert candidate({"type": "integer", "minimum": 1}, "maximum", "3.1.1") is None
    # An implicit format range is a validity limit, never a target.
    assert (
        candidate({"type": "integer", "format": "int32", "minimum": 2**40}, "minimum", "3.1.1")
        is None
    )


NEAREST_FLOAT_CASES = [
    # The decimal step 0.1 would give 0.2; the nearest float inside the bound wins.
    ({"type": "number", "exclusiveMinimum": 0.1, "maximum": 1.0}, "exclusiveMinimum", 0.10000000000000002),
    ({"type": "number", "exclusiveMinimum": 0.1, "maximum": 0.15}, "exclusiveMinimum", 0.10000000000000002),
    ({"type": "number", "minimum": 0.05, "exclusiveMaximum": 0.1}, "exclusiveMaximum", 0.09999999999999999),
    ({"type": "number", "minimum": 0.1, "exclusiveMaximum": 0.15}, "exclusiveMaximum", 0.14999999999999997),
    # High precision bounds.
    ({"type": "number", "exclusiveMinimum": 0.13436424411240122}, "exclusiveMinimum", 0.13436424411240125),
    ({"type": "number", "exclusiveMaximum": 0.13436424411240122}, "exclusiveMaximum", 0.1343642441124012),
    # Another active constraint moves the valid interval inward.
    ({"type": "number", "exclusiveMinimum": 0.1, "minimum": 0.5, "maximum": 1.0}, "exclusiveMinimum", 0.5),
    ({"type": "number", "exclusiveMaximum": 1.0, "minimum": 0.1, "maximum": 0.5}, "exclusiveMaximum", 0.5),
    ({"type": "number", "exclusiveMinimum": 0.1, "exclusiveMaximum": 3.0, "minimum": 2.5}, "exclusiveMinimum", 2.5),
    # Inclusive bounds stay on the bound.
    ({"type": "number", "minimum": 0.1, "maximum": 0.15}, "minimum", 0.1),
    ({"type": "number", "minimum": 0.1, "maximum": 0.15}, "maximum", 0.15),
]


@pytest.mark.parametrize(("schema", "constraint", "expected"), NEAREST_FLOAT_CASES)
def test_number_edge_is_the_nearest_representable_value_inside_the_bounds(
    schema: dict[str, object], constraint: str, expected: float
) -> None:
    candidate = generator_module._numeric_edge_candidate(schema, constraint, "3.1.1")

    assert candidate is not None and candidate[0] == expected
    # Checked in Decimal arithmetic against every supported constraint.
    assert generator_module._numeric_schema_constraints_match(
        schema, generator_module._as_decimal(expected), "3.1.1", ""  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("schema", "constraint", "expected"),
    [
        ({"type": "number", "exclusiveMinimum": 0.1, "maximum": 1.0}, "exclusiveMinimum", 0.10000000000000002),
        ({"type": "number", "minimum": 0.05, "exclusiveMaximum": 0.1}, "exclusiveMaximum", 0.09999999999999999),
        ({"type": "number", "minimum": 0.1, "exclusiveMaximum": 0.15}, "exclusiveMaximum", 0.14999999999999997),
    ],
)
def test_number_edge_cases_use_the_nearest_value(
    schema: dict[str, object], constraint: str, expected: float
) -> None:
    suite = edge_suite(edge_object(**schema))

    assert (f"{constraint}:/v", {"v": expected}) in edge_summary(suite)
    assert edge_diagnostics(suite) == []


def test_a_rounded_float_that_is_already_inside_the_bound_is_checked_first() -> None:
    from decimal import Decimal

    candidate = generator_module._numeric_edge_candidate
    # float(bound) is 0.1; its JSON text 0.1 is already inside both bounds.
    lower = {"type": "number", "exclusiveMinimum": Decimal("0.099999999999999999")}
    upper = {"type": "number", "exclusiveMaximum": Decimal("0.100000000000000001")}
    assert candidate(lower, "exclusiveMinimum", "3.1.1") == (0.1, "0.1")
    assert candidate(upper, "exclusiveMaximum", "3.1.1") == (0.1, "0.1")
    # A nearer float than the rounded one is used when it is still inside the bound.
    inclusive = {"type": "number", "minimum": Decimal("0.100000000000000001")}
    assert candidate(inclusive, "minimum", "3.1.1") == (0.10000000000000002, "0.10000000000000002")
    below = {"type": "number", "maximum": Decimal("0.099999999999999999")}
    assert candidate(below, "maximum", "3.1.1") == (0.09999999999999999, "0.09999999999999999")


def test_number_edges_respect_both_bounds_and_narrow_intervals() -> None:
    candidate = generator_module._numeric_edge_candidate
    both = {"type": "number", "exclusiveMinimum": 0.1, "exclusiveMaximum": 0.15}
    lower = candidate(both, "exclusiveMinimum", "3.1.1")
    upper = candidate(both, "exclusiveMaximum", "3.1.1")
    assert lower == (0.10000000000000002, "0.10000000000000002")
    assert upper == (0.14999999999999997, "0.14999999999999997")

    narrow = {"type": "number", "exclusiveMinimum": 0.1, "maximum": 0.10000000000000002}
    assert candidate(narrow, "exclusiveMinimum", "3.1.1") == (0.10000000000000002, "0.10000000000000002")
    assert candidate(narrow, "maximum", "3.1.1") == (0.10000000000000002, "0.10000000000000002")


def test_a_genuinely_impossible_float_interval_has_no_candidate() -> None:
    candidate = generator_module._numeric_edge_candidate
    # No float lies strictly between 0.1 and its successor.
    empty = {"type": "number", "exclusiveMinimum": 0.1, "exclusiveMaximum": 0.10000000000000002}
    assert candidate(empty, "exclusiveMinimum", "3.1.1") is None
    assert candidate(empty, "exclusiveMaximum", "3.1.1") is None
    assert candidate(
        {"type": "number", "exclusiveMinimum": 5, "exclusiveMaximum": 5}, "exclusiveMinimum", "3.1.1"
    ) is None
    # Another constraint pushes the nearest value past the other bound.
    assert candidate(
        {"type": "number", "exclusiveMinimum": 0.1, "minimum": 2.0, "maximum": 1.0}, "exclusiveMinimum", "3.1.1"
    ) is None
    # Nothing is generated for such a body: the baseline has no value either.
    assert edge_cases(edge_suite(edge_object(**empty))) == []


FLOAT_MAX = 1.7976931348623157e308
FLOAT_MAX_TEXT = "1.7976931348623157E+308"


def decimal_of(text: str):  # type: ignore[no-untyped-def]
    from decimal import Decimal

    return Decimal(text)


def extreme_candidate(schema: dict[str, object], constraint: str) -> tuple[object, object] | None:
    return generator_module._numeric_edge_candidate(schema, constraint, "3.1.1")


@pytest.mark.parametrize("constraint", ["minimum", "exclusiveMinimum"])
def test_a_lower_bound_below_the_finite_range_uses_the_smallest_finite_float(
    constraint: str,
) -> None:
    schema = {"type": "number", constraint: decimal_of("-1e400")}

    candidate = extreme_candidate(schema, constraint)

    assert candidate is not None
    assert decimal_of(str(candidate[0])) == decimal_of("-" + FLOAT_MAX_TEXT)
    assert candidate[1] == generator_module._decimal_key(decimal_of("-" + FLOAT_MAX_TEXT))


@pytest.mark.parametrize("constraint", ["minimum", "exclusiveMinimum"])
def test_a_lower_bound_above_the_finite_range_has_no_finite_candidate(constraint: str) -> None:
    assert extreme_candidate({"type": "number", constraint: decimal_of("1e400")}, constraint) is None


@pytest.mark.parametrize("constraint", ["maximum", "exclusiveMaximum"])
def test_an_upper_bound_above_the_finite_range_uses_the_largest_finite_float(
    constraint: str,
) -> None:
    schema = {"type": "number", constraint: decimal_of("1e400")}

    candidate = extreme_candidate(schema, constraint)

    assert candidate is not None
    assert decimal_of(str(candidate[0])) == decimal_of(FLOAT_MAX_TEXT)
    assert candidate[1] == generator_module._decimal_key(decimal_of(FLOAT_MAX_TEXT))


@pytest.mark.parametrize("constraint", ["maximum", "exclusiveMaximum"])
def test_an_upper_bound_below_the_finite_range_has_no_finite_candidate(constraint: str) -> None:
    assert extreme_candidate({"type": "number", constraint: decimal_of("-1e400")}, constraint) is None


def test_a_bound_at_the_edge_of_the_finite_range_keeps_strict_and_inclusive_semantics() -> None:
    top = decimal_of(FLOAT_MAX_TEXT)
    # Inclusive: the largest finite float itself. Exclusive: nothing lies above it.
    assert extreme_candidate({"type": "number", "minimum": top}, "minimum") is not None
    assert extreme_candidate({"type": "number", "exclusiveMinimum": top}, "exclusiveMinimum") is None
    assert extreme_candidate({"type": "number", "maximum": -top}, "maximum") is not None
    assert extreme_candidate({"type": "number", "exclusiveMaximum": -top}, "exclusiveMaximum") is None
    # Just inside the range, the next finite float below is used.
    assert extreme_candidate({"type": "number", "exclusiveMaximum": top}, "exclusiveMaximum") is not None


def test_an_extreme_float_candidate_is_validated_against_the_other_constraints() -> None:
    # The smallest finite float is not below this maximum, so no candidate is returned.
    schema = {
        "type": "number",
        "minimum": decimal_of("-1e400"),
        "maximum": decimal_of("-1e309"),
    }
    assert extreme_candidate(schema, "minimum") is None
    # With a satisfiable maximum the extreme float is accepted.
    schema = {"type": "number", "minimum": decimal_of("-1e400"), "maximum": 0}
    candidate = extreme_candidate(schema, "minimum")
    assert candidate is not None and decimal_of(str(candidate[0])) < 0
    # A stricter finite bound on the same side moves the nearest valid value inward.
    schema = {"type": "number", "minimum": decimal_of("-1e400"), "exclusiveMinimum": 5}
    assert extreme_candidate(schema, "exclusiveMinimum") == (5.000000000000001, "5.000000000000001")


def test_the_float_range_behavior_is_deterministic() -> None:
    schema = {"type": "number", "minimum": decimal_of("-1e400"), "maximum": decimal_of("1e400")}

    assert extreme_candidate(schema, "minimum") == extreme_candidate(deepcopy(schema), "minimum")
    assert extreme_candidate(schema, "maximum") == extreme_candidate(deepcopy(schema), "maximum")


def test_integer_and_multiple_of_candidates_never_use_the_float_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("the float search was used")

    monkeypatch.setattr(generator_module, "_nearest_float_edge_candidate", forbidden)
    candidate = generator_module._numeric_edge_candidate

    assert candidate({"type": "integer", "exclusiveMinimum": 5, "maximum": 6}, "exclusiveMinimum", "3.1.1") == (6, "6")
    assert candidate(
        {"type": "number", "multipleOf": 0.25, "exclusiveMinimum": 0.1, "maximum": 1}, "exclusiveMinimum", "3.1.1"
    ) == (0.25, "0.25")
    # A multipleOf schema with no multiple inside the interval has no candidate.
    assert candidate(
        {"type": "number", "multipleOf": 0.3, "exclusiveMinimum": 0.3, "maximum": 0.5},
        "exclusiveMinimum",
        "3.1.1",
    ) is None
    assert candidate({"type": "integer", "exclusiveMinimum": 5, "exclusiveMaximum": 6}, "exclusiveMinimum", "3.1.1") is None


def test_float_search_output_is_deterministic() -> None:
    body = edge_object(type="number", exclusiveMinimum=0.1, maximum=1.0)

    first = edge_suite(body)
    assert first.model_dump_json() == edge_suite(deepcopy(body)).model_dump_json()
    assert edge_summary(first) == [("exclusiveMinimum:/v", {"v": 0.10000000000000002})]


def test_implicit_format_limits_are_not_targets() -> None:
    suite = edge_suite(edge_object(type="integer", format="int32"))

    assert edge_summary(suite) == []
    assert edge_diagnostics(suite) == []


def test_ancestor_constraint_that_rejects_the_changed_body_blocks_the_case() -> None:
    body = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {
                "type": "object",
                "const": {"n": 7},
                "required": ["n"],
                "properties": {"n": {"type": "integer", "minimum": 5, "maximum": 9}},
            }
        },
    }

    suite = edge_suite(body)

    assert suite.cases[0].request_body.value == {"a": {"n": 7}}  # type: ignore[union-attr]
    assert edge_summary(suite) == []
    assert [item.source_pointer.rsplit("/", 1)[1] for item in edge_diagnostics(suite)] == [  # type: ignore[union-attr]
        "minimum",
        "maximum",
    ]


def test_edge_cases_are_deterministic_last_and_use_stable_ids() -> None:
    body = edge_object(type="integer", minimum=-3, maximum=10)
    first = edge_suite(body)
    second = edge_suite(deepcopy(body))

    assert first.model_dump_json() == second.model_dump_json()
    ids = [case.case_id for case in first.cases]
    assert len(ids) == len(set(ids))
    # Every negative case comes before every valid-edge case.
    kinds = [is_valid_edge_case(case) for case in first.cases]
    assert kinds == sorted(kinds)
    assert [case.case_id for case in edge_cases(first)] == [
        build_case_id("positive", "/items", "post", "numeric-valid-edge:/v:minimum:-3"),
        build_case_id("positive", "/items", "post", "numeric-valid-edge:/v:maximum:10"),
    ]


def test_existing_baselines_and_negative_cases_are_unchanged_by_the_edge_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["v", "w"],
        "properties": {
            "v": {"type": "integer", "minimum": -3, "maximum": 10},
            "w": {"type": "number", "exclusiveMinimum": -2.5},
        },
    }

    def build() -> GeneratedTestSuite:
        return edge_suite(body)

    with_step = build()
    without_step = suite_without_edge_step(monkeypatch, build)

    assert edge_cases(with_step)
    kept = without_valid_edge_cases(with_step)
    assert [case.model_dump_json() for case in kept] == [
        case.model_dump_json() for case in without_step.cases
    ]
    assert with_step.diagnostics[: len(without_step.diagnostics)] == without_step.diagnostics


def test_edge_cases_share_no_state_with_other_cases() -> None:
    suite = isolation_suite()
    source = next(case for case in suite.cases if is_valid_edge_case(case))
    for mutate in ISOLATION_MUTATIONS.values():
        before = [case.model_dump(mode="json") for case in suite.cases]
        mutate(source)
        after = [case.model_dump(mode="json") for case in suite.cases]
        index = suite.cases.index(source)
        assert after[index] != before[index]
        assert [item for i, item in enumerate(after) if i != index] == [
            item for i, item in enumerate(before) if i != index
        ]

    # The body is an independent copy of the baseline body.
    nested = edge_suite(
        {
            "type": "object",
            "required": ["a"],
            "properties": {
                "a": {
                    "type": "object",
                    "required": ["n", "m"],
                    "properties": {
                        "n": {"type": "integer", "minimum": -3},
                        "m": {"type": "object", "example": {"k": [1]}},
                    },
                }
            },
        }
    )
    [edge] = edge_cases(nested)
    baseline_before = nested.cases[0].model_dump(mode="json")
    edge.request_body.value["a"]["m"]["k"].append(2)  # type: ignore[union-attr, index]
    edge.request_body.value["a"]["n"] = 99  # type: ignore[union-attr, index]
    assert nested.cases[0].model_dump(mode="json") == baseline_before


def test_the_edge_step_does_not_mutate_its_input() -> None:
    body = edge_object(type="integer", minimum=-3, maximum=10)
    result = make_result(post_endpoint(body))
    before = result.model_dump_json()

    generate_test_suite(result)

    assert result.model_dump_json() == before


def edge_endpoint_suite() -> GeneratedTestSuite:
    return edge_suite(edge_object(type="integer", minimum=-3, maximum=10))


def test_endpoint_case_limit_keeps_every_negative_case_and_drops_only_the_edge_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = edge_endpoint_suite()
    retained = len(without_valid_edge_cases(full))
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", retained)

    suite = edge_endpoint_suite()

    assert [case.case_id for case in suite.cases] == [
        case.case_id for case in without_valid_edge_cases(full)
    ]
    [budget] = [item for item in suite.diagnostics if item.code == "generation_case_budget_exceeded"]
    assert "'numeric_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []


def test_suite_case_limit_keeps_every_negative_case_and_drops_only_the_edge_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = len(without_valid_edge_cases(edge_endpoint_suite()))
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", retained)

    suite = edge_endpoint_suite()

    assert edge_cases(suite) == []
    assert len(suite.cases) == retained
    assert "generation_case_budget_exceeded" in {item.code for item in suite.diagnostics}


def test_the_edge_step_is_all_or_nothing_under_the_case_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = len(without_valid_edge_cases(edge_endpoint_suite()))
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", retained + 1)  # room for one of two

    suite = edge_endpoint_suite()

    assert edge_cases(suite) == []
    assert len(suite.cases) == retained


def test_expansion_budget_exhaustion_in_the_edge_step_is_a_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas = {"Num": {"type": "integer", "minimum": -3, "maximum": 10}}
    body = edge_object(**schema_reference("Num"))

    def build() -> GeneratedTestSuite:
        return edge_suite(body, schemas=schemas)

    without_step = suite_without_edge_step(monkeypatch, build)
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_numeric_valid_edge_cases", lambda *args: ((), ()))
        needed = count_expansions(
            make_result(
                post_endpoint(body),
                components=Components(schemas={"Num": Schema(raw=schemas["Num"])}),  # type: ignore[arg-type]
            )
        )
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", needed)

    suite = build()

    assert [case.case_id for case in suite.cases] == [case.case_id for case in without_step.cases]
    [budget] = [item for item in suite.diagnostics if item.code == "generation_expansion_budget_exceeded"]
    assert "'numeric_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []
    assert not any(item.code == "unsupported_schema_reference" for item in suite.diagnostics)


def test_pattern_budget_exhaustion_in_the_edge_step_is_a_budget_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["v", "s"],
        "properties": {
            "v": {"type": "integer", "minimum": -3, "maximum": 10},
            "s": {"type": "string", "pattern": "^[a-z]+$"},
        },
    }
    without_step = suite_without_edge_step(monkeypatch, lambda: edge_suite(body))
    original = generator_module._matches

    def exhausted_on_edge_bodies(schema: object, value: object, *args: object) -> bool:
        # Only the valid-edge step validates a whole body whose "v" is an edge value.
        if isinstance(value, dict) and value.get("v") in (-3, 10):
            raise generator_module._BudgetExhausted(
                generator_module._PATTERN_BUDGET_CODE, "endpoint", 1, "/pattern", None
            )
        return original(schema, value, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_matches", exhausted_on_edge_bodies)

    suite = edge_suite(body)

    assert [case.case_id for case in suite.cases] == [case.case_id for case in without_step.cases]
    [budget] = [item for item in suite.diagnostics if item.code == "generation_pattern_budget_exceeded"]
    assert "'numeric_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []


def test_an_edge_body_does_not_recharge_a_pattern_evaluated_by_the_baseline() -> None:
    body = {
        "type": "object",
        "required": ["v", "s"],
        "properties": {
            "v": {"type": "integer", "minimum": -3, "maximum": 10},
            "s": {"type": "string", "pattern": "^[a-z]+$"},
        },
    }
    charges: list[int] = []
    original = generator_module._GenerationBudget.charge_pattern_evaluation

    def counting(self: object, pointer: object, work: int) -> None:
        charges.append(work)
        original(self, pointer, work)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator_module._GenerationBudget, "charge_pattern_evaluation", counting)
        with_step = edge_suite(body)
        with_step_charges = len(charges)
        charges.clear()
        patch.setattr(generator_module, "_numeric_valid_edge_cases", lambda *args: ((), ()))
        edge_suite(body)

    assert len(edge_cases(with_step)) == 2
    assert with_step_charges == len(charges)


def test_unsupported_schema_diagnostics_are_not_duplicated_by_the_edge_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["v"],
        "properties": {
            "v": {"type": "integer", "minimum": -3, "maximum": 10},
            "gone": {"$ref": "#/components/schemas/Missing"},
        },
    }

    def build() -> GeneratedTestSuite:
        return edge_suite(body)

    without_step = suite_without_edge_step(monkeypatch, build)
    with_step = build()

    assert [item.code for item in without_step.diagnostics].count("unsupported_schema_reference") >= 1
    assert with_step.diagnostics == without_step.diagnostics
    assert len(edge_cases(with_step)) == 2


def test_edge_cases_carry_the_step_8_41_response_expectations() -> None:
    endpoint = make_endpoint(
        "/items",
        "post",
        request_body=body_with_schema(edge_object(type="integer", minimum=-3, maximum=10)),
        responses={
            "200": Response(
                description="OK",
                content={
                    "application/json": MediaType(schema=Schema(raw=schema_reference("Problem")))
                },
            ),
            "400": Response(description="Invalid request"),
        },
    )
    suite = generate_test_suite(
        make_result_with_schemas(endpoint, {"Problem": {"type": "object"}})
    )

    baseline = suite.cases[0]
    resolved = baseline.response_expectations[0].resolved_schemas_by_media_type
    assert resolved["application/json"].reference_chain == ("#/components/schemas/Problem",)
    for case in edge_cases(suite):
        assert case.response_expectations == baseline.response_expectations
        assert case.response_expectations is not baseline.response_expectations
        edge_resolved = case.response_expectations[0].resolved_schemas_by_media_type
        assert edge_resolved["application/json"].resolved is not resolved["application/json"].resolved
    assert len(edge_cases(suite)) == 2


# --- Effective-bound ownership of valid-edge cases (Step 8.42.1) -------------------------


def edge_identities(suite: GeneratedTestSuite) -> list[tuple[str, str, object, bool]]:
    """(constraint, keyword pointer tail, value, ID matches the expected ID) per edge case."""
    found = []
    for case in edge_cases(suite):
        constraint = case.provenance[-1].split(":")[1]
        value = case.request_body.value["v"]  # type: ignore[union-attr, index]
        key = generator_module._decimal_key(generator_module._as_decimal(value))  # type: ignore[arg-type]
        expected = build_case_id(
            "positive", "/items", "post", f"numeric-valid-edge:/v:{constraint}:{key}"
        )
        assert case.provenance[-1] == f"valid_boundary_edge:{constraint}:/v"
        assert case.provenance[-2].endswith(f"/properties/v/{constraint}")
        assert case.provenance[-3].endswith("/properties/v")
        found.append((constraint, case.provenance[-2].rsplit("/", 1)[1], value, case.case_id == expected))
    return found


@pytest.mark.parametrize(
    ("schema", "owner", "value", "weaker"),
    [
        # exclusiveMinimum 6 is stricter than minimum 5.
        ({"minimum": 5, "exclusiveMinimum": 6, "maximum": 10}, "exclusiveMinimum", 6.000000000000001, "minimum"),
        # minimum 6 is stricter than exclusiveMinimum 5; the default keeps 6 off the baseline.
        ({"minimum": 6, "exclusiveMinimum": 5, "default": 7}, "minimum", 6, "exclusiveMinimum"),
        # exclusiveMaximum 9 is stricter than maximum 10.
        ({"minimum": 1, "maximum": 10, "exclusiveMaximum": 9}, "exclusiveMaximum", 8.999999999999998, "maximum"),
        # maximum 9 is stricter than exclusiveMaximum 10.
        ({"minimum": 1, "maximum": 9, "exclusiveMaximum": 10}, "maximum", 9, "exclusiveMaximum"),
    ],
)
def test_only_the_stricter_same_side_constraint_owns_the_edge(
    schema: dict[str, object], owner: str, value: float, weaker: str
) -> None:
    suite = edge_suite(edge_object(type="number", **schema))

    same_side = [item for item in edge_identities(suite) if item[0] in (owner, weaker)]
    assert same_side == [(owner, owner, value, True)]
    assert edge_diagnostics(suite) == []


def test_a_weaker_integer_bound_no_longer_reports_a_false_diagnostic() -> None:
    suite = edge_suite(edge_object(type="integer", minimum=5, exclusiveMinimum=6, maximum=20))

    # The baseline holds 20 (the maximum); the weaker minimum 5 is invalid anyway.
    assert suite.cases[0].request_body.value == {"v": 20}  # type: ignore[union-attr]
    assert edge_identities(suite) == [("exclusiveMinimum", "exclusiveMinimum", 7, True)]
    assert edge_diagnostics(suite) == []


@pytest.mark.parametrize(
    ("schema", "owner", "value"),
    [
        ({"minimum": 5, "exclusiveMinimum": 5, "maximum": 10}, "exclusiveMinimum", 5.000000000000001),
        ({"minimum": 1, "maximum": 9, "exclusiveMaximum": 9}, "exclusiveMaximum", 8.999999999999998),
    ],
)
def test_the_exclusive_constraint_owns_an_equal_bound(
    schema: dict[str, object], owner: str, value: float
) -> None:
    suite = edge_suite(edge_object(type="number", **schema))

    inclusive = "minimum" if owner == "exclusiveMinimum" else "maximum"
    assert [item for item in edge_identities(suite) if item[0] in (owner, inclusive)] == [
        (owner, owner, value, True)
    ]


def test_equivalent_effective_bounds_keep_one_deterministic_case() -> None:
    # minimum -3 and exclusiveMinimum -4 both admit -3 first; minimum is stricter.
    body = edge_object(type="integer", minimum=-3, exclusiveMinimum=-4, maximum=10)
    first = edge_suite(body)
    second = edge_suite(deepcopy(body))

    assert edge_identities(first) == [
        ("minimum", "minimum", -3, True),
        ("maximum", "maximum", 10, True),
    ]
    assert [case.case_id for case in edge_cases(first)] == [
        build_case_id("positive", "/items", "post", "numeric-valid-edge:/v:minimum:-3"),
        build_case_id("positive", "/items", "post", "numeric-valid-edge:/v:maximum:10"),
    ]
    assert first.model_dump_json() == second.model_dump_json()


def test_openapi_3_0_boolean_modifiers_keep_their_ownership() -> None:
    flagged = edge_suite(
        edge_object(type="number", minimum=5, exclusiveMinimum=True, maximum=10, exclusiveMaximum=True),
        version="3.0.3",
    )
    plain = edge_suite(
        edge_object(type="number", minimum=-3, exclusiveMinimum=False, maximum=10),
        version="3.0.3",
    )

    assert [item[:3] for item in edge_identities(flagged)] == [
        ("exclusiveMinimum", "exclusiveMinimum", 5.000000000000001),
        ("exclusiveMaximum", "exclusiveMaximum", 9.999999999999998),
    ]
    assert all(item[3] for item in edge_identities(flagged))
    assert [item[:3] for item in edge_identities(plain)] == [
        ("minimum", "minimum", -3),
        ("maximum", "maximum", 10),
    ]


def test_edge_bound_owners_per_side_and_dialect() -> None:
    owners = generator_module._edge_bound_owners
    assert owners({"minimum": 5, "exclusiveMinimum": 6, "maximum": 10}, "3.1.1") == {
        "exclusiveMinimum",
        "maximum",
    }
    assert owners({"minimum": 6, "exclusiveMinimum": 5}, "3.1.1") == {"minimum"}
    assert owners({"maximum": 10, "exclusiveMaximum": 9}, "3.1.1") == {"exclusiveMaximum"}
    assert owners({"maximum": 9, "exclusiveMaximum": 10}, "3.1.1") == {"maximum"}
    assert owners({"minimum": 5, "exclusiveMinimum": 5}, "3.1.1") == {"exclusiveMinimum"}
    assert owners({"minimum": 5, "exclusiveMinimum": True}, "3.0.3") == {"exclusiveMinimum"}
    assert owners({"minimum": 5, "exclusiveMinimum": False}, "3.0.3") == {"minimum"}
    # An implicit format range stricter than the declared bound is owned by no keyword.
    assert owners({"format": "int32", "minimum": -(2**40), "maximum": 5}, "3.1.1") == {"maximum"}


def test_ownership_output_is_repeatable() -> None:
    body = edge_object(type="number", minimum=5, exclusiveMinimum=6, maximum=10)

    assert edge_suite(body).model_dump_json() == edge_suite(deepcopy(body)).model_dump_json()


# --- String valid boundary-edge cases (Step 8.42.2.1) ------------------------------------


def string_edge_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [
        case
        for case in suite.cases
        if is_valid_edge_case(case)
        and case.provenance[-1].split(":")[1] in ("minLength", "maxLength")
    ]


def string_edges(suite: GeneratedTestSuite) -> list[tuple[str, object]]:
    """(constraint:pointer, body) of each string valid-edge case, in case order.

    Also asserts the provenance shape and the exact ID of every case.
    """
    found = []
    for case in string_edge_cases(suite):
        label = case.provenance[-1].removeprefix("valid_boundary_edge:")
        constraint, pointer = label.split(":", 1)
        assert case.category == "positive"
        assert case.violated_constraint is None and case.affected_property is None
        assert case.provenance[-2].endswith(f"/{constraint}")
        assert case.provenance[-3] == case.provenance[-2].rsplit("/", 1)[0]
        length = len(_string_at(case.request_body.value, pointer))  # type: ignore[union-attr]
        assert case.case_id == build_case_id(
            "positive", "/items", "post", f"string-valid-edge:{pointer}:{constraint}:{length}"
        )
        found.append((label, case.request_body.value))  # type: ignore[union-attr]
    return found


def _string_at(body: object, pointer: str) -> str:
    value = body
    for part in [item for item in pointer.split("/") if item]:
        value = value[int(part)] if isinstance(value, list) else value[part]  # type: ignore[index]
    assert isinstance(value, str)
    return value


def string_object(**schema: object) -> dict[str, object]:
    return {"type": "object", "required": ["v"], "properties": {"v": {"type": "string", **schema}}}


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_min_length_only_gets_a_minimum_edge(version: str) -> None:
    suite = edge_suite(string_object(minLength=3), version=version)

    assert suite.cases[0].request_body.value == {"v": "sentinel"}  # type: ignore[union-attr]
    assert string_edges(suite) == [("minLength:/v", {"v": "xxx"})]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_max_length_only_gets_a_maximum_edge(version: str) -> None:
    suite = edge_suite(string_object(maxLength=20), version=version)

    assert string_edges(suite) == [("maxLength:/v", {"v": "x" * 20})]


def test_both_constraints_give_two_cases_in_keyword_order() -> None:
    suite = edge_suite(string_object(minLength=2, maxLength=20))

    assert string_edges(suite) == [
        ("minLength:/v", {"v": "xx"}),
        ("maxLength:/v", {"v": "x" * 20}),
    ]


@pytest.mark.parametrize("bounds", [{"minLength": 3, "maxLength": 3}, {"minLength": 12, "maxLength": 12}])
def test_equal_bounds_are_covered_by_the_baseline_length(bounds: dict[str, int]) -> None:
    suite = edge_suite(string_object(**bounds))

    assert len(suite.cases[0].request_body.value["v"]) == bounds["minLength"]  # type: ignore[union-attr, index]
    assert string_edges(suite) == []


def test_a_baseline_at_the_minimum_length_skips_the_minimum_edge() -> None:
    suite = edge_suite(string_object(minLength=5, maxLength=20, example="hello"))

    assert suite.cases[0].request_body.value == {"v": "hello"}  # type: ignore[union-attr]
    # Deduplication is by length: "hello" has the minimum length, "xxxxx" would repeat it.
    assert string_edges(suite) == [("maxLength:/v", {"v": "x" * 20})]


def test_a_baseline_at_the_maximum_length_skips_the_maximum_edge() -> None:
    suite = edge_suite(string_object(minLength=1, maxLength=3))

    assert suite.cases[0].request_body.value == {"v": "sen"}  # type: ignore[union-attr]
    assert string_edges(suite) == [("minLength:/v", {"v": "x"})]


def test_min_length_zero_gives_the_empty_string() -> None:
    suite = edge_suite(string_object(minLength=0))

    assert suite.cases[0].request_body.value == {"v": "sentinel"}  # type: ignore[union-attr]
    assert string_edges(suite) == [("minLength:/v", {"v": ""})]
    assert edge_suite(string_object(minLength=0, example="")).cases[0].request_body.value == {"v": ""}  # type: ignore[union-attr]
    assert string_edges(edge_suite(string_object(minLength=0, example=""))) == []


def test_max_length_zero_is_covered_by_the_empty_baseline() -> None:
    for bounds in ({"maxLength": 0}, {"minLength": 0, "maxLength": 0}):
        suite = edge_suite(string_object(**bounds))

        assert suite.cases[0].request_body.value == {"v": ""}  # type: ignore[union-attr]
        assert string_edges(suite) == []


def test_type_arrays_containing_string_are_targeted() -> None:
    schema = {
        "type": "object",
        "required": ["v"],
        "properties": {"v": {"type": ["string", "null"], "minLength": 2, "maxLength": 20}},
    }

    suite = edge_suite(schema)

    assert string_edges(suite) == [("minLength:/v", {"v": "xx"}), ("maxLength:/v", {"v": "x" * 20})]


def test_nullable_schemas_and_null_baselines_get_no_edge() -> None:
    nullable_30 = edge_suite(string_object(nullable=True, minLength=2, maxLength=20), version="3.0.3")
    assert string_edges(nullable_30) == [
        ("minLength:/v", {"v": "xx"}),
        ("maxLength:/v", {"v": "x" * 20}),
    ]

    null_31 = {
        "type": "object",
        "required": ["v"],
        "properties": {"v": {"type": ["string", "null"], "minLength": 2, "default": None}},
    }
    suite = edge_suite(null_31)
    assert suite.cases[0].request_body.value == {"v": None}  # type: ignore[union-attr]
    assert string_edges(suite) == []


def test_a_schema_without_a_declared_string_type_is_not_targeted() -> None:
    suite = edge_suite({"type": "object", "required": ["v"], "properties": {"v": {"minLength": 3}}})

    assert string_edges(suite) == []


def test_nested_object_properties_keep_the_rest_of_the_baseline() -> None:
    body = {
        "type": "object",
        "required": ["a", "tag"],
        "properties": {
            "tag": {"type": "string", "example": "t"},
            "a": {
                "type": "object",
                "required": ["s"],
                "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 20}},
            },
        },
    }

    suite = edge_suite(body)

    assert suite.cases[0].request_body.value == {"tag": "t", "a": {"s": "sentinel"}}  # type: ignore[union-attr]
    assert string_edges(suite) == [
        ("minLength:/a/s", {"tag": "t", "a": {"s": "xx"}}),
        ("maxLength:/a/s", {"tag": "t", "a": {"s": "x" * 20}}),
    ]


def test_only_the_first_array_element_is_targeted_for_strings() -> None:
    body = {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 3, "maxLength": 20}}

    suite = edge_suite(body)

    assert suite.cases[0].request_body.value == ["sentinel", "sentinel"]  # type: ignore[union-attr]
    assert string_edges(suite) == [
        ("minLength:/0", ["xxx", "sentinel"]),
        ("maxLength:/0", ["x" * 20, "sentinel"]),
    ]


def test_string_edges_follow_property_reference_chains() -> None:
    schemas = {
        "Outer": {"$ref": "#/components/schemas/Inner"},
        "Inner": {"type": "string", "minLength": 3, "maxLength": 20},
    }
    suite = edge_suite(
        {"type": "object", "required": ["v"], "properties": {"v": schema_reference("Outer")}},
        schemas=schemas,
    )

    assert string_edges(suite) == [("minLength:/v", {"v": "xxx"}), ("maxLength:/v", {"v": "x" * 20})]
    first = string_edge_cases(suite)[0]
    assert "/components/schemas/Inner" in first.provenance
    assert "/components/schemas/Inner/minLength" in first.provenance


def test_a_root_string_body_is_targeted() -> None:
    suite = edge_suite({"type": "string", "minLength": 3, "maxLength": 20})

    assert string_edges(suite) == [("minLength:", "xxx"), ("maxLength:", "x" * 20)]


def test_optional_absent_and_read_only_properties_are_not_materialized() -> None:
    suite = edge_suite(
        {
            "type": "object",
            "required": ["v", "ro"],
            "properties": {
                "v": {"type": "string", "example": "abc"},
                "ro": {"type": "string", "readOnly": True, "minLength": 3, "maxLength": 9},
                "opt": {"type": "string", "minLength": 3, "maxLength": 9},
            },
        }
    )

    assert suite.cases[0].request_body.value == {"v": "abc"}  # type: ignore[union-attr]
    assert string_edges(suite) == []


def test_pattern_candidates_are_only_validated_never_synthesized() -> None:
    accepted = edge_suite(string_object(minLength=3, maxLength=20, pattern="^[a-z]+$"))
    assert string_edges(accepted) == [
        ("minLength:/v", {"v": "xxx"}),
        ("maxLength:/v", {"v": "x" * 20}),
    ]

    for pattern in ("^a[a-z]+$", "^[a-z]{2}$"):
        rejected = edge_suite(string_object(minLength=2, maxLength=20, pattern=pattern, example="ab"))
        assert rejected.cases[0].request_body.value == {"v": "ab"}  # type: ignore[union-attr]
        assert string_edges(rejected) == []
        assert edge_diagnostics(rejected) == []


def test_format_constrained_strings_are_skipped() -> None:
    for format_name, kwargs in (
        ("email", {"maxLength": 20}),
        ("date", {"minLength": 5, "maxLength": 10}),
        ("uuid", {"minLength": 5, "maxLength": 40}),
    ):
        assert string_edges(edge_suite(string_object(format=format_name, **kwargs))) == []


@pytest.mark.parametrize("literal", [{"const": "abcd"}, {"enum": ["abcd", "abcde"]}])
def test_const_and_enum_strings_are_skipped(literal: dict[str, object]) -> None:
    assert string_edges(edge_suite(string_object(minLength=2, maxLength=20, **literal))) == []


def test_ancestor_constraints_that_reject_the_changed_body_skip_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {
                "type": "object",
                "const": {"s": "abcdef"},
                "required": ["s"],
                "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 10}},
            }
        },
    }

    suite = edge_suite(body)
    without_step = suite_without_edge_step(monkeypatch, lambda: edge_suite(body))

    assert suite.cases[0].request_body.value == {"a": {"s": "abcdef"}}  # type: ignore[union-attr]
    assert string_edges(suite) == []
    assert suite.diagnostics == without_step.diagnostics


def test_the_candidate_length_limit_is_one_hundred() -> None:
    at_limit = edge_suite(string_object(minLength=2, maxLength=100))
    above = edge_suite(string_object(minLength=2, maxLength=101))

    assert string_edges(at_limit) == [
        ("minLength:/v", {"v": "xx"}),
        ("maxLength:/v", {"v": "x" * 100}),
    ]
    assert string_edges(above) == [("minLength:/v", {"v": "xx"})]
    assert edge_diagnostics(above) == []


def test_edge_order_is_traversal_then_min_then_max_with_stable_ids() -> None:
    body = {
        "type": "object",
        "required": ["b", "a"],
        "properties": {
            "b": {"type": "string", "minLength": 2, "maxLength": 20},
            "a": {"type": "string", "minLength": 3, "maxLength": 30},
        },
    }
    first = edge_suite(body)
    second = edge_suite(deepcopy(body))

    assert [label for label, _ in string_edges(first)] == [
        "minLength:/a",
        "maxLength:/a",
        "minLength:/b",
        "maxLength:/b",
    ]
    assert first.model_dump_json() == second.model_dump_json()
    ids = [case.case_id for case in first.cases]
    assert len(ids) == len(set(ids))
    kinds = [is_valid_edge_case(case) for case in first.cases]
    assert kinds == sorted(kinds)  # every negative case comes before every valid-edge case
    assert string_edge_cases(first)[0].provenance[-3:] == (
        "/paths/~1items/post/requestBody/content/application~1json/schema/properties/a",
        "/paths/~1items/post/requestBody/content/application~1json/schema/properties/a/minLength",
        "valid_boundary_edge:minLength:/a",
    )


def test_string_edge_cases_share_no_state_and_do_not_mutate_the_input() -> None:
    suite = isolation_suite()
    source = next(
        case for case in suite.cases if case.provenance[-1] == "valid_boundary_edge:minLength:/name"
    )
    for mutate in ISOLATION_MUTATIONS.values():
        before = [case.model_dump(mode="json") for case in suite.cases]
        mutate(source)
        after = [case.model_dump(mode="json") for case in suite.cases]
        index = suite.cases.index(source)
        assert after[index] != before[index]
        assert [item for i, item in enumerate(after) if i != index] == [
            item for i, item in enumerate(before) if i != index
        ]

    body = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {
                "type": "object",
                "required": ["s", "m"],
                "properties": {
                    "s": {"type": "string", "minLength": 2},
                    "m": {"type": "object", "example": {"k": [1]}},
                },
            }
        },
    }
    result = make_result(post_endpoint(body))
    before_input = result.model_dump_json()
    nested = generate_test_suite(result)
    assert result.model_dump_json() == before_input
    [edge] = string_edge_cases(nested)
    baseline_before = nested.cases[0].model_dump(mode="json")
    edge.request_body.value["a"]["m"]["k"].append(2)  # type: ignore[union-attr, index]
    edge.request_body.value["a"]["s"] = "changed"  # type: ignore[union-attr, index]
    assert nested.cases[0].model_dump(mode="json") == baseline_before


def mixed_edge_body() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["n", "s"],
        "properties": {
            "n": {"type": "integer", "minimum": -3, "maximum": 10},
            "s": {"type": "string", "minLength": 2, "maxLength": 20},
        },
    }


def test_numeric_valid_edge_cases_are_unchanged_by_the_string_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_step = edge_suite(mixed_edge_body())
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
        without_step = edge_suite(mixed_edge_body())

    assert [case.model_dump_json() for case in with_step.cases[: len(without_step.cases)]] == [
        case.model_dump_json() for case in without_step.cases
    ]
    assert with_step.diagnostics == without_step.diagnostics
    assert [case.provenance[-1] for case in with_step.cases[len(without_step.cases) :]] == [
        "valid_boundary_edge:minLength:/s",
        "valid_boundary_edge:maxLength:/s",
    ]
    assert [case.provenance[-1] for case in edge_cases(without_step)] == [
        "valid_boundary_edge:minimum:/n",
        "valid_boundary_edge:maximum:/n",
    ]


def test_endpoint_case_limit_drops_only_the_string_step(monkeypatch: pytest.MonkeyPatch) -> None:
    full = edge_suite(mixed_edge_body())
    retained = len(full.cases) - 2
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", retained)

    suite = edge_suite(mixed_edge_body())

    assert [case.case_id for case in suite.cases] == [case.case_id for case in full.cases[:retained]]
    assert len(edge_cases(suite)) == 2  # the numeric edges are kept
    [budget] = [item for item in suite.diagnostics if item.code == "generation_case_budget_exceeded"]
    assert "'string_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []


def test_suite_case_limit_drops_only_the_string_step(monkeypatch: pytest.MonkeyPatch) -> None:
    full = edge_suite(mixed_edge_body())
    monkeypatch.setattr(generator_module, "_MAX_SUITE_CASES", len(full.cases) - 2)

    suite = edge_suite(mixed_edge_body())

    assert string_edge_cases(suite) == []
    assert len(edge_cases(suite)) == 2
    assert "generation_case_budget_exceeded" in {item.code for item in suite.diagnostics}


def test_the_string_step_is_all_or_nothing_under_the_case_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = edge_suite(mixed_edge_body())
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_CASES", len(full.cases) - 1)  # room for one of two

    suite = edge_suite(mixed_edge_body())

    assert string_edge_cases(suite) == []
    assert len(suite.cases) == len(full.cases) - 2


def test_expansion_exhaustion_in_the_string_step_keeps_the_numeric_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas = {
        "Num": {"type": "integer", "minimum": -3, "maximum": 10},
        "Str": {"type": "string", "minLength": 2, "maxLength": 20},
    }
    body = {
        "type": "object",
        "required": ["n", "s"],
        "properties": {"n": schema_reference("Num"), "s": schema_reference("Str")},
    }

    def build() -> GeneratedTestSuite:
        return edge_suite(body, schemas=schemas)

    components = Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})  # type: ignore[arg-type]
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
        without_string = build()
        needed = count_expansions(make_result(post_endpoint(body), components=components))
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_EXPANSIONS", needed)

    suite = build()

    assert [case.case_id for case in suite.cases] == [case.case_id for case in without_string.cases]
    assert len(edge_cases(suite)) == 2  # numeric edges survive
    assert string_edge_cases(suite) == []
    [budget] = [item for item in suite.diagnostics if item.code == "generation_expansion_budget_exceeded"]
    assert "'string_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []


def test_pattern_exhaustion_in_the_string_step_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "type": "object",
        "required": ["n", "s"],
        "properties": {
            "n": {"type": "integer", "minimum": -3, "maximum": 10},
            "s": {"type": "string", "minLength": 2, "maxLength": 20, "pattern": "^[a-z]+$"},
        },
    }
    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_string_valid_edge_cases", lambda *args: ((), ()))
        without_step = edge_suite(body)
    original = generator_module._matches

    def exhausted_on_edge_candidates(schema: object, value: object, *args: object) -> bool:
        # Only the string step validates exactly these strings.
        if value in ("xx", "x" * 20):
            raise generator_module._BudgetExhausted(
                generator_module._PATTERN_BUDGET_CODE, "endpoint", 1, "/pattern", None
            )
        return original(schema, value, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_matches", exhausted_on_edge_candidates)

    suite = edge_suite(body)

    assert string_edge_cases(suite) == []
    assert [case.case_id for case in suite.cases] == [case.case_id for case in without_step.cases]
    [budget] = [item for item in suite.diagnostics if item.code == "generation_pattern_budget_exceeded"]
    assert "'string_valid_edge'" in budget.message
    assert edge_diagnostics(suite) == []


# --- Bounded validation work for valid boundary edges (Step 8.43.1) ----------------------

VALIDATION_CODE = "generation_validation_budget_exceeded"
NUMERIC_BODY = edge_object(type="integer", minimum=1, maximum=5)
STRING_BODY = string_object(minLength=2, maxLength=5)
TWO_NUMBERS_BODY = {
    "type": "object",
    "required": ["a", "b"],
    "properties": {
        "a": {"type": "integer", "minimum": 1, "maximum": 5},
        "b": {"type": "integer", "minimum": 1, "maximum": 5},
    },
}
NUMBER_AND_STRING_BODY = {
    "type": "object",
    "required": ["n", "s"],
    "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 5},
        "s": {"type": "string", "minLength": 2, "maxLength": 5},
    },
}


def validation_run(
    monkeypatch: pytest.MonkeyPatch,
    *bodies: object,
    endpoint_limit: int | None = None,
    suite_limit: int | None = None,
) -> tuple[GeneratedTestSuite, "generator_module._GenerationBudget"]:
    """Generate one endpoint per body and return the suite and its budget."""
    created: list[generator_module._GenerationBudget] = []

    class RecordingBudget(generator_module._GenerationBudget):
        def __init__(self) -> None:
            super().__init__()
            created.append(self)

    monkeypatch.setattr(generator_module, "_GenerationBudget", RecordingBudget)
    if endpoint_limit is not None:
        monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_VALIDATION_UNITS", endpoint_limit)
    if suite_limit is not None:
        monkeypatch.setattr(generator_module, "_MAX_SUITE_VALIDATION_UNITS", suite_limit)
    endpoints = [post_endpoint(body, f"/items{index}") for index, body in enumerate(bodies)]
    suite = generate_test_suite(make_result(*endpoints))
    [budget] = created
    return suite, budget


def validation_diagnostics(suite: GeneratedTestSuite) -> list[GenerationDiagnostic]:
    return [item for item in suite.diagnostics if item.code == VALIDATION_CODE]


def case_keys(cases: object) -> list[tuple[str, object]]:
    return [(case.case_id, case.request_body) for case in cases]  # type: ignore[attr-defined]


def valid_edge_cases(suite: GeneratedTestSuite) -> list[GeneratedTestCase]:
    return [case for case in suite.cases if is_valid_edge_case(case)]


def wide_array_body(items: int, props: int, kind: str = "integer") -> dict[str, object]:
    leaf = {
        "integer": {"type": "integer", "minimum": 0, "maximum": 9},
        "string": {"type": "string", "minLength": 1, "maxLength": 5},
    }[kind]
    return {
        "type": "array",
        "minItems": items,
        "items": {
            "type": "object",
            "required": [f"p{i}" for i in range(props)],
            "properties": {f"p{i}": dict(leaf) for i in range(props)},
        },
    }


def wide_array_units(items: int, props: int, edges: int) -> int:
    """Units of the valid-edge step for wide_array_body, counted by hand.

    The root check enters the array schema, its items schema and each required
    property. A candidate body enters the array and every item object once, and
    for every property of every item both its schema check and its value.
    """
    root_check = 1 + (1 + props)
    per_candidate = 1 + items * (1 + props) + (1 + props) + items * props
    return root_check + edges * per_candidate


def node_count(value: object) -> int:
    if isinstance(value, list):
        return 1 + sum(node_count(item) for item in value)
    if isinstance(value, dict):
        return 1 + sum(node_count(item) for item in value.values())
    return 1


def nested_body(depth: int, width: int) -> dict[str, object]:
    schema: dict[str, object] = {"type": "integer", "minimum": 0, "maximum": 9}
    for _ in range(depth):
        properties = {f"p{i}": {"type": "integer", "minimum": 0, "maximum": 9} for i in range(width)}
        schema = {
            "type": "object",
            "required": [*properties, "n"],
            "properties": {**properties, "n": schema},
        }
    return schema


def test_validation_budget_constants_and_code() -> None:
    assert generator_module._VALIDATION_BUDGET_CODE == VALIDATION_CODE
    assert generator_module._VALIDATION_UNIT == "validation work units"
    assert generator_module._MAX_ENDPOINT_VALIDATION_UNITS == 600_000
    assert generator_module._MAX_SUITE_VALIDATION_UNITS == 4_800_000
    assert (
        generator_module._MAX_SUITE_VALIDATION_UNITS
        == 8 * generator_module._MAX_ENDPOINT_VALIDATION_UNITS
    )


def test_one_unit_is_one_entry_into_matches_or_check_schema_with_a_meter() -> None:
    token, budget = with_budget()
    try:
        meter = generator_module._validation_meter("/p")
        schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}

        # The object schema and its required property.
        generator_module._check_schema(schema, "3.1.1", "/p", "request", 0, meter)
        assert budget.suite_validation_units == 2

        # The object value, then the property's schema check and the property value.
        assert generator_module._matches(schema, {"a": 1}, "3.1.1", "request", 0, "/p", meter)
        assert budget.suite_validation_units == 2 + 3
        assert budget.endpoint_validation_units == 5
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_calls_without_a_meter_are_neither_charged_nor_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_VALIDATION_UNITS", 1)
    monkeypatch.setattr(generator_module, "_MAX_SUITE_VALIDATION_UNITS", 1)
    token, budget = with_budget()
    try:
        schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}

        for _ in range(10):
            generator_module._check_schema(schema, "3.1.1", "/p", "request", 0)
            assert generator_module._matches(schema, {"a": 1}, "3.1.1", "request", 0, "/p")

        assert budget.suite_validation_units == 0
        assert budget.endpoint_validation_units == 0
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


def test_no_meter_exists_outside_a_generation_call() -> None:
    assert generator_module._validation_meter("/p") is None


def test_other_steps_validate_candidates_without_validation_charges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No length or numeric bound, so no valid-edge candidate is ever validated. The
    # enum, wrong-type and required steps still call _matches.
    body = {
        "type": "object",
        "required": ["v", "w"],
        "properties": {
            "v": {"type": "string", "pattern": "^[a-z]+$", "enum": ["abc", "def"]},
            "w": {"type": "integer", "enum": [1, 2, 3]},
        },
    }
    calls = {"matches": 0}
    original = generator_module._matches

    def counting(*args: object) -> bool:
        calls["matches"] += 1
        return original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_matches", counting)

    suite, budget = validation_run(monkeypatch, body)

    assert calls["matches"] > 0
    assert len(suite.cases) > 3
    assert valid_edge_cases(suite) == []
    # The only validation work is the root schema check of the valid-edge steps:
    # the object and its two required properties. The other steps' calls are free.
    assert budget.suite_validation_units == 3
    assert validation_diagnostics(suite) == []


def test_numeric_valid_edge_validation_work_is_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    suite, budget = validation_run(monkeypatch, NUMERIC_BODY)

    assert len(valid_edge_cases(suite)) == 1
    # Root check: the object schema and its required property (2). The one
    # candidate body: the object (1), then the property's schema check and value (2).
    assert budget.suite_validation_units == 2 + 3
    assert budget.endpoint_validation_units == 5
    # Validation never charges the other budgets.
    assert budget.suite_expansions == 0
    assert budget.suite_pattern_evaluations == 0
    assert budget.suite_cases == len(suite.cases)


def test_string_valid_edge_validation_work_is_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    suite, budget = validation_run(monkeypatch, STRING_BODY)

    assert len(valid_edge_cases(suite)) == 1
    # Root check (2). The target string: its schema check and value (2). The
    # candidate body: the object, then the property's schema check and value (3).
    assert budget.suite_validation_units == 2 + 2 + 3
    assert budget.suite_expansions == 0
    assert budget.suite_pattern_evaluations == 0
    assert budget.suite_cases == len(suite.cases)


@pytest.mark.parametrize(
    ("body", "step", "units"),
    [(NUMERIC_BODY, "numeric_valid_edge", 5), (STRING_BODY, "string_valid_edge", 7)],
)
@pytest.mark.parametrize("scope", ["endpoint", "suite"])
def test_validation_limit_is_exact(
    monkeypatch: pytest.MonkeyPatch, body: object, step: str, units: int, scope: str
) -> None:
    limits = lambda value: {f"{scope}_limit": value}  # noqa: E731
    with monkeypatch.context() as patch:
        enough, _ = validation_run(patch, body, **limits(units))
    with monkeypatch.context() as patch:
        short, budget = validation_run(patch, body, **limits(units - 1))

    assert len(valid_edge_cases(enough)) == 1
    assert validation_diagnostics(enough) == []

    assert valid_edge_cases(short) == []
    [diagnostic] = validation_diagnostics(short)
    assert f"The per-{scope} limit of {units - 1} validation work units" in diagnostic.message
    assert f"'{step}'" in diagnostic.message
    assert diagnostic.endpoint_path == "/items0"
    assert diagnostic.source_pointer.endswith("/requestBody/content/application~1json/schema")  # type: ignore[union-attr]
    # The unit that crossed the limit is counted, as for the other work budgets.
    assert budget.suite_validation_units == units
    assert case_keys(short.cases) == case_keys(without_valid_edge_cases(enough))


def test_exhaustion_discards_every_case_of_the_step_even_after_valid_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        full, budget = validation_run(patch, TWO_NUMBERS_BODY)
    units = budget.suite_validation_units
    assert len(valid_edge_cases(full)) == 2

    with monkeypatch.context() as patch:
        # The first candidate is fully validated; the second runs out of work.
        short, _ = validation_run(patch, TWO_NUMBERS_BODY, endpoint_limit=units - 1)

    assert valid_edge_cases(short) == []
    assert len(validation_diagnostics(short)) == 1


def test_baseline_negative_and_earlier_step_cases_survive_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        full, _ = validation_run(patch, NUMBER_AND_STRING_BODY)
    assert [c.violated_constraint for c in full.cases if c.category == "negative"]
    assert len(valid_edge_cases(full)) == 2

    with monkeypatch.context() as patch:
        # The first valid-edge step cannot even check its root schema.
        short, _ = validation_run(patch, NUMBER_AND_STRING_BODY, endpoint_limit=1)

    assert full.cases[0].category == "positive" and short.cases[0] == full.cases[0]
    assert valid_edge_cases(short) == []
    assert case_keys(short.cases) == case_keys(without_valid_edge_cases(full))
    assert [c.category for c in short.cases].count("negative") > 0


def test_a_later_step_exhaustion_keeps_the_earlier_valid_edge_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    numeric_end: list[int] = []
    original = generator_module._numeric_valid_edge_cases

    def record_numeric_end(*args: object) -> object:
        result = original(*args)  # type: ignore[arg-type]
        numeric_end.append(generator_module._BUDGET.get().endpoint_validation_units)  # type: ignore[union-attr]
        return result

    with monkeypatch.context() as patch:
        patch.setattr(generator_module, "_numeric_valid_edge_cases", record_numeric_end)
        full, budget = validation_run(patch, NUMBER_AND_STRING_BODY)
    [numeric_units] = numeric_end
    assert numeric_units < budget.suite_validation_units
    numeric_edges = [c for c in valid_edge_cases(full) if "minimum" in c.provenance[-1] or "maximum" in c.provenance[-1]]
    assert len(numeric_edges) == 1 and len(valid_edge_cases(full)) == 2

    # Enough work for the numeric step, none left for the string step.
    with monkeypatch.context() as patch:
        short, _ = validation_run(patch, NUMBER_AND_STRING_BODY, endpoint_limit=numeric_units)
    assert case_keys(valid_edge_cases(short)) == case_keys(numeric_edges)
    [diagnostic] = validation_diagnostics(short)
    assert "'string_valid_edge'" in diagnostic.message
    assert case_keys(without_valid_edge_cases(short)) == case_keys(without_valid_edge_cases(full))

    # One unit less and the numeric step fails; the string step after it is skipped
    # by the existing policy for an exhausted endpoint.
    with monkeypatch.context() as patch:
        none, _ = validation_run(patch, NUMBER_AND_STRING_BODY, endpoint_limit=numeric_units - 1)
    assert valid_edge_cases(none) == []
    [diagnostic] = validation_diagnostics(none)
    assert "'numeric_valid_edge'" in diagnostic.message


def test_endpoint_limit_exhaustion_does_not_affect_the_next_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heavy = wide_array_body(6, 4)

    suite, budget = validation_run(monkeypatch, heavy, NUMERIC_BODY, endpoint_limit=5)

    [diagnostic] = validation_diagnostics(suite)
    assert diagnostic.endpoint_path == "/items0"
    assert "The per-endpoint limit of 5 validation work units" in diagnostic.message
    assert [c.path for c in valid_edge_cases(suite)] == ["/items1"]
    # The heavy endpoint kept its baseline and the next endpoint was generated whole.
    assert {c.path for c in suite.cases} == {"/items0", "/items1"}
    assert budget.endpoint_validation_units == 5
    assert budget.suite_validation_units > 5 + 5


def test_suite_limit_exhaustion_keeps_earlier_endpoints_and_stops_later_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        full, _ = validation_run(patch, NUMERIC_BODY, NUMERIC_BODY, NUMERIC_BODY)

    # One endpoint needs 5 units, so the second runs out of suite work.
    suite, budget = validation_run(
        monkeypatch, NUMERIC_BODY, NUMERIC_BODY, NUMERIC_BODY, suite_limit=7
    )

    [diagnostic] = validation_diagnostics(suite)
    assert diagnostic.endpoint_path == "/items1"
    assert "The per-suite limit of 7 validation work units" in diagnostic.message
    assert "1 following endpoint(s) were not generated" in diagnostic.message
    first = [c for c in full.cases if c.path == "/items0"]
    second = [c for c in full.cases if c.path == "/items1"]
    assert case_keys([c for c in suite.cases if c.path == "/items0"]) == case_keys(first)
    assert case_keys([c for c in suite.cases if c.path == "/items1"]) == case_keys(
        without_valid_edge_cases_of(second)
    )
    assert [c for c in suite.cases if c.path == "/items2"] == []
    assert budget.suite_validation_units == 8


def without_valid_edge_cases_of(cases: list[GeneratedTestCase]) -> list[GeneratedTestCase]:
    return [case for case in cases if not is_valid_edge_case(case)]


@pytest.mark.parametrize("limit", [None, 12, 4])
def test_identical_runs_produce_identical_cases_diagnostics_and_work(
    monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    runs = []
    for _ in range(3):
        with monkeypatch.context() as patch:
            suite, budget = validation_run(
                patch, NUMBER_AND_STRING_BODY, TWO_NUMBERS_BODY, endpoint_limit=limit
            )
        runs.append((suite.model_dump(mode="json"), budget.suite_validation_units))

    assert runs[0] == runs[1] == runs[2]
    if limit is not None:
        assert any(item["code"] == VALIDATION_CODE for item in runs[0][0]["diagnostics"])


@pytest.mark.parametrize(("items", "props"), [(3, 2), (6, 5), (12, 5)])
def test_wide_array_work_follows_body_size_and_not_the_case_count(
    monkeypatch: pytest.MonkeyPatch, items: int, props: int
) -> None:
    suite, budget = validation_run(monkeypatch, wide_array_body(items, props))

    edges = valid_edge_cases(suite)
    assert len(edges) == props
    assert budget.suite_validation_units == wide_array_units(items, props, props)
    assert budget.suite_expansions == 0
    assert budget.suite_cases == len(suite.cases)


def test_more_items_cost_more_validation_work_for_the_same_number_of_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        small, small_budget = validation_run(patch, wide_array_body(6, 5))
    with monkeypatch.context() as patch:
        large, large_budget = validation_run(patch, wide_array_body(12, 5))

    assert len(valid_edge_cases(small)) == len(valid_edge_cases(large)) == 5
    assert large_budget.suite_validation_units > 1.5 * small_budget.suite_validation_units


def test_100_items_of_50_constrained_properties_fit_the_default_validation_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, budget = validation_run(monkeypatch, wide_array_body(100, 50))

    assert len(suite.cases[0].request_body.value) == 100  # type: ignore[union-attr,arg-type]
    assert len(valid_edge_cases(suite)) == 50
    assert validation_diagnostics(suite) == []
    # Counted work, not time: the step needs exactly this many units.
    assert budget.suite_validation_units == wide_array_units(100, 50, 50) == 507_652
    assert budget.endpoint_validation_units < generator_module._MAX_ENDPOINT_VALIDATION_UNITS
    assert budget.suite_validation_units < generator_module._MAX_SUITE_VALIDATION_UNITS


@pytest.mark.parametrize("kind", ["integer", "string"])
def test_large_array_of_constrained_objects_stops_at_the_limit_without_running_on(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # The scenario of the Final Audit: 100 items of 100 constrained properties.
    # Unbounded, the step makes about two million validation entries.
    body = wide_array_body(100, 100, kind)
    unbounded = wide_array_units(100, 100, 100)
    assert unbounded > 2_000_000

    suite, budget = validation_run(monkeypatch, body, endpoint_limit=50_000)

    assert len(suite.cases[0].request_body.value) == 100  # type: ignore[union-attr,arg-type]
    assert valid_edge_cases(suite) == []
    [diagnostic] = validation_diagnostics(suite)
    assert "limit of 50000 validation work units" in diagnostic.message
    # The work stopped on the first unit over the limit, not after the whole step.
    assert budget.suite_validation_units == 50_001


@pytest.mark.parametrize("depth", [4, 8, 15])
def test_validation_units_equal_the_traversal_actually_performed_in_nested_schemas(
    monkeypatch: pytest.MonkeyPatch, depth: int
) -> None:
    metered = {"matches": 0, "checks": 0}
    original_matches = generator_module._matches
    original_check = generator_module._check_schema

    def counting_matches(*args: object) -> bool:
        metered["matches"] += len(args) > 6 and args[6] is not None
        return original_matches(*args)  # type: ignore[arg-type]

    def counting_check(*args: object) -> object:
        metered["checks"] += len(args) > 5 and args[5] is not None
        return original_check(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_matches", counting_matches)
    monkeypatch.setattr(generator_module, "_check_schema", counting_check)

    suite, budget = validation_run(monkeypatch, nested_body(depth, 5))

    edges = valid_edge_cases(suite)
    nodes = node_count(suite.cases[0].request_body.value)  # type: ignore[union-attr]
    assert edges
    assert budget.suite_validation_units == metered["matches"] + metered["checks"]
    # One entry per value node and candidate, plus the schema checks. A count that
    # ignored the schema traversal would be len(edges) * nodes.
    assert metered["matches"] == len(edges) * nodes
    assert metered["checks"] > 2 * len(edges) * nodes if depth >= 8 else metered["checks"] > 0


def test_nested_schemas_cost_more_than_their_body_size_alone_suggests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        shallow, shallow_budget = validation_run(patch, nested_body(4, 5))
    with monkeypatch.context() as patch:
        deep, deep_budget = validation_run(patch, nested_body(8, 5))

    shallow_nodes = node_count(shallow.cases[0].request_body.value)  # type: ignore[union-attr]
    deep_nodes = node_count(deep.cases[0].request_body.value)  # type: ignore[union-attr]
    shallow_edges = len(valid_edge_cases(shallow))
    deep_edges = len(valid_edge_cases(deep))
    shallow_per = shallow_budget.suite_validation_units / (shallow_edges * shallow_nodes)
    deep_per = deep_budget.suite_validation_units / (deep_edges * deep_nodes)

    # Units per candidate value node grow with the nesting depth.
    assert deep_per > shallow_per


# --- Annotation metadata: xml, externalDocs and format password (Step 8.43.2) ------------

ANNOTATION_VERSIONS = ["3.0.3", "3.1.1"]
XML_ANNOTATION: dict[str, object] = {
    "name": "v",
    "namespace": "https://example.com/schema",
    "prefix": "ex",
    "attribute": False,
    "x-note": 1,
}
EXTERNAL_DOCS_ANNOTATION: dict[str, object] = {
    "url": "https://example.com/docs",
    "description": "More detail",
    "x-note": 1,
}
ANNOTATIONS: dict[str, object] = {
    "xml": XML_ANNOTATION,
    "externalDocs": EXTERNAL_DOCS_ANNOTATION,
}
ANNOTATION_BASE_SCHEMAS: list[dict[str, object]] = [
    {"type": "string", "minLength": 2, "maxLength": 6},
    {"type": "integer", "minimum": 1, "maximum": 9},
    {"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "minLength": 1}}},
]


def dump_suite(suite: GeneratedTestSuite) -> dict[str, object]:
    return suite.model_dump(mode="json")


def assert_unsupported_codes_absent(suite: GeneratedTestSuite) -> None:
    assert not [d for d in suite.diagnostics if d.code.startswith("unsupported_")], suite.diagnostics


def only_diagnostic(suite: GeneratedTestSuite) -> GenerationDiagnostic:
    assert suite.cases == ()
    [diagnostic] = suite.diagnostics
    return diagnostic


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("keyword", sorted(ANNOTATIONS))
@pytest.mark.parametrize("base", ANNOTATION_BASE_SCHEMAS)
def test_xml_and_external_docs_on_a_property_schema_change_nothing(
    keyword: str, base: dict[str, object], version: str
) -> None:
    plain = format_suite(base, version)
    annotated = format_suite({**base, keyword: ANNOTATIONS[keyword]}, version)

    assert len(plain.cases) > 2
    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)
    assert [(c.case_id, c.provenance) for c in annotated.cases] == [
        (c.case_id, c.provenance) for c in plain.cases
    ]


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("base", ANNOTATION_BASE_SCHEMAS)
def test_format_password_on_a_string_schema_is_accepted_and_changes_nothing(
    base: dict[str, object], version: str
) -> None:
    string_base = {"type": "string", "minLength": 2, "maxLength": 6}
    for schema in (string_base, {"type": "string"}):
        plain = format_suite(schema, version)
        annotated = format_suite({**schema, "format": "password"}, version)

        assert len(plain.cases) > 2
        assert_unsupported_codes_absent(annotated)
        assert dump_suite(annotated) == dump_suite(plain)


def test_format_password_accepts_a_nullable_string_in_each_version() -> None:
    for version, schema in (
        ("3.0.3", {"type": "string", "nullable": True, "minLength": 2}),
        ("3.1.1", {"type": ["string", "null"], "minLength": 2}),
    ):
        plain = format_suite(schema, version)
        annotated = format_suite({**schema, "format": "password"}, version)

        assert_unsupported_codes_absent(annotated)
        assert dump_suite(annotated) == dump_suite(plain)


def test_format_password_keeps_the_string_edge_and_boundary_cases() -> None:
    suite = format_suite({"type": "string", "format": "password", "minLength": 3, "maxLength": 8})

    lengths = sorted(
        len(case.request_body.value["v"])
        for case in suite.cases
        if case.request_body and isinstance(case.request_body.value.get("v"), str)
    )
    assert 3 in lengths and 8 in lengths
    assert any(is_valid_edge_case(case) for case in suite.cases)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("keyword", sorted(ANNOTATIONS))
@pytest.mark.parametrize("place", ["root", "items", "reference_target"])
def test_annotations_are_accepted_in_every_schema_object_position_without_effect(
    place: str, keyword: str, version: str
) -> None:
    value = ANNOTATIONS[keyword]
    leaf = {"type": "string", "minLength": 2, "maxLength": 6}

    def build(annotated: bool) -> GeneratedTestSuite:
        extra = {keyword: value} if annotated else {}
        components = None
        if place == "root":
            raw: dict[str, object] = {
                "type": "object", "required": ["v"], "properties": {"v": leaf}, **extra,
            }
        elif place == "items":
            raw = {
                "type": "object", "required": ["v"],
                "properties": {"v": {"type": "array", "minItems": 2, "items": {**leaf, **extra}}},
            }
        else:
            raw = {
                "type": "object", "required": ["v"],
                "properties": {"v": {"$ref": "#/components/schemas/Leaf"}},
            }
            components = Components(schemas={"Leaf": Schema(raw={**leaf, **extra})})
        endpoint = make_endpoint(
            "/items", "post", request_body=body_with_schema(raw), responses=BAD_REQUEST
        )
        return generate_test_suite(make_result(endpoint, version=version, components=components))

    plain = build(False)

    assert len(plain.cases) > 2
    assert_unsupported_codes_absent(build(True))
    assert dump_suite(build(True)) == dump_suite(plain)


def test_xml_and_external_docs_are_only_keywords_inside_a_schema_not_property_names() -> None:
    raw = {
        "type": "object",
        "required": ["xml", "externalDocs", "format"],
        "properties": {
            "xml": {"type": "string", "minLength": 2},
            "externalDocs": {"type": "integer", "minimum": 1},
            "format": {"type": "string", "minLength": 3},
        },
    }
    endpoint = make_endpoint("/items", "post", request_body=body_with_schema(raw), responses=BAD_REQUEST)

    suite = generate_test_suite(make_result(endpoint))

    assert_unsupported_codes_absent(suite)
    assert set(suite.cases[0].request_body.value) == {"xml", "externalDocs", "format"}


MALFORMED_ANNOTATIONS: list[tuple[str, object, str, str]] = [
    ("xml", "x", "/xml", "xml must be an object"),
    ("xml", [], "/xml", "xml must be an object"),
    ("xml", None, "/xml", "xml must be an object"),
    ("xml", 5, "/xml", "xml must be an object"),
    ("xml", {"foo": 1}, "/xml/foo", "xml has the unsupported field 'foo'"),
    ("xml", {"name": 5}, "/xml/name", "xml.name must be a string"),
    ("xml", {"namespace": True}, "/xml/namespace", "xml.namespace must be a string"),
    ("xml", {"prefix": None}, "/xml/prefix", "xml.prefix must be a string"),
    ("xml", {"attribute": "true"}, "/xml/attribute", "xml.attribute must be a boolean"),
    ("xml", {"wrapped": 1}, "/xml/wrapped", "xml.wrapped must be a boolean"),
    ("externalDocs", "x", "/externalDocs", "externalDocs must be an object"),
    ("externalDocs", None, "/externalDocs", "externalDocs must be an object"),
    ("externalDocs", {}, "/externalDocs", "externalDocs requires 'url'"),
    ("externalDocs", {"description": "d"}, "/externalDocs", "externalDocs requires 'url'"),
    ("externalDocs", {"url": 5}, "/externalDocs/url", "externalDocs.url must be a string"),
    (
        "externalDocs", {"url": "https://e.test", "description": 3},
        "/externalDocs/description", "externalDocs.description must be a string",
    ),
    (
        "externalDocs", {"url": "https://e.test", "foo": 1},
        "/externalDocs/foo", "externalDocs has the unsupported field 'foo'",
    ),
]


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(("keyword", "value", "suffix", "message"), MALFORMED_ANNOTATIONS)
def test_malformed_annotation_values_are_rejected_with_a_form_diagnostic(
    keyword: str, value: object, suffix: str, message: str, version: str
) -> None:
    suite = format_suite({"type": "string", keyword: value}, version)

    diagnostic = only_diagnostic(suite)
    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v" + suffix)
    assert message in diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    "schema",
    [
        {"format": "password"},
        {"type": "integer", "format": "password"},
        {"type": "number", "format": "password"},
        {"type": "boolean", "format": "password"},
        {"type": "array", "items": {"type": "string"}, "format": "password"},
        {"type": "object", "format": "password"},
    ],
)
def test_format_password_on_a_non_string_schema_is_rejected(
    schema: dict[str, object], version: str
) -> None:
    diagnostic = only_diagnostic(format_suite(schema, version))

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/format")
    assert "Schema format 'password' requires the string type." == diagnostic.message


@pytest.mark.parametrize("schema_type", ["null", ["integer", "null"], ["null"]])
def test_format_password_on_a_non_string_openapi_31_type_is_rejected(
    schema_type: object,
) -> None:
    diagnostic = only_diagnostic(format_suite({"type": schema_type, "format": "password"}, "3.1.1"))

    assert diagnostic.code == "unsupported_schema_form"
    assert "Schema format 'password' requires the string type." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_format_password_must_be_a_string_value(version: str) -> None:
    diagnostic = only_diagnostic(format_suite({"type": "string", "format": ["password"]}, version))

    assert diagnostic.code == "unsupported_schema_form"
    assert "Schema format must be a string." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    "keyword",
    [
        "discriminator", "$comment", "minProperties", "maxProperties", "patternProperties",
        "contentEncoding", "contentMediaType", "XML", "externaldocs", "x", "unknownKeyword",
    ],
)
def test_other_unknown_keywords_remain_rejected(keyword: str, version: str) -> None:
    diagnostic = only_diagnostic(format_suite({"type": "string", keyword: "x"}, version))

    assert diagnostic.code == "unsupported_schema_keyword"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith(f"/properties/v/{keyword}")
    assert f"Schema keyword '{keyword}' is outside the supported subset." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    "name", ["uri", "binary", "byte", "hostname", "ipv4", "Password", "PASSWORD", "passwords", ""]
)
def test_other_unsupported_formats_remain_rejected(name: str, version: str) -> None:
    diagnostic = only_diagnostic(format_suite({"type": "string", "format": name}, version))

    assert diagnostic.code == "unsupported_schema_keyword"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/format")
    assert "Schema keyword 'format' is outside the supported subset." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("xml", XML_ANNOTATION),
        ("externalDocs", EXTERNAL_DOCS_ANNOTATION),
        ("format", "password"),
        ("maxLength", 2),
    ],
)
def test_reference_sibling_handling_is_unchanged_for_the_new_annotations(
    keyword: str, value: object, version: str
) -> None:
    schemas = {"Id": Schema(raw={"type": "string", "minLength": 2})}
    endpoint = format_endpoint({"$ref": "#/components/schemas/Id", keyword: value})

    suite = generate_test_suite(
        make_result(endpoint, version=version, components=Components(schemas=schemas))
    )

    diagnostic = only_diagnostic(suite)
    assert diagnostic.code == "unsupported_schema_reference_siblings"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith(f"/properties/v/{keyword}")


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_documents_with_the_annotations_pass_project_validation_and_generate_the_same_suite(
    version: str,
) -> None:
    from app.services.openapi.validator import validate_openapi_document

    def document(annotated: bool) -> dict[str, object]:
        def mark(schema: dict[str, object], **extra: object) -> dict[str, object]:
            return {**schema, **extra} if annotated else schema

        return {
            "openapi": version,
            "info": {"title": "Annotated", "version": "1.0"},
            "paths": {
                "/accounts": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Account"}
                                }
                            },
                        },
                        "responses": {"400": {"description": "Invalid"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "Account": mark(
                        {
                            "type": "object",
                            "required": ["login", "secret", "tags"],
                            "properties": {
                                "login": mark(
                                    {"type": "string", "minLength": 3, "maxLength": 12},
                                    xml={"name": "user", "attribute": True},
                                ),
                                "secret": mark(
                                    {"type": "string", "minLength": 8}, format="password"
                                ),
                                "tags": mark(
                                    {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": mark(
                                            {"type": "string", "minLength": 1},
                                            xml={"name": "tag"},
                                        ),
                                    },
                                    xml={"wrapped": True},
                                ),
                            },
                        },
                        externalDocs={"url": "https://example.com/docs"},
                    )
                }
            },
        }

    suites = []
    for annotated in (False, True):
        validated = validate_openapi_document(document(annotated))
        suites.append(generate_test_suite(normalize_openapi_document(validated)))
    plain, annotated_suite = suites

    assert len(plain.cases) > 5
    assert_unsupported_codes_absent(annotated_suite)
    assert dump_suite(annotated_suite) == dump_suite(plain)


# --- Annotation review corrections: URIs and xml.wrapped (Step 8.43.2) ----------------

from app.services.openapi.uri_references import (  # noqa: E402
    _is_valid_absolute_uri,
    _is_valid_uri_reference,
)

VALID_URI_REFERENCES = [
    "https://example.com/docs",
    "http://example.com:8080/a/b?c=d#e",
    "docs/page.html",
    "../up/page.html",
    "#section",
    "//example.com/docs",
    "mailto:docs@example.com",
    "urn:example:docs",
    "https://example.com/a%20b",
    "http://[2001:db8::1]/docs",
    "https://example.com/list?x%5B%5D=1",
]
INVALID_URI_REFERENCES = [
    "",
    "not a url",
    "http://a b/",
    "ht!tp://x",
    "1http://x",
    ":nope",
    "http://[bad",
    "http://host:abc/",
    "http://host:99999/",
    "https://example.com/%zz",
    "https://example.com/a%2",
    "https://example.com/文档",
    "http://a\nb",
    "https://exa\tmple.com",
    "<https://example.com>",
    "https://example.com/list?x[]=1",
]
RELATIVE_URI_REFERENCES = ["docs/page.html", "../up", "#section", "//example.com/ns", "a/b:c"]


@pytest.mark.parametrize("value", VALID_URI_REFERENCES)
def test_uri_reference_syntax_accepts_urls_and_relative_references(value: str) -> None:
    assert _is_valid_uri_reference(value)


@pytest.mark.parametrize("value", INVALID_URI_REFERENCES)
def test_uri_reference_syntax_rejects_malformed_values(value: str) -> None:
    assert not _is_valid_uri_reference(value)
    assert not _is_valid_absolute_uri(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/ns",
        "http://example.com/schema/sample#",
        "urn:example:ns",
        "mailto:docs@example.com",
    ],
)
def test_absolute_uri_syntax_accepts_values_with_a_scheme(value: str) -> None:
    assert _is_valid_absolute_uri(value)


@pytest.mark.parametrize("value", RELATIVE_URI_REFERENCES)
def test_absolute_uri_syntax_rejects_relative_references(value: str) -> None:
    assert _is_valid_uri_reference(value)
    assert not _is_valid_absolute_uri(value)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("namespace", ["https://example.com/schema", "urn:example:ns", "http://example.com/ns#"])
def test_xml_namespace_that_is_an_absolute_uri_is_accepted(namespace: str, version: str) -> None:
    base = {"type": "string", "minLength": 2, "maxLength": 6}
    plain = format_suite(base, version)
    annotated = format_suite({**base, "xml": {"name": "v", "namespace": namespace}}, version)

    assert len(plain.cases) > 2
    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    "namespace", [*RELATIVE_URI_REFERENCES, "", "not a uri", "http://a b/", "ht!tp://x"]
)
def test_xml_namespace_that_is_not_an_absolute_uri_is_rejected(
    namespace: str, version: str
) -> None:
    diagnostic = only_diagnostic(format_suite({"type": "string", "xml": {"namespace": namespace}}, version))

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/xml/namespace")
    assert "Schema xml.namespace must be an absolute URI." == diagnostic.message


def array_schema(**extra: object) -> dict[str, object]:
    return {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, **extra}


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("wrapped", [True, False])
def test_xml_wrapped_on_an_array_schema_is_accepted_and_changes_nothing(
    wrapped: bool, version: str
) -> None:
    plain = format_suite(array_schema(), version)
    annotated = format_suite(array_schema(xml={"name": "tags", "wrapped": wrapped}), version)

    assert len(plain.cases) > 2
    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


def test_xml_wrapped_is_accepted_beside_an_openapi_31_array_type_list() -> None:
    plain = format_suite(array_schema(type=["array", "null"]), "3.1.1")
    annotated = format_suite(
        array_schema(type=["array", "null"], xml={"wrapped": True}), "3.1.1"
    )

    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("wrapped", [True, False])
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "integer"},
        {"type": "boolean"},
        {"type": "object", "properties": {}},
        {"enum": [1, 2]},
        {},
    ],
)
def test_xml_wrapped_on_a_non_array_schema_is_rejected(
    schema: dict[str, object], wrapped: bool, version: str
) -> None:
    diagnostic = only_diagnostic(format_suite({**schema, "xml": {"wrapped": wrapped}}, version))

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/xml/wrapped")
    assert "Schema xml.wrapped applies only to an array schema." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_xml_wrapped_inside_an_items_schema_is_not_array_wrapper_metadata(version: str) -> None:
    items = {"type": "string", "minLength": 1, "xml": {"name": "tag", "wrapped": True}}

    diagnostic = only_diagnostic(
        format_suite(array_schema(items=items, xml={"wrapped": True}), version)
    )

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/items/xml/wrapped")
    assert "Schema xml.wrapped applies only to an array schema." == diagnostic.message


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_xml_without_wrapped_inside_an_items_schema_is_accepted(version: str) -> None:
    items = {"type": "string", "minLength": 1, "xml": {"name": "tag"}}
    plain = format_suite(array_schema(), version)
    annotated = format_suite(array_schema(items=items, xml={"wrapped": True}), version)

    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


def test_xml_wrapped_on_a_nested_array_schema_is_that_arrays_own_metadata() -> None:
    inner = {"type": "array", "minItems": 1, "items": {"type": "string"}, "xml": {"wrapped": True}}
    outer = {"type": "array", "minItems": 1, "items": inner}
    plain_inner = {key: value for key, value in inner.items() if key != "xml"}

    plain = format_suite({**outer, "items": plain_inner})
    annotated = format_suite(outer)

    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/docs",
        "http://example.com:8080/a/b?c=d#e",
        "docs/page.html",
        "../up/page.html",
        "#section",
        "//example.com/docs",
        "mailto:docs@example.com",
    ],
)
def test_external_docs_url_that_is_a_url_or_relative_reference_is_accepted(
    url: str, version: str
) -> None:
    base = {"type": "string", "minLength": 2, "maxLength": 6}
    plain = format_suite(base, version)
    annotated = format_suite({**base, "externalDocs": {"url": url}}, version)

    assert len(plain.cases) > 2
    assert_unsupported_codes_absent(annotated)
    assert dump_suite(annotated) == dump_suite(plain)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("url", INVALID_URI_REFERENCES)
def test_external_docs_url_that_is_malformed_is_rejected(url: str, version: str) -> None:
    diagnostic = only_diagnostic(
        format_suite({"type": "string", "externalDocs": {"url": url}}, version)
    )

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/externalDocs/url")
    assert "Schema externalDocs.url must be a URL or a relative URL reference." == diagnostic.message


def annotation_document(version: str, schema: dict[str, object]) -> dict[str, object]:
    return {
        "openapi": version,
        "info": {"title": "Annotated", "version": "1.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["v"],
                                    "properties": {"v": schema},
                                }
                            }
                        },
                    },
                    "responses": {"400": {"description": "Invalid"}},
                }
            }
        },
    }


def require_real_validator() -> None:
    """Fail the test when openapi-spec-validator is missing or a stub that accepts anything."""
    from app.services.openapi.exceptions import OpenAPIValidationError
    from app.services.openapi.validator import validate_openapi_document

    try:
        validate_openapi_document({"openapi": "3.0.3", "info": {"title": "x"}, "paths": {}})
    except OpenAPIValidationError:
        return
    pytest.fail("openapi-spec-validator accepted an invalid document: it is a stub, not the real one")


def uri_format_checkers_installed() -> bool:
    import importlib.util

    return any(importlib.util.find_spec(name) for name in ("rfc3987", "rfc3986_validator"))


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "minLength": 2, "xml": {"name": "v", "namespace": "https://example.com/ns", "prefix": "ex", "attribute": False}},
        {"type": "array", "minItems": 1, "items": {"type": "string", "xml": {"name": "tag"}}, "xml": {"name": "tags", "wrapped": True}},
        {"type": "string", "minLength": 2, "externalDocs": {"url": "https://example.com/docs", "description": "Docs"}},
        {"type": "string", "minLength": 2, "externalDocs": {"url": "docs/page.html"}},
    ],
)
def test_real_validator_and_generator_both_accept_valid_xml_and_external_docs(
    version: str, schema: dict[str, object]
) -> None:
    from app.services.openapi.validator import validate_openapi_document

    require_real_validator()
    plain = {key: value for key, value in schema.items() if key not in {"xml", "externalDocs"}}
    if "items" in plain:
        plain["items"] = {"type": "string"}

    suites = [
        generate_test_suite(
            normalize_openapi_document(validate_openapi_document(annotation_document(version, item)))
        )
        for item in (plain, schema)
    ]

    assert len(suites[0].cases) > 2
    assert_unsupported_codes_absent(suites[1])
    assert dump_suite(suites[1]) == dump_suite(suites[0])


# What the real validator lets through (see tests/test_openapi_parser.py) and the
# generator then rejects. `uri` marks cases that depend on URI format checking.
GENERATOR_ONLY_ANNOTATION_CHECKS: list[tuple[str, dict[str, object], str, str, bool]] = [
    ("relative namespace", {"type": "string", "xml": {"namespace": "ns/x"}}, "/xml/namespace", "must be an absolute URI", True),
    ("text namespace", {"type": "string", "xml": {"namespace": "not a uri"}}, "/xml/namespace", "must be an absolute URI", True),
    ("empty namespace", {"type": "string", "xml": {"namespace": ""}}, "/xml/namespace", "must be an absolute URI", True),
    ("text url", {"type": "string", "externalDocs": {"url": "not a url"}}, "/externalDocs/url", "must be a URL or a relative URL reference", True),
    ("bad host url", {"type": "string", "externalDocs": {"url": "http://[bad"}}, "/externalDocs/url", "must be a URL or a relative URL reference", True),
    ("empty url", {"type": "string", "externalDocs": {"url": ""}}, "/externalDocs/url", "must be a URL or a relative URL reference", True),
    ("wrapped on string", {"type": "string", "xml": {"wrapped": True}}, "/xml/wrapped", "applies only to an array schema", False),
    ("wrapped on integer", {"type": "integer", "xml": {"wrapped": True}}, "/xml/wrapped", "applies only to an array schema", False),
    (
        "wrapped in items",
        {"type": "array", "minItems": 1, "items": {"type": "string", "xml": {"wrapped": True}}},
        "/items/xml/wrapped", "applies only to an array schema", False,
    ),
]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
@pytest.mark.parametrize(
    ("label", "schema", "suffix", "message", "uri"),
    GENERATOR_ONLY_ANNOTATION_CHECKS,
    ids=[item[0] for item in GENERATOR_ONLY_ANNOTATION_CHECKS],
)
def test_generator_rejects_what_the_real_validator_accepts(
    label: str, schema: dict[str, object], suffix: str, message: str, uri: bool, version: str
) -> None:
    from app.services.openapi.validator import validate_openapi_document

    require_real_validator()
    if uri and uri_format_checkers_installed():
        pytest.skip("URI format checkers are installed, so the validator may reject this itself")

    document = validate_openapi_document(annotation_document(version, schema))
    suite = generate_test_suite(normalize_openapi_document(document))

    diagnostic = only_diagnostic(suite)
    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v" + suffix)
    assert message in diagnostic.message


@pytest.mark.parametrize(
    ("schema", "suffix"),
    [
        ({"type": "string", "xml": {"foo": 1}}, "/xml/foo"),
        ({"type": "string", "xml": {"attribute": "true"}}, "/xml/attribute"),
        ({"type": "string", "xml": {"namespace": 5}}, "/xml/namespace"),
        ({"type": "string", "externalDocs": {"description": "d"}}, "/externalDocs"),
        ({"type": "string", "externalDocs": {"url": 5}}, "/externalDocs/url"),
        ({"type": "string", "externalDocs": {"url": "https://e.test", "foo": 1}}, "/externalDocs/foo"),
    ],
)
def test_openapi_31_documents_with_malformed_annotation_structure_reach_the_generator(
    schema: dict[str, object], suffix: str
) -> None:
    from app.services.openapi.validator import validate_openapi_document

    require_real_validator()

    # The real validator checks these objects for OpenAPI 3.0 only (see test_openapi_parser.py).
    document = validate_openapi_document(annotation_document("3.1.1", schema))
    diagnostic = only_diagnostic(generate_test_suite(normalize_openapi_document(document)))

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v" + suffix)


# --- Square brackets and ports in URI references (Step 8.43.2 URI correction) ---------

BRACKET_VALID = [
    "http://[2001:db8::1]/docs",
    "http://[2001:db8::1]:8080/docs",
    "http://[::1]",
    "http://[::ffff:192.0.2.1]/x",
    "http://user:pw@[2001:db8::1]:80/x?q=1#f",
    "//[2001:db8::1]/docs",
    "http://[v1.fe80::a]/x",
    "https://example.com/%5Bok%5D",
    "https://example.com/a?x%5B%5D=1",
    "https://example.com/a#%5Bfrag%5D",
]
BRACKET_INVALID = [
    "https://example.com/[bad]",
    "https://example.com/a[0]",
    "https://example.com/a?x[]=1",
    "https://example.com/a?[x]",
    "https://example.com/a#[frag]",
    "https://example.com/a?q=1#a[0]",
    "docs/[bad].html",
    "[bad]",
    "#[bad]",
    "?[bad]",
    "http://exa[mple.com/",
    "http://[2001:db8::1",
    "http://2001:db8::1]/",
    "http://[2001:db8::1]x/",
    "http://[2001:db8::1]:80x/",
    "http://[not-ip]/",
    "http://[12345::1]/",
    "http://[1:2:3]/",
    "http://[fe80::1%25eth0]/",
    "http://[]/",
    "http://[v1.]/",
    "http://user[x]@host/",
    "http://[::1][::1]/",
    "http://a@b@host/",
]


@pytest.mark.parametrize("value", BRACKET_VALID)
def test_uri_reference_accepts_brackets_only_as_ip_literals_or_percent_encoded(value: str) -> None:
    assert _is_valid_uri_reference(value)


@pytest.mark.parametrize("value", BRACKET_INVALID)
def test_uri_reference_rejects_misplaced_brackets_and_bad_ip_literals(value: str) -> None:
    assert not _is_valid_uri_reference(value)
    assert not _is_valid_absolute_uri(value)


def test_ipv6_authority_is_still_accepted_as_a_namespace_and_a_url() -> None:
    for schema in (
        {"type": "string", "xml": {"namespace": "http://[2001:db8::1]/ns"}},
        {"type": "string", "externalDocs": {"url": "http://[2001:db8::1]/docs"}},
    ):
        assert_unsupported_codes_absent(format_suite(schema))


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(
    ("schema", "suffix", "message"),
    [
        ({"type": "string", "externalDocs": {"url": "https://example.com/[bad]"}}, "/externalDocs/url", "Schema externalDocs.url must be a URL or a relative URL reference."),
        ({"type": "string", "externalDocs": {"url": "https://example.com/a?x[]=1"}}, "/externalDocs/url", "Schema externalDocs.url must be a URL or a relative URL reference."),
        ({"type": "string", "externalDocs": {"url": "https://example.com/a#[f]"}}, "/externalDocs/url", "Schema externalDocs.url must be a URL or a relative URL reference."),
        ({"type": "string", "xml": {"namespace": "https://example.com/[ns]"}}, "/xml/namespace", "Schema xml.namespace must be an absolute URI."),
    ],
)
def test_misplaced_brackets_keep_the_existing_diagnostic_code_and_pointer(
    schema: dict[str, object], suffix: str, message: str, version: str
) -> None:
    diagnostic = only_diagnostic(format_suite(schema, version))

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v" + suffix)
    assert diagnostic.message == message


# Port decision: RFC 3986 syntax is `port = *DIGIT` with no maximum. The helper keeps
# rejecting ports above 65535 on purpose (a port is a 16-bit number), as it did before
# the bracket correction. These tests pin that decision so a change is never silent.
@pytest.mark.parametrize(
    "value",
    [
        "http://host:80/",
        "http://host:/",
        "http://host:0/",
        "http://host:0080/",
        "http://host:65535/",
        "http://[::1]:65535/",
        "http://host:65535",
    ],
)
def test_ports_from_zero_to_65535_and_an_empty_port_are_accepted(value: str) -> None:
    assert _is_valid_uri_reference(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://host:65536/",
        "http://host:99999/",
        "http://[::1]:65536/",
        "http://host:99999999999999999999/",
        "http://host:abc/",
        "http://host:-1/",
        "http://host:8 0/",
        "http://host:80:80/",
    ],
)
def test_ports_above_65535_and_non_digit_ports_are_rejected(value: str) -> None:
    assert not _is_valid_uri_reference(value)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_port_above_65535_is_a_form_diagnostic_for_a_url(version: str) -> None:
    diagnostic = only_diagnostic(
        format_suite({"type": "string", "externalDocs": {"url": "http://host:65536/"}}, version)
    )

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/externalDocs/url")


# --- Port range on very long digit strings (Step 8.43.2 final URI correction) ---------

LONG = 5000


@pytest.mark.parametrize(
    ("port", "accepted"),
    [
        ("", True),
        ("0", True),
        ("80", True),
        ("65535", True),
        ("065535", True),
        ("65536", False),
        ("99999", False),
        ("0" * LONG, True),
        ("0" * LONG + "80", True),
        ("0" * LONG + "65535", True),
        ("0" * LONG + "65536", False),
        ("1" * LONG, False),
        ("9" * LONG, False),
        ("6" + "0" * LONG, False),
    ],
    ids=lambda value: value if isinstance(value, bool) or len(value) < 12 else f"len{len(value)}",
)
def test_port_range_is_checked_on_digits_without_int_conversion(port: str, accepted: bool) -> None:
    assert _is_valid_uri_reference(f"http://host:{port}/x") is accepted
    assert _is_valid_uri_reference(f"http://[::1]:{port}") is accepted


def test_port_check_does_not_depend_on_the_integer_string_limit() -> None:
    import sys

    previous = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    if previous is None:
        pytest.skip("this Python has no integer string conversion limit")
    sys.set_int_max_str_digits(640)
    try:
        assert not _is_valid_uri_reference("http://host:" + "9" * 5000 + "/")
        assert _is_valid_uri_reference("http://host:" + "0" * 5000 + "80/")
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_oversized_port_in_external_docs_url_is_a_form_diagnostic(version: str) -> None:
    diagnostic = only_diagnostic(
        format_suite(
            {"type": "string", "externalDocs": {"url": "http://host:" + "9" * LONG + "/"}}, version
        )
    )

    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/v/externalDocs/url")
    assert diagnostic.message == "Schema externalDocs.url must be a URL or a relative URL reference."


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
def test_long_zero_padded_port_in_external_docs_url_is_accepted(version: str) -> None:
    schema = {"type": "string", "externalDocs": {"url": "http://host:" + "0" * LONG + "80/"}}

    assert_unsupported_codes_absent(format_suite(schema, version))


# --- Per-run memo of URI checks (Step 8.43.4) --------------------------------------------

URI_MEMO_CASES = [
    # keyword, field, the check it uses, a value that passes, a value that fails
    ("xml", "namespace", "absolute", "https://example.com/ns", "relative/ns"),
    ("externalDocs", "url", "reference", "https://example.com/docs", "not a url"),
]


def count_uri_checks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Wrap both URI checks of the Generator and record every string they scan."""
    calls: dict[str, list[str]] = {"absolute": [], "reference": []}

    def wrap(kind: str, check: object, key: str, table: dict) -> None:
        def counted(value: str) -> bool:
            calls[kind].append(value)
            return check(value)  # type: ignore[operator]

        monkeypatch.setitem(table, key, (counted, table[key][1]))

    wrap("absolute", generator_module._is_valid_absolute_uri, "namespace", generator_module._XML_URI_FIELDS)
    wrap("reference", generator_module._is_valid_uri_reference, "url", generator_module._EXTERNAL_DOCS_URI_FIELDS)
    return calls


def count_annotated_schema_visits(monkeypatch: pytest.MonkeyPatch, keyword: str) -> list[int]:
    visits = [0]
    original = generator_module._check_schema

    def counting_check(*args: object) -> object:
        if isinstance(args[0], dict) and keyword in args[0]:
            visits[0] += 1
        return original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_check_schema", counting_check)
    return visits


def annotated_grid(keyword: str, field: str, value: str, size: int = 12) -> dict[str, object]:
    leaf = {"type": "integer", "minimum": 0, "maximum": 10, keyword: {field: value}}
    item = {"type": "object", "required": ["v"], "properties": {"v": leaf}}
    return {"type": "array", "minItems": size, "items": {"type": "array", "minItems": size, "items": item}}


@pytest.mark.parametrize(("keyword", "field", "kind", "good", "bad"), URI_MEMO_CASES)
def test_a_repeated_uri_value_is_scanned_once_per_generation_run(
    monkeypatch: pytest.MonkeyPatch, keyword: str, field: str, kind: str, good: str, bad: str
) -> None:
    calls = count_uri_checks(monkeypatch)
    visits = count_annotated_schema_visits(monkeypatch, keyword)

    suite, budget = validation_run(monkeypatch, annotated_grid(keyword, field, good))

    assert valid_edge_cases(suite)
    assert_unsupported_codes_absent(suite)
    # The annotated schema is checked on every visit of the valid-edge validation ...
    assert visits[0] > 100
    # ... but its string is scanned once, by the one check of its kind.
    assert calls[kind] == [good]
    assert calls["absolute" if kind == "reference" else "reference"] == []
    assert list(budget.uri_memo.values()) == [True]


@pytest.mark.parametrize(("keyword", "field", "kind", "good", "bad"), URI_MEMO_CASES)
def test_a_repeated_invalid_uri_value_is_scanned_once_and_still_rejected(
    monkeypatch: pytest.MonkeyPatch, keyword: str, field: str, kind: str, good: str, bad: str
) -> None:
    calls = count_uri_checks(monkeypatch)
    schema = {
        "type": "object",
        "required": ["a", "b", "c"],
        "properties": {name: {"type": "string", keyword: {field: bad}} for name in "abc"},
    }

    suite, budget = validation_run(monkeypatch, schema)

    assert suite.cases == ()
    [diagnostic] = suite.diagnostics
    assert diagnostic.code == "unsupported_schema_form"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith(f"/properties/a/{keyword}/{field}")
    assert calls[kind] == [bad]
    assert list(budget.uri_memo.values()) == [False]


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize(("keyword", "field", "kind", "good", "bad"), URI_MEMO_CASES)
def test_a_cached_invalid_result_reports_the_pointer_of_the_current_occurrence(
    monkeypatch: pytest.MonkeyPatch, keyword: str, field: str, kind: str, good: str, bad: str,
    version: str,
) -> None:
    calls = count_uri_checks(monkeypatch)
    endpoints = [
        post_endpoint(
            {"type": "object", "required": [name], "properties": {name: {"type": "string", keyword: {field: bad}}}},
            path,
        )
        for name, path in (("first", "/one"), ("second", "/two"), ("third", "/three"))
    ]

    suite = generate_test_suite(make_result(*endpoints, version=version))

    assert suite.cases == ()
    assert calls[kind] == [bad]  # scanned for the first endpoint only
    seen = [(d.endpoint_path, d.code, d.source_pointer) for d in suite.diagnostics]
    assert [(path, code) for path, code, _ in seen] == [
        ("/one", "unsupported_schema_form"),
        ("/two", "unsupported_schema_form"),
        ("/three", "unsupported_schema_form"),
    ]
    for (_, _, pointer), name in zip(seen, ("first", "second", "third")):
        assert pointer is not None
        assert pointer.endswith(f"/properties/{name}/{keyword}/{field}")
    assert len({d.message for d in suite.diagnostics}) == 1


@pytest.mark.parametrize("namespace_first", [True, False])
def test_absolute_uri_and_uri_reference_results_are_not_mixed_up(
    monkeypatch: pytest.MonkeyPatch, namespace_first: bool
) -> None:
    relative = "docs/page.html"  # a valid reference, but not an absolute URI
    calls = count_uri_checks(monkeypatch)
    namespace = {"type": "string", "xml": {"namespace": relative}}
    url = {"type": "string", "externalDocs": {"url": relative}}
    # Required properties are checked in sorted name order, so the names set the order.
    properties = {"a": namespace, "b": url} if namespace_first else {"a": url, "b": namespace}
    namespace_name = "a" if namespace_first else "b"

    suite, budget = validation_run(
        monkeypatch, {"type": "object", "required": list(properties), "properties": properties}
    )

    [diagnostic] = suite.diagnostics
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith(f"/properties/{namespace_name}/xml/namespace")
    assert diagnostic.message == "Schema xml.namespace must be an absolute URI."
    assert calls["absolute"] == [relative]
    if namespace_first:
        # The endpoint is dropped at the namespace, so the URL is never reached.
        assert calls["reference"] == []
        assert list(budget.uri_memo.values()) == [False]
    else:
        # The URL was accepted first; the same string is then rejected as a namespace.
        assert calls["reference"] == [relative]
        assert sorted(budget.uri_memo.values()) == [False, True]


def test_a_reference_accepted_as_a_url_is_still_checked_as_an_absolute_uri_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "docs/page.html"
    url_ok = {"type": "string", "externalDocs": {"url": relative}}
    ns_bad = {"type": "string", "xml": {"namespace": relative}}

    suite, budget = validation_run(
        monkeypatch,
        {"type": "object", "required": ["u"], "properties": {"u": url_ok}},
        {"type": "object", "required": ["n"], "properties": {"n": ns_bad}},
    )

    assert {case.path for case in suite.cases} == {"/items0"}
    [diagnostic] = suite.diagnostics
    assert diagnostic.endpoint_path == "/items1"
    assert diagnostic.source_pointer is not None
    assert diagnostic.source_pointer.endswith("/properties/n/xml/namespace")
    assert sorted(budget.uri_memo.values()) == [False, True]


def test_the_uri_memo_is_not_shared_between_generation_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"accept": True, "calls": 0}

    def controlled(value: str) -> bool:
        state["calls"] += 1
        return bool(state["accept"])

    # The same function object is used by all three runs, so a result kept from an
    # earlier run would be reused if anything outlived it.
    monkeypatch.setitem(
        generator_module._EXTERNAL_DOCS_URI_FIELDS, "url", (controlled, "a URL or a relative URL reference")
    )
    endpoint = format_endpoint({"type": "string", "externalDocs": {"url": "https://example.com/docs"}})
    result = make_result(endpoint)

    first = generate_test_suite(result)
    state["accept"] = False
    second = generate_test_suite(result)
    state["accept"] = True
    third = generate_test_suite(result)

    assert_unsupported_codes_absent(first)
    [diagnostic] = second.diagnostics
    assert diagnostic.code == "unsupported_schema_form" and second.cases == ()
    assert dump_suite(third) == dump_suite(first)
    assert state["calls"] == 3  # once per run
    assert generator_module._BUDGET.get() is None


def test_the_uri_memo_lives_on_the_run_budget_and_holds_only_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = {"type": "string", "xml": {"namespace": "https://example.com/ns"}}
    other = {"type": "string", "externalDocs": {"url": "docs/page.html"}}
    shape = {"type": "object", "required": ["a", "b"], "properties": {"a": good, "b": other}}

    _, budget = validation_run(monkeypatch, shape, shape)
    _, again = validation_run(monkeypatch, shape)

    assert not hasattr(generator_module, "_URI_MEMO")
    assert sorted(
        (check.__name__, text) for check, text in budget.uri_memo
    ) == [
        ("_is_valid_absolute_uri", "https://example.com/ns"),
        ("_is_valid_uri_reference", "docs/page.html"),
    ]
    assert all(isinstance(result, bool) for result in budget.uri_memo.values())
    assert again.uri_memo.keys() == budget.uri_memo.keys() and again is not budget


@pytest.mark.parametrize("version", ANNOTATION_VERSIONS)
@pytest.mark.parametrize("keyword", ["xml", "externalDocs"])
@pytest.mark.parametrize(
    "value", [*VALID_URI_REFERENCES, *INVALID_URI_REFERENCES, *BRACKET_VALID, *BRACKET_INVALID]
)
def test_memoized_checks_give_the_same_result_as_the_uri_helpers(
    value: str, keyword: str, version: str
) -> None:
    field, check = (
        ("namespace", _is_valid_absolute_uri) if keyword == "xml" else ("url", _is_valid_uri_reference)
    )
    expected = check(value)
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {name: {"type": "string", keyword: {field: value}} for name in "ab"},
    }

    suite = generate_test_suite(make_result(post_endpoint(schema), version=version))

    if expected:
        assert_unsupported_codes_absent(suite)
        assert suite.cases
    else:
        assert suite.cases == ()
        [diagnostic] = suite.diagnostics
        assert diagnostic.code == "unsupported_schema_form"
        assert diagnostic.source_pointer is not None
        assert diagnostic.source_pointer.endswith(f"/properties/a/{keyword}/{field}")


# --- Meter wiring: $ref and additionalProperties (Step 8.43.5) ---------------------------
# The valid-edge validation passes one meter down every recursive call. These tests pin
# three places where the meter is handed on, so that dropping it there is noticed:
#   1. the $ref branch of _check_schema,
#   2. the _check_schema call for an additionalProperties schema (in _matches),
#   3. the _matches call for an additionalProperties value.
# _check_schema has no additionalProperties recursion of its own; points 2 and 3 are the
# two calls inside the additionalProperties branch of _matches.

REF_BODY = {
    "type": "object",
    "required": ["v"],
    "properties": {"v": {"$ref": "#/components/schemas/Num"}},
}
REF_SCHEMAS = {"Num": {"type": "integer", "minimum": 1, "maximum": 5}}
# `v` has an example with an undeclared property `x`, so the baseline body, and with it
# every valid-edge candidate, reaches the additionalProperties branch of _matches.
ADDITIONAL_BODY = {
    "type": "object",
    "required": ["v"],
    "properties": {
        "v": {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 5}},
            "additionalProperties": {"type": "integer"},
            "example": {"a": 2, "x": 3},
        }
    },
}
# Root check: the object (1), v (1), and v's required property a (1). Each candidate
# body: the object (1), v's schema check (1) with its required property a (1), v's
# value (1), then a's schema check (1) and value (1), then the undeclared x: the
# additionalProperties schema check (1) and its value (1). There are two candidates.
ADDITIONAL_UNITS = 3 + 2 * 8
# For REF_BODY: root check object (1), v (1), the referenced Num (1); one candidate:
# the object (1), v and its reference target (2), v's value (1).
REF_UNITS = 3 + 4


def wiring_run(
    monkeypatch: pytest.MonkeyPatch,
    body: object,
    schemas: dict[str, object] | None = None,
    *,
    endpoint_limit: int | None = None,
) -> tuple[GeneratedTestSuite, "generator_module._GenerationBudget", list[object], list[tuple[str, str, object]]]:
    """Generate one endpoint; return the suite, its budget, the meters and every call.

    Each call is (function, pointer, meter) for _check_schema and _matches, where the
    meter is None when the call was made without one.
    """
    created: list[generator_module._GenerationBudget] = []
    meters: list[object] = []
    calls: list[tuple[str, str, object]] = []

    class RecordingBudget(generator_module._GenerationBudget):
        def __init__(self) -> None:
            super().__init__()
            created.append(self)

    original_meter = generator_module._validation_meter
    original_check = generator_module._check_schema
    original_matches = generator_module._matches

    def recording_meter(pointer: str) -> object:
        meter = original_meter(pointer)
        meters.append(meter)
        return meter

    def recording_check(*args: object) -> object:
        calls.append(("check", str(args[2]), args[5] if len(args) > 5 else None))
        return original_check(*args)  # type: ignore[arg-type]

    def recording_matches(*args: object) -> bool:
        calls.append(("matches", str(args[5]) if len(args) > 5 else "", args[6] if len(args) > 6 else None))
        return original_matches(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(generator_module, "_GenerationBudget", RecordingBudget)
    monkeypatch.setattr(generator_module, "_validation_meter", recording_meter)
    monkeypatch.setattr(generator_module, "_check_schema", recording_check)
    monkeypatch.setattr(generator_module, "_matches", recording_matches)
    if endpoint_limit is not None:
        monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_VALIDATION_UNITS", endpoint_limit)
    components = (
        Components(schemas={name: Schema(raw=raw) for name, raw in schemas.items()})
        if schemas
        else None
    )
    suite = generate_test_suite(make_result(post_endpoint(body), components=components))
    [budget] = created
    return suite, budget, meters, calls


def metered_calls_at(
    calls: list[tuple[str, str, object]], function: str, pointer_suffix: str
) -> list[object]:
    """The meters of the calls of one function, at a pointer, that carried a meter.

    The other generation steps call the same functions without a meter, so a call
    that lost its meter shows up as a missing entry here.
    """
    return [
        meter
        for name, pointer, meter in calls
        if name == function and pointer.endswith(pointer_suffix) and meter is not None
    ]


def test_the_ref_branch_of_check_schema_hands_the_step_meter_to_the_target_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, budget, meters, calls = wiring_run(monkeypatch, REF_BODY, REF_SCHEMAS)

    assert len(valid_edge_cases(suite)) == 1
    [meter] = meters
    # The referenced schema is checked twice: when the root is checked and when the
    # candidate body is validated. Both checks must carry the step's own meter.
    target_meters = metered_calls_at(calls, "check", "/components/schemas/Num")
    assert len(target_meters) == 2
    assert all(item is meter for item in target_meters)
    # Following the reference is one unit per validation, on top of the 5 units of the
    # same body without a reference (NUMERIC_BODY).
    assert budget.suite_validation_units == REF_UNITS == 5 + 2


def test_a_followed_reference_counts_toward_the_validation_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        enough, _, _, _ = wiring_run(patch, REF_BODY, REF_SCHEMAS, endpoint_limit=REF_UNITS)
    with monkeypatch.context() as patch:
        short, budget, _, _ = wiring_run(patch, REF_BODY, REF_SCHEMAS, endpoint_limit=REF_UNITS - 1)

    assert len(valid_edge_cases(enough)) == 1 and validation_diagnostics(enough) == []
    # One unit less and the step is discarded as a whole; nothing else changes.
    assert valid_edge_cases(short) == []
    [diagnostic] = validation_diagnostics(short)
    assert f"The per-endpoint limit of {REF_UNITS - 1} validation work units" in diagnostic.message
    assert case_keys(short.cases) == case_keys(without_valid_edge_cases(enough))
    assert budget.suite_validation_units == REF_UNITS


def test_the_additional_properties_schema_check_uses_the_step_meter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, _, meters, calls = wiring_run(monkeypatch, ADDITIONAL_BODY)

    assert len(valid_edge_cases(suite)) == 2
    [meter] = meters
    checks = metered_calls_at(calls, "check", "/properties/v/additionalProperties")
    # One check of the additionalProperties schema per candidate body.
    assert len(checks) == 2
    assert all(item is meter for item in checks)


def test_the_additional_properties_value_match_uses_the_step_meter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, _, meters, calls = wiring_run(monkeypatch, ADDITIONAL_BODY)

    assert len(valid_edge_cases(suite)) == 2
    [meter] = meters
    matches = metered_calls_at(calls, "matches", "/properties/v/additionalProperties")
    # One match of the undeclared value per candidate body.
    assert len(matches) == 2
    assert all(item is meter for item in matches)


def test_additional_properties_work_is_charged_and_limits_the_step_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        enough, budget, _, _ = wiring_run(patch, ADDITIONAL_BODY, endpoint_limit=ADDITIONAL_UNITS)
    with monkeypatch.context() as patch:
        short, _, _, _ = wiring_run(patch, ADDITIONAL_BODY, endpoint_limit=ADDITIONAL_UNITS - 1)

    assert len(valid_edge_cases(enough)) == 2 and validation_diagnostics(enough) == []
    assert budget.suite_validation_units == ADDITIONAL_UNITS
    # The last unit belongs to the second candidate's additionalProperties value, so
    # running out there discards the first candidate as well.
    assert valid_edge_cases(short) == []
    [diagnostic] = validation_diagnostics(short)
    assert f"The per-endpoint limit of {ADDITIONAL_UNITS - 1} validation work units" in diagnostic.message
    assert case_keys(short.cases) == case_keys(without_valid_edge_cases(enough))


ADDITIONAL_SCHEMA = {"type": "object", "additionalProperties": {"type": "integer"}}


def test_matches_charges_the_schema_check_and_the_value_of_an_additional_property() -> None:
    token, budget = with_budget()
    try:
        meter = generator_module._validation_meter("/p")

        assert generator_module._matches(ADDITIONAL_SCHEMA, {"x": 1}, "3.1.1", "request", 0, "/p", meter)

        # The object (1), then the additionalProperties schema check (1) and value (1).
        assert budget.endpoint_validation_units == 3
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [1, 2])
def test_running_out_of_work_inside_the_additional_properties_branch_is_not_a_mismatch(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    # An unusable additionalProperties schema only rejects a candidate, but budget
    # exhaustion must still propagate out of the branch. Three units are needed.
    monkeypatch.setattr(generator_module, "_MAX_ENDPOINT_VALIDATION_UNITS", limit)
    token, _ = with_budget()
    try:
        meter = generator_module._validation_meter("/p")

        with pytest.raises(generator_module._BudgetExhausted):
            generator_module._matches(ADDITIONAL_SCHEMA, {"x": 1}, "3.1.1", "request", 0, "/p", meter)
    finally:
        generator_module._BUDGET.reset(token)  # type: ignore[arg-type]


# --- Step 8.44.4: golden regression for JSON-only request bodies -------------------
# The expected values below are fixed data captured from the implementation before
# request media type selection was changed. They must never be regenerated from the
# code under test.

GOLDEN_JSON_BODY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["name", "age"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 5},
        "age": {"type": "integer", "minimum": 1, "maximum": 9},
    },
}

GOLDEN_REQUIRED_JSON_CASES: tuple[tuple[str, str, str | None, object], ...] = (
    ("case-e990e04552667f473990e99c778bdf6bd792335a52617924061620ed85b284eb", "positive", None, {'age': 1, 'name': 'senti'}),
    ("case-1047814a1a62fc52e57b9718bc10069817aaa1e1fd4296d8c6497eb75b1a2b63", "negative", 'required_property', {'name': 'senti'}),
    ("case-93692ce01e76b14e7386644034d86eb60d745083b165a0c9be027cf953e20499", "negative", 'required_property', {'age': 1}),
    ("case-cf25f0252256856c0f1db798a461c9dfd458a863e4f8c2365e62bd8f34a12b4b", "negative", 'minimum', {'age': 0, 'name': 'senti'}),
    ("case-1c976651cf456aadf677cadb2f1f0b72b7f45a6add35d71f4a766b927ac21245", "negative", 'maximum', {'age': 10, 'name': 'senti'}),
    ("case-dbdd46b05c9956f9dfe1cb4fe6e491749bc662e9de3a7934513acde297bbcca0", "negative", 'type', {'age': 'wrong-type', 'name': 'senti'}),
    ("case-d03cd488d51ff6211bbbf6c1129c8ee42dd451946d68235ca82f7c354867b687", "negative", 'type', {'age': 1, 'name': 0}),
    ("case-c499ccf4d73aaa2033ac21ff2af3df18bcfac627e913181b896a8600da365509", "negative", 'minLength', {'age': 1, 'name': ''}),
    ("case-88e510de01f5734db92a4b051aac415f6da12d98201e2c2faf05b9ae9fee61ba", "negative", 'maxLength', {'age': 1, 'name': 'xxxxxx'}),
    ("case-8ea15ceb12173df5d6d74b996c6841bc2c982d4765bd3467239dc8c3837a6083", "negative", 'required_parameter', {'age': 1, 'name': 'senti'}),
    ("case-805f56ea6e2d9c309edc8b0a3a8e103c55df80229c6163535c426added7f8722", "negative", 'required_request_body', None),
    ("case-d9001a5108d4828bebacdce67798436b547a3ad88bc076bece8a9ba945d49e09", "positive", None, {'age': 9, 'name': 'senti'}),
    ("case-8084a18ff2c38470c0208719b29ad94ee65788e5e4a0bcc77cdd2feff4aafce7", "positive", None, {'age': 1, 'name': 'x'}),
)

GOLDEN_OPTIONAL_JSON_CASES: tuple[tuple[str, str, str | None, object], ...] = (
    ("case-e990e04552667f473990e99c778bdf6bd792335a52617924061620ed85b284eb", "positive", None, {'age': 1, 'name': 'senti'}),
    ("case-00b1f0298733ddbe370e34c223e5b851cdc171f2063c6631269de2ab920c2372", "positive", None, None),
    ("case-1047814a1a62fc52e57b9718bc10069817aaa1e1fd4296d8c6497eb75b1a2b63", "negative", 'required_property', {'name': 'senti'}),
    ("case-93692ce01e76b14e7386644034d86eb60d745083b165a0c9be027cf953e20499", "negative", 'required_property', {'age': 1}),
    ("case-cf25f0252256856c0f1db798a461c9dfd458a863e4f8c2365e62bd8f34a12b4b", "negative", 'minimum', {'age': 0, 'name': 'senti'}),
    ("case-1c976651cf456aadf677cadb2f1f0b72b7f45a6add35d71f4a766b927ac21245", "negative", 'maximum', {'age': 10, 'name': 'senti'}),
    ("case-dbdd46b05c9956f9dfe1cb4fe6e491749bc662e9de3a7934513acde297bbcca0", "negative", 'type', {'age': 'wrong-type', 'name': 'senti'}),
    ("case-d03cd488d51ff6211bbbf6c1129c8ee42dd451946d68235ca82f7c354867b687", "negative", 'type', {'age': 1, 'name': 0}),
    ("case-c499ccf4d73aaa2033ac21ff2af3df18bcfac627e913181b896a8600da365509", "negative", 'minLength', {'age': 1, 'name': ''}),
    ("case-88e510de01f5734db92a4b051aac415f6da12d98201e2c2faf05b9ae9fee61ba", "negative", 'maxLength', {'age': 1, 'name': 'xxxxxx'}),
    ("case-8ea15ceb12173df5d6d74b996c6841bc2c982d4765bd3467239dc8c3837a6083", "negative", 'required_parameter', {'age': 1, 'name': 'senti'}),
    ("case-d9001a5108d4828bebacdce67798436b547a3ad88bc076bece8a9ba945d49e09", "positive", None, {'age': 9, 'name': 'senti'}),
    ("case-8084a18ff2c38470c0208719b29ad94ee65788e5e4a0bcc77cdd2feff4aafce7", "positive", None, {'age': 1, 'name': 'x'}),
)


def golden_summary(suite: GeneratedTestSuite) -> tuple[tuple[str, str, str | None, object], ...]:
    return tuple(
        (
            case.case_id,
            case.category,
            case.violated_constraint,
            None
            if case.request_body is None
            else {"media_type": case.request_body.media_type, "value": case.request_body.value},
        )
        for case in suite.cases
    )


def with_json_media(rows: tuple[tuple[str, str, str | None, object], ...]):
    return tuple(
        (
            case_id,
            category,
            constraint,
            None if value is None else {"media_type": "application/json", "value": value},
        )
        for case_id, category, constraint, value in rows
    )


def test_golden_json_only_required_body_cases_ids_order_and_values() -> None:
    suite = body_suite(body_of(GOLDEN_JSON_BODY_SCHEMA, required=True))

    assert golden_summary(suite) == with_json_media(GOLDEN_REQUIRED_JSON_CASES)
    assert suite.diagnostics == ()


def test_golden_json_only_optional_body_cases_ids_order_and_values() -> None:
    suite = body_suite(body_of(GOLDEN_JSON_BODY_SCHEMA, required=False))

    assert golden_summary(suite) == with_json_media(GOLDEN_OPTIONAL_JSON_CASES)
    assert suite.diagnostics == ()


def test_golden_json_only_provenance_pointers() -> None:
    suite = body_suite(body_of(GOLDEN_JSON_BODY_SCHEMA, required=True))
    schema = "/paths/~1x/post/requestBody/content/application~1json/schema"

    assert suite.cases[0].provenance == (
        "/paths/~1x/post",
        "/paths/~1x/post/requestBody",
        schema,
    )
    assert suite.cases[3].provenance == (
        "/paths/~1x/post",
        "/paths/~1x/post/requestBody",
        schema,
        f"{schema}/properties/age",
        f"{schema}/properties/age/minimum",
        "violated_constraint:minimum:/age",
    )


def test_golden_json_only_unsupported_schema_diagnostic_is_unchanged() -> None:
    suite = body_suite(body_of(UNSUPPORTED_BODY_SCHEMA, required=False))

    assert [(case.case_id, case.category, case.request_body) for case in suite.cases] == [
        (
            "case-e990e04552667f473990e99c778bdf6bd792335a52617924061620ed85b284eb",
            "positive",
            None,
        ),
        (
            "case-8ea15ceb12173df5d6d74b996c6841bc2c982d4765bd3467239dc8c3837a6083",
            "negative",
            None,
        ),
    ]
    assert [
        (d.code, d.source_pointer, d.endpoint_path, d.endpoint_method, d.message)
        for d in suite.diagnostics
    ] == [
        (
            "unsupported_schema_keyword",
            "/paths/~1x/post/requestBody/content/application~1json/schema/properties/a/format",
            "/x",
            "post",
            "Schema keyword 'format' is outside the supported subset.",
        )
    ]


# --- Step 8.44.4: deterministic request media type selection ------------------------

PLAIN_JSON = "application/json"
CHARSET_JSON = "application/json; charset=utf-8"
IGNORED_CODE = "request_media_type_ignored"
CONTENT_POINTER = "/paths/~1x/post/requestBody/content"
SCHEMA_A: dict[str, object] = {
    "type": "object",
    "required": ["a"],
    "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 9}},
}
SCHEMA_B: dict[str, object] = {
    "type": "object",
    "required": ["b"],
    "properties": {"b": {"type": "string", "maxLength": 4}},
}
RICH_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["name", "age", "kind", "tags"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 5},
        "age": {"type": "integer", "minimum": 1, "maximum": 9},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "tags": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "integer"}},
    },
}
BOTH_VERSIONS = pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])


def media_body(
    content: dict[str, object], *, required: bool, encoding: dict[str, dict[str, object]] | None = None
) -> RequestBody:
    entries = {
        key: MediaType(
            schema=None if schema is None else Schema(raw=schema),  # type: ignore[arg-type]
            encoding=(encoding or {}).get(key, {}),
        )
        for key, schema in content.items()
    }
    return RequestBody(required=required, content=entries)


def media_suite(
    request_body: RequestBody | LocalReference,
    *,
    version: str = "3.1.1",
    request_bodies: dict[str, RequestBody | LocalReference] | None = None,
    schemas: dict[str, object] | None = None,
) -> GeneratedTestSuite:
    return generate_test_suite(
        make_result(
            body_endpoint(request_body),
            version=version,
            components=Components(
                request_bodies=request_bodies or {},
                schemas={name: Schema(raw=raw) for name, raw in (schemas or {}).items()},  # type: ignore[arg-type]
                security_schemes=declared_security_schemes("apiKey").security_schemes,
            ),
        )
    )


def body_labels(suite: GeneratedTestSuite) -> set[str]:
    return {case.request_body.media_type for case in suite.cases if case.request_body is not None}


def diagnostic_summary(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    return [(item.code, item.source_pointer) for item in suite.diagnostics]


def ignored_pointer(key: str) -> str:
    return f"{CONTENT_POINTER}/{key.replace('~', '~0').replace('/', '~1')}"


def body_values(suite: GeneratedTestSuite) -> list[object]:
    return [case.request_body.value if case.request_body else None for case in suite.cases]


def test_json_only_required_and_optional_bodies_report_no_media_type_diagnostic() -> None:
    for required in (True, False):
        suite = media_suite(media_body({PLAIN_JSON: SCHEMA_A}, required=required))

        assert diagnostic_summary(suite) == []
        assert body_labels(suite) == {PLAIN_JSON}
        assert suite.cases[0].request_body is not None
        assert suite.cases[0].request_body.value == {"a": 1}


@BOTH_VERSIONS
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(
    "other", ["application/xml", "application/x-www-form-urlencoded", "multipart/form-data", "*/*", "text/plain"]
)
def test_plain_json_wins_over_unsupported_media_types_that_are_reported(
    version: str, required: bool, other: str
) -> None:
    json_only = media_suite(media_body({PLAIN_JSON: SCHEMA_A}, required=required), version=version)
    suite = media_suite(
        media_body({PLAIN_JSON: SCHEMA_A, other: SCHEMA_B}, required=required), version=version
    )

    assert suite.cases == json_only.cases
    assert body_labels(suite) == {PLAIN_JSON}
    assert [
        (d.code, d.endpoint_path, d.endpoint_method, d.source_pointer, d.reference_uri, d.message)
        for d in suite.diagnostics
    ] == [
        (
            IGNORED_CODE,
            "/x",
            "post",
            ignored_pointer(other),
            None,
            f"Request media type {other!r} was ignored; 'application/json' is generated.",
        )
    ]
    assert (suite.cases[1].case_id == build_case_id("positive", "/x", "post", OMITTED_VARIANT)) is (
        not required
    )


@BOTH_VERSIONS
@pytest.mark.parametrize("required", [True, False])
def test_charset_variant_alone_is_selected_and_keeps_its_declared_key(
    version: str, required: bool
) -> None:
    plain = media_suite(media_body({PLAIN_JSON: SCHEMA_A}, required=required), version=version)
    suite = media_suite(media_body({CHARSET_JSON: SCHEMA_A}, required=required), version=version)

    assert diagnostic_summary(suite) == []
    assert body_labels(suite) == {CHARSET_JSON}
    assert [case.case_id for case in suite.cases] == [case.case_id for case in plain.cases]
    assert body_values(suite) == body_values(plain)
    schema_pointer = f"{CONTENT_POINTER}/application~1json; charset=utf-8/schema"
    assert suite.cases[0].provenance[-1] == schema_pointer


@pytest.mark.parametrize(
    "key",
    [
        "application/json;charset=utf-8",
        "application/json ;charset=utf-8",
        "application/json ; charset=utf-8",
        "application/json;\tcharset=utf-8",
        "application/json; charset = utf-8",
        "  application/json; charset=utf-8  ",
        "Application/JSON",
        "APPLICATION/JSON; CHARSET=UTF-8",
        "application/json; Charset=Utf-8",
        'application/json; charset="utf-8"',
        'application/json; charset="UTF-8"',
        'application/json;charset = "utf-8" ',
    ],
)
def test_supported_spellings_of_json_media_types_are_selected(key: str) -> None:
    suite = media_suite(media_body({key: SCHEMA_A}, required=True))

    assert diagnostic_summary(suite) == []
    assert body_labels(suite) == {key}
    assert suite.cases[0].request_body is not None
    assert suite.cases[0].request_body.value == {"a": 1}
    assert len(suite.cases) == len(media_suite(media_body({PLAIN_JSON: SCHEMA_A}, required=True)).cases)


@pytest.mark.parametrize(
    "key",
    [
        "application/json; charset=iso-8859-1",
        "application/json; charset=utf8",
        "application/json; charset=utf-16",
        "application/json; charset=utf-8; boundary=x",
        "application/json; charset=utf-8;",
        "application/json; q=1",
        "application/json;",
        "application/json; charset",
        "application/json; charset=",
        "application/json;; charset=utf-8",
        'application/json; charset="utf-8',
        'application/json; charset=" utf-8"',
        "application/json; charset='utf-8'",
        "application/json; xcharset=utf-8",
        "application/jsön",
        "application/jſon",
        "application/json; charſet=utf-8",
        "application/problem+json",
        "application/vnd.example+json",
        "application/*",
        "application/",
        "/json",
        "application/json/x",
        "application json",
        "json",
        "",
        " ",
        ";",
    ],
)
def test_unsupported_or_malformed_media_type_keys_keep_the_unsupported_behavior(key: str) -> None:
    required = media_suite(media_body({key: SCHEMA_A}, required=True))

    assert required.cases == ()
    assert diagnostic_summary(required) == [("unsupported_request_media_type", CONTENT_POINTER)]

    optional = media_suite(media_body({key: SCHEMA_A}, required=False))

    assert [case.request_body for case in optional.cases] == [None] * len(optional.cases)
    assert [case.case_id for case in optional.cases[:1]] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert diagnostic_summary(optional) == [("unsupported_request_media_type", CONTENT_POINTER)]


def test_only_unsupported_media_types_and_empty_content_keep_the_existing_behavior() -> None:
    for content in (
        {"application/xml": SCHEMA_A, "text/plain": SCHEMA_B},
        {"multipart/form-data": SCHEMA_A, "*/*": SCHEMA_B, "application/*": None},
        {},
    ):
        required = media_suite(media_body(content, required=True))
        optional = media_suite(media_body(content, required=False))

        assert required.cases == ()
        assert diagnostic_summary(required) == [("unsupported_request_media_type", CONTENT_POINTER)]
        assert all(case.request_body is None for case in optional.cases)
        assert diagnostic_summary(optional) == [("unsupported_request_media_type", CONTENT_POINTER)]
        assert [case.case_id for case in optional.cases[:1]] == [
            build_case_id("positive", "/x", "post", "baseline")
        ]
        assert len(optional.cases) == 2


@BOTH_VERSIONS
def test_plain_json_wins_over_a_charset_variant_with_a_different_schema(version: str) -> None:
    suite = media_suite(
        media_body({PLAIN_JSON: SCHEMA_A, CHARSET_JSON: SCHEMA_B}, required=True), version=version
    )

    assert body_labels(suite) == {PLAIN_JSON}
    assert all(
        case.request_body is None or "b" not in case.request_body.value  # type: ignore[operator]
        for case in suite.cases
    )
    assert suite.cases[0].request_body is not None
    assert suite.cases[0].request_body.value == {"a": 1}
    assert [(d.code, d.source_pointer) for d in suite.diagnostics] == [
        (IGNORED_CODE, ignored_pointer(CHARSET_JSON))
    ]
    assert suite.diagnostics[0].message == (
        "Request media type 'application/json; charset=utf-8' was ignored; "
        "'application/json' is generated."
    )


def test_a_selected_charset_variant_also_reports_every_other_declaration_in_sorted_order() -> None:
    suite = media_suite(
        media_body(
            {"text/plain": SCHEMA_B, CHARSET_JSON: SCHEMA_A, "*/*": SCHEMA_B, "application/xml": SCHEMA_B},
            required=False,
        )
    )

    assert body_labels(suite) == {CHARSET_JSON}
    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, ignored_pointer("*/*")),
        (IGNORED_CODE, ignored_pointer("application/xml")),
        (IGNORED_CODE, ignored_pointer("text/plain")),
    ]
    assert suite.cases[0].request_body is not None
    assert suite.cases[0].request_body.value == {"a": 1}


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(
    "keys",
    [
        (PLAIN_JSON, "Application/JSON"),
        (PLAIN_JSON, "application/json "),
        (CHARSET_JSON, "application/json;charset=UTF-8"),
        (CHARSET_JSON, 'application/json; charset="utf-8"'),
        (PLAIN_JSON, "APPLICATION/JSON", CHARSET_JSON),
    ],
)
def test_several_candidates_of_the_winning_priority_are_ambiguous(
    required: bool, keys: tuple[str, ...]
) -> None:
    schemas = (SCHEMA_A, SCHEMA_B, SCHEMA_A)
    content = {key: schemas[index] for index, key in enumerate(keys)}
    suite = media_suite(media_body(content, required=required))

    assert diagnostic_summary(suite) == [("ambiguous_request_media_type", CONTENT_POINTER)]
    message = suite.diagnostics[0].message
    assert all(repr(key) in message for key in keys[:2])
    assert all(repr(key) not in message for key in keys[2:])
    assert all(case.request_body is None for case in suite.cases)
    if required:
        assert suite.cases == ()
    else:
        assert [case.case_id for case in suite.cases[:1]] == [
            build_case_id("positive", "/x", "post", "baseline")
        ]
        assert len(suite.cases) == 2


def test_ambiguity_is_decided_at_the_winning_priority_only() -> None:
    one_plain = media_suite(
        media_body(
            {PLAIN_JSON: SCHEMA_A, CHARSET_JSON: SCHEMA_B, "application/json;charset=UTF-8": SCHEMA_B},
            required=True,
        )
    )

    assert body_labels(one_plain) == {PLAIN_JSON}
    assert diagnostic_summary(one_plain) == [
        (IGNORED_CODE, ignored_pointer(CHARSET_JSON)),
        (IGNORED_CODE, ignored_pointer("application/json;charset=UTF-8")),
    ]

    two_plain = media_suite(
        media_body({PLAIN_JSON: SCHEMA_A, "Application/Json": SCHEMA_B, CHARSET_JSON: SCHEMA_B}, required=True)
    )

    assert two_plain.cases == ()
    assert diagnostic_summary(two_plain) == [("ambiguous_request_media_type", CONTENT_POINTER)]


@BOTH_VERSIONS
def test_each_selected_media_type_resolves_its_own_schema_reference(version: str) -> None:
    schemas = {"Alpha": SCHEMA_A, "Beta": SCHEMA_B}
    content = {
        PLAIN_JSON: {"$ref": "#/components/schemas/Alpha"},
        CHARSET_JSON: {"$ref": "#/components/schemas/Beta"},
    }
    plain = media_suite(media_body(content, required=True), version=version, schemas=schemas)

    assert plain.cases[0].request_body is not None
    assert plain.cases[0].request_body.value == {"a": 1}
    assert "/components/schemas/Alpha" in " ".join(plain.cases[0].provenance)
    assert "/components/schemas/Beta" not in " ".join(plain.cases[0].provenance)

    without_plain = media_suite(
        media_body({CHARSET_JSON: content[CHARSET_JSON], "application/xml": content[PLAIN_JSON]}, required=True),
        version=version,
        schemas=schemas,
    )

    assert without_plain.cases[0].request_body is not None
    assert set(without_plain.cases[0].request_body.value) == {"b"}  # type: ignore[arg-type]
    assert "/components/schemas/Beta" in " ".join(without_plain.cases[0].provenance)
    assert "/components/schemas/Alpha" not in " ".join(without_plain.cases[0].provenance)


def test_a_referenced_request_body_selects_among_its_own_content() -> None:
    suite = media_suite(
        request_body_reference("Shared"),
        request_bodies={
            "Shared": media_body({PLAIN_JSON: SCHEMA_A, "application/xml": SCHEMA_B}, required=True)
        },
    )

    assert body_labels(suite) == {PLAIN_JSON}
    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, "/components/requestBodies/Shared/content/application~1xml")
    ]


@pytest.mark.parametrize("required", [True, False])
def test_encoding_on_a_non_selected_declaration_is_not_reported(required: bool) -> None:
    suite = media_suite(
        media_body(
            {PLAIN_JSON: SCHEMA_A, "multipart/form-data": SCHEMA_B},
            required=required,
            encoding={"multipart/form-data": {"b": {"contentType": "text/plain"}}},
        )
    )

    assert diagnostic_summary(suite) == [(IGNORED_CODE, ignored_pointer("multipart/form-data"))]
    assert body_labels(suite) == {PLAIN_JSON}


def test_encoding_on_the_selected_declaration_is_still_unsupported() -> None:
    suite = media_suite(
        media_body(
            {CHARSET_JSON: SCHEMA_A, "application/xml": SCHEMA_B},
            required=False,
            encoding={CHARSET_JSON: {"a": {}}},
        )
    )

    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, ignored_pointer("application/xml")),
        ("unsupported_request_encoding", f"{CONTENT_POINTER}/application~1json; charset=utf-8/encoding"),
    ]
    assert all(case.request_body is None for case in suite.cases)


@BOTH_VERSIONS
@pytest.mark.parametrize("required", [True, False])
def test_every_generation_step_uses_the_selected_media_type_and_schema(
    version: str, required: bool
) -> None:
    plain = media_suite(media_body({PLAIN_JSON: RICH_SCHEMA}, required=required), version=version)
    selected = media_suite(
        media_body({CHARSET_JSON: RICH_SCHEMA, "application/xml": SCHEMA_B}, required=required),
        version=version,
    )

    constraints = {case.violated_constraint for case in plain.cases}
    assert {
        "required_property",
        "minimum",
        "maximum",
        "type",
        "enum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    } <= constraints
    valid_edges = [c for c in plain.cases if c.category == "positive" and c.request_body]
    assert len(valid_edges) >= 3

    assert [case.case_id for case in selected.cases] == [case.case_id for case in plain.cases]
    assert [case.category for case in selected.cases] == [case.category for case in plain.cases]
    assert [case.violated_constraint for case in selected.cases] == [
        case.violated_constraint for case in plain.cases
    ]
    assert body_values(selected) == body_values(plain)
    assert body_labels(selected) == {CHARSET_JSON}
    assert all(
        "b" not in case.request_body.value  # type: ignore[operator]
        for case in selected.cases
        if case.request_body is not None
    )
    assert diagnostic_summary(selected) == [(IGNORED_CODE, ignored_pointer("application/xml"))]
    # Every pointer of every step names the selected declaration: with the declared
    # key swapped back, the provenance equals the plain JSON suite's, case by case.
    assert [
        tuple(item.replace("application~1json; charset=utf-8", "application~1json") for item in case.provenance)
        for case in selected.cases
    ] == [case.provenance for case in plain.cases]
    assert any(
        len(case.provenance) > 3 and "application~1json; charset=utf-8/schema/" in case.provenance[-2]
        for case in selected.cases
    )
    for case in selected.cases:
        assert not any("application~1json/" in item or "application~1xml" in item for item in case.provenance)


def test_the_selected_schema_is_the_one_that_drives_every_case_when_schemas_differ() -> None:
    suite = media_suite(
        media_body({PLAIN_JSON: RICH_SCHEMA, CHARSET_JSON: SCHEMA_A, "application/xml": SCHEMA_B}, required=True)
    )

    assert body_labels(suite) == {PLAIN_JSON}
    assert all(
        set(case.request_body.value) <= {"name", "age", "kind", "tags"}  # type: ignore[arg-type]
        for case in suite.cases
        if case.request_body is not None
    )
    assert {case.violated_constraint for case in suite.cases} >= {"minLength", "minItems", "enum"}


@pytest.mark.parametrize("required", [True, False])
def test_selection_and_diagnostics_do_not_depend_on_declaration_order(required: bool) -> None:
    declared = {
        "text/plain": SCHEMA_B,
        CHARSET_JSON: SCHEMA_B,
        "application/xml": SCHEMA_B,
        PLAIN_JSON: SCHEMA_A,
        "*/*": None,
    }
    forward = media_suite(media_body(declared, required=required))
    backward = media_suite(media_body(dict(reversed(list(declared.items()))), required=required))
    shuffled = media_suite(media_body({k: declared[k] for k in sorted(declared, key=len)}, required=required))

    assert forward.model_dump_json() == backward.model_dump_json() == shuffled.model_dump_json()
    assert [d.source_pointer for d in forward.diagnostics] == [
        ignored_pointer(key) for key in sorted(declared) if key != PLAIN_JSON
    ]
    assert generate_test_suite(
        make_result(body_endpoint(media_body(declared, required=required)))
    ).model_dump_json() == generate_test_suite(
        make_result(body_endpoint(media_body(declared, required=required)))
    ).model_dump_json()


def test_ambiguity_is_independent_of_declaration_order() -> None:
    first = media_suite(media_body({PLAIN_JSON: SCHEMA_A, "Application/JSON": SCHEMA_B}, required=False))
    second = media_suite(media_body({"Application/JSON": SCHEMA_B, PLAIN_JSON: SCHEMA_A}, required=False))

    assert first.model_dump_json() == second.model_dump_json()
    assert first.diagnostics[0].message == (
        "Several supported request media types have the same priority and none is selected: "
        "'Application/JSON', 'application/json'."
    )


def test_ignored_media_types_are_reported_before_a_selected_body_failure() -> None:
    suite = media_suite(
        media_body({PLAIN_JSON: UNSUPPORTED_BODY_SCHEMA, "application/xml": SCHEMA_B}, required=False)
    )

    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, ignored_pointer("application/xml")),
        (
            "unsupported_schema_keyword",
            f"{CONTENT_POINTER}/application~1json/schema/properties/a/format",
        ),
    ]
    assert all(case.request_body is None for case in suite.cases)


FAILING_SELECTED_BODY_SCENARIOS = {
    "schema": (
        {PLAIN_JSON: UNSUPPORTED_BODY_SCHEMA},
        {},
        (
            "unsupported_schema_keyword",
            f"{CONTENT_POINTER}/application~1json/schema/properties/a/format",
        ),
    ),
    "encoding": (
        {PLAIN_JSON: SCHEMA_A},
        {PLAIN_JSON: {"a": {}}},
        ("unsupported_request_encoding", f"{CONTENT_POINTER}/application~1json/encoding"),
    ),
}


@pytest.mark.parametrize("scenario", sorted(FAILING_SELECTED_BODY_SCENARIOS))
def test_a_required_selected_body_failure_keeps_the_ignored_media_types_in_order(
    scenario: str,
) -> None:
    selected, encoding, failure = FAILING_SELECTED_BODY_SCENARIOS[scenario]
    content = {**selected, "text/plain": SCHEMA_B, "application/xml": SCHEMA_B}
    suite = media_suite(media_body(content, required=True, encoding=encoding))

    assert suite.cases == ()
    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, ignored_pointer("application/xml")),
        (IGNORED_CODE, ignored_pointer("text/plain")),
        failure,
    ]
    assert [d.endpoint_path for d in suite.diagnostics] == ["/x"] * 3
    assert [d.endpoint_method for d in suite.diagnostics] == ["post"] * 3
    assert suite.diagnostics[0].message == (
        "Request media type 'application/xml' was ignored; 'application/json' is generated."
    )
    reversed_content = dict(reversed(list(content.items())))
    assert (
        media_suite(media_body(reversed_content, required=True, encoding=encoding)).model_dump_json()
        == suite.model_dump_json()
    )


@pytest.mark.parametrize("scenario", sorted(FAILING_SELECTED_BODY_SCENARIOS))
def test_an_optional_selected_body_failure_keeps_the_body_less_baseline_and_both_diagnostics(
    scenario: str,
) -> None:
    selected, encoding, failure = FAILING_SELECTED_BODY_SCENARIOS[scenario]
    content = {**selected, "text/plain": SCHEMA_B, "application/xml": SCHEMA_B}
    suite = media_suite(media_body(content, required=False, encoding=encoding))

    assert [case.case_id for case in suite.cases[:1]] == [
        build_case_id("positive", "/x", "post", "baseline")
    ]
    assert len(suite.cases) == 2
    assert all(case.request_body is None for case in suite.cases)
    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, ignored_pointer("application/xml")),
        (IGNORED_CODE, ignored_pointer("text/plain")),
        failure,
    ]


def test_a_required_failure_before_a_media_type_is_selected_reports_no_ignored_diagnostic() -> None:
    for content in ({"application/xml": SCHEMA_A}, {PLAIN_JSON: SCHEMA_A, "Application/JSON": SCHEMA_B}):
        suite = media_suite(media_body(content, required=True))

        assert suite.cases == ()
        assert len(suite.diagnostics) == 1
        assert suite.diagnostics[0].code != IGNORED_CODE


def test_a_required_referenced_body_failure_keeps_the_ignored_media_types() -> None:
    suite = media_suite(
        request_body_reference("Shared"),
        request_bodies={
            "Shared": media_body(
                {CHARSET_JSON: UNSUPPORTED_BODY_SCHEMA, "application/xml": SCHEMA_B}, required=True
            )
        },
    )
    declaration = "/components/requestBodies/Shared/content"

    assert suite.cases == ()
    assert diagnostic_summary(suite) == [
        (IGNORED_CODE, f"{declaration}/application~1xml"),
        (
            "unsupported_schema_keyword",
            f"{declaration}/application~1json; charset=utf-8/schema/properties/a/format",
        ),
    ]


def test_a_media_type_without_a_schema_is_selected_like_before() -> None:
    suite = media_suite(media_body({CHARSET_JSON: None, "application/xml": SCHEMA_B}, required=True))

    assert body_labels(suite) == {CHARSET_JSON}
    assert diagnostic_summary(suite) == [(IGNORED_CODE, ignored_pointer("application/xml"))]
