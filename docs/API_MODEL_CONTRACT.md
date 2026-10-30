# API Sentinel — API Model Contract

**Status:** Design proposal only. This document does not describe implemented code.

## 1. Model overview

The API Model is the typed, deterministic boundary between a future OpenAPI Normalizer and the future Deterministic Test Generator. The current Parser remains unchanged: `parse_openapi_document(content: str, filename: str) -> dict[str, Any]` returns a parsed and validated dictionary. No API Model or Normalizer currently exists.

The Normalizer will accept that validated dictionary and return a `NormalizationResult`, whose `api` field contains the model below and whose `diagnostics` field reports parameter overrides. The model is not a replacement for the source document and does not perform parsing, specification validation, network access, or HTTP execution. It preserves the source information needed for test generation, especially full JSON-compatible Schema Objects and their reference edges.

Use Pydantic v2 models with strict validation, `extra="forbid"` on the model contract, and `frozen=True` as a shallow mutation guard. Use tuples for semantically ordered lists such as endpoint order, parameters, media content entries if represented as pairs, and security alternatives. Use dictionaries where names or status keys are meaningful lookup keys. Do not sort arrays whose order is significant; where a map is serialized, define canonical key ordering at serialization time rather than relying on input file order.

The API Model is intentionally not a byte-for-byte copy of the source document. It preserves normalized semantics needed by the Generator and preserves schema objects losslessly. If exact round-trip serialization or source-location highlighting becomes a requirement, retain the original parsed document separately in an ingestion/source record rather than adding duplicate source fields throughout this model. Unsupported Path Item references are an explicit Normalizer error in the MVP; they are never silently skipped or partially applied.

## 2. Current repository facts

- The public Parser interface is `parse_openapi_document(content, filename) -> dict[str, Any]`.
- The Parser accepts JSON and YAML, imposes a 5 MiB content limit, rejects unsupported extensions and YAML aliases, and uses a safe YAML loader.
- The validator supports OpenAPI 3.0.x and 3.1.x, filters external `$ref` values, and calls `openapi-spec-validator`.
- The Parser returns the validated dictionary. It does not extract endpoints, build a component registry for consumers, or produce model entities.
- `requirements.txt` pins `pydantic-settings==2.16.2`, but does not directly pin `pydantic`. Pydantic is present transitively through FastAPI, pydantic-settings, and the OpenAPI validator. The inspected local environment had Pydantic 2.14.2. For a model package that imports Pydantic directly, declare and pin Pydantic directly when implementation is approved.
- `openapi-spec-validator==0.9.0` provides specification validation, not API model extraction. `PyYAML==6.0.3` provides YAML parsing. FastAPI and HTTPX are not needed by the model itself.
- Current tests exercise Parser behavior and the health endpoints, not a Normalizer or API Model. The previous full test run had 47 passing tests; that result does not validate this unimplemented design.

See [the architecture proposal](API_MODEL_NORMALIZER_PROPOSAL.md), [the Parser](../backend/app/services/openapi/parser.py), [the validator](../backend/app/services/openapi/validator.py), [the requirements](../requirements.txt), and [the Parser tests](../tests/test_openapi_parser.py).

## 3. Design rules

1. Keep the model independent of FastAPI, HTTPX, databases, credentials, and execution.
2. Do not fetch, expand, or recursively inline references.
3. Preserve schema compositions exactly; do not flatten `allOf`, `oneOf`, `anyOf`, or `not`.
4. Preserve unknown schema keywords and vendor extensions.
5. Do not conflate absent, explicitly empty, and inherited states when OpenAPI semantics distinguish them.
6. Keep effective endpoint data convenient for generation while retaining provenance where it explains inheritance or override behavior.
7. Treat server URLs and security scheme definitions as specification data. Actual credentials and target selection belong elsewhere.
8. The model handles both OpenAPI 3.0.x and 3.1.x. The original version is part of the model so consumers can interpret dialect-specific schema keywords correctly.

## 4. Python types and common model configuration

The following definitions show the proposed contract. They are illustrative design code, not source files or an implementation. Model validators may enforce local invariants such as path parameters being required, mutually exclusive `schema`/`content` on a parameter, and a nonempty response map.

