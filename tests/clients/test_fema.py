import pytest
import respx
import httpx

from titletrace.clients.fema import fetch_flood_zone, _FEMA_BASE


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.mark.asyncio
async def test_fetch_flood_zone_high_risk(client):
    payload = {
        "features": [
            {
                "attributes": {
                    "FLD_ZONE": "AE",
                    "ZONE_SUBTY": "",
                    "FIRM_PANEL": "42101C0277F",
                    "EFF_DATE": "2013-09-27",
                }
            }
        ]
    }
    with respx.mock:
        respx.get(_FEMA_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_flood_zone(client, lat=39.95, lon=-75.16)

    assert result is not None
    assert result.zone_designation == "AE"
    assert "1% annual chance" in result.zone_description
    assert result.firm_panel == "42101C0277F"
    assert result.source == "FEMA NFHL ArcGIS REST"


@pytest.mark.asyncio
async def test_fetch_flood_zone_minimal_risk(client):
    payload = {
        "features": [
            {
                "attributes": {
                    "FLD_ZONE": "X",
                    "ZONE_SUBTY": None,
                    "FIRM_PANEL": None,
                    "EFF_DATE": None,
                }
            }
        ]
    }
    with respx.mock:
        respx.get(_FEMA_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_flood_zone(client, lat=40.02, lon=-75.18)

    assert result is not None
    assert result.zone_designation == "X"
    assert "Minimal" in result.zone_description


@pytest.mark.asyncio
async def test_fetch_flood_zone_no_features_returns_none(client):
    payload = {"features": []}
    with respx.mock:
        respx.get(_FEMA_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_flood_zone(client, lat=39.95, lon=-75.16)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_flood_zone_with_subtype(client):
    payload = {
        "features": [
            {
                "attributes": {
                    "FLD_ZONE": "A",
                    "ZONE_SUBTY": "FLOODWAY",
                    "FIRM_PANEL": None,
                    "EFF_DATE": None,
                }
            }
        ]
    }
    with respx.mock:
        respx.get(_FEMA_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_flood_zone(client, lat=39.9, lon=-75.2)

    assert result is not None
    assert "A" in result.zone_designation
