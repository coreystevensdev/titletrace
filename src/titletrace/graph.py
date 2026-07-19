"""TitleTrace LangGraph state machine.

Topology:
  parse_address
      |
  fetch_parcel --(error)--> END
      |
  [parallel Send fan-out -- all 5 run concurrently]
    fetch_ownership | search_liens | search_encumbrances
    fetch_zoning     | fetch_flood_zone
      |
  determine_tax_status (Philadelphia: OPA lookup; elsewhere: derived from
                         search_liens's already-fetched lien list -- no new
                         ATTOM call)
      |
  (liens?)        (tax delinquent?)
      |                |
  fetch_lienholder  fetch_tax_claim
      |                |
         [fan-in via annotated reducer]
              |
        synthesize_report
              |
            END

Five of the original six parallel nodes still use LangGraph's Send API to
execute concurrently with no inter-node dependency. Tax-status determination
was moved out of the parallel set: for non-Philadelphia parcels it depends on
search_liens's result, so it now runs as a sequential step immediately after
the fan-in of the five remaining parallel nodes, before the existing
lien/tax-claim conditional routing.
"""

from __future__ import annotations

import os
from functools import partial

import httpx
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from titletrace.clients.attom import (
    fetch_lienholder_details_attom,
    fetch_ownership_attom,
    fetch_tax_claim_detail_attom,
    fetch_zoning_attom,
    search_encumbrances_attom,
    search_liens_attom,
)
from titletrace.clients.fema import fetch_flood_zone
from titletrace.clients.opa import (
    fetch_ownership_history_opa,
    fetch_tax_status_opa,
)
from titletrace.nodes.fetch_parcel import _is_philadelphia, fetch_parcel
from titletrace.nodes.parse_address import parse_address
from titletrace.nodes.synthesize_report import synthesize_report
from titletrace.state import TaxStatus, TraceState

async def _fetch_ownership(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"ownership_history": []}
    if _is_philadelphia(state):
        records = await fetch_ownership_history_opa(client, parcel.parcel_id)
    else:
        street = state["raw_address"].split(",", 1)[0].strip()
        rest = state["raw_address"].split(",", 1)[1].strip() if "," in state["raw_address"] else ""
        records = await fetch_ownership_attom(client, street, rest)
    return {"ownership_history": records}


async def _search_liens(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"liens": []}
    liens = await search_liens_attom(client, parcel.parcel_id, state.get("state") or "PA")
    return {"liens": liens}


async def _search_encumbrances(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"encumbrances": []}
    enc = await search_encumbrances_attom(client, parcel.parcel_id, state.get("state") or "PA")
    return {"encumbrances": enc}


async def _fetch_zoning(state: TraceState, client: httpx.AsyncClient) -> dict:
    # ATTOM is the zoning source for both Philadelphia and non-Philadelphia properties.
    # OPA does not expose a zoning classification endpoint.
    street = state["raw_address"].split(",", 1)[0].strip()
    rest = state["raw_address"].split(",", 1)[1].strip() if "," in state["raw_address"] else ""
    result = await fetch_zoning_attom(client, street, rest)
    return {"zoning": result}


_TAX_LIEN_TYPE_MARKER = "tax"


async def _determine_tax_status(state: TraceState, client: httpx.AsyncClient) -> dict:
    """Runs after the parallel fan-out (not inside it) so the non-Philadelphia
    branch can reuse search_liens's already-fetched lien list instead of
    issuing a fifth call against ATTOM's /alllien/detail endpoint -- the same
    endpoint four other nodes already hit per trace, against a 200-calls/
    month free tier.

    Philadelphia keeps its existing OPA-based lookup unchanged. Everywhere
    else, ATTOM has no dedicated delinquency endpoint: a tax lien recorded
    against the parcel *is* the delinquency signal, so this scans the liens
    already fetched in parallel rather than calling ATTOM again.
    """
    parcel = state.get("parcel")
    if not parcel:
        return {"tax_status": None}
    if _is_philadelphia(state):
        status = await fetch_tax_status_opa(client, parcel.parcel_id)
        return {"tax_status": status}
    tax_liens = [lien for lien in state.get("liens", []) if _TAX_LIEN_TYPE_MARKER in lien.lien_type.lower()]
    if not tax_liens:
        return {"tax_status": TaxStatus(is_delinquent=False, source="ATTOM (derived from lien records)")}
    lien = tax_liens[0]
    return {
        "tax_status": TaxStatus(
            is_delinquent=True,
            balance_due=lien.amount,
            source="ATTOM (derived from lien records)",
        )
    }


