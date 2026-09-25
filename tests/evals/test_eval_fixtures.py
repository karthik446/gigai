"""P7 (v0.1.9): the assess eval set is well-formed and its metrics compute as specified -- offline.

Nothing here calls a model, Jev or the network: the fixtures parse, every
label references an existing pair, ids match the shipped ``question_id``
shape, every row renders through the shipped prompt without truncation, and
``summarize`` produces each planned metric from synthetic rows.  The label
policy of 2026-09-25 is checked too: no ``sponsorship:*``/``visa:*`` expected
id (rule F) and ``excluded`` rows (rule E) enter no plan and no metric.  Runs in
``make unit-tests`` (the fast_unit classifier sees no filesystem, process or
network seam in this file).
"""

from __future__ import annotations

from typing import Any

from gigai.scout.assessment_core import _MAX_PROMPT_POSTING_TEXT, _MAX_PROMPT_RESUME_TEXT, AssessContext, AssessJob, render_assess_prompt

from tests.evals import run_assess_eval as harness

_COUNTRY_WORDS = {"US": "United States", "CA": "Canada", "PL": "Poland", "GB": "United Kingdom"}


# --- fixtures ---------------------------------------------------------------------------


def test_postings_fixture_is_frozen_real_text_that_fits_the_prompt() -> None:
    postings = harness.load_postings()
    assert len(postings) == 15
    for posting_id, posting in postings.items():
        assert posting_id.startswith("gh:")
        assert posting.title.strip() == posting.title and posting.title
        assert posting.company and posting.location and posting.url.startswith("https://")
        assert len(posting.full_text) >= 1000
        # A hard requirement hidden past the prompt's bound would make a label unfair.
        assert len(posting.full_text) <= _MAX_PROMPT_POSTING_TEXT


def test_resume_index_settings_are_config_shaped() -> None:
    resumes = harness.load_resumes()
    postings = harness.load_postings()
    clean_fits = [resume for resume in resumes.values() if resume.kind == "clean_fit"]
    assert len(clean_fits) >= 5
    assert len({resume.clean_fit_posting_id for resume in clean_fits}) == len(clean_fits)
    for resume in resumes.values():
        assert resume.text.strip()
        assert len(resume.text) <= _MAX_PROMPT_RESUME_TEXT
        assert resume.countries and all(harness.COUNTRY_CODE_RE.fullmatch(code) for code in resume.countries)
        assert resume.titles and all(title.strip() for title in resume.titles)
        assert resume.kind in {"synthetic", "clean_fit"}
        if resume.kind == "clean_fit":
            assert resume.clean_fit_posting_id in postings
            # The residency the countries setting asserts is also stated in the resume text.
            assert any(_COUNTRY_WORDS[code] in resume.text for code in resume.countries)
        else:
            assert resume.clean_fit_posting_id is None


def test_labels_reference_fixture_pairs_and_use_shipped_id_shapes() -> None:
    resumes = harness.load_resumes()
    postings = harness.load_postings()
    labels = harness.load_labels(include_excluded=True)
    assert len(labels) >= 20
    assert len({label.key for label in labels}) == len(labels)
    clean_rows_by_resume: dict[str, int] = {}
    for label in labels:
        assert label.resume_id in resumes, label
        assert label.posting_id in postings, label
        assert label.expected_verdict in harness.VERDICTS, label
        assert label.source and label.notes
        for question_id in label.expected_question_ids:
            assert harness.QUESTION_ID_RE.fullmatch(question_id), question_id
            assert harness.SCHEMA_QUESTION_ID_RE.fullmatch(question_id), question_id
            # Rule F: sponsorship is never an ask -- the posting decides it or it stays unknown.
            assert harness.question_category(question_id) not in {"sponsorship", "visa"}, (label.key, question_id)
        if label.expected_verdict == "matched_above_threshold":
            assert label.expected_question_ids == ()
        if label.expected_verdict == "pending_user_answers":
            assert label.expected_question_ids
        if label.clean_fit:
            assert label.expected_verdict == "matched_above_threshold"
            assert not label.uncertain
            assert resumes[label.resume_id].kind == "clean_fit"
            assert resumes[label.resume_id].clean_fit_posting_id == label.posting_id
            clean_rows_by_resume[label.resume_id] = clean_rows_by_resume.get(label.resume_id, 0) + 1
    clean_fit_resumes = {resume.resume_id for resume in resumes.values() if resume.kind == "clean_fit"}
    assert clean_rows_by_resume == {resume_id: 1 for resume_id in clean_fit_resumes}


