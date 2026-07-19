"""Philadelphia OPA (Office of Property Assessment) + OpenDataPhilly client.

Both APIs are free with no key required. OPA provides parcel details,
owner name, assessment, and tax status for Philadelphia properties.
OpenDataPhilly supplements with deed transfer history.

OPA API: https://data.phila.gov/resource/w7rb-qrpr.json (Socrata)
OPA tax: https://data.phila.gov/resource/3qem-6v3v.json (Real Estate Tax)
Transfers: https://data.phila.gov/resource/2upd-bkgb.json (Property Transfers)
"""

from __future__ import annotations

import httpx

from titletrace.clients._base import get_json
from titletrace.state import OwnerRecord, ParcelResult, TaxStatus

_OPA_BASE = "https://data.phila.gov/resource"
_PARCEL_ENDPOINT = f"{_OPA_BASE}/w7rb-qrpr.json"
_TAX_ENDPOINT = f"{_OPA_BASE}/3qem-6v3v.json"
_TRANSFER_ENDPOINT = f"{_OPA_BASE}/2upd-bkgb.json"


def _parse_coordinate(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_parcel_opa(
    client: httpx.AsyncClient,
    address: str,
) -> ParcelResult | None:
    """Look up a Philadelphia parcel by street address via the OPA API."""
    data = await get_json(
        client,
        _PARCEL_ENDPOINT,
        params={"location": address, "$limit": "1"},
    )
    if not data:
        return None
    r = data[0]
    return ParcelResult(
        parcel_id=r.get("parcel_number", ""),
        address=r.get("location", address),
        city="Philadelphia",
        state="PA",
        zip_code=r.get("zip_code", ""),
        owner_name=r.get("owner_1"),
        lot_size_sqft=float(r["total_area"]) if r.get("total_area") else None,
        year_built=int(r["year_built"]) if r.get("year_built") else None,
        land_use=r.get("category_code_description"),
        # Field names "lat"/"lng" are an educated guess for this dataset,
        # not live-verified -- see the ParcelResult docstring in state.py.
        latitude=_parse_coordinate(r.get("lat")),
        longitude=_parse_coordinate(r.get("lng")),
        source="Philadelphia OPA",
    )


async def fetch_tax_status_opa(
    client: httpx.AsyncClient,
    parcel_id: str,
) -> TaxStatus | None:
    """Retrieve tax delinquency status from the Philadelphia Real Estate Tax dataset."""
    data = await get_json(
        client,
        _TAX_ENDPOINT,
        params={"parcel_number": parcel_id, "$limit": "1"},
    )
    if not data:
        return None
    r = data[0]
    balance = float(r["total_due"]) if r.get("total_due") else 0.0
    return TaxStatus(
        is_delinquent=balance > 0,
        balance_due=balance if balance > 0 else None,
        last_paid_year=int(r["last_year_paid"]) if r.get("last_year_paid") else None,
        source="Philadelphia OPA Real Estate Tax",
    )


async def fetch_ownership_history_opa(
    client: httpx.AsyncClient,
    parcel_id: str,
    limit: int = 10,
) -> list[OwnerRecord]:
    """Retrieve deed transfer history for a Philadelphia parcel."""
    data = await get_json(
        client,
        _TRANSFER_ENDPOINT,
        params={"opa_account_num": parcel_id, "$limit": str(limit), "$order": "display_date DESC"},
    )
    records = []
    for r in data:
        records.append(
            OwnerRecord(
                owner_name=r.get("grantee_name", "Unknown"),
                sale_date=r.get("display_date"),
                sale_price=float(r["total_consideration"]) if r.get("total_consideration") else None,
                document_type=r.get("document_type"),
                source="OpenDataPhilly Transfers",
            )
        )
    return records
