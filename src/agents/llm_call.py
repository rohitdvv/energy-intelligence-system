"""One Claude call with retries, shared by the Committee and the AI Analyst chat.

Retries rate limits (429), server errors (5xx, including 529 overloaded),
timeouts and dropped connections with exponential backoff: 2 s, 4 s, 8 s.
Other 4xx errors are the request's fault and are raised at once.

Every attempt, failed or not, is reported to telemetry with its agent label
and retry count, so the Observatory shows retries and errors as they happen.
The Anthropic client is created with ``max_retries=0`` so these are the only
retries and none are hidden from telemetry.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from observability import record_llm

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BACKOFF_BASE = 2  # seconds
_sleep = time.sleep  # replaced in tests


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True  # APITimeoutError is a subclass of APIConnectionError
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


def _describe(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}{f' {status}' if status else ''}: {exc}"


def create_with_retry(client: Any, *, agent: str | None, **kwargs: Any) -> Any:
    """``client.messages.create(**kwargs)`` with retries and telemetry."""
    model = kwargs.get("model", "")
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.time()
        try:
            response = client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            last_exc = exc
            record_llm(
                model, None, duration_ms=(time.time() - start) * 1000,
                agent=agent, retries=attempt - 1, success=False, error=_describe(exc),
            )
            if not _retryable(exc) or attempt == MAX_ATTEMPTS:
                break
            wait = BACKOFF_BASE ** attempt
            logger.warning(
                "Claude call failed (%s); retrying in %ds (attempt %d of %d)",
                type(exc).__name__, wait, attempt, MAX_ATTEMPTS,
            )
            _sleep(wait)
            continue
        record_llm(
            model, response, duration_ms=(time.time() - start) * 1000,
            agent=agent, retries=attempt - 1,
        )
        return response
    raise last_exc
