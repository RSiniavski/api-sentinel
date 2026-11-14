"""End-to-end tests for integer bounds beyond the float range.

The documents are real JSON and YAML text parsed by the actual Parser, then
normalized and generated, so no stage is replaced by a stub.
"""

from __future__ import annotations

import json

import pytest
import yaml

from app.services.openapi import normalize_openapi_document, parse_openapi_document
from app.services.test_generation import GeneratedTestSuite, generate_test_suite

HUGE = 10**400


def build_document(version: str, big_schema: dict[str, object]) -> dict[str, object]:
    def post(schema: dict[str, object]) -> dict[str, object]:
        return {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["n"],
                                "properties": {"n": schema},
                            }
                        }
                    },
                },
                "responses": {"400": {"description": "Bad request"}},
            }
        }

    return {
        "openapi": version,
        "info": {"title": "Numeric API", "version": "1.0.0"},
        "paths": {
            "/big": post(big_schema),
            "/plain": post({"type": "integer", "minimum": 1, "maximum": 5}),
        },
    }


def run_pipeline(document: dict[str, object], fmt: str) -> GeneratedTestSuite:
    if fmt == "json":
        text, filename = json.dumps(document), "numeric-api.json"
    else:
        text, filename = yaml.safe_dump(document, sort_keys=False), "numeric-api.yaml"
    parsed = parse_openapi_document(text, filename)
    return generate_test_suite(normalize_openapi_document(parsed))


def positive_value(suite: GeneratedTestSuite, path: str) -> object:
    case = next(case for case in suite.cases if case.path == path and case.category == "positive")
    assert case.request_body is not None
    return case.request_body.value["n"]  # type: ignore[index]


@pytest.mark.parametrize("fmt", ["json", "yaml"])
@pytest.mark.parametrize("version", ["3.0.3", "3.1.1"])
def test_huge_integer_bounds_through_the_real_pipeline(version: str, fmt: str) -> None:
    schema = {"type": "integer", "minimum": HUGE, "multipleOf": 3}
    document = build_document(version, schema)

    suite = run_pipeline(document, fmt)

    value = positive_value(suite, "/big")
    assert isinstance(value, int) and value >= HUGE and value % 3 == 0
    assert positive_value(suite, "/plain") == 1
    assert any(case.path == "/plain" and case.category != "positive" for case in suite.cases)
    assert not [d for d in suite.diagnostics if d.code == "no_positive_schema_value"]


@pytest.mark.parametrize("bound", [HUGE, -HUGE])
@pytest.mark.parametrize("keyword", ["minimum", "maximum"])
def test_huge_bound_keywords_keep_the_other_endpoint_through_the_real_pipeline(
    keyword: str, bound: int
) -> None:
    document = build_document("3.1.1", {"type": "integer", keyword: bound})

    first = run_pipeline(document, "json")
    second = run_pipeline(document, "json")

    assert first == second
    assert positive_value(first, "/plain") == 1
    assert any(case.path == "/big" for case in first.cases)
