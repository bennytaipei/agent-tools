"""Validate tool inputs and outputs against the manifest schemas."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ToolFailure:
    """A declared error code plus the text the caller should see in detail."""

    code: str
    detail: str


# One example and the field names. Shown when a call does not match the schema,
# and when an interaction tool rejects a target shape at runtime.
_INPUT_HINTS = {
    "browser.navigate": 'url is an absolute http or https URL. Example: {"url":"https://example.com"}.',
    "browser.read": 'max_viewports is an integer from 1 to 30. Example: {"max_viewports":10}.',
    "browser.screenshot": "This tool takes no fields. Example: {}.",
    "browser.handoff": (
        'reason is text, timeout is seconds, and done_when is optional. '
        'Example: {"reason":"login wall","timeout":120}.'
    ),
    "browser.click": (
        "target is placeholder, label, text, css, role and name, point {x, y}, or a string. "
        'Example: {"target":{"placeholder":"Search Admin Resources"}}.'
    ),
    "browser.type": (
        "text is the characters to type. target is optional and uses placeholder, label, text, "
        'css, role and name, point {x, y}, or a string. Example: {"target":{"label":"Email"},"text":"ada@example.com"}.'
    ),
    "browser.scroll": (
        "A number, scroll_y, or y is an absolute page offset in CSS pixels. "
        "delta is {dx, dy} or a number of pixels down. to is top or bottom. "
        'Example: 2200 or {"scroll_y":2200}.'
    ),
    "browser.select": (
        "target is placeholder, label, text, css, role and name, point {x, y}, or a string. "
        'Pass one of value, label, or index. Example: {"target":{"css":"select"},"label":"Green"}.'
    ),
    "browser.wait_for": (
        "Pass target and state, or text, or url_matches, or load_state. "
        "target is placeholder, label, text, css, role and name, or a string. "
        'Example: {"target":{"text":"Ready"},"state":"visible"}.'
    ),
}


def input_hint(name: str) -> str:
    """What to send instead, for a schema or target-shape rejection."""

    return _INPUT_HINTS.get(name, "Send arguments that match this tool's input schema.")


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
