"""0.1.10.7 M4a: the live index search and "Assess these" (``scout/posting_search.py``). Synthetic only.

(a) The live search returns, per active profile, exactly the postings the
    stored index matches for it (``index_search.read_indexed_boards``, asked
    independently here) and the read model shows; no run is created (the
    workpad's ``runs`` folder is untouched), no board is asked (no socket is
    opened) and no model is called. A changed profile setting is seen by the
    next search. A deleted profile is never listed.
(c) "Assess these" asks first (count, estimate): no model call and no
    approval record without approval. With approval the postings are assessed
    by the fixture model, the batch is recorded as approved, it is live work
    while it runs, and the read model shows the results.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import data_labels, posting_search, postings, profile_records, scout_new
from gigai.scout.pipeline import runner
from gigai.scout.pipeline.store import LEASE_ASSESS_BATCH, PipelineStore, pipeline_path

from tests.support.pipeline_fixtures import MARKERS
from tests.support.posting_fixtures import (
    NOW,
    TITLE_SECOND_ONLY,
    PostingsFixture,
    build_postings_fixture,
    days_ago,
    job_url,
    lever_job,
)
from tests.support.scout_profile_fixtures import uuids

_PRIVATE_WORDS = ("six years", "Two years on GCP", "Story bank")


def _seed(fx: PostingsFixture) -> None:
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1))
    fx.seed("old", [lever_job("old", 1)], seen_at=days_ago(20))


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _jobs(response: dict[str, object]) -> set[str]:
    return {row["job_identity"] for row in response["postings"]["rows"]}  # type: ignore[index]


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted((str(path.relative_to(root)), path.stat().st_mtime_ns) for path in root.rglob("*")) if root.exists() else []


def _store(fx: PostingsFixture) -> PipelineStore:
    return PipelineStore(pipeline_path(fx.home_root, fx.target))


def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every attempt to open a connection is counted and refused: a board request cannot go unnoticed."""

    attempts: list[object] = []

    def refuse(self, address, *args, **kwargs):
        attempts.append(address)
        raise AssertionError(f"a request was attempted: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


def _assert_public_only(response: dict[str, object]) -> None:
    scout_new.check_response(response)
    assert set(scout_new.response_labels(response)) == {data_labels.PUBLIC_UNTRUSTED}
    dumped = json.dumps(response)
    for word in (*_PRIVATE_WORDS, *MARKERS, "Old Search"):
        assert word not in dumped, word


def test_a_live_search_returns_what_the_index_matches_per_active_profile_with_no_run_and_no_board_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed(fx)
    runs = Path(fx.base.gig.resolved.path) / "runs"
    runs_before = _tree(runs)
    attempts = _no_network(monkeypatch)
    calls = fx.base.model.calls

    everything = _search(fx)

    # What the index matches for each active profile, asked the index's own way, independently of the read model.
    _resolved, views = postings.active_profiles(fx.home_root, fx.target)
    assert [view.profile_id for view in views] == [fx.default_profile_id, fx.second_profile_id]
    matched = {
        view.profile_id: {row.normalized_url for row in postings.profile_posting_rows(view, fx.home_root, fx.target, NOW)}  # type: ignore[attr-defined]
        for view in views
    }
    assert matched == {
        fx.default_profile_id: {job_url("acme", 1), job_url("old", 1)},
        fx.second_profile_id: {job_url("acme", 1), job_url("acme", 2), job_url("old", 1)},
    }
    store = _store(fx)
    try:
        for profile_id, expected in matched.items():
            found = _search(fx, profile_ids=[profile_id])
            assert _jobs(found) == expected  # the search == the index's own match
            assert {row.job for row in store.postings(profile_id=profile_id)} == expected  # == what the read model shows
            assert {row["profile_id"] for row in found["postings"]["rows"]} == {profile_id}  # type: ignore[index]
            _assert_public_only(found)
        assert _jobs(everything) == set().union(*matched.values()) == {row.job for row in store.postings()}
    finally:
        store.close()
    # Each posting once, tagged with every active profile it matches, best first; the deleted profile never.
    by_job = {row["job_identity"]: row for row in everything["postings"]["rows"]}  # type: ignore[index]
    assert len(by_job) == len(everything["postings"]["rows"]) == 3  # type: ignore[index,arg-type]
    assert [item["profile_id"] for item in by_job[job_url("acme", 1)]["profiles"]] == [fx.default_profile_id, fx.second_profile_id]
    assert [item["profile_id"] for item in by_job[job_url("acme", 2)]["profiles"]] == [fx.second_profile_id]
    assert fx.deleted_profile_id is not None and fx.deleted_profile_id not in json.dumps(everything)
    assert everything["counts"] == {"matched": 3, "shown": 3, "new": 2, "by_state": {"not_assessed": 3}, "weak_fit": 0}
    assert [(item["profile_id"], item["matched"]) for item in everything["profiles"]] == [(fx.default_profile_id, 2), (fx.second_profile_id, 3)]  # type: ignore[union-attr]
    assert everything["history"] is None and everything["anchor"]["last_checked_at"] is None  # type: ignore[index]
    _assert_public_only(everything)

    # The filters: words, the time window, the state, paging.
    assert _jobs(_search(fx, query="ai")) == {job_url("acme", 1), job_url("old", 1)}
    assert _jobs(_search(fx, query="acme engineer")) == {job_url("acme", 1), job_url("acme", 2)}
    assert _jobs(_search(fx, window="new")) == {job_url("acme", 1), job_url("acme", 2)}  # first use: first seen in the last 7 days
    assert _jobs(_search(fx, window="30d")) == set(by_job)
    assert _jobs(_search(fx, states=["needs_answers", "matched"])) == set() and _jobs(_search(fx, states="not_assessed")) == set(by_job)
    paged = _search(fx, limit=2, offset=2)
    assert paged["counts"]["matched"] == 3 and paged["counts"]["shown"] == 1  # type: ignore[index]
    with pytest.raises(posting_search.PostingSearchError) as refused:
        _search(fx, profile_ids=[fx.deleted_profile_id])
    assert refused.value.code == "profile_not_found"
    with pytest.raises(posting_search.PostingSearchError):
        _search(fx, window="yesterday")

    # A changed setting is seen by the next search: no run, no "search again" step.
    profile_records.write_profile(
        fx.base.gig.resolved, profile_id=fx.second_profile_id, titles=("staff ai engineer",), queries=("staff ai engineer",),
        uuid_factory=uuids(61),
    )
    assert _jobs(_search(fx, profile_ids=[fx.second_profile_id])) == {job_url("acme", 1), job_url("old", 1)}

    # The CLI prints the same object.
    printed = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.output)["schema_version"] == "scout-postings:1"
    lines = CliRunner().invoke(cli, ["scout", "jobs", "list", "--window", "new", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert lines.exit_code == 0 and "posting(s) match" in lines.output

    # No run was created, no board was asked, no model was called, and the "new since" anchor did not move.
    assert _tree(runs) == runs_before
    assert attempts == []
    assert fx.base.model.calls == calls
    store = _store(fx)
    try:
        assert store.anchor() is None and store.approvals() == ()
    finally:
        store.close()


def test_assess_these_asks_first_and_assesses_only_on_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed(fx)
    wanted = [job_url("acme", 1), job_url("acme", 2)]
    calls = fx.base.model.calls

    def these(**kwargs: object) -> dict[str, object]:
        kwargs.setdefault("now", NOW)
        return posting_search.assess_these(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]

    # 1. Without approval: the question, and nothing else.
    ask = these(jobs=[*wanted, "https://jobs.lever.co/acme/acme-99999"])
    assert (ask["schema_version"], ask["status"]) == ("scout-postings-assess:1", "ask")
    question = ask["question"]
    assert (question["kind"], question["selected"], question["to_assess"], question["already_current"]) == ("assess_these", 2, 2, 0)  # type: ignore[index]
    assert question["by_profile"] == [{"profile_id": fx.default_profile_id, "count": 1}, {"profile_id": fx.second_profile_id, "count": 1}]  # type: ignore[index]
    assert question["estimate"]["calls"] == 2 and question["text"].startswith("Assess 2 postings (")  # type: ignore[index]
    assert question["yes"]["api"]["body"] == {"approve": True, "jobs": [*wanted, "https://jobs.lever.co/acme/acme-99999"]}  # type: ignore[index]
    assert ask["not_found"] == ["https://jobs.lever.co/acme/acme-99999"] and ask["assessed"] is None and ask["approval"] is None
    assert {row["state"] for row in ask["postings"]["rows"]} == {"not_assessed"}  # type: ignore[index]
    by_filter = these(window="new", states=["not_assessed"])
    assert by_filter["status"] == "ask" and by_filter["question"]["to_assess"] == 2  # type: ignore[index]
    asked_by_cli = CliRunner().invoke(
        cli, ["scout", "jobs", "assess", *wanted, "--home", str(fx.home_root), "--target", str(fx.target), "--json"]
    )
    assert asked_by_cli.exit_code == 0 and json.loads(asked_by_cli.output)["status"] == "ask"
    _assert_public_only(ask)
    # NOTHING was assessed and nothing was recorded as approved.
    assert fx.base.model.calls == calls
    store = _store(fx)
    try:
        assert store.approvals() == () and {row.state for row in store.postings()} == {"not_assessed"}
    finally:
        store.close()

    # 2. While another batch holds the lease, a second one is refused (and assesses nothing). The lease only keeps a
    #    second batch out: what the pipeline yields to is the batch's live marker (pipeline.busy), step 3.
    other = _store(fx)
    try:
        assert other.take_lease(LEASE_ASSESS_BATCH, worker="other")
        with pytest.raises(posting_search.PostingSearchError) as refused:
            these(jobs=wanted, approve=True)
        assert refused.value.code == "assess_batch_running" and fx.base.model.calls == calls
        other.release_lease(LEASE_ASSESS_BATCH, worker="other")
    finally:
        other.close()
    assert runner.live_work(fx.home_root, fx.target) is None

    # 3. With approval: assessed by the fixture model, registered as live work while it runs.
    seen_live: list[str | None] = []
    real = posting_search._assess

    def watched(*args: object, **kwargs: object):
        seen_live.append(runner.live_work(fx.home_root, fx.target))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(posting_search, "_assess", watched)
    done = these(jobs=wanted, approve=True, decided_by="agent")
    assert done["status"] == "assessed" and done["question"] is None
    assert done["assessed"] == {"requested": 2, "assessed": 2, "failed": [], "stopped": None, "fetched_on_demand": 0}
    assert fx.base.model.calls == calls + 2
    assert seen_live == [runner.BUSY_ASSESS_BATCH] and runner.live_work(fx.home_root, fx.target) is None
    _assert_public_only(done)
    store = _store(fx)
    try:
        (approval,) = store.approvals()
        assert (approval.trigger, approval.state, approval.decided_by, approval.jobs, approval.est_calls) == ("assess_these", "approved", "agent", 2, 2)
        assert done["approval"] == {"id": approval.id, "decided_by": "agent", "jobs": 2}
        # The results are in the read model.
        rows = {(row.job, row.profile_id): row for row in store.postings()}
        for pair in ((wanted[0], fx.default_profile_id), (wanted[1], fx.second_profile_id)):
            assert rows[pair].state == "matched" and (rows[pair].reqs_met, rows[pair].reqs_total) == (2, 2) and rows[pair].assessed_at
        assert rows[(job_url("old", 1), fx.default_profile_id)].state == "not_assessed"  # not named: not assessed
    finally:
        store.close()
    shown = {row["job_identity"]: row for row in _search(fx)["postings"]["rows"]}  # type: ignore[index]
    for job in wanted:
        assert (shown[job]["state"], shown[job]["score"], shown[job]["score_kind"]) == ("matched", 100, "assessment")
        assert shown[job]["assessment_basis"] == {"origin": "quick_assess"}
    assert shown[job_url("old", 1)]["assessment_basis"] is None

    # 4. Asked again: nothing is left, and no call is made, with or without approval.
    again = these(jobs=wanted, approve=True)
    assert again["status"] == "nothing_to_assess" and again["counts"]["already_current"] == 2  # type: ignore[index]
    assert fx.base.model.calls == calls + 2
