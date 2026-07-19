import os
import pytest
import respx
import httpx

from titletrace.clients.attom import (
    fetch_lienholder_details_attom,
    fetch_parcel_attom,
    fetch_ownership_attom,
    fetch_tax_claim_detail_attom,
    search_liens_attom,
    fetch_zoning_attom,
    _ATTOM_BASE,
)


@pytest.fixture(autouse=True)
def set_attom_key(monkeypatch):
    monkeypatch.setenv("ATTOM_API_KEY", "test-key")


@pytest.fixture
def client():
    return httpx.AsyncClient()


PARCEL_DETAIL_URL = f"{_ATTOM_BASE}/property/detail"
SALES_HISTORY_URL = f"{_ATTOM_BASE}/saleshistory/basichistory"
LIEN_URL = f"{_ATTOM_BASE}/alllien/detail"


@pytest.mark.asyncio
async def test_fetch_parcel_attom_success(client):
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
                "location": {"latitude": "40.221583", "longitude": "-74.762307"},
                "lot": {"lotSize1": "8000"},
                "summary": {"yearBuilt": "1985", "propSubType": "SFR"},
                "owner": {"owner1": {"fullName": "JOHNSON ALICE"}},
            }
        ]
    }
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel_attom(client, "100 Broad St", "Trenton, NJ 08608")

    assert result is not None
    assert result.parcel_id == "NJ-001-ABC"
    assert result.city == "Trenton"
    assert result.state == "NJ"
    assert result.owner_name == "JOHNSON ALICE"
    assert result.year_built == 1985
    assert result.lot_size_sqft == 8000.0
    assert result.latitude == pytest.approx(40.221583)
    assert result.longitude == pytest.approx(-74.762307)
    assert result.source == "ATTOM"


@pytest.mark.asyncio
async def test_fetch_parcel_attom_missing_location_is_none(client):
    payload = {
        "property": [
            {
                "identifier": {"apn": "NJ-002-XYZ"},
                "address": {"line1": "1 Test Ave", "city": "Newark", "countrySubd": "NJ", "postal1": "07102"},
            }
        ]
    }
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel_attom(client, "1 Test Ave", "Newark, NJ 07102")

    assert result is not None
    assert result.latitude is None
    assert result.longitude is None


@pytest.mark.asyncio
async def test_fetch_parcel_attom_not_found(client):
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json={"property": []}))
        result = await fetch_parcel_attom(client, "0 Nowhere Rd", "Camden, NJ 08101")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_ownership_attom(client):
    payload = {
        "property": [
            {
                "owner": {"owner1": {"fullName": "WONG DAVID"}},
                "sale": {"saleTransDate": "2021-06-10", "saleAmt": "425000", "transType": "DEED"},
            }
        ]
    }
    with respx.mock:
        respx.get(SALES_HISTORY_URL).mock(return_value=httpx.Response(200, json=payload))
        records = await fetch_ownership_attom(client, "100 Broad St", "Trenton, NJ 08608")

    assert len(records) == 1
    assert records[0].owner_name == "WONG DAVID"
    assert records[0].sale_price == 425000.0
    assert records[0].source == "ATTOM"


@pytest.mark.asyncio
async def test_search_liens_attom_with_lien(client):
    payload = {
        "property": [
            {
                "liens": [
                    {
                        "lienType": "mortgage",
                        "lienAmt": "250000",
                        "recordingDate": "2021-06-10",
                        "lienHolderName": "First Federal Bank",
                        "lienStatus": "open",
                    }
                ]
            }
        ]
    }
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json=payload))
        liens = await search_liens_attom(client, "NJ-001-ABC", "NJ")

    assert len(liens) == 1
    assert liens[0].lien_type == "mortgage"
    assert liens[0].amount == 250000.0
    assert liens[0].lienholder == "First Federal Bank"
    assert liens[0].source == "ATTOM"


@pytest.mark.asyncio
async def test_search_liens_attom_empty(client):
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json={"property": []}))
        liens = await search_liens_attom(client, "NJ-001-ABC", "NJ")

    assert liens == []


@pytest.mark.asyncio
async def test_fetch_zoning_attom(client):
    payload = {
        "property": [
            {
                "summary": {"propSubType": "R1", "propClass": "Residential Single Family"},
            }
        ]
    }
    with respx.mock:
        respx.get(PARCEL_DETAIL_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_zoning_attom(client, "100 Broad St", "Trenton, NJ 08608")

    assert result is not None
    assert result.zoning_code == "R1"
    assert result.zoning_description == "Residential Single Family"
    assert result.source == "ATTOM"


@pytest.mark.asyncio
async def test_fetch_lienholder_details_attom_with_data(client):
    payload = {
        "property": [
            {
                "liens": [
                    {
                        "lienType": "mortgage",
                        "lienHolderName": "First Federal Bank",
                        "lienHolderAddr": "100 Finance Ave, Newark, NJ 07102",
                        "uccFileNum": "UCC-2021-00123",
                    }
                ]
            }
        ]
    }
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json=payload))
        details = await fetch_lienholder_details_attom(client, "NJ-001-ABC", "NJ")

    assert len(details) == 1
    assert details[0].lien_type == "mortgage"
    assert details[0].lienholder_name == "First Federal Bank"
    assert details[0].lienholder_address == "100 Finance Ave, Newark, NJ 07102"
    assert details[0].ucc_filing_number == "UCC-2021-00123"
    assert details[0].source == "ATTOM"


@pytest.mark.asyncio
async def test_fetch_lienholder_details_attom_empty(client):
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json={"property": []}))
        details = await fetch_lienholder_details_attom(client, "NJ-001-ABC", "NJ")

    assert details == []


@pytest.mark.asyncio
async def test_fetch_tax_claim_detail_attom_with_data(client):
    payload = {
        "property": [
            {
                "liens": [
                    {
                        "lienType": "taxlien",
                        "taxYear": "2023",
                        "lienAmt": "4800.50",
                        "lienHolderName": "Camden County Tax Collector",
                        "lienStatus": "open",
                    }
                ]
            }
        ]
    }
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json=payload))
        detail = await fetch_tax_claim_detail_attom(client, "NJ-001-ABC", "NJ")

    assert detail is not None
    assert detail.claim_year == 2023
    assert detail.amount == 4800.50
    assert detail.lienholder == "Camden County Tax Collector"
    assert detail.status == "open"
    assert detail.source == "ATTOM"


@pytest.mark.asyncio
async def test_fetch_tax_claim_detail_attom_not_found(client):
    with respx.mock:
        respx.get(LIEN_URL).mock(return_value=httpx.Response(200, json={"property": []}))
        detail = await fetch_tax_claim_detail_attom(client, "NJ-001-ABC", "NJ")

    assert detail is None
