"""0.1.11.12 dossier-C (gigai-6po): the red flags reach the job page's read and the Jobs grid's row, derived at read time.

On a synthetic home, through the product's own assessment path (``run_quick_assessment`` with the scripted fixture model)
and its own reads: ``GET /api/jobs/{id}``'s aggregate (``assessments[*].red_flags``), the Jobs grid's row (``red_flags``,
the row already holds the stored assessment: no new read) and the grid's text line. The stored record is not changed by
being read, and nothing about the fit, the cap or the verdict moves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import posting_search, postings
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.fit import fit_cap, shown_fit
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.red_flags import red_flags_json, stored_declared

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_SLUG = "toastline"
_URL = job_url(_SLUG, 1)
_POSTING = (
    "Toastline is hiring a Staff Software Engineer, Tech Lead (Scheduling). What you bring: 8+ years of back-end experience "
    "in Java, Kotlin, or other object-oriented languages; Postgres in production. Remote within the United States."
)
_LANGUAGE_ROW = {
    "requirement": "8+ years of back-end experience in Java, Kotlin, or other object-oriented languages", "class": "hard", "status": "unclear",
    "resume_evidence": [], "alternatives": ["Java", "Kotlin", "other object-oriented languages"],
}
_PYTHON_ROW = {"requirement": "Postgres in production", "class": "askable", "status": "met", "resume_evidence": ["six years on Postgres"], "alternatives": ["Postgres"]}
_ASK = {"question_id": "lang:java", "question": "Have you shipped back-end services in Java or Kotlin?", "requirement": _LANGUAGE_ROW["requirement"]}
_FLAG = {"kind": "language", "text": "Asks for Java, Kotlin"}


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _answer(rows: list[dict[str, object]], questions: list[dict[str, object]]) -> str:
    verdict = "pending_user_answers" if questions else "matched_above_threshold"  # rule 7: a question holds the pending verdict
    return json.dumps({"verdict": verdict, "matrix": rows, "questions": questions, "not_a_match_reason": None})


def _assess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> tuple[PostingsFixture, object, dict]:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fx.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    fx.base.model.assessed = answer
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    postings.refresh(fx.home_root, fx.target, now=NOW)
    job = _Routes()._job_aggregate(normalize_url(_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None
    return fx, stored, job


def test_the_job_read_and_the_grid_row_carry_the_language_flag_and_the_stored_record_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, stored, job = _assess(tmp_path, monkeypatch, _answer([_LANGUAGE_ROW, _PYTHON_ROW], [_ASK]))
    stored_bytes = Path(stored.stored_path).read_bytes()

    assessment = job["assessments"][0]
    assert assessment["red_flags"] == [_FLAG]
    assert "red_flags" not in json.loads(stored_bytes)["result"], "a read-time derivation is not stored"
    # The grid's row: the same flags, from the stored assessment the row already reads (no new column, no new read).
    grid = job["index_posting"]
    assert grid["red_flags"] == [_FLAG]
    listed = posting_search.search_postings(fx.home_root, fx.target, now=NOW)
    row = next(item for item in listed["postings"]["rows"] if item["job_identity"] == grid["job_identity"])
    assert row["red_flags"] == [_FLAG]
    assert "Asks for Java, Kotlin" in posting_search.render(listed)
    # Nothing about the score moved: the verdict, the fit and the row's words are the stored assessment's own.
    assert assessment["verdict"] == stored.result.verdict.value == "pending_user_answers"
    assert row["fit"] == grid["fit"] and row["score_text"] == grid["score_text"]
    assert Path(stored.stored_path).read_bytes() == stored_bytes
    # F6: the cap and the shown fit are the same before and after the flags are derived from the same rows.
    matrix = stored.result.matrix
    cap_before, fit_before = fit_cap(matrix), shown_fit(matrix)
    assert red_flags_json(matrix, stored_declared(fx.home_root, fx.target)) == [_FLAG]
    assert (fit_cap(matrix), shown_fit(matrix)) == (cap_before, fit_before)
    assert row["fit"] == fit_before


def test_an_assessment_with_no_flag_serves_an_empty_list_on_every_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    met = {**_LANGUAGE_ROW, "status": "met", "resume_evidence": ["eight years of Java"]}
    fx, _stored, job = _assess(tmp_path, monkeypatch, _answer([met, _PYTHON_ROW], []))

    assert job["assessments"][0]["red_flags"] == []
    assert job["index_posting"]["red_flags"] == []
    listed = posting_search.search_postings(fx.home_root, fx.target, now=NOW)
    assert [row["red_flags"] for row in listed["postings"]["rows"]] == [[]]
    assert "Asks for" not in posting_search.render(listed)
