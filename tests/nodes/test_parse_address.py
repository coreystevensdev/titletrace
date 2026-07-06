import pytest

from titletrace.nodes.parse_address import parse_address


def _state(address: str) -> dict:
    return {
        "raw_address": address,
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


def test_parse_philadelphia_address():
    result = parse_address(_state("1234 Market St, Philadelphia, PA 19107"))
    assert result["state"] == "PA"
    assert result["city"] == "Philadelphia"
    assert result["error"] is None


def test_parse_new_jersey_address():
    result = parse_address(_state("100 Broad St, Trenton, NJ 08608"))
    assert result["state"] == "NJ"
    assert result["city"] == "Trenton"
    assert result["error"] is None


def test_parse_case_insensitive_state():
    result = parse_address(_state("100 Main St, Lansdowne, pa 19050"))
    assert result["state"] == "PA"


def test_parse_invalid_address_returns_error():
    result = parse_address(_state("123 Fake Street, Nowhere"))
    assert result["error"] is not None
    assert "PA or NJ" in result["error"]


def test_parse_unsupported_state_returns_error():
    # The regex only matches PA|NJ, so NY addresses fail the parse step rather
    # than reaching the supported-states check.
    result = parse_address(_state("500 Fifth Ave, New York, NY 10110"))
    assert result["error"] is not None
    assert "PA or NJ" in result["error"]


def test_parse_strips_whitespace():
    result = parse_address(_state("  1600 JFK Blvd, Philadelphia, PA 19103  "))
    assert result["state"] == "PA"
    assert result["error"] is None


def test_parse_nine_digit_zip():
    result = parse_address(_state("300 N 5th St, Philadelphia, PA 19106-2009"))
    assert result["state"] == "PA"
    assert result["error"] is None
