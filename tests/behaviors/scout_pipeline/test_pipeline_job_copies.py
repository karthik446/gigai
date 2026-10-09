"""0.1.11.9 INT1: the pipeline is keyed by the CANONICAL job, whichever copy or role asks.

PJ6 keeps one pipeline set a job, whichever ROLE asks (``test_pipeline_one_job_one_set.py``). RB2 gives the three
job stores one identity a job posted once per country (``job_key.job_key``, ``test_job_copies_one_record.py``).
Until this packet the two did not meet: ``pipeline.job_identity.job_identity_for`` answered the identity it was
given, so the pipeline treated each country's copy of a job as its own job.

Pinned, on the END outcome (what is on disk, what the route answers):

- a job posted in several countries, asked for by a NON-canonical copy's address and then by the canonical one,
  from two roles: ONE set of pipeline steps, one tailoring, one re-assessment, one ATS score, one label in total,
  one record a store, under the job's canonical key;
- ``GET /api/pipeline/job`` answers the same for either copy's address.

Synthetic only: the copies fixture (``tests/support/copies_fixtures``) on a ``PostingsFixture`` home, the scripted
model of ``pipeline_fixtures``. Fail-before: with ``job_identity_for`` answering its input (as it did before this
packet), the second ask opens a SECOND set under the other copy's own digest; shown below as a monkeypatch, not by
reverting the source.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.job_store_layout import JOB_FOLDER, job_digest
from gigai.scout.pipeline import job_identity, triggers
from gigai.scout.pipeline.runner import DRAIN_RAN, PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore

from tests.behaviors.scout_find_jobs.test_pick_header_room import _Server
from tests.support.answers_stories_fixtures import config, two_profiles
from tests.support.copies_fixtures import SLUG, board, copy_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url

NEWEST = NOW - timedelta(hours=3)
US, ESTONIA, LATVIA = job_url(SLUG, 14), job_url(SLUG, 1), job_url(SLUG, 3)
#: The eight postings of the one job: the US one (canonical), then the seven countries.
COPIES = [US, *(job_url(SLUG, n) for n in range(1, 8))]
_DONE = {name: "done" for name in STEPS}
_STORES = ("quick_assess_tailored", "ats", "label", "pipeline/tailor")


def _us_copy() -> dict[str, object]:
    return copy_job(SLUG, 14, "Staff Engineer", "Denver, CO", "US", NEWEST + timedelta(minutes=20))


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed(SLUG, [*board(newest=NEWEST), _us_copy()], seen_at=days_ago(1))
    return fixture


def _ask(fx: PostingsFixture, url: str, profile_id: str) -> None:
    """A stored (base) assessment of ``url``'s job for ``profile_id``, through the posting it is found under."""

    done = posting_search.assess_these(fx.home_root, fx.target, jobs=[url], profile_id=profile_id, approve=True, include_low_rank=True, now=NOW)
    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 1, done


def _drain(fx: PostingsFixture) -> str:
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain().state


def _rows(fx: PostingsFixture, job: str) -> dict[str, dict[str, str]]:
    store = PipelineStore(fx.base.db)
    try:
        found: dict[str, dict[str, str]] = {}
        for step in store.steps(job=job):
            found.setdefault(step.profile_id, {})[step.name] = step.state
        return found
    finally:
        store.close()


def _runs(fx: PostingsFixture, job: str) -> dict[str, int]:
    store = PipelineStore(fx.base.db)
    try:
        counted: dict[str, int] = {}
        for run in store.runs(job=job):
            if run.outcome == "ok":
                counted[run.name] = counted.get(run.name, 0) + 1
        return counted
    finally:
        store.close()


def _files(fx: PostingsFixture, job: str) -> dict[str, list[str]]:
    name = f"{job_digest(job)}.json"
    return {
        store: sorted(path.parent.name for path in (fx.base.scout_root / store).glob(f"*/{name}")) if (fx.base.scout_root / store).is_dir() else []
        for store in _STORES
    }