```python
from __future__ import annotations

from copy import deepcopy
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator


NoneType = type(None)
JsonScalar: TypeAlias = NoneType | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]
SchemaSource: TypeAlias = bool | JsonObject
ParameterLocation: TypeAlias = Literal["path", "query", "header", "cookie"]
ParameterScope: TypeAlias = Literal["path", "operation", "component"]


class Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )


class LocalReference(Model):
    """A validated local reference URI, kept as an edge rather than expanded."""

    uri: str
    summary: str | None = None
    description: str | None = None


class ParameterReference(Model):
    """A parameter reference edge plus parameter-only declaration metadata."""

    reference: LocalReference
    source_scope: ParameterScope


class Schema(Model):
    """Raw OpenAPI/JSON Schema node; a boolean schema is valid in OAS 3.1."""

    raw: SchemaSource

    @field_validator("raw", mode="before")
    @classmethod
    def detach_source_value(cls, value: SchemaSource) -> SchemaSource:
        """Copy nested source containers so later source mutation cannot leak in."""
        return deepcopy(value)

    def copy_raw(self) -> SchemaSource:
        """Return a mutable copy; consumers should not mutate `raw` directly."""
        return deepcopy(self.raw)

    @property
    def reference(self) -> LocalReference | None:
        """Expose a top-level `$ref` as a typed view without copying schema data."""
        if not isinstance(self.raw, dict):
            return None
        uri = self.raw.get("$ref")
        if not isinstance(uri, str):
            return None
        return LocalReference(
            uri=uri,
            summary=self.raw.get("summary") if isinstance(self.raw.get("summary"), str) else None,
            description=(
                self.raw.get("description")
                if isinstance(self.raw.get("description"), str)
                else None
            ),
        )


class Server(Model):
    url: str
    description: str | None = None
    variables: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ApiInfo(Model):
    title: str
    version: str
    summary: str | None = None
    description: str | None = None
    terms_of_service: str | None = None
    contact: JsonObject | None = None
    license: JsonObject | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class SecurityRequirement(Model):
    """One alternative requirement; names refer to Components.security_schemes."""

    schemes: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class SecurityScheme(Model):
    """A scheme definition only; actual credentials are never stored here."""

    type: str
    description: str | None = None
    name: str | None = None
    location: Literal["query", "header", "cookie"] | None = None
    scheme: str | None = None
    bearer_format: str | None = None
    flows: JsonObject | None = None
    openid_connect_url: str | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class Parameter(Model):
    name: str
    location: ParameterLocation
    required: bool = False
    description: str | None = None
    deprecated: bool = False
    allow_empty_value: bool | None = None
    style: str | None = None
    explode: bool | None = None
    allow_reserved: bool | None = None
    schema: Schema | None = None
    content: dict[str, "MediaType"] | None = None
    example: JsonValue | None = None
    examples: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    source_scope: ParameterScope
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class MediaType(Model):
    schema: Schema | None = None
    example: JsonValue | None = None
    examples: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    encoding: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class RequestBody(Model):
    description: str | None = None
    required: bool = False
    content: dict[str, MediaType]
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class Response(Model):
    description: str
    headers: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    content: dict[str, MediaType] = Field(default_factory=dict)
    links: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


ParameterEntry: TypeAlias = Parameter | ParameterReference
RequestBodyEntry: TypeAlias = RequestBody | LocalReference
ResponseEntry: TypeAlias = Response | LocalReference
SecuritySchemeEntry: TypeAlias = SecurityScheme | LocalReference


class Components(Model):
    schemas: dict[str, Schema] = Field(default_factory=dict)
    parameters: dict[str, ParameterEntry] = Field(default_factory=dict)
    request_bodies: dict[str, RequestBodyEntry] = Field(default_factory=dict)
    responses: dict[str, ResponseEntry] = Field(default_factory=dict)
    security_schemes: dict[str, SecuritySchemeEntry] = Field(default_factory=dict)
    # Less common component categories are retained as JSON objects for MVP
    # source preservation; the first Generator need not interpret them.
    other: dict[str, dict[str, JsonObject | LocalReference]] = Field(default_factory=dict)


class Endpoint(Model):
    path: str
    method: Literal["get", "put", "post", "delete", "options", "head", "patch", "trace"]
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    path_summary: str | None = None
    path_description: str | None = None
    parameters: tuple[ParameterEntry, ...] = ()
    request_body: RequestBodyEntry | None = None
    responses: dict[str, ResponseEntry]
    # None means inherit; () means explicitly empty. These states are distinct.
    security: tuple[SecurityRequirement, ...] | None = None
    # Retain declarations by scope. None means field absent; () means present-empty.
    path_servers: tuple[Server, ...] | None = None
    operation_servers: tuple[Server, ...] | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ApiModel(Model):
    openapi_version: str
    info: ApiInfo
    # None and () preserve absent versus explicitly empty root `servers`.
    servers: tuple[Server, ...] | None = None
    security: tuple[SecurityRequirement, ...] | None = None
    endpoints: tuple[Endpoint, ...] = ()
    components: Components = Field(default_factory=Components)
    # OAS 3.1 webhook declarations are retained but are not client-call endpoints.
    webhooks: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ParameterOverrideDiagnostic(Model):
    """An operation parameter that replaces a same-identity path parameter."""

    code: Literal["parameter_overridden"] = "parameter_overridden"
    path: str
    parameter_name: str
    parameter_location: ParameterLocation
    original_source_scope: Literal["path"]
    overriding_source_scope: Literal["operation"]
    original_reference_uri: str | None = None
    overriding_reference_uri: str | None = None


class NormalizationResult(Model):
    """Normalized model and informational parameter override diagnostics."""

    api: ApiModel
    diagnostics: tuple[ParameterOverrideDiagnostic, ...] = ()
```

### Common JSON-compatible value types

- `JsonValue` contains only JSON scalar/list/object values. It excludes YAML-specific Python values such as dates, sets, and custom tagged objects; the Parser’s safe-loader and validator remain responsible for the accepted document.
- `SchemaSource` is `bool | JsonObject`: OpenAPI 3.1 allows boolean schemas; OpenAPI 3.0 does not. Validation by the Parser determines whether a particular document/version permits the value.
- `ParameterEntry` is used both in `Components.parameters` and `Endpoint.parameters`; either may retain a parsed `Parameter` or a local `ParameterReference` edge. Other reference-bearing entries use generic `LocalReference` without parameter scope metadata.
- `ParameterLocation` and `ParameterScope` are shared aliases used by model fields and diagnostics so their values stay consistent.
- The recursive `JsonValue` alias does not recursively instantiate Pydantic `Schema` objects. This keeps schema source data finite and preserves reference cycles as strings.

