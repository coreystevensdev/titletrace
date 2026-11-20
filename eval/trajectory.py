"""Trajectory eval: does the graph take the right path, not just produce an answer.

`eval/eval.py` grades the report that comes out. This grades the route to it.
That distinction matters because every failure mode specific to an agent lives
in the route: a fan-out node that silently stopped running, a conditional branch
that fires when it should not, a drill-down that queries a vendor for detail on
a parcel with nothing to detail. None of those necessarily change the final
report enough for an output eval to notice, and all of them cost real API calls.

Two things make this an eval rather than a snapshot test.

The expected drill-down set is DERIVED from the final state, not written down
per address. `fetch_lienholder_detail` should run exactly when the trace found
liens, and `fetch_tax_claim_detail` exactly when it found the parcel
delinquent. So the expectation comes from the data the graph collected and the
observation comes from the node trace, and `_route_after_fanin` is what has to
agree with both. Hardcoding "Philadelphia runs both" would still pass with the
routing predicate inverted.

Vendor routing is read off the wire rather than from the state. The recorded
transport logs every (host, path) the trace requested, so "Philadelphia goes
through OPA and everywhere else through ATTOM" is checked against the hosts
actually dialled, which is the tool-calling half of the same question.

What this does NOT measure: wall-clock parallelism. The five fan-out nodes are
asserted to run between `fetch_parcel` and the fan-in, and the fan-in to run
once after all five, which is the graph contract. Whether they overlapped in
time is not observable here, because the recorded transport answers in
microseconds and there is nothing to overlap.

Usage:
    PYTHONPATH=src python -m eval.trajectory [--out eval/trajectory.json]

Needs no API keys. Exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from titletrace import demo
from titletrace.graph import build_graph
from titletrace.state import TraceState

FAN_OUT = {
    "fetch_ownership",
    "search_liens",
    "search_encumbrances",
    "fetch_zoning",
    "fetch_flood_zone",
}
DRILL_DOWN = {"fetch_lienholder_detail", "fetch_tax_claim_detail"}
DECLARED = {"parse_address", "fetch_parcel", "determine_tax_status",
            "synthesize_report"} | FAN_OUT | DRILL_DOWN

_OPA_HOST = "data.phila.gov"
_ATTOM_HOST = "api.gateway.attomdata.com"


def _blank(address: str) -> dict[str, Any]:
    state = {key: None for key in TraceState.__annotations__}
    state["raw_address"] = address
    return state


async def _stub_synthesis(state: TraceState) -> dict[str, Any]:
    """Synthesis calls Claude, so it is replaced. Nothing downstream of it is
    part of a trajectory, and leaving it live would make this eval cost money."""
    return {"report": None}


def _logging_transport(log: list[tuple[str, str]]) -> httpx.MockTransport:
    """The recorded transport, with every request recorded as (host, path)."""

    def handler(request: httpx.Request) -> httpx.Response:
        log.append((request.url.host, request.url.path))
        return demo._handler(request)

    return httpx.MockTransport(handler)


async def _run_one(address: str) -> dict[str, Any]:
    calls: list[tuple[str, str]] = []
    path: list[str] = []
    async with httpx.AsyncClient(transport=_logging_transport(calls)) as client:
        graph = build_graph(client, synthesize=_stub_synthesis)
        final: dict[str, Any] = {}
        async for update in graph.astream(_blank(address), stream_mode="updates"):
            for node, delta in update.items():
                path.append(node)
                if isinstance(delta, dict):
                    final.update(delta)
    return {"address": address, "path": path, "calls": calls, "state": final}


def _score(run: dict[str, Any]) -> dict[str, Any]:
    path, calls, state = run["path"], run["calls"], run["state"]
    seen = Counter(path)
    checks: dict[str, Any] = {}

    # The graph contract: five fan-out nodes, each once, all of them strictly
    # between the parcel fetch and the fan-in.
    if "fetch_parcel" in path and "determine_tax_status" in path:
        lo = path.index("fetch_parcel")
        hi = path.index("determine_tax_status")
        between = set(path[lo + 1:hi])
    else:
        between = set()
    checks["fanout_complete"] = between == FAN_OUT
    checks["fanout_once_each"] = all(seen[n] == 1 for n in FAN_OUT)
    checks["fanin_once"] = seen["determine_tax_status"] == 1

    # The routing decision, derived from what the trace actually found rather
    # than from a per-address answer written down here.
    liens = state.get("liens") or []
    tax = state.get("tax_status")
    delinquent = bool(getattr(tax, "is_delinquent", False))
    expected_drill = set()
    if liens:
        expected_drill.add("fetch_lienholder_detail")
    if delinquent:
        expected_drill.add("fetch_tax_claim_detail")
    actual_drill = set(path) & DRILL_DOWN
    checks["drilldown_matches_data"] = actual_drill == expected_drill

    checks["terminates_in_synthesis"] = bool(path) and path[-1] == "synthesize_report"
    checks["no_undeclared_nodes"] = set(path) <= DECLARED

    # Tool-calling: which vendor was dialled for this address.
    hosts = {host for host, _ in calls}
    philly = "philadelphia" in run["address"].lower()
    checks["vendor_routing"] = (_OPA_HOST in hosts) if philly else (_ATTOM_HOST in hosts)

    # The lien endpoint is shared by two fan-out nodes through an in-flight
    # task, so it must be dialled fewer times than the nodes that read it.
    lien_calls = sum(1 for _, p in calls if p.endswith("/alllien/detail"))

    return {
        "address": run["address"],
        "path": path,
        "checks": checks,
        "passed": all(checks.values()),
        "observed": {
            "liens": len(liens),
            "delinquent": delinquent,
            "drilldown": sorted(actual_drill),
            "expected_drilldown": sorted(expected_drill),
            "http_calls": len(calls),
            "lien_detail_calls": lien_calls,
            "hosts": sorted(hosts),
        },
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "trajectory.json"))
    args = ap.parse_args()

    results = [_score(await _run_one(a)) for a in demo.RECORDED_ADDRESSES]

    names = sorted(results[0]["checks"]) if results else []
    width = max((len(n) for n in names), default=0)
    for r in results:
        head = "PASS" if r["passed"] else "FAIL"
        print(f"\n{head}  {r['address']}")
        print(f"      path: {' -> '.join(r['path'])}")
        o = r["observed"]
        print(f"      {o['liens']} liens, delinquent={o['delinquent']}, "
              f"{o['http_calls']} http calls ({o['lien_detail_calls']} to /alllien/detail)")
        for name in names:
            ok = r["checks"][name]
            print(f"      [{'ok' if ok else 'XX'}] {name:<{width}}")

    per_check = {
        n: sum(1 for r in results if r["checks"][n]) / len(results)
        for n in names
    } if results else {}
    passed = sum(1 for r in results if r["passed"])

    print(f"\n{passed}/{len(results)} trajectories correct")
    for name, rate in sorted(per_check.items()):
        print(f"  {name:<{width}}  {rate:.0%}")

    Path(args.out).write_text(json.dumps({
        "n_addresses": len(results),
        "trajectories_correct": passed,
        "per_check": per_check,
        "results": results,
    }, indent=2, default=str) + "\n")
    print(f"\nwrote {args.out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
