"""0.1.11.9 PJ6: the pipeline is keyed by the JOB, not by (role, job).

Until 0.1.11.9 a job two roles tag had two pipelines: asking for it from the
second role queued a second set of steps, re-assessed the tailored resume
again, scored it again and labelled it again, and each role kept its own
``quick_assess_tailored/``, ``ats/`` and ``label/`` record. A job has ONE
assessment and ONE resume, so it has one pipeline:

* asked for from role A and then from role B (the trigger, the CLI, the
  route): one tailoring, one re-assessment, one ATS score and one label in
  total, one set of step rows, one record a store, in the store's ``job``
  folder. The role on them is the one that asked first and selects nothing;
* ``GET /api/pipeline/job`` answers with no ``profile_id``;
* a home written before 0.1.11.9, with step rows and records under BOTH
  roles for one job, still reads (its newest set), and a new process writes
  one set: the job's;
* a saved answer's weak-fit check judges a job at its BEST rank among the
  roles that tag it.

Synthetic only: the scripted model of ``tests/support/pipeline_fixtures``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3

from click.testing import CliRunner
import pytest

from gigai.scout import fit, postings
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.job_store_layout import JOB_FOLDER, job_digest
from gigai.scout.pipeline import steps, triggers
from gigai.scout.pipeline.overview import job_detail, overview
from gigai.scout.pipeline.runner import DRAIN_IDLE, DRAIN_RAN, PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group

from tests.behaviors.scout_find_jobs.test_pick_header_room import _Server
from tests.support.answers_stories_fixtures import config, pending, two_profiles
from tests.support.fit_fixtures import assess_one, one_of_ten, seed_rank
from tests.support.pipeline_fixtures import TAILORED, PipelineFixture, assessment, build_pipeline_fixture, resolved_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")
_DONE = {name: "done" for name in STEPS}
#: The stores whose record is the job's: the tailored-variant assessment, the ATS record, the label, the tailor step's own.
_STORES = ("quick_assess_tailored", "ats", "label", "pipeline/tailor")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    return build_pipeline_fixture(tmp_path, monkeypatch, base=False)


def _job(n: int) -> str:
    return f"https://jobs.example.test/acme/role-{n:02d}"


def _ask(fx: PipelineFixture, url: str, profile_id: str) -> None:
    """The job's stored (base) assessment, asked for by ``profile_id``, that leaves the Terraform question open."""

    fx.model.assessed = pending(*_TERRAFORM)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()
    fx.model.tailor_prompts.clear()


def _drain(fx: PipelineFixture) -> str:
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain().state


def _rows(fx: PipelineFixture, job: str) -> dict[str, dict[str, str]]:
    """role -> ``{step: state}`` of every step row of ``job``."""

    store = PipelineStore(fx.db)
    try:
        found: dict[str, dict[str, str]] = {}
        for step in store.steps(job=job):
            found.setdefault(step.profile_id, {})[step.name] = step.state
        return found
    finally:
        store.close()


def _runs(fx: PipelineFixture, job: str) -> dict[str, int]:
    """step -> how many times it RAN to an output for ``job``, whatever the role (the scorer's and the label's counter)."""

    store = PipelineStore(fx.db)
    try:
        counted: dict[str, int] = {}
        for run in store.runs(job=job):
            if run.outcome == "ok":
                counted[run.name] = counted.get(run.name, 0) + 1
        return counted
    finally:
        store.close()


def _files(fx: PipelineFixture, job: str) -> dict[str, list[str]]:
    """store -> the folders of it that hold a record of ``job``."""

    name = f"{job_digest(job)}.json"
    return {
        store: sorted(path.parent.name for path in (fx.scout_root / store).glob(f"*/{name}")) if (fx.scout_root / store).is_dir() else []
        for store in _STORES
    }


