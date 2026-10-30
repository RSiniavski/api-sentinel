"""Exceptions raised while parsing OpenAPI documents."""


class OpenAPIParserError(Exception):
    """Base class for predictable OpenAPI parser failures."""


class OpenAPIParseError(OpenAPIParserError):
    """Raised when the input cannot be decoded as the declared format."""


class UnsupportedOpenAPIVersionError(OpenAPIParserError):
    """Raised when a document declares an unsupported OpenAPI version."""


class OpenAPIValidationError(OpenAPIParserError):
    """Raised when a parsed document does not satisfy OpenAPI requirements."""
