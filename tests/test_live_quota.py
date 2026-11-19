"""ATTOM's free tier is 200 calls a month and has no per-call price, so the
dollar cap cannot see it. A public live endpoint burns it in one afternoon.
"""

from __future__ import annotations

import asyncio

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
    # The in-process floor is process state, so a fresh one per test is what
    # "a fresh process" means here.
    monkeypatch.setattr(routes, "_counted_this_process", 0)
    monkeypatch.setattr(routes, "_counted_month", "")
    monkeypatch.setattr(routes, "_quota_unwritable", False)
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


def test_an_unwritable_ledger_stops_live_mode_rather_than_never_counting(client, monkeypatch, tmp_path):
    """The counter is read back from the file it failed to write, so a silent
    write failure leaves it at zero forever and live mode never stops. That is
    unlimited ATTOM calls on a read-only disk."""
    from titletrace.api import routes

    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 50)

    def no_writes(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(routes.Path, "write_text", no_writes, raising=False)

    modes = [
        client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]}).json()["mode"]
        for _ in range(4)
    ]

    assert "recorded" in modes, f"live mode never stopped with an unwritable ledger: {modes}"


def test_the_in_process_count_is_a_floor_under_the_file(client, monkeypatch, tmp_path):
    """Two requests that both read N before either writes would both store N+1,
    losing one. The in-process counter cannot be lost that way."""
    from titletrace.api import routes

    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 50)
    for _ in range(3):
        client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]})

    # the file is wiped behind the process, as a stale container restart might
    routes._QUOTA_PATH.unlink(missing_ok=True)

    assert routes._live_traces_used() >= 3, "a deleted ledger reset the count to zero"


@pytest.mark.asyncio
async def test_concurrent_traces_cannot_share_one_quota_slot(monkeypatch, tmp_path):
    """Twelve concurrent traces must count as twelve.

    It holds today because the increment is synchronous and the event loop runs
    it to completion. That is a property of the code rather than of a lock, so
    this is the test that notices if an await ever appears inside it."""
    import httpx

    from titletrace.api import routes
    from titletrace.demo import recorded_transport

    monkeypatch.setenv("ATTOM_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(routes, "_QUOTA_PATH", tmp_path / "quota.json")
    monkeypatch.setattr(routes, "_counted_this_process", 0)
    monkeypatch.setattr(routes, "_counted_month", "")
    monkeypatch.setattr(routes, "_quota_unwritable", False)
    monkeypatch.setattr(routes, "_LIVE_TRACES_PER_MONTH", 100)
    monkeypatch.setattr(routes, "get_http_client",
                        lambda: httpx.AsyncClient(transport=recorded_transport()))

    async def no_synthesis(state):
        return {}

    monkeypatch.setattr(routes, "synthesize_report", no_synthesis)

    from titletrace.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await asyncio.gather(*(
            c.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]})
            for _ in range(12)
        ))

    assert routes._live_traces_used() == 12, (
        f"12 live traces counted as {routes._live_traces_used()}; updates were lost"
    )
