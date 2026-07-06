from titletrace.state import (
    FloodZoneResult,
    LienResult,
    ParcelResult,
    TaxStatus,
    TraceReport,
    TraceState,
)


def test_state_defaults():
    state: TraceState = {
        "raw_address": "1234 Market St, Philadelphia, PA 19107",
        "parcel_id": None,
        "state": None,
        "city": None,
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
    assert state["raw_address"] == "1234 Market St, Philadelphia, PA 19107"
    assert state["liens"] == []


def test_lien_result_model():
    lien = LienResult(
        lien_type="tax",
        amount=5000.00,
        recorded_date="2024-01-15",
        lienholder=None,
        status="open",
        source="attom",
    )
    assert lien.lien_type == "tax"
    assert lien.amount == 5000.00


def test_flood_zone_result_defaults():
    zone = FloodZoneResult(
        zone_designation="X",
        zone_description="Minimal/moderate risk -- outside 1% annual chance floodplain",
    )
    assert zone.source == "FEMA NFHL ArcGIS REST"
    assert zone.firm_panel is None


def test_tax_status_delinquent():
    tax = TaxStatus(is_delinquent=True, balance_due=3200.50, source="Philadelphia OPA")
    assert tax.is_delinquent
    assert tax.balance_due == 3200.50


def test_trace_report_partial_confidence():
    report = TraceReport(
        address="1234 Market St, Philadelphia, PA 19107",
        data_gaps=["zoning unavailable", "flood zone unavailable"],
        confidence="partial",
    )
    assert report.confidence == "partial"
    assert len(report.data_gaps) == 2
    assert report.lien_count == 0


def test_parcel_result_optional_fields():
    parcel = ParcelResult(
        parcel_id="001-ABC",
        address="100 N Broad St",
        city="Philadelphia",
        state="PA",
        zip_code="19107",
        source="Philadelphia OPA",
    )
    assert parcel.owner_name is None
    assert parcel.year_built is None
