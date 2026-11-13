"""Tests for deterministic test-generation foundation models and entry point."""

from __future__ import annotations

import json
from copy import deepcopy

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
    SecurityScheme,
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


def array_diagnostics(suite: GeneratedTestSuite) -> list[tuple[str, str | None]]:
    return [
        (item.code, item.source_pointer)
        for item in suite.diagnostics
        if item.code.startswith("array_bound_")
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
            {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^a$"}}
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
                "items": {"type": "string", "pattern": "^a$"},
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
                "items": {"type": "string", "pattern": "^a$"},
            }
        ),
        {"tags": ["a"]},
    )

    assert [case.violated_constraint for case in cases] == ["minItems"]
    assert [(item.code, item.source_pointer) for item in diagnostics] == [
        ("array_bound_unsupported_schema", tags_schema_pointer("items", "pattern"))
    ]


UNSUPPORTED_ITEMS: dict[str, object] = {"type": "string", "pattern": "^a$"}


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
        tags_body({"type": "array", "maxItems": 1, "items": {"type": "string", "pattern": "^a$"}}),
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


def test_out_of_scope_arrays_are_not_generated() -> None:
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

    assert array_summary(nested_array) == [("/tags", "minItems", 0)]


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


def test_unique_items_out_of_scope_arrays_are_not_generated() -> None:
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

    assert unique_cases(nested_array.cases) == [] and array_diagnostics(nested_array) == []
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

UNSUPPORTED_ITEMS: dict[str, object] = {"type": "string", "pattern": "^a$"}
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
    suite = unique_suite({"type": "string", "pattern": "^a$", "examples": ["a", "aa"]}, 2)

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
        {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^a$"}},
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
    unsupported = root_suite({"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string", "pattern": "^a$"}})
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
        {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "pattern": "^a$"}}
    )

    # The positive baseline needs an item here, so nothing is generated at all.
    assert suite.cases == ()
    empty_ok = root_suite({"type": "array", "maxItems": 2, "items": {"type": "string", "pattern": "^a$"}})
    assert body_value(empty_ok.cases[0]) == []
    assert [(item.code, item.source_pointer) for item in empty_ok.diagnostics] == [
        ("array_bound_unsupported_schema", f"{ROOT_PATH}/items/pattern")
    ]


def test_root_array_enum_literal_candidates_and_nested_arrays_are_not_expanded() -> None:
    literal = root_suite({"type": "array", "maxItems": 1, "enum": [[1], [1, 2]], "items": {"type": "integer"}})
    nested = root_suite(
        {"type": "array", "minItems": 1, "items": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "integer"}}}
    )

    assert body_value(root_array_cases(literal)["maxItems"]) == [1, 2]
    assert [(case.violated_constraint, body_value(case)) for case in array_cases(nested)] == [("minItems", [])]
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
        ({"type": "string", "pattern": "^a$"}, None, "/schema/pattern", None),
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
        {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "pattern": "^a$"}},
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


def test_optional_array_inside_an_absent_parent_is_not_materialized() -> None:
    absent_parent = optional_body(
        {"a": ITEM_SCHEMA, "outer": optional_body({"tags": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA}}, ("tags",))},
        ("a",),
    )
    absent_optional_in_absent = optional_body(
        {"outer": optional_body({"tags": {"type": "array", "maxItems": 1, "items": ITEM_SCHEMA}})}
    )

    for body in (absent_parent, absent_optional_in_absent):
        suite = optional_array_suite(body)
        assert array_cases(suite) == []
        assert array_diagnostics(suite) == []
        assert "outer" not in (suite.cases[0].request_body.value or {})  # type: ignore[union-attr]


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
    for unusable in ({"$ref": "#/components/schemas/Missing"}, {"type": "string", "pattern": "^x$"}):
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

AP_UNSUPPORTED = {"type": "string", "pattern": "^x$"}
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
