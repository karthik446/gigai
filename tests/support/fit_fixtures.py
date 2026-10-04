"""0.1.10.9 ticket 0110-10-02: what the weak-fit / fit-sort / assess-threshold tests share. Synthetic only.

On top of ``posting_fixtures`` (a gig, two profiles, Lever boards in the
fixture home's board cache): rank scores written the way the background rank
writes them, model answers with a chosen requirement matrix, and the two
scenes the tests use (four needs-answers postings of which one is a weak fit;
five new postings around the assess threshold).
"""

from __future__ import annotations

import json

import pytest

from gigai.scout import posting_search, postings
from gigai.scout.find_jobs.model_rank import _hex, cache_dir, cache_key, prefs_digest
from gigai.scout.find_jobs.rank_digest import resume_digest
from gigai.scout.find_jobs.rank_run import rank_prefs
from gigai.scout.pipeline.store import PipelineStore, pipeline_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import RESUME, assessment
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, job_url, lever_job

TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")


def seed_rank(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, scores: dict[str, int], profile_id: str | None = None) -> None:
    """What the background rank does: a score in the home's rank cache, written as the ranker writes it.

    The cache is keyed by a posting's content: two postings with the same title and text share one score.
    """

    owner = profile_id or fx.default_profile_id
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    done = postings.refresh(fx.home_root, fx.target, now=NOW)
    view = next(item for item in done.profiles if item.profile_id == owner)
    prefs = rank_prefs(view.config)  # type: ignore[arg-type]
    model = postings.rank_model_key(fx.home_root, fx.target)
    assert model is not None
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        digests = {row.job: row.listing_digest for row in store.postings(profile_id=owner, live=False)}
    finally:
        store.close()
    cache_dir(fx.home_root).mkdir(parents=True, exist_ok=True)
    for job, score in scores.items():
        key = cache_key(
            content_sha256=digests[job], resume_digest_sha256=_hex({"resume_digest": resume_digest(RESUME, prefs)}),
            prefs_sha256=prefs_digest(prefs), model=model,
        )
        (cache_dir(fx.home_root) / f"{key}.json").write_text(json.dumps({"score": score, "reasons": [], "blockers": []}), encoding="utf-8")


def assess_one(fx: PostingsFixture, job: str, answer: str) -> None:
    """One posting assessed from its stored text for the default profile, the scripted model answering ``answer``.

    Called before the posting's rank score is seeded (or for a rank of 50 or more): a plain approval assesses it.
    """

    fx.base.model.assessed = answer
    done = posting_search.assess_these(fx.home_root, fx.target, jobs=[job], profile_id=fx.default_profile_id, approve=True, now=NOW)
    assert done["assessed"] == {"requested": 1, "assessed": 1, "failed": [], "stopped": None, "fetched_on_demand": 0}, done
    fx.base.model.assessed = assessment(met=2)


def matrix_answer(rows: list[tuple[str, str, str]], questions: int = 0) -> str:
    """A model answer: ``rows`` are ``(requirement, class, status)``; the first ``questions`` askable rows that are not met each ask one."""

    matrix = [
        {"requirement": name, "class": kind, "status": status, "resume_evidence": ["six years"] if status == "met" else []}
        for name, kind, status in rows
    ]
    open_rows = [name for name, kind, status in rows if kind == "askable" and status != "met"][:questions]
    asked = [
        {
            "question_id": TERRAFORM[0] if index == 0 else f"tooling:topic-{index}",
            "question": TERRAFORM[1] if index == 0 else f"Have you worked with topic {index}?", "requirement": name,
        }
        for index, name in enumerate(open_rows)
    ]
    verdict = "pending_user_answers" if asked else "matched_above_threshold"
    return json.dumps({"verdict": verdict, "matrix": matrix, "suggestions": [], "questions": asked, "not_a_match_reason": None})


def one_of_ten() -> str:
    """The operator's case: 1 of 10 requirements met (only Python), three questions. Fit: 2 of 14 weighted = 14%."""

    rows = [("5+ years of Python", "hard", "met")]
    rows += [(f"Askable topic {n}", "askable", "unmet") for n in range(1, 4)]
    rows += [(f"Nice topic {n}", "nice_to_have", "unmet") for n in range(1, 7)]
    return matrix_answer(rows, questions=3)


def five_of_ten() -> str:
    """Half met, one question: fit 10 of 20 weighted = 50%."""

    rows = [(f"Hard topic {n}", "hard", "met") for n in range(1, 6)]
    rows += [(f"Askable topic {n}", "askable", "unmet") for n in range(1, 6)]
    return matrix_answer(rows, questions=1)


def weak_fixture(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Four needs-answers postings: the weak fit (rank 39), and one each that the share, the rank or a missing rank keeps."""

    fx.seed("aero", [lever_job("aero", n) for n in range(1, 5)], seen_at=days_ago(1))
    jobs = {name: job_url("aero", n) for n, name in enumerate(("weak", "high_rank", "half_met", "unranked"), start=1)}
    assess_one(fx, jobs["weak"], one_of_ten())
    assess_one(fx, jobs["high_rank"], one_of_ten())
    assess_one(fx, jobs["half_met"], five_of_ten())
    assess_one(fx, jobs["unranked"], one_of_ten())
    seed_rank(fx, monkeypatch, {jobs["weak"]: 39, jobs["high_rank"]: 80, jobs["half_met"]: 39})
    return jobs


def ranked_new(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Five new postings: rank 80, 50 (the threshold itself), 49, 20, and one not ranked yet.

    Numbered from 11: their text differs from ``weak_fixture``'s postings, so the two scenes share no rank score.
    """

    fx.seed("thr", [lever_job("thr", n) for n in range(11, 16)], seen_at=days_ago(1))
    jobs = {name: job_url("thr", n) for n, name in enumerate(("r80", "r50", "r49", "r20", "unranked"), start=11)}
    seed_rank(fx, monkeypatch, {jobs["r80"]: 80, jobs["r50"]: 50, jobs["r49"]: 49, jobs["r20"]: 20})
    return jobs
