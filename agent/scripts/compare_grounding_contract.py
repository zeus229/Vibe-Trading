"""Small, read-only differential probe for upstream/fork grounding contracts.

Run from each checkout with PYTHONPATH pointing to that checkout's agent directory.
This tool prints normalized observations, never claims parity on its own.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.agent.grounding import GroundingLedger

ROW_A = "data.rows[0].market_value"
ROW_B = "data.rows[1].market_value"
TOTAL = "data.totals.market_value"


def _payload(*, duplicate=False, different_snapshot=False):
    return {
        "identifier": "CASE",
        "data": {
            "snapshot_id": "s2" if different_snapshot else "s1",
            "as_of": "2026-10-09T00:00:00Z",
            "totals": {"currency": "USD", "market_value": 10000},
            "rows": [
                {"entity_id": "ALPHA", "currency": "USD", "market_value": 125},
                {"entity_id": "BETA", "currency": "USD", "market_value": 125 if duplicate else 250},
            ],
        },
    }


CASES = (
    ("complete", "125 / 10000 * 100", 1.25, (ROW_A, TOTAL)),
    ("missing_denominator", "125 / 10000 * 100", 1.25, (ROW_A,)),
    ("invented_operand", "126 / 10000 * 100", 1.26, (ROW_A, TOTAL)),
    ("same_value_wrong_row", "125 / 10000 * 100", 1.25, (ROW_B, TOTAL)),
    ("scalar_factor", "125 * 0.25 / 10000 * 100", 0.3125, (ROW_A, TOTAL)),
    ("unobserved_addend", "(125 + 999) / 10000 * 100", 11.24, (ROW_A, TOTAL)),
    ("explicit_wrong_call", "125 / 10000 * 100", 1.25, ("unknown::" + ROW_A, "unknown::" + TOTAL)),
    ("label_trailing_text", "ratio: 125 / 10000 * 100 maybe", 1.25, (ROW_A, TOTAL)),
    ("label_exact_formula", "ratio: 125 / 10000 * 100", 1.25, (ROW_A, TOTAL)),
)


def probe_case(spec, root):
    name, formula, value, paths = spec
    ledger = GroundingLedger(run_dir=root / name, user_message="Compare ALPHA and BETA holdings.")
    payload = _payload(duplicate=name == "same_value_wrong_row")
    ledger.ingest_tool_result(
        tool_name="read_measurements", arguments={}, result=json.dumps(payload),
        call_id="call_A", success=True,
    )
    refs = [p if "::" in p else "call_A::" + p for p in paths]
    answer = (
        f"ALPHA change: {value:.6f} pp.\n"
        f"```figures\n{value:.6f} | derived | {formula} | {'; '.join(refs)}\n```"
    )
    result = ledger.validate_final_answer(answer)
    issues = []
    for issue in result.issues:
        issues.append({
            "role": issue.get("role"),
            "reason": issue.get("reason"),
            "preservable": bool(issue.get("derived_repair_verified")),
            "operand_ref_count": len(issue.get("derive_operand_refs") or ()),
        })
    return {"case": name, "valid": bool(result.valid), "issues": issues}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    with TemporaryDirectory(prefix="vibe-grounding-probe-") as temporary:
        root = Path(temporary)
        report = [probe_case(case, root) for case in CASES]
    result = {"schema": "vibe_grounding_comparison_v1", "cases": report}
    encoded = json.dumps(result, sort_keys=True, indent=2)
    if opts.output:
        opts.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
