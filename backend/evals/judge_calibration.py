"""Calibrate the Auditor (LLM-as-a-Judge) against hand-labelled judge cases.

Question answered: when the Auditor says "ungrounded", how often is it right (precision),
and how many planted flaws does it catch (recall)? With --repeats N it also measures
consistency: does the judge give the same verdict every time on the same input?

Usage (from backend/):
    python -m evals.judge_calibration              # each case once (~7 min on Groq free tier)
    python -m evals.judge_calibration --repeats 3  # consistency check (~20 min)

Writes full results to evals/results/calibration_<timestamp>.json (gitignored).
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.golden import GoldenSet, load_golden_set
from evals.metrics import agreement_rate, claim_recall, confusion, fmt
from evals.run_evals import RESULTS_DIR
from evals.runner import JudgeResult, run_judge_case, to_dict
from src.clients.groq_client import GroqClient


def run_calibration(golden: GoldenSet, client: GroqClient, repeats: int = 1) -> dict[str, list[JudgeResult]]:
    """Run every judge case `repeats` times. Returns case id -> results (one per repeat)."""
    runs: dict[str, list[JudgeResult]] = {}
    for case in golden.judge_cases:
        runs[case.id] = []
        for attempt in range(1, repeats + 1):
            result = run_judge_case(case, client)
            runs[case.id].append(result)
            print(f"  {case.id} #{attempt}: expected grounded={case.expected_is_grounded} "
                  f"got={result.predicted_is_grounded} hits={result.planted_hits}"
                  f"{' ERROR ' + result.error[:80] if result.error else ''}", flush=True)
    return runs


def calibration_report(runs: dict[str, list[JudgeResult]]) -> dict[str, Any]:
    """Aggregate metrics over every individual auditor verdict (errors excluded and counted)."""
    scored = [r for results in runs.values() for r in results if r.predicted_is_grounded is not None]
    errors = sum(len(results) for results in runs.values()) - len(scored)
    matrix = confusion([not r.expected_is_grounded for r in scored], [not r.predicted_is_grounded for r in scored])
    hits = [hit for r in scored for hit in r.planted_hits.values()]

    per_case = {}
    for case_id, results in runs.items():
        verdicts = [r.predicted_is_grounded for r in results if r.predicted_is_grounded is not None]
        majority = Counter(verdicts).most_common(1)[0][0] if verdicts else None
        per_case[case_id] = {
            "flaw": results[0].flaw,
            "expected_is_grounded": results[0].expected_is_grounded,
            "verdicts": verdicts,
            "majority_correct": majority == results[0].expected_is_grounded if majority is not None else None,
        }

    return {
        "verdicts_scored": len(scored),
        "errors": errors,
        "confusion": {"tp": matrix.tp, "fp": matrix.fp, "fn": matrix.fn, "tn": matrix.tn},
        "precision": matrix.precision,
        "recall": matrix.recall,
        "accuracy": matrix.accuracy,
        "f1": matrix.f1,
        "claim_recall": claim_recall(hits),
        "agreement_rate": agreement_rate([case["verdicts"] for case in per_case.values() if case["verdicts"]]),
        "per_case": per_case,
    }


def print_report(report: dict[str, Any], repeats: int) -> None:
    c = report["confusion"]
    print("\n=== Judge calibration (positive class = UNGROUNDED) ===")
    print(f"verdicts scored: {report['verdicts_scored']}  errors: {report['errors']}  repeats per case: {repeats}")
    print("                     auditor: ungrounded   auditor: grounded")
    print(f"label: ungrounded    TP = {c['tp']:<16}  FN = {c['fn']}")
    print(f"label: grounded      FP = {c['fp']:<16}  TN = {c['tn']}")
    print(f"precision {fmt(report['precision'])} | recall {fmt(report['recall'])} | accuracy {fmt(report['accuracy'])} "
          f"| f1 {fmt(report['f1'])} | claim recall {fmt(report['claim_recall'])} "
          f"| agreement across repeats {fmt(report['agreement_rate'])}")
    print("\nper case:")
    for case_id, case in report["per_case"].items():
        mark = {True: "ok ", False: "BAD", None: "ERR"}[case["majority_correct"]]
        print(f"  [{mark}] {case_id:<28} {case['flaw']:<22} expected grounded={case['expected_is_grounded']!s:<5} "
              f"verdicts={case['verdicts']}")


def save_report(report: dict[str, Any], runs: dict[str, list[JudgeResult]], results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"calibration_{stamp}.json"
    payload = {"report": report, "runs": {case_id: [to_dict(r) for r in results] for case_id, results in runs.items()}}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate the auditor against manual labels.")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case (consistency check)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    runs = run_calibration(load_golden_set(), GroqClient(), repeats=args.repeats)
    report = calibration_report(runs)
    print_report(report, args.repeats)
    print(f"\nFull results: {save_report(report, runs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
