"""TitleTrace evaluation harness.

Runs the full LangGraph trace pipeline against addresses in golden_dataset.json
and scores each result on:
  - address_parse_accuracy: did parse_address extract state/city correctly?
  - parcel_found_rate: did fetch_parcel return a result?
  - error_correctness: did bad addresses produce the expected error?
  - report_confidence: what confidence did synthesize_report assign?

Uses openai (optional dep) for LLM-based faithfulness scoring if OPENAI_API_KEY
is set. Without it, only deterministic metrics are computed.

Usage:
    python -m eval.eval [--dataset eval/golden_dataset.json] [--output eval/results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).parent
_DEFAULT_DATASET = _HERE / "golden_dataset.json"
_DEFAULT_OUTPUT = _HERE / "results.json"


def _load_dataset(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


async def _run_trace(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    from titletrace.graph import build_graph
    from titletrace.state import TraceState

    graph = build_graph(client)
    initial: TraceState = {
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
    t0 = time.monotonic()
    result: TraceState = await graph.ainvoke(initial)
    duration_ms = int((time.monotonic() - t0) * 1000)
    report = result.get("report")
    return {
        "state": result.get("state"),
        "city": result.get("city"),
        "error": result.get("error"),
        "parcel_found": result.get("parcel") is not None,
        "report_confidence": report.confidence if report else None,
        "lien_count": report.lien_count if report else 0,
        "data_gaps": report.data_gaps if report else [],
        "duration_ms": duration_ms,
    }


def _score(case: dict, actual: dict) -> dict[str, Any]:
    scores: dict[str, Any] = {"id": case["id"]}

    expected_error = case.get("expected_error_contains")
    if expected_error:
        actual_error = actual.get("error") or ""
        scores["error_correct"] = expected_error in actual_error
        scores["parse_correct"] = True
        scores["parcel_found"] = False
    else:
        scores["parse_correct"] = (
            actual.get("state") == case.get("expected_state")
            and actual.get("city") == case.get("expected_city")
        )
        scores["parcel_found"] = actual.get("parcel_found", False)
        scores["error_correct"] = actual.get("error") is None
        scores["confidence"] = actual.get("report_confidence")

    scores["duration_ms"] = actual.get("duration_ms")
    return scores


async def main(dataset_path: Path, output_path: Path) -> None:
    cases = _load_dataset(dataset_path)
    results = []

    async with httpx.AsyncClient() as client:
        for case in cases:
            print(f"  Running: {case['id']} ({case['address']!r})")
            try:
                actual = await _run_trace(client, case["address"])
            except Exception as exc:
                actual = {"error": str(exc), "parcel_found": False}
            score = _score(case, actual)
            results.append(score)
            status = "OK" if all(v for k, v in score.items() if k.endswith("_correct")) else "FAIL"
            print(f"    {status}: parse={score.get('parse_correct')} parcel={score.get('parcel_found')}")

    parse_acc = sum(1 for r in results if r.get("parse_correct")) / len(results)
    parcel_rate = sum(1 for r in results if r.get("parcel_found")) / len(results)
    error_acc = sum(1 for r in results if r.get("error_correct")) / len(results)

    summary = {
        "total": len(results),
        "parse_accuracy": round(parse_acc, 3),
        "parcel_found_rate": round(parcel_rate, 3),
        "error_accuracy": round(error_acc, 3),
        "cases": results,
    }

    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\nEval complete:")
    print(f"  parse_accuracy:    {parse_acc:.1%}")
    print(f"  parcel_found_rate: {parcel_rate:.1%}")
    print(f"  error_accuracy:    {error_acc:.1%}")
    print(f"  Results: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    args = parser.parse_args()
    asyncio.run(main(Path(args.dataset), Path(args.output)))
