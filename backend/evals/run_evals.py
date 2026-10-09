"""Score the pipeline against the golden set. Standalone: NOT part of the live app.

Usage (from backend/):
    python -m evals.run_evals --suite extraction     # ~1 min
    python -m evals.run_evals --suite judge          # ~7 min on Groq free tier
    python -m evals.run_evals --suite pipeline       # ~15 min (full live debates)
    python -m evals.run_evals --suite all

Prints a summary table and writes full results to evals/results/run_<timestamp>.json (gitignored).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.golden import EVALS_DIR, GoldenSet, load_golden_set
from evals.metrics import claim_recall, confusion, fmt
from evals.runner import (
    ExtractionResult,
    JudgeResult,
    PipelineResult,
    run_extraction_case,
    run_judge_case,
    run_pipeline_case,
    to_dict,
)
from src.clients.groq_client import GroqClient

RESULTS_DIR = EVALS_DIR / "results"
SUITES = ("extraction", "judge", "pipeline")


def run_extraction_suite(golden: GoldenSet, client: GroqClient, **kwargs: Any) -> list[ExtractionResult]:
    results = []
    for case in golden.extraction_cases:
        result = run_extraction_case(case, client, **kwargs)
        print(f"  [{'PASS' if result.passed else 'FAIL'}] {case.id}: expected={case.expected_ticker} "
              f"got={result.got} {('(' + result.error[:80] + ')') if result.error else ''}", flush=True)
        results.append(result)
    return results


def run_judge_suite(golden: GoldenSet, client: GroqClient) -> list[JudgeResult]:
    results = []
    for case in golden.judge_cases:
        result = run_judge_case(case, client)
        mark = "ERROR" if result.error else ("PASS" if result.correct else "FAIL")
        print(f"  [{mark}] {case.id} ({case.flaw}): expected grounded={case.expected_is_grounded} "
              f"got={result.predicted_is_grounded} planted_hits={result.planted_hits}", flush=True)
        results.append(result)
    return results


def run_pipeline_suite(golden: GoldenSet, client: GroqClient, **kwargs: Any) -> list[PipelineResult]:
    results = []
    for case in golden.pipeline_cases:
        result = run_pipeline_case(case, client, **kwargs)
        failed = [name for name, ok in result.checks.items() if not ok]
        print(f"  [{'PASS' if result.passed else 'FAIL'}] {case.id}: ticker={result.ticker} rounds={result.rounds} "
              f"forced_exit={result.forced_exit} auditor_grounded={result.auditor_grounded} "
              f"{result.seconds:.0f}s failed_checks={failed} {result.error or ''}", flush=True)
        results.append(result)
    return results


def summarize(extraction: list[ExtractionResult], judge: list[JudgeResult],
              pipeline: list[PipelineResult]) -> dict[str, Any]:
    """Headline numbers per suite."""
    summary: dict[str, Any] = {}
    if extraction:
        summary["extraction"] = {"passed": sum(r.passed for r in extraction), "total": len(extraction)}
    if judge:
        scored = [r for r in judge if r.predicted_is_grounded is not None]
        matrix = confusion([not r.expected_is_grounded for r in scored],
                           [not r.predicted_is_grounded for r in scored])
        hits = [hit for r in scored for hit in r.planted_hits.values()]
        summary["judge"] = {
            "correct": sum(r.correct for r in judge), "total": len(judge), "errors": len(judge) - len(scored),
            "precision": matrix.precision, "recall": matrix.recall, "accuracy": matrix.accuracy,
            "claim_recall": claim_recall(hits),
            "confusion": {"tp": matrix.tp, "fp": matrix.fp, "fn": matrix.fn, "tn": matrix.tn},
        }
    if pipeline:
        grounded = [r.auditor_grounded for r in pipeline if r.auditor_grounded is not None]
        summary["pipeline"] = {
            "passed": sum(r.passed for r in pipeline), "total": len(pipeline),
            "auditor_grounded_rate": (sum(grounded) / len(grounded)) if grounded else None,
            "forced_exits": sum(bool(r.forced_exit) for r in pipeline),
            "avg_seconds": sum(r.seconds for r in pipeline) / len(pipeline),
        }
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("\n=== Summary ===")
    if "extraction" in summary:
        s = summary["extraction"]
        print(f"extraction : {s['passed']}/{s['total']} passed")
    if "judge" in summary:
        s = summary["judge"]
        print(f"judge      : {s['correct']}/{s['total']} correct ({s['errors']} errors) | precision {fmt(s['precision'])} "
              f"recall {fmt(s['recall'])} accuracy {fmt(s['accuracy'])} claim_recall {fmt(s['claim_recall'])}")
    if "pipeline" in summary:
        s = summary["pipeline"]
        print(f"pipeline   : {s['passed']}/{s['total']} passed all checks | auditor grounded rate "
              f"{fmt(s['auditor_grounded_rate'])} | forced exits {s['forced_exits']} | avg {s['avg_seconds']:.0f}s")


def save_results(summary: dict[str, Any], results: dict[str, list[Any]], results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"run_{stamp}.json"
    payload = {"summary": summary, "results": {k: [to_dict(r) for r in v] for k, v in results.items()}}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set evals.")
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    golden, client = load_golden_set(), GroqClient()
    selected = SUITES if args.suite == "all" else (args.suite,)
    results: dict[str, list[Any]] = {"extraction": [], "judge": [], "pipeline": []}
    if "extraction" in selected:
        print("--- extraction ---", flush=True)
        results["extraction"] = run_extraction_suite(golden, client)
    if "judge" in selected:
        print("--- judge ---", flush=True)
        results["judge"] = run_judge_suite(golden, client)
    if "pipeline" in selected:
        print("--- pipeline ---", flush=True)
        results["pipeline"] = run_pipeline_suite(golden, client)

    summary = summarize(results["extraction"], results["judge"], results["pipeline"])
    print_summary(summary)
    print(f"\nFull results: {save_results(summary, results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
