"""Strict output models produced by the deterministic test generator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.openapi.models import (
    JsonValue,
    Schema,
    SecurityRequirement,
    Server,
)


class GeneratedModel(BaseModel):
    """Shared strict, frozen configuration for generated output values."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class GeneratedParameterValue(GeneratedModel):
    """A logical parameter value with stable source provenance."""

    name: str
    location: Literal["path", "query", "header", "cookie"]
    value: JsonValue
    provenance: tuple[str, ...]


class GeneratedBody(GeneratedModel):
    """A present request body encoded for one declared media type."""

    media_type: str
    value: JsonValue


class ResolvedResponseSchema(GeneratedModel):
    """A response schema whose top-level local reference chain was followed.

    `resolved` is a detached copy of the final component schema; references
    nested inside it are not resolved.
    """

    reference_chain: tuple[str, ...]
    # JSON Pointer of the declaration of the final, non-reference schema.
    pointer: str
    resolved: Schema


class ResponseHeaderExpectation(GeneratedModel):
    """A declared response header, kept as passive metadata.

    Only the fields listed here are retained. `style` and `explode` are kept
    only when declared; no default is applied and no serialization is implied.
    """

    name: str
    reference_chain: tuple[str, ...] = ()
    description: str | None = None
    required: bool = False
    deprecated: bool = False
    header_schema: Schema | None = None
    # Media type to schema for a header declared with `content`.
    content_schemas: dict[str, Schema | None] | None = None
    style: str | None = None
    explode: bool | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class LinkRequestBody(GeneratedModel):
    """A declared Link Object `requestBody`, so that an explicit null is kept."""

    value: JsonValue


class ResponseLinkExpectation(GeneratedModel):
    """A declared response link, kept as passive metadata and never executed.

    Runtime expressions inside `parameters` and `request_body` stay literal.
    """

    name: str
    reference_chain: tuple[str, ...] = ()
    operation_id: str | None = None
    operation_ref: str | None = None
    description: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    request_body: LinkRequestBody | None = None
    server: Server | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class ResponseExpectation(GeneratedModel):
    """A declared response contract retained for later execution checks."""

    status_key: str
    media_types: tuple[str, ...]
    schemas_by_media_type: dict[str, Schema | None]
    response_reference_uri: str | None
    # Only media types whose top-level schema `$ref` chain was resolved.
    resolved_schemas_by_media_type: dict[str, ResolvedResponseSchema] = Field(
        default_factory=dict
    )
    headers: tuple[ResponseHeaderExpectation, ...] = ()
    links: tuple[ResponseLinkExpectation, ...] = ()


class GenerationDiagnostic(GeneratedModel):
    """A stable, contextual diagnostic produced during case generation."""

    code: str
    message: str
    endpoint_path: str | None
    endpoint_method: str | None
    source_pointer: str | None
    reference_uri: str | None


class GeneratedTestCase(GeneratedModel):
    """One declarative generated test case."""

    case_id: str
    category: str
    path: str
    method: str
    parameters: tuple[GeneratedParameterValue, ...]
    request_body: GeneratedBody | None
    security: tuple[SecurityRequirement, ...] | None
    response_expectations: tuple[ResponseExpectation, ...]
    provenance: tuple[str, ...]
    expected_outcome: Literal[
        "response_contract_unasserted", "request_rejection_status_unspecified"
    ] = (
        "response_contract_unasserted"
    )
    violated_constraint: str | None = None
    # JSON Pointer relative to the request body, for negative property cases.
    affected_property: str | None = None


class GeneratedTestSuite(GeneratedModel):
    """Generated cases and generation diagnostics for one API."""

    api_title: str
    api_version: str
    openapi_version: str
    cases: tuple[GeneratedTestCase, ...]
    diagnostics: tuple[GenerationDiagnostic, ...]
