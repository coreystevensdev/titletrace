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

from titletrace.clients.fema import _FEMA_BASE
from titletrace.graph import _determine_tax_status, _fetch_flood_zone
from titletrace.state import LienResult, ParcelResult


def _parcel(latitude=None, longitude=None) -> ParcelResult:
    return ParcelResult(
        parcel_id="NJ-001-ABC", address="100 Broad St", city="Trenton", state="NJ",
        zip_code="08608", latitude=latitude, longitude=longitude, source="ATTOM",
    )


def _lien(lien_type: str, amount: float | None = None) -> LienResult:
    return LienResult(lien_type=lien_type, amount=amount, status="active", source="ATTOM")


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


@pytest.mark.asyncio
async def test_determine_tax_status_non_philly_delinquent_from_tax_lien():
    state = {
        "city": "Trenton", "state": "NJ",
        "parcel": _parcel(),
        "liens": [_lien("mortgage"), _lien("taxlien", amount=4200.0)],
    }
    async with httpx.AsyncClient() as client:
        result = await _determine_tax_status(state, client)

    assert result["tax_status"] is not None
    assert result["tax_status"].is_delinquent is True
    assert result["tax_status"].balance_due == pytest.approx(4200.0)
    assert result["tax_status"].source == "ATTOM (derived from lien records)"


@pytest.mark.asyncio
async def test_determine_tax_status_non_philly_not_delinquent_with_no_tax_lien():
    state = {
        "city": "Trenton", "state": "NJ",
        "parcel": _parcel(),
        "liens": [_lien("mortgage")],
    }
    async with httpx.AsyncClient() as client:
        result = await _determine_tax_status(state, client)

    assert result["tax_status"] is not None
    assert result["tax_status"].is_delinquent is False
    assert result["tax_status"].balance_due is None


@pytest.mark.asyncio
async def test_determine_tax_status_no_parcel_is_none():
    state = {"city": "Trenton", "state": "NJ", "parcel": None, "liens": []}
    async with httpx.AsyncClient() as client:
        result = await _determine_tax_status(state, client)
    assert result["tax_status"] is None


@pytest.mark.asyncio
async def test_determine_tax_status_philadelphia_uses_opa():
    from titletrace.clients.opa import _TAX_ENDPOINT

    opa_payload = [{"total_due": "1500.00", "last_year_paid": "2022"}]
    state = {
        "city": "Philadelphia", "state": "PA",
        "parcel": ParcelResult(
            parcel_id="884000100", address="1234 Market St", city="Philadelphia",
            state="PA", zip_code="19107", source="Philadelphia OPA",
        ),
        "liens": [],
    }
    with respx.mock:
        respx.get(_TAX_ENDPOINT).mock(return_value=httpx.Response(200, json=opa_payload))
        async with httpx.AsyncClient() as client:
            result = await _determine_tax_status(state, client)

    assert result["tax_status"] is not None
    assert result["tax_status"].is_delinquent is True
    assert result["tax_status"].balance_due == pytest.approx(1500.00)
    assert result["tax_status"].source == "Philadelphia OPA Real Estate Tax"
