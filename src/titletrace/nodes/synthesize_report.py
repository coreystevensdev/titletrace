"""synthesize_report node -- always runs, even with partial data.

Calls Claude to produce a structured TraceReport. Partial or unavailable data
surfaces as explicit data_gaps entries, never as silent omission. The prompt
forces a JSON tool call so the output is always machine-readable.
"""

from __future__ import annotations

import json
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from rolling_cost_cap import CostCap

logger = logging.getLogger(__name__)

from titletrace.state import TraceReport, TraceState

_CLIENT = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _CLIENT


_REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit the synthesized property title report.",
    "input_schema": {
        "type": "object",
        "required": [
            "current_owner",
            "lien_count",
            "lien_summary",
            "encumbrance_count",
            "tax_delinquent",
            "zoning",
            "flood_zone",
            "findings",
            "data_gaps",
            "confidence",
        ],
        "properties": {
            "current_owner": {"type": ["string", "null"]},
            "lien_count": {"type": "integer"},
            "lien_summary": {"type": ["string", "null"]},
            "encumbrance_count": {"type": "integer"},
            "tax_delinquent": {"type": ["boolean", "null"]},
            "tax_balance_due": {"type": ["number", "null"]},
            "zoning": {"type": ["string", "null"]},
            "flood_zone": {"type": ["string", "null"]},
            "estimated_title_premium_usd": {"type": ["number", "null"]},
            "findings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Notable findings that affect title insurability.",
            },
            "data_gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data sources that returned no results or were unavailable.",
            },
            "confidence": {
                "type": "string",
                "enum": ["full", "partial", "minimal"],
            },
        },
    },
}


def _build_prompt(state: TraceState) -> str:
    parcel = state.get("parcel")
    liens = state.get("liens") or []
    encumbrances = state.get("encumbrances") or []
    ownership = state.get("ownership_history") or []
    tax = state.get("tax_status")
    zoning = state.get("zoning")
    flood = state.get("flood_zone")

    sections = [
        f"Address: {state['raw_address']}",
        f"Parcel ID: {parcel.parcel_id if parcel else 'not found'}",
        f"Current owner (from parcel lookup): {parcel.owner_name if parcel else 'unknown'}",
        f"Ownership history records: {len(ownership)}",
        f"Liens found: {len(liens)}",
    ]
    for lien in liens:
        sections.append(
            f"  - {lien.lien_type}: ${lien.amount or 'unknown'}, status={lien.status}, holder={lien.lienholder or 'unknown'}"
        )
    sections.append(f"Encumbrances found: {len(encumbrances)}")
    sections.append(
        f"Tax status: {'delinquent, $' + str(tax.balance_due) if tax and tax.is_delinquent else 'current' if tax else 'unavailable'}"
    )
    sections.append(
        f"Zoning: {zoning.zoning_code + ' (' + (zoning.zoning_description or '') + ')' if zoning else 'unavailable'}"
    )
    sections.append(
        f"Flood zone: {flood.zone_designation + ' -- ' + flood.zone_description if flood else 'unavailable'}"
    )

    return (
        "You are a title search analyst. Summarize the following property data into a "
        "structured title report. Call submit_report with your findings. Be factual and "
        "precise. List every gap in data_gaps. Set confidence to 'full' only if all six "
        "data categories returned results.\n\n"
        + "\n".join(sections)
    )


# Sonnet 4.6 list price. Wrong rates would make the budget a number that means
# nothing, so they are named here rather than buried in an expression.
_INPUT_PER_MTOK = 3.00
_OUTPUT_PER_MTOK = 15.00
_MAX_OUTPUT_TOKENS = 1024


def _build_cap(monthly_budget: float | None = None) -> CostCap:
    return CostCap(
        multiplier=3.0,
        window=50,
        min_samples=5,
        absolute_ceiling=float(os.environ.get("TITLETRACE_CALL_CEILING_USD", "0.25")),
        monthly_budget=monthly_budget
        if monthly_budget is not None
        else float(os.environ.get("TITLETRACE_MONTHLY_BUDGET_USD", "5.00")),
    )


