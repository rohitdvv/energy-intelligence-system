"""A failed data request must never expose an API key anywhere."""
from __future__ import annotations

import http.server
import logging
import threading
import traceback
from unittest import mock

import pytest

from conftest import FAKE_EIA_KEY, FAKE_FRED_KEY
from data import eia, fred
from observability import Event, get_telemetry, redact


@pytest.fixture(scope="module")
def forbidden_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(403)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


CLIENTS = [
    (eia, eia.EIAClient, FAKE_EIA_KEY, lambda client: client._get("route", [])),
    (fred, fred.FREDClient, FAKE_FRED_KEY, lambda client: client._get("route", {})),
]


@pytest.mark.parametrize("module,client_cls,secret,call", CLIENTS, ids=["eia", "fred"])
@pytest.mark.parametrize("failure", ["http_403", "connection_refused"])
def test_failed_request_hides_key_everywhere(
    module, client_cls, secret, call, failure, forbidden_server, caplog
):
    url = forbidden_server if failure == "http_403" else "http://127.0.0.1:9"
    caplog.set_level(logging.DEBUG)

    with mock.patch.object(module, "_BASE_URL", url), mock.patch("time.sleep"):
        with pytest.raises(Exception) as raised:
            call(client_cls())

    rendered_traceback = "".join(
        traceback.format_exception(raised.type, raised.value, raised.tb)
    )
    events = get_telemetry().events()
    assert events, "the failure should still be recorded"
    assert events[-1].success is False

    assert secret not in str(raised.value)
    assert secret not in rendered_traceback
    assert secret not in events[-1].to_json()
    assert secret not in caplog.text
    # The redacted message stays useful for debugging.
    assert "route" in str(raised.value)


def test_telemetry_scrubs_events_from_any_source():
    get_telemetry().record(
        Event(
            ts=1.0,
            category="api",
            name="other",
            duration_ms=1.0,
            success=False,
            error="GET https://x.test/a?api_key=topsecret&b=1 failed",
            attributes={"detail": {"url": "https://x.test/?api_key=topsecret"}},
        )
    )
    assert "topsecret" not in get_telemetry().events()[-1].to_json()


@pytest.mark.parametrize(
    "text",
    [
        "https://api.eia.gov/v2/x?api_key=abc123&frequency=monthly",
        "/fred/series?file_type=json&api_key=abc123",
        "{'x-api-key': 'abc123'}",
        "api-key: abc123",
        "sk-ant-abc123",
    ],
)
def test_redact_masks_each_credential_form(text):
    assert "abc123" not in redact(text)


def test_redact_is_idempotent():
    once = redact("https://h/x?api_key=abc&frequency=monthly")
    assert redact(once) == once


def test_redact_keeps_the_rest_of_the_url():
    assert redact("https://h/x?api_key=abc&frequency=monthly") == (
        "https://h/x?api_key=[REDACTED]&frequency=monthly"
    )
