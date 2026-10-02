"""0110-036: "Mark applied" and the applications read do not grow with the history.

``POST /api/applications`` took 2.7 s at 300 application events and the first
``GET /api/applications`` after a server start 0.8 to 1.5 s, both growing with
every event: one journal commit publishes one event, and both a write and a
read walked all of them (and every record and every run beside them) again.

Now the committed events are kept at the journal head and caught up by the
commits since (``gigai.application_event_index``), the rows are built from
them (``gigai.scout.application_rows``), and a write reads the events alone.

Pinned here, with counts and never with wall time:

* **the same answer**: over a mixed history of 300 events the kept rows are the
  projection's rows, value for value and key for key, kept in memory, read
  back from the index file, and through the route;
* **flat**: a write, the read after it, and the first read after a server
  start spawn the same few git subprocesses at 4 events and at 300;
* **never stale**: a write is in the very next read; a write by another
  process is; a journal that was reset is read again;
* **the file is a cache**: deleted, corrupt, behind the journal, or holding
  bytes the journal does not, it is checked against the journal and built
  again, and the answer is the journal's;
* **two writers at once** stay consistent;
* **what the index cannot answer takes the old path**, with the old answer.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest

import gigai.application_event_index as event_index
import gigai.application_events as application_events
import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.application_events import record_application
from gigai.journal import JournalArtifact, read_committed_additions, read_committed_snapshot, record_transition, run_with_journal_writer
from gigai.scout import application_rows
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.api import applications as applications_api
from gigai.scout.find_jobs.api import server as server_module
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.projection import projection_from_snapshot, read_projection_snapshot
from gigai.scout.report_readers import default_reader_set
from gigai.workpad import committed_read_cache, paths_committed_between, resolve_workpad, straight_commits_between

from .test_applications_api import _poll_succeeded
from .test_m1_end_to_end import _fixture, _run_request

MIXED_EVENTS = 300
LINKED_POSTINGS = 6
EVENT_KINDS = ("saved", "applied", "interview_scheduled", "offer_received", "rejected", "withdrawn")

# What one request may spawn, whatever the journal holds (measured: 7, 2, 15).
POST_SPAWNS_MAX = 8  # the receipt lookup, then the publication's own six (+1 when another reader has not listed the last commit yet)
GET_AFTER_POST_SPAWNS_MAX = 2  # the commits since, and one cat-file for the new event
FIRST_GET_SPAWNS_MAX = 15  # the workpad check (13), then one ls-tree for each of the two files


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _forget_everything() -> None:
    """As a server that just started: nothing read earlier in this process is kept (the files stay)."""

    for module, names in (
        (workpad_module, ("_validated_repositories", "_resolved_targets", "_committed_between")),
        (journal, ("_validated_workpads", "_snapshot_cache", "_artifact_cache")),
        (server_module, ("_selected_profile_cache", "_profile_resume_details_cache")),
        (applications_api, ("_rows_cache",)),
    ):
        for name in names:
            cache = getattr(module, name, None)
            if cache is not None:
                cache.clear()
    event_index.forget_kept_events()
    application_rows.forget_kept_rows()


@contextmanager
def _scout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real in-process server on a real workpad, after one offline run of six postings."""

    home, target, workpad = _fixture(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "applications-scale-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS", str(LINKED_POSTINGS))
    _forget_everything()
    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"
    try:
        with httpx.Client(base_url=base_url, timeout=120.0) as client:
            digest = client.get("/api/config").json()["config_digest"]
            started = client.post("/api/run", json=_run_request(digest))
            assert started.status_code == 202, started.text
            run_id = started.json()["run_id"]
            assert _poll_succeeded(client, run_id)["status"] == "succeeded"
            rows = client.get(f"/api/runs/{run_id}/results").json()["payload"]["rows"]
            urls = [row["posting"]["normalized_url"] for row in rows]
            assert len(urls) >= 3, urls
            resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
            assert resolved.path == workpad
            yield SimpleNamespace(client=client, base_url=base_url, home=home, target=target, resolved=resolved, workpad=workpad, urls=urls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def scout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with _scout(tmp_path, monkeypatch) as running:
        yield running


def _mixed_history(urls: list[str], count: int) -> list[dict[str, object]]:
    """``count`` events over 60 jobs: every kind, linked / unlinked / pasted jobs, ties, corrections, notes."""

    events: list[dict[str, object]] = []
    for index in range(count):
        job = (index * 7) % 60
        if job % 5 == 0:
            ref = f"https://boards.greenhouse.io/nowhere/jobs/{job}"  # no posting was ever acquired with it
        elif job % 5 == 1:
            ref = "text:sha256:" + hashlib.sha256(f"pasted posting {job}".encode()).hexdigest()
        else:
            ref = urls[job % len(urls)]
        hour = (index * 37) % 97  # not in recording order, and with ties
        occurred_at = f"2026-03-{1 + hour // 24:02d}T{hour % 24:02d}:00:00Z" if index % 4 else f"2026-03-{1 + hour // 24:02d}T{hour % 24:02d}:00:00+02:00"
        events.append({
            "external_ref": ref,
            "event_kind": EVENT_KINDS[(index + index // 60) % len(EVENT_KINDS)],
            "occurred_at": occurred_at,
            "timezone": "UTC" if index % 3 else "Europe/Berlin",
            "notes": (None, "referred by a friend", "résumé v2 ✓")[index % 3],
            "correct_previous": index % 11 == 10,
        })
    return events


def _record_history(resolved, events: list[dict[str, object]]) -> list[dict[str, object]]:
    recorded: list[dict[str, object]] = []
    last_of_job: dict[str, str] = {}
    with committed_read_cache():
        for item in events:
            data = {key: value for key, value in item.items() if key != "correct_previous"}
            data["operation_key"] = f"applications-scale-{uuid.uuid4()}"
            ref = str(item["external_ref"])
            if item["correct_previous"] and ref in last_of_job:
                data["supersedes"] = last_of_job[ref]
            result = record_application(resolved=resolved, data=data, confirm=True)
            assert result["status"] == "recorded"
            last_of_job[ref] = result["event"]["event_id"]
            recorded.append(result["event"])
    return recorded


@pytest.fixture(scope="module")
def mixed(tmp_path_factory: pytest.TempPathFactory):
    """The server of ``scout`` with a mixed history of 300 events, shared by the tests that only add to it."""

    with pytest.MonkeyPatch.context() as monkeypatch:
        with _scout(tmp_path_factory.mktemp("mixed"), monkeypatch) as running:
            running.recorded = _record_history(running.resolved, _mixed_history(running.urls, MIXED_EVENTS))
            yield running


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every subprocess any thread of this process starts (the server's request threads included)."""

    started: list[str] = []
    real = subprocess.run

    def counting(args, *rest, **kwargs):
        started.append(" ".join(str(word) for word in args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return started


def _projection_rows(resolved) -> list[dict[str, object]]:
    """The rows as they were always built: the whole projection, from a snapshot of the journal."""

    projection = projection_from_snapshot(
        snapshot=read_projection_snapshot(resolved), project_id=resolved.project_id, gig_id=resolved.gig_id,
        readers=default_reader_set(resolved),
    )
    return [dict(item) for item in projection.applications]


def _kept_rows(resolved) -> list[dict[str, object]]:
    kept = application_rows.kept_application_rows(resolved)
    assert kept is not None, "the kept rows must answer for a history of plain events"
    return [dict(item) for item in kept.rows]


def _same(left: object, right: object) -> bool:
    """Equal values in the same key order: what a client receives is byte for byte the same."""

    return json.dumps(left) == json.dumps(right)


def _post(client: httpx.Client, url: str, kind: str = "applied", **more: object) -> httpx.Response:
    return client.post("/api/applications", json={"normalized_url": url, "event_kind": kind, **more})


def _get_without_the_kept_rows(client: httpx.Client, monkeypatch: pytest.MonkeyPatch) -> httpx.Response:
    """``GET /api/applications`` the way it was built before: from the projection."""

    with monkeypatch.context() as patch:
        patch.setattr(applications_api, "kept_application_rows", lambda resolved: None)
        applications_api._rows_cache.clear()
        response = client.get("/api/applications")
    applications_api._rows_cache.clear()
    return response


def _index_file(resolved) -> Path:
    return Path(resolved.path) / event_index.INDEX_DIRECTORY / event_index.INDEX_FILENAME


def _links_file(resolved) -> Path:
    return Path(resolved.path) / application_rows.LINKS_DIRECTORY / application_rows.LINKS_FILENAME


# --------------------------------------------------------------------------
# The same answer
# --------------------------------------------------------------------------


def test_the_kept_rows_are_the_projections_rows_over_a_mixed_history(mixed, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = mixed.resolved
    old = _projection_rows(resolved)

    # The history is what it claims to be: every kind, linked and unlinked and pasted jobs, corrections, ties.
    assert len(old) >= MIXED_EVENTS
    assert set(Counter(row["event_kind"] for row in old)) == set(EVENT_KINDS)
    assert sum(1 for row in old if row["linked_posting"] is not None) > 100
    assert sum(1 for row in old if row["linked_posting"] is None and str(row["external_ref"]).startswith("https://")) > 20
    assert sum(1 for row in old if str(row["external_ref"]).startswith("text:sha256:")) > 20
    assert sum(1 for row in old if row["supersedes"]) > 10 and sum(1 for row in old if not row["current"]) > 10
    assert len({row["occurred_at"] for row in old}) < len(old)  # ties
    assert [row["event_id"] for row in old] != sorted(row["event_id"] for row in old)

    # Kept in memory.
    assert _same(_kept_rows(resolved), old)
    # Read back from the files, as after a server start.
    _forget_everything()
    assert _index_file(resolved).is_file() and _links_file(resolved).is_file()
    assert _same(_kept_rows(resolved), old)
    # Built from nothing.
    _forget_everything()
    _index_file(resolved).unlink()
    _links_file(resolved).unlink()
    assert _same(_kept_rows(resolved), old)

    # Through the route: the same bytes with and without the kept rows.
    new_body = mixed.client.get("/api/applications")
    old_body = _get_without_the_kept_rows(mixed.client, monkeypatch)
    assert new_body.status_code == old_body.status_code == 200
    assert new_body.content == old_body.content
    assert len(new_body.json()["applications"]) == len(old)


def test_the_job_state_reader_answers_the_same_events(mixed, monkeypatch: pytest.MonkeyPatch) -> None:
    new = job_state.read_application_events(mixed.resolved)
    with monkeypatch.context() as patch:
        def unavailable(resolved, **_kwargs):
            raise event_index.ApplicationEventIndexUnavailable("off")

        patch.setattr(job_state, "committed_application_events", unavailable)
        old = job_state.read_application_events(mixed.resolved)
    assert _same(new, old) and len(new) > 30


def test_a_writer_reads_the_same_prior_events_from_the_index_and_from_a_snapshot(mixed) -> None:
    resolved = mixed.resolved

    def both(writer):
        kept = application_events._kept_prior_events(resolved, writer, "records/operations/application-record-" + "0" * 64 + ".json")
        snapshot = writer.snapshot(("records/",))
        return kept, application_events._events(snapshot, resolved.project_id, resolved.gig_id)

    kept, old = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=both)
    assert kept is not None and len(kept) >= MIXED_EVENTS
    by_id = lambda events: sorted(events, key=lambda event: event["event_id"])  # noqa: E731
    assert _same(by_id(kept), by_id(old))


# --------------------------------------------------------------------------
# Flat
# --------------------------------------------------------------------------


def _costs(running, spawns: list[str]) -> dict[str, int]:
    """The git subprocesses of a write, of the read after it, and of the first read after a start."""

    client = running.client
    assert client.get("/api/applications").status_code == 200  # the page is open
    costs: dict[str, int] = {}
    spawns.clear()
    recorded = _post(client, f"https://boards.greenhouse.io/flat/jobs/{uuid.uuid4().hex}")
    assert recorded.status_code == 201, recorded.text
    costs["post"] = len(spawns)
    spawns.clear()
    rows = client.get("/api/applications").json()["applications"]
    costs["get_after_post"] = len(spawns)
    assert recorded.json()["event"]["event_id"] in {row["event_id"] for row in rows}
    _forget_everything()
    spawns.clear()
    again = client.get("/api/applications").json()["applications"]
    costs["first_get"] = len(spawns)
    assert _same(again, rows)
    return costs


def test_a_write_and_the_reads_spawn_the_same_at_4_events_and_at_300(scout, mixed, spawns: list[str]) -> None:
    for url in scout.urls[:3]:
        assert _post(scout.client, url).status_code == 201
    small = _costs(scout, spawns)
    large = _costs(mixed, spawns)
    assert len(mixed.client.get("/api/applications").json()["applications"]) > MIXED_EVENTS

    assert small == large, (small, large)
    assert large["post"] <= POST_SPAWNS_MAX, large
    assert large["get_after_post"] <= GET_AFTER_POST_SPAWNS_MAX, large
    assert large["first_get"] <= FIRST_GET_SPAWNS_MAX, large


def test_a_write_takes_no_snapshot_of_the_journal(mixed, monkeypatch: pytest.MonkeyPatch, spawns: list[str]) -> None:
    """Before: one snapshot of every record and every run, a write (1.9 s of 2.4 s at catalog size)."""

    taken: list[tuple[str, ...]] = []
    real = journal._capture_committed_snapshot

    def spying(root, project_id, gig_id, prefixes, *more):
        taken.append(tuple(prefixes))
        return real(root, project_id, gig_id, prefixes, *more)

    monkeypatch.setattr(journal, "_capture_committed_snapshot", spying)
    assert mixed.client.get("/api/applications").status_code == 200
    taken.clear()
    spawns.clear()
    assert _post(mixed.client, mixed.urls[0] + "?snapshot=none", "saved").status_code == 201
    assert taken == []
    assert not any(" log " in command and "--name-only" in command and ".." not in command for command in spawns), spawns
    assert mixed.client.get("/api/applications").status_code == 200
    assert taken == []


# --------------------------------------------------------------------------
# Never stale
# --------------------------------------------------------------------------


def test_a_write_is_in_the_very_next_read_every_time(scout) -> None:
    client = scout.client
    assert client.get("/api/applications").json()["applications"] == []
    linked, unlinked = scout.urls[0], "https://boards.greenhouse.io/nowhere/jobs/7"
    steps = [(linked, "saved"), (linked, "applied"), (unlinked, "applied"), (linked, "interview_scheduled"), (unlinked, "withdrawn"), (linked, "offer_received")]
    for count, (url, kind) in enumerate(steps, start=1):
        recorded = _post(client, url, kind)
        assert recorded.status_code == 201, recorded.text
        rows = client.get("/api/applications").json()["applications"]
        assert len(rows) == count
        mine = [row for row in rows if row["event_id"] == recorded.json()["event"]["event_id"]]
        assert len(mine) == 1 and mine[0]["event_kind"] == kind
        assert (mine[0]["linked_posting"] is not None) == (url == linked)
        assert mine[0]["job_state"] == recorded.json()["job_state"]
        assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))
    # The pipeline is checked against the events just written, not against a read from before them.
    refused = _post(client, linked, "applied")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "application_transition_refused"
    assert _post(client, unlinked, "interview_scheduled").status_code == 409
    assert len(client.get("/api/applications").json()["applications"]) == len(steps)


def test_a_write_by_another_process_is_in_the_next_read(scout, spawns: list[str]) -> None:
    """The CLI writes the journal without telling the server: the index head is behind the journal head."""

    client = scout.client
    assert _post(client, scout.urls[0]).status_code == 201
    assert len(client.get("/api/applications").json()["applications"]) == 1
    script = (
        "import sys, uuid\n"
        "from pathlib import Path\n"
        "from gigai.application_events import record_application\n"
        "from gigai.workpad import resolve_workpad\n"
        "resolved = resolve_workpad(home_root=Path(sys.argv[1]), requested_target=Path(sys.argv[2]), gig_id=None, allow_semantic_state=True)\n"
        "for url in sys.argv[3:]:\n"
        "    record_application(resolved=resolved, confirm=True, data={'operation_key': f'cli-{uuid.uuid4()}', 'external_ref': url,\n"
        "        'event_kind': 'applied', 'occurred_at': '2026-05-01T09:00:00Z', 'timezone': 'UTC'})\n"
    )
    outside = [scout.urls[1], "https://boards.greenhouse.io/nowhere/jobs/outside"]
    done = subprocess.run(
        [sys.executable, "-c", script, str(scout.home), str(scout.target), *outside],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(path for path in sys.path if path)}, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr

    spawns.clear()
    rows = client.get("/api/applications").json()["applications"]
    assert sorted(row["external_ref"] for row in rows) == sorted([scout.urls[0], *outside])
    assert {row["external_ref"]: row["linked_posting"] is not None for row in rows} == {scout.urls[0]: True, outside[0]: True, outside[1]: False}
    assert len(spawns) <= 3, spawns  # caught up from the two commits, not read again
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))
    # And this process's next write sees the other process's events.
    assert _post(client, outside[0]).status_code == 409


def test_a_journal_that_was_reset_is_read_again(scout) -> None:
    """The head is no longer a descendant of the kept one: nothing kept is trusted."""

    client = scout.client
    for url in scout.urls[:3]:
        assert _post(client, url).status_code == 201
    assert len(client.get("/api/applications").json()["applications"]) == 3
    subprocess.run(["git", "-C", str(scout.workpad), "reset", "--quiet", "--hard", "HEAD~1"], check=True)

    rows = client.get("/api/applications").json()["applications"]
    assert sorted(row["external_ref"] for row in rows) == sorted(scout.urls[:2])
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))
    # The job whose event is gone can be applied to again, and that is read too.
    assert _post(client, scout.urls[2]).status_code == 201
    assert len(client.get("/api/applications").json()["applications"]) == 3
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))


