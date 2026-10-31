"""Deterministic test-suite generation orchestration."""

from __future__ import annotations

from hashlib import sha256

from app.services.openapi.models import NormalizationResult
from app.services.test_generation.models import GeneratedTestSuite


def build_case_id(
    category: str,
    endpoint_path: str,
    method: str,
    variant_id: str,
) -> str:
    """Build a stable ID from UTF-8 fields encoded with unambiguous lengths.

    Each field is prefixed by its byte length as an unsigned 8-byte big-endian
    integer before hashing. This avoids delimiter ambiguity and Python's
    process-randomized ``hash()`` behavior.
    """
    fields = (category, endpoint_path, method, variant_id)
    payload = b"".join(
        len(encoded).to_bytes(8, byteorder="big") + encoded
        for encoded in (field.encode("utf-8") for field in fields)
    )
    return f"case-{sha256(payload).hexdigest()}"


def generate_test_suite(result: NormalizationResult) -> GeneratedTestSuite:
    """Return the deterministic empty suite for a normalized API.

    Actual test-case generation is intentionally deferred. Normalization
    diagnostics describe the prior pipeline stage and are not generation
    diagnostics, so this foundation does not copy them into the suite.
    """
    if not isinstance(result, NormalizationResult):
        raise TypeError("result must be a NormalizationResult")

    api = result.api
    return GeneratedTestSuite(
        api_title=api.info.title,
        api_version=api.info.version,
        openapi_version=api.openapi_version,
        cases=(),
        diagnostics=(),
    )
