"""FEMA NFHL ArcGIS REST client for flood zone designation.

Uses the public FEMA National Flood Hazard Layer endpoint -- no API key
required. Queries by longitude/latitude after geocoding the address.

FEMA endpoint docs:
  https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer
"""

from __future__ import annotations

import httpx

from titletrace.clients._base import get_json
from titletrace.state import FloodZoneResult

_FEMA_BASE = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"

# Zone description lookup for common FEMA designations.
_ZONE_DESCRIPTIONS: dict[str, str] = {
    "A": "High risk -- 1% annual chance flood (no BFE determined)",
    "AE": "High risk -- 1% annual chance flood with base flood elevation",
    "AH": "High risk -- 1% annual chance flood, shallow ponding",
    "AO": "High risk -- 1% annual chance flood, shallow sheet flow",
    "V": "High risk coastal -- 1% annual chance with wave action",
    "VE": "High risk coastal -- 1% annual chance with wave action and BFE",
    "X": "Minimal/moderate risk -- outside 1% annual chance floodplain",
    "D": "Undetermined risk",
}


def _zone_description(zone: str) -> str:
    for prefix, desc in _ZONE_DESCRIPTIONS.items():
        if zone.upper().startswith(prefix):
            return desc
    return "See FEMA FIRM panel for full designation"


async def fetch_flood_zone(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> FloodZoneResult | None:
    """Query FEMA NFHL for the flood zone at the given coordinates.

    Returns None when the point falls outside all FIRM panels (very rare for
    PA/NJ addresses). Returns a FloodZoneResult with the zone designation and
    effective date when found.
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,FIRM_PANEL,EFF_DATE",
        "returnGeometry": "false",
        "f": "json",
    }
    data = await get_json(client, _FEMA_BASE, params=params)
    features = data.get("features", [])
    if not features:
        return None
    attrs = features[0].get("attributes", {})
    zone = attrs.get("FLD_ZONE", "")
    subtype = attrs.get("ZONE_SUBTY", "") or ""
    full_zone = f"{zone}{subtype}".strip() if subtype else zone
    return FloodZoneResult(
        zone_designation=full_zone or "Unknown",
        zone_description=_zone_description(full_zone),
        firm_panel=attrs.get("FIRM_PANEL"),
        effective_date=attrs.get("EFF_DATE"),
    )