def test_excluded_rows_are_well_formed_but_never_scored() -> None:
    every = harness.load_labels(include_excluded=True)
    scored = harness.load_labels()
    excluded = [label for label in every if label.excluded]
    # Rule E (US-only): the Canadian rows are the excluded ones, and only they are.
    postings = harness.load_postings()
    assert excluded and all(postings[label.posting_id].location == "Remote Canada" for label in excluded)
    assert {label.key for label in every if postings[label.posting_id].location == "Remote Canada"} == {label.key for label in excluded}
    assert [label.key for label in scored] == [label.key for label in every if not label.excluded]
    assert not any(label.excluded for label in harness.plan_rows(every))
    assert harness.plan_rows(every) == harness.plan_rows(scored)
    assert not harness.plan_rows(every, resume_ids=(excluded[0].resume_id,), posting_ids=(excluded[0].posting_id,))


def test_every_posting_has_a_cross_profile_or_clean_fit_twin_where_labelled() -> None:
    labels = harness.load_labels()  # over the scored rows only
    by_posting: dict[str, set[str]] = {}
    for label in labels:
        by_posting.setdefault(label.posting_id, set()).add(label.expected_verdict)
    twins = [posting_id for posting_id, verdicts in by_posting.items() if len(verdicts) >= 2]
    assert len(twins) >= 6


def test_every_row_renders_through_the_shipped_prompt_untruncated() -> None:
    resumes = harness.load_resumes()
    postings = harness.load_postings()
    for label in harness.load_labels(include_excluded=True):
        posting = postings[label.posting_id]
        resume = resumes[label.resume_id]
        prompt = render_assess_prompt(
            AssessJob(title=posting.title, company=posting.company, location=posting.location, posting_text=posting.full_text),
            AssessContext(resume_text=resume.text, visa_sponsorship_required=resume.visa_sponsorship_required, countries=resume.countries, titles=resume.titles),
        )
        assert posting.full_text in prompt
        assert resume.text in prompt
        assert ", ".join(resume.countries) in prompt
        assert ", ".join(resume.titles) in prompt
        assert "{{" not in prompt


# --- planning and helpers -----------------------------------------------------------------


def test_plan_rows_calls_clean_fits_first_then_confident_then_uncertain() -> None:
    labels = harness.load_labels()
    planned = harness.plan_rows(labels)
    assert len(planned) == len(labels)
    tiers = [0 if label.clean_fit else 1 if not label.uncertain else 2 for label in planned]
    assert tiers == sorted(tiers)
    assert all(label.clean_fit for label in planned[: harness.DEFAULT_MAX_CALLS][: tiers.count(0)])
    sampled = harness.plan_rows(labels, sample=5, seed=7)
    assert len(sampled) == 5
    assert sampled == harness.plan_rows(labels, sample=5, seed=7)
    only_clean = harness.plan_rows(labels, clean_fit_only=True)
    assert only_clean and all(label.clean_fit for label in only_clean)
    one_resume = harness.plan_rows(labels, resume_ids=("r5-platform-sre-senior",))
    assert one_resume and all(label.resume_id == "r5-platform-sre-senior" for label in one_resume)


