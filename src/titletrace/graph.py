"""TitleTrace LangGraph state machine.

Topology:
  parse_address
      |
  fetch_parcel --(error)--> END
      |
  [parallel Send fan-out -- all 6 run concurrently]
    fetch_ownership | search_liens | search_encumbrances
    fetch_zoning    | check_tax    | fetch_flood_zone
      |                |
  (liens?)        (tax delinquent?)
      |                |
  fetch_lienholder  fetch_tax_claim
      |                |
         [fan-in via annotated reducer]
              |
        synthesize_report
              |
            END

The six parallel nodes use LangGraph's Send API so they execute concurrently
with no inter-node dependency. Each returns a partial state update. The
annotated reducer on TraceState merges them.
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
from titletrace.state import TraceState

# ---------------------------------------------------------------------------
# Parallel fan-out node wrappers
# Each returns a partial TraceState dict; LangGraph merges them at the fan-in.
# ---------------------------------------------------------------------------


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


async def _check_tax(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"tax_status": None}
    if _is_philadelphia(state):
        status = await fetch_tax_status_opa(client, parcel.parcel_id)
        return {"tax_status": status}
    # Non-Philly: ATTOM tax data not implemented in v1
    return {"tax_status": None}


async def _fetch_flood_zone(state: TraceState, client: httpx.AsyncClient) -> dict:
    parcel = state.get("parcel")
    if not parcel:
        return {"flood_zone": None}
    # Derive lat/lon from parcel address via ATTOM geocode in production.
    # FEMA NFHL requires lat/lon; placeholder returns None until geocoding is wired.
    return {"flood_zone": None}


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------


def _route_after_parcel(state: TraceState):
    """If parcel lookup failed, end immediately. Otherwise fan out all 6 lookups."""
    if state.get("error"):
        return END
    return [
        Send("fetch_ownership", state),
        Send("search_liens", state),
        Send("search_encumbrances", state),
        Send("fetch_zoning", state),
        Send("check_tax", state),
        Send("fetch_flood_zone", state),
    ]


def _route_after_fanin(state: TraceState):
    """After fan-in, drill down if liens or tax delinquency found."""
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


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


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
    g.add_node("check_tax", partial(_check_tax, client=client))
    g.add_node("fetch_flood_zone", partial(_fetch_flood_zone, client=client))
    g.add_node("fetch_lienholder_detail", partial(_fetch_lienholder_detail, client=client))
    g.add_node("fetch_tax_claim_detail", partial(_fetch_tax_claim_detail, client=client))
    g.add_node("synthesize_report", synthesize_report)

    g.set_entry_point("parse_address")
    g.add_edge("parse_address", "fetch_parcel")
    g.add_conditional_edges("fetch_parcel", _route_after_parcel)

    # Fan-in: all six parallel nodes feed back into a conditional edge
    for fan_node in (
        "fetch_ownership",
        "search_liens",
        "search_encumbrances",
        "fetch_zoning",
        "check_tax",
        "fetch_flood_zone",
    ):
        g.add_conditional_edges(fan_node, _route_after_fanin)

    g.add_edge("fetch_lienholder_detail", "synthesize_report")
    g.add_edge("fetch_tax_claim_detail", "synthesize_report")
    g.add_edge("synthesize_report", END)

    return g.compile()
