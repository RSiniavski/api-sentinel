"""Tests for the public API Model contract."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
    Components,
    Endpoint,
    JsonValue,
    LocalReference,
    MediaType,
    NormalizationResult,
    Parameter,
    ParameterEntry,
    ParameterOverrideDiagnostic,
    ParameterReference,
    RequestBody,
    RequestBodyEntry,
    Response,
    ResponseEntry,
    Schema,
    SecurityRequirement,
    SecurityScheme,
    SecuritySchemeEntry,
    Server,
)


def valid_endpoint(**overrides: object) -> Endpoint:
    """Create the smallest valid endpoint for model tests."""
    values: dict[str, object] = {
        "path": "/items",
        "method": "get",
        "responses": {"200": Response(description="OK")},
    }
    values.update(overrides)
    return Endpoint(**values)


def test_constructs_principal_models_and_component_registries() -> None:
    parameter = Parameter(
        name="item_id",
        location="path",
        required=True,
        schema=Schema(raw={"type": "integer"}),
        source_scope="component",
    )
    parameter_ref = ParameterReference(
        reference=LocalReference(uri="#/components/parameters/ItemId"),
        source_scope="path",
    )
    api = ApiModel(
        openapi_version="3.1.1",
        info=ApiInfo(title="Inventory", version="1"),
        servers=(Server(url="https://api.example.test"),),
        security=(SecurityRequirement(schemes={"bearer": ()}),),
        components=Components(
            schemas={"Item": Schema(raw={"type": "object"})},
            parameters={"ItemId": parameter},
            request_bodies={
                "ItemInput": RequestBody(
                    content={"application/json": MediaType(schema=Schema(raw=True))}
                )
            },
            responses={"NotFound": Response(description="Not found")},
            security_schemes={"bearer": SecurityScheme(type="http", scheme="bearer")},
            other={"examples": {"sample": {"value": {"name": "widget"}}}},
        ),
        endpoints=(valid_endpoint(parameters=(parameter_ref,)),),
    )

    assert api.endpoints[0].parameters == (parameter_ref,)
    assert api.components.parameters["ItemId"] == parameter
    assert api.components.schemas["Item"].raw == {"type": "object"}
    assert api.components.request_bodies["ItemInput"].content["application/json"].schema.raw is True
    assert api.components.responses["NotFound"].description == "Not found"
    assert api.components.security_schemes["bearer"].scheme == "bearer"
    assert api.servers[0].url == "https://api.example.test"


def test_common_model_configuration_is_strict_frozen_and_forbids_extras() -> None:
    assert issubclass(ApiModel, BaseModel)
    assert ApiModel.model_config["strict"] is True
    assert ApiModel.model_config["extra"] == "forbid"
    assert ApiModel.model_config["frozen"] is True

    with pytest.raises(ValidationError):
        Server(url=123)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Server(url="/", surprise="value")  # type: ignore[call-arg]

    server = Server(url="/")
    with pytest.raises(ValidationError, match="frozen_instance"):
        server.url = "/changed"  # type: ignore[misc]


def test_schema_detaches_input_and_copy_raw_is_independent() -> None:
    source = {"properties": {"item": {"type": "string"}}, "required": ["item"]}
    schema = Schema(raw=source)
    source["properties"]["item"]["type"] = "integer"
    source["required"].append("other")

    assert schema.raw == {
        "properties": {"item": {"type": "string"}},
        "required": ["item"],
    }
    copied = schema.copy_raw()
    assert copied == schema.raw
    copied["properties"]["item"]["type"] = "boolean"  # type: ignore[index]
    assert schema.raw["properties"]["item"]["type"] == "string"  # type: ignore[index]


def test_schema_preserves_refs_compositions_dialects_and_unknown_keywords() -> None:
    raw = {
        "$ref": "#/components/schemas/Base",
        "description": "Sibling annotation",
        "maxLength": 40,
        "allOf": [{"type": "object"}],
        "oneOf": [{"const": "a"}, {"const": "b"}],
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "not": {"required": ["forbidden"]},
        "nullable": True,
        "type": ["string", "null"],
        "x-vendor": {"enabled": True},
        "customKeyword": [1, "preserved"],
    }
    schema = Schema(raw=raw)

    assert schema.raw == raw
    assert schema.reference == LocalReference(
        uri="#/components/schemas/Base", description="Sibling annotation"
    )
    assert schema.reference.uri == "#/components/schemas/Base"
    assert schema.raw["allOf"] == raw["allOf"]
    assert schema.raw["oneOf"] == raw["oneOf"]
    assert schema.raw["anyOf"] == raw["anyOf"]
    assert schema.raw["not"] == raw["not"]
    assert schema.raw["nullable"] is True
    assert schema.raw["type"] == ["string", "null"]
    assert schema.raw["customKeyword"] == [1, "preserved"]

    assert Schema(raw=True).raw is True
    assert Schema(raw=False).raw is False
    assert Schema(raw={"type": "string", "nullable": True}).raw["nullable"] is True
    assert Schema(raw={"type": ["string", "null"]}).raw["type"] == ["string", "null"]
    assert Schema(raw={"x-example": {"nested": [None, 2.5]}}).raw == {
        "x-example": {"nested": [None, 2.5]}
    }
    assert Schema(raw={"type": "string"}).reference is None


def test_generic_reference_has_no_parameter_scope() -> None:
    reference = LocalReference(uri="#/components/responses/NotFound")

    assert reference.uri == "#/components/responses/NotFound"
    assert "source_scope" not in LocalReference.model_fields
    with pytest.raises(ValidationError, match="extra_forbidden"):
        LocalReference(uri="#/components/parameters/Id", source_scope="path")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "uri",
    [
        "#/components/schemas/User",
        "#/components/schemas/A~1B",
        "#UserSchema",
        "#",
        "#/components/schemas/%E2%9C%93",
    ],
)
def test_local_reference_accepts_valid_local_uri_fragments(uri: str) -> None:
    assert LocalReference(uri=uri).uri == uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/schema.json",
        "schemas/common.yaml",
        "/schemas/common.yaml",
        "components/schemas/User",
        "#bad%ZZ",
        "#bad%",
        "#%FF",
        "#/components/schemas/bad~2escape",
        "#9InvalidAnchor",
        "#bad anchor",
        "#bad[character]",
        "#caf\u00e9",
    ],
)
def test_local_reference_rejects_external_or_malformed_uri_fragments(uri: str) -> None:
    with pytest.raises(ValidationError, match="valid local URI fragment reference"):
        LocalReference(uri=uri)


@pytest.mark.parametrize("scope", ["path", "operation", "component"])
def test_parameter_reference_preserves_each_allowed_scope(scope: str) -> None:
    reference = ParameterReference(
        reference=LocalReference(uri="#/components/parameters/Id"),
        source_scope=scope,  # type: ignore[arg-type]
    )

    assert reference.reference.uri == "#/components/parameters/Id"
    assert reference.source_scope == scope


def test_parameter_and_other_reference_unions_validate_expected_variants() -> None:
    parameter_adapter = TypeAdapter(ParameterEntry)
    request_body_adapter = TypeAdapter(RequestBodyEntry)
    response_adapter = TypeAdapter(ResponseEntry)
    security_scheme_adapter = TypeAdapter(SecuritySchemeEntry)

    parameter = Parameter(
        name="q", location="query", source_scope="operation"
    )
    parameter_ref = ParameterReference(
        reference=LocalReference(uri="#/components/parameters/Q"),
        source_scope="operation",
    )
    assert isinstance(parameter_adapter.validate_python(parameter), Parameter)
    assert isinstance(parameter_adapter.validate_python(parameter_ref), ParameterReference)
    assert isinstance(
        request_body_adapter.validate_python(LocalReference(uri="#/components/requestBodies/Input")),
        LocalReference,
    )
    assert isinstance(
        response_adapter.validate_python(LocalReference(uri="#/components/responses/Ok")),
        LocalReference,
    )
    assert isinstance(
        security_scheme_adapter.validate_python(LocalReference(uri="#/components/securitySchemes/Key")),
        LocalReference,
    )
    assert isinstance(request_body_adapter.validate_python(RequestBody(content={})), RequestBody)


def test_components_other_preserves_json_objects_and_local_references_round_trip() -> None:
    components = Components(
        other={
            "examples": {
                "inline": {"summary": "Example", "value": {"name": "widget"}},
                "shared": LocalReference(
                    uri="#/components/examples/SharedExample",
                    extensions={"x-reference-source": "catalog"},
                ),
                "uri_metadata": {
                    "uri": "#/components/examples/NotAReference",
                    "kind": "opaque metadata",
                },
                "uri_and_extensions_metadata": {
                    "uri": "#/components/examples/AlsoNotAReference",
                    "extensions": {"x-label": "plain JSON"},
                    "kind": "ordinary component object",
                },
            },
            "headers": {
                "X-Rate-Limit": {"description": "Requests remaining", "schema": {"type": "integer"}}
            },
            "links": {
                "GetOwner": {"operationId": "getOwner", "parameters": {"ownerId": "$response.body#/ownerId"}}
            },
            "callbacks": {
                "onEvent": {
                    "{$request.body#/callbackUrl}": {
                        "post": {"requestBody": {"description": "Event payload"}}
                    }
                }
            },
            "pathItems": {
                "SharedPath": {
                    "get": {"responses": {"200": {"description": "OK"}}}
                }
            },
        }
    )

    assert components.other["examples"]["inline"] == {
        "summary": "Example",
        "value": {"name": "widget"},
    }
    assert isinstance(components.other["examples"]["shared"], LocalReference)
    assert components.other["examples"]["shared"].uri == "#/components/examples/SharedExample"  # type: ignore[union-attr]
    assert components.other["examples"]["shared"].extensions == {"x-reference-source": "catalog"}  # type: ignore[union-attr]
    assert components.other["examples"]["uri_metadata"] == {
        "uri": "#/components/examples/NotAReference",
        "kind": "opaque metadata",
    }
    assert components.other["examples"]["uri_and_extensions_metadata"] == {
        "uri": "#/components/examples/AlsoNotAReference",
        "extensions": {"x-label": "plain JSON"},
        "kind": "ordinary component object",
    }
    assert components.other["headers"]["X-Rate-Limit"]["schema"]["type"] == "integer"  # type: ignore[index]
    assert components.other["links"]["GetOwner"]["operationId"] == "getOwner"  # type: ignore[index]
    assert "{$request.body#/callbackUrl}" in components.other["callbacks"]["onEvent"]  # type: ignore[operator]
    assert components.other["pathItems"]["SharedPath"]["get"]["responses"]["200"]["description"] == "OK"  # type: ignore[index]

    restored = Components.model_validate(components.model_dump())

    assert restored == components
    assert isinstance(restored.other["examples"]["shared"], LocalReference)
    assert restored.other["examples"]["shared"].uri == "#/components/examples/SharedExample"  # type: ignore[union-attr]
    assert restored.other["examples"]["shared"].extensions == {"x-reference-source": "catalog"}  # type: ignore[union-attr]
    assert restored.other["examples"]["uri_metadata"] == {
        "uri": "#/components/examples/NotAReference",
        "kind": "opaque metadata",
    }
    assert restored.other["examples"]["uri_and_extensions_metadata"] == {
        "uri": "#/components/examples/AlsoNotAReference",
        "extensions": {"x-label": "plain JSON"},
        "kind": "ordinary component object",
    }


def test_components_other_restores_legacy_reference_serialization_without_extensions() -> None:
    restored = Components.model_validate(
        {
            "other": {
                "examples": {
                    "legacy": {
                        "uri": "#/components/examples/Legacy",
                        "summary": None,
                        "description": None,
                    }
                }
            }
        }
    )

    reference = restored.other["examples"]["legacy"]
    assert isinstance(reference, LocalReference)
    assert reference.uri == "#/components/examples/Legacy"
    assert reference.extensions == {}


def test_local_reference_extensions_serialize_and_validate_from_json() -> None:
    reference = LocalReference(
        uri="#/components/responses/NotFound",
        summary="Reusable response",
        description="Shared response metadata",
        extensions={"x-owner": "platform", "x-flags": ["stable", 2]},
    )

    serialized = reference.model_dump_json()
    restored = LocalReference.model_validate_json(serialized)

    assert restored == reference
    assert json.loads(serialized)["extensions"] == {
        "x-owner": "platform",
        "x-flags": ["stable", 2],
    }


def test_components_other_rejects_invalid_data_in_local_reference_shape() -> None:
    invalid_reference_data = {
        "uri": "https://example.com/shared-example",
        "summary": None,
        "description": None,
    }

    with pytest.raises(ValidationError, match="valid local URI fragment reference"):
        Components.model_validate(
            {"other": {"examples": {"shared": invalid_reference_data}}}
        )


def test_components_other_rejects_unsupported_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Components(other={}, unsupported="value")  # type: ignore[call-arg]


def test_local_parameter_invariants_are_enforced() -> None:
    with pytest.raises(ValidationError, match="Path parameters must be required"):
        Parameter(name="id", location="path", source_scope="path")
    with pytest.raises(ValidationError, match="both schema and content"):
        Parameter(
            name="q",
            location="query",
            schema=Schema(raw={"type": "string"}),
            content={"text/plain": MediaType()},
            source_scope="operation",
        )
    with pytest.raises(ValidationError, match="at least one response"):
        valid_endpoint(responses={})


def test_none_empty_and_anonymous_security_states_remain_distinct() -> None:
    absent = ApiModel(openapi_version="3.0.3", info=ApiInfo(title="A", version="1"))
    empty = ApiModel(
        openapi_version="3.0.3",
        info=ApiInfo(title="A", version="1"),
        security=(),
        servers=(),
    )
    anonymous = ApiModel(
        openapi_version="3.0.3",
        info=ApiInfo(title="A", version="1"),
        security=(SecurityRequirement(schemes={}),),
    )
    operation_inherit = valid_endpoint(security=None)
    operation_disable = valid_endpoint(security=())

    assert absent.security is None and absent.servers is None
    assert empty.security == () and empty.servers == ()
    assert anonymous.security == (SecurityRequirement(schemes={}),)
    assert operation_inherit.security is None
    assert operation_disable.security == ()


def test_security_requirements_preserve_alternatives_schemes_and_oauth_scopes() -> None:
    api_alternatives = (
        SecurityRequirement(
            schemes={"tenantKey": (), "oauth": ("read:items", "write:items")}
        ),
        SecurityRequirement(schemes={"bearerAuth": ()}),
    )
    operation_alternatives = (
        SecurityRequirement(schemes={"mutualTls": (), "oauth": ("admin",)}),
        SecurityRequirement(schemes={}),
    )
    api = ApiModel(
        openapi_version="3.1.1",
        info=ApiInfo(title="Security cases", version="1"),
        security=api_alternatives,
        endpoints=(valid_endpoint(security=operation_alternatives),),
    )

    assert api.security == api_alternatives
    assert api.security[0].schemes == {
        "tenantKey": (),
        "oauth": ("read:items", "write:items"),
    }
    assert api.security[0].schemes["oauth"] == ("read:items", "write:items")
    assert api.security[1] == SecurityRequirement(schemes={"bearerAuth": ()})
    assert api.endpoints[0].security == operation_alternatives


def test_security_none_empty_and_anonymous_states_survive_json_round_trip() -> None:
    api_states = (
        None,
        (),
        (SecurityRequirement(schemes={}),),
    )
    endpoint_states = (
        None,
        (),
        (SecurityRequirement(schemes={}),),
    )

    for api_security in api_states:
        for endpoint_security in endpoint_states:
            api = ApiModel(
                openapi_version="3.1.1",
                info=ApiInfo(title="Security states", version="1"),
                security=api_security,
                endpoints=(valid_endpoint(security=endpoint_security),),
            )

            restored = ApiModel.model_validate_json(api.model_dump_json())

            assert restored.security == api_security
            assert restored.endpoints[0].security == endpoint_security
    assert api_states[0] is None and api_states[1] == ()
    assert api_states[2] == (SecurityRequirement(schemes={}),)


@pytest.mark.parametrize(
    "scheme",
    [
        SecurityScheme(
            type="apiKey",
            name="X-API-Key",
            location="header",
            description="Tenant API key",
            extensions={"x-provider": "gateway"},
        ),
        SecurityScheme(
            type="http",
            scheme="bearer",
            bearer_format="JWT",
            description="Bearer authentication",
            extensions={"x-provider": {"name": "identity"}},
        ),
        SecurityScheme(
            type="oauth2",
            flows={
                "authorizationCode": {
                    "authorizationUrl": "https://auth.example.test/authorize",
                    "tokenUrl": "https://auth.example.test/token",
                    "refreshUrl": "https://auth.example.test/refresh",
                    "scopes": {
                        "read:items": "Read items",
                        "write:items": "Write items",
                    },
                }
            },
            description="OAuth authorization code flow",
            extensions={"x-provider": "oauth"},
        ),
        SecurityScheme(
            type="openIdConnect",
            openid_connect_url="https://identity.example.test/.well-known/openid-configuration",
            description="OpenID Connect",
            extensions={"x-provider": "oidc"},
        ),
        SecurityScheme(
            type="mutualTLS",
            description="Client certificate authentication",
            extensions={"x-provider": "mtls"},
        ),
    ],
)
def test_security_scheme_types_and_metadata_survive_json_round_trip(
    scheme: SecurityScheme,
) -> None:
    restored = SecurityScheme.model_validate_json(scheme.model_dump_json())

    assert restored == scheme
    assert restored.type == scheme.type
    assert restored.description == scheme.description
    assert restored.extensions == scheme.extensions

    if scheme.type == "apiKey":
        assert restored.name == "X-API-Key"
        assert restored.location == "header"
    elif scheme.type == "http":
        assert restored.scheme == "bearer"
        assert restored.bearer_format == "JWT"
    elif scheme.type == "oauth2":
        assert restored.flows == scheme.flows
        assert restored.flows["authorizationCode"]["authorizationUrl"] == "https://auth.example.test/authorize"  # type: ignore[index]
        assert restored.flows["authorizationCode"]["tokenUrl"] == "https://auth.example.test/token"  # type: ignore[index]
        assert restored.flows["authorizationCode"]["refreshUrl"] == "https://auth.example.test/refresh"  # type: ignore[index]
        assert restored.flows["authorizationCode"]["scopes"] == {
            "read:items": "Read items",
            "write:items": "Write items",
        }  # type: ignore[index]
    elif scheme.type == "openIdConnect":
        assert restored.openid_connect_url == scheme.openid_connect_url
    else:
        assert scheme.type == "mutualTLS"


@pytest.mark.parametrize("credential_field", ["token", "password", "access_token"])
def test_security_scheme_rejects_credential_fields(credential_field: str) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SecurityScheme(type="http", **{credential_field: "secret"})


def test_security_models_keep_strict_extra_forbid_configuration() -> None:
    for security_model in (SecurityRequirement, SecurityScheme):
        assert security_model.model_config["strict"] is True
        assert security_model.model_config["extra"] == "forbid"
        assert security_model.model_config["frozen"] is True


def test_json_serialization_and_deserialization_preserve_representative_model() -> None:
    api = ApiModel(
        openapi_version="3.1.1",
        info=ApiInfo(title="Example", version="1"),
        security=(SecurityRequirement(schemes={"oauth": ("read",)}),),
        components=Components(
            schemas={"Node": Schema(raw={"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}})}
        ),
        endpoints=(valid_endpoint(parameters=(
            ParameterReference(
                reference=LocalReference(uri="#/components/parameters/Id", summary="id"),
                source_scope="operation",
            ),
        )),),
    )

    encoded = api.model_dump_json()
    decoded = ApiModel.model_validate_json(encoded)
    assert decoded == api
    assert json.loads(encoded)["components"]["schemas"]["Node"]["raw"]["properties"]["next"]["$ref"] == "#/components/schemas/Node"
    assert decoded.endpoints[0].parameters[0].source_scope == "operation"  # type: ignore[union-attr]
    assert TypeAdapter(JsonValue).validate_json('{"nested":[null,true,3,"x"]}') == {
        "nested": [None, True, 3, "x"]
    }


def test_normalization_result_and_parameter_override_diagnostic() -> None:
    diagnostic = ParameterOverrideDiagnostic(
        path="/items/{item_id}",
        parameter_name="item_id",
        parameter_location="path",
        original_source_scope="path",
        overriding_source_scope="operation",
        original_reference_uri="#/components/parameters/ItemId",
    )
    result = NormalizationResult(
        api=ApiModel(
            openapi_version="3.1.1",
            info=ApiInfo(title="Items", version="1"),
            endpoints=(valid_endpoint(),),
        ),
        diagnostics=(diagnostic,),
    )
    restored = NormalizationResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.diagnostics[0].parameter_name == "item_id"
    assert restored.diagnostics[0].original_source_scope == "path"
    assert restored.diagnostics[0].overriding_source_scope == "operation"
    assert restored.diagnostics[0].original_reference_uri == "#/components/parameters/ItemId"