def test_an_event_removed_and_published_again_is_refused_as_before(scout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits made around the journal: a removed event leaves the rows; the same path published twice is no authority."""

    client = scout.client
    for url in scout.urls[:3]:
        assert _post(client, url).status_code == 201
    rows = client.get("/api/applications").json()["applications"]
    removed = sorted(f"records/applications/events/{row['event_id']}.json" for row in rows)[0]
    original = (scout.workpad / removed).read_bytes()
    git = ["git", "-C", str(scout.workpad)]
    subprocess.run([*git, "rm", "--quiet", removed], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "removed around the journal"], check=True)

    after = client.get("/api/applications")
    assert after.status_code == 200 and len(after.json()["applications"]) == 2
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))

    (scout.workpad / removed).write_bytes(original)
    subprocess.run([*git, "add", "--", removed], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "published again around the journal"], check=True)
    new = client.get("/api/applications")
    old = _get_without_the_kept_rows(client, monkeypatch)
    assert new.status_code == old.status_code != 200  # the path has two publishers
    assert application_rows.kept_application_rows(scout.resolved) is None


def test_a_finished_run_changes_the_links_of_the_rows(scout) -> None:
    """The acquired postings are kept until a commit touches a run's acquire output."""

    client = scout.client
    assert _post(client, scout.urls[0]).status_code == 201
    before = client.get("/api/applications").json()["applications"]
    digest = client.get("/api/config").json()["config_digest"]
    started = client.post("/api/run", json=_run_request(digest))
    assert started.status_code == 202, started.text
    assert _poll_succeeded(client, started.json()["run_id"])["status"] == "succeeded"

    after = client.get("/api/applications").json()["applications"]
    assert len(after) == len(before) == 1 and after[0]["linked_posting"] is not None
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))


