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
def fresh_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "_LEDGER_PATH", tmp_path / "spend.json")
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
async def test_the_reserved_cost_is_the_estimate_not_the_measured_usage(monkeypatch):
    """This asserted the measured usage until the reservation landed.

    Recording what the call actually used means recording it after the call,
    and the await in between is the window every concurrent request passes
    through on the same unspent budget. Reserving first closes that, and the
    ledger is append-only so the over-reservation cannot be corrected back
    down. The estimate assumes the full max_tokens, so the budget drains ahead
    of real spend, which is the direction worth being wrong in."""
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
    measured = (2_000 / 1e6) * 3.00 + (500 / 1e6) * 15.00
    assert spent == pytest.approx(node._estimate(node._build_prompt(_state())), abs=1e-9)
    assert spent > measured, "the reservation must not undercount what the call could cost"


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