async def _fetch_flood_zone(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel or parcel.latitude is None or parcel.longitude is None:
        # No resolvable coordinates -- omit the field rather than guess.
        return {"flood_zone": None}
    result = await fetch_flood_zone(client, parcel.latitude, parcel.longitude)
    return {"flood_zone": result}


def _route_after_parcel(state: TraceState):
    if state.get("error"):
        return END
    return [
        Send("fetch_ownership", state),
        Send("search_liens", state),
        Send("search_encumbrances", state),
        Send("fetch_zoning", state),
        Send("fetch_flood_zone", state),
    ]


def _route_after_fanin(state: TraceState):
    next_nodes = []
    if state.get("liens"):
        next_nodes.append("fetch_lienholder_detail")
    if state.get("tax_status") and state["tax_status"].is_delinquent:
        next_nodes.append("fetch_tax_claim_detail")
    if not next_nodes:
        return "synthesize_report"
    return next_nodes


async def _fetch_lienholder_detail(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"lienholder_details": []}
    details = await fetch_lienholder_details_attom(
        client, parcel.parcel_id, state.get("state") or "PA"
    )
    return {"lienholder_details": details}


async def _fetch_tax_claim_detail(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"tax_claim_detail": None}
    detail = await fetch_tax_claim_detail_attom(
        client, parcel.parcel_id, state.get("state") or "PA"
    )
    return {"tax_claim_detail": detail}


def build_graph(client: httpx.AsyncClient) -> StateGraph:
    """Compile and return the TitleTrace LangGraph. Pass a shared httpx client
    so all node coroutines share a single connection pool."""
    g = StateGraph(TraceState)

    g.add_node("parse_address", parse_address)
    g.add_node("fetch_parcel", partial(fetch_parcel, client=client))
    g.add_node("fetch_ownership", partial(_fetch_ownership, client=client))
    g.add_node("search_liens", partial(_search_liens, client=client))
    g.add_node("search_encumbrances", partial(_search_encumbrances, client=client))
    g.add_node("fetch_zoning", partial(_fetch_zoning, client=client))
    g.add_node("fetch_flood_zone", partial(_fetch_flood_zone, client=client))
    g.add_node("determine_tax_status", partial(_determine_tax_status, client=client))
    g.add_node("fetch_lienholder_detail", partial(_fetch_lienholder_detail, client=client))
    g.add_node("fetch_tax_claim_detail", partial(_fetch_tax_claim_detail, client=client))
    g.add_node("synthesize_report", synthesize_report)

    g.set_entry_point("parse_address")
    g.add_edge("parse_address", "fetch_parcel")
    g.add_conditional_edges("fetch_parcel", _route_after_parcel)

    # Fan-in: all five parallel nodes feed into determine_tax_status, which
    # runs once tax_status can be computed (Philadelphia via OPA, everywhere
    # else derived from search_liens's already-fetched lien list), then
    # routes on to the lienholder/tax-claim conditional branch.
    for fan_node in (
        "fetch_ownership",
        "search_liens",
        "search_encumbrances",
        "fetch_zoning",
        "fetch_flood_zone",
    ):
        g.add_edge(fan_node, "determine_tax_status")
    g.add_conditional_edges("determine_tax_status", _route_after_fanin)

    g.add_edge("fetch_lienholder_detail", "synthesize_report")
    g.add_edge("fetch_tax_claim_detail", "synthesize_report")
    g.add_edge("synthesize_report", END)

    return g.compile()