def _cli(fx: PipelineFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, fx.cli(*args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- one job, two roles: one pipeline ----------------------------------------------------------------------------------------


def test_a_job_two_roles_ask_for_runs_once_and_keeps_one_set_of_steps_and_records(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    role_a, role_b = two_profiles(fx.gig)
    job = _job(1)
    _ask(fx, job, role_a)

    # Role A asks.
    first = triggers.process_now(fx.home_root, fx.target, role_a, job)
    assert (first["result"], first["profile_id"]) == ("enqueued", role_a)
    assert _drain(fx) == DRAIN_RAN
    # Role B asks for the same job: the trigger, then the command line, then an answer both roles' pairs name.
    second = triggers.process_now(fx.home_root, fx.target, role_b, job)
    assert (second["result"], second["profile_id"], second["input_digest"]) == ("noop_unchanged", role_a, first["input_digest"])
    told = _cli(fx, "process", job, "--profile", role_b)
    assert (told["enqueue"]["result"], told["enqueue"]["profile_id"]) == ("noop_unchanged", role_a)
    assert {step["profile_id"] for step in told["status"]["steps"]} == {role_a} and len(told["status"]["steps"]) == len(STEPS)
    pairs = triggers.enqueue_pairs(fx.home_root, fx.target, [(role_b, job), (role_a, job)], trigger=triggers.TRIGGER_ANSWER)
    assert (pairs.state, pairs.unchanged, pairs.enqueued, pairs.awaiting) == (triggers.NOTHING, 1, (), ())
    assert _drain(fx) == DRAIN_IDLE

    # Exactly one run of each step in total: one tailoring and one re-assessment call, one ATS score, one label.
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)
    assert _runs(fx, job) == {name: 1 for name in STEPS}
    # One set of step rows, under the role that asked first.
    assert _rows(fx, job) == {role_a: _DONE}
    # One record a store, the job's own; no role's folder is written.
    assert _files(fx, job) == {store: [JOB_FOLDER] for store in _STORES}
    for store in ("ats", "label"):
        record = json.loads((fx.scout_root / store / JOB_FOLDER / f"{job_digest(job)}.json").read_text(encoding="utf-8"))
        assert record["profile_id"] == role_a and record["job_identity"] == job  # the role is who asked, recorded only
    # Whichever role is named, and with none, the reads are the job's.
    label = steps.read_label(fx.home_root, fx.target, role_a, job)
    assert label is not None and label == steps.read_label(fx.home_root, fx.target, role_b, job) == steps.read_label(fx.home_root, fx.target, None, job)
    assert steps.read_ats(fx.home_root, fx.target, role_b, job) == steps.read_ats(fx.home_root, fx.target, None, job) is not None
    variant = steps.read_variant(fx.home_root, fx.target, role_b, job)
    assert variant is not None and variant.stored_path == steps.read_variant(fx.home_root, fx.target, None, job).stored_path
    assert Path(variant.stored_path).parent.name == JOB_FOLDER
    # The overview lists the job once.
    listed = overview(fx.home_root, fx.target, busy=lambda: None)
    assert [(item["job_identity"], item["profile_id"], item["state"]) for item in listed["jobs"]] == [(job, role_a, "done")]
    assert listed["counts"]["jobs_total"] == 1 and listed["jobs"][0]["label"] is not None

    # Cancel and retry from the other role act on the job's one set (nothing to do here: every step is done).
    assert (_cli(fx, "cancel", job, "--profile", role_b)["profile_id"], _cli(fx, "retry", job, "--profile", role_b)["profile_id"]) == (role_a, role_a)
    assert _rows(fx, job) == {role_a: _DONE}


def test_two_pairs_of_one_job_in_one_trigger_are_one_job(fx: PipelineFixture) -> None:
    role_a, role_b = two_profiles(fx.gig)
    job = _job(2)
    _ask(fx, job, role_b)

    fired = triggers.enqueue_pairs(fx.home_root, fx.target, [(role_b, job), (role_a, job)], trigger=triggers.TRIGGER_ANSWER)
    assert [(item["profile_id"], item["job_identity"]) for item in fired.enqueued] == [(role_b, job)] and fired.approval is None
    assert _drain(fx) == DRAIN_RAN
    assert _rows(fx, job) == {role_b: _DONE} and _runs(fx, job) == {name: 1 for name in STEPS}
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)


def test_the_job_routes_take_no_role_and_a_named_one_selects_nothing(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    role_a, role_b = two_profiles(fx.gig)
    job = _job(3)
    _ask(fx, job, role_a)
    api = _Server(PostingsFixture(fx, second_profile_id=role_b, deleted_profile_id=None), monkeypatch)
    origin = {"Origin": str(api.client.base_url).rstrip("/")}
    try:
        def read(**params: str) -> dict:
            answer = api.client.get("/api/pipeline/job", params={"job_identity": job, **params})
            assert answer.status_code == 200, answer.text
            return answer.json()

        def process(**body: object) -> dict:
            answer = api.client.post("/api/pipeline/process", json={"job_identity": job, **body}, headers=origin)
            assert answer.status_code == 202, answer.text
            return answer.json()

        never = read()  # no role named, the job never entered the pipeline
        assert (never["steps"], never["state"], never["profile_id"], never["label"]) == ([], None, None, None)
        assert read(profile_id=role_b)["profile_id"] == role_b  # no step yet: the role that asks, echoed

        first = process(profile_id=role_b)
        assert (first["result"], first["profile_id"]) == ("enqueued", role_b)
        again = process(profile_id=role_a)
        assert again["profile_id"] == role_b and again["result"] != "enqueued", again
        unnamed = process()  # no role named: the selected role is not even read for a job that has steps
        assert unnamed["profile_id"] == role_b and unnamed["result"] != "enqueued", unnamed
        PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain()

        seen = read()
        assert seen["profile_id"] == role_b and [step["state"] for step in seen["steps"]] == ["done"] * len(STEPS)
        assert seen["label"] is not None and seen["ats"] is not None and seen["requirements_met"]["tailored"] is not None
        assert read(profile_id=role_a) == seen == read(profile_id=role_b)
        assert api.client.get("/api/pipeline/job").status_code == 422  # the job is still required
    finally:
        api.close()
    assert _rows(fx, job) == {role_b: _DONE} and _runs(fx, job) == {name: 1 for name in STEPS}
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)


# --- a home written before 0.1.11.9: rows and records under both roles ------------------------------------------------------


def _as_written_before(fx: PipelineFixture, job: str, newer: str, older: str) -> dict[str, bytes]:
    """Make the finished pipeline of ``job`` look as 0.1.11.8 left it for a job two roles processed.

    Its records are in each ROLE's folder (none in ``job``), and each role has its own four step rows: ``newer``'s
    are the ones the pipeline just finished, ``older``'s a copy stamped a day before, with a label of its own.
    Returns ``store/role -> the bytes of that record``.
    """

    name = f"{job_digest(job)}.json"
    kept: dict[str, bytes] = {}
    for store in _STORES:
        source = fx.scout_root / store / JOB_FOLDER / name
        if not source.is_file():
            source = fx.scout_root / store / newer / name  # a build that still writes the role's folder: it is there already
        original = source.read_bytes()
        for role in (newer, older):
            folder = fx.scout_root / store / role
            folder.mkdir(parents=True, exist_ok=True)
            data = original
            if store == "label" and role == older:
                record = json.loads(data)
                record.update(label="needs_attention", reasons=["ats_below_minimum"], profile_id=older)
                data = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
            (folder / name).write_bytes(data)
            if role == older:
                os.utime(folder / name, (1_700_000_000, 1_700_000_000))
            kept[f"{store}/{role}"] = data
        if source.parent.name == JOB_FOLDER:
            shutil.rmtree(source.parent)  # the one job of this home: before 0.1.11.9 the store had no such folder
    db = sqlite3.connect(fx.db)
    try:
        with db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(step)")]
            rest = ", ".join(column for column in columns if column not in ("profile_id", "updated_at"))
            db.execute(
                f"INSERT INTO step(profile_id, updated_at, {rest}) SELECT ?, '2026-01-01T00:00:00.000000Z', {rest} FROM step WHERE job=?",
                (older, job),
            )
            for store in ("quick_assess_tailored", "ats", "label"):
                for role in (newer, older):
                    db.execute(
                        "UPDATE step SET output_ref=? WHERE job=? AND profile_id=? AND output_ref=?",
                        (f"{store}/{role}/{name}", job, role, f"{store}/{JOB_FOLDER}/{name}"),
                    )
    finally:
        db.close()
    return kept


def test_a_home_with_rows_and_records_under_both_roles_still_reads_and_a_new_process_writes_one_set(fx: PipelineFixture) -> None:
    role_a, role_b = two_profiles(fx.gig)
    job = _job(4)
    _ask(fx, job, role_a)
    triggers.process_now(fx.home_root, fx.target, role_a, job)
    assert _drain(fx) == DRAIN_RAN
    before = _as_written_before(fx, job, newer=role_a, older=role_b)
    assert _rows(fx, job) == {role_a: _DONE, role_b: _DONE}
    assert _files(fx, job) == {store: sorted((role_a, role_b)) for store in _STORES}
    calls = fx.model.calls

    # It still reads: the newest set, whichever role is named and with none.
    for named in (None, role_a, role_b):
        seen = job_detail(fx.home_root, fx.target, named, job)
        assert seen["profile_id"] == role_a and [step["state"] for step in seen["steps"]] == ["done"] * len(STEPS), named
        assert seen["label"]["label"] == "recommended" and seen["ats"] is not None, named  # role A's record, not B's older one
        assert seen["requirements_met"]["tailored"] is not None, named
    assert steps.read_label(fx.home_root, fx.target, None, job)["profile_id"] == role_a  # no role named: the newest file
    listed = overview(fx.home_root, fx.target, busy=lambda: None)
    assert [(item["job_identity"], item["profile_id"]) for item in listed["jobs"]] == [(job, role_a)] and listed["counts"]["jobs_total"] == 1
    # Asked again from either role with nothing changed: nothing is queued, and a changed role re-opens no second set.
    for named in (role_b, role_a):
        asked = triggers.process_now(fx.home_root, fx.target, named, job)
        assert (asked["result"], asked["profile_id"]) == ("noop_unchanged", role_a), named
    assert triggers.profile_changed(fx.home_root, fx.target).state == triggers.NOTHING
    assert _drain(fx) == DRAIN_IDLE and fx.model.calls == calls

    # A new process (forced, from role B's side; the model now tailors and assesses otherwise, so every step has new
    # inputs and runs) writes ONE set: the job's.
    fx.model.tailored = {"sections": TAILORED["sections"][:-1]}  # type: ignore[index]
    fx.model.assessed = assessment(met=1)
    forced = triggers.process_now(fx.home_root, fx.target, role_b, job, force=True)
    assert (forced["result"], forced["profile_id"]) == ("enqueued", role_a)
    assert _drain(fx) == DRAIN_RAN
    assert fx.model.calls == calls + 2  # one tailoring (the step's own stored resume is told by the role folder's record), one re-assessment
    assert _rows(fx, job) == {role_a: _DONE, role_b: _DONE}  # no third set; the older rows are left as they were
    assert _runs(fx, job) == {name: 2 for name in STEPS}
    assert _files(fx, job) == {store: sorted((JOB_FOLDER, role_a, role_b)) for store in _STORES}
    name = f"{job_digest(job)}.json"
    for key, data in before.items():
        assert (fx.scout_root / key / name).read_bytes() == data, key  # a role's folder is never written, moved or removed
    store = PipelineStore(fx.db)
    try:
        refs = {step.name: step.output_ref for step in store.steps(profile_id=role_a, job=job)}
    finally:
        store.close()
    assert {refs[step] for step in ("reassess", "ats", "label")} == {f"{folder}/{JOB_FOLDER}/{name}" for folder in ("quick_assess_tailored", "ats", "label")}
    seen = job_detail(fx.home_root, fx.target, role_b, job)
    assert seen["profile_id"] == role_a and seen["requirements_met"]["tailored"]["met"] == 1  # the job's new records are read
    for named in (None, role_a, role_b):
        assert Path(steps.read_label(fx.home_root, fx.target, named, job)["stored_path"]).parent.name == JOB_FOLDER, named
        assert Path(steps.read_ats(fx.home_root, fx.target, named, job)["stored_path"]).parent.name == JOB_FOLDER, named
        assert Path(steps.read_variant(fx.home_root, fx.target, named, job).stored_path).parent.name == JOB_FOLDER, named


def test_a_job_whose_role_is_no_longer_active_is_queued_under_the_role_that_asks(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    role_a, role_b = two_profiles(fx.gig)
    job = _job(5)
    _ask(fx, job, role_a)
    triggers.process_now(fx.home_root, fx.target, role_a, job)
    assert _drain(fx) == DRAIN_RAN
    monkeypatch.setattr(triggers, "_active_profile_ids", lambda home_root, target: frozenset({role_b}))  # role A was archived

    asked = triggers.process_now(fx.home_root, fx.target, role_b, job)
    assert (asked["result"], asked["profile_id"]) == ("enqueued", role_b)
    assert _drain(fx) == DRAIN_RAN
    assert _rows(fx, job) == {role_a: _DONE, role_b: _DONE}
    assert _files(fx, job) == {store: [JOB_FOLDER] for store in _STORES}  # still one record a store
    store = PipelineStore(fx.db)
    try:
        assert store.job_role(job) == role_b and triggers.job_role(store, role_a, job) == role_b  # the newer set is the job's now
    finally:
        store.close()
    # The new role's resume is tailored for the job (the stored one was the pipeline's own, so it may be replaced).
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (2, 2)


# --- the weak-fit check of a saved answer: the job's best rank ---------------------------------------------------------------


def test_a_saved_answers_weak_fit_check_judges_a_job_at_its_best_rank_among_its_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    two = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    two.seed("aero", [lever_job("aero", n) for n in (1, 2)], seen_at=days_ago(1))
    kept, weak = job_url("aero", 1), job_url("aero", 2)
    assess_one(two, kept, one_of_ten())  # needs answers, fit 14%
    assess_one(two, weak, one_of_ten())
    seed_rank(two, monkeypatch, {kept: 30, weak: 30})  # the default role ranks both low
    seed_rank(two, monkeypatch, {kept: 80, weak: 20}, two.second_profile_id)  # the other role ranks ONE of them well
    postings.refresh(two.home_root, two.target, now=NOW)  # the read model's rows hold both roles' scores, as after any list read
    ranks = fit.stored_rank_scores(two.home_root, two.target)
    assert (ranks[(two.default_profile_id, kept)], ranks[(two.second_profile_id, kept)]) == (30, 80)

    for role in (two.default_profile_id, two.second_profile_id):
        pairs = [(role, kept), (role, weak)]
        # Until 0.1.11.9 PJ6 the default role's own score (30) made a weak fit of the job the other role ranks at 80.
        assert triggers._weak_fits(two.home_root, two.target, pairs) == frozenset({(role, weak)}), role
