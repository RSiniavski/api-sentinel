"""OpenAPI document parsing and validation public interface."""

from app.services.openapi.exceptions import (
    OpenAPINormalizationError,
    OpenAPIParseError,
    OpenAPIParserError,
    OpenAPIValidationError,
    UnsupportedOpenAPIVersionError,
)
from app.services.openapi.normalizer import normalize_openapi_document
from app.services.openapi.parser import parse_openapi_document

__all__ = [
    "OpenAPINormalizationError",
    "OpenAPIParseError",
    "OpenAPIParserError",
    "OpenAPIValidationError",
    "UnsupportedOpenAPIVersionError",
    "parse_openapi_document",
    "normalize_openapi_document",
]
