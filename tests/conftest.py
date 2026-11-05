"""Shared test setup: fake keys only, no real network or paid API calls."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAKE_EIA_KEY = "FAKE_EIA_SECRET_123"
FAKE_FRED_KEY = "FAKE_FRED_SECRET_456"

os.environ.update(
    EIA_API_KEY=FAKE_EIA_KEY,
    FRED_API_KEY=FAKE_FRED_KEY,
    ANTHROPIC_API_KEY="fake",
)


@pytest.fixture(autouse=True)
def clean_telemetry():
    from observability import get_telemetry

    get_telemetry().clear()
    yield
    get_telemetry().clear()