# --------------------------------------------------------------------------
# The files are a cache
# --------------------------------------------------------------------------


def _three_events(scout) -> list[dict[str, object]]:
    for url, kind in ((scout.urls[0], "applied"), (scout.urls[1], "saved"), ("https://boards.greenhouse.io/nowhere/jobs/3", "applied")):
        assert _post(scout.client, url, kind).status_code == 201
    rows = scout.client.get("/api/applications").json()["applications"]
    assert len(rows) == 3 and _index_file(scout.resolved).is_file() and _links_file(scout.resolved).is_file()
    return rows


def test_deleted_files_are_built_again_from_the_journal(scout) -> None:
    rows = _three_events(scout)
    _forget_everything()
    _index_file(scout.resolved).unlink()
    _links_file(scout.resolved).unlink()

    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    assert _index_file(scout.resolved).is_file() and _links_file(scout.resolved).is_file()
    # Deleted under a running server (nothing forgotten): the kept events still answer, and the next write still works.
    _index_file(scout.resolved).unlink()
    _links_file(scout.resolved).unlink()
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    assert _post(scout.client, scout.urls[2]).status_code == 201
    assert len(scout.client.get("/api/applications").json()["applications"]) == 4
    _forget_everything()
    assert len(scout.client.get("/api/applications").json()["applications"]) == 4
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))


