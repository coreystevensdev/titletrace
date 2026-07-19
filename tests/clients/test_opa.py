import pytest
import respx
import httpx

from titletrace.clients.opa import (
    fetch_parcel_opa,
    fetch_tax_status_opa,
    fetch_ownership_history_opa,
    _PARCEL_ENDPOINT,
    _TAX_ENDPOINT,
    _TRANSFER_ENDPOINT,
)


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.mark.asyncio
async def test_fetch_parcel_opa_success(client):
    payload = [
        {
            "parcel_number": "883309300",
            "location": "1234 MARKET ST",
            "zip_code": "19107",
            "owner_1": "SMITH JOHN",
            "total_area": "1500",
            "year_built": "1920",
            "category_code_description": "Single Family",
        }
    ]
    with respx.mock:
        respx.get(_PARCEL_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel_opa(client, "1234 Market St")

    assert result is not None
    assert result.parcel_id == "883309300"
    assert result.owner_name == "SMITH JOHN"
    assert result.year_built == 1920
    assert result.lot_size_sqft == 1500.0
    assert result.source == "Philadelphia OPA"
    assert result.city == "Philadelphia"
    assert result.state == "PA"


@pytest.mark.asyncio
async def test_fetch_parcel_opa_not_found(client):
    with respx.mock:
        respx.get(_PARCEL_ENDPOINT).mock(return_value=httpx.Response(200, json=[]))
        result = await fetch_parcel_opa(client, "9999 Fake St")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_parcel_opa_includes_coordinates_when_present(client):
    payload = [
        {
            "parcel_number": "884000100",
            "location": "1234 MARKET ST",
            "zip_code": "19107",
            "owner_1": "SMITH JOHN",
            "total_area": "1200",
            "year_built": "1920",
            "category_code_description": "ROW W/OFF STREET PARKING",
            "lat": "39.952740",
            "lng": "-75.163590",
        }
    ]
    with respx.mock:
        respx.get(_PARCEL_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel_opa(client, "1234 MARKET ST")

    assert result is not None
    assert result.latitude == pytest.approx(39.952740)
    assert result.longitude == pytest.approx(-75.163590)


@pytest.mark.asyncio
async def test_fetch_parcel_opa_missing_coordinates_is_none(client):
    payload = [
        {
            "parcel_number": "884000100",
            "location": "1234 MARKET ST",
            "zip_code": "19107",
        }
    ]
    with respx.mock:
        respx.get(_PARCEL_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_parcel_opa(client, "1234 MARKET ST")

    assert result is not None
    assert result.latitude is None
    assert result.longitude is None


@pytest.mark.asyncio
async def test_fetch_tax_status_opa_delinquent(client):
    payload = [
        {
            "parcel_number": "883309300",
            "total_due": "4500.75",
            "last_year_paid": "2020",
        }
    ]
    with respx.mock:
        respx.get(_TAX_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_tax_status_opa(client, "883309300")

    assert result is not None
    assert result.is_delinquent is True
    assert result.balance_due == 4500.75
    assert result.last_paid_year == 2020
    assert result.source == "Philadelphia OPA Real Estate Tax"


@pytest.mark.asyncio
async def test_fetch_tax_status_opa_current(client):
    payload = [{"parcel_number": "883309300", "total_due": "0", "last_year_paid": "2025"}]
    with respx.mock:
        respx.get(_TAX_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        result = await fetch_tax_status_opa(client, "883309300")

    assert result is not None
    assert result.is_delinquent is False
    assert result.balance_due is None


@pytest.mark.asyncio
async def test_fetch_tax_status_opa_not_found(client):
    with respx.mock:
        respx.get(_TAX_ENDPOINT).mock(return_value=httpx.Response(200, json=[]))
        result = await fetch_tax_status_opa(client, "000000000")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_ownership_history_opa(client):
    payload = [
        {
            "grantee_name": "JONES MARY",
            "display_date": "2022-03-15",
            "total_consideration": "350000",
            "document_type": "DEED",
        },
        {
            "grantee_name": "SMITH JOHN",
            "display_date": "2005-07-01",
            "total_consideration": "210000",
            "document_type": "DEED",
        },
    ]
    with respx.mock:
        respx.get(_TRANSFER_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        records = await fetch_ownership_history_opa(client, "883309300")

    assert len(records) == 2
    assert records[0].owner_name == "JONES MARY"
    assert records[0].sale_price == 350000.0
    assert records[0].source == "OpenDataPhilly Transfers"
    assert records[1].document_type == "DEED"
