"""Typed, JSON-compatible models for normalized OpenAPI data."""

from __future__ import annotations

from copy import deepcopy
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType

from app.services.openapi.uri_references import _is_valid_local_reference

NoneType = type(None)
JsonScalar: TypeAlias = NoneType | bool | int | float | str
JsonValue = TypeAliasType(
    "JsonValue", JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]
SchemaSource: TypeAlias = bool | JsonObject
ParameterLocation: TypeAlias = Literal["path", "query", "header", "cookie"]
ParameterScope: TypeAlias = Literal["path", "operation", "component"]


class Model(BaseModel):
    """Base for strict, frozen API model values."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class LocalReference(Model):
    """A generic local reference edge, retained without expansion."""

    uri: str
    summary: str | None = None
    description: str | None = None

    @field_validator("uri")
    @classmethod
    def validate_local_uri(cls, value: str) -> str:
        """Accept only syntactically valid local URI fragment references."""
        if not _is_valid_local_reference(value):
            raise ValueError("uri must be a valid local URI fragment reference")
        return value


class ParameterReference(Model):
    """A parameter reference together with its declaration scope."""

    reference: LocalReference
    source_scope: ParameterScope


class Schema(Model):
    """An OpenAPI/JSON Schema node preserved as JSON-compatible source data."""

    raw: SchemaSource

    @field_validator("raw", mode="before")
    @classmethod
    def detach_source_value(cls, value: SchemaSource) -> SchemaSource:
        """Deep-copy input containers to prevent aliasing with the source."""
        return deepcopy(value)

    def copy_raw(self) -> SchemaSource:
        """Return a mutable deep copy of the preserved schema value."""
        return deepcopy(self.raw)

    @property
    def reference(self) -> LocalReference | None:
        """Return a typed view of a top-level ``$ref`` without resolving it."""
        if not isinstance(self.raw, dict):
            return None
        uri = self.raw.get("$ref")
        if not isinstance(uri, str):
            return None
        summary = self.raw.get("summary")
        description = self.raw.get("description")
        return LocalReference(
            uri=uri,
            summary=summary if isinstance(summary, str) else None,
            description=description if isinstance(description, str) else None,
        )


class Server(Model):
    """An OpenAPI server declaration; it never makes a network request."""

    url: str
    description: str | None = None
    variables: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ApiInfo(Model):
    """API metadata from the OpenAPI Info Object."""

    title: str
    version: str
    summary: str | None = None
    description: str | None = None
    terms_of_service: str | None = None
    contact: JsonObject | None = None
    license: JsonObject | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class SecurityRequirement(Model):
    """One security alternative; all schemes in it are required together."""

    schemes: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class SecurityScheme(Model):
    """Security scheme metadata without credential values."""

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
    """An inline OpenAPI Parameter Object with source declaration scope."""

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
    content: dict[str, MediaType] | None = None
    example: JsonValue | None = None
    examples: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    source_scope: ParameterScope
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_local_invariants(self) -> Parameter:
        """Enforce Parameter Object invariants represented by this model."""
        if self.location == "path" and not self.required:
            raise ValueError("Path parameters must be required.")
        if self.schema is not None and self.content is not None:
            raise ValueError("A parameter cannot define both schema and content.")
        return self


class MediaType(Model):
    """A content entry and its optional schema/example/encoding metadata."""

    schema: Schema | None = None
    example: JsonValue | None = None
    examples: dict[str, JsonObject | LocalReference] = Field(default_factory=dict)
    encoding: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class RequestBody(Model):
    """A declared request body, including whether it is required."""

    description: str | None = None
    required: bool = False
    content: dict[str, MediaType]
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class Response(Model):
    """An OpenAPI response definition."""

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
    """Named reusable component registries and retained opaque categories."""

    schemas: dict[str, Schema] = Field(default_factory=dict)
    parameters: dict[str, ParameterEntry] = Field(default_factory=dict)
    request_bodies: dict[str, RequestBodyEntry] = Field(default_factory=dict)
    responses: dict[str, ResponseEntry] = Field(default_factory=dict)
    security_schemes: dict[str, SecuritySchemeEntry] = Field(default_factory=dict)
    other: dict[str, dict[str, JsonObject | LocalReference]] = Field(
        default_factory=dict
    )


class Endpoint(Model):
    """One operation extracted from a concrete OpenAPI Path Item."""

    path: str
    method: Literal[
        "get", "put", "post", "delete", "options", "head", "patch", "trace"
    ]
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
    security: tuple[SecurityRequirement, ...] | None = None
    path_servers: tuple[Server, ...] | None = None
    operation_servers: tuple[Server, ...] | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("responses")
    @classmethod
    def require_response(cls, value: dict[str, ResponseEntry]) -> dict[str, ResponseEntry]:
        """Require at least one response, as required by the model contract."""
        if not value:
            raise ValueError("An endpoint must define at least one response.")
        return value


class ApiModel(Model):
    """Normalized API metadata, components, and deterministically ordered endpoints."""

    openapi_version: str
    info: ApiInfo
    servers: tuple[Server, ...] | None = None
    security: tuple[SecurityRequirement, ...] | None = None
    endpoints: tuple[Endpoint, ...] = ()
    components: Components = Field(default_factory=Components)
    webhooks: dict[str, JsonObject] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ParameterOverrideDiagnostic(Model):
    """An operation parameter replacing a same-identity path parameter."""

    code: Literal["parameter_overridden"] = "parameter_overridden"
    path: str
    parameter_name: str
    parameter_location: ParameterLocation
    original_source_scope: Literal["path"]
    overriding_source_scope: Literal["operation"]
    original_reference_uri: str | None = None
    overriding_reference_uri: str | None = None


class NormalizationResult(Model):
    """Normalized API model and separate informational diagnostics."""

    api: ApiModel
    diagnostics: tuple[ParameterOverrideDiagnostic, ...] = ()
