"""Parse JSON and YAML OpenAPI documents from in-memory content."""

import json
from pathlib import PurePath
from typing import Any

import yaml
from yaml.events import AliasEvent

from app.services.openapi.exceptions import OpenAPIParseError
from app.services.openapi.validator import validate_openapi_document

MAX_DOCUMENT_SIZE = 5 * 1024 * 1024


class _NoAliasSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases to avoid cyclic object graphs."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported.")
        return super().compose_node(parent, index)


def parse_openapi_document(content: str, filename: str) -> dict[str, Any]:
    """Parse and validate an OpenAPI document supplied as text and filename."""
    if not isinstance(content, str):
        raise OpenAPIParseError("Document content must be text.")
    if not isinstance(filename, str) or not filename.strip():
        raise OpenAPIParseError("A filename with a .json, .yaml, or .yml extension is required.")
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise OpenAPIParseError("Document content must contain valid UTF-8 text.") from exc
    if content_size > MAX_DOCUMENT_SIZE:
        raise OpenAPIParseError("The document exceeds the 5 MiB size limit.")

    extension = PurePath(filename).suffix.lower()
    if extension not in {".json", ".yaml", ".yml"}:
        raise OpenAPIParseError("Unsupported file extension; use .json, .yaml, or .yml.")
    if not content.strip():
        raise OpenAPIParseError("The document is empty.")

    try:
        document = (
            json.loads(content)
            if extension == ".json"
            else yaml.load(content, Loader=_NoAliasSafeLoader)
        )
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeError) as exc:
        raise OpenAPIParseError(f"Invalid {('JSON' if extension == '.json' else 'YAML')} syntax.") from exc

    return validate_openapi_document(document)
