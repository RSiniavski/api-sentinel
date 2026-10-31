"""Transform validated OpenAPI dictionaries into the internal API model."""

from __future__ import annotations

from urllib.parse import unquote
from typing import Any

from pydantic import ValidationError

from app.services.openapi.exceptions import OpenAPINormalizationError
from app.services.openapi.models import (
    ApiInfo,
    ApiModel,
    Components,
    Endpoint,
    LocalReference,
    MediaType,
    NormalizationResult,
    Parameter,
    ParameterEntry,
    ParameterOverrideDiagnostic,
    ParameterReference,
    RequestBody,
    Response,
    Schema,
    SecurityRequirement,
    SecurityScheme,
    Server,
)


_TYPED_COMPONENTS = {
    "schemas",
    "parameters",
    "requestBodies",
    "responses",
    "securitySchemes",
}
_OPAQUE_COMPONENTS = {"examples", "headers", "links", "callbacks", "pathItems"}
_REFERENCE_FIELDS = {"$ref", "summary", "description"}
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
_PATH_ITEM_FIELDS = {"summary", "description", "servers", "parameters"}
_OPERATION_FIELDS = {
    "tags",
    "summary",
    "description",
    "externalDocs",
    "operationId",
    "parameters",
    "requestBody",
    "responses",
    "callbacks",
    "deprecated",
    "security",
    "servers",
}


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise OpenAPINormalizationError(f"{context} must be an object with string keys.")
    return value


def _required(source: dict[str, Any], key: str, context: str) -> Any:
    if key not in source:
        raise OpenAPINormalizationError(f"{context} is missing required field '{key}'.")
    return source[key]


def _extensions(source: dict[str, Any]) -> dict[str, Any]:
    return {key: source[key] for key in sorted(source) if key.startswith("x-")}