def test_question_helpers() -> None:
    assert harness.question_category("cloud:aws") == "cloud"
    assert harness.question_tokens("cloud:aws") == ("aws",)
    assert harness.question_tokens("language:python_or_kotlin") == ("python", "kotlin")
    assert harness.possible_false_ask("cloud:aws", "Deep Terraform, AWS/GCP experience")
    assert not harness.possible_false_ask("cloud:gcp", "AWS only")
    assert not harness.possible_false_ask("years:backend_systems", "backend engineer, 6 years")
    exact, by_category = harness.question_hits(["cloud:aws", "database:mysql"], ["cloud:aws", "database:mysql_or_postgres", "tool:ide"])
    assert exact == {"cloud:aws"}
    assert by_category == {"cloud:aws", "database:mysql"}


# --- metrics --------------------------------------------------------------------------------


def _row(
    resume_id: str,
    posting_id: str,
    expected: str,
    *,
    verdict: str | None = None,
    questions: tuple[tuple[str, str], ...] = (),
    expected_ids: tuple[str, ...] = (),
    clean_fit: bool = False,
    uncertain: bool = False,
    excluded: bool = False,
    ok: bool = True,
    attempts: int = 1,
    reason: str | None = None,
    elapsed: float = 30.0,
    unmet: int = 0,
    resume_text: str = "AWS Kubernetes Python",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "resume_id": resume_id,
        "posting_id": posting_id,
        "expected_verdict": expected,
        "expected_question_ids": list(expected_ids),
        "clean_fit": clean_fit,
        "uncertain": uncertain,
        "excluded": excluded,
        "ok": ok,
        "attempts": attempts,
        "retried": attempts >= 2,
        "validation_error": None if ok else "assessment_result.matrix is out of bounds",
        "not_assessed_reason": reason,
        "elapsed_seconds": elapsed,
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150} if ok else None,
        "verdict": verdict if ok else None,
        "questions": [{"question_id": question_id, "question": text, "requirement": None} for question_id, text in questions] if ok else [],
        "matrix": [{"requirement": f"req{i}", "class": "hard", "status": "unmet", "resume_evidence": []} for i in range(unmet)] if ok else [],
        "not_a_match_reason": "stated gap" if verdict == "not_a_match" else None,
    }
    return harness.annotate_row(row, resume_text)


