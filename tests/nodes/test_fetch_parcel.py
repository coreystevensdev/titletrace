import httpx
import pytest
import respx

from titletrace.clients.attom import _ATTOM_BASE
from titletrace.nodes.fetch_parcel import fetch_parcel

PARCEL_DETAIL_URL = f"{_ATTOM_BASE}/property/detail"


@pytest.fixture(autouse=True)
def set_attom_key(monkeypatch):
    monkeypatch.setenv("ATTOM_API_KEY", "test-key")


@pytest.fixture
def client():
    return httpx.AsyncClient()


def _state(address: str) -> dict:
    return {
        "raw_address": address,
        "parcel_id": None,
        "state": "NJ",
        "city": "Trenton",
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


@pytest.mark.asyncio
async def test_fetch_parcel_success(client):
    payload = {
        "property": [
            {
                "identifier": {"apn": "NJ-001-ABC"},
                "address": {
                    "line1": "100 Broad St",
                    "city": "Trenton",
                    "countrySubd": "NJ",
                    "postal1": "08608",
                },
                "lot": {},
                "summary": {},
                "owner": {"owner1": {"fullName": "JOHNSON ALICE"}},
            }
        ]
    }
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert result["parcel_id"] == "NJ-001-ABC"
    assert result["parcel"].source == "ATTOM"


@pytest.mark.asyncio
async def test_empty_property_is_not_found(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json={"property": []}))
        result = await fetch_parcel(_state("0 Nowhere Rd, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" in result["error"]


@pytest.mark.asyncio
async def test_attom_401_is_config_error_not_not_found(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(401, json={}))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" not in result["error"]
    assert "misconfigured" in result["error"]
    assert "ATTOM_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_attom_403_is_config_error(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(403, json={}))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" not in result["error"]
    assert "403" in result["error"]


@pytest.mark.asyncio
async def test_attom_500_is_upstream_error(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(500, json={}))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" not in result["error"]
    assert "upstream" in result["error"]


@pytest.mark.asyncio
async def test_attom_404_still_reads_as_not_found(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(404, json={}))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" in result["error"]


@pytest.mark.asyncio
async def test_attom_network_error_is_upstream_error(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(side_effect=httpx.ConnectError("boom"))
        result = await fetch_parcel(_state("100 Broad St, Trenton, NJ 08608"), client)

    assert "parcel" not in result
    assert "Parcel not found" not in result["error"]
    assert "network or upstream" in result["error"]
