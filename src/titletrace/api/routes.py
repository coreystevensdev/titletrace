"""TitleTrace API routes.

POST /api/trace   -- run the full property trace for an address
GET  /health      -- liveness check
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from titletrace.api.main import get_http_client
from titletrace.graph import build_graph
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
