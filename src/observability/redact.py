"""Remove credentials from text before it is stored, logged, or displayed.

The EIA and FRED APIs take their key as a query parameter, and `requests` puts
the full URL into its exception messages. Those messages then flow to telemetry,
logs, Streamlit error boxes, and tool results sent to the model, so any text
derived from an exception must pass through :func:`redact` first.
"""
from __future__ import annotations

import logging
import re
from typing import Any

_MASK = "[REDACTED]"
_NOT_ALREADY_MASKED = r"(?!\[REDACTED\])"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Query-string, header, or dict form: api_key=abc, "x-api-key": "abc"
    (
        re.compile(
            r"(?i)((?:x-)?api[_-]?key['\"]?\s*[:=]\s*['\"]?)"
            + _NOT_ALREADY_MASKED
            + r"[^&\s'\",}<>)\]]+"
        ),
        rf"\g<1>{_MASK}",
    ),
    # Anthropic keys, wherever they appear
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]+"), _MASK),
)

# urllib3 logs every request URL at DEBUG, including the api_key parameter.
# Keep those lines off even if someone later turns on debug logging for the app.
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


def redact(text: Any) -> str:
    """Return *text* as a string with any credentials masked."""
    result = str(text)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_value(value: Any) -> Any:
    """Redact strings inside nested dicts and lists; leave other types alone."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value
