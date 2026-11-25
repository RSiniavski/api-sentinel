"""Syntax checks for local URI fragment references and RFC 3986 URI references."""

import re
from ipaddress import AddressValueError, IPv6Address
from urllib.parse import unquote

_ANCHOR_PATTERN = re.compile(r"^[A-Za-z_][-A-Za-z0-9._]*$")
_URI_FRAGMENT_CHAR = re.compile(r"^[A-Za-z0-9\-._~!$&'()*+,;=:@/?]$")
# RFC 3986 character classes. Each class is one alternative of unreserved or
# sub-delims characters plus percent-encoded octets. Square brackets are in none of
# them: they appear only around an IP literal in the host (see _is_valid_authority).
_PCT = r"%[0-9A-Fa-f]{2}"
_UNRESERVED_SUB_DELIMS = r"A-Za-z0-9\-._~!$&'()*+,;="
_PATH_TEXT = re.compile(rf"^(?:[{_UNRESERVED_SUB_DELIMS}:@/]|{_PCT})*$")
_QUERY_TEXT = re.compile(rf"^(?:[{_UNRESERVED_SUB_DELIMS}:@/?]|{_PCT})*$")
_USERINFO_TEXT = re.compile(rf"^(?:[{_UNRESERVED_SUB_DELIMS}:]|{_PCT})*$")
_REG_NAME_TEXT = re.compile(rf"^(?:[{_UNRESERVED_SUB_DELIMS}]|{_PCT})*$")
_IPV6_TEXT = re.compile(r"^[0-9A-Fa-f:.]+$")
_IPVFUTURE = re.compile(rf"^[vV][0-9A-Fa-f]+\.[{_UNRESERVED_SUB_DELIMS}:]+$")
_PORT = re.compile(r"^[0-9]*$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*$")
# Intentional restriction beyond the RFC 3986 syntax (`port = *DIGIT`): a port is a
# 16-bit TCP/UDP port number, so values above 65535 cannot name a real endpoint.
_MAX_PORT = 65535


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


def _uri_scheme(value: str) -> str | None:
    """Return the text before the scheme colon, or None for a relative reference.

    A colon starts a scheme only when it comes before the first `/`, `?` or `#`.
    """
    delimiters = [index for index in (value.find("/"), value.find("?"), value.find("#")) if index != -1]
    colon = value.find(":")
    if colon == -1 or (delimiters and colon > min(delimiters)):
        return None
    return value[:colon]


def _is_valid_ip_literal(host: str) -> bool:
    """Return whether `host` is a bracketed IPv6 address or an IPvFuture literal."""
    inner = host[1:-1]
    if _IPVFUTURE.fullmatch(inner):
        return True
    if not _IPV6_TEXT.fullmatch(inner):
        return False
    try:
        IPv6Address(inner)
    except AddressValueError:
        return False
    return True


def _is_port_in_range(port: str) -> bool:
    """Return whether a string of ASCII digits is empty or a value up to _MAX_PORT.

    The comparison is on the decimal text, never through int(): Python limits the
    length of a string it converts to an int, and a port can be arbitrarily long.
    """
    digits = port.lstrip("0")
    max_digits = str(_MAX_PORT)
    return len(digits) < len(max_digits) or (len(digits) == len(max_digits) and digits <= max_digits)


def _is_valid_authority(authority: str) -> bool:
    """Check `[userinfo@]host[:port]`. Brackets are allowed only around an IP literal."""
    userinfo, at, hostport = authority.rpartition("@")
    if at and not _USERINFO_TEXT.fullmatch(userinfo):
        return False
    if hostport.startswith("["):
        end = hostport.find("]")
        if end == -1 or not _is_valid_ip_literal(hostport[: end + 1]):
            return False
        rest = hostport[end + 1 :]
        if rest and not rest.startswith(":"):
            return False
        port = rest[1:]
    else:
        host, _, port = hostport.partition(":")
        if not _REG_NAME_TEXT.fullmatch(host):
            return False
    return bool(_PORT.fullmatch(port)) and _is_port_in_range(port)


def _is_valid_uri_reference(value: str) -> bool:
    """Return whether a non-empty string is an RFC 3986 URI or relative reference.

    This is a syntax check only: it splits the reference into its components and
    checks the characters of each one, the scheme, the host and the port. It never
    resolves or fetches anything. Square brackets are valid only around an IP
    literal in the authority; in a path, query or fragment they must be
    percent-encoded.
    """
    if not value:
        return False
    scheme = _uri_scheme(value)
    if scheme is not None:
        if not _URI_SCHEME.fullmatch(scheme):
            return False
        value = value[len(scheme) + 1 :]
    rest, hash_sign, fragment = value.partition("#")
    if hash_sign and not _QUERY_TEXT.fullmatch(fragment):
        return False
    rest, question, query = rest.partition("?")
    if question and not _QUERY_TEXT.fullmatch(query):
        return False
    if rest.startswith("//"):
        authority, slash, path = rest[2:].partition("/")
        if not _is_valid_authority(authority):
            return False
        path = slash + path
    else:
        path = rest
    return bool(_PATH_TEXT.fullmatch(path))


def _is_valid_absolute_uri(value: str) -> bool:
    """Return whether a string is an RFC 3986 URI that starts with a scheme."""
    return _is_valid_uri_reference(value) and _uri_scheme(value) is not None
