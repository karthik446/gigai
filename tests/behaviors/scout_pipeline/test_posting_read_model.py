"""0.1.10.7 PL6: the per-(posting, profile) read model (``scout/postings.py``). Synthetic only.

1. Tags: a posting has one row per ACTIVE profile it matches, best first; a
   deleted profile (whose titles match everything) has none, and deleting or
   archiving a profile drops its rows.
2. What a row carries: ``first_seen`` from the index, the cached rank score
   (no model call), the assessment's state and counts, ``removed_at`` once the
   board no longer lists the posting.
3. Invalidation: a changed posting, changed profile settings and a changed
   resume each rebuild what they must and nothing else; an unchanged read
   rebuilds nothing.
4. THE TIMING GATE (hard acceptance): 5,000 postings x 2 profiles. The full
   rebuild and a per-request read are measured in process CPU seconds (not
   wall clock) under generous ceilings, and neither starts a subprocess per
   posting: the launches are counted, for 40 postings and for 5,000, and stay
   under one small fixed number.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.private_records import create_record, import_reference
from gigai.scout import postings, profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.find_jobs.model_rank import _hex, cache_dir, cache_key, prefs_digest
from gigai.scout.find_jobs.rank_digest import resume_digest
from gigai.scout.find_jobs.rank_run import rank_prefs
from gigai.scout.pipeline.store import PipelineStore, pipeline_path
from gigai.scout.quick_assess import run_quick_assessment

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import RESUME, assess_base, assessment, resolved_job
from tests.support.posting_fixtures import (
    NOW,
    TITLE_BOTH,
    TITLE_SECOND_ONLY,
    PostingsFixture,
    build_postings_fixture,
    days_ago,
    job_url,
    lever_job,
    posting_text,
)
from tests.support.scout_profile_fixtures import uuids

#: Generous ceilings, in process CPU seconds (measured on a laptop: see the worker report for the numbers).
REBUILD_CPU_CEILING = 30.0
READ_CPU_CEILING = 3.0
#: A cold rebuild launches about 27 git processes for its journal reads; 10,000 rows must not add one.
MAX_LAUNCHES = 40
GATE_POSTINGS = 5_000
GATE_BOARDS = 10


def _rows(fx: PostingsFixture, **kwargs: object):
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        return store.postings(**kwargs)  # type: ignore[arg-type]
    finally:
        store.close()


def _refresh(fx: PostingsFixture, **kwargs: object) -> postings.RefreshResult:
    return postings.refresh(fx.home_root, fx.target, now=NOW, **kwargs)  # type: ignore[arg-type]


class _Launches:
    """Every subprocess this process starts while it is open."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        real = subprocess.Popen.__init__

        def counting(popen, *args, **kwargs):
            self.count += 1
            return real(popen, *args, **kwargs)

        monkeypatch.setattr(subprocess.Popen, "__init__", counting)

    def take(self) -> int:
        taken, self.count = self.count, 0
        return taken


def test_a_posting_is_tagged_with_every_active_profile_it_matches_and_never_a_deleted_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY), lever_job("acme", 3, title="Account Executive")], seen_at=days_ago(1))

    done = _refresh(fx)

    assert {view.profile_id for view in done.profiles} == {fx.default_profile_id, fx.second_profile_id}
    assert [view.is_default for view in done.profiles] == [True, False]
    rows = _rows(fx)
    assert {(row.job, row.profile_id) for row in rows} == {
        (job_url("acme", 1), fx.default_profile_id), (job_url("acme", 1), fx.second_profile_id), (job_url("acme", 2), fx.second_profile_id),
    }
    assert fx.deleted_profile_id not in {row.profile_id for row in rows}
    both = [row for row in rows if row.job == job_url("acme", 1)]
    # No rank score, no assessment: the default profile is the best tag.
    assert [(row.profile_id, row.match_rank) for row in both] == [(fx.default_profile_id, 1), (fx.second_profile_id, 2)]
    assert {row.first_seen for row in rows} == {"2026-10-02T15:00:00.000000Z"} and {row.board for row in rows} == {"lever:acme"}
    assert all(row.state == "not_assessed" and row.rank_score is None and row.removed_at is None and row.listing_known for row in rows)

    # Archiving the second profile drops its rows; the posting only it matched is gone from the model.
    profile_records.write_profile(fx.base.gig.resolved, profile_id=fx.second_profile_id, state="archived", uuid_factory=uuids(50))
    again = _refresh(fx)
    assert again.dropped == 2 and {row.profile_id for row in _rows(fx)} == {fx.default_profile_id}


def test_no_posting_or_resume_text_is_kept_in_the_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1))
    _refresh(fx)

    path = pipeline_path(fx.home_root, fx.target)
    dump = "\n".join(sqlite3.connect(path).iterdump())
    for text in ("Staff AI Engineer", "Staff Engineer", "inference", "Python", "Quillfeather", "Old Search", "Remote"):
        assert text not in dump, text
    assert job_url("acme", 1) in dump  # the job identity (a public URL) is the key


