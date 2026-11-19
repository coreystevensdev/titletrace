"""What the live NFHL service actually exposes.

These pin the two facts that silently broke the flood lookup: the service path
moved, and FIRM_PANEL/EFF_DATE are not on the flood-zone layer. Both failed in
ways that produced no error, just a null flood zone on every report.
"""

from __future__ import annotations

import httpx
import pytest

from titletrace.clients.fema import _FEMA_BASE, _PANEL_BASE, fetch_flood_zone


def test_the_base_url_is_the_arcgis_path_not_the_retired_one():
    """hazards.fema.gov/gis/nfhl/rest 404s through FEMA's WebSEAL. Checked
    2026-11-19: the host answers 200 at /, and the whole /gis/ tree is gone."""
    assert "/arcgis/rest/services/public/NFHL/MapServer" in _FEMA_BASE
    assert "/gis/nfhl/rest/" not in _FEMA_BASE


def test_the_zone_query_asks_only_for_fields_layer_28_has():
    """Layer 28 carries FLD_ZONE and ZONE_SUBTY. FIRM_PANEL and EFF_DATE live
    on layer 3, so asking 28 for them returns nothing for those two columns."""
    import inspect
    from titletrace.clients import fema

    src = inspect.getsource(fema.fetch_flood_zone)
    outfields = [l for l in src.splitlines() if "outFields" in l and "FLD_ZONE" in l]
    assert outfields, "zone query no longer names its outFields"
    assert "FIRM_PANEL" not in outfields[0]
    assert "EFF_DATE" not in outfields[0]


def test_the_panel_query_targets_layer_3():
    assert _PANEL_BASE.rstrip("/").endswith("/3/query")


@pytest.mark.asyncio
async def test_panel_dates_arrive_as_epoch_millis_and_become_strings():
    """ArcGIS returns date fields as integer epoch milliseconds. The model types
    effective_date as a string, so passing it through raises ValidationError."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/28/query" in request.url.path:
            return httpx.Response(200, json={"features": [
                {"attributes": {"FLD_ZONE": "AE", "ZONE_SUBTY": ""}}
            ]})
        return httpx.Response(200, json={"features": [
            {"attributes": {"FIRM_PANEL": "34007C0084G", "EFF_DATE": 1551398400000}}
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_flood_zone(client, 39.8914, -75.0376)

    assert result is not None
    assert result.zone_designation == "AE"
    assert result.firm_panel == "34007C0084G"
    assert result.effective_date == "2019-03-01", result.effective_date


@pytest.mark.asyncio
async def test_a_failed_panel_lookup_still_returns_the_zone():
    """The zone is the answer people need; the panel is provenance. Losing the
    second call must not discard the first."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/28/query" in request.url.path:
            return httpx.Response(200, json={"features": [
                {"attributes": {"FLD_ZONE": "X", "ZONE_SUBTY": ""}}
            ]})
        return httpx.Response(500, json={"error": "panel layer down"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_flood_zone(client, 40.0449, -75.3878)

    assert result is not None
    assert result.zone_designation == "X"
    assert result.firm_panel is None


@pytest.mark.asyncio
async def test_the_subtype_describes_the_zone_rather_than_extending_its_name():
    """FLD_ZONE already carries the full designation: AE arrives as "AE", not
    as "A" plus "E". ZONE_SUBTY is a sentence, so concatenating produced
    designations like "XAREA OF MINIMAL FLOOD HAZARD". Live values on
    2026-11-19 were "AREA OF MINIMAL FLOOD HAZARD" and
    "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/28/query" in request.url.path:
            return httpx.Response(200, json={"features": [{"attributes": {
                "FLD_ZONE": "X", "ZONE_SUBTY": "AREA OF MINIMAL FLOOD HAZARD",
            }}]})
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_flood_zone(client, 39.95258, -75.16352)

    assert result.zone_designation == "X"
    assert result.zone_description == "Area of minimal flood hazard"


@pytest.mark.asyncio
async def test_a_high_risk_designation_survives_intact():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/28/query" in request.url.path:
            return httpx.Response(200, json={"features": [{"attributes": {
                "FLD_ZONE": "AE", "ZONE_SUBTY": None,
            }}]})
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_flood_zone(client, 39.89, -75.03)

    assert result.zone_designation == "AE"
    assert "base flood elevation" in result.zone_description
