"""fetch_parcel node -- routes to OPA (Philadelphia) or ATTOM (all others).

Philadelphia addresses use the free OPA API. All other PA counties and all NJ
addresses use ATTOM as the primary source. If the parcel is not found in either
source, the node sets an error and the graph terminates without synthesizing.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

from titletrace.clients.attom import fetch_parcel_attom
from titletrace.clients.opa import fetch_parcel_opa
from titletrace.state import TraceState


def _is_philadelphia(state: TraceState) -> bool:
    city = (state.get("city") or "").lower()
    return state.get("state") == "PA" and ("philadelphia" in city or "philly" in city)


def _street_and_citystate(raw_address: str) -> tuple[str, str]:
    """Split '123 Main St, Philadelphia, PA 19103' into ('123 Main St', 'Philadelphia, PA 19103')."""
    parts = raw_address.split(",", 1)
    street = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return street, rest


async def fetch_parcel(state: TraceState, client: httpx.AsyncClient) -> dict:
    if state.get("error"):
        return {}

    raw = state["raw_address"]
    is_philly = _is_philadelphia(state)

    parcel = None
    if is_philly:
        street, _ = _street_and_citystate(raw)
        parcel = await fetch_parcel_opa(client, street)

    if parcel is None:
        street, citystate = _street_and_citystate(raw)
        try:
            parcel = await fetch_parcel_attom(client, street, citystate)
        except Exception as exc:
            logger.warning("ATTOM parcel fetch failed for %r: %s", citystate, exc)
            parcel = None

    if parcel is None:
        return {
            "error": (
                f"Parcel not found for '{raw}'. "
                "Verify the address and try again. "
                "Rural routes and PO boxes are not supported."
            )
        }

    return {"parcel": parcel, "parcel_id": parcel.parcel_id}
