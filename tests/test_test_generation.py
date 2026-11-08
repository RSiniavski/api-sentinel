"""Tests for deterministic test-generation foundation models and entry point."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import app.services.test_generation.generator as generator_module
from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
    Components,
    Endpoint,
    LocalReference,
    MediaType,
    NormalizationResult,
    Parameter,
    ParameterOverrideDiagnostic,
    ParameterReference,
    Response,
    RequestBody,
    Schema,
    SecurityRequirement,
)
from app.services.test_generation import (
    GeneratedBody,
    GeneratedParameterValue,
    GeneratedTestCase,
    GeneratedTestSuite,
    GenerationDiagnostic,
    ResponseExpectation,
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


def make_result(*endpoints: Endpoint, version: str = "3.1.1") -> NormalizationResult:
    return NormalizationResult(
        api=ApiModel(
            openapi_version=version,
            info=ApiInfo(title="Generated API", version="1.0"),
            endpoints=tuple(endpoints),
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
    result = make_result(endpoint)
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
    result = make_result(endpoint)
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


@pytest.mark.parametrize("request_body", [
    None,
    RequestBody(
        required=False,
        content={"application/json": MediaType(schema=Schema(raw={"type": "string"}))},
    ),
])
def test_optional_or_absent_request_body_has_no_omission_case(
    request_body: RequestBody | None,
) -> None:
    endpoint = make_endpoint("/optional-body", "post", request_body=request_body)

    suite = generate_test_suite(make_result(endpoint))

    assert len(suite.cases) == 1
    assert suite.cases[0].category == "positive"
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
    result = make_result(endpoint)
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
        schema: dict[str, object], value: object, version: str, direction: str, depth: int
    ) -> bool:
        if type(value) is int and value in (103, 101) and "const" not in schema:
            match_checks.append((dict(schema), value))  # type: ignore[arg-type]
        return original_matches(schema, value, version, direction, depth)  # type: ignore[arg-type]

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

    assert sum(case.category == "positive" for case in suite.cases) == 1
    positive = suite.cases[0]
    body = positive.request_body
    assert body == GeneratedBody(
        media_type="application/json",
        value={"name": "sentinel", "secret": "sentinel"},
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
                                "name": {"type": "string", "format": "email"}
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
                "/ref-body",
                "post",
                request_body=LocalReference(uri="#/components/requestBodies/Input"),
            ),
            "unsupported_request_body_reference",
            "#/components/requestBodies/Input",
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

    suite = generate_test_suite(make_result(endpoint))
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

    assert suite.cases == ()
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

    assert suite.cases == ()
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
            components=Components(responses=responses),
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
    assert count_expansions(alias_chain_result()) == 3
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
    monkeypatch.setattr(generator_module, "_MAX_SUITE_EXPANSIONS", 2)
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
    assert "per-suite limit of 2" in suite.diagnostics[0].message
    assert "1 following endpoint(s)" in suite.diagnostics[0].message
