"""Daily cap on public Committee runs, so an open feature cannot run up a large bill.

Each run makes up to 24 paid Claude calls. Anyone can run the Committee, but
only a fixed number of public runs happen per UTC day; sessions unlocked with
AI_ACCESS_CODE skip the cap. The count lives in this process, so it also resets
when the app restarts.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone

from config import get_optional_secret

DEFAULT_DAILY_PUBLIC_RUNS = 20
_LIMIT_SECRET = "COMMITTEE_DAILY_RUN_LIMIT"

_lock = threading.Lock()
_state: dict = {"day": None, "count": 0}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def daily_public_run_limit() -> int:
    raw = get_optional_secret(_LIMIT_SECRET)
    try:
        return max(0, int(raw)) if raw is not None else DEFAULT_DAILY_PUBLIC_RUNS
    except ValueError:
        return DEFAULT_DAILY_PUBLIC_RUNS


def _roll_day(today: date) -> None:
    if _state["day"] != today:
        _state["day"] = today
        _state["count"] = 0


def remaining_public_runs(today: date | None = None) -> int:
    with _lock:
        _roll_day(today or _today())
        return max(0, daily_public_run_limit() - _state["count"])


def try_start_public_run(today: date | None = None) -> bool:
    """Reserve one public run for today. False when the cap is reached."""
    with _lock:
        _roll_day(today or _today())
        if _state["count"] >= daily_public_run_limit():
            return False
        _state["count"] += 1
        return True


def reset() -> None:
    with _lock:
        _state.update(day=None, count=0)