### Practical decision on nested schema mutability

The MVP will keep `Schema.raw` as ordinary `dict`/`list`/scalar JSON data. This preserves normal Pydantic JSON serialization and makes raw OpenAPI keywords directly inspectable. `frozen=True` prevents replacing the `raw` field but cannot prevent a caller from mutating a nested dictionary or list.

To reduce accidental cross-object mutation, the proposed `field_validator` deep-copies input when constructing `Schema`; callers should treat `raw` as read-only and use `copy_raw()` when they intentionally need a mutable copy. Model, Normalizer, and Generator code should not mutate the stored schema tree. This is a documented convention rather than deep immutability; converting nested containers to immutable maps is deferred because it complicates ordinary JSON serialization and is not justified for the MVP.

## 5. Complete field contract

In the tables, “required” means the Normalizer must supply the field when constructing the model. Defaults shown are model defaults, not necessarily explicit source values. The Pydantic `model_fields_set` can be consulted for optional scalar/example fields if distinguishing an absent source property from a source value of JSON `null` becomes necessary.

### `ApiModel`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `openapi_version` | `str` | Required | Exact supported OpenAPI version string; selects dialect-aware interpretation. |
| `info` | `ApiInfo` | Required | API metadata. |
| `servers` | `tuple[Server, ...] \| None` | `None` | Root server declaration; `None` (absent) is distinct from `()` (explicit empty). |
| `security` | `tuple[SecurityRequirement, ...] \| None` | `None` | Global security declaration; preserves absent vs explicit empty vs an empty requirement object. |
| `endpoints` | `tuple[Endpoint, ...]` | `()` | Deterministically ordered operations extracted from `paths`. |
| `components` | `Components` | Empty model | Typed registries and retained opaque component categories. |
| `webhooks` | `dict[str, JsonObject]` | `{}` | Raw OAS 3.1 webhook objects; not mixed into ordinary client-call endpoints. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Root `x-` extensions. |

### `ApiInfo`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `title` | `str` | Required | API title. |
| `version` | `str` | Required | API product version; distinct from `openapi_version`. |
| `summary` | `str \| None` | `None` | Preserve when supported by source version. |
| `description` | `str \| None` | `None` | API description. |
| `terms_of_service` | `str \| None` | `None` | Source terms URL/reference string. |
| `contact` | `JsonObject \| None` | `None` | Contact object preserved without storing secrets. |
| `license` | `JsonObject \| None` | `None` | License name/URL and version-specific fields. |
| `extensions` | `dict[str, JsonValue]` | `{}` | `x-` extensions. |

### `Endpoint`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `path` | `str` | Required | Path template exactly as declared, including braces. |
| `method` | `Literal[...]` | Required | Lowercase HTTP method; include every standard OAS path method. |
| `operation_id` | `str \| None` | `None` | Operation identifier. |
| `summary`, `description` | `str \| None` | `None` | Operation text. |
| `tags` | `tuple[str, ...]` | `()` | Preserve declared tag order. |
| `deprecated` | `bool` | `False` | Operation deprecation. |
| `path_summary`, `path_description` | `str \| None` | `None` | Path Item metadata applicable to its operations. |
| `parameters` | `tuple[ParameterEntry, ...]` | `()` | Effective parameters after path/operation merge. A concrete parameter or a local reference edge is retained; references are not expanded. |
| `request_body` | `RequestBodyEntry \| None` | `None` | Body absent vs body declared; reusable local Reference Objects remain edges. |
| `responses` | `dict[str, ResponseEntry]` | Required | Original response keys such as `200`, `2XX`, and `default`; each entry is a response or local reference. |
| `security` | `tuple[SecurityRequirement, ...] \| None` | `None` | `None` inherits global security; `()` explicitly removes inherited security; `(SecurityRequirement(schemes={}),)` allows an anonymous alternative. |
| `path_servers` | `tuple[Server, ...] \| None` | `None` | Path-level declaration; absent vs present-empty preserved. |
| `operation_servers` | `tuple[Server, ...] \| None` | `None` | Operation-level declaration; absent vs present-empty preserved. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Operation `x-` fields. |

The effective server precedence is operation declaration, then path declaration, then API root declaration, then the OpenAPI-defined default. Preserve source-level declarations rather than copying the same root servers into every endpoint. The consumer should apply precedence using `is not None`, not truthiness, so explicit empty and absent values are not accidentally conflated. Exact effective behavior for empty path/operation arrays should be confirmed against the supported OAS versions before implementation.

### Path Item `$ref` policy

