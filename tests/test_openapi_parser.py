"""Unit tests for the isolated OpenAPI parser."""

import json

import pytest

from app.services.openapi import (
    OpenAPIParseError,
    OpenAPIValidationError,
    UnsupportedOpenAPIVersionError,
    parse_openapi_document,
)


def openapi_document(version: str = "3.0.3") -> dict:
    return {
        "openapi": version,
        "info": {"title": "Example API", "version": "1.0.0"},
        "paths": {"/ping": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_parse_json_openapi(version: str) -> None:
    document = openapi_document(version)

    result = parse_openapi_document(json.dumps(document), "api.json")

    assert result == document
    assert result["openapi"] == version


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_parse_yaml_openapi(version: str) -> None:
    content = f'''openapi: "{version}"
info:
  title: Example API
  version: 1.0.0
paths:
  /ping:
    get:
      responses:
        "200":
          description: OK
'''

    result = parse_openapi_document(content, "api.yaml")

    assert result["openapi"] == version
    assert result["info"]["title"] == "Example API"
    assert result["paths"]["/ping"]["get"]["responses"]["200"]["description"] == "OK"


def test_malformed_json_raises_parse_error() -> None:
    with pytest.raises(OpenAPIParseError, match="Invalid JSON syntax"):
        parse_openapi_document('{"openapi":', "api.json")


def test_malformed_yaml_raises_parse_error() -> None:
    with pytest.raises(OpenAPIParseError, match="Invalid YAML syntax"):
        parse_openapi_document("openapi: [unclosed", "api.yaml")


def test_empty_document_raises_parse_error() -> None:
    with pytest.raises(OpenAPIParseError, match="empty"):
        parse_openapi_document("  \n", "api.yaml")


@pytest.mark.parametrize("document", [{}, {"openapi": "3.0.3"}])
def test_missing_mandatory_fields_raise_validation_error(document: dict) -> None:
    with pytest.raises(OpenAPIValidationError):
        parse_openapi_document(json.dumps(document), "api.json")


def test_unsupported_version_is_distinct() -> None:
    document = openapi_document("2.0")

    with pytest.raises(UnsupportedOpenAPIVersionError):
        parse_openapi_document(json.dumps(document), "api.json")


@pytest.mark.parametrize("version", ["3.0.abc", "3.1.whatever", "3.0.", "v3.0.3"])
def test_malformed_version_is_validation_error(version: str) -> None:
    document = openapi_document(version)

    with pytest.raises(OpenAPIValidationError, match="numeric major.minor.patch"):
        parse_openapi_document(json.dumps(document), "api.json")


@pytest.mark.parametrize("version", ["2.0.0", "3.2.0", "4.0.1"])
def test_well_formed_unsupported_version_is_distinct(version: str) -> None:
    document = openapi_document(version)

    with pytest.raises(UnsupportedOpenAPIVersionError):
        parse_openapi_document(json.dumps(document), "api.json")


def test_invalid_openapi_structure_raises_validation_error() -> None:
    document = openapi_document()
    document["paths"]["/ping"]["get"]["responses"] = {}

    with pytest.raises(OpenAPIValidationError, match="not a valid OpenAPI"):
        parse_openapi_document(json.dumps(document), "api.json")


def test_non_object_root_raises_validation_error() -> None:
    with pytest.raises(OpenAPIValidationError, match="root must be an object"):
        parse_openapi_document("[]", "api.json")


@pytest.mark.parametrize("filename", ["api.txt", "api", "api.json.bak", ""])
def test_invalid_filename_extension_is_rejected(filename: str) -> None:
    with pytest.raises(OpenAPIParseError, match="filename|extension"):
        parse_openapi_document("{}", filename)


def test_unsafe_yaml_tag_is_rejected_without_execution(tmp_path) -> None:
    marker = tmp_path / "executed.txt"
    payload = f'''!!python/object/apply:builtins.open
- {marker.as_posix()}
- w
'''

    with pytest.raises(OpenAPIParseError):
        parse_openapi_document(payload, "api.yaml")
    assert not marker.exists()


def test_yaml_aliases_are_rejected() -> None:
    with pytest.raises(OpenAPIParseError, match="Invalid YAML syntax"):
        parse_openapi_document("node: &node [*node]", "api.yaml")


@pytest.mark.parametrize("reference", [
    "https://example.invalid/response.json",
    "../schemas/response.yaml",
    "file:///tmp/response.json",
])
def test_external_reference_is_rejected_before_validator(monkeypatch, reference: str) -> None:
    import app.services.openapi.validator as validator_module

    def fail_if_called(_document):
        pytest.fail("OpenAPI validation must not resolve external references")

    monkeypatch.setattr(validator_module, "validate", fail_if_called)
    document = openapi_document()
    document["paths"]["/ping"]["get"]["responses"]["200"]["$ref"] = reference

    with pytest.raises(OpenAPIValidationError, match=r"Invalid or external \$ref"):
        parse_openapi_document(json.dumps(document), "api.json")


@pytest.mark.parametrize(
    "reference",
    ["", None, 42, "#bad~2pointer", "#bad%ZZ", "#bad anchor", "#bad[char]", "#café"],
)
def test_malformed_reference_is_rejected(reference) -> None:
    document = openapi_document()
    document["paths"]["/ping"]["get"]["responses"]["200"]["$ref"] = reference

    with pytest.raises(OpenAPIValidationError, match=r"Invalid or external \$ref"):
        parse_openapi_document(json.dumps(document), "api.json")


@pytest.mark.parametrize("reference", [
    "#/components/schemas/Pet",
    "#/components/schemas/A~1B",
    "#PetAnchor",
])
def test_valid_local_references_are_preserved(monkeypatch, reference: str) -> None:
    import app.services.openapi.validator as validator_module

    monkeypatch.setattr(validator_module, "validate", lambda _document: None)
    document = openapi_document("3.1.1")
    target = "Pet"
    schema = {"type": "object"}
    if "A~1B" in reference:
        schema["properties"] = {"A/B": {"type": "string"}}
    if reference == "#PetAnchor":
        schema["$anchor"] = "PetAnchor"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    document["components"] = {"schemas": {target: schema}}
    if "A~1B" in reference:
        reference = "#/components/schemas/Pet/properties/A~1B"
    document["paths"]["/ping"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"schema": {"$ref": reference}}
    }

    result = parse_openapi_document(json.dumps(document), "api.json")

    assert result["paths"]["/ping"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"] == reference


def test_valid_json_pointer_reference_passes_real_openapi_validation() -> None:
    document = openapi_document("3.1.1")
    document["components"] = {"schemas": {"Pet": {"type": "object"}}}
    reference = "#/components/schemas/Pet"
    document["paths"]["/ping"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"schema": {"$ref": reference}}
    }

    result = parse_openapi_document(json.dumps(document), "api.json")

    assert result["paths"]["/ping"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"] == reference


def test_real_validator_does_not_support_openapi_31_local_anchor() -> None:
    from openapi_spec_validator import validate
    from referencing.exceptions import Unresolvable

    document = openapi_document("3.1.1")
    document["components"] = {
        "schemas": {"Pet": {"type": "object", "$anchor": "PetAnchor"}}
    }
    document["paths"]["/ping"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"schema": {"$ref": "#PetAnchor"}}
    }

    # The anchor is valid under JSON Schema 2020-12, but this installed validator
    # fails to index component schema anchors while validating an OpenAPI document.
    with pytest.raises(Unresolvable):
        validate(document)
    with pytest.raises(OpenAPIValidationError):
        parse_openapi_document(json.dumps(document), "api.json")


def test_deep_reference_traversal_is_iterative() -> None:
    from app.services.openapi.validator import _reject_external_refs

    nested: dict = {"$ref": "#/components/schemas/Pet"}
    for _ in range(2000):
        nested = {"nested": nested}

    _reject_external_refs(nested)


def test_invalid_unicode_surrogate_raises_parse_error() -> None:
    with pytest.raises(OpenAPIParseError, match="valid UTF-8"):
        parse_openapi_document('{"name":"\ud800"}', "api.json")


def test_oversized_document_is_rejected() -> None:
    content = " " * (5 * 1024 * 1024 + 1)

    with pytest.raises(OpenAPIParseError, match="5 MiB"):
        parse_openapi_document(content, "api.json")
