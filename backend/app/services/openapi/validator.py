"""OpenAPI structure, version, and specification validation."""

import re
from collections.abc import Mapping
from typing import Any
from openapi_spec_validator import validate
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from openapi_spec_validator.validation.exceptions import (
    OpenAPIValidationError as SpecValidationError,
)
from referencing.exceptions import Unresolvable

from app.services.openapi.uri_references import _is_valid_local_reference
from app.services.openapi.exceptions import (
    OpenAPIValidationError,
    UnsupportedOpenAPIVersionError,
)

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_SUPPORTED_VERSION_PATTERN = re.compile(r"^3\.[01]\.\d+$")


def validate_openapi_document(document: Any) -> dict[str, Any]:
    """Validate an OpenAPI 3.0.x or 3.1.x document and return it as a dict."""
    if not isinstance(document, dict):
        raise OpenAPIValidationError("The document root must be an object.")

    version = document.get("openapi")
    if not isinstance(version, str):
        raise OpenAPIValidationError("The document must declare an OpenAPI version.")
    if not _VERSION_PATTERN.fullmatch(version):
        raise OpenAPIValidationError("The OpenAPI version must use the numeric major.minor.patch format.")
    if not _SUPPORTED_VERSION_PATTERN.fullmatch(version):
        raise UnsupportedOpenAPIVersionError(
            "Only OpenAPI versions 3.0.x and 3.1.x are supported."
        )

    _reject_external_refs(document)
    try:
        validate(document)
    except (
        OpenAPISpecValidatorError,
        SpecValidationError,
        ValueError,
        TypeError,
        RecursionError,
        Unresolvable,
    ) as exc:
        # Avoid including validator messages, which can contain user document data.
        raise OpenAPIValidationError("The document is not a valid OpenAPI specification.") from exc
    return document


def _reject_external_refs(value: Any) -> None:
    """Reject external references without recursive traversal or resolution."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            if "$ref" in current:
                ref = current["$ref"]
                if not isinstance(ref, str) or not _is_valid_local_reference(ref):
                    raise OpenAPIValidationError(
                        "Invalid or external $ref; only valid local references are allowed."
                    )
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
