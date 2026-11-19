"""The page at the root.

Anything the page reads out of the trace response has to keep existing, so
these pin the contract between the two rather than just asserting a 200.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)
    from titletrace.api.main import app

    with TestClient(app) as c:
        yield c


def test_the_root_serves_the_page(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in resp.text.lower()


def test_the_page_posts_to_the_route_that_exists(client):
    body = client.get("/").text

    assert "/api/demo/trace" in body


def test_every_field_the_page_reads_is_in_the_response(client):
    """A rename on either side would empty a panel with nothing failing."""
    from titletrace.demo import RECORDED_ADDRESSES

    page = client.get("/").text
    data = client.post("/api/demo/trace", json={"address": RECORDED_ADDRESSES[2]}).json()

    for key in ("mode", "mode_reason", "budget", "duration_ms", "parcel",
                "ownership_history", "liens", "encumbrances", "zoning",
                "tax_status", "flood_zone", "lienholder_details",
                "tax_claim_detail", "prompt", "error"):
        assert key in data, f"response lost {key}, which the page reads"
        assert key in page, f"page stopped reading {key}"

    for key in ("parcel_id", "owner_name", "year_built", "lot_size_sqft", "source"):
        assert key in data["parcel"], f"parcel lost {key}"
        assert key in page

    for key in ("zone_designation", "zone_description", "firm_panel", "effective_date"):
        assert key in data["flood_zone"], f"flood_zone lost {key}"
        assert key in page


def test_the_addresses_offered_are_the_ones_recorded(client):
    """Offering an address with no recording is a button that fails."""
    from titletrace.demo import RECORDED_ADDRESSES

    page = client.get("/").text
    offered = re.findall(r'data-a="([^"]+)"', page)

    assert offered, "no quick-pick addresses on the page"
    for address in offered:
        assert address in RECORDED_ADDRESSES, f"{address!r} is offered but not recorded"


def test_the_page_names_no_colour_the_token_set_does_not(client):
    """The palette is the product's signature. A hardcoded hex in markup is how
    it drifts back to a default."""
    page = client.get("/").text
    body = page.split("</style>", 1)[1] if "</style>" in page else page

    assert not re.search(r"#[0-9A-Fa-f]{6}\b", body), "a literal colour escaped into the markup"
