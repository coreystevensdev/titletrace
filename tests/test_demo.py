"""Recorded traces for a deployment with no vendor API keys.

The point of recording at the transport layer rather than stubbing the clients
is that everything above the socket still runs: retry classification, response
parsing, the graph's fan-out, and the conditional drill-down routing. These
tests assert that, because a recording that bypassed the real code would prove
nothing about the real code.
"""

from __future__ import annotations

import pytest

from titletrace.demo import RECORDED_ADDRESSES, demo_client, recorded_transport
from titletrace.clients import attom
from titletrace.graph import build_graph


@pytest.mark.asyncio
async def test_the_real_attom_client_parses_a_recorded_response():
    """If the recording drifts from what the client expects, this is where it
    shows, rather than as an empty panel on the demo page."""
    async with demo_client() as client:
        parcel = await attom.fetch_parcel_attom(client, "45 Lafayette Ave", "Haddonfield, NJ 08033")

    assert parcel is not None
    assert parcel.address
    assert parcel.source


@pytest.mark.asyncio
async def test_an_unrecorded_url_is_a_miss_not_a_silent_empty():
    """A typo in a recording key must fail loudly. Returning {} would render as
    'no liens found' and look like a real answer."""
    import httpx

    async with httpx.AsyncClient(transport=recorded_transport()) as client:
        resp = await client.get("https://api.gateway.attomdata.com/propertyapi/v1.0.0/nope")

    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("address", RECORDED_ADDRESSES)
async def test_every_recorded_address_traces_end_to_end(address):
    """The graph runs for real against the recordings: fan-out, fan-in, and the
    conditional branch. Synthesis is injected because it needs a key."""
    async def no_synthesis(state):
        return {}

    async with demo_client() as client:
        graph = build_graph(client, synthesize=no_synthesis)
        result = await graph.ainvoke({
            "raw_address": address, "parcel_id": None, "state": None, "city": None,
            "parcel": None, "ownership_history": [], "liens": [], "encumbrances": [],
            "zoning": None, "tax_status": None, "flood_zone": None,
            "lienholder_details": [], "tax_claim_detail": None, "report": None, "error": None,
        })

    assert result.get("error") is None, result.get("error")
    assert result["parcel"] is not None, "parcel lookup produced nothing"
    assert result["flood_zone"] is not None, "FEMA was called with real coordinates"


@pytest.mark.asyncio
async def test_a_recorded_trace_reaches_the_drill_down_branch():
    """The conditional edge after fan-in is the most interesting thing the graph
    does. A recording set where no address has liens would never exercise it."""
    async def no_synthesis(state):
        return {}

    hits = []
    async with demo_client() as client:
        graph = build_graph(client, synthesize=no_synthesis)
        for address in RECORDED_ADDRESSES:
            result = await graph.ainvoke({
                "raw_address": address, "parcel_id": None, "state": None, "city": None,
                "parcel": None, "ownership_history": [], "liens": [], "encumbrances": [],
                "zoning": None, "tax_status": None, "flood_zone": None,
                "lienholder_details": [], "tax_claim_detail": None, "report": None,
                "error": None,
            })
            if result.get("lienholder_details") or result.get("tax_claim_detail"):
                hits.append(address)

    assert hits, "no recorded address drills down, so the branch is never shown"


@pytest.mark.asyncio
async def test_the_prompt_is_built_from_the_traced_state():
    """What the demo shows in place of a synthesized report. It has to be the
    real prompt assembly, not a description of it."""
    from titletrace.nodes.synthesize_report import _build_prompt

    async def no_synthesis(state):
        return {}

    async with demo_client() as client:
        graph = build_graph(client, synthesize=no_synthesis)
        result = await graph.ainvoke({
            "raw_address": RECORDED_ADDRESSES[0], "parcel_id": None, "state": None,
            "city": None, "parcel": None, "ownership_history": [], "liens": [],
            "encumbrances": [], "zoning": None, "tax_status": None, "flood_zone": None,
            "lienholder_details": [], "tax_claim_detail": None, "report": None, "error": None,
        })

    prompt = _build_prompt(result)
    assert "submit_report" in prompt
    assert RECORDED_ADDRESSES[0].split()[0] in prompt


@pytest.mark.asyncio
async def test_an_unrecorded_philadelphia_address_returns_no_parcel():
    """The demo must not answer with a parcel nobody asked about.

    The OPA recordings carried predicate=None, so they matched every request to
    their path regardless of the address queried. Asking the live demo for
    "1600 Market St" answered "1201 CHESTNUT ST" owned by CHESTNUT PARTNERS LP,
    with the tax and ownership recordings agreeing, so a visitor typing their own
    address got a complete and confident trace of a different property.

    routes.demo_trace already replaces the error with "no recorded trace for X,
    this demo only covers ..." whenever the parcel comes back None. It simply
    never got the chance.
    """
    async def no_synthesis(state):
        return {}

    async with demo_client() as client:
        graph = build_graph(client, synthesize=no_synthesis)
        result = await graph.ainvoke({
            "raw_address": "1600 Market St, Philadelphia, PA 19103",
            "parcel_id": None, "state": None, "city": None, "parcel": None,
            "ownership_history": [], "liens": [], "encumbrances": [], "zoning": None,
            "tax_status": None, "flood_zone": None, "lienholder_details": [],
            "tax_claim_detail": None, "report": None, "error": None,
        })

    assert result.get("parcel") is None, (
        f"an unrecorded address resolved to {result.get('parcel')!r}, which is "
        "some other property's record"
    )
    # Nothing downstream should have invented content for it either.
    assert result.get("ownership_history") == []
    assert result.get("liens") == []
