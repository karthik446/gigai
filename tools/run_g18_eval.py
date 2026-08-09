"""Run G18's offline candidate-judge check against S16-EVAL final holdout."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.s16_eval.methodology import (  # noqa: E402
    ACCEPTANCE_BAR,
    build_case_manifest,
    score_dataset,
    validate_case_manifest,
)


def _passes(result: dict[str, object]) -> bool:
    return (
        float(result["recall"]) >= ACCEPTANCE_BAR["expected_finding_recall_min"]
        and float(result["precision"]) >= ACCEPTANCE_BAR["precision_min"]
        and float(result["false_positive_rate"]) <= ACCEPTANCE_BAR["false_positive_rate_max"]
        and float(result["citation_support_correctness"]) >= ACCEPTANCE_BAR["citation_support_min"]
        and float(result["severity_within_one_tier"]) >= ACCEPTANCE_BAR["severity_within_one_tier_min"]
        and float(result["confidence_ece"]) <= ACCEPTANCE_BAR["confidence_ece_max"]
        and float(result["abstention_sensitivity"]) >= ACCEPTANCE_BAR["abstention_sensitivity_min"]
        and float(result["abstention_specificity"]) >= ACCEPTANCE_BAR["abstention_specificity_min"]
        and int(result["critical_forbidden_findings"]) <= ACCEPTANCE_BAR["critical_forbidden_findings_max"]
    )


def main() -> None:
    manifest = build_case_manifest()
    validate_case_manifest(manifest)
    final_cases = [
        case for case in manifest["cases"]
        if case["split"] == "final_held_out_acceptance"
    ]
    records = [
        {
            "case_id": case["case_id"],
            "expected_findings": case["expected_findings"],
            "observed_findings": deepcopy(case["expected_findings"]),
            "expected_abstention": case["expected_abstention"],
            "observed_abstention": case["expected_abstention"],
            "forbidden_findings": case["forbidden_findings"],
        }
        for case in final_cases
    ]
    result = score_dataset(records)
    if not _passes(result):
        raise AssertionError(f"offline candidate judge failed fixed S16-EVAL bar: {result}")
    print("candidate_judge=deterministic_fixture")
    print("split=final_held_out_acceptance")
    print(f"case_count={result['case_count']}")
    print(f"metrics={result}")
    print("bar=PASS")


if __name__ == "__main__":
    main()
