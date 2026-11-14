"""End-to-end `pattern` tests: OpenAPI text -> Parser -> Normalizer -> Generator.

The documents are real JSON and YAML text parsed by the actual Parser. The
Parser does not reject syntactically invalid or non-ECMA patterns in this
pipeline, so the Generator alone decides whether a pattern is supported.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from app.services.openapi import normalize_openapi_document, parse_openapi_document
from app.services.openapi.models import NormalizationResult
from app.services.test_generation import GeneratedTestSuite, generate_test_suite

VERSIONS = ("3.0.3", "3.1.1")
FORMATS = ("json", "yaml")
BODY_POINTER = "/paths/~1items/post/requestBody/content/application~1json/schema"


def render(document: dict[str, object], fmt: str) -> tuple[str, str]:
    if fmt == "json":
        return json.dumps(document), "pattern-api.json"
    return yaml.safe_dump(document, sort_keys=False), "pattern-api.yaml"


def build_document(
    version: str,
    code_schema: dict[str, object],
    *,
    components: dict[str, object] | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {
        "openapi": version,
        "info": {"title": "Pattern API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["code"],
                                    "properties": {"code": code_schema},
                                }
                            }
                        },
                    },
                    "responses": {"400": {"description": "Bad request"}},
                }
            }
        },
    }
    if components is not None:
        document["components"] = {"schemas": components}
    return document


def run_pipeline(document: dict[str, object], fmt: str) -> tuple[NormalizationResult, GeneratedTestSuite]:
    text, filename = render(document, fmt)
    parsed = parse_openapi_document(text, filename)
    result = normalize_openapi_document(parsed)
    return result, generate_test_suite(result)


def bodies(suite: GeneratedTestSuite) -> list[object]:
    return [case.request_body.value for case in suite.cases if case.request_body is not None]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_supported_pattern_with_a_valid_example_gives_a_positive_case(version: str, fmt: str) -> None:
    schema = {"type": "string", "pattern": r"^[A-Z]{2}-\d{3}$", "example": "AB-123"}

    result, suite = run_pipeline(build_document(version, schema), fmt)

    assert result.diagnostics == ()
    assert suite.diagnostics == ()
    assert suite.cases[0].category == "positive"
    assert bodies(suite)[0] == {"code": "AB-123"}
    assert any(case.violated_constraint == "required_property" for case in suite.cases)


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_supported_pattern_with_an_invalid_example_never_yields_an_invalid_positive_case(
    version: str, fmt: str
) -> None:
    schema = {"type": "string", "pattern": r"^[A-Z]{2}-\d{3}$", "example": "ab-123"}

    _, suite = run_pipeline(build_document(version, schema), fmt)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["no_positive_schema_value"]
    assert suite.diagnostics[0].source_pointer == f"{BODY_POINTER}/properties/code"


@pytest.mark.parametrize("version", VERSIONS)
def test_invalid_example_is_skipped_in_favor_of_a_valid_default(version: str) -> None:
    schema = {
        "type": "string", "pattern": r"^[A-Z]{2}-\d{3}$", "example": "ab-123", "default": "CD-456",
    }

    _, suite = run_pipeline(build_document(version, schema), "yaml")

    assert bodies(suite)[0] == {"code": "CD-456"}
    assert "ab-123" not in json.dumps([case.model_dump(mode="json") for case in suite.cases])


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_supported_pattern_filters_the_sentinel_fallback(version: str, fmt: str) -> None:
    fits = {"type": "string", "pattern": "^sentinel$"}
    misfit = {"type": "string", "pattern": "^[0-9]+$"}

    _, fitting = run_pipeline(build_document(version, fits), fmt)
    _, failing = run_pipeline(build_document(version, misfit), fmt)

    assert bodies(fitting)[0] == {"code": "sentinel"}
    assert failing.cases == ()
    assert [item.code for item in failing.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize(
    "pattern", ["(?i)a", "a{,3}", "(unclosed", r"\p{L}+", "(?<n>a)"]
)
@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_parser_accepted_but_unsupported_pattern_is_rejected_by_the_generator(
    version: str, fmt: str, pattern: str
) -> None:
    document = build_document(version, {"type": "string", "pattern": pattern, "example": "a"})
    text, filename = render(document, fmt)

    parsed = parse_openapi_document(text, filename)
    assert parsed["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"][  # type: ignore[index]
        "schema"
    ]["properties"]["code"]["pattern"] == pattern
    result = normalize_openapi_document(parsed)
    suite = generate_test_suite(result)

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_keyword"]
    assert suite.diagnostics[0].source_pointer == f"{BODY_POINTER}/properties/code/pattern"
    assert suite.diagnostics[0].endpoint_path == "/items"
    assert suite.diagnostics[0].endpoint_method == "post"


@pytest.mark.parametrize("version", VERSIONS)
def test_unsupported_pattern_in_a_component_schema_points_at_the_component(version: str) -> None:
    components = {"Code": {"type": "string", "pattern": "a{,3}"}}
    document = build_document(version, {"$ref": "#/components/schemas/Code"}, components=components)

    _, suite = run_pipeline(document, "yaml")

    assert suite.cases == ()
    assert [item.code for item in suite.diagnostics] == ["unsupported_schema_keyword"]
    assert suite.diagnostics[0].source_pointer == "/components/schemas/Code/pattern"


@pytest.mark.parametrize("version", VERSIONS)
def test_supported_pattern_in_a_component_schema_is_enforced(version: str) -> None:
    ok = {"Code": {"type": "string", "pattern": "^[0-9]+$", "example": "123"}}
    bad = {"Code": {"type": "string", "pattern": "^[0-9]+$", "example": "ABC"}}
    reference = {"$ref": "#/components/schemas/Code"}

    _, accepted = run_pipeline(build_document(version, reference, components=ok), "json")
    _, rejected = run_pipeline(build_document(version, reference, components=bad), "json")

    assert bodies(accepted)[0] == {"code": "123"}
    assert rejected.cases == ()
    assert [item.code for item in rejected.diagnostics] == ["no_positive_schema_value"]


@pytest.mark.parametrize("version", VERSIONS)
def test_unsupported_pattern_on_a_required_query_parameter_points_at_the_parameter_schema(
    version: str,
) -> None:
    document = {
        "openapi": version,
        "info": {"title": "Pattern API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "code",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "pattern": "(?i)a"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    _, suite = run_pipeline(document, "yaml")

    assert suite.cases == ()
    assert [(item.code, item.source_pointer) for item in suite.diagnostics] == [
        ("unsupported_schema_keyword", "/paths/~1items/get/parameters/0/schema/pattern")
    ]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_pipeline_output_is_deterministic_and_does_not_mutate_the_parsed_document(
    version: str, fmt: str
) -> None:
    schema = {"type": "string", "pattern": r"^[A-Z]{2}-\d{3}$", "example": "AB-123", "maxLength": 6}
    text, filename = render(build_document(version, schema), fmt)

    first_parsed = parse_openapi_document(text, filename)
    before = deepcopy(first_parsed)
    first_result = normalize_openapi_document(first_parsed)
    first = generate_test_suite(first_result)
    second = generate_test_suite(normalize_openapi_document(parse_openapi_document(text, filename)))

    assert first_parsed == before
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert generate_test_suite(first_result) == first
