"""ATTOM's free tier is 200 calls a month and has no per-call price, so the
dollar cap cannot see it. A public live endpoint burns it in one afternoon.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from titletrace.demo import RECORDED_ADDRESSES


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Live mode here means "took the live branch", not "reached ATTOM". The
    branch is what the quota gates, so the transport underneath it is given
    the recordings and the model call is stubbed."""
    from titletrace.api import routes
    from titletrace.demo import recorded_transport

    import httpx

    monkeypatch.setenv("ATTOM_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(routes, "_QUOTA_PATH", tmp_path / "quota.json")
    monkeypatch.setattr(routes, "get_http_client",
                        lambda: httpx.AsyncClient(transport=recorded_transport()))

    async def no_synthesis(state):
        return {}

    monkeypatch.setattr(routes, "synthesize_report", no_synthesis)
    from titletrace.api.main import app

    with TestClient(app) as c:
        yield c


def test_live_mode_stops_once_the_monthly_trace_share_is_used(client, monkeypatch):
    from titletrace.api import routes

    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 2)

    modes = []
    for _ in range(4):
        body = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]}).json()
        modes.append(body["mode"])

    assert modes[:2] == ["live", "live"], modes
    assert modes[2:] == ["recorded", "recorded"], modes


def test_the_reason_names_the_quota_rather_than_the_keys(client, monkeypatch):
    """'No ATTOM key configured' would be a lie once the key is there."""
    from titletrace.api import routes

    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 0)

    body = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]}).json()

    assert body["mode"] == "recorded"
    assert "quota" in body["mode_reason"].lower() or "share" in body["mode_reason"].lower()


def test_the_quota_survives_a_restart(client, monkeypatch, tmp_path):
    from titletrace.api import routes

    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 5)
    client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]})

    assert routes._live_traces_used() == 1
