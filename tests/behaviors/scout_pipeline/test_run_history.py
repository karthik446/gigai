"""0.1.10.7 M4a: old find-jobs runs as read-only history in the read model (``scout/run_history.py``). Synthetic only.

(d) A synthetic old run with 3 assessed postings is imported once, with the
    provenance the run sealed; a second migration imports nothing (counts 0).
    The read model shows the run's assessment where nothing newer is stored.
    A run sealed with no profile lands in ``ephemeral``: hidden by default,
    listed only when asked for, never ranked and never in the pipeline.
    Nothing of a run is written or changed.
(f) Timing: with 5,000 run-assessed rows imported (5,000 postings x 2
    profiles in the read model), a per-request read stays under the M3a
    gate's CPU ceiling and starts no subprocess per posting.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings, run_history
from gigai.scout.assessment_core import ASSESS_PROMPT_VERSION
from gigai.scout.find_jobs.contracts import AssessOutput, ProfileRef
from gigai.scout.pipeline import rank_lane
from gigai.scout.pipeline.steps import StepError, enqueue_job
from gigai.scout.pipeline.store import EPHEMERAL_PROFILE, PipelineStore, RunAssessment, pipeline_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import MARKERS, assess_base
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

#: The M3a gate's own ceilings and sizes (``test_posting_read_model.py``).
READ_CPU_CEILING = 3.0
MAX_LAUNCHES = 40
GATE_POSTINGS = 5_000
GATE_BOARDS = 10

_OLD_RUN = "run_20260901T100000Z"
_LOOSE_RUN = "run_20260815T080000Z"
_LIVE_RUN = "run_20261003T140000Z"
_GONE = "https://jobs.lever.co/gone/gone-00001"  # a posting the stored index no longer has
_CONSTRAINTS = "sha256:" + "c" * 64
_PINNED = {"record_id": "rec_0001", "revision_id": "rev_0002", "content_sha256": "sha256:" + "9" * 64}


def _digest(n: int) -> str:
    return "sha256:" + f"{n:064x}"


def _assess_output(jobs: list[tuple[str, str, int]], *, prompt_version: str = ASSESS_PROMPT_VERSION) -> AssessOutput:
    """A run's sealed assess output for ``jobs``: ``(url, verdict, requirements met of 3)`` each."""

    candidates, assessments, selected = [], [], []
    for index, (url, verdict, met) in enumerate(jobs):
        identity = {"normalized_url": url, "url": url, "content_sha256": _digest(index + 1), "role_match": True}
        selected.append(identity)
        candidates.append({
            "posting": {
                "url": url, "normalized_url": url, "provider": "lever", "board_token": "acme", "company": "Acme",
                "title": "Staff AI Engineer", "location": "Remote", "published_at": None, "content_sha256": _digest(index + 1),
                "source_kind": "ats", "query_key": "staff-ai-engineer",
            },
            "outcome": "new",
        })
        matrix = [
            {"requirement": f"Requirement {n}", "resume_evidence": ["six years"] if n < met else [], "status": "met" if n < met else "unmet"}
            for n in range(3)
        ]
        assessments.append({
            "posting": identity, "matrix": matrix, "suggestions": [], "proposal_revision_ref": None, "verdict": verdict,
            "questions": ["Have you run workloads on GCP?"] if verdict == "pending_user_answers" else [],
        })
    return AssessOutput.from_json({
        "schema_version": "scout-find-jobs-assess-output:1", "selected_postings": selected, "pinned_resume": _PINNED,
        "target": "targets/acme", "selection_cap": 10, "selection_rule": "new_or_edited_role_match", "candidate_rows": candidates,
        "assessments": assessments, "not_assessed": [], "proposal_revision_refs": [], "model_target": "codex_cli",
        "producer": {"callable": "scout.find_jobs.assess", "version": "1", "actor": "scout-assess", "model_target": "codex_cli", "adapter": "codex_cli"},
        "usage": None, "failures": [], "prompt_version": prompt_version, "constraints_digest": _CONSTRAINTS,
    })


