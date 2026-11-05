"""The Committee-to-Observatory path and the resource adequacy metric."""
from __future__ import annotations

import types

from streamlit.testing.v1 import AppTest

from observability import get_telemetry, record_llm

OBSERVATORY_SCRIPT = """
from ui.observatory import render_observatory
render_observatory()
"""

# Runs the real Committee tab against a fake client, so no paid calls happen.
COMMITTEE_SCRIPT = """
import types
from unittest import mock
from ui.committee import render_committee

usage = types.SimpleNamespace(
    input_tokens=1000, output_tokens=200,
    cache_creation_input_tokens=0, cache_read_input_tokens=0,
)
reply = types.SimpleNamespace(
    content=[types.SimpleNamespace(type="text", text="VERDICT: WATCH")],
    stop_reason="end_turn",
    usage=usage,
)
client = mock.MagicMock()
client.messages.create.return_value = reply
render_committee("Permian", "oil", 2028, 75.0, client)
"""


def _llm_response():
    usage = types.SimpleNamespace(
        input_tokens=1000, output_tokens=200,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return types.SimpleNamespace(usage=usage, stop_reason="end_turn")


def test_observatory_handles_events_without_an_agent_label():
    record_llm("claude-sonnet-4-5-20250929", _llm_response(), 1000.0)
    app = AppTest.from_string(OBSERVATORY_SCRIPT, default_timeout=60).run()
    assert not app.exception


def test_observatory_handles_api_only_events():
    from observability import span

    with span("api", "eia._get", source="eia"):
        pass
    app = AppTest.from_string(OBSERVATORY_SCRIPT, default_timeout=60).run()
    assert not app.exception


def test_committee_run_reports_cost_per_agent_and_renders():
    committee = AppTest.from_string(COMMITTEE_SCRIPT, default_timeout=60).run()
    assert not committee.exception
    committee.button[0].click().run()
    assert not committee.exception

    llm_events = [e for e in get_telemetry().events() if e.category == "llm"]
    assert {e.attributes.get("agent") for e in llm_events} == {"bull", "bear", "pm"}
    assert all(e.attributes["cost_usd"] > 0 for e in llm_events)

    observatory = AppTest.from_string(OBSERVATORY_SCRIPT, default_timeout=60).run()
    assert not observatory.exception
    metrics = {item.label: item.value for item in observatory.metric}
    assert metrics["LLM calls"] == "3"


def test_resource_adequacy_uses_annual_production_once():
    app = AppTest.from_string(
        """
from ui.overview import _render_usgs_section
_render_usgs_section(
    "Permian", "oil", {"projected_production": {"value": 3_372_957}}
)
""",
        default_timeout=60,
    ).run()
    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    # 46.3 BBO is 46,300,000 Mbbl; divided by 3,372,957 Mbbl/yr gives 13.7 years.
    assert metrics["Oil Resource Adequacy"] == "13.7 yrs"