def test_two_copies_and_two_roles_make_one_pipeline_set_and_one_record_a_store(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    role_a, role_b = two_profiles(fx.base.gig)
    # Role A's stored assessment is made by the NON-canonical (Latvia) copy's address; it resolves to the canonical (US) job.
    _ask(fx, LATVIA, role_a)
    calls = fx.base.model.calls

    # Role A asks the pipeline from the Latvia copy's address.
    first = triggers.process_now(fx.home_root, fx.target, role_a, LATVIA)
    assert (first["result"], first["profile_id"], first["job"]) == ("enqueued", role_a, US)
    assert _drain(fx) == DRAIN_RAN

    # Role B asks the SAME job from the canonical (US) address: a no-op, named under role A's set.
    second = triggers.process_now(fx.home_root, fx.target, role_b, US)
    assert (second["result"], second["profile_id"], second["job"]) == ("noop_unchanged", role_a, US)

    # The pipeline route, named by the Estonia copy's own address: the same job, the same answer.
    api = _Server(fx, monkeypatch)
    try:
        origin = {"Origin": str(api.client.base_url).rstrip("/")}
        by_latvia = api.client.get("/api/pipeline/job", params={"job_identity": LATVIA})
        by_estonia = api.client.get("/api/pipeline/job", params={"job_identity": ESTONIA})
        by_us = api.client.get("/api/pipeline/job", params={"job_identity": US})
        assert by_latvia.status_code == by_estonia.status_code == by_us.status_code == 200
        assert by_latvia.json() == by_estonia.json() == by_us.json()
        assert by_us.json()["profile_id"] == role_a and [step["state"] for step in by_us.json()["steps"]] == ["done"] * len(STEPS)
        processed = api.client.post("/api/pipeline/process", json={"job_identity": ESTONIA, "profile_id": role_b}, headers=origin)
        assert processed.status_code == 202, processed.text
        assert processed.json()["profile_id"] == role_a and processed.json()["result"] != "enqueued"
    finally:
        api.close()

    # Exactly one tailoring, one re-assessment call (plus the one base assessment already counted in `calls` below).
    assert len(fx.base.model.tailor_prompts) == 1
    assert _runs(fx, US) == {name: 1 for name in STEPS}
    assert _rows(fx, US) == {role_a: _DONE}
    # One record a store, under the canonical job's own key.
    assert _files(fx, US) == {store: [JOB_FOLDER] for store in _STORES}
    for store in ("ats", "label"):
        assert (fx.base.scout_root / store / JOB_FOLDER / f"{job_digest(US)}.json").is_file()
    assert fx.base.model.calls == calls + 2  # the base assessment, then one re-assessment


def test_fail_before_job_identity_for_answering_its_input_opens_a_second_set(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-before: with the wrapper NOT wired to the canonical rule (pre-INT1 behaviour), a second copy opens its
    OWN set of pipeline step rows, under the OTHER copy's raw digest (the store choke point already resolves the
    one stored assessment and the one tailored resume to the canonical key regardless of this wiring, which is why
    role A's own, unresolved set then fails downstream of tailor: its ``ats``/``reassess`` steps look for the
    tailored output under LATVIA's digest, not under the US one it was actually written to)."""

    monkeypatch.setattr(triggers, "job_identity_for", lambda value, *, home_root=None, target=None: value)
    role_a, role_b = two_profiles(fx.base.gig)
    _ask(fx, LATVIA, role_a)

    first = triggers.process_now(fx.home_root, fx.target, role_a, LATVIA)
    assert (first["result"], first["job"]) == ("enqueued", LATVIA)
    assert _drain(fx) == DRAIN_RAN

    second = triggers.process_now(fx.home_root, fx.target, role_b, US)
    assert (second["result"], second["job"]) == ("enqueued", US)  # a SECOND set, not a no-op: the bug this packet fixes
    assert _drain(fx) == DRAIN_RAN

    # TWO sets of step rows for what is really one job, one under each copy's own raw address.
    assert set(_rows(fx, LATVIA)) == {role_a} and set(_rows(fx, US)) == {role_b}
    assert _rows(fx, US)[role_b] == _DONE  # role B's set, queued under the canonical address directly, completes
    assert _rows(fx, LATVIA)[role_a]["ats"] == "failed" and _rows(fx, LATVIA)[role_a]["reassess"] == "failed"


def test_pass_after_the_same_scenario_through_job_identity_for_itself(fx: PostingsFixture) -> None:
    """Pass-after: the same two-copy, two-role scenario as the fail-before test, run through the real wrapper."""

    assert job_identity.job_identity_for(LATVIA, home_root=fx.home_root, target=fx.target) == US
    assert job_identity.job_identity_for(ESTONIA, home_root=fx.home_root, target=fx.target) == US
    assert job_identity.job_identity_for(LATVIA) == LATVIA  # home_root/target missing: unchanged

    role_a, role_b = two_profiles(fx.base.gig)
    _ask(fx, LATVIA, role_a)

    first = triggers.process_now(fx.home_root, fx.target, role_a, LATVIA)
    assert (first["result"], first["job"]) == ("enqueued", US)
    assert _drain(fx) == DRAIN_RAN

    second = triggers.process_now(fx.home_root, fx.target, role_b, US)
    assert (second["result"], second["profile_id"], second["job"]) == ("noop_unchanged", role_a, US)
    assert set(_rows(fx, US)) == {role_a}
    assert len(fx.base.model.tailor_prompts) == 1  # one tailoring for the job, not one a copy
