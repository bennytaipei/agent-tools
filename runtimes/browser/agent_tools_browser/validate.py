"""Validate tool inputs and outputs against the manifest schemas."""

from __future__ import annotations

from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

# format: uri is declared on the browser URL fields. Draft 2020-12's bundled
# checker does not enforce uri, so this runtime registers one. An absolute URI
# has a scheme and a network location. http and https are checked separately
# where the manifest's invalid_url condition requires a URL the browser can open.
_FORMAT_CHECKER = FormatChecker(Draft202012Validator.FORMAT_CHECKER.checkers)


@_FORMAT_CHECKER.checks("uri", raises=())
def _check_uri(instance: object) -> bool:
    if not isinstance(instance, str) or not instance or any(ch.isspace() for ch in instance):
        return False
    parts = urlsplit(instance)
    return bool(parts.scheme and parts.netloc)


def validator_for(schema: dict) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def schema_errors(schema: dict, instance: object) -> list[str]:
    return [error.message for error in validator_for(schema).iter_errors(instance)]


def declared_codes(manifest: dict) -> list[str]:
    codes: list[str] = []
    for item in manifest.get("errors", []):
        if isinstance(item, dict) and isinstance(item.get("code"), str):
            codes.append(item["code"])
    return codes


def input_failure_code(manifest: dict) -> str:
    """Map a schema rejection to a code this manifest already declares.

    navigate's input is the URL, so a bad input is invalid_url. Interaction
    tools reject a target the schema does not describe as invalid_target.
    handoff cannot start a share from arguments the schema rejects, so that
    is share_failed. read and screenshot declare only no_page.
    """

    codes = declared_codes(manifest)
    for preferred in ("invalid_target", "invalid_url", "share_failed", "no_page"):
        if preferred in codes:
            return preferred
    if not codes:
        raise RuntimeError(f"{manifest.get('name')} declares no error codes")
    return codes[0]


def operation_failure_code(manifest: dict) -> str:
    """A declared code for an unexpected failure while performing the call."""

    codes = declared_codes(manifest)
    for preferred in ("navigation_failed", "share_failed", "not_interactable", "no_page", "notify_failed"):
        if preferred in codes:
            return preferred
    return input_failure_code(manifest)


def is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)
