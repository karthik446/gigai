"""Executable, offline evidence for the S16-EVAL methodology spike.

This module is deliberately outside ``src/gigai``.  It defines the corpus,
labels, quality metrics, and fixed calibration bar without changing GigAI's
runtime evaluator, schemas, provider ports, or Goal execution behavior.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Any, Iterable, Mapping


SPLIT_COUNTS = {
    "development": 4,
    "calibration": 2,
    "final_held_out_acceptance": 2,
}

BEHAVIORS = (
    ("artifact_tamper", "G16-10", "tampered artifact rejection"),
    ("missing_reference", "G16-10", "missing-reference rejection"),
    ("invented_citation", "G16-10", "invented-citation rejection"),
    ("missing_citation", "S16-EXT", "missing-citation detection"),
    ("citation_support", "S16-EXT", "citation supports the attached claim"),
    ("duplicate_merge", "S16-EXT", "duplicate finding merge with provenance"),
    ("disagreement_adjudication", "G16-10", "disagreement preservation and adjudication"),
    ("unsupported_clarification", "G16-10", "unsupported clarification rejection"),
    ("missing_context_block", "S16-EXT", "missing-context blocking clarification"),
    ("partial_address", "G16-10", "partial-address detection at closure"),
    ("deferred_feedback", "G16-10", "deferred-feedback non-reapplication"),
    ("cycle_exhaustion", "G16-10", "cycle-exhaustion rejection"),
    ("malformed_loop_state", "G16-10", "malformed loop-state rejection"),
    ("parent_mismatch", "G16-10", "addressed-artifact parent-mismatch rejection"),
    ("overreporting", "S16-EXT", "valid no-finding versus over-reporting"),
    ("severity_confidence", "S16-EXT", "severity and confidence correctness"),
    ("abstention_insufficient", "S16-EXT", "abstention when evidence is insufficient"),
    ("nonabstention_sufficient", "S16-EXT", "non-abstention when evidence is sufficient"),
)

BEHAVIOR_IDS = tuple(item[0] for item in BEHAVIORS)

ASSERTION_IDS = (
    "s16.case.expected_findings",
    "s16.case.precision",
    "s16.case.citation_existence",
    "s16.case.citation_support",
    "s16.case.severity_confidence",
    "s16.case.abstention",
    "s16.loop.duplicate_provenance",
    "s16.loop.disagreement_visibility",
    "s16.loop.feedback_traceability",
    "s16.loop.closure_partial_address",
    "s16.loop.rejected_feedback_non_reapplication",
    "s16.loop.blocking_clarification",
    "s16.loop.cycle_cap",
    "s16.loop.replay_stability",
    "s16.harness.mutation_kill",
    "s16.harness.corpus_completeness",
    "s16.calibration.final_holdout_only",
)

RUNTIME_FINDING_CODE_MAP = {
    "s16.case.expected_findings": "seeded_defect_recall",
    "s16.case.citation_support": "citation_support",
    "s16.loop.duplicate_provenance": "duplicate_finding_provenance",
    "s16.loop.disagreement_visibility": "disagreement_preserved",
    "s16.loop.closure_partial_address": "partial_address",
}

ACCEPTANCE_BAR = {
    "expected_finding_recall_min": 0.90,
    "precision_min": 0.90,
    "false_positive_rate_max": 0.10,
    "citation_support_min": 0.95,
    "severity_within_one_tier_min": 0.90,
    "confidence_ece_max": 0.10,
    "abstention_sensitivity_min": 0.90,
    "abstention_specificity_min": 0.90,
    "critical_forbidden_findings_max": 0,
}


def _case_categories(index: int) -> tuple[str, ...]:
    categories = ("positive", "negative", "ambiguous", "incomplete-reference")
    if index < 4:
        return (categories[index],)
    return (categories[index % 2], categories[(index + 1) % 4])


def _expected_findings(behavior_id: str, case_id: str) -> list[dict[str, Any]]:
    if behavior_id in {
        "artifact_tamper",
        "missing_reference",
        "unsupported_clarification",
        "missing_context_block",
        "cycle_exhaustion",
        "malformed_loop_state",
        "parent_mismatch",
        "abstention_insufficient",
    }:
        return []
    if behavior_id == "overreporting":
        return []
    return [
        {
            "finding_id": f"expected-{case_id}",
            "criterion_id": behavior_id,
            "severity": "high" if behavior_id in {"invented_citation", "citation_support"} else "medium",
            "confidence": 0.95,
            "evidence_support": "supported" if behavior_id != "missing_citation" else "missing",
        }
    ]


def build_case_manifest() -> dict[str, Any]:
    """Build deterministic labeled cases and exact 4/2/2 coverage assignments."""

    cases: list[dict[str, Any]] = []
    coverage: dict[str, list[str]] = {behavior_id: [] for behavior_id in BEHAVIOR_IDS}
    behavior_index = {behavior_id: index for index, (behavior_id, _, _) in enumerate(BEHAVIORS)}
    for behavior_id, source, label in BEHAVIORS:
        for split, count in SPLIT_COUNTS.items():
            for index in range(count):
                case_id = f"{behavior_id}-{split}-{index + 1:02d}"
                categories = _case_categories(index + behavior_index[behavior_id])
                source_text = (
                    f"S16-EVAL fixture source\nbehavior={behavior_id}\n"
                    f"case={case_id}\nlabel={label}\n"
                ).encode("utf-8")
                case = {
                    "case_id": case_id,
                    "split": split,
                    "behavior_ids": [behavior_id],
                    "category_labels": list(categories),
                    "source_bytes_sha256": sha256(source_text).hexdigest(),
                    "expected_findings": _expected_findings(behavior_id, case_id),
                    "acceptable_alternatives": ["unanswerable"]
                    if behavior_id in {"missing_context_block", "abstention_insufficient"}
                    else [],
                    "forbidden_findings": [f"forbidden-{behavior_id}"],
                    "expected_abstention": behavior_id
                    in {"missing_context_block", "abstention_insufficient"},
                    "expected_severity": "high"
                    if behavior_id in {"invented_citation", "citation_support"}
                    else None,
                    "expected_confidence_min": 0.90
                    if behavior_id not in {"missing_context_block", "abstention_insufficient"}
                    else None,
                }
                cases.append(case)
                coverage[behavior_id].append(case_id)
    return {
        "schema_version": "1.0",
        "corpus_id": "s16-eval-prealpha-1",
        "matrix_version": "s16-eval-matrix-1",
        "coverage_per_behavior": 8,
        "split_counts": SPLIT_COUNTS,
        "category_labels": ["positive", "negative", "ambiguous", "incomplete-reference"],
        "contamination_rule": "A case used to tune taxonomy, prompt, threshold, rubric, judge, or harness cannot certify the final bar.",
        "coverage": coverage,
        "tuning_case_ids": [],
        "cases": cases,
    }


def validate_case_manifest(manifest: Mapping[str, Any]) -> None:
    """Reject corpus-shape and contamination mutations deterministically."""

    if tuple(manifest.get("split_counts", {})) != tuple(SPLIT_COUNTS):
        raise ValueError("split names are not the fixed S16-EVAL split set")
    if dict(manifest.get("split_counts", {})) != SPLIT_COUNTS:
        raise ValueError("split counts are not the fixed 4/2/2 matrix")
    if manifest.get("coverage_per_behavior") != 8:
        raise ValueError("coverage floor is not exactly eight cases per behavior")
    if set(manifest.get("coverage", {})) != set(BEHAVIOR_IDS):
        raise ValueError("coverage matrix does not contain exactly the fixed behaviors")

    cases = list(manifest.get("cases", []))
    by_id = {case.get("case_id"): case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("case IDs are not unique")
    if len(cases) != len(BEHAVIOR_IDS) * sum(SPLIT_COUNTS.values()):
        raise ValueError("case assignment total is incomplete")

    required_categories = set(manifest.get("category_labels", []))
    if required_categories != {"positive", "negative", "ambiguous", "incomplete-reference"}:
        raise ValueError("category label set is incomplete")
    for behavior_id, case_ids in manifest["coverage"].items():
        if len(case_ids) != 8 or len(set(case_ids)) != 8:
            raise ValueError(f"{behavior_id} does not have eight unique assignments")
        counts = Counter(by_id[case_id]["split"] for case_id in case_ids)
        if dict(counts) != dict(SPLIT_COUNTS):
            raise ValueError(f"{behavior_id} does not have the fixed 4/2/2 split")
        if any(behavior_id not in by_id[case_id]["behavior_ids"] for case_id in case_ids):
            raise ValueError(f"{behavior_id} coverage points at the wrong behavior")

    for split in SPLIT_COUNTS:
        labels = {
            label
            for case in cases
            if case.get("split") == split
            for label in case.get("category_labels", [])
        }
        if labels != required_categories:
            raise ValueError(f"{split} does not contain every required category label")

    tuning_ids = set(manifest.get("tuning_case_ids", []))
    final_ids = {case["case_id"] for case in cases if case.get("split") == "final_held_out_acceptance"}
    if tuning_ids & final_ids:
        raise ValueError("final held-out cases were used during tuning")


def validate_assertion_namespaces(
    runtime_finding_code_map: Mapping[str, str] = RUNTIME_FINDING_CODE_MAP,
) -> None:
    """Keep harness assertion IDs separate from runtime Finding codes."""

    unknown = set(runtime_finding_code_map) - set(ASSERTION_IDS)
    if unknown:
        raise ValueError(f"runtime map contains unknown assertion IDs: {sorted(unknown)}")
    harness_ids = {assertion_id for assertion_id in ASSERTION_IDS if assertion_id.startswith("s16.harness.")}
    leaked = harness_ids & set(runtime_finding_code_map)
    if leaked:
        raise ValueError(f"harness assertions leaked into runtime finding codes: {sorted(leaked)}")


def _finding_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        finding.get("criterion_id"),
        finding.get("severity"),
        finding.get("evidence_support"),
    )


def score_case(
    expected_findings: Iterable[Mapping[str, Any]],
    observed_findings: Iterable[Mapping[str, Any]],
    *,
    expected_abstention: bool,
    observed_abstention: bool,
    forbidden_findings: Iterable[str] = (),
) -> dict[str, Any]:
    """Score one labeled case; finding identity is independent of prose wording."""

    expected = list(expected_findings)
    observed = list(observed_findings)
    expected_keys = {_finding_key(item) for item in expected}
    observed_keys = {_finding_key(item) for item in observed}
    expected_criteria = {item.get("criterion_id") for item in expected}
    observed_criteria = {item.get("criterion_id") for item in observed}
    true_positive = len(expected_criteria & observed_criteria)
    false_positive = len(observed_criteria - expected_criteria)
    false_negative = len(expected_criteria - observed_criteria)
    precision = true_positive / len(observed_criteria) if observed_criteria else (1.0 if not expected_criteria else 0.0)
    recall = true_positive / len(expected_criteria) if expected_criteria else (1.0 if not observed_criteria else 0.0)
    expected_by_criterion = {item.get("criterion_id"): item for item in expected}
    observed_by_criterion = {item.get("criterion_id"): item for item in observed}
    matched = set(expected_by_criterion) & set(observed_by_criterion)
    citation_support_correct = (
        sum(
            observed_by_criterion[key].get("evidence_support")
            == expected_by_criterion[key].get("evidence_support")
            for key in matched
        )
        / len(matched)
        if matched
        else 1.0
    )
    severity_tiers = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    severity_within_one_tier = (
        sum(
            abs(
                severity_tiers.get(observed_by_criterion[key].get("severity"), -99)
                - severity_tiers.get(expected_by_criterion[key].get("severity"), -99)
            )
            <= 1
            for key in matched
        )
        / len(matched)
        if matched
        else 1.0
    )
    confidence_values = [
        abs(
            float(observed_by_criterion[key].get("confidence", 0.0))
            - (1.0 if observed_by_criterion[key].get("evidence_support") == expected_by_criterion[key].get("evidence_support") else 0.0)
        )
        for key in matched
        if "confidence" in observed_by_criterion[key]
    ]
    confidence_error = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else 0.0
    )
    forbidden = set(forbidden_findings)
    critical_forbidden_findings = len(observed_criteria & forbidden)
    abstention_correct = expected_abstention == observed_abstention
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "false_positive_rate": 1.0 if false_positive else 0.0,
        "abstention_correct": abstention_correct,
        "citation_support_correct": citation_support_correct,
        "severity_within_one_tier": severity_within_one_tier,
        "confidence_error": confidence_error,
        "critical_forbidden_findings": critical_forbidden_findings,
    }


def score_dataset(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate case scores while retaining split-level traceability."""

    rows = list(records)
    if not rows:
        raise ValueError("cannot score an empty dataset")
    totals = Counter()
    abstention_correct = 0
    citation_support = 0.0
    severity_correct = 0.0
    confidence_errors = 0.0
    abstention_positive = abstention_positive_correct = 0
    abstention_negative = abstention_negative_correct = 0
    forbidden_findings = 0
    for row in rows:
        score = score_case(
            row["expected_findings"],
            row["observed_findings"],
            expected_abstention=bool(row["expected_abstention"]),
            observed_abstention=bool(row["observed_abstention"]),
            forbidden_findings=row.get("forbidden_findings", []),
        )
        totals.update({key: score[key] for key in ("true_positive", "false_positive", "false_negative")})
        abstention_correct += int(score["abstention_correct"])
        citation_support += score["citation_support_correct"]
        severity_correct += score["severity_within_one_tier"]
        confidence_errors += score["confidence_error"]
        forbidden_findings += score["critical_forbidden_findings"]
        if row["expected_abstention"]:
            abstention_positive += 1
            abstention_positive_correct += int(score["abstention_correct"])
        else:
            abstention_negative += 1
            abstention_negative_correct += int(score["abstention_correct"])
    total_expected = totals["true_positive"] + totals["false_negative"]
    total_observed = totals["true_positive"] + totals["false_positive"]
    return {
        "case_count": len(rows),
        "precision": totals["true_positive"] / total_observed if total_observed else 1.0,
        "recall": totals["true_positive"] / total_expected if total_expected else 1.0,
        "false_positive_rate": totals["false_positive"] / total_observed if total_observed else 0.0,
        "abstention_accuracy": abstention_correct / len(rows),
        "citation_support_correctness": citation_support / len(rows),
        "severity_within_one_tier": severity_correct / len(rows),
        "confidence_ece": confidence_errors / len(rows),
        "abstention_sensitivity": abstention_positive_correct / abstention_positive if abstention_positive else 1.0,
        "abstention_specificity": abstention_negative_correct / abstention_negative if abstention_negative else 1.0,
        "critical_forbidden_findings": forbidden_findings,
    }
