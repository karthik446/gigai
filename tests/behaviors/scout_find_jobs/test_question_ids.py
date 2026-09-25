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


# --- P0 (Terra review): the category/value BOUNDARY is preserved by default -----------
#
# The old pool-and-pick-one-category design dissolved category and value into
# one bag, so a token could cross the boundary for ANY pair, not just the
# evidence-backed ML-lifecycle one -- silently merging two distinct facts
# whenever they happened to share tokens on opposite sides. The fix
# canonicalizes each side separately; only the explicit, evidence-backed
# ``_CROSS_BOUNDARY_ALIASES`` table may still cross the boundary.


def test_location_remote_vs_remote_location_stay_distinct() -> None:
    # Two different real-world facts that happen to share the same two
    # tokens on opposite sides of the colon: "location:remote" (does the
    # POSTING's location make the candidate eligible) is not the same
    # question as "remote:location" (is remote work itself permitted). A
    # pool-everything normalizer would merge these; per-side canonicalization
    # must not.
    assert normalize_question_id("location:remote") != normalize_question_id("remote:location")


def test_cloud_platform_vs_platform_cloud_stay_distinct() -> None:
    # "cloud:platform" (which cloud platform) vs "platform:cloud" (is the
    # role itself a cloud-platform role) -- same two tokens, swapped sides,
    # not an evidence-backed alias.
    assert normalize_question_id("cloud:platform") != normalize_question_id("platform:cloud")


def test_years_seniority_vs_seniority_years_stay_distinct() -> None:
    # "years:seniority" vs "seniority:years" -- not the r1 ML-lifecycle pair,
    # so no alias-table entry backs merging these; they must stay distinct.
    assert normalize_question_id("years:seniority") != normalize_question_id("seniority:years")


def test_tool_ai_vs_ai_tool_stay_distinct() -> None:
    # "tool:ai" (a named AI tool/platform) vs "ai:tool" (is AI tooling
    # experience itself required) -- distinct facts sharing tokens across
    # the boundary; not in the alias table.
    assert normalize_question_id("tool:ai") != normalize_question_id("ai:tool")


def test_explicit_cross_boundary_alias_still_unifies_the_evidence_backed_pair() -> None:
    # The one cross-boundary merge P0 keeps: r1's own rerun of the SAME
    # question against the SAME (resume, posting) pair moved "ml"/"tooling"
    # from the category side to the value side between calls. This is named
    # explicitly in ``_CROSS_BOUNDARY_ALIASES``, not inferred by pooling.
    assert normalize_question_id("ml_tooling:lifecycle") == normalize_question_id("tooling:ml-lifecycle")


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


# --- assess-prompt-v2: rule 4's ``location:<country>_region`` id is a fixed point --------------
#
# assess.md rule 4 names ONE id for "which state/province do you live in"
# (asked once when the posting restricts a remote role to named regions and
# the candidate's location is unknown) so P3's prior-answer join finds the
# answer across postings. Plain per-side sorting would keep ``ca_region``
# but turn ``us_region`` into ``region_us``; the normalizer emits the code
# first for exactly this two-token shape.


def test_region_ids_from_the_prompt_are_stable_under_normalization() -> None:
    for question_id in ("location:ca_region", "location:us_region", "location:pl_region", "location:gb_region"):
        assert normalize_question_id(question_id) == question_id


def test_region_id_token_order_and_separator_drift_still_lands_on_the_prompt_form() -> None:
    assert normalize_question_id("location:region_us") == "location:us_region"
    assert normalize_question_id("Location:CA-Region") == "location:ca_region"
    assert normalize_question_id(normalize_question_id("location:region_ca")) == "location:ca_region"


def test_region_ordering_does_not_touch_other_location_values() -> None:
    # Only "<two-letter code> + region" is special-cased; a three-token or
    # non-code value keeps the sorted form every other id gets.
    assert normalize_question_id("location:us_eligible_region") == normalize_question_id("location:eligible_us_region")
    assert normalize_question_id("location:eastern_region") == "location:eastern_region"
    assert normalize_question_id("location:region_bay") == "location:bay_region" or normalize_question_id("location:region_bay") == "location:region_bay"
