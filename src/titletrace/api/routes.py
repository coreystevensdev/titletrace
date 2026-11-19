"""TitleTrace API routes.

POST /api/trace       -- run the full property trace for an address
POST /api/demo/trace  -- the same trace for the public demo, see below
GET  /health          -- liveness check

The demo route exists because the live one cannot be put on an open URL: ATTOM
allows 200 calls a month and the synthesis step spends money per request. It
runs live when both keys are configured and the budget holds, and falls back to
recorded vendor responses otherwise. Either way it names the mode it used, so a
recorded trace is never mistaken for proof the deployment can do the live one.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from titletrace.api.http_client import get_http_client
from titletrace.demo import RECORDED_ADDRESSES, demo_client
from titletrace.graph import build_graph
from titletrace.nodes.synthesize_report import _CAP, _build_prompt, synthesize_report
from titletrace.state import TraceState

router = APIRouter()


class TraceRequest(BaseModel):
    address: str


class TraceResponse(BaseModel):
    address: str
    report: dict | None
    error: str | None
    duration_ms: int


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/api/trace", response_model=TraceResponse)
async def trace(req: TraceRequest):
    if not req.address or not req.address.strip():
        raise HTTPException(status_code=422, detail="address is required")

    client = get_http_client()
    graph = build_graph(client)

    initial: TraceState = {
        "raw_address": req.address.strip(),
        "parcel_id": None,
        "state": None,
        "city": None,
        "parcel": None,
        "ownership_history": [],
        "liens": [],
        "encumbrances": [],
        "zoning": None,
        "tax_status": None,
        "flood_zone": None,
        "lienholder_details": [],
        "tax_claim_detail": None,
        "report": None,
        "error": None,
    }

    t0 = time.monotonic()
    result: TraceState = await graph.ainvoke(initial)
    duration_ms = int((time.monotonic() - t0) * 1000)

    report = result.get("report")
    return TraceResponse(
        address=req.address,
        report=report.model_dump() if report else None,
        error=result.get("error"),
        duration_ms=duration_ms,
    )


def _blank_state(address: str) -> TraceState:
    return {
        "raw_address": address, "parcel_id": None, "state": None, "city": None,
        "parcel": None, "ownership_history": [], "liens": [], "encumbrances": [],
        "zoning": None, "tax_status": None, "flood_zone": None,
        "lienholder_details": [], "tax_claim_detail": None, "report": None,
        "error": None,
    }


def _live_is_available() -> tuple[bool, str]:
    """Both keys and budget, or it is not live. Reported as a sentence because
    the page shows it: 'recorded' with no reason invites the reader to guess."""
    if not os.environ.get("ATTOM_API_KEY"):
        return False, "No ATTOM key configured, so vendor lookups are served from recordings."
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "No Anthropic key configured, so the report is not generated."
    verdict = _CAP.check(0.0)
    if not verdict.allowed:
        return False, (
            f"This month's model budget of ${_CAP_BUDGET:.2f} is spent, "
            "so the trace is served from recordings until it resets."
        )
    return True, "Live vendor lookups, with the model budget still open."


_CAP_BUDGET = float(os.environ.get("TITLETRACE_MONTHLY_BUDGET_USD", "5.00"))


@router.post("/api/demo/trace")
async def demo_trace(req: TraceRequest):
    address = (req.address or "").strip()
    if not address:
        raise HTTPException(status_code=422, detail="address is required")

    live, reason = _live_is_available()
    t0 = time.monotonic()

    if live:
        graph = build_graph(get_http_client(), synthesize=synthesize_report)
        result = await graph.ainvoke(_blank_state(address))
        client_cm = None
    else:
        async def skip_synthesis(state):
            return {}

        client_cm = demo_client()
        async with client_cm as client:
            graph = build_graph(client, synthesize=skip_synthesis)
            result = await graph.ainvoke(_blank_state(address))

    duration_ms = int((time.monotonic() - t0) * 1000)
    parcel = result.get("parcel")
    error = result.get("error")
    # In recorded mode a missing parcel always means a missing recording, so the
    # graph's own "verify the address" message is replaced rather than kept. It
    # would tell someone their perfectly good address was wrong.
    if not live and parcel is None:
        error = (
            f"No recorded trace for {address!r}. This demo runs on recorded vendor "
            "responses, so it only covers: " + "; ".join(RECORDED_ADDRESSES) + "."
        )

    report = result.get("report")
    return {
        "address": address,
        "mode": "live" if live else "recorded",
        "mode_reason": reason,
        "duration_ms": duration_ms,
        "parcel": parcel.model_dump() if parcel else None,
        "ownership_history": [o.model_dump() for o in result.get("ownership_history") or []],
        "liens": [l.model_dump() for l in result.get("liens") or []],
        "encumbrances": [e.model_dump() for e in result.get("encumbrances") or []],
        "zoning": result["zoning"].model_dump() if result.get("zoning") else None,
        "tax_status": result["tax_status"].model_dump() if result.get("tax_status") else None,
        "flood_zone": result["flood_zone"].model_dump() if result.get("flood_zone") else None,
        "lienholder_details": [d.model_dump() for d in result.get("lienholder_details") or []],
        "tax_claim_detail": (
            result["tax_claim_detail"].model_dump() if result.get("tax_claim_detail") else None
        ),
        "report": report.model_dump() if report else None,
        # Shown in place of a synthesized report when there is no key. Real
        # assembly on real state, rather than a sentence describing one.
        "prompt": _build_prompt(result) if not error else None,
        "budget": {
            "monthly_budget_usd": _CAP_BUDGET,
            "monthly_spend_usd": round(_CAP.monthly_spend, 4),
        },
        "error": error,
    }
