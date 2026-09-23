from __future__ import annotations

from copy import deepcopy

from research.s16_eval.methodology import (
    ACCEPTANCE_BAR,
    ASSERTION_IDS,
    BEHAVIORS,
    RUNTIME_FINDING_CODE_MAP,
    SPLIT_COUNTS,
    build_case_manifest,
    score_case,
    score_dataset,
    validate_assertion_namespaces,
    validate_case_manifest,
)


def test_fixed_matrix_has_eighteen_behaviors_and_g16_mapping() -> None:
    manifest = build_case_manifest()
    validate_case_manifest(manifest)
    assert len(BEHAVIORS) == 18
    assert len(manifest["coverage"]) == 18
    assert sum(source == "G16-10" for _, source, _ in BEHAVIORS) == 10
    assert sum(source == "S16-EXT" for _, source, _ in BEHAVIORS) == 8


def test_every_behavior_has_exact_4_2_2_coverage() -> None:
    manifest = build_case_manifest()
    cases_by_id = {case["case_id"]: case for case in manifest["cases"]}
    for behavior_id, case_ids in manifest["coverage"].items():
        assert len(case_ids) == 8
        assert {cases_by_id[case_id]["split"] for case_id in case_ids} == set(SPLIT_COUNTS)
        assert {
            sum(cases_by_id[case_id]["split"] == split for case_id in case_ids)
            for split in SPLIT_COUNTS
        } == {2, 4}
        assert all(behavior_id in cases_by_id[case_id]["behavior_ids"] for case_id in case_ids)


def test_category_labels_cover_every_split() -> None:
    manifest = build_case_manifest()
    for split in SPLIT_COUNTS:
        labels = {
            label
            for case in manifest["cases"]
            if case["split"] == split
            for label in case["category_labels"]
        }
        assert labels == set(manifest["category_labels"])


def test_case_labels_include_expected_forbidden_and_abstention_fields() -> None:
    manifest = build_case_manifest()
    for case in manifest["cases"]:
        assert case["source_bytes_sha256"]
        assert "expected_findings" in case
        assert "acceptable_alternatives" in case
        assert case["forbidden_findings"]
        assert isinstance(case["expected_abstention"], bool)


def test_quality_scorer_rejects_overreporting_and_rewards_exact_output() -> None:
    expected = [{"criterion_id": "citation_support", "severity": "high", "evidence_support": "supported"}]
    exact = score_case(expected, expected, expected_abstention=False, observed_abstention=False)
    overreported = score_case(
        expected,
        expected + [{"criterion_id": "invented", "severity": "high", "evidence_support": "unsupported"}],
        expected_abstention=False,
        observed_abstention=False,
    )
    assert exact["precision"] == exact["recall"] == 1.0
    assert overreported["precision"] < 1.0
    assert overreported["false_positive"] == 1


def test_quality_scorer_handles_empty_expected_findings_and_abstention() -> None:
    correct = score_case([], [], expected_abstention=True, observed_abstention=True)
    fabricated = score_case(
        [],
        [{"criterion_id": "fabricated", "severity": "high", "evidence_support": "unsupported"}],
        expected_abstention=True,
        observed_abstention=False,
    )
    assert correct["precision"] == correct["recall"] == 1.0
    assert correct["abstention_correct"] is True
    assert fabricated["precision"] == 0.0
    assert fabricated["abstention_correct"] is False


def test_dataset_scoring_is_traceable_and_bar_is_normative() -> None:
    records = [
        {
            "expected_findings": [{"criterion_id": "x", "severity": "medium", "evidence_support": "supported"}],
            "observed_findings": [{"criterion_id": "x", "severity": "medium", "evidence_support": "supported"}],
            "expected_abstention": False,
            "observed_abstention": False,
        },
        {
            "expected_findings": [],
            "observed_findings": [],
            "expected_abstention": True,
            "observed_abstention": True,
        },
    ]
    result = score_dataset(records)
    assert result == {
        "case_count": 2,
        "precision": 1.0,
        "recall": 1.0,
        "false_positive_rate": 0.0,
        "abstention_accuracy": 1.0,
        "citation_support_correctness": 1.0,
        "severity_within_one_tier": 1.0,
        "confidence_ece": 0.0,
        "abstention_sensitivity": 1.0,
        "abstention_specificity": 1.0,
        "critical_forbidden_findings": 0,
    }
    assert ACCEPTANCE_BAR["critical_forbidden_findings_max"] == 0
    assert "s16.case.precision" in ASSERTION_IDS
    assert RUNTIME_FINDING_CODE_MAP["s16.case.citation_support"] == "citation_support"
    assert "s16.harness.mutation_kill" not in RUNTIME_FINDING_CODE_MAP


def test_manifest_mutations_are_rejected() -> None:
    manifest = build_case_manifest()

    missing_behavior = deepcopy(manifest)
    del missing_behavior["coverage"][BEHAVIORS[0][0]]
    try:
        validate_case_manifest(missing_behavior)
    except ValueError as error:
        assert "exactly the fixed behaviors" in str(error)
    else:
        raise AssertionError("missing behavior mutation was not caught")

    bad_split = deepcopy(manifest)
    bad_split["split_counts"]["calibration"] = 3
    try:
        validate_case_manifest(bad_split)
    except ValueError as error:
        assert "4/2/2" in str(error)
    else:
        raise AssertionError("split mutation was not caught")

    bad_category = deepcopy(manifest)
    bad_category["category_labels"] = ["positive", "negative", "ambiguous"]
    try:
        validate_case_manifest(bad_category)
    except ValueError as error:
        assert "category label set" in str(error)
    else:
        raise AssertionError("category mutation was not caught")

    contaminated = deepcopy(manifest)
    final_case = next(case for case in contaminated["cases"] if case["split"] == "final_held_out_acceptance")
    contaminated["tuning_case_ids"] = [final_case["case_id"]]
    try:
        validate_case_manifest(contaminated)
    except ValueError as error:
        assert "tuning" in str(error)
    else:
        raise AssertionError("contamination mutation was not caught")


def test_namespace_mutation_is_rejected() -> None:
    mapping = dict(RUNTIME_FINDING_CODE_MAP)
    mapping["s16.harness.mutation_kill"] = "mutation_kill"
    try:
        validate_assertion_namespaces(mapping)
    except ValueError as error:
        assert "harness assertions" in str(error)
    else:
        raise AssertionError("namespace mutation was not caught")


def test_extended_quality_metrics_are_computed() -> None:
    row = {
        "expected_findings": [{"criterion_id": "x", "severity": "high", "evidence_support": "supported"}],
        "observed_findings": [{"criterion_id": "x", "severity": "medium", "evidence_support": "unsupported", "confidence": 0.8}],
        "expected_abstention": False,
        "observed_abstention": False,
        "forbidden_findings": ["fabricated"],
    }
    result = score_dataset([row])
    assert result["citation_support_correctness"] == 0.0
    assert result["severity_within_one_tier"] == 1.0
    assert result["confidence_ece"] == 0.8
    assert result["abstention_specificity"] == 1.0
