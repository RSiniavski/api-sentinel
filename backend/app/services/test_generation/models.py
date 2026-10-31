"""Strict output models produced by the deterministic test generator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.openapi.models import JsonValue, Schema, SecurityRequirement


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


class ResponseExpectation(GeneratedModel):
    """A declared response contract retained for later execution checks."""

    status_key: str
    media_types: tuple[str, ...]
    schemas_by_media_type: dict[str, Schema | None]
    response_reference_uri: str | None


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