@pytest.mark.parametrize("damage", ["garbage", "empty", "another_schema", "another_gig", "unknown_head"])
def test_files_that_cannot_be_used_are_built_again(scout, damage: str) -> None:
    rows = _three_events(scout)
    _forget_everything()
    for path in (_index_file(scout.resolved), _links_file(scout.resolved)):
        if damage == "garbage":
            path.write_bytes(b"this is not a database" * 100)
        elif damage == "empty":
            path.write_bytes(b"")
        else:
            connection = sqlite3.connect(path)
            key, value = {
                "another_schema": ("schema", "some-older-index:0"),
                "another_gig": ("gig_id", "gig_00000000-0000-4000-8000-000000000000"),
                "unknown_head": ("head" if path == _index_file(scout.resolved) else "signature", "0" * 40),
            }[damage]
            connection.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
            connection.commit()
            connection.close()

    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))
    # Built again: the next start reads them without a snapshot.
    _forget_everything()
    assert event_index.committed_application_events(scout.resolved).head == journal.committed_head(
        workpad=scout.resolved.path, project_id=scout.resolved.project_id, gig_id=scout.resolved.gig_id
    )
    connection = sqlite3.connect(_index_file(scout.resolved))
    assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 3
    connection.close()


def test_a_file_that_holds_what_the_journal_does_not_is_never_served(scout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Other bytes, another blob id, one path too many, one too few: the tree at the head decides."""

    rows = _three_events(scout)
    index_file = _index_file(scout.resolved)
    for change in ("other_bytes", "another_blob", "one_too_many", "one_too_few"):
        _forget_everything()
        connection = sqlite3.connect(index_file)
        stored = connection.execute("SELECT path, blob_id, data, digest, size, mtime_ns, inode FROM artifacts ORDER BY path LIMIT 1").fetchone()
        path, blob_id, data = stored[0], stored[1], bytes(stored[2])
        assert len(blob_id) in (40, 64)
        if change == "other_bytes":
            forged = data.replace(b'"event_kind":"applied"', b'"event_kind":"offer_received"').replace(b'"event_kind":"saved"', b'"event_kind":"offer_received"')
            assert forged != data
            connection.execute("UPDATE artifacts SET data = ? WHERE path = ?", (forged, path))
        elif change == "another_blob":
            connection.execute("UPDATE artifacts SET blob_id = ? WHERE path = ?", ("0" * len(blob_id), path))
        elif change == "one_too_many":
            connection.execute(
                "INSERT INTO artifacts(path, blob_id, data, digest, size, mtime_ns, inode) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"records/applications/events/event_{uuid.uuid4()}.json", *stored[1:]),
            )
        else:
            connection.execute("DELETE FROM artifacts WHERE path = ?", (path,))
        connection.commit()
        connection.close()

        assert _same(scout.client.get("/api/applications").json()["applications"], rows), change
    # A working file that changed since it was proven: the file is not used, and the snapshot refuses it as before.
    working = scout.workpad / path
    original = working.read_bytes()
    forged = original.replace(b'"event_kind":"applied"', b'"event_kind":"offer_received"').replace(b'"event_kind":"saved"', b'"event_kind":"offer_received"')
    assert forged != original
    working.write_bytes(forged)
    try:
        _forget_everything()
        new = scout.client.get("/api/applications")
        old = _get_without_the_kept_rows(scout.client, monkeypatch)
        assert new.status_code == old.status_code != 200
        assert "offer_received" not in new.text
    finally:
        working.write_bytes(original)
    _forget_everything()
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    # A posting in the links file is only used with the acquire outputs it was built from.
    _forget_everything()
    connection = sqlite3.connect(_links_file(scout.resolved))
    connection.execute("UPDATE meta SET value = ? WHERE key = 'signature'", ("sha256:" + "f" * 64,))
    connection.execute("UPDATE postings SET posting = ?", (json.dumps({"title": "forged"}),))
    connection.commit()
    connection.close()
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)


def test_a_file_behind_the_journal_is_caught_up(scout, spawns: list[str]) -> None:
    """The file of an earlier head (a server that stopped; the CLI wrote since): only the commits since are read."""

    _three_events(scout)
    earlier = scout.workpad.parent / "earlier-index.sqlite"
    shutil.copyfile(_index_file(scout.resolved), earlier)
    assert _post(scout.client, scout.urls[2]).status_code == 201
    assert _post(scout.client, scout.urls[0], "interview_scheduled").status_code == 201
    rows = scout.client.get("/api/applications").json()["applications"]
    assert len(rows) == 5

    _forget_everything()
    shutil.copyfile(earlier, _index_file(scout.resolved))
    spawns.clear()
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    assert not any("show -z" in command for command in spawns), spawns  # no snapshot: the two commits since
    assert len(spawns) <= FIRST_GET_SPAWNS_MAX + 2, spawns
    # And the file is now at the head: the next start reads nothing more.
    _forget_everything()
    spawns.clear()
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)
    assert len(spawns) <= FIRST_GET_SPAWNS_MAX, spawns


def test_a_file_in_the_working_tree_that_is_not_committed_is_refused_as_before(scout, monkeypatch: pytest.MonkeyPatch) -> None:
    """The kept events never hide what a snapshot refuses: an event file nobody committed."""

    _three_events(scout)
    stray = scout.workpad / "records" / "applications" / "events" / f"event_{uuid.uuid4()}.json"
    stray.write_bytes(b"{}")
    try:
        _forget_everything()
        new = scout.client.get("/api/applications")
        old = _get_without_the_kept_rows(scout.client, monkeypatch)
        assert new.status_code == old.status_code != 200
    finally:
        stray.unlink()
    assert len(scout.client.get("/api/applications").json()["applications"]) == 3


# --------------------------------------------------------------------------
# Two writers at once
# --------------------------------------------------------------------------


def _in_parallel(calls) -> list[httpx.Response]:
    answers: list[httpx.Response | BaseException | None] = [None] * len(calls)
    barrier = threading.Barrier(len(calls))

    def run(position: int, call) -> None:
        try:
            barrier.wait(timeout=30)
            answers[position] = call()
        except BaseException as exc:  # noqa: BLE001 - reported by the caller's assertion
            answers[position] = exc

    threads = [threading.Thread(target=run, args=(position, call)) for position, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert all(isinstance(answer, httpx.Response) for answer in answers), answers
    return answers  # type: ignore[return-value]


def test_two_posts_at_once_stay_consistent(scout) -> None:
    base_url = scout.base_url
    assert scout.client.get("/api/applications").status_code == 200

    def post(url: str, kind: str = "applied"):
        def call() -> httpx.Response:
            with httpx.Client(base_url=base_url, timeout=120.0) as client:
                return _post(client, url, kind)

        return call

    def get() -> httpx.Response:
        with httpx.Client(base_url=base_url, timeout=120.0) as client:
            return client.get("/api/applications")

    # Two different jobs, five rounds, a reader beside each pair: every write lands, every read answers.
    expected: list[str] = []
    for round_number in range(5):
        pair = [scout.urls[round_number % len(scout.urls)] + f"?round={round_number}", f"https://boards.greenhouse.io/nowhere/jobs/{round_number}"]
        answers = _in_parallel([post(pair[0]), post(pair[1]), get])
        assert [answer.status_code for answer in answers] == [201, 201, 200], [answer.text for answer in answers]
        expected.extend(pair)
        rows = scout.client.get("/api/applications").json()["applications"]
        assert sorted(row["external_ref"] for row in rows) == sorted(expected)
    # The same job twice at once: the pipeline lets one through.
    same = _in_parallel([post(scout.urls[0]), post(scout.urls[0])])
    assert sorted(answer.status_code for answer in same) == [201, 409], [answer.text for answer in same]
    assert same[[answer.status_code for answer in same].index(409)].json()["error"]["code"] == "application_transition_refused"

    rows = scout.client.get("/api/applications").json()["applications"]
    assert len(rows) == 11 and len({row["event_id"] for row in rows}) == 11
    assert _same(_kept_rows(scout.resolved), _projection_rows(scout.resolved))
    # The journal agrees, read with nothing kept; so does the file.
    _forget_everything()
    snapshot = read_committed_snapshot(
        workpad=scout.resolved.path, project_id=scout.resolved.project_id, gig_id=scout.resolved.gig_id,
        prefixes=("records/applications/",),
    )
    assert len(snapshot.artifacts) == 11
    assert _same(scout.client.get("/api/applications").json()["applications"], rows)


# --------------------------------------------------------------------------
# What the index cannot answer takes the old path
# --------------------------------------------------------------------------


def test_an_operation_key_that_was_used_answers_from_its_receipt(scout) -> None:
    resolved = scout.resolved
    data = {"operation_key": "applications-scale-replay", "external_ref": scout.urls[0], "event_kind": "applied", "occurred_at": "2026-04-01T08:00:00Z", "timezone": "UTC"}
    first = record_application(resolved=resolved, data=dict(data), confirm=True)
    again = record_application(resolved=resolved, data=dict(data), confirm=True)
    assert first["status"] == "recorded" and again["status"] == "already_recorded"
    assert again["event"] == first["event"]  # the receipt's copy: the same event
    with pytest.raises(application_events.ApplicationEventError) as conflict:
        record_application(resolved=resolved, data={**data, "event_kind": "saved"}, confirm=True)
    assert conflict.value.code == "application_operation_conflict"
    assert len(scout.client.get("/api/applications").json()["applications"]) == 1


def test_a_correction_must_name_an_earlier_event_of_the_same_job(scout) -> None:
    resolved = scout.resolved
    base = {"event_kind": "applied", "occurred_at": "2026-04-01T08:00:00Z", "timezone": "UTC"}
    first = record_application(resolved=resolved, confirm=True, data={**base, "operation_key": "scale-a", "external_ref": scout.urls[0]})["event"]
    with pytest.raises(application_events.ApplicationEventError) as other_job:
        record_application(resolved=resolved, confirm=True, data={**base, "operation_key": "scale-b", "external_ref": scout.urls[1], "supersedes": first["event_id"]})
    assert other_job.value.code == "application_correction_invalid"
    with pytest.raises(application_events.ApplicationEventError) as taken:
        record_application(resolved=resolved, confirm=True, data={**base, "operation_key": "scale-c", "external_ref": scout.urls[1], "event_id": first["event_id"]})
    assert taken.value.code == "application_event_identity_conflict"
    corrected = record_application(resolved=resolved, confirm=True, data={**base, "operation_key": "scale-d", "external_ref": scout.urls[0], "event_kind": "withdrawn", "supersedes": first["event_id"]})
    assert corrected["status"] == "recorded"
    rows = {row["event_id"]: row for row in scout.client.get("/api/applications").json()["applications"]}
    assert rows[first["event_id"]]["current"] is False and rows[corrected["event"]["event_id"]]["current"] is True
    assert _same(_kept_rows(resolved), _projection_rows(resolved))


def test_an_event_that_names_a_discover_opportunity_takes_the_projection(scout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its row is checked against other record families: the kept rows do not answer for it."""

    resolved = scout.resolved
    assert _post(scout.client, scout.urls[0]).status_code == 201
    assert application_rows.kept_application_rows(resolved) is not None
    taken: list[tuple[str, ...]] = []
    real = journal._capture_committed_snapshot

    def spying(root, project_id, gig_id, prefixes, *more):
        taken.append(tuple(prefixes))
        return real(root, project_id, gig_id, prefixes, *more)

    monkeypatch.setattr(journal, "_capture_committed_snapshot", spying)
    with pytest.raises(application_events.ApplicationEventError):
        # No such opportunity was ever discovered: refused by the snapshot path, as before.
        record_application(resolved=resolved, confirm=True, data={
            "operation_key": "scale-opportunity", "opportunity_ref": "opportunity_" + "a" * 32, "event_kind": "applied",
            "occurred_at": "2026-04-01T08:00:00Z", "timezone": "UTC",
        })
    assert any("runs/" in prefixes and "records/" in prefixes for prefixes in taken), taken
    # One committed anyway (a legacy Gig): the read is the projection's, with or without the kept rows.
    event = _committed_opportunity_event(resolved)
    assert application_rows.kept_application_rows(resolved) is None
    new = scout.client.get("/api/applications")
    old = _get_without_the_kept_rows(scout.client, monkeypatch)
    assert new.status_code == old.status_code and new.content == old.content
    assert event["event_id"] in new.text or new.status_code != 200


def _committed_opportunity_event(resolved) -> dict[str, object]:
    """An ``opportunity_ref`` event, published the way ``record_application`` publishes (no resolver: a legacy Gig)."""

    made: dict[str, object] = {}
    real = application_events.validate_application_links

    def unresolved(event, **kwargs):
        made.update(event)
        return real(event, **{**kwargs, "opportunity_reader": None})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application_events, "validate_application_links", unresolved)
        result = record_application(resolved=resolved, confirm=True, data={
            "operation_key": f"scale-legacy-{uuid.uuid4()}", "opportunity_ref": "opportunity_" + "b" * 32, "event_kind": "applied",
            "occurred_at": "2026-04-02T08:00:00Z", "timezone": "UTC",
        })
    assert result["status"] == "recorded"
    return result["event"]


