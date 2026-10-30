"""Syntax checks for local URI fragment references."""

import re
from urllib.parse import unquote

_ANCHOR_PATTERN = re.compile(r"^[A-Za-z_][-A-Za-z0-9._]*$")
_URI_FRAGMENT_CHAR = re.compile(r"^[A-Za-z0-9\-._~!$&'()*+,;=:@/?]$")


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
