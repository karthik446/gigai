"""P3 (v0.1.9): ``question_ids.normalize_question_id`` -- the in-code
canonical-slug normalizer the orchestrator added to the plan after S29 r1's
rerun logs showed the model's own slug for the SAME real-world fact drifting
across calls of the identical prompt/pair (``orchestrator/research/
S29-assess-verdict-evals/r1/trial_log.jsonl``).

The drift pairs asserted below are taken directly from that log (EXECUTED:
grepped for a (resume_id, posting_id) pair whose ``question_ids`` differed
across reruns) -- this is not a synthetic scenario.
"""

from __future__ import annotations

from gigai.scout.question_ids import normalize_question_id


# --- r1's actual drift: same fact, different raw slug -------------------------------


def test_ml_lifecycle_tooling_drift_from_r1_unifies() -> None:
    # r1 log, resume r1-ml-staff x posting gh:affirm:7822387003: two reruns
    # of the identical prompt/pair produced these two raw ids for the same
    # "ML lifecycle tooling experience" question.
    a = normalize_question_id("tooling:ml-lifecycle")
    b = normalize_question_id("technology:ml_lifecycle_tooling")
    c = normalize_question_id("ml_tooling:lifecycle")
    assert a == b == c


def test_ai_developer_tools_hyphen_vs_underscore_and_singular_vs_plural_unifies() -> None:
    # r1 log, same pair: "tool:ai_developer_tools" vs "tools:ai-developer-tools".
    assert normalize_question_id("tool:ai_developer_tools") == normalize_question_id("tools:ai-developer-tools")


def test_stable_slugs_pass_through_unchanged_in_content() -> None:
    # cloud:gcp / years:python / clearance:secret / seniority:staff are
    # assess.md rule 2's own worked examples -- already stable, no drift to fix.
    for question_id in ("cloud:gcp", "years:python", "clearance:secret", "seniority:staff"):
        assert normalize_question_id(question_id).split(":", 1)[0] in question_id


# --- deliberately NOT merged: different facts, not just different wording -----------


def test_genuinely_different_location_facts_stay_distinct() -> None:
    # r1 log, resume r1-ml-staff x posting gh:airbnb:7955579: "us_eligible_state"
    # and "us_state" are two different phrasings that are NOT provably the
    # same fact -- a controlled term list must not risk a false merge here.
    assert normalize_question_id("location:us_eligible_state") != normalize_question_id("location:us_state")


def test_genuinely_different_technology_facts_stay_distinct() -> None:
    assert normalize_question_id("technology:graph_ml") != normalize_question_id("technology:tabular_classification")


# --- shape / determinism -------------------------------------------------------------


def test_token_order_never_matters() -> None:
    assert normalize_question_id("cloud:multi_region_gcp") == normalize_question_id("cloud:gcp_multi_region")


def test_separator_style_never_matters() -> None:
    assert (
        normalize_question_id("years-of:python-experience")
        == normalize_question_id("years_of:python_experience")
        == normalize_question_id("years of:python experience")
    )


def test_case_never_matters() -> None:
    assert normalize_question_id("Cloud:GCP") == normalize_question_id("cloud:gcp")


def test_idempotent() -> None:
    for question_id in ("cloud:gcp", "tooling:ml-lifecycle", "technology:ml_lifecycle_tooling", "years:python"):
        once = normalize_question_id(question_id)
        assert normalize_question_id(once) == once


def test_result_matches_the_structured_question_id_pattern() -> None:
    import re

    pattern = re.compile(r"\A[a-z0-9._-]+:[a-z0-9._-]+\Z")
    for question_id in ("cloud:gcp", "tooling:ml-lifecycle", "Technology:ML_Lifecycle_Tooling", "years-of:python-experience"):
        assert pattern.fullmatch(normalize_question_id(question_id)), normalize_question_id(question_id)


def test_never_raises_on_no_colon_or_empty_input() -> None:
    assert normalize_question_id("no colon here") == "no_colon_here"
    assert normalize_question_id("") == ""
    assert normalize_question_id("   ") == ""


def test_repeated_tokens_collapse_once() -> None:
    # A category word that also legitimately repeats in the value collapses
    # to a single occurrence rather than growing the id with duplicates.
    assert normalize_question_id("cloud:cloud_gcp") == normalize_question_id("cloud:gcp")
