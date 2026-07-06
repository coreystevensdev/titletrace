"""ATTOM Data Solutions API client.

ATTOM is the primary data spine for non-Philadelphia PA counties and all NJ
properties. It provides parcel details, deed history, lien data, encumbrances,
and zoning. Requires an ATTOM_API_KEY from https://api.attomdata.com.

Free tier limits: 200 API calls/month. All calls include exponential backoff
on 429 to stay within rate limits.
"""

from __future__ import annotations

import os

import httpx

from titletrace.clients._base import get_json
from titletrace.state import (
    EncumbranceResult,
    LienResult,
    LienholderDetail,
    OwnerRecord,
    ParcelResult,
    TaxClaimDetail,
    ZoningResult,
)

_ATTOM_BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"


def _headers() -> dict[str, str]:
    key = os.environ.get("ATTOM_API_KEY", "")
    return {"apikey": key, "accept": "application/json"}


async def fetch_parcel_attom(
    client: httpx.AsyncClient,
    address1: str,
    address2: str,
) -> ParcelResult | None:
    """Fetch parcel details by street address. address1 = street, address2 = city+state+zip."""
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/property/detail",
        params={"address1": address1, "address2": address2},
        headers=_headers(),
    )
    props = data.get("property", [])
    if not props:
        return None
    p = props[0]
    identifier = p.get("identifier", {})
    address = p.get("address", {})
    lot = p.get("lot", {})
    summary = p.get("summary", {})
    return ParcelResult(
        parcel_id=identifier.get("apn", ""),
        address=address.get("line1", address1),
        city=address.get("city", ""),
        state=address.get("countrySubd", ""),
        zip_code=address.get("postal1", ""),
        owner_name=p.get("owner", {}).get("owner1", {}).get("fullName"),
        lot_size_sqft=float(lot["lotSize1"]) if lot.get("lotSize1") else None,
        year_built=int(summary["yearBuilt"]) if summary.get("yearBuilt") else None,
        land_use=summary.get("propSubType"),
        source="ATTOM",
    )


async def fetch_ownership_attom(
    client: httpx.AsyncClient,
    address1: str,
    address2: str,
    limit: int = 10,
) -> list[OwnerRecord]:
    """Fetch deed/sale transfer history from ATTOM."""
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/saleshistory/basichistory",
        params={"address1": address1, "address2": address2},
        headers=_headers(),
    )
    records = []
    for sale in data.get("property", [])[:limit]:
        s = sale.get("sale", {})
        records.append(
            OwnerRecord(
                owner_name=sale.get("owner", {}).get("owner1", {}).get("fullName", "Unknown"),
                sale_date=s.get("saleTransDate"),
                sale_price=float(s["saleAmt"]) if s.get("saleAmt") else None,
                document_type=s.get("transType"),
                source="ATTOM",
            )
        )
    return records


async def search_liens_attom(
    client: httpx.AsyncClient,
    apn: str,
    state: str,
) -> list[LienResult]:
    """Retrieve lien data for a parcel from ATTOM."""
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/alllien/detail",
        params={"apn": apn, "state": state},
        headers=_headers(),
    )
    liens = []
    for item in data.get("property", []):
        for lien in item.get("liens", []):
            liens.append(
                LienResult(
                    lien_type=lien.get("lienType", "unknown"),
                    amount=float(lien["lienAmt"]) if lien.get("lienAmt") else None,
                    recorded_date=lien.get("recordingDate"),
                    lienholder=lien.get("lienHolderName"),
                    status=lien.get("lienStatus", "unknown"),
                    source="ATTOM",
                )
            )
    return liens


async def search_encumbrances_attom(
    client: httpx.AsyncClient,
    apn: str,
    state: str,
) -> list[EncumbranceResult]:
    """Retrieve encumbrance data for a parcel from ATTOM."""
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/alllien/detail",
        params={"apn": apn, "state": state, "lienType": "encumbrance"},
        headers=_headers(),
    )
    results = []
    for item in data.get("property", []):
        for enc in item.get("liens", []):
            results.append(
                EncumbranceResult(
                    encumbrance_type=enc.get("lienType", "unknown"),
                    description=enc.get("lienComment"),
                    recorded_date=enc.get("recordingDate"),
                    source="ATTOM",
                )
            )
    return results


async def fetch_zoning_attom(
    client: httpx.AsyncClient,
    address1: str,
    address2: str,
) -> ZoningResult | None:
    """Fetch zoning classification from ATTOM."""
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/property/detail",
        params={"address1": address1, "address2": address2},
        headers=_headers(),
    )
    props = data.get("property", [])
    if not props:
        return None
    summary = props[0].get("summary", {})
    zoning_code = summary.get("propSubType", "")
    return ZoningResult(
        zoning_code=zoning_code or "Unknown",
        zoning_description=summary.get("propClass"),
        permit_count=None,
        source="ATTOM",
    )


async def fetch_lienholder_details_attom(
    client: httpx.AsyncClient,
    apn: str,
    state: str,
) -> list[LienholderDetail]:
    """Fetch lienholder name, address, and UCC filing numbers from ATTOM.

    Calls the same /alllien/detail endpoint used by search_liens_attom but
    maps the richer per-lienholder fields (address, UCC filing number) that
    the initial lien pass skips in favor of the lighter LienResult model.
    """
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/alllien/detail",
        params={"apn": apn, "state": state},
        headers=_headers(),
    )
    details: list[LienholderDetail] = []
    for item in data.get("property", []):
        for lien in item.get("liens", []):
            details.append(
                LienholderDetail(
                    lien_type=lien.get("lienType", "unknown"),
                    lienholder_name=lien.get("lienHolderName"),
                    lienholder_address=lien.get("lienHolderAddr"),
                    ucc_filing_number=lien.get("uccFileNum"),
                    source="ATTOM",
                )
            )
    return details


async def fetch_tax_claim_detail_attom(
    client: httpx.AsyncClient,
    apn: str,
    state: str,
) -> TaxClaimDetail | None:
    """Fetch tax lien claim detail from ATTOM.

    Filters /alllien/detail to tax liens only, returns the first match.
    Called only when check_tax already established delinquency, so an
    empty response means the lien is not yet recorded in ATTOM (county lag).
    """
    data = await get_json(
        client,
        f"{_ATTOM_BASE}/alllien/detail",
        params={"apn": apn, "state": state, "lienType": "taxlien"},
        headers=_headers(),
    )
    for item in data.get("property", []):
        for lien in item.get("liens", []):
            claim_year: int | None = None
            raw_year = lien.get("taxYear") or lien.get("recordingDate", "")
            if raw_year:
                try:
                    claim_year = int(str(raw_year)[:4])
                except (ValueError, TypeError):
                    pass
            return TaxClaimDetail(
                claim_year=claim_year,
                amount=float(lien["lienAmt"]) if lien.get("lienAmt") else None,
                lienholder=lien.get("lienHolderName"),
                status=lien.get("lienStatus"),
                source="ATTOM",
            )
    return None
