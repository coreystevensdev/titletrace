"""synthesize_report node -- always runs, even with partial data.

Calls Claude to produce a structured TraceReport. Partial or unavailable data
surfaces as explicit data_gaps entries, never as silent omission. The prompt
forces a JSON tool call so the output is always machine-readable.
"""

from __future__ import annotations

import json
import os

import anthropic

from titletrace.state import TraceReport, TraceState

_CLIENT = None


def _get_client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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


async def synthesize_report(state: TraceState) -> dict:
    if state.get("error"):
        return {}

    client = _get_client()
    parcel = state.get("parcel")

    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=1024,
        tools=[_REPORT_TOOL],
        tool_choice={"type": "tool", "name": "submit_report"},
        messages=[{"role": "user", "content": _build_prompt(state)}],
    )

    tool_use = next(b for b in response.content if b.type == "tool_use")
    args = tool_use.input

    report = TraceReport(
        parcel_id=parcel.parcel_id if parcel else None,
        address=state["raw_address"],
        ownership_history_count=len(state.get("ownership_history") or []),
        **{k: v for k, v in args.items() if k in TraceReport.model_fields},
    )
    return {"report": report}