def test_a_row_carries_the_cached_rank_score_the_assessment_and_removed_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    # The rank cache is keyed by the configured model: the fixture's model targets, as the CLI would load them.
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2)], seen_at=days_ago(2))
    done = _refresh(fx)
    first = {row.profile_id: row for row in _rows(fx, jobs=[job_url("acme", 1)])}

    # A rank score in the home's score cache for the SECOND profile (written as the ranker writes it).
    view = next(item for item in done.profiles if item.profile_id == fx.second_profile_id)
    prefs = rank_prefs(view.config)  # type: ignore[arg-type]
    model = postings.rank_model_key(fx.home_root, fx.target)
    assert model is not None
    key = cache_key(
        content_sha256=first[fx.second_profile_id].listing_digest, resume_digest_sha256=_hex({"resume_digest": resume_digest(RESUME, prefs)}),
        prefs_sha256=prefs_digest(prefs), model=model,
    )
    cache_dir(fx.home_root).mkdir(parents=True, exist_ok=True)
    (cache_dir(fx.home_root) / f"{key}.json").write_text(json.dumps({"score": 91, "reasons": [], "blockers": []}), encoding="utf-8")
    # An assessment of the same posting for the default profile, with an open requirement.
    assess_base(fx.base, job_url("acme", 1), met=1)
    calls = fx.base.model.calls

    done = _refresh(fx)

    assert done.builds == {fx.default_profile_id: postings.BUILD_FACTS, fx.second_profile_id: postings.BUILD_FACTS}
    assert fx.base.model.calls == calls  # the read model never calls a model
    rows = {row.profile_id: row for row in _rows(fx, jobs=[job_url("acme", 1)])}
    # 0110-8-01: the profile with a current assessment stays the best tag; another profile's rank score does not move it.
    assert rows[fx.second_profile_id].rank_score == 91 and rows[fx.second_profile_id].match_rank == 2
    assessed = rows[fx.default_profile_id]
    assert assessed.match_rank == 1 and assessed.state == "matched" and (assessed.reqs_met, assessed.reqs_total) == (1, 2)
    assert assessed.assessed_at is not None and not assessed.tailored

    # The board stops listing posting 2: the row stays, with removed_at, and a live read leaves it out.
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(0.5), watch=False)
    _refresh(fx)
    gone = _rows(fx, jobs=[job_url("acme", 2)], live=False)
    assert len(gone) == 2 and {row.removed_at for row in gone} == {"2026-10-03T03:00:00.000000Z"}
    assert _rows(fx, jobs=[job_url("acme", 2)]) == ()


def _second_resume(fx: PostingsFixture, tmp_path: Path) -> PinnedResume:
    text = b"# Resume\n\nStaff engineer: Go services and Terraform. (fixture only.)\n"
    path = tmp_path / "resume-second.md"
    path.write_bytes(text)
    gig = fx.base.gig
    imported = import_reference(
        home_root=gig.home_root, requested_target=gig.target, gig_id=gig.created.gig_id, kind="resume", source=path,
        operation_key=f"scout-resume-add:resume-second.md:{digest_imported_bytes(text)}", uuid_factory=uuids(60),
    )
    record = create_record(
        home_root=gig.home_root, requested_target=gig.target, gig_id=gig.created.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id, actor={"kind": "operator", "id": "local-user"},
        origin="imported", operation_key=f"scout-resume-record:{imported.item_id}", uuid_factory=uuids(61),
    )
    return PinnedResume(record.record_id, record.revision_id, digest_imported_bytes(text))