Both OpenAPI 3.0.x and 3.1.x define `$ref` on a Path Item Object. Both versions state that behavior is undefined when a field appears in both the referencing and referenced Path Item; 3.1 also notes that adjacent-property behavior may change in a future specification version. See the official [OAS 3.0.3 Path Item Object](https://spec.openapis.org/oas/v3.0.3#path-item-object) and [OAS 3.1.1 Path Item Object](https://spec.openapis.org/oas/v3.1.1.html#path-item-object) definitions.

**MVP decision: reject a Path Item `$ref` with a clear `OpenAPINormalizationError`.** The current architecture produces Endpoint objects from concrete Path Item operations and has no Path Item entity or merge policy. Preserving a reference edge there while also emitting endpoints would risk silently omitting or partially merging operations. Supporting this later would require explicit Path Item reference and conflict semantics. The Normalizer should report code `unsupported_path_item_reference` and the source pointer (for example `/paths/~1pets/$ref`), then stop normalization. It must not fetch the target or combine adjacent fields. An unused reusable `components.pathItems` entry may remain preserved in `Components.other`; a Path Item reference used under `paths` is the rejected case.

### `Parameter`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `name` | `str` | Required | Name exactly as declared. |
| `location` | `Literal["path", "query", "header", "cookie"]` | Required | Parameter location. |
| `required` | `bool` | `False` | Effective requiredness. Path parameters are required under OAS and should be validated upstream. |
| `description` | `str \| None` | `None` | Parameter description. |
| `deprecated` | `bool` | `False` | Deprecation annotation. |
| `allow_empty_value` | `bool \| None` | `None` | Preserve only where applicable; `None` means not explicitly supplied. |
| `style` | `str \| None` | `None` | Preserve explicit serialization style; do not fill defaults by mutating the source value. |
| `explode` | `bool \| None` | `None` | Preserve explicit value; consumer computes OAS default when needed. |
| `allow_reserved` | `bool \| None` | `None` | Query serialization option. |
| `schema` | `Schema \| None` | `None` | Schema-style parameter definition. |
| `content` | `dict[str, MediaType] \| None` | `None` | Content-style parameter definition keyed by media type. At most one of `schema` and `content` should be set. |
| `example` | `JsonValue \| None` | `None` | Singular example; `model_fields_set` distinguishes omitted from explicit JSON null. |
| `examples` | `dict[str, JsonObject \| LocalReference]` | `{}` | Named examples, preserving local references. |
| `source_scope` | `Literal["path", "operation", "component"]` | Required | Provenance of the effective parameter value. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Parameter `x-` fields. |

Parameter-level `required`, property requiredness inside a schema, and request-body requiredness are different concepts and remain on different model fields.

### `RequestBody`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `description` | `str \| None` | `None` | Body description. |
| `required` | `bool` | `False` | Whether a request body must be supplied. |
| `content` | `dict[str, MediaType]` | Required | All supported content entries, keyed by exact media type. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Request Body `x-` fields. |

An endpoint’s `request_body=None` means no request-body object. `RequestBody(required=False, ...)` means the body is defined but optional. They must not become the same state.

### `Response`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `description` | `str` | Required | Response description required by OpenAPI. |
| `headers` | `dict[str, JsonObject \| LocalReference]` | `{}` | Header definitions/references retained as JSON-compatible objects in the MVP. |
| `content` | `dict[str, MediaType]` | `{}` | All declared response media types. Empty means no response content declared. |
| `links` | `dict[str, JsonObject \| LocalReference]` | `{}` | Preserved but not interpreted by the MVP Generator. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Response `x-` fields. |

Responses live in `Endpoint.responses` under string keys. This preserves exact categories `200`, `2XX`, and `default`; do not coerce keys to integers.

### `MediaType`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `schema` | `Schema \| None` | `None` | Optional schema value; `Schema.raw` preserves keywords and composition. |
| `example` | `JsonValue \| None` | `None` | Singular example value, including explicit JSON null via field-set tracking. |
| `examples` | `dict[str, JsonObject \| LocalReference]` | `{}` | Named example definitions or local references. |
| `encoding` | `dict[str, JsonObject]` | `{}` | Property encoding metadata, notably for form/multipart. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Media Type `x-` fields. |

`RequestBody.content` and `Response.content` can contain multiple media types simultaneously. The model must retain all of them and must not choose JSON by default.

### `Server`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `url` | `str` | Required | Server URL/template exactly as declared; model construction makes no request. |
| `description` | `str \| None` | `None` | Server description. |
| `variables` | `dict[str, JsonObject]` | `{}` | Server variable definitions, including defaults/enums/descriptions. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Server `x-` fields. |

Root servers are on `ApiModel.servers`; path and operation declarations are on the relevant endpoint fields. This preserves all three scopes without repeating root values on every endpoint.

### `SecurityRequirement`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `schemes` | `dict[str, tuple[str, ...]]` | `{}` | Security scheme names mapped to scopes. Empty map is a meaningful anonymous alternative. Empty scopes are preserved. |

The tuple of `SecurityRequirement` objects is an OR-list. Scheme entries inside one requirement are ANDed. Preserve the distinction between a missing requirement list (`None`), an empty list (`()`), and a one-item list with an empty map.

### `SecurityScheme`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `type` | `str` | Required | Validated upstream; string avoids prematurely narrowing version-specific scheme types. |
| `description` | `str \| None` | `None` | Scheme description. |
| `name` | `str \| None` | `None` | API-key name when applicable. |
| `location` | `Literal["query", "header", "cookie"] \| None` | `None` | API-key location when applicable. |
| `scheme` | `str \| None` | `None` | HTTP authentication scheme. |
| `bearer_format` | `str \| None` | `None` | Bearer format hint. |
| `flows` | `JsonObject \| None` | `None` | OAuth flows preserved as a JSON object; no credentials. |
| `openid_connect_url` | `str \| None` | `None` | OpenID Connect URL string; never fetched by the model. |
| `extensions` | `dict[str, JsonValue]` | `{}` | Scheme `x-` fields. |

Do not store actual bearer tokens, API keys, OAuth client secrets, passwords, or acquired tokens in these objects. Those belong in separately protected environment/credential configuration.

### `Components`

| Field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `schemas` | `dict[str, Schema]` | `{}` | Named schemas; each retains its original raw schema value. |
| `parameters` | `dict[str, ParameterEntry]` | `{}` | Reusable parameter definitions or parameter references, with component scope recorded on reference edges. |
| `request_bodies` | `dict[str, RequestBodyEntry]` | `{}` | Reusable request bodies or references. |
| `responses` | `dict[str, ResponseEntry]` | `{}` | Reusable responses or references. |
| `security_schemes` | `dict[str, SecuritySchemeEntry]` | `{}` | Reusable schemes or references. |
| `other` | `dict[str, dict[str, JsonObject \| LocalReference]]` | `{}` | Remaining component categories such as examples, headers, links, callbacks, and OAS 3.1 path items. Preserve raw object content; defer interpretation. |

This keeps the MVP typed around concepts the first Generator needs while preventing the Normalizer from silently dropping other component objects. The Normalizer must preserve category and component names exactly. If a component value is a Reference Object, store a `LocalReference` rather than expanding it.

### `Schema` and `LocalReference`

| Model/field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `Schema.raw` | `bool \| JsonObject` | Required | Entire original schema node. Preserves `$ref`, `$ref` siblings, `allOf`, `oneOf`, `anyOf`, `not`, `nullable`, `type`, null unions, required names, read/write flags, constraints, custom dialect keywords, and unknown/vendor keywords. |
| `Schema.reference` | `LocalReference \| None` | Derived property | Typed view of a top-level `$ref` only; does not duplicate serialized state or resolve it. Composition references remain in `raw`. |
| `LocalReference.uri` | `str` | Required | Exact local URI reference string; no target content is embedded. |
| `LocalReference.summary` | `str \| None` | `None` | Reference Object override or schema sibling annotation when present. |
| `LocalReference.description` | `str \| None` | `None` | Reference Object override or schema sibling annotation when present. |
| `ParameterReference.reference` | `LocalReference` | Required | Generic local reference data, nested to keep parameter metadata out of all other reference types. |
| `ParameterReference` | Model | — | Parameter-only wrapper around a reference edge; used only by `ParameterEntry`. |
| `ParameterReference.source_scope` | `ParameterScope` | Required | Declaration scope: `path`, `operation`, or `component`. |

### Normalization result and override diagnostics

| Model/field | Type | Required/default | Meaning and preservation |
|---|---|---|---|
| `NormalizationResult.api` | `ApiModel` | Required | The normalized API consumed by the Generator. |
| `NormalizationResult.diagnostics` | `tuple[ParameterOverrideDiagnostic, ...]` | `()` | Ordered, deterministic normalization decisions; kept outside API semantics. |
| `ParameterOverrideDiagnostic.code` | `Literal["parameter_overridden"]` | `"parameter_overridden"` | Stable diagnostic kind. |
| `ParameterOverrideDiagnostic.path` | `str` | Required | OpenAPI path identifying the endpoint. |
| `ParameterOverrideDiagnostic.parameter_name` | `str` | Required | Parameter name used for merge identity. |
| `ParameterOverrideDiagnostic.parameter_location` | `ParameterLocation` | Required | Parameter location used for merge identity. |
| `ParameterOverrideDiagnostic.original_source_scope` | `Literal["path"]` | Required | Scope of the displaced declaration. |
| `ParameterOverrideDiagnostic.overriding_source_scope` | `Literal["operation"]` | Required | Scope of the winning declaration. |
| `ParameterOverrideDiagnostic.original_reference_uri` | `str \| None` | `None` | URI when the displaced declaration was a reference. |
| `ParameterOverrideDiagnostic.overriding_reference_uri` | `str \| None` | `None` | URI when the winning declaration was a reference. |

Return a `NormalizationResult` from normalization. Keep effective entries on `ApiModel.endpoints`; do not add diagnostics or displaced declarations to `ApiModel`.

For a schema such as:

```json
{
  "$ref": "#/components/schemas/Base",
  "description": "A constrained use of Base",
  "maxLength": 40
}
```

`Schema.raw` retains the complete object. `Schema.reference` offers a typed view of the URI while the sibling keywords remain untouched in `raw`. The Normalizer and Generator must use the source OpenAPI version when interpreting siblings; 3.0 and 3.1 semantics are not interchangeable.

The `Schema.raw` tree is finite because references remain strings. Recursive schemas therefore do not produce cyclic Python `Schema` object graphs. Boolean schemas are preserved for 3.1; the Parser/validator must reject them in 3.0 where invalid.

## 6. Model relationships and normalization rules

```text
ApiModel
├── info: ApiInfo
├── servers: tuple[Server, ...] | None         # root scope
├── security: tuple[SecurityRequirement, ...] | None  # global scope
├── components: Components                     # named registries
└── endpoints: tuple[Endpoint, ...]
    ├── parameters: tuple[ParameterEntry, ...]  # effective merged values/references
    ├── request_body: RequestBodyEntry | None
    ├── responses: dict[str, ResponseEntry]
    ├── security: tuple[SecurityRequirement, ...] | None
    ├── path_servers: tuple[Server, ...] | None
    └── operation_servers: tuple[Server, ...] | None

RequestBody / Response / Parameter
└── media types -> MediaType
    └── schema -> Schema(raw source tree; refs remain URI edges)
```

### Security states

- `ApiModel.security is None`: no global `security` field.
- `ApiModel.security == ()`: explicitly empty global list.
- `ApiModel.security == (SecurityRequirement(schemes={}),)`: anonymous alternative is declared.
- `Endpoint.security is None`: inherit global security.
- `Endpoint.security == ()`: operation explicitly disables inherited security.
- A nonempty operation tuple overrides the global tuple.

Never represent `None` and `()` as the same state.

### Parameters

`Endpoint.parameters` is the effective set for the operation, after combining Path Item and operation declarations by `(name, location)`. It is typed as `tuple[ParameterEntry, ...]`, so an effective entry can be a concrete `Parameter` or a `ParameterReference`. Inline `Parameter` entries and parameter-reference entries both carry declaration scope: `Parameter.source_scope` and `ParameterReference.source_scope` are required. Generic `LocalReference` remains free of parameter-specific metadata and is used for request bodies, responses, security schemes, examples, headers, links, and other reference-bearing objects.

To determine a referenced parameter's merge identity, the Normalizer performs an explicit, non-expanding lookup in `Components.parameters` to inspect the target Parameter's `name` and location. The endpoint entry remains the original reference edge if it wins; lookup is only for identity and merge rules, not inlining. A reference chain may be followed for identity with cycle detection, but the reference URI is retained as the effective entry. If a reference target cannot be inspected safely, normalization fails with a clear error instead of keeping an ambiguous duplicate or silently discarding it.

Merge rule: process path-level declarations first, then operation-level declarations. A same-identity operation entry replaces the effective path entry, whether either side is inline or referenced. If a reference wins, retain that `ParameterReference`; if an inline entry wins, retain that `Parameter`. Unmatched entries from both scopes remain. Preserve deterministic declaration order: retain the path slot when replaced, then append operation-only entries in operation declaration order.

For each replacement, return a `ParameterOverrideDiagnostic` in a separate `NormalizationResult`, not inside `ApiModel`. It records endpoint path, parameter name and location, original scope (`path`), overriding scope (`operation`), and the original/overriding reference URI where either entry is a reference. Diagnostics describe normalization decisions; they are not API semantics. The Generator consumes `result.api`; UI/logging can present `result.diagnostics`.

### Servers

Root declarations remain on `ApiModel.servers`; path and operation declarations stay on the endpoint in their own fields. The consumer chooses the effective set by OAS precedence. Since the model preserves `None` versus `()`, the precedence helper can correctly distinguish absence from an explicit empty declaration. The default server `/` should be applied by a documented effective-server rule, not inserted into source declarations as if the user wrote it.

### Request/response content and status keys

- `request_body=None`: no request-body object.
- `RequestBody(required=False, ...)`: a declared, optional body.
- Every media type remains an independent entry in `content`.
- Responses remain keyed by original strings (`"200"`, `"2XX"`, `"default"`).
- A response with no content remains distinguishable from an absent response key.

## 7. Schema/reference representation and registry lookup

`Components.schemas` is the schema registry. A schema’s raw representation retains references without expansion. A consumer follows a reference by identifying the component target and looking it up in the registry; it does not recursively copy the target into the referencing schema.

Conceptual example:

```python
pet_media = endpoint.responses["200"].content["application/json"]
schema_node = pet_media.schema

assert schema_node is not None
assert schema_node.reference is not None
assert schema_node.reference.uri == "#/components/schemas/Pet"

# The eventual resolver parses the local JSON Pointer and selects the component.
# This is a direct lookup for the simple example, not an implementation of a resolver.
pet_schema = api.components.schemas["Pet"]
assert pet_schema.raw["type"] == "object"
```

The future Generator should receive a small resolver/lookup service or use a Normalizer-provided registry access method. On a reference edge it looks up one target, traverses only as required for that test, and keeps a visited-reference set to detect cycles. It must enforce an explicit traversal/generation budget. It must not fetch external URIs. This document defines the contract only; it does not implement reference parsing, resolution, or traversal.

For JSON Pointer component references, target lookup must percent-decode and apply `~1`/`~0` decoding in the correct order. A local anchor is a distinct reference form, not a component-name string. The installed validator currently has a known OpenAPI 3.1 anchor resolution limitation documented in the current Parser tests; do not claim full anchor support for normalized/generator use until that library behavior is addressed.

### Composition and dialect preservation

Because `Schema.raw` retains the original mapping:

- `allOf`, `oneOf`, `anyOf`, and `not` remain exactly represented and are never flattened by model construction.
- OpenAPI 3.0 `nullable` remains present as written.
- OpenAPI 3.1 `type: ["string", "null"]`, `anyOf` with a null branch, boolean schemas, `const`, `$schema`, `$id`, `$anchor`, and other JSON Schema keywords remain present.
- Read-only/write-only annotations and the parent object’s `required` array stay at their original schema locations.
- Unknown JSON Schema keywords and vendor extensions survive model serialization inside `Schema.raw`.

The Generator can offer typed helper functions for common keywords, but those helpers must read from `raw` and must not create a second mutable copy of the same constraints.

## 8. Example `ApiModel` instance

The following constructs a model for two endpoints. It is an example of the contract, not code added to the repository.

```python
api = ApiModel(
    openapi_version="3.1.1",
    info=ApiInfo(title="Pets API", version="1.0"),
    servers=(Server(url="https://api.example.test/v1"),),
    security=(SecurityRequirement(schemes={"bearerAuth": ()}),),
    components=Components(
        parameters={
            "PetId": Parameter(
                name="pet_id",
                location="path",
                required=True,
                schema=Schema(raw={"type": "integer", "minimum": 1}),
                source_scope="component",
            ),
        },
        schemas={
            "Pet": Schema(
                raw={
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "integer", "minimum": 1},
                        "name": {"type": "string", "minLength": 1, "maxLength": 80},
                        "tag": {"type": ["string", "null"], "readOnly": False},
                    },
                }
            ),
            "CreatePet": Schema(
                raw={
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "tag": {"type": ["string", "null"], "writeOnly": True},
                    },
                }
            ),
        },
        security_schemes={
            "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
            "tenantKey": SecurityScheme(
                type="apiKey", name="X-Tenant-Key", location="header"
            ),
        },
    ),
    endpoints=(
        Endpoint(
            method="get",
            path="/pets/{pet_id}",
            operation_id="getPet",
            parameters=(
                ParameterReference(
                    reference=LocalReference(uri="#/components/parameters/PetId"),
                    source_scope="path",
                ),
                Parameter(
                    name="include_details",
                    location="query",
                    required=False,
                    style="form",
                    explode=False,
                    schema=Schema(raw={"type": "boolean", "default": False}),
                    source_scope="operation",
                ),
            ),
            responses={
                "200": Response(
                    description="Pet found",
                    content={
                        "application/json": MediaType(
                            schema=Schema(raw={"$ref": "#/components/schemas/Pet"})
                        ),
                        "application/xml": MediaType(
                            schema=Schema(raw={"type": "object"})
                        ),
                    },
                ),
                "404": Response(description="Pet not found"),
                "default": Response(description="Unexpected error"),
            },
            # None means this operation inherits ApiModel.security.
            security=None,
        ),
        Endpoint(
            method="post",
            path="/pets",
            operation_id="createPet",
            request_body=RequestBody(
                required=True,
                content={
                    "application/json": MediaType(
                        schema=Schema(raw={"$ref": "#/components/schemas/CreatePet"})
                    )
                },
            ),
            responses={
                "201": Response(
                    description="Created",
                    content={
                        "application/json": MediaType(
                            schema=Schema(raw={"$ref": "#/components/schemas/Pet"})
                        )
                    },
                ),
                "400": Response(description="Invalid request"),
            },
            # Operation requirement overrides global bearerAuth.
            security=(SecurityRequirement(schemes={"tenantKey": ()}),),
        ),
    ),
)

response_schema = api.endpoints[0].responses["200"].content["application/json"].schema
assert response_schema is not None
assert response_schema.reference is not None
pet_schema = api.components.schemas["Pet"]
```

This example demonstrates:

- Two endpoints with different methods.
- A required path parameter retained as a local reusable Parameter reference, and an optional query parameter.
- Reusable component schemas and local `$ref` edges.
- A required JSON request body.
- Multiple response status keys, including `default`.
- Multiple response media types.
- Global security inherited by GET and overridden by POST.
- 3.1 null union types and direction-specific read/write annotations.

The model has not expanded `Pet` into the response schema. The `pet_schema` lookup points to the registry entry; recursion and composition remain finite source data.

### Parameter override example

If the Path Item declares `ParameterReference(reference=LocalReference(uri="#/components/parameters/PetId"), source_scope="path")` and the operation declares an inline `Parameter(name="pet_id", location="path", source_scope="operation", ...)`, the Normalizer inspects the referenced component only to learn its `(name, location)` identity. The effective `Endpoint.parameters` entry is the operation Parameter; the reference is not inlined or kept as a second conflicting effective parameter. The Normalizer returns a separate diagnostic:

```python
ParameterOverrideDiagnostic(
    path="/pets/{pet_id}",
    parameter_name="pet_id",
    parameter_location="path",
    original_source_scope="path",
    overriding_source_scope="operation",
    original_reference_uri="#/components/parameters/PetId",
)
```

If the operation declaration is itself a reference, that operation reference remains the effective entry and its URI is also recorded as `overriding_reference_uri`. Thus the result exposes the winning source edge while the diagnostic records the overridden edge and both scopes.

### Path Item reference rejection example

Path Item `$ref` is valid syntax in both supported OpenAPI families, but resolving/merging it is outside the MVP. This source document therefore fails normalization rather than producing a partial API model:

```yaml
paths:
  /pets:
    $ref: "#/components/pathItems/PetCollection"
components:
  pathItems:
    PetCollection:
      get:
        responses:
          "200":
            description: OK
```

The error identifies `unsupported_path_item_reference` at `/paths/~1pets/$ref`. It is not a Parser validation failure and does not imply that the Parser should accept external Path Item references; the existing Parser policy remains unchanged.

## 9. MVP versus deferred model fields

### Required for Basic Edition

- `ApiModel`, `ApiInfo`, `Endpoint`, `Parameter`, `RequestBody`, `Response`, `MediaType`, `Server`, `SecurityRequirement`, `SecurityScheme`, and `Components` as above.
- OpenAPI version retained on the API model.
- Full raw schema preservation, including unknown keywords, composition, references, and 3.0/3.1 dialect details.
- Local component registry and local reference edges.
- Effective endpoint `ParameterEntry` values, preserving parameter reference edges and their declaration `source_scope`, with deterministic path/operation override behavior and separate override diagnostics in `NormalizationResult`.
- Clear rejection of Path Item `$ref` under `paths` with a normalization error; do not partially emit endpoints from it.
- Request body requiredness and all request media types.
- Response map keyed by original status strings and all response media types.
- Global and operation security with `None` versus empty-list distinctions.
- Root/path/operation server declarations retained separately.
- No secrets, network activity, or recursive reference expansion.
- Deterministic endpoint ordering (recommend path lexicographic order then a fixed canonical method order); preserve declared order in arrays such as tags, security alternatives, composition branches, and enum values.

### Useful but deferrable

- Deeply typed contact/license, OAuth Flow, Server Variable, Header, Example, Link, Callback, and Path Item models. In the MVP, less common structures can remain JSON-compatible objects in the component registry.
- Source file line/column maps and per-field provenance for UI diagnostics.
- A canonical serialization format/version for storing or exchanging `ApiModel` snapshots.
- Broader typed normalization diagnostics; MVP parameter override diagnostics already belong to a separate `NormalizationResult`.
- Rich reference indexing for anchors and non-schema reusable component categories.

### Deliberately not represented as operational behavior

- Authentication values or acquired credentials.
- Target base URL chosen by a user/environment; spec server declarations are candidates only.
- Resolved/expanded recursive schemas.
- Network-resolved external references.
- Test cases, execution outcomes, persistence IDs, or HTTP client state.

## 10. Open questions and risks

1. **Direct Pydantic dependency:** approve a direct Pydantic pin when implementation begins. Current requirements declare pydantic-settings but do not directly declare pydantic; the current local environment reports Pydantic 2.14.2.
2. **Nested mutability:** The MVP decision is to keep `Schema.raw` as regular JSON-compatible dictionaries/lists, deep-copy on construction, and treat stored values as read-only by convention; `copy_raw()` provides an intentional mutable copy. Direct nested mutation remains technically possible. Revisit only if consumers need enforced deep immutability, since immutable mappings complicate normal Pydantic JSON serialization.
3. **Source round trip:** this model is generator-oriented and not a lossless representation of every OpenAPI object. Decide whether the original parsed document is retained alongside the model for edit/export and diagnostic use cases.
4. **Parameter override provenance:** MVP returns a `ParameterOverrideDiagnostic` in `NormalizationResult` with identity, both scopes, and reference URIs when applicable. Retaining the full displaced Parameter object for round-tripping is deferred; preserve the original parsed document separately if that becomes necessary.
5. **Reference coverage:** the current Parser filters external `$ref` values but does not visibly filter every JSON Schema reference keyword. Decide on `$dynamicRef`/custom dialect policy before promising no external reference behavior for all 3.1 constructs.
6. **Anchor limitation:** current installed validator fails a referenced OpenAPI 3.1 anchor. Decide whether anchors are unsupported in the first model/Generator release, or whether validator support should be addressed as separate Parser work.
7. **Components coverage:** confirm whether opaque `Components.other` is sufficient for 3.1 `pathItems`, examples, headers, links, callbacks, and other objects the UI may need to inspect.
8. **Webhooks:** keep webhooks separate from ordinary client-call endpoints. Decide whether Basic Edition merely preserves them or reports a structured unsupported-feature diagnostic.
9. **Empty server arrays:** preserve absent versus empty declarations as designed; verify exact inheritance/default behavior for path and operation scopes against both supported OAS families before implementing effective server selection.
10. **Normalization failures:** decide whether unsupported constructs cause a single `OpenAPINormalizationError` or return a partial model with diagnostics. For deterministic test generation, the recommended MVP is fail clearly for constructs that would otherwise be silently misrepresented, while retaining opaque nonessential data.
11. **Format/constraint support:** the model can preserve any keyword, but the Test Generator needs a documented subset of formats and patterns it can generate deterministically.

## 11. Recommended implementation sequence

1. Approve this contract and decide the remaining open questions that affect semantics: Pydantic direct pin, reference coverage, and source-document retention. Path Item `$ref` rejection and parameter override diagnostics are MVP decisions in this contract.
2. Add model tests for Pydantic strictness, serialization, aliasing/copy behavior, and distinctions among `None`, empty tuples, and anonymous security alternatives.
3. Implement schema raw preservation and `LocalReference` views first; test composition, unknown keywords, 3.0 `nullable`, 3.1 null unions, and boolean schemas.
4. Implement component registries and typed `Reference` entries without expansion.
5. Normalize API metadata, security schemes, and root servers.
6. Normalize endpoints, merge `ParameterEntry` values by `(name, location)` without expanding winning references, preserve effective source scope, and return override diagnostics separately.
7. Normalize request bodies, media types, responses, status keys, and path/operation server declarations.
8. Add recursive component/reference fixtures and assert the model stays finite and deterministic.
9. Update architecture documentation to describe Parser → Normalizer → Test Generator accurately.
10. Only after this model contract is stable, implement Generator consumers and deterministic test definitions.

No part of this proposal has been implemented. The current Parser and application behavior remain unchanged until explicit approval to implement.
