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

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from titletrace.api.http_client import get_http_client
from titletrace.demo import RECORDED_ADDRESSES, demo_client
from titletrace.graph import build_graph
from titletrace.nodes.synthesize_report import _CAP, _build_prompt, synthesize_report
from titletrace.state import TraceState

logger = logging.getLogger(__name__)

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


# ATTOM allows 200 calls a month on the free tier and charges nothing per call,
# so the dollar cap cannot see it. A worst-case trace makes 6 ATTOM calls,
# both drill-downs included, so 30 live traces is up to 180, leaving little
# headroom for development against the 200 ceiling.
_LIVE_TRACES_PER_MONTH = int(os.environ.get("TITLETRACE_LIVE_TRACES_PER_MONTH", "30"))
_QUOTA_PATH = Path(os.environ.get("TITLETRACE_QUOTA_LEDGER", "/tmp/titletrace-quota.json"))


# The file is where the count survives a restart; this is where it survives a
# disk that will not take writes. Reading the count back from storage alone
# means a failed write leaves it at zero forever, and live mode never stops.
_counted_this_process = 0
_counted_month = ""
_quota_unwritable = False


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _live_traces_used() -> int:
    """The higher of what this process counted and what the file says.

    Neither alone is enough: the file misses a write that failed, and the
    process counter misses everything from before the last restart.
    """
    month = _this_month()
    in_process = _counted_this_process if _counted_month == month else 0

    try:
        saved = json.loads(_QUOTA_PATH.read_text())
    except FileNotFoundError:
        on_disk = 0
    except (OSError, ValueError):
        # Unreadable is not evidence of unused.
        return _LIVE_TRACES_PER_MONTH
    else:
        on_disk = int(saved.get("traces") or 0) if saved.get("month") == month else 0

    return max(in_process, on_disk)


def _record_live_trace() -> None:
    """Read, increment, write. Keep this synchronous.

    Its atomicity is only the event loop's: with no await inside it, it runs to
    completion before another request is served. Adding one, async file I/O for
    instance, reintroduces the read-modify-write race silently, and it would
    need the same reservation the spend cap uses.
    """
    global _counted_this_process, _counted_month, _quota_unwritable

    month = _this_month()
    if _counted_month != month:
        _counted_month, _counted_this_process, _quota_unwritable = month, 0, False
    _counted_this_process = _live_traces_used() + 1

    try:
        _QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _QUOTA_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"month": month, "traces": _counted_this_process}))
        tmp.replace(_QUOTA_PATH)
    except OSError:
        # A quota that cannot be persisted is a quota that will be forgotten on
        # the next restart, so stop handing out live traces rather than hand out
        # an unbounded number of them.
        logger.warning("quota_ledger_write_failed path=%s, live mode disabled", _QUOTA_PATH)
        _quota_unwritable = True


def _live_is_available() -> tuple[bool, str]:
    """Both keys and budget, or it is not live. Reported as a sentence because
    the page shows it: 'recorded' with no reason invites the reader to guess."""
    if not os.environ.get("ATTOM_API_KEY"):
        return False, "No ATTOM key configured, so vendor lookups are served from recordings."
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "No Anthropic key configured, so the report is not generated."
    if _quota_unwritable:
        return False, (
            "The live-trace ledger cannot be written, so live mode is off until that is "
            "fixed. An uncounted quota is one that gets forgotten on the next restart."
        )
    used = _live_traces_used()
    if used >= _LIVE_TRACES_PER_MONTH:
        return False, (
            f"This month's live-trace share is used ({used} of {_LIVE_TRACES_PER_MONTH}), "
            "so the trace is served from recordings. ATTOM's free tier is the quota being "
            "protected, not a dollar budget."
        )
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
        # Counted before the lookups, not after. A trace that crashes partway
        # has still spent the ATTOM calls it made.
        _record_live_trace()
        graph = build_graph(get_http_client(), synthesize=synthesize_report)
        result = await graph.ainvoke(_blank_state(address))
    else:
        async def skip_synthesis(state):
            return {}

        async with demo_client() as client:
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