# One cap for the process, not one per request. A per-request cap caps nothing:
# every visitor would arrive with a fresh budget.
_CAP = _build_cap()

# check() and record() are each locked, but a caller doing one then the other
# leaves an await in between, and every request in flight reads the same
# unspent budget. This serialises judge-and-reserve only; the model call
# happens outside it, so concurrency survives.
_RESERVE = asyncio.Lock()

_LEDGER_PATH = Path(os.environ.get("TITLETRACE_SPEND_LEDGER", "/tmp/titletrace-spend.json"))


def _restore_spend(cap: CostCap) -> None:
    """Carry this month's spend across a restart.

    Without it a crash loop hands out a fresh budget on every boot, which is
    the one failure here that can actually run up a bill. An unreadable ledger
    is treated as a spent budget rather than an empty one: a file we cannot
    read is not evidence that nothing was spent.
    """
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        saved = json.loads(_LEDGER_PATH.read_text())
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.warning("spend_ledger_unreadable path=%s, assuming the budget is spent", _LEDGER_PATH)
        cap.record(cap._monthly_budget or 0.0)
        return

    if saved.get("month") != month:
        return
    amount = float(saved.get("spend") or 0.0)
    if amount > 0:
        # Replayed as one entry. It restores the monthly total, which is the
        # layer that matters here; the median window sees a single outlier.
        cap.record(amount)


def _persist_spend(cap: CostCap) -> None:
    payload = {
        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "spend": round(cap.monthly_spend, 6),
    }
    try:
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(_LEDGER_PATH)
    except OSError:
        logger.warning("spend_ledger_write_failed path=%s", _LEDGER_PATH)


_restore_spend(_CAP)


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1e6) * _INPUT_PER_MTOK + (output_tokens / 1e6) * _OUTPUT_PER_MTOK


def _estimate(prompt: str) -> float:
    """Pre-flight figure. Roughly four characters per token on the way in, and
    the full max_tokens on the way out, because the only safe estimate for a
    call that has not happened is the most expensive one it could be."""
    return _cost(len(prompt) // 4, _MAX_OUTPUT_TOKENS)


async def _call_model(prompt: str):
    client = _get_client()
    return await client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=_MAX_OUTPUT_TOKENS,
        tools=[_REPORT_TOOL],
        tool_choice={"type": "tool", "name": "submit_report"},
        messages=[{"role": "user", "content": prompt}],
    )


async def synthesize_report(state: TraceState) -> dict:
    if state.get("error"):
        return {}

    parcel = state.get("parcel")
    prompt = _build_prompt(state)

    # Judge and reserve together, before the call. evaluate() is the wrong tool
    # twice over: it records a cost already spent, and it records even when it
    # refuses, so a budget at its limit keeps inflating on requests it denied.
    estimate = _estimate(prompt)
    async with _RESERVE:
        verdict = _CAP.check(estimate)
        if not verdict.allowed:
            logger.warning(
                "synthesis_refused trip=%s observed=%.4f monthly_spend=%.4f",
                verdict.trip, verdict.observed, verdict.monthly_spend,
            )
            return {"error": (
                "Report generation is paused: this deployment's monthly model budget is spent. "
                "The property data above was still gathered in full."
            )}
        _CAP.record(estimate)
        _persist_spend(_CAP)

    # Reserved rather than reconciled afterwards, because the ledger is
    # append-only and a correction would have to be negative. The estimate
    # assumes the full max_tokens, so the budget drains faster than real spend.
    # That is the direction to be wrong in, and it means a call that raises is
    # already paid for: a timeout is not proof the tokens went unbilled.
    response = await _call_model(prompt)

    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        return {"error": "Model did not return a structured report. Retry the request."}
    tool_use = tool_use_blocks[0]
    args = tool_use.input

    report = TraceReport(
        parcel_id=parcel.parcel_id if parcel else None,
        address=state["raw_address"],
        ownership_history_count=len(state.get("ownership_history") or []),
        **{k: v for k, v in args.items() if k in TraceReport.model_fields},
    )
    return {"report": report}
