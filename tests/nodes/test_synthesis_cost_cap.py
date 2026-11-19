"""The synthesis node is the only place TitleTrace spends money.

On a public URL that matters more than it does locally: anyone can hold the
button down. These tests pin the two halves of the guard, refusing before the
call and recording what the call actually cost, and the degradation path when
it refuses.
"""

from __future__ import annotations

import pytest

from titletrace.nodes import synthesize_report as node


@pytest.fixture(autouse=True)
def fresh_cap(monkeypatch):
    monkeypatch.setattr(node, "_CAP", node._build_cap(monthly_budget=1.00))
    monkeypatch.setattr(node, "_CLIENT", None)
    yield


def _state(**over):
    base = {
        "raw_address": "812 Morris Rd, Wayne, PA 19087", "parcel_id": None,
        "state": "PA", "city": "Wayne", "parcel": None, "ownership_history": [],
        "liens": [], "encumbrances": [], "zoning": None, "tax_status": None,
        "flood_zone": None, "lienholder_details": [], "tax_claim_detail": None,
        "report": None, "error": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_an_exhausted_budget_refuses_before_the_api_call(monkeypatch):
    """The point of a pre-flight check. evaluate() records after the fact, by
    which time the money is gone, so a public endpoint has to ask first."""
    called = False

    async def must_not_run(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("the model was called after the budget was spent")

    node._CAP.record(1.00)  # month's budget consumed
    monkeypatch.setattr(node, "_call_model", must_not_run)

    out = await node.synthesize_report(_state())

    assert called is False
    assert out.get("error")
    assert "budget" in out["error"].lower()


@pytest.mark.asyncio
async def test_the_recorded_cost_is_what_the_call_actually_used(monkeypatch):
    """Not the estimate. An estimate that runs high would throttle the demo
    early; one that runs low would let real spend drift past the budget."""
    class Usage:
        input_tokens = 2_000
        output_tokens = 500

    class Block:
        type = "tool_use"
        input = {"lien_count": 2, "confidence": "partial"}

    class Resp:
        content = [Block()]
        usage = Usage()

    async def fake_call(prompt):
        return Resp()

    monkeypatch.setattr(node, "_call_model", fake_call)
    before = node._CAP.monthly_spend

    await node.synthesize_report(_state())

    spent = node._CAP.monthly_spend - before
    expected = (2_000 / 1e6) * 3.00 + (500 / 1e6) * 15.00
    assert spent == pytest.approx(expected, abs=1e-9), f"recorded {spent}, call cost {expected}"


@pytest.mark.asyncio
async def test_a_refusal_is_not_a_crash(monkeypatch):
    """Everything before synthesis succeeded. Losing the narrative must not
    throw away the traced data that cost five lookups to gather."""
    async def must_not_run(*a, **kw):
        raise AssertionError("called despite an exhausted budget")

    node._CAP.record(1.00)
    monkeypatch.setattr(node, "_call_model", must_not_run)

    out = await node.synthesize_report(_state(liens=[], encumbrances=[]))

    assert isinstance(out, dict)
    assert out.get("report") is None


@pytest.mark.asyncio
async def test_the_cap_is_shared_across_requests(monkeypatch):
    """Per-request caps would cap nothing: every visitor gets a fresh budget
    and the monthly total is unbounded."""
    class Usage:
        input_tokens = 1_000
        output_tokens = 100

    class Block:
        type = "tool_use"
        input = {}

    class Resp:
        content = [Block()]
        usage = Usage()

    monkeypatch.setattr(node, "_call_model", lambda prompt: _coro(Resp()))

    await node.synthesize_report(_state())
    after_one = node._CAP.monthly_spend
    await node.synthesize_report(_state())

    assert node._CAP.monthly_spend > after_one


async def _coro(value):
    return value
