"""Concurrency and crash behaviour around the spend guard.

check() then call then record() leaves an await between the judgement and the
accounting, so every request in flight sees the same unspent budget. These
pin the reservation that closes it, and the fail-closed choices around it.
"""

from __future__ import annotations

import asyncio

import pytest

from titletrace.nodes import synthesize_report as node


@pytest.fixture(autouse=True)
def fresh_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "_LEDGER_PATH", tmp_path / "spend.json")
    monkeypatch.setattr(node, "_CAP", node._build_cap(monthly_budget=0.10))
    yield


def _state():
    return {
        "raw_address": "812 Morris Rd, Wayne, PA 19087", "parcel_id": None,
        "state": "PA", "city": "Wayne", "parcel": None, "ownership_history": [],
        "liens": [], "encumbrances": [], "zoning": None, "tax_status": None,
        "flood_zone": None, "lienholder_details": [], "tax_claim_detail": None,
        "report": None, "error": None,
    }


class _Resp:
    class usage:
        input_tokens = 2_000
        output_tokens = 500

    class _Block:
        type = "tool_use"
        input = {"confidence": "partial"}

    content = [_Block()]


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_all_pass_the_same_budget(monkeypatch):
    """The TOCTOU. Twenty callers arriving together must not each see an
    untouched budget and all reach the model."""
    calls = 0

    async def slow_call(prompt):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # the window the race lives in
        return _Resp()

    monkeypatch.setattr(node, "_call_model", slow_call)

    await asyncio.gather(*(node.synthesize_report(_state()) for _ in range(20)))

    # $0.10 budget against an estimate of roughly $0.0155 a call
    assert calls <= 8, f"{calls} calls got through a budget that affords about 6"


@pytest.mark.asyncio
async def test_a_failed_call_still_counts_against_the_budget(monkeypatch):
    """A timeout does not prove the tokens were never billed. Counting a failed
    call as zero lets a failing deployment spend without limit."""
    async def boom(prompt):
        raise TimeoutError("upstream gone")

    monkeypatch.setattr(node, "_call_model", boom)
    before = node._CAP.monthly_spend

    with pytest.raises(TimeoutError):
        await node.synthesize_report(_state())

    assert node._CAP.monthly_spend > before


@pytest.mark.asyncio
async def test_spend_survives_a_restart(monkeypatch, tmp_path):
    """Otherwise a crash loop grants a fresh budget on every boot, which is the
    one failure mode that can actually run up a bill."""
    async def ok(prompt):
        return _Resp()

    monkeypatch.setattr(node, "_call_model", ok)
    await node.synthesize_report(_state())
    spent = node._CAP.monthly_spend
    assert spent > 0

    # a new process, same ledger file
    restarted = node._build_cap(monthly_budget=0.10)
    node._restore_spend(restarted)

    assert restarted.monthly_spend == pytest.approx(spent, abs=1e-9)


@pytest.mark.asyncio
async def test_an_unreadable_ledger_does_not_grant_a_fresh_budget(monkeypatch, tmp_path):
    """Fail closed. A ledger that cannot be read is not evidence of no spend."""
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not json")
    monkeypatch.setattr(node, "_LEDGER_PATH", bad)

    cap = node._build_cap(monthly_budget=0.10)
    node._restore_spend(cap)

    assert cap.monthly_spend >= 0.10, "an unreadable ledger should read as spent, not as zero"
