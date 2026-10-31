"""Tests for API-level OpenAPI normalization."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.services.openapi import OpenAPINormalizationError, normalize_openapi_document
from app.services.openapi.models import (
    LocalReference,
    MediaType,
    Parameter,
    ParameterReference,
    RequestBody,
    Response,
    Schema,
    SecurityRequirement,
    SecurityScheme,
    Server,
)
from app.services.openapi.parser import parse_openapi_document


def openapi_document(
    version: str = "3.1.1", *, include_reference_extensions: bool = True
) -> dict[str, object]:
    """Return a valid-shaped document exercising API-level data and components."""
    is_31 = version.startswith("3.1.")
    info: dict[str, object] = {
        "title": "Inventory API",
        "version": "2.4.0",
        "description": "API description",
        "termsOfService": "https://example.test/terms",
        "contact": {"name": "API Support", "email": "support@example.test"},
        "license": {"name": "MIT", "url": "https://example.test/license"},
        "x-info-extension": {"team": "platform"},
    }
    if is_31:
        info["summary"] = "Manage inventory"
    schemas: dict[str, object] = {
        "Base": {"type": "object"},
        "Envelope": {
            "type": "object",
            "properties": {"data": {"$ref": "#/components/schemas/Item"}},
            "allOf": [{"$ref": "#/components/schemas/Base"}],
            "oneOf": [{"type": "string"}],
            "anyOf": [{"type": "integer"}],
            "x-schema-extension": "retained",
        },
        "Item": {"type": "object"},
    }
    if is_31:
        schemas["Envelope"]["not"] = {"enum": ["forbidden"]}  # type: ignore[index]

    parameters: dict[str, object] = {
        "Filter": {"name": "filter", "in": "query", "schema": {"type": "string"}},
        "FilterContent": {
            "name": "filter_content",
            "in": "query",
            "content": {"application/json": {"schema": {"type": "string"}}},
        },
        "SharedId": {"$ref": "#/components/parameters/Filter"},
    }
    if is_31:
        parameters["SharedId"] = {
            "$ref": "#/components/parameters/Filter",
            "description": "Reusable",
            "summary": "Shared filter",
            **(
                {"x-reference-source": "component-library"}
                if include_reference_extensions
                else {}
            ),
        }

    components: dict[str, object] = {
        "schemas": schemas,
        "parameters": parameters,
        "requestBodies": {
            "CreateItem": {
                "description": "New item",
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Item"},
                        "example": {"id": 7, "name": "widget"},
                    },
                    "application/xml": {
                        "schema": {"$ref": "#/components/schemas/Item"},
                        "examples": {
                            "sample": {"summary": "Sample", "value": {"id": 7}},
                            "shared": {
                                "$ref": "#/components/examples/Sample",
                                **(
                                    {"x-example-reference": "shared"}
                                    if is_31 and include_reference_extensions
                                    else {}
                                ),
                            },
                        },
                        "encoding": {
                            "name": {
                                "contentType": "text/plain",
                                "style": "form",
                                "explode": True,
                            }
                        },
                    }
                },
                "x-body-extension": "preserved",
            },
            "SharedBody": {"$ref": "#/components/requestBodies/CreateItem"},
        },
        "responses": {
            "Success": {
                "description": "Created",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
            },
            "SharedResponse": {"$ref": "#/components/responses/Success"},
        },
        "securitySchemes": {
            "apiKey": {"type": "apiKey", "name": "X-Key", "in": "header"},
            "oauth": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": "https://auth.example.test/authorize",
                        "tokenUrl": "https://auth.example.test/token",
                        "refreshUrl": "https://auth.example.test/refresh",
                        "scopes": {"read:items": "Read items", "write:items": "Write items"},
                    }
                },
            },
            "bearer": {
                "$ref": "#/components/securitySchemes/apiKey",
                **(
                    {"x-scheme-reference": "shared"}
                    if is_31 and include_reference_extensions
                    else {}
                ),
            },
        },
        "examples": {
            "Sample": {"summary": "Sample item", "value": {"id": 7}},
            "SharedExample": {"$ref": "#/components/examples/Sample"},
        },
        "headers": {"RateLimit": {"description": "Remaining", "schema": {"type": "integer"}}},
        "links": {"ItemLink": {"operationId": "getItem"}},
        "callbacks": {
            "Event": {
                "{$request.body#/callbackUrl}": {
                    "post": {"responses": {"204": {"description": "No content"}}}
                }
            }
        },
    }
    if is_31:
        components["pathItems"] = {
            "SharedPath": {"get": {"responses": {"200": {"description": "OK"}}}}
        }

    return {
        "openapi": version,
        "info": info,
        "servers": [
            {
                "url": "https://{region}.example.test/v1",
                "description": "Regional endpoint",
                "variables": {"region": {"default": "us", "enum": ["us", "eu"]}},
                "x-server-extension": True,
            },
            {"url": "https://backup.example.test"},
        ],
        "security": [
            {"apiKey": [], "oauth": ["read:items", "write:items"]},
            {"bearer": []},
            {},
        ],
        "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": components,
        "x-root-extension": ["preserved", 1],
    }


def operation(**overrides: object) -> dict[str, object]:
    """Create a minimal Operation Object and apply requested fields."""
    result: dict[str, object] = {"responses": {"200": {"description": "OK"}}}
    result.update(overrides)
    return result


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_normalizes_api_metadata_and_path_endpoint(version: str) -> None:
    result = normalize_openapi_document(openapi_document(version))

    assert result.api.openapi_version == version
    assert result.api.info.title == "Inventory API"
    assert result.api.info.version == "2.4.0"
    assert result.api.info.summary == ("Manage inventory" if version == "3.1.1" else None)
    assert result.api.info.description == "API description"
    assert result.api.info.terms_of_service == "https://example.test/terms"
    assert result.api.info.contact == {"name": "API Support", "email": "support@example.test"}
    assert result.api.info.license == {"name": "MIT", "url": "https://example.test/license"}
    assert result.api.info.extensions == {"x-info-extension": {"team": "platform"}}
    assert len(result.api.endpoints) == 1
    assert (result.api.endpoints[0].path, result.api.endpoints[0].method) == ("/items", "get")
    assert result.diagnostics == ()
    assert result.api.extensions == {"x-root-extension": ["preserved", 1]}


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_version_fixtures_pass_the_existing_openapi_parser(version: str) -> None:
    # openapi-spec-validator 0.9.0 currently rejects 3.1.1 Reference Object x- fields.
    document = openapi_document(version, include_reference_extensions=False)

    parsed = parse_openapi_document(json.dumps(document), "normalizer-fixture.json")

    assert parsed == document
    assert ("summary" in parsed["info"]) is (version == "3.1.1")
    assert ("pathItems" in parsed["components"]) is (version == "3.1.1")
    reference = parsed["components"]["parameters"]["SharedId"]
    assert ("summary" in reference or "description" in reference) is (version == "3.1.1")
    assert "x-reference-source" not in reference


def test_openapi_30_reference_metadata_remains_empty() -> None:
    components = normalize_openapi_document(openapi_document("3.0.3")).api.components

    parameter_ref = components.parameters["SharedId"]
    example_ref = components.request_bodies["CreateItem"].content[  # type: ignore[union-attr]
        "application/xml"
    ].examples["shared"]  # type: ignore[union-attr]
    assert isinstance(parameter_ref, ParameterReference)
    assert parameter_ref.reference.summary is None
    assert parameter_ref.reference.description is None
    assert parameter_ref.reference.extensions == {}
    assert isinstance(example_ref, LocalReference)
    assert example_ref.extensions == {}


def test_normalizes_root_servers_preserving_order_variables_and_extensions() -> None:
    api = normalize_openapi_document(openapi_document()).api

    assert api.servers == (
        Server(
            url="https://{region}.example.test/v1",
            description="Regional endpoint",
            variables={"region": {"default": "us", "enum": ["us", "eu"]}},
            extensions={"x-server-extension": True},
        ),
        Server(url="https://backup.example.test"),
    )


@pytest.mark.parametrize(
    ("servers", "expected"),
    [(None, None), ([], ())],
)
def test_root_servers_preserve_absent_and_empty_states(
    servers: list[object] | None, expected: tuple[Server, ...] | None
) -> None:
    document = openapi_document()
    if servers is None:
        del document["servers"]
    else:
        document["servers"] = servers

    assert normalize_openapi_document(document).api.servers == expected


def test_global_security_preserves_alternatives_combined_schemes_and_scope_order() -> None:
    security = normalize_openapi_document(openapi_document()).api.security

    assert security == (
        SecurityRequirement(schemes={"apiKey": (), "oauth": ("read:items", "write:items")}),
        SecurityRequirement(schemes={"bearer": ()}),
        SecurityRequirement(schemes={}),
    )
    assert security[0].schemes["oauth"] == ("read:items", "write:items")


@pytest.mark.parametrize(
    ("security", "expected"),
    [(None, None), ([], ()), ([{}], (SecurityRequirement(schemes={}),))],
)
def test_global_security_preserves_absent_empty_and_anonymous_states(
    security: list[object] | None, expected: tuple[SecurityRequirement, ...] | None
) -> None:
    document = openapi_document()
    if security is None:
        del document["security"]
    else:
        document["security"] = security

    assert normalize_openapi_document(document).api.security == expected


def test_component_registries_preserve_typed_entries_and_local_reference_edges() -> None:
    components = normalize_openapi_document(openapi_document()).api.components

    assert isinstance(components.schemas["Envelope"], Schema)
    assert components.schemas["Envelope"].raw["allOf"] == [
        {"$ref": "#/components/schemas/Base"}
    ]
    assert components.schemas["Envelope"].raw["oneOf"] == [{"type": "string"}]
    assert components.schemas["Envelope"].raw["anyOf"] == [{"type": "integer"}]
    if "not" in components.schemas["Envelope"].raw:
        assert components.schemas["Envelope"].raw["not"] == {"enum": ["forbidden"]}
    assert components.schemas["Envelope"].raw["x-schema-extension"] == "retained"

    assert isinstance(components.parameters["Filter"], Parameter)
    assert components.parameters["Filter"].source_scope == "component"  # type: ignore[union-attr]
    parameter_ref = components.parameters["SharedId"]
    assert isinstance(parameter_ref, ParameterReference)
    assert parameter_ref.reference.uri == "#/components/parameters/Filter"
    if parameter_ref.reference.description is not None:
        assert parameter_ref.reference.description == "Reusable"
        assert parameter_ref.reference.summary == "Shared filter"
        assert parameter_ref.reference.extensions == {
            "x-reference-source": "component-library"
        }
    else:
        assert parameter_ref.reference.extensions == {}
    assert parameter_ref.source_scope == "component"

    assert isinstance(components.request_bodies["CreateItem"], RequestBody)
    assert components.request_bodies["CreateItem"].required is True  # type: ignore[union-attr]
    assert isinstance(components.request_bodies["SharedBody"], LocalReference)
    assert components.request_bodies["SharedBody"].uri == "#/components/requestBodies/CreateItem"  # type: ignore[union-attr]
    assert isinstance(components.responses["Success"], Response)
    assert isinstance(components.responses["SharedResponse"], LocalReference)
    assert isinstance(components.security_schemes["apiKey"], SecurityScheme)
    assert components.security_schemes["apiKey"].location == "header"  # type: ignore[union-attr]
    assert isinstance(components.security_schemes["bearer"], LocalReference)
    assert components.security_schemes["bearer"].uri == "#/components/securitySchemes/apiKey"  # type: ignore[union-attr]
    assert components.security_schemes["bearer"].extensions == {  # type: ignore[union-attr]
        "x-scheme-reference": "shared"
    }


def test_media_type_example_examples_and_encoding_are_preserved() -> None:
    request_body = normalize_openapi_document(openapi_document()).api.components.request_bodies[
        "CreateItem"
    ]
    assert isinstance(request_body, RequestBody)
    example_media_type = request_body.content["application/json"]
    examples_media_type = request_body.content["application/xml"]

    assert isinstance(example_media_type, MediaType)
    assert example_media_type.example == {"id": 7, "name": "widget"}
    assert examples_media_type.example is None
    assert examples_media_type.examples["sample"] == {
        "summary": "Sample",
        "value": {"id": 7},
    }
    assert isinstance(examples_media_type.examples["shared"], LocalReference)
    assert examples_media_type.examples["shared"].uri == "#/components/examples/Sample"  # type: ignore[union-attr]
    assert examples_media_type.examples["shared"].extensions == {  # type: ignore[union-attr]
        "x-example-reference": "shared"
    }
    assert examples_media_type.encoding == {
        "name": {"contentType": "text/plain", "style": "form", "explode": True}
    }


def test_parameter_content_is_preserved_without_a_schema_field() -> None:
    parameter = normalize_openapi_document(openapi_document()).api.components.parameters[
        "FilterContent"
    ]

    assert isinstance(parameter, Parameter)
    assert parameter.schema is None
    assert parameter.content == {
        "application/json": MediaType(schema=Schema(raw={"type": "string"}))
    }


def test_oauth2_security_scheme_preserves_complete_flows() -> None:
    scheme = normalize_openapi_document(openapi_document()).api.components.security_schemes[
        "oauth"
    ]

    assert isinstance(scheme, SecurityScheme)
    assert scheme.type == "oauth2"
    assert scheme.flows == {
        "authorizationCode": {
            "authorizationUrl": "https://auth.example.test/authorize",
            "tokenUrl": "https://auth.example.test/token",
            "refreshUrl": "https://auth.example.test/refresh",
            "scopes": {"read:items": "Read items", "write:items": "Write items"},
        }
    }


def test_normalization_does_not_mutate_input_document() -> None:
    document = openapi_document()
    original = deepcopy(document)

    normalize_openapi_document(document)

    assert document == original


def test_reference_extensions_survive_api_model_json_round_trip() -> None:
    api = normalize_openapi_document(openapi_document()).api

    restored = type(api).model_validate_json(api.model_dump_json())

    parameter_ref = restored.components.parameters["SharedId"]
    example_ref = restored.components.request_bodies["CreateItem"].content[  # type: ignore[union-attr]
        "application/xml"
    ].examples["shared"]  # type: ignore[union-attr]
    scheme_ref = restored.components.security_schemes["bearer"]
    assert isinstance(parameter_ref, ParameterReference)
    assert parameter_ref.reference.extensions == {"x-reference-source": "component-library"}
    assert isinstance(example_ref, LocalReference)
    assert example_ref.extensions == {"x-example-reference": "shared"}
    assert isinstance(scheme_ref, LocalReference)
    assert scheme_ref.extensions == {"x-scheme-reference": "shared"}


def test_opaque_component_categories_preserve_objects_and_reference_edges() -> None:
    other = normalize_openapi_document(openapi_document()).api.components.other

    assert set(other) == {"examples", "headers", "links", "callbacks", "pathItems"}
    assert other["examples"]["Sample"] == {
        "summary": "Sample item",
        "value": {"id": 7},
    }
    assert isinstance(other["examples"]["SharedExample"], LocalReference)
    assert other["examples"]["SharedExample"].uri == "#/components/examples/Sample"  # type: ignore[union-attr]
    assert other["headers"]["RateLimit"]["schema"]["type"] == "integer"  # type: ignore[index]
    assert other["links"]["ItemLink"]["operationId"] == "getItem"  # type: ignore[index]
    assert "{$request.body#/callbackUrl}" in other["callbacks"]["Event"]  # type: ignore[operator]
    assert other["pathItems"]["SharedPath"]["get"]["responses"]["200"]["description"] == "OK"  # type: ignore[index]


def test_component_registries_have_deterministic_key_order() -> None:
    first = openapi_document()
    second = deepcopy(first)
    first_components = first["components"]
    second_components = second["components"]
    assert isinstance(first_components, dict) and isinstance(second_components, dict)
    first_components["schemas"] = {"Z": {"type": "string"}, "A": {"type": "integer"}}
    second_components["schemas"] = {"A": {"type": "integer"}, "Z": {"type": "string"}}

    normalized_first = normalize_openapi_document(first).api
    normalized_second = normalize_openapi_document(second).api

    assert normalized_first == normalized_second
    assert list(normalized_first.components.schemas) == ["A", "Z"]


def test_extracts_multiple_paths_and_all_standard_http_methods_deterministically() -> None:
    document = openapi_document()
    document["paths"] = {
        "/z": {"trace": operation(), "get": operation()},
        "/a": {
            "post": operation(),
            "patch": operation(),
            "delete": operation(),
            "put": operation(),
            "options": operation(),
            "head": operation(),
        },
    }

    endpoints = normalize_openapi_document(document).api.endpoints

    assert [(endpoint.path, endpoint.method) for endpoint in endpoints] == [
        ("/a", "post"),
        ("/a", "put"),
        ("/a", "patch"),
        ("/a", "delete"),
        ("/a", "options"),
        ("/a", "head"),
        ("/z", "get"),
        ("/z", "trace"),
    ]


def test_path_item_metadata_is_kept_separate_from_operation_metadata() -> None:
    document = openapi_document()
    document["paths"] = {
        "/metadata": {
            "summary": "Path summary",
            "description": "Path description",
            "servers": [{"url": "https://path.example.test"}],
            "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
            "get": operation(
                operationId="readMetadata",
                summary="Operation summary",
                description="Operation description",
                tags=["items", "metadata"],
                deprecated=True,
                **{"x-operation": {"flag": True}},
            ),
        }
    }

    endpoint = normalize_openapi_document(document).api.endpoints[0]

    assert endpoint.path == "/metadata"
    assert endpoint.method == "get"
    assert endpoint.operation_id == "readMetadata"
    assert endpoint.summary == "Operation summary"
    assert endpoint.description == "Operation description"
    assert endpoint.tags == ("items", "metadata")
    assert endpoint.deprecated is True
    assert endpoint.path_summary == "Path summary"
    assert endpoint.path_description == "Path description"
    assert endpoint.extensions == {"x-operation": {"flag": True}}
    assert "summary" not in endpoint.extensions


def test_path_and_operation_parameters_merge_by_name_and_location() -> None:
    document = openapi_document()
    document["components"]["parameters"]["ItemId"] = {  # type: ignore[index]
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    document["paths"] = {
        "/items/{id}": {
            "parameters": [
                {"$ref": "#/components/parameters/ItemId"},
                {"name": "tag", "in": "query", "schema": {"type": "string", "description": "path query"}},
                {"name": "tag", "in": "header", "schema": {"type": "string"}},
                {"name": "page", "in": "query", "schema": {"type": "integer"}},
            ],
            "get": operation(
                parameters=[
                    {"name": "id", "in": "path", "required": True, "description": "operation path", "schema": {"type": "string"}},
                    {"name": "tag", "in": "query", "description": "operation query", "schema": {"type": "string"}},
                    {"name": "tag", "in": "cookie", "schema": {"type": "string"}},
                ]
            ),
        }
    }

    result = normalize_openapi_document(document)
    endpoint = result.api.endpoints[0]

    assert [(parameter.name, parameter.location, parameter.source_scope) for parameter in endpoint.parameters if isinstance(parameter, Parameter)] == [
        ("id", "path", "operation"),
        ("tag", "query", "operation"),
        ("tag", "header", "path"),
        ("page", "query", "path"),
        ("tag", "cookie", "operation"),
    ]
    assert endpoint.parameters[0].description == "operation path"  # type: ignore[union-attr]
    assert endpoint.parameters[1].description == "operation query"  # type: ignore[union-attr]
    assert len(result.diagnostics) == 2
    assert [
        (diagnostic.parameter_name, diagnostic.parameter_location)
        for diagnostic in result.diagnostics
    ] == [("id", "path"), ("tag", "query")]
    assert all(diagnostic.original_source_scope == "path" for diagnostic in result.diagnostics)
    assert all(diagnostic.overriding_source_scope == "operation" for diagnostic in result.diagnostics)
    assert result.diagnostics[0].original_reference_uri == "#/components/parameters/ItemId"
    assert result.diagnostics[0].overriding_reference_uri is None


def test_parameter_reference_chain_is_retained_with_scope_and_diagnostic_provenance() -> None:
    document = openapi_document()
    document["components"]["parameters"].update(  # type: ignore[index]
        {
            "ItemId": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            "ItemIdAlias": {"$ref": "#/components/parameters/ItemId"},
            "ItemIdAlias2": {"$ref": "#/components/parameters/ItemIdAlias"},
            "Query": {"name": "q", "in": "query", "schema": {"type": "string"}},
        }
    )
    document["paths"] = {
        "/items/{id}": {
            "parameters": [{"$ref": "#/components/parameters/ItemIdAlias2"}],
            "get": operation(
                parameters=[{"$ref": "#/components/parameters/Query"}]
            ),
        }
    }

    endpoint = normalize_openapi_document(document).api.endpoints[0]

    assert len(endpoint.parameters) == 2
    assert isinstance(endpoint.parameters[0], ParameterReference)
    assert endpoint.parameters[0].reference.uri == "#/components/parameters/ItemIdAlias2"
    assert endpoint.parameters[0].source_scope == "path"
    assert isinstance(endpoint.parameters[1], ParameterReference)
    assert endpoint.parameters[1].reference.uri == "#/components/parameters/Query"
    assert endpoint.parameters[1].source_scope == "operation"


def test_parameter_override_diagnostic_records_reference_uris() -> None:
    document = openapi_document()
    document["components"]["parameters"]["ItemId"] = {  # type: ignore[index]
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    document["paths"] = {
        "/items/{id}": {
            "parameters": [{"$ref": "#/components/parameters/ItemId"}],
            "get": operation(parameters=[{
                "$ref": "#/components/parameters/ItemId",
                "description": "operation-level ref",
            }]),
        }
    }

    result = normalize_openapi_document(document)
    diagnostic = result.diagnostics[0]

    assert diagnostic.path == "/items/{id}"
    assert diagnostic.parameter_name == "id"
    assert diagnostic.parameter_location == "path"
    assert diagnostic.original_reference_uri == "#/components/parameters/ItemId"
    assert diagnostic.overriding_reference_uri == "#/components/parameters/ItemId"
    effective = result.api.endpoints[0].parameters[0]
    assert isinstance(effective, ParameterReference)
    assert effective.source_scope == "operation"


def test_multilevel_parameter_reference_identity_allows_operation_override() -> None:
    document = openapi_document()
    document["components"]["parameters"].update(  # type: ignore[index]
        {
            "ItemId": {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            },
            "ItemIdAlias": {"$ref": "#/components/parameters/ItemId"},
            "ItemIdAlias2": {"$ref": "#/components/parameters/ItemIdAlias"},
        }
    )
    document["paths"] = {
        "/items/{id}": {
            "parameters": [{"$ref": "#/components/parameters/ItemIdAlias2"}],
            "get": operation(
                parameters=[
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "description": "operation override",
                        "schema": {"type": "integer"},
                    }
                ]
            ),
        }
    }

    result = normalize_openapi_document(document)
    endpoint = result.api.endpoints[0]

    assert len(endpoint.parameters) == 1
    effective = endpoint.parameters[0]
    assert isinstance(effective, Parameter)
    assert effective.name == "id"
    assert effective.location == "path"
    assert effective.source_scope == "operation"
    assert effective.description == "operation override"
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert (diagnostic.parameter_name, diagnostic.parameter_location) == ("id", "path")
    assert diagnostic.original_reference_uri == "#/components/parameters/ItemIdAlias2"
    assert diagnostic.overriding_reference_uri is None


@pytest.mark.parametrize(
    ("component_name", "reference_uri", "location"),
    [
        ("Slash/Key", "#/components/parameters/Slash~1Key", "query"),
        ("Tilde~Key", "#/components/parameters/Tilde~0Key", "header"),
        (
            "Slash/Key",
            "#%2Fcomponents%2Fparameters%2FSlash%7E1Key",
            "query",
        ),
        (
            "Tilde~Key",
            "#%2Fcomponents%2Fparameters%2FTilde%7E0Key",
            "header",
        ),
    ],
)
def test_parameter_reference_identity_decodes_json_pointer_and_uri_escaping(
    component_name: str, reference_uri: str, location: str
) -> None:
    document = openapi_document()
    document["components"]["parameters"][component_name] = {  # type: ignore[index]
        "name": "shared",
        "in": location,
        **({"required": True} if location == "path" else {}),
        "schema": {"type": "string"},
    }
    document["paths"] = {
        "/encoded": {
            "parameters": [{"$ref": reference_uri}],
            "get": operation(
                parameters=[
                    {
                        "name": "shared",
                        "in": location,
                        **({"required": True} if location == "path" else {}),
                        "description": "operation override",
                        "schema": {"type": "integer"},
                    }
                ]
            ),
        }
    }

    result = normalize_openapi_document(document)
    endpoint = result.api.endpoints[0]

    assert len(endpoint.parameters) == 1
    effective = endpoint.parameters[0]
    assert isinstance(effective, Parameter)
    assert (effective.name, effective.location) == ("shared", location)
    assert effective.source_scope == "operation"
    assert result.diagnostics[0].original_reference_uri == reference_uri


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        (
            {
                "A": {"$ref": "#/components/parameters/B"},
                "B": {"$ref": "#/components/parameters/A"},
            },
            "Cyclic parameter reference chain",
        ),
        (
            {},
            "has no component target",
        ),
        (
            {"User": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}},
            "Cannot determine parameter identity from local reference",
        ),
    ],
)
def test_invalid_parameter_reference_chains_raise_normalization_error(
    parameters: dict[str, object], message: str
) -> None:
    document = openapi_document()
    document["components"]["parameters"].update(parameters)  # type: ignore[index]
    reference_uri = {
        "Cyclic parameter reference chain": "#/components/parameters/A",
        "has no component target": "#/components/parameters/Missing",
        "Cannot determine parameter identity from local reference": "#/components/schemas/User",
    }[message]
    document["paths"] = {
        "/invalid": {
            "parameters": [{"$ref": reference_uri}],
            "get": operation(),
        }
    }

    with pytest.raises(OpenAPINormalizationError, match=message):
        normalize_openapi_document(document)


@pytest.mark.parametrize(
    ("scope", "parameters", "message"),
    [
        (
            "Path Item",
            [
                {"name": "filter", "in": "query", "schema": {"type": "string"}},
                {"name": "filter", "in": "query", "schema": {"type": "integer"}},
            ],
            "Duplicate Path Item parameter",
        ),
        (
            "Operation",
            [
                {"name": "filter", "in": "query", "schema": {"type": "string"}},
                {"name": "filter", "in": "query", "schema": {"type": "integer"}},
            ],
            "Duplicate Operation parameter",
        ),
    ],
)
def test_duplicate_parameter_identities_are_rejected_within_each_scope(
    scope: str, parameters: list[dict[str, object]], message: str
) -> None:
    document = openapi_document()
    path_item: dict[str, object] = {"get": operation()}
    if scope == "Path Item":
        path_item["parameters"] = parameters
    else:
        path_item["get"] = operation(parameters=parameters)
    document["paths"] = {"/duplicates": path_item}

    with pytest.raises(OpenAPINormalizationError, match=message):
        normalize_openapi_document(document)


def test_request_body_response_and_status_key_normalization() -> None:
    document = openapi_document()
    document["paths"] = {
        "/create": {
            "post": operation(
                requestBody={
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                responses={
                    "200": {"description": "OK"},
                    "2XX": {"$ref": "#/components/responses/Success"},
                    "default": {"description": "Unexpected"},
                },
            )
        },
        "/read": {"get": operation()},
    }

    endpoints = normalize_openapi_document(document).api.endpoints
    create = next(endpoint for endpoint in endpoints if endpoint.path == "/create")
    read = next(endpoint for endpoint in endpoints if endpoint.path == "/read")

    assert isinstance(create.request_body, RequestBody)
    assert create.request_body.required is True
    assert isinstance(create.responses["200"], Response)
    assert isinstance(create.responses["2XX"], LocalReference)
    assert isinstance(create.responses["default"], Response)
    assert set(create.responses) == {"200", "2XX", "default"}
    assert read.request_body is None


def test_operation_security_preserves_inherit_empty_and_anonymous_states() -> None:
    document = openapi_document()
    document["paths"] = {
        "/inherit": {"get": operation()},
        "/disable": {"get": operation(security=[])},
        "/anonymous": {"get": operation(security=[{}])},
    }

    endpoints = {endpoint.path: endpoint for endpoint in normalize_openapi_document(document).api.endpoints}

    assert endpoints["/inherit"].security is None
    assert endpoints["/disable"].security == ()
    assert endpoints["/anonymous"].security == (SecurityRequirement(schemes={}),)


def test_path_and_operation_servers_remain_separate_and_preserve_empty_values() -> None:
    document = openapi_document()
    document["paths"] = {
        "/path-empty": {
            "servers": [],
            "get": operation(),
        },
        "/operation-empty": {
            "get": operation(servers=[]),
        },
        "/both": {
            "servers": [{"url": "https://path.example.test"}],
            "get": operation(servers=[{"url": "https://operation.example.test"}]),
        },
    }

    endpoints = {endpoint.path: endpoint for endpoint in normalize_openapi_document(document).api.endpoints}

    assert endpoints["/path-empty"].path_servers == ()
    assert endpoints["/path-empty"].operation_servers is None
    assert endpoints["/operation-empty"].path_servers is None
    assert endpoints["/operation-empty"].operation_servers == ()
    assert endpoints["/both"].path_servers == (Server(url="https://path.example.test"),)
    assert endpoints["/both"].operation_servers == (Server(url="https://operation.example.test"),)


def test_rejects_path_item_references_with_clear_error_code_and_pointer() -> None:
    document = openapi_document()
    document["paths"] = {"/items/{id}": {"$ref": "#/components/pathItems/Item"}}

    with pytest.raises(
        OpenAPINormalizationError,
        match=r"unsupported_path_item_reference at /paths/~1items~1\{id\}/\$ref",
    ):
        normalize_openapi_document(document)


@pytest.mark.parametrize(
    ("path_item", "message"),
    [
        (None, "must be an object"),
        ({"unknown": "field"}, "Malformed Path Item"),
        ({"get": []}, "must be an object"),
        ({"get": {"summary": "missing responses"}}, "missing required field 'responses'"),
        ({"get": operation(responses={})}, "Invalid operation GET /invalid"),
        ({"get": operation(tags="not-an-array")}, "tags must be an array"),
    ],
)
def test_rejects_malformed_path_items_and_operations(
    path_item: object, message: str
) -> None:
    document = openapi_document()
    document["paths"] = {"/invalid": path_item}

    with pytest.raises(OpenAPINormalizationError, match=message):
        normalize_openapi_document(document)


def test_endpoint_extraction_is_deterministic_and_does_not_mutate_input() -> None:
    document = openapi_document()
    document["paths"] = {
        "/z": {"post": operation(), "get": operation()},
        "/a": {"trace": operation(), "delete": operation()},
    }
    original = deepcopy(document)

    first = normalize_openapi_document(document)
    second = normalize_openapi_document(deepcopy(document))

    assert first == second
    assert [(endpoint.path, endpoint.method) for endpoint in first.api.endpoints] == [
        ("/a", "delete"),
        ("/a", "trace"),
        ("/z", "get"),
        ("/z", "post"),
    ]
    assert document == original


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"openapi": "3.1.1"},
        {"openapi": "3.1.1", "info": {"title": "Missing version"}},
        {"openapi": "3.1.1", "info": {"title": "Bad", "version": "1"}, "servers": "not-list"},
        {"openapi": "3.1.1", "info": {"title": "Bad", "version": "1"}, "security": [{"oauth": "read"}]},
        {"openapi": "3.1.1", "info": {"title": "Bad", "version": "1"}, "components": {"unsupported": {}}},
        {
            "openapi": "3.1.1",
            "info": {"title": "Bad", "version": "1"},
            "components": {"requestBodies": {"Bad": {"description": "missing content"}}},
        },
        {
            "openapi": "3.1.1",
            "info": {"title": "Bad", "version": "1"},
            "components": {"responses": {"Bad": {"$ref": "https://example.test/response"}}},
        },
        {
            "openapi": "3.1.1",
            "info": {"title": "Bad", "version": "1"},
            "components": {
                "responses": {
                    "Bad": {
                        "$ref": "#/components/responses/Ok",
                        "unsupported": "field",
                    }
                }
            },
        },
    ],
)
def test_invalid_or_unrepresentable_input_raises_normalization_error(
    document: dict[str, object],
) -> None:
    with pytest.raises(OpenAPINormalizationError):
        normalize_openapi_document(document)


def test_normalization_error_is_exported_and_distinct_from_validation_errors() -> None:
    from app.services.openapi.exceptions import OpenAPIValidationError

    assert OpenAPINormalizationError is not OpenAPIValidationError