def _evidence(run_id: str, started_at: str, output: AssessOutput | None, *, profile_id: str | None, status: str = "succeeded"):
    """One run's committed evidence, in ``projection.RunEvidence``'s shape (what ``run_history`` reads of it)."""

    ref = None if profile_id is None else ProfileRef(profile_id, 3, "sha256:" + "d" * 64)
    return SimpleNamespace(
        run_id=run_id, started_at=started_at, terminal=status in {"succeeded", "failed", "cancelled"},
        run_input=SimpleNamespace(profile_ref=ref), assess_output=output,
    )


class _Runs:
    """A synthetic source of runs; counts how often the runs themselves are read."""

    def __init__(self, runs: dict[str, object]) -> None:
        self.runs = runs
        self.read: list[tuple[str, ...]] = []

    def run_ids(self) -> list[str]:
        return sorted(self.runs)

    def evidence(self, run_ids):
        self.read.append(tuple(run_ids))
        return {run_id: self.runs[run_id] for run_id in run_ids}


def _migrate(fx: PostingsFixture, source: _Runs) -> dict[str, object]:
    return run_history.migrate_runs(fx.home_root, fx.target, source=source)


def _store(fx: PostingsFixture) -> PipelineStore:
    return PipelineStore(pipeline_path(fx.home_root, fx.target))


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def test_an_old_run_migrates_once_with_its_provenance_and_a_profile_less_run_is_ephemeral_and_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", n) for n in (1, 2, 3)], seen_at=days_ago(30))
    old = _assess_output([
        (job_url("acme", 1), "matched_above_threshold", 3), (job_url("acme", 2), "pending_user_answers", 1), (_GONE, "not_a_match", 0),
    ])
    loose = _assess_output([(job_url("acme", 3), "matched_above_threshold", 2)], prompt_version="assess-prompt-v4")
    source = _Runs({
        _OLD_RUN: _evidence(_OLD_RUN, "2026-09-01T10:00:00Z", old, profile_id=fx.default_profile_id),
        _LOOSE_RUN: _evidence(_LOOSE_RUN, "2026-08-15T08:00:00Z", loose, profile_id=None),
        _LIVE_RUN: _evidence(_LIVE_RUN, "2026-10-03T14:00:00Z", None, profile_id=fx.default_profile_id, status="running"),
    })
    before = _search(fx)
    assert before["counts"]["by_state"] == {"not_assessed": 3}  # type: ignore[index]
    calls = fx.base.model.calls

    first = _migrate(fx, source)

    assert first["schema_version"] == "scout-run-history:1"
    assert {key: first[key] for key in ("runs", "runs_imported", "runs_already_imported", "runs_not_finished", "runs_unreadable")} == {
        "runs": 3, "runs_imported": 2, "runs_already_imported": 0, "runs_not_finished": 1, "runs_unreadable": 0,
    }
    assert (first["assessments_imported"], first["ephemeral_assessments"], first["rows_skipped"], first["values_dropped"]) == (4, 1, 0, 0)
    assert first["by_profile"] == [{"profile_id": "ephemeral", "assessments": 1}, {"profile_id": fx.default_profile_id, "assessments": 3}]
    assert first["imported"] == [
        {"run_id": _LOOSE_RUN, "profile_id": "ephemeral", "assessments": 1},
        {"run_id": _OLD_RUN, "profile_id": fx.default_profile_id, "assessments": 3},
    ]

    # Idempotent: the second migration reads no run that is imported and imports nothing.
    second = _migrate(fx, source)
    assert (second["runs_imported"], second["assessments_imported"], second["ephemeral_assessments"]) == (0, 0, 0)
    assert second["runs_already_imported"] == 2 and second["imported"] == [] and second["by_profile"] == []
    assert source.read == [(_LOOSE_RUN, _OLD_RUN, _LIVE_RUN), (_LIVE_RUN,)]  # the second time: only the unfinished run
    store = _store(fx)
    try:
        assert store.imported_runs() == {_OLD_RUN: 3, _LOOSE_RUN: 1}
        stored = {(row.profile_id, row.job): row for row in store.run_assessments()}
        assert len(stored) == 4  # no data loss: all four assessments, the one whose posting is gone included
        # The provenance fields the run sealed, on the imported row.
        assert stored[(fx.default_profile_id, job_url("acme", 1))] == RunAssessment(
            run_id=_OLD_RUN, job=job_url("acme", 1), profile_id=fx.default_profile_id, state="matched",
            assessed_at="2026-09-01T10:00:00.000000Z", reqs_met=3, reqs_total=3, open_questions=0, listing_digest=_digest(1),
            prompt_version=ASSESS_PROMPT_VERSION, constraints_digest=_CONSTRAINTS, bank_digest=None, profile_revision=3,
            profile_digest="sha256:" + "d" * 64, pinned_record="rec_0001", pinned_revision="rev_0002",
            pinned_digest="sha256:" + "9" * 64, model_target="codex_cli", adapter="codex_cli",
        )
        loose_row = stored[(EPHEMERAL_PROFILE, job_url("acme", 3))]
        assert (loose_row.run_id, loose_row.profile_revision, loose_row.profile_digest, loose_row.prompt_version) == (_LOOSE_RUN, None, None, "assess-prompt-v4")
    finally:
        store.close()

    # The read model shows the run's assessments for the run's profile, with where they came from.
    after = _search(fx, history=True)
    rows = {row["job_identity"]: row for row in after["postings"]["rows"]}  # type: ignore[index]
    one, two, three = (rows[job_url("acme", n)] for n in (1, 2, 3))
    # 0.1.11.2: 3 of 3 is a thin posting: the counts are served, a percentage is not.
    assert (one["profile_id"], one["state"], one["score"], one["score_kind"]) == (fx.default_profile_id, "matched", None, None)
    assert (one["thin_posting"], one["fit"]) == (True, None)
    assert one["assessment"] == {"verdict": None, "met": 3, "requirements": 3, "percent": None, "assessed_at": "2026-09-01T10:00:00.000000Z"}
    assert (two["state"], two["assessment"]["met"], two["assessment"]["requirements"]) == ("needs_answers", 1, 3)
    assert one["stale_reason"] == "posting_changed"  # the indexed posting is not the text the run assessed: said, not hidden
    basis = one["assessment_basis"]
    assert basis == {
        "origin": f"run:{_OLD_RUN}", "run_id": _OLD_RUN, "prompt_version": ASSESS_PROMPT_VERSION, "prompt_sealed": True,
        "constraints_digest": _CONSTRAINTS, "story_bank_digest": None,
        "profile_ref": {"profile_id": fx.default_profile_id, "revision": 3, "content_digest": "sha256:" + "d" * 64},
        "resume": _PINNED, "posting_sha256": _digest(1), "model_target": "codex_cli", "model": "codex_cli",
    }
    # The profile-less run's posting is NOT assessed for any profile: it stays not_assessed in the default view.
    assert (three["state"], three["assessment"], three["assessment_basis"]) == ("not_assessed", None, None)
    assert after["counts"]["by_state"] == {"matched": 1, "needs_answers": 1, "not_assessed": 1}  # type: ignore[index]

    # History: the run's three rows (the gone posting too); the ephemeral one is hidden by default.
    history = after["history"]
    assert (history["runs_imported"], history["hidden"]) == (2, 1)  # type: ignore[index]
    assert sorted(item["job_identity"] for item in history["rows"]) == sorted([job_url("acme", 1), job_url("acme", 2), _GONE])  # type: ignore[index]
    assert all(item["profile_id"] == fx.default_profile_id and item["hidden"] is False for item in history["rows"])  # type: ignore[index]
    assert EPHEMERAL_PROFILE not in json.dumps(_search(fx)) and EPHEMERAL_PROFILE not in json.dumps(after["history"]["rows"])  # type: ignore[index]
    # ...and listed when asked for, marked hidden, with no profile identity.
    for asked in (_search(fx, history=True, include_hidden=True), _search(fx, history=True, profile_ids=[EPHEMERAL_PROFILE])):
        (hidden,) = [item for item in asked["history"]["rows"] if item["profile_id"] == EPHEMERAL_PROFILE]  # type: ignore[index]
        assert (hidden["job_identity"], hidden["hidden"], hidden["state"]) == (job_url("acme", 3), True, "matched")
        assert hidden["basis"]["origin"] == f"run:{_LOOSE_RUN}" and hidden["basis"]["profile_ref"] is None
    assert _search(fx, history=True, profile_ids=[EPHEMERAL_PROFILE])["postings"]["rows"] == []  # type: ignore[index]
    posting_search.check_response(after)
    assert all(marker not in json.dumps(after) and "six years" not in json.dumps(after) for marker in MARKERS)

    # Ephemeral never triggers the pipeline and is never ranked.
    with pytest.raises(StepError):
        enqueue_job(EPHEMERAL_PROFILE, job_url("acme", 3), home_root=fx.home_root, target=fx.target, trigger="process_now")
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    ranked = rank_lane.rank_tick(fx.home_root, fx.target, now=lambda: NOW, busy=lambda: "sources_update")
    assert EPHEMERAL_PROFILE not in json.dumps(ranked)
    store = _store(fx)
    try:
        assert store.steps() == () and store.posting_count(profile_id=EPHEMERAL_PROFILE) == 0
    finally:
        store.close()

    assert fx.base.model.calls == calls  # the migration and every read above made no model call

    # Latest wins: a newer assessment from the job page replaces the run's in the read model; the run's row stays as history.
    assess_base(fx.base, job_url("acme", 2), met=2)
    newer = {row["job_identity"]: row for row in _search(fx)["postings"]["rows"]}[job_url("acme", 2)]  # type: ignore[index]
    assert (newer["state"], newer["assessment_basis"]) == ("matched", {"origin": "quick_assess"})
    assert len(_search(fx, history=True)["history"]["rows"]) == 3  # type: ignore[index]

    # The CLI reads the runs from the journal: this gig has none, so there is nothing to import.
    printed = CliRunner().invoke(cli, ["scout", "jobs", "import-runs", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert printed.exit_code == 0, printed.output
    counts = json.loads(printed.output)
    assert (counts["runs"], counts["runs_imported"], counts["assessments_imported"]) == (0, 0, 0)


def test_a_run_value_that_is_text_is_left_out_and_counted_never_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    output = _assess_output([(job_url("acme", 1), "matched_above_threshold", 3)], prompt_version="the prompt with Jane's notes")
    pasted = _assess_output([("https://jobs.lever.co/acme/a b", "matched_above_threshold", 3)])
    source = _Runs({
        _OLD_RUN: _evidence(_OLD_RUN, "2026-09-01T10:00:00Z", output, profile_id=fx.default_profile_id),
        _LOOSE_RUN: _evidence(_LOOSE_RUN, "not a time", pasted, profile_id="Jane Doe"),
    })

    counts = _migrate(fx, source)

    assert (counts["runs_imported"], counts["assessments_imported"], counts["rows_skipped"], counts["values_dropped"]) == (2, 1, 1, 1)
    store = _store(fx)
    try:
        (row,) = store.run_assessments()
        assert row.prompt_version is None and row.profile_id == fx.default_profile_id
        assert store.imported_runs() == {_OLD_RUN: 1, _LOOSE_RUN: 0}  # a profile id that is not an id: ephemeral, and its row was not a URL
    finally:
        store.close()
    raw = b"".join(path.read_bytes() for path in pipeline_path(fx.home_root, fx.target).parent.iterdir())
    assert b"Jane" not in raw


class _Launches:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        real = subprocess.Popen.__init__

        def counted(popen, *args, **kwargs):
            self.count += 1
            return real(popen, *args, **kwargs)

        monkeypatch.setattr(subprocess.Popen, "__init__", counted)

    def take(self) -> int:
        count, self.count = self.count, 0
        return count


def test_timing_gate_reading_the_read_model_after_migrating_5000_run_assessed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    per_board = GATE_POSTINGS // GATE_BOARDS
    jobs: list[str] = []
    for board in range(GATE_BOARDS):
        slug = f"co{board:02d}"
        fx.seed(slug, [lever_job(slug, n) for n in range(per_board)], seen_at=days_ago(1))
        jobs.extend(job_url(slug, n) for n in range(per_board))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    # 5,000 run-assessed rows: 100 old runs of 50 assessed postings each, for the default profile.
    runs = {}
    for number in range(GATE_POSTINGS // 50):
        run_id = f"run_202609{number % 28 + 1:02d}T{number % 24:02d}0000Z{number:03d}"
        chunk = jobs[number * 50:(number + 1) * 50]
        output = SimpleNamespace(
            pinned_resume=SimpleNamespace(**_PINNED), prompt_version=ASSESS_PROMPT_VERSION, constraints_digest=_CONSTRAINTS,
            story_bank=None, model_target="codex_cli", producer=SimpleNamespace(adapter="codex_cli"),
            assessments=[
                SimpleNamespace(
                    posting=SimpleNamespace(normalized_url=job, content_sha256=_digest(index)), verdict="matched_above_threshold",
                    matrix=[SimpleNamespace(status=SimpleNamespace(value="met"))] * 2, structured_questions=(), questions=(),
                )
                for index, job in enumerate(chunk)
            ],
        )
        runs[run_id] = _evidence(run_id, "2026-09-01T10:00:00Z", output, profile_id=fx.default_profile_id)  # type: ignore[arg-type]
    launches = _Launches(monkeypatch)

    started = time.process_time()
    counts = _migrate(fx, _Runs(runs))
    migrate_cpu = time.process_time() - started
    assert (counts["runs_imported"], counts["assessments_imported"]) == (GATE_POSTINGS // 50, GATE_POSTINGS)
    launches.take()

    # The first read after the import gives the rows their facts again (the index is not read again).
    started = time.process_time()
    rebuilt = postings.refresh(fx.home_root, fx.target, now=NOW)
    facts_cpu = time.process_time() - started
    facts_launches = launches.take()
    assert rebuilt.builds[fx.default_profile_id] == postings.BUILD_FACTS and rebuilt.rows == GATE_POSTINGS * 2

    # A per-request read: the digests are compared (nothing is rebuilt), then the rows and the imported history are read.
    store = postings.open_store(fx.home_root, fx.target)
    try:
        started = time.process_time()
        read = postings.refresh(fx.home_root, fx.target, store=store, now=NOW)
        rows = store.postings(profile_id=fx.default_profile_id)
        top = store.postings_by_score(states=("matched",), limit=10)
        history = store.run_assessments(profile_id=fx.default_profile_id, latest=True)
        read_cpu = time.process_time() - started
    finally:
        store.close()
    read_launches = launches.take()
    assert set(read.builds.values()) == {postings.BUILD_UNCHANGED}
    assert len(rows) == GATE_POSTINGS and all(row.state == "matched" and row.reqs_total == 2 for row in rows)
    assert len(top) == 10 and len(history) == GATE_POSTINGS

    # And the search a page is served from: one page of rows, with their provenance.
    started = time.process_time()
    page = _search(fx, states=["matched"], limit=50)
    search_cpu = time.process_time() - started
    search_launches = launches.take()
    assert page["counts"]["matched"] == GATE_POSTINGS and len(page["postings"]["rows"]) == 50  # type: ignore[index,arg-type]
    assert all(str(row["assessment_basis"]["origin"]).startswith("run:") for row in page["postings"]["rows"])  # type: ignore[index]

    with capsys.disabled():
        print(
            f"\nM4a timing gate: {GATE_POSTINGS} run-assessed rows imported in {migrate_cpu:.2f} s CPU; facts rebuild "
            f"{facts_cpu:.2f} s CPU, {facts_launches} launches; per-request read {read_cpu:.3f} s CPU, {read_launches} launches; "
            f"search page {search_cpu:.3f} s CPU, {search_launches} launches"
        )
    assert read_cpu < READ_CPU_CEILING, f"a per-request read took {read_cpu:.3f} s CPU"
    assert search_cpu < READ_CPU_CEILING, f"a search page took {search_cpu:.3f} s CPU"
    assert max(facts_launches, read_launches, search_launches) <= MAX_LAUNCHES, (facts_launches, read_launches, search_launches)
