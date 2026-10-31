"""Tests for deterministic test-generation foundation models and entry point."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
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

    assert len(suite.cases) == 1
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

    assert len(suite.cases) == 3
    positive = suite.cases[0]
    body = positive.request_body
    assert body == GeneratedBody(
        media_type="application/json",
        value={"name": "sentinel", "secret": "sentinel"},
    )
    negatives = suite.cases[1:]
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

    assert len(suite.cases) == 2
    positive, negative = suite.cases
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

    assert len(suite.cases) == 3
    positive = suite.cases[0]
    assert positive.request_body.value == {"alpha": "sentinel", "beta": 0}
    negatives = {case.affected_property: case for case in suite.cases[1:]}
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

    assert len(suite.cases) == 3
    negatives = {case.affected_property: case for case in suite.cases[1:]}
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
    assert len(first.cases) == 4
    assert len({case.case_id for case in first.cases}) == 4
    negative = next(case for case in first.cases if case.affected_property == "/name")
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

    assert len(suite.cases) == 2
    negative = suite.cases[1]
    assert negative.expected_outcome == "request_rejection_status_unspecified"
    assert negative.response_expectations[0].status_key == "200"
    assert len(suite.diagnostics) == 1
    assert suite.diagnostics[0].code == "negative_response_contract_unavailable"
    assert "no HTTP status is inferred" in suite.diagnostics[0].message


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
