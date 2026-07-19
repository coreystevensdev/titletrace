"""Direct tests for the internal fan-out node functions in graph.py.

These test the node functions themselves (not the compiled graph), since
they are plain async functions that take (state, client) and are simple to
exercise in isolation -- matching the existing per-node test style in
tests/nodes/.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from titletrace.clients.attom import _ATTOM_BASE
from titletrace.clients.fema import _FEMA_BASE
from titletrace.graph import _fetch_flood_zone
from titletrace.state import ParcelResult


def _parcel(latitude=None, longitude=None) -> ParcelResult:
    return ParcelResult(
        parcel_id="NJ-001-ABC", address="100 Broad St", city="Trenton", state="NJ",
        zip_code="08608", latitude=latitude, longitude=longitude, source="ATTOM",
    )


@pytest.mark.asyncio
async def test_fetch_flood_zone_resolves_when_coordinates_present():
    fema_payload = {
        "features": [{"attributes": {
            "FLD_ZONE": "AE", "ZONE_SUBTY": "", "FIRM_PANEL": "34021C0123D", "EFF_DATE": "2015-01-01",
        }}]
    }
    state = {"parcel": _parcel(latitude=40.2216, longitude=-74.7623)}
    with respx.mock:
        respx.get(_FEMA_BASE).mock(return_value=httpx.Response(200, json=fema_payload))
        async with httpx.AsyncClient() as client:
            result = await _fetch_flood_zone(state, client)

    assert result["flood_zone"] is not None
    assert result["flood_zone"].zone_designation == "AE"


@pytest.mark.asyncio
async def test_fetch_flood_zone_omitted_when_coordinates_missing():
    state = {"parcel": _parcel(latitude=None, longitude=None)}
    async with httpx.AsyncClient() as client:
        result = await _fetch_flood_zone(state, client)
    assert result["flood_zone"] is None


@pytest.mark.asyncio
async def test_fetch_flood_zone_omitted_when_no_parcel():
    state = {"parcel": None}
    async with httpx.AsyncClient() as client:
        result = await _fetch_flood_zone(state, client)
    assert result["flood_zone"] is None
