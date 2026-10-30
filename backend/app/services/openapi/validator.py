"""OpenAPI structure, version, and specification validation."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

from openapi_spec_validator import validate
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from openapi_spec_validator.validation.exceptions import (
    OpenAPIValidationError as SpecValidationError,
)
from referencing.exceptions import Unresolvable

from app.services.openapi.exceptions import (
    OpenAPIValidationError,
    UnsupportedOpenAPIVersionError,
)

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_SUPPORTED_VERSION_PATTERN = re.compile(r"^3\.[01]\.\d+$")
_ANCHOR_PATTERN = re.compile(r"^[A-Za-z_][-A-Za-z0-9._]*$")
_URI_FRAGMENT_CHAR = re.compile(r"^[A-Za-z0-9\-._~!$&'()*+,;=:@/?]$")


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


def _is_valid_local_reference(reference: str) -> bool:
    """Return whether a reference is a syntactically valid local URI fragment."""
    if not reference.startswith("#"):
        return False

    fragment = reference[1:]
    if any(ord(char) > 127 for char in fragment):
        return False
    for index, char in enumerate(fragment):
        if char == "%":
            if index + 2 >= len(fragment) or not re.fullmatch(
                r"[0-9A-Fa-f]{2}", fragment[index + 1 : index + 3]
            ):
                return False
        elif not _URI_FRAGMENT_CHAR.fullmatch(char):
            return False
    try:
        decoded = unquote(fragment, errors="strict")
    except UnicodeDecodeError:
        return False

    if not decoded:
        return True
    if decoded.startswith("/"):
        return all(
            not re.search(r"~(?![01])", segment)
            for segment in decoded[1:].split("/")
        )
    return bool(_ANCHOR_PATTERN.fullmatch(decoded))
