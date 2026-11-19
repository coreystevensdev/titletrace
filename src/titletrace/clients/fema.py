"""FEMA NFHL ArcGIS REST client for flood zone designation.

Uses the public FEMA National Flood Hazard Layer endpoint, no API key
required. Queries by longitude/latitude after geocoding the address.

FEMA endpoint docs:
  https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer

The zone and the panel come from two different layers. 28 (Flood Hazard Zones)
has FLD_ZONE and ZONE_SUBTY; the FIRM panel id and its effective date are on 3
(FIRM Panels). Asking 28 for them returns the feature with those columns absent,
so the panel fields came back null on every lookup that otherwise worked.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from titletrace.clients._base import get_json
from titletrace.state import FloodZoneResult

logger = logging.getLogger(__name__)

_NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
_FEMA_BASE = f"{_NFHL}/28/query"
_PANEL_BASE = f"{_NFHL}/3/query"

# Zone description lookup for common FEMA designations.
_ZONE_DESCRIPTIONS: dict[str, str] = {
    "A": "High risk, 1% annual chance flood, no base flood elevation determined",
    "AE": "High risk, 1% annual chance flood with base flood elevation",
    "AH": "High risk, 1% annual chance flood, shallow ponding",
    "AO": "High risk, 1% annual chance flood, shallow sheet flow",
    "V": "High risk coastal, 1% annual chance with wave action",
    "VE": "High risk coastal, 1% annual chance with wave action and base flood elevation",
    "X": "Minimal to moderate risk, outside the 1% annual chance floodplain",
    "D": "Undetermined risk",
}


def _sentence_case(text: str) -> str:
    """FEMA ships these in caps. Title case puts a capital on "Of"."""
    return text[0].upper() + text[1:].lower() if text else text


def _zone_description(zone: str) -> str:
    """Longest prefix wins. Shortest-first matched AE against A and reported
    "no BFE determined" for a zone that has one, which is backwards on the
    question a flood determination is asked."""
    zone = zone.upper()
    for prefix in sorted(_ZONE_DESCRIPTIONS, key=len, reverse=True):
        if zone.startswith(prefix):
            return _ZONE_DESCRIPTIONS[prefix]
    return "See FEMA FIRM panel for full designation"


def _esri_date(value) -> str | None:
    """ArcGIS returns date columns as epoch milliseconds, and the model wants a
    string. Passing the integer through raises a ValidationError that surfaces
    as a dead trace, since only fetch_parcel catches node exceptions."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()


async def _fetch_panel(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    """FIRM panel id and effective date, from layer 3. Provenance for the zone
    above it, so a failure here must not discard the zone itself."""
    try:
        data = await get_json(client, _PANEL_BASE, params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FIRM_PAN,EFF_DATE",
            "returnGeometry": "false",
            "f": "json",
        })
    except Exception:
        logger.warning("fema_panel_lookup_failed lat=%s lon=%s", lat, lon)
        return {}
    features = data.get("features", [])
    return features[0].get("attributes", {}) if features else {}


async def fetch_flood_zone(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> FloodZoneResult | None:
    """Query FEMA NFHL for the flood zone at the given coordinates.

    Returns None when the point falls outside all FIRM panels (very rare for
    PA/NJ addresses).
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY",
        "returnGeometry": "false",
        "f": "json",
    }
    data = await get_json(client, _FEMA_BASE, params=params)
    features = data.get("features", [])
    if not features:
        return None
    attrs = features[0].get("attributes", {})
    # FLD_ZONE already carries the whole designation: AE arrives as "AE". The
    # old code appended ZONE_SUBTY to it, but that column is a sentence, so
    # zone X came out as "XAREA OF MINIMAL FLOOD HAZARD".
    zone = (attrs.get("FLD_ZONE") or "").strip()
    subtype = (attrs.get("ZONE_SUBTY") or "").strip()

    panel = await _fetch_panel(client, lat, lon)
    return FloodZoneResult(
        zone_designation=zone or "Unknown",
        zone_description=_sentence_case(subtype) if subtype else _zone_description(zone),
        firm_panel=panel.get("FIRM_PAN") or panel.get("FIRM_PANEL"),
        effective_date=_esri_date(panel.get("EFF_DATE")),
    )
