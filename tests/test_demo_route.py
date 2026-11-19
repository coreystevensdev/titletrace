"""The demo endpoint picks its data source and says which one it used.

Live when the vendor keys are configured and the budget holds, recorded
otherwise. The mode has to reach the response, because a recorded trace
presented as a live one is a claim about what the deployment can do.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from titletrace.demo import RECORDED_ADDRESSES


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from titletrace.api.main import app

    with TestClient(app) as c:
        yield c


def test_without_keys_the_demo_still_answers(client):
    resp = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "recorded"
    assert body["parcel"]["parcel_id"]


def test_the_recorded_mode_is_named_in_the_response(client):
    """Not inferable from the payload. If the caller cannot tell a recorded
    trace from a live one, the demo claims something it has not earned."""
    body = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[1]}).json()

    assert body["mode"] == "recorded"
    assert body["mode_reason"]


def test_an_address_with_no_recording_says_so_rather_than_half_answering(client):
    body = client.post("/api/demo/trace",
                       json={"address": "1 Nowhere Ln, Scranton, PA 18503"}).json()

    assert body["mode"] == "recorded"
    assert body.get("error")
    assert "recorded" in body["error"].lower()


def test_the_demo_returns_the_prompt_the_model_would_receive(client):
    """What stands in for a synthesized report when there is no key. It is the
    real assembly, so it is worth showing rather than describing."""
    body = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[2]}).json()

    assert "submit_report" in body["prompt"]
    assert "812 Morris" in body["prompt"]


def test_the_demo_exposes_what_is_left_in_the_budget(client):
    """So the page can show it. A cost cap nobody can see is a cost cap nobody
    trusts."""
    body = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[0]}).json()

    assert "budget" in body
    assert body["budget"]["monthly_budget_usd"] > 0
    assert body["budget"]["monthly_spend_usd"] >= 0
