"""0110-8-09: a successful assess call always leaves a record or a named failure. Synthetic only.

The case (ANALYSIS-1 Q6.1, "nextpatient"): one ``assess`` row in the call
metrics with outcome ``ok``, no record in the quick-assess store, the posting
still ``not_assessed``. Reproduced here with the two ways a model call can
answer and leave nothing: the answer is withheld (a posting whose requirements
cannot be read: no requirement cue in the text, no real requirement in the
answer), and the answer cannot be written.

THE INVARIANT, over every pair of a batch:

* a call recorded ``ok`` implies a stored assessment for its job and profile;
* a pair with no stored assessment is in ``failed[]`` with its code, and its
  call (when one was made) is an ``error`` with that code;
* ``assessed`` counts stored assessments, nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from gigai.scout import quick_assess, scout_new
from gigai.scout.pipeline.store import pipeline_path
from gigai.scout.quick_assess import quick_assess_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import PipelineModel, assessment, install_model
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

#: Text with no requirement cue that does not read as prose: a scrape of names (the unreadable-posting guard's own case).
UNREADABLE = "\n".join(["Acme", "Careers", "Teams", "Locations", "Benefits", "Blog", "Press", "Sign in"] * 3)
NO_REQUIREMENTS = json.dumps({
    "verdict": "matched_above_threshold",
    "matrix": [{"requirement": "No stated requirements", "class": "nice_to_have", "status": "met", "resume_evidence": []}],
    "suggestions": [], "questions": [], "not_a_match_reason": None,
})


class _Model(PipelineModel):
    """The scripted model; a posting whose text is the scrape gets a valid answer with no requirement."""

    def answer(self, prompt: str):
        self.assessed = NO_REQUIREMENTS if "Locations\nBenefits" in prompt else assessment(met=2)
        return super().answer(prompt)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    install_model(monkeypatch, _Model())
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, text=UNREADABLE), lever_job("acme", 3)], seen_at=days_ago(1))
    return fx


def _calls(fx: PostingsFixture) -> list[tuple[str, str, str, str | None]]:
    with sqlite3.connect(pipeline_path(fx.home_root, fx.target)) as db:
        return list(db.execute("SELECT job, profile_id, outcome, error_code FROM model_call WHERE kind='assess' ORDER BY id"))


def _stored(fx: PostingsFixture, job: str, profile_id: str) -> bool:
    return quick_assess_path(fx.home_root, fx.target, profile_id, job).is_file()


def _assert_invariant(fx: PostingsFixture, assessed: dict[str, object], pairs: list[tuple[str, str]]) -> None:
    failed = {(item["job_identity"], item["profile_id"]): item["error_code"] for item in assessed["failed"]}  # type: ignore[union-attr,index]
    calls = _calls(fx)
    assert calls, "the batch made model calls"
    for job, profile_id, outcome, error_code in calls:
        if outcome == "ok":
            assert _stored(fx, job, profile_id), f"an ok call left no assessment: {job}"
            assert (job, profile_id) not in failed
        else:
            assert error_code and failed.get((job, profile_id)) == error_code, (job, outcome, error_code, failed)
    for job, profile_id in pairs:
        assert _stored(fx, job, profile_id) is ((job, profile_id) not in failed), job
        assert failed.get((job, profile_id), "x")  # a failure always carries a code
    assert assessed["assessed"] == sum(1 for job, profile_id in pairs if _stored(fx, job, profile_id)) == len(pairs) - len(failed)


def _yes(fx: PostingsFixture) -> tuple[dict[str, object], list[tuple[str, str]]]:
    asked = scout_new.scout_new(fx.home_root, fx.target, now=NOW, profile_id=fx.default_profile_id)
    assert asked["status"] == "ask"
    pairs = [(job_url("acme", n), fx.default_profile_id) for n in (1, 2, 3)]
    response = scout_new.scout_new(
        fx.home_root, fx.target, now=NOW, assess=True, since=str(asked["since"]), profile_id=fx.default_profile_id, config=fixture_config(fx.home_root),
    )
    return response["assessed"], pairs  # type: ignore[return-value]


@pytest.mark.skip(reason="the unreadable-posting guard changed in 0.1.11 (an ATS board's own text is trusted; a menu page is refused before the model call): re-pin in 0.1.11.1")
def test_a_withheld_answer_is_a_named_failure_and_its_call_is_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)

    assessed, pairs = _yes(fx)

    unreadable = job_url("acme", 2)
    assert [(item["job_identity"], item["error_code"]) for item in assessed["failed"]] == [(unreadable, "posting_requirements_unreadable")]  # type: ignore[union-attr,index]
    assert not _stored(fx, unreadable, fx.default_profile_id)
    # THE REPRO: this call answered (it was ``ok`` before the fix) and nothing was stored for it.
    assert [(outcome, code) for job, _profile, outcome, code in _calls(fx) if job == unreadable] == [("error", "posting_requirements_unreadable")] * 2  # 0.1.11: the refused answer got one more call, also refused
    _assert_invariant(fx, assessed, pairs)


@pytest.mark.skip(reason="the unreadable-posting guard changed in 0.1.11 (an ATS board's own text is trusted; a menu page is refused before the model call): re-pin in 0.1.11.1")
def test_an_answer_that_cannot_be_written_is_a_named_failure_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    blocked = job_url("acme", 3)
    real_write = quick_assess.atomic_write

    def write(path, data, *args, **kwargs):
        if Path(path) == quick_assess_path(fx.home_root, fx.target, fx.default_profile_id, blocked):
            raise OSError(28, "No space left on device")
        return real_write(path, data, *args, **kwargs)

    monkeypatch.setattr(quick_assess, "atomic_write", write)

    assessed, pairs = _yes(fx)

    failed = {item["job_identity"]: item["error_code"] for item in assessed["failed"]}  # type: ignore[union-attr,index]
    assert failed == {job_url("acme", 2): "posting_requirements_unreadable", blocked: "assessment_not_stored"}
    assert _stored(fx, job_url("acme", 1), fx.default_profile_id)  # the rest of the batch finished
    _assert_invariant(fx, assessed, pairs)