def _mapping(source: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    return _object(source.get(key, {}), f"{context}.{key}")


def _reference(value: Any, context: str) -> LocalReference:
    source = _object(value, context)
    if "$ref" not in source:
        raise OpenAPINormalizationError(f"{context} is not a reference object.")
    unsupported_fields = {
        key for key in source if key not in _REFERENCE_FIELDS and not key.startswith("x-")
    }
    if unsupported_fields:
        raise OpenAPINormalizationError(
            f"{context} contains reference fields that the API Model cannot preserve."
        )
    try:
        return LocalReference(
            uri=source["$ref"],
            summary=source.get("summary"),
            description=source.get("description"),
            extensions=_extensions(source),
        )
    except ValidationError as exc:
        raise OpenAPINormalizationError(f"Invalid local reference in {context}.") from exc


def _is_reference(value: Any) -> bool:
    return isinstance(value, dict) and "$ref" in value


def _media_type(value: Any, context: str) -> MediaType:
    source = _object(value, context)
    examples: dict[str, Any] = {}
    for name, example in _mapping(source, "examples", context).items():
        examples[name] = _reference(example, f"{context}.examples.{name}") if _is_reference(example) else example
    return MediaType(
        schema=Schema(raw=source["schema"]) if "schema" in source else None,
        example=source.get("example"),
        examples=dict(sorted(examples.items())),
        encoding=dict(sorted(_object(source.get("encoding", {}), f"{context}.encoding").items())),
        extensions=_extensions(source),
    )


def _media_content(value: Any, context: str) -> dict[str, MediaType]:
    content = _object(value, context)
    return {
        name: _media_type(content[name], f"{context}.{name}")
        for name in sorted(content)
    }


def _parameter(value: Any, context: str, scope: str) -> Parameter | ParameterReference:
    source = _object(value, context)
    if _is_reference(source):
        return ParameterReference(
            reference=_reference(source, context), source_scope=scope  # type: ignore[arg-type]
        )
    examples: dict[str, Any] = {}
    for name, example in _mapping(source, "examples", context).items():
        examples[name] = _reference(example, f"{context}.examples.{name}") if _is_reference(example) else example
    content = source.get("content")
    return Parameter(
        name=_required(source, "name", context),
        location=_required(source, "in", context),
        required=source.get("required", False),
        description=source.get("description"),
        deprecated=source.get("deprecated", False),
        allow_empty_value=source.get("allowEmptyValue"),
        style=source.get("style"),
        explode=source.get("explode"),
        allow_reserved=source.get("allowReserved"),
        schema=Schema(raw=source["schema"]) if "schema" in source else None,
        content=_media_content(content, f"{context}.content") if content is not None else None,
        example=source.get("example"),
        examples=dict(sorted(examples.items())),
        source_scope=scope,  # type: ignore[arg-type]
        extensions=_extensions(source),
    )


def _request_body(value: Any, context: str) -> RequestBody | LocalReference:
    source = _object(value, context)
    if _is_reference(source):
        return _reference(source, context)
    return RequestBody(
        description=source.get("description"),
        required=source.get("required", False),
        content=_media_content(_required(source, "content", context), f"{context}.content"),
        extensions=_extensions(source),
    )


def _response(value: Any, context: str) -> Response | LocalReference:
    source = _object(value, context)
    if _is_reference(source):
        return _reference(source, context)
    headers: dict[str, Any] = {}
    for name, header in _object(source.get("headers", {}), f"{context}.headers").items():
        headers[name] = _reference(header, f"{context}.headers.{name}") if _is_reference(header) else header
    links: dict[str, Any] = {}
    for name, link in _object(source.get("links", {}), f"{context}.links").items():
        links[name] = _reference(link, f"{context}.links.{name}") if _is_reference(link) else link
    return Response(
        description=_required(source, "description", context),
        headers=dict(sorted(headers.items())),
        content=_media_content(source.get("content", {}), f"{context}.content"),
        links=dict(sorted(links.items())),
        extensions=_extensions(source),
    )


def _security_scheme(value: Any, context: str) -> SecurityScheme | LocalReference:
    source = _object(value, context)
    if _is_reference(source):
        return _reference(source, context)
    return SecurityScheme(
        type=_required(source, "type", context),
        description=source.get("description"),
        name=source.get("name"),
        location=source.get("in"),
        scheme=source.get("scheme"),
        bearer_format=source.get("bearerFormat"),
        flows=source.get("flows"),
        openid_connect_url=source.get("openIdConnectUrl"),
        extensions=_extensions(source),
    )


def _components(value: Any) -> Components:
    source = _object(value, "components")
    unsupported = set(source) - _TYPED_COMPONENTS - _OPAQUE_COMPONENTS
    if unsupported:
        raise OpenAPINormalizationError(
            "components contains unsupported categories: " + ", ".join(sorted(unsupported))
        )

    schemas_source = _mapping(source, "schemas", "components")
    schemas = {name: Schema(raw=schemas_source[name]) for name in sorted(schemas_source)}
    parameters_source = _object(source.get("parameters", {}), "components.parameters")
    request_source = _object(source.get("requestBodies", {}), "components.requestBodies")
    responses_source = _object(source.get("responses", {}), "components.responses")
    schemes_source = _object(source.get("securitySchemes", {}), "components.securitySchemes")

    parameters = {
        name: _parameter(parameters_source[name], f"components.parameters.{name}", "component")
        for name in sorted(parameters_source)
    }
    request_bodies = {
        name: _request_body(request_source[name], f"components.requestBodies.{name}")
        for name in sorted(request_source)
    }
    responses = {
        name: _response(responses_source[name], f"components.responses.{name}")
        for name in sorted(responses_source)
    }
    security_schemes = {
        name: _security_scheme(schemes_source[name], f"components.securitySchemes.{name}")
        for name in sorted(schemes_source)
    }
    other: dict[str, dict[str, Any]] = {}
    for category in sorted(_OPAQUE_COMPONENTS.intersection(source)):
        entries = _object(source[category], f"components.{category}")
        other[category] = {
            name: _reference(entries[name], f"components.{category}.{name}")
            if _is_reference(entries[name])
            else entries[name]
            for name in sorted(entries)
        }
    return Components(
        schemas=schemas,
        parameters=parameters,
        request_bodies=request_bodies,
        responses=responses,
        security_schemes=security_schemes,
        other=other,
    )


def _security(value: Any) -> tuple[SecurityRequirement, ...]:
    if not isinstance(value, list):
        raise OpenAPINormalizationError("security must be an array of requirement objects.")
    requirements = []
    for index, requirement in enumerate(value):
        source = _object(requirement, f"security[{index}]")
        schemes: dict[str, tuple[str, ...]] = {}
        for name in sorted(source):
            scopes = source[name]
            if not isinstance(scopes, list):
                raise OpenAPINormalizationError(f"security[{index}].{name} must be an array of scopes.")
            schemes[name] = tuple(scopes)
        requirements.append(SecurityRequirement(schemes=schemes))
    return tuple(requirements)


def _server(value: Any, context: str) -> Server:
    source = _object(value, context)
    variables = _object(source.get("variables", {}), f"{context}.variables")
    return Server(
        url=_required(source, "url", context),
        description=source.get("description"),
        variables={name: variables[name] for name in sorted(variables)},
        extensions=_extensions(source),
    )


def _servers(value: Any, context: str) -> tuple[Server, ...]:
    if not isinstance(value, list):
        raise OpenAPINormalizationError(f"{context} must be an array.")
    return tuple(_server(item, f"{context}[{index}]") for index, item in enumerate(value))


def _parameter_identity(
    entry: ParameterEntry,
    components: Components,
    context: str,
) -> tuple[str, str]:
    """Inspect a component parameter's identity without expanding its reference edge."""
    visited: set[str] = set()
    current: ParameterEntry = entry
    while isinstance(current, ParameterReference):
        uri = current.reference.uri
        prefix = "/components/parameters/"
        try:
            fragment = unquote(uri[1:], errors="strict")
        except UnicodeDecodeError as exc:
            raise OpenAPINormalizationError(
                f"Cannot decode parameter reference at {context}."
            ) from exc
        if not fragment.startswith(prefix):
            raise OpenAPINormalizationError(
                f"Cannot determine parameter identity from local reference '{uri}' at {context}."
            )
        encoded_name = fragment[len(prefix) :]
        if not encoded_name or "/" in encoded_name:
            raise OpenAPINormalizationError(
                f"Cannot determine parameter identity from local reference '{uri}' at {context}."
            )
        name = encoded_name.replace("~1", "/").replace("~0", "~")
        if name in visited:
            raise OpenAPINormalizationError(
                f"Cyclic parameter reference chain at {context}."
            )
        visited.add(name)
        target = components.parameters.get(name)
        if target is None:
            raise OpenAPINormalizationError(
                f"Parameter reference '{uri}' has no component target at {context}."
            )
        current = target
    return current.name, current.location


def _parameter_list(
    value: Any,
    context: str,
    scope: str,
) -> tuple[ParameterEntry, ...]:
    if not isinstance(value, list):
        raise OpenAPINormalizationError(f"{context} must be an array.")
    return tuple(
        _parameter(item, f"{context}[{index}]", scope)
        for index, item in enumerate(value)
    )


def _parameter_reference_uri(entry: ParameterEntry) -> str | None:
    if isinstance(entry, ParameterReference):
        return entry.reference.uri
    return None


def _merge_parameters(
    path_parameters: tuple[ParameterEntry, ...],
    operation_parameters: tuple[ParameterEntry, ...],
    components: Components,
    path: str,
    method: str,
) -> tuple[tuple[ParameterEntry, ...], tuple[ParameterOverrideDiagnostic, ...]]:
    context = f"operation {method.upper()} {path}"
    effective = list(path_parameters)
    index_by_identity: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(path_parameters):
        identity = _parameter_identity(entry, components, context)
        if identity in index_by_identity:
            raise OpenAPINormalizationError(
                f"Duplicate Path Item parameter {identity!r} at {context}."
            )
        index_by_identity[identity] = index

    diagnostics: list[ParameterOverrideDiagnostic] = []
    operation_identities: set[tuple[str, str]] = set()
    for entry in operation_parameters:
        identity = _parameter_identity(entry, components, context)
        if identity in operation_identities:
            raise OpenAPINormalizationError(
                f"Duplicate Operation parameter {identity!r} at {context}."
            )
        operation_identities.add(identity)
        existing_index = index_by_identity.get(identity)
        if existing_index is None:
            index_by_identity[identity] = len(effective)
            effective.append(entry)
            continue

        original = effective[existing_index]
        effective[existing_index] = entry
        diagnostics.append(
            ParameterOverrideDiagnostic(
                path=path,
                parameter_name=identity[0],
                parameter_location=identity[1],  # type: ignore[arg-type]
                original_source_scope="path",
                overriding_source_scope="operation",
                original_reference_uri=_parameter_reference_uri(original),
                overriding_reference_uri=_parameter_reference_uri(entry),
            )
        )
    return tuple(effective), tuple(diagnostics)


def _operation(
    value: Any,
    path_item: dict[str, Any],
    path: str,
    method: str,
    components: Components,
) -> tuple[Endpoint, tuple[ParameterOverrideDiagnostic, ...]]:
    context = f"paths.{path}.{method}"
    source = _object(value, context)
    unknown = set(source) - _OPERATION_FIELDS - {
        key for key in source if key.startswith("x-")
    }
    if unknown:
        raise OpenAPINormalizationError(
            f"{context} contains unsupported operation fields: {', '.join(sorted(unknown))}."
        )
    unsupported = set(source) & {"externalDocs", "callbacks"}
    if unsupported:
        raise OpenAPINormalizationError(
            f"{context} uses operation fields not represented by the API Model: "
            f"{', '.join(sorted(unsupported))}."
        )

    path_parameters = _parameter_list(
        path_item.get("parameters", []), f"{context} path parameters", "path"
    )
    operation_parameters = _parameter_list(
        source.get("parameters", []), f"{context} parameters", "operation"
    )
    parameters, diagnostics = _merge_parameters(
        path_parameters, operation_parameters, components, path, method
    )

    request_body = (
        _request_body(source["requestBody"], f"{context}.requestBody")
        if "requestBody" in source
        else None
    )
    response_values = _object(_required(source, "responses", context), f"{context}.responses")
    responses = {
        status: _response(response_values[status], f"{context}.responses.{status}")
        for status in sorted(response_values)
    }
    security = _security(source["security"]) if "security" in source else None
    path_servers = (
        _servers(path_item["servers"], f"paths.{path}.servers")
        if "servers" in path_item
        else None
    )
    operation_servers = (
        _servers(source["servers"], f"{context}.servers")
        if "servers" in source
        else None
    )
    tags = source.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise OpenAPINormalizationError(f"{context}.tags must be an array of strings.")

    try:
        endpoint = Endpoint(
            path=path,
            method=method,  # type: ignore[arg-type]
            operation_id=source.get("operationId"),
            summary=source.get("summary"),
            description=source.get("description"),
            tags=tuple(tags),
            deprecated=source.get("deprecated", False),
            path_summary=path_item.get("summary"),
            path_description=path_item.get("description"),
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            security=security,
            path_servers=path_servers,
            operation_servers=operation_servers,
            extensions=_extensions(source),
        )
    except ValidationError as exc:
        raise OpenAPINormalizationError(
            f"Invalid operation {method.upper()} {path} could not be represented by the API Model."
        ) from exc
    return endpoint, diagnostics


def _endpoints(
    value: Any,
    components: Components,
) -> tuple[tuple[Endpoint, ...], tuple[ParameterOverrideDiagnostic, ...]]:
    paths = _object(value, "paths")
    endpoints: list[Endpoint] = []
    diagnostics: list[ParameterOverrideDiagnostic] = []
    for path in sorted(paths):
        path_item = _object(paths[path], f"paths.{path}")
        if "$ref" in path_item:
            pointer = "/paths/" + path.replace("~", "~0").replace("/", "~1") + "/$ref"
            raise OpenAPINormalizationError(
                f"unsupported_path_item_reference at {pointer}; Path Item references are not normalized."
            )
        unknown = set(path_item) - _PATH_ITEM_FIELDS - {
            key for key in path_item if key.startswith("x-")
        } - set(_HTTP_METHODS)
        if unknown:
            raise OpenAPINormalizationError(
                f"Malformed Path Item at paths.{path}: unsupported fields {', '.join(sorted(unknown))}."
            )
        path_extensions = {key for key in path_item if key.startswith("x-")}
        if path_extensions:
            raise OpenAPINormalizationError(
                f"Path Item extensions at paths.{path} cannot be represented by the API Model."
            )
        for field in ("summary", "description"):
            if field in path_item and not isinstance(path_item[field], str):
                raise OpenAPINormalizationError(
                    f"paths.{path}.{field} must be a string."
                )
        if "servers" in path_item:
            _servers(path_item["servers"], f"paths.{path}.servers")
        if "parameters" in path_item:
            _parameter_list(path_item["parameters"], f"paths.{path}.parameters", "path")
        for method in _HTTP_METHODS:
            if method not in path_item:
                continue
            endpoint, endpoint_diagnostics = _operation(
                path_item[method], path_item, path, method, components
            )
            endpoints.append(endpoint)
            diagnostics.extend(endpoint_diagnostics)
    return tuple(endpoints), tuple(diagnostics)


def normalize_openapi_document(document: dict[str, Any]) -> NormalizationResult:
    """Normalize API metadata, components, and operations from a validated document.

    Local references are retained as edges and are never resolved or fetched.
    """
    source = _object(document, "OpenAPI document")
    try:
        info_source = _object(_required(source, "info", "OpenAPI document"), "info")
        info = ApiInfo(
            title=_required(info_source, "title", "info"),
            version=_required(info_source, "version", "info"),
            summary=info_source.get("summary"),
            description=info_source.get("description"),
            terms_of_service=info_source.get("termsOfService"),
            contact=info_source.get("contact"),
            license=info_source.get("license"),
            extensions=_extensions(info_source),
        )

        servers: tuple[Server, ...] | None = None
        if "servers" in source:
            server_values = source["servers"]
            if not isinstance(server_values, list):
                raise OpenAPINormalizationError("servers must be an array.")
            servers = tuple(_server(item, f"servers[{index}]") for index, item in enumerate(server_values))

        security = _security(source["security"]) if "security" in source else None
        components = _components(source.get("components", {}))
        endpoints, diagnostics = _endpoints(source.get("paths", {}), components)
        webhooks_source = _object(source.get("webhooks", {}), "webhooks")
        webhooks = {name: webhooks_source[name] for name in sorted(webhooks_source)}

        api = ApiModel(
            openapi_version=_required(source, "openapi", "OpenAPI document"),
            info=info,
            servers=servers,
            security=security,
            endpoints=endpoints,
            components=components,
            webhooks=webhooks,
            extensions=_extensions(source),
        )
        return NormalizationResult(api=api, diagnostics=diagnostics)
    except OpenAPINormalizationError:
        raise
    except ValidationError as exc:
        raise OpenAPINormalizationError(
            "Validated OpenAPI data could not be represented by the API Model."
        ) from exc