def test_an_invalid_committed_event_is_refused_the_way_it_was(scout, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = scout.resolved
    assert _post(scout.client, scout.urls[0]).status_code == 201
    path = f"records/applications/events/event_{uuid.uuid4()}.json"
    record_transition(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=f"handoff_{uuid.uuid4()}",
        transition="private_record_revised", body="An event that is not one.", artifacts=(JournalArtifact(path, b'{"event_id":"nope"}'),),
        allow_artifact_replacement=False,
    )

    with pytest.raises(event_index.ApplicationEventIndexUnavailable):
        event_index.committed_application_events(resolved)
    new_get = scout.client.get("/api/applications")
    new_post = _post(scout.client, scout.urls[1])
    with monkeypatch.context() as patch:
        patch.setattr(application_events, "_kept_prior_events", lambda *args, **kwargs: None)
        old_post = _post(scout.client, scout.urls[1])
    old_get = _get_without_the_kept_rows(scout.client, monkeypatch)
    assert (new_get.status_code, new_post.status_code) == (old_get.status_code, old_post.status_code)
    assert new_post.status_code != 201 and new_post.json() == old_post.json()
    assert new_get.status_code != 200


# --------------------------------------------------------------------------
# The journal's part
# --------------------------------------------------------------------------


def test_additions_are_exactly_what_the_commits_since_published(scout) -> None:
    resolved = scout.resolved
    where = dict(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
    family = "records/applications/"
    assert _post(scout.client, scout.urls[0]).status_code == 201
    old_head = journal.committed_head(**where)
    first, known = journal.read_committed_family(prefix=family, **where)
    assert known == set(first.artifacts) == set(read_committed_snapshot(prefixes=(family,), **where).artifacts) and len(known) == 1
    assert first.head == old_head
    assert read_committed_additions(old_head=old_head, new_head=old_head, prefix=family, known=known, **where) is None  # no commit between

    assert _post(scout.client, scout.urls[1]).status_code == 201
    middle = journal.committed_head(**where)
    assert _post(scout.client, scout.urls[2], "saved").status_code == 201
    new_head = journal.committed_head(**where)
    snapshot = read_committed_snapshot(prefixes=(family,), **where)

    added = read_committed_additions(old_head=old_head, new_head=new_head, prefix=family, known=known, **where)
    assert added is not None and len(added) == 2
    assert {path: data for path, (_blob_id, data) in added.items()} == {path: data for path, data in snapshot.artifacts.items() if path not in known}
    # Git's own ids, the same from the tree and from the blobs read.
    committed = journal.committed_blob_ids(head=new_head, prefix=family, **where)
    assert committed is not None and set(committed) == set(snapshot.artifacts)
    assert {path: blob_id for path, (blob_id, _data) in added.items()} == {path: committed[path] for path in added}
    hashed = subprocess.run(["git", "-C", str(scout.workpad), "hash-object", "--", *sorted(committed)], capture_output=True, text=True, check=True)
    assert hashed.stdout.split() == [committed[path] for path in sorted(committed)]
    # Each commit with its own paths, newest first; the union is what the kept reads of 0110-033 ask for.
    commits = straight_commits_between(resolved.path, old_head, new_head)
    assert commits is not None and [commit for commit, _names in commits] == [new_head, middle]
    assert all(sum(1 for name in names if name.startswith(family)) == 1 for _commit, names in commits)
    assert paths_committed_between(resolved.path, old_head, new_head) == frozenset().union(*(names for _commit, names in commits))
    # Commits that publish nothing into the family add nothing.
    assert read_committed_additions(old_head=old_head, new_head=new_head, prefix="records/scout-profiles/", known=(), **where) == {}

    # Not a plain addition: a path that already had a publisher; heads the wrong way round; an unknown head.
    assert read_committed_additions(old_head=old_head, new_head=new_head, prefix=family, known=known | set(added), **where) is None
    assert read_committed_additions(old_head=new_head, new_head=old_head, prefix=family, known=known, **where) is None
    assert read_committed_additions(old_head="0" * 40, new_head=new_head, prefix=family, known=known, **where) is None
    assert journal.committed_blob_ids(head="0" * 40, prefix=family, **where) is None
    # The working copy of a new event must be the committed bytes.
    changed = scout.workpad / sorted(added)[0]
    original = changed.read_bytes()
    changed.write_bytes(original + b" ")
    try:
        assert read_committed_additions(old_head=old_head, new_head=new_head, prefix=family, known=known, **where) is None
    finally:
        changed.write_bytes(original)
    assert read_committed_additions(old_head=old_head, new_head=new_head, prefix=family, known=known, **where) == added


def test_the_journals_own_workpad_check_is_not_asked_twice_in_a_read(scout, spawns: list[str]) -> None:
    """Inside a read, the repository check that passed covers the journal's (a subset of it)."""

    resolved = scout.resolved
    _forget_everything()
    with committed_read_cache():
        resolve_workpad(home_root=scout.home, requested_target=scout.target, gig_id=None, allow_semantic_state=True)
        spawns.clear()
        assert journal.committed_head(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
        assert spawns == []
    # Outside a read every check is asked, as before.
    spawns.clear()
    assert journal.committed_head(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
    # 0110-043: the layout marker's kept publisher is admitted in 2 subprocesses (it was a walk and 4 more);
    # the four ownership markers and the remote are asked as before.
    assert len(spawns) >= 7
    assert sum(" config --local --get " in call for call in spawns) == 4 and any(call.endswith(" remote") for call in spawns)
    # A changed ownership marker is refused by the journal's check inside a read, too.
    subprocess.run(["git", "-C", str(scout.workpad), "config", "--local", "gigai.gig-id", "gig_00000000-0000-4000-8000-000000000000"], check=True)
    try:
        time.sleep(0.01)
        with committed_read_cache(), pytest.raises(journal.JournalConflictError):
            journal.committed_head(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
    finally:
        subprocess.run(["git", "-C", str(scout.workpad), "config", "--local", "gigai.gig-id", resolved.gig_id], check=True)
