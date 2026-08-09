"""Run the research-only S16-EVAL guard and fixture mutation checks.

This deliberately mutates in-memory methodology inputs. It never edits a
repository file, invokes a provider, or touches a GigAI target.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.s16_eval.methodology import (
    RUNTIME_FINDING_CODE_MAP,
    build_case_manifest,
    score_case,
    validate_assertion_namespaces,
    validate_case_manifest,
)


def expect_rejection(name: str, action) -> None:
    try:
        action()
    except (AssertionError, ValueError):
        print(f"{name}: caught")
    else:
        raise AssertionError(f"mutation escaped: {name}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    baseline = build_case_manifest()
    validate_case_manifest(baseline)
    mutations = []

    missing_behavior = deepcopy(baseline)
    del missing_behavior["coverage"][next(iter(missing_behavior["coverage"]))]
    mutations.append(("M01", "remove behavior coverage", lambda: validate_case_manifest(missing_behavior)))

    bad_split = deepcopy(baseline)
    bad_split["split_counts"]["calibration"] = 3
    mutations.append(("M02", "change fixed split count", lambda: validate_case_manifest(bad_split)))

    bad_categories = deepcopy(baseline)
    bad_categories["category_labels"] = ["positive", "negative", "ambiguous"]
    mutations.append(("M03", "remove required category", lambda: validate_case_manifest(bad_categories)))

    contaminated = deepcopy(baseline)
    final_case = next(case for case in contaminated["cases"] if case["split"] == "final_held_out_acceptance")
    contaminated["tuning_case_ids"] = [final_case["case_id"]]
    mutations.append(("M04", "tune on final held-out case", lambda: validate_case_manifest(contaminated)))

    expected = [{"criterion_id": "citation_support", "severity": "high", "evidence_support": "supported"}]
    overreported = expected + [{"criterion_id": "fabricated", "severity": "high", "evidence_support": "unsupported"}]
    mutations.append(
        (
            "M05",
            "emit forbidden extra finding",
            lambda: require(
                score_case(expected, overreported, expected_abstention=False, observed_abstention=False)["precision"] >= 0.90,
                "over-reporting should fail the precision bar",
            ),
        )
    )

    wrong_citation = [{"criterion_id": "citation_support", "severity": "high", "evidence_support": "unsupported"}]
    mutations.append(
        (
            "M06",
            "accept unsupported citation",
            lambda: require(
                score_case(expected, wrong_citation, expected_abstention=False, observed_abstention=False)["citation_support_correct"] >= 0.95,
                "unsupported citation should fail the support bar",
            ),
        )
    )

    mutations.append(
        (
            "M07",
            "fail to abstain on insufficient evidence",
            lambda: require(
                score_case([], [], expected_abstention=True, observed_abstention=False)["abstention_correct"],
                "incorrect abstention should fail the abstention bar",
            ),
        )
    )

    wrong_namespace = dict(RUNTIME_FINDING_CODE_MAP)
    wrong_namespace["s16.harness.mutation_kill"] = "mutation_kill"
    mutations.append(("M08", "map harness assertion to runtime finding", lambda: validate_assertion_namespaces(wrong_namespace)))

    wrong_severity = [{"criterion_id": "citation_support", "severity": "low", "evidence_support": "supported"}]
    mutations.append(
        (
            "M09",
            "accept severity outside adjacent tier",
            lambda: require(
                score_case(expected, wrong_severity, expected_abstention=False, observed_abstention=False)["severity_within_one_tier"] >= 0.90,
                "severity error should fail the severity bar",
            ),
        )
    )

    unjustified_confidence = [{"criterion_id": "citation_support", "severity": "high", "evidence_support": "unsupported", "confidence": 0.95}]
    mutations.append(
        (
            "M10",
            "accept unjustified confidence",
            lambda: require(
                score_case(expected, unjustified_confidence, expected_abstention=False, observed_abstention=False)["confidence_error"] <= 0.10,
                "confidence error should fail the confidence bar",
            ),
        )
    )

    for mutation_id, description, action in mutations:
        print(f"{mutation_id} {description}", end=" -> ")
        expect_rejection(mutation_id, action)
    print(f"mutation_killed={len(mutations)}/{len(mutations)}")


if __name__ == "__main__":
    main()