def test_invalidation_by_posting_content_profile_settings_and_resume_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(2))
    built = _refresh(fx)
    assert set(built.builds.values()) == {postings.BUILD_FULL}
    # Assessed as a board row is (the posting's own text), so the stored digest is the index's.
    fx.base.model.assessed = assessment(met=2)
    job = replace(resolved_job(job_url("acme", 1), posting_text(1)), fetch_kind="ats_board", title=TITLE_BOTH)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=job_url("acme", 1)), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root), resolved_job=job,
    )
    _refresh(fx)
    before = {(row.job, row.profile_id): row for row in _rows(fx)}
    assert before[(job_url("acme", 1), fx.default_profile_id)].stale_code is None

    # Nothing changed: nothing is rebuilt.
    assert set(_refresh(fx).builds.values()) == {postings.BUILD_UNCHANGED}

    # 1. The posting's content changes (the board body is re-read): both profiles are matched again, the row has the
    #    new digest and the assessment made on the old text is stale.
    fx.seed("acme", [lever_job("acme", 1, text=posting_text(1, extra=" Now also requires Rust.")), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1), watch=False)
    changed = _refresh(fx)
    assert set(changed.builds.values()) == {postings.BUILD_FULL}
    after = {(row.job, row.profile_id): row for row in _rows(fx)}
    key = (job_url("acme", 1), fx.default_profile_id)
    assert after[key].listing_digest != before[key].listing_digest
    assert after[key].stale_code == "posting_changed" and after[key].state == "matched"
    assert after[key].first_seen == before[key].first_seen  # a changed posting is not a new one
    untouched = (job_url("acme", 2), fx.second_profile_id)
    assert after[untouched].listing_digest == before[untouched].listing_digest

    # 2. One profile's settings change (its titles): only that profile is matched again.
    profile_records.write_profile(
        fx.base.gig.resolved, profile_id=fx.second_profile_id, titles=("staff ai engineer",), queries=("staff ai engineer",), uuid_factory=uuids(51)
    )
    settings = _refresh(fx)
    assert settings.builds == {fx.default_profile_id: postings.BUILD_UNCHANGED, fx.second_profile_id: postings.BUILD_FULL}
    rows = _rows(fx)
    assert {(row.job, row.profile_id) for row in rows} == {(job_url("acme", 1), fx.default_profile_id), (job_url("acme", 1), fx.second_profile_id)}
    assert {row.settings_digest for row in rows if row.profile_id == fx.second_profile_id} != {before[untouched].settings_digest}

    # 3. One profile's resume changes: its rows get their facts again (the index is not read), the other is left alone.
    pinned = _second_resume(fx, tmp_path)
    profile_records.write_profile(fx.base.gig.resolved, profile_id=fx.second_profile_id, resume_ref=pinned, uuid_factory=uuids(52))
    resume = _refresh(fx)
    assert resume.builds == {fx.default_profile_id: postings.BUILD_UNCHANGED, fx.second_profile_id: postings.BUILD_FACTS}
    assert {row.pinned_digest for row in _rows(fx, profile_id=fx.second_profile_id)} == {pinned.content_sha256}
    assert {row.pinned_digest for row in _rows(fx, profile_id=fx.default_profile_id)} != {pinned.content_sha256}


def _seed_many(fx: PostingsFixture, total: int, boards: int, *, watch: bool) -> None:
    per_board = total // boards
    for board in range(boards):
        slug = f"co{board:02d}"
        fx.seed(slug, [lever_job(slug, n) for n in range(per_board)], seen_at=days_ago(1), watch=watch)


def test_timing_gate_5000_postings_two_profiles_rebuild_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    launches = _Launches(monkeypatch)

    # The same boards with 4 postings each first: what a rebuild and a read launch when there is almost nothing to read.
    _seed_many(fx, GATE_BOARDS * 4, GATE_BOARDS, watch=True)
    launches.take()
    _refresh(fx, force=True)
    small_rebuild = launches.take()
    _refresh(fx)
    small_read = launches.take()

    _seed_many(fx, GATE_POSTINGS, GATE_BOARDS, watch=False)
    launches.take()

    started = time.process_time()
    rebuilt = _refresh(fx, force=True)
    rebuild_cpu = time.process_time() - started
    rebuild_launches = launches.take()
    assert rebuilt.rows == GATE_POSTINGS * 2 and set(rebuilt.builds.values()) == {postings.BUILD_FULL}

    # A per-request read: the digests are compared (nothing is rebuilt), then the newest postings and the top 10 are read.
    store = postings.open_store(fx.home_root, fx.target)
    try:
        started = time.process_time()
        read = postings.refresh(fx.home_root, fx.target, store=store, now=NOW)
        new = store.postings(since="2026-09-26T15:00:00.000000Z")
        top = store.postings_by_score(states=("not_assessed", "matched", "needs_answers"), limit=10)
        read_cpu = time.process_time() - started
    finally:
        store.close()
    read_launches = launches.take()
    assert set(read.builds.values()) == {postings.BUILD_UNCHANGED}
    assert len(new) == GATE_POSTINGS * 2 and len(top) == 10 and all(row.match_rank == 1 for row in top)

    with capsys.disabled():
        print(
            f"\nPL6 timing gate: {GATE_POSTINGS} postings x 2 profiles = {rebuilt.rows} rows; "
            f"full rebuild {rebuild_cpu:.2f} s CPU, {rebuild_launches} launches (40 postings: {small_rebuild}); "
            f"per-request read {read_cpu:.3f} s CPU, {read_launches} launches (40 postings: {small_read})"
        )
    assert rebuild_cpu < REBUILD_CPU_CEILING, f"full rebuild took {rebuild_cpu:.2f} s CPU"
    assert read_cpu < READ_CPU_CEILING, f"a per-request read took {read_cpu:.3f} s CPU"
    # No subprocess per posting: 125 times the postings and still a handful of launches (the journal reads of the
    # profiles, the watched boards and the resumes; how many depends on what this process has already read).
    assert max(small_rebuild, small_read, rebuild_launches, read_launches) <= MAX_LAUNCHES, (
        small_rebuild, small_read, rebuild_launches, read_launches,
    )
