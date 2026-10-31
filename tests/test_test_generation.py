"""Tests for deterministic test-generation foundation models and entry point."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
    Endpoint,
    NormalizationResult,
    ParameterOverrideDiagnostic,
    Response,
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


def test_generated_models_are_frozen() -> None:
    parameter, body, expectation, diagnostic, case, suite = sample_output_models()
    for model, field, value in (
        (parameter, "name", "changed"),
        (body, "media_type", "text/plain"),
        (expectation, "status_key", "500"),
        (diagnostic, "code", "changed"),
        (case, "path", "/changed"),
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


def test_generation_copies_api_metadata_and_returns_empty_outputs() -> None:
    result = sample_normalization_result()

    suite = generate_test_suite(result)

    assert suite.api_title == "Inventory API"
    assert suite.api_version == "2.4.0"
    assert suite.openapi_version == "3.1.1"
    assert suite.cases == ()
    assert suite.diagnostics == ()


def test_generation_does_not_copy_normalization_diagnostics() -> None:
    result = sample_normalization_result()

    suite = generate_test_suite(result)

    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "parameter_overridden"
    assert suite.diagnostics == ()


def test_repeated_generation_is_equivalent_and_does_not_mutate_input() -> None:
    result = sample_normalization_result()
    before = result.model_dump_json()

    first = generate_test_suite(result)
    second = generate_test_suite(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert result.model_dump_json() == before


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
