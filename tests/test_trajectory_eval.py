"""The trajectory eval needs its own tests for the reason any eval does: one
that cannot fail is not measuring anything. `test_scorer_catches_*` feed the
scorer runs that are wrong in one specific way each, so a vacuous rewrite of
`_score` breaks these rather than quietly reporting 100%.
"""

from __future__ import annotations

import asyncio

import pytest

from eval.trajectory import DRILL_DOWN, FAN_OUT, _run_one, _score
from titletrace.demo import RECORDED_ADDRESSES
from titletrace.state import TaxStatus

GOOD_PATH = [
    "parse_address", "fetch_parcel",
    "fetch_ownership", "search_liens", "search_encumbrances",
    "fetch_zoning", "fetch_flood_zone",
    "determine_tax_status",
    "fetch_lienholder_detail", "fetch_tax_claim_detail",
    "synthesize_report",
]


def _run(path=None, liens=2, delinquent=True, address="812 Morris Rd, Wayne, PA 19087",
         calls=None):
    return {
        "address": address,
        "path": list(GOOD_PATH if path is None else path),
        "calls": calls if calls is not None else [("api.gateway.attomdata.com", "/x")],
        "state": {
            "liens": [{"i": i} for i in range(liens)],
            "tax_status": TaxStatus(is_delinquent=delinquent, source="test"),
        },
    }


def test_scorer_passes_a_correct_trajectory():
    assert _score(_run())["passed"] is True


def test_scorer_catches_a_missing_fanout_node():
    path = [n for n in GOOD_PATH if n != "fetch_zoning"]
    r = _score(_run(path=path))
    assert r["checks"]["fanout_complete"] is False
    assert r["passed"] is False


def test_scorer_catches_a_duplicated_fanout_node():
    path = GOOD_PATH[:3] + ["fetch_ownership"] + GOOD_PATH[3:]
    r = _score(_run(path=path))
    assert r["checks"]["fanout_once_each"] is False


def test_scorer_catches_a_drilldown_that_should_not_have_run():
    """No liens and not delinquent, yet both drill-downs fired. This is the
    inverted-predicate case, and the one that costs vendor calls."""
    r = _score(_run(liens=0, delinquent=False))
    assert r["checks"]["drilldown_matches_data"] is False
    assert r["observed"]["expected_drilldown"] == []


def test_scorer_catches_a_drilldown_that_should_have_run():
    path = [n for n in GOOD_PATH if n not in DRILL_DOWN]
    r = _score(_run(path=path, liens=3, delinquent=True))
    assert r["checks"]["drilldown_matches_data"] is False
    assert set(r["observed"]["expected_drilldown"]) == DRILL_DOWN


def test_scorer_catches_a_fanin_running_twice():
    path = GOOD_PATH + ["determine_tax_status"]
    assert _score(_run(path=path))["checks"]["fanin_once"] is False


def test_scorer_catches_not_terminating_in_synthesis():
    assert _score(_run(path=GOOD_PATH[:-1]))["checks"]["terminates_in_synthesis"] is False


def test_scorer_catches_an_undeclared_node():
    r = _score(_run(path=GOOD_PATH + ["fetch_something_new"]))
    assert r["checks"]["no_undeclared_nodes"] is False


def test_scorer_catches_philadelphia_routed_through_attom():
    r = _score(_run(address="1201 Chestnut St, Philadelphia, PA 19107",
                    calls=[("api.gateway.attomdata.com", "/propertyapi/x")]))
    assert r["checks"]["vendor_routing"] is False


def test_scorer_accepts_philadelphia_through_opa():
    r = _score(_run(address="1201 Chestnut St, Philadelphia, PA 19107",
                    calls=[("data.phila.gov", "/resource/x.json")]))
    assert r["checks"]["vendor_routing"] is True


@pytest.mark.parametrize("address", RECORDED_ADDRESSES)
def test_every_recorded_address_takes_the_right_path(address):
    """End to end against the recorded transport, no API keys, no cost."""
    result = _score(asyncio.run(_run_one(address)))
    failed = [name for name, ok in result["checks"].items() if not ok]
    assert not failed, f"{address} failed: {failed}\npath: {result['path']}"


def test_lien_endpoint_is_shared_by_the_two_search_nodes():
    """search_liens and search_encumbrances coalesce onto one in-flight fetch,
    so a clean parcel dials /alllien/detail once rather than twice."""
    run = asyncio.run(_run_one("45 Lafayette Ave, Haddonfield, NJ 08033"))
    lien_calls = sum(1 for _, p in run["calls"] if p.endswith("/alllien/detail"))
    assert lien_calls == 1, f"expected one shared lien fetch, saw {lien_calls}"


def test_fanout_set_is_the_graph_contract():
    """Guards against the eval drifting from the graph: if a node is added to
    the fan-out, FAN_OUT has to learn about it or fanout_complete goes stale."""
    import inspect

    from titletrace import graph

    source = inspect.getsource(graph.build_graph)
    for node in FAN_OUT:
        assert f'Send("{node}"' in source or f'"{node}"' in source, (
            f"{node} is in the eval's FAN_OUT but not in build_graph"
        )