def test_summarize_computes_every_planned_metric() -> None:
    rows = [
        _row("cf-a", "p1", "matched_above_threshold", verdict="matched_above_threshold", clean_fit=True),
        _row("cf-b", "p2", "matched_above_threshold", verdict="pending_user_answers", questions=(("location:poland", "Where do you live?"),), clean_fit=True),
        _row("r1", "p1", "not_a_match", verdict="not_a_match", unmet=1),
        _row("r2", "p2", "pending_user_answers", verdict="pending_user_answers", questions=(("cloud:aws", "AWS?"), ("database:mysql_flavour", "MySQL?")), expected_ids=("cloud:aws", "database:mysql", "pattern:bff")),
        _row("r3", "p3", "not_a_match", verdict="pending_user_answers", questions=(("cloud:aws", "AWS?"),), uncertain=True, attempts=2),
        _row("r4", "p3", "pending_user_answers", ok=False, attempts=2, reason="model_output_invalid", expected_ids=("years:ml",)),
        _row("r5", "p4", "not_a_match", ok=False, attempts=0, reason="model_unavailable"),
    ]
    metrics = harness.summarize(rows, planned=9, max_calls=7)

    assert metrics["calls"] == {"planned": 9, "made": 7, "max_calls": 7, "stopped_at_cap": True}

    agreement = metrics["verdict_agreement"]
    assert (agreement["rows"], agreement["agree"], agreement["rate"]) == (5, 3, 0.6)
    assert (agreement["confident_rows"], agreement["confident_agree"], agreement["confident_rate"]) == (4, 3, 0.75)
    assert agreement["confusion"]["not_a_match"] == {"not_a_match": 1, "pending_user_answers": 1}

    clean = metrics["clean_fit"]
    assert (clean["rows"], clean["matched"], clean["rate"]) == (2, 1, 0.5)
    assert [failure["resume_id"] for failure in clean["failures"]] == ["cf-b"]
    assert clean["failures"][0]["questions"][0]["question_id"] == "location:poland"

    recall = metrics["question_recall"]
    assert recall["rows"] == 1  # r4's invalid row cannot be scored
    assert (recall["expected_ids"], recall["hit_exact"], recall["hit_category"]) == (3, 1, 2)
    assert (recall["recall_exact"], recall["recall_category"]) == (0.3333, 0.6667)

    false_asks = metrics["false_asks"]
    assert false_asks["clean_fit_questions"] == 1 and false_asks["bar_zero_met"] is False
    assert [(item["resume_id"], item["question_id"]) for item in false_asks["possible"]] == [("r2", "cloud:aws"), ("r3", "cloud:aws")]

    cross = metrics["cross_profile"]
    assert (cross["pairs"], cross["discriminated"], cross["rate"]) == (2, 1, 0.5)
    assert cross["postings"]["p1"]["discriminated"] == 1 and cross["postings"]["p2"]["discriminated"] == 0

    reliability = metrics["reliability"]
    assert (reliability["valid"], reliability["invalid"], reliability["valid_output_rate"]) == (5, 2, 0.7143)
    assert (reliability["invalid_after_retry"], reliability["invalid_after_retry_rate"]) == (1, 0.1429)
    assert reliability["invalid_after_retry_bar"] == 0.05 and reliability["invalid_after_retry_bar_met"] is False
    assert (reliability["retries"], reliability["recovered_on_retry"], reliability["transport_failures"]) == (2, 1, 1)
    assert reliability["latency_seconds"]["max"] == 30.0 and reliability["tokens"] == {"input": 500, "output": 250, "total": 750}
    assert reliability["model_cost_usd"] == "unavailable" and reliability["jev_cost_usd"] is None
    assert metrics["jev"] is None


def test_summarize_skips_excluded_rows_in_every_metric() -> None:
    rows = [
        _row("cf-a", "p1", "matched_above_threshold", verdict="matched_above_threshold", clean_fit=True),
        _row("r1", "p1", "not_a_match", verdict="pending_user_answers", questions=(("cloud:aws", "AWS?"),), expected_ids=("years:ml",)),
        _row("r2", "p2", "pending_user_answers", ok=False, attempts=2, reason="model_output_invalid", expected_ids=("cloud:aws",)),
    ]
    # One excluded row of every kind that could move a metric: a failing clean fit, a
    # disagreeing confident row with a false ask and a cross-profile twin, and an invalid row.
    noise = [
        _row("cf-x", "p1", "matched_above_threshold", verdict="not_a_match", clean_fit=True, excluded=True, unmet=2),
        _row("r9", "p2", "not_a_match", verdict="pending_user_answers", questions=(("cloud:aws", "AWS?"),), expected_ids=("cloud:aws",), excluded=True, elapsed=900.0),
        _row("r8", "p1", "pending_user_answers", ok=False, attempts=2, reason="model_output_invalid", expected_ids=("cloud:gcp",), excluded=True),
    ]
    assert harness.summarize(rows + noise, planned=3, max_calls=20) == harness.summarize(rows, planned=3, max_calls=20)
    metrics = harness.summarize(rows + noise, planned=3, max_calls=20)
    assert metrics["calls"]["made"] == 3
    assert metrics["clean_fit"] == {"rows": 1, "valid": 1, "matched": 1, "rate": 1.0, "failures": []}
    assert metrics["cross_profile"]["pairs"] == 1
    assert metrics["reliability"]["latency_seconds"]["max"] == 30.0


def test_summarize_on_no_rows_reports_nulls_not_errors() -> None:
    metrics = harness.summarize([], planned=0, max_calls=20)
    assert metrics["verdict_agreement"]["rate"] is None
    assert metrics["reliability"]["invalid_after_retry_bar_met"] is False
    assert metrics["reliability"]["latency_seconds"]["mean"] is None
