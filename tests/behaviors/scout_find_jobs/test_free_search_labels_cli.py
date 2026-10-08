"""FS1 (0.1.11.7): the free search's LABELS and its command, on a real (synthetic) Scout gig.

- a row is labelled, by job identity, with what the stores hold AS THEY ARE: the profiles whose list holds the
  posting, its assessment state, its application status; a posting no profile holds has no label;
- the labels are the same whether the index or the scan answered;
- ``gigai scout jobs search``: the page first, the total after it; ``--json`` is one object; ``--all``; a title with
  no match is a sentence and exit 0; a request for nothing is refused;
- WRITES NOTHING on a gig home: every file is byte-identical after the searches (the journal, the pipeline file,
  the stores), with the index built and without it. The snapshot is taken after one read of the home: SQLite's own
  ``-shm`` files beside a WAL database are made by a first open, by any reader.

Every posting, company and person is made up. No network, no real home.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from click.testing import CliRunner
import pytest

from gigai.application_events import record_application
from gigai.cli import cli
from gigai.scout import posting_search
from gigai.scout.find_jobs import free_search, search_index
from gigai.scout.find_jobs.free_search import SearchRequest
from gigai.workpad import committed_read_cache

from tests.support.posting_fixtures import SECOND_LABEL, TITLE_BOTH, TITLE_SECOND_ONLY, PostingsFixture, build_postings_fixture, job_url, lever_job

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

WATCHED, UNWATCHED = "acme-health", "quiet-harbor"
#: "now" for the searches: the real clock, because the command reads it. The postings are an hour or two old.
MOMENT = datetime.now(UTC)


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    # Three "Staff AI Engineer" (both profiles' lists) and two "Staff Engineer" (the second profile's) on a watched board.
    fixture.seed(
        WATCHED,
        [lever_job(WATCHED, n, title=TITLE_BOTH if n <= 3 else TITLE_SECOND_ONLY, created=MOMENT - timedelta(hours=n)) for n in range(1, 6)],
        seen_at=MOMENT - timedelta(minutes=30),
    )
    # A board nobody watches: stored, in no profile's list.
    fixture.seed(
        UNWATCHED, [lever_job(UNWATCHED, n, title=TITLE_BOTH, created=MOMENT - timedelta(hours=10 + n)) for n in (1, 2)],
        seen_at=MOMENT - timedelta(minutes=30), watch=False,
    )
    posting_search.search_postings(fixture.home_root, fixture.target, now=MOMENT)  # the profiles' lists, as the Jobs page builds them
    yield fixture
    search_index.close(fixture.home_root)


def _record(fx: PostingsFixture, n: int, kind: str, occurred_at: str) -> None:
    with committed_read_cache():
        result = record_application(
            resolved=fx.base.gig.resolved,
            data={
                "external_ref": job_url(WATCHED, n), "event_kind": kind, "occurred_at": occurred_at, "timezone": "UTC",
                "operation_key": f"free-search-{uuid.uuid4()}",
            },
            confirm=True,
        )
    assert result["status"] == "recorded"


def _search(fx: PostingsFixture, title: str, **more: object) -> dict:
    request = SearchRequest.typed(title, count=True, **more)  # type: ignore[arg-type]
    return free_search.to_json(free_search.search(fx.home_root, request, target=fx.target, now=MOMENT))


def _by_job(response: dict) -> dict[str, dict]:
    return {row["job_identity"]: row for row in response["postings"]["rows"]}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_rows_carry_the_profiles_the_assessment_and_the_application(fx: PostingsFixture) -> None:
    assessed = posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url(WATCHED, 1)], approve=True, now=MOMENT)
    assert assessed["assessed"]["assessed"] == 1  # type: ignore[index]
    _record(fx, 2, "applied", "2026-10-06T10:00:00Z")
    _record(fx, 2, "interview_scheduled", "2026-10-07T10:00:00Z")
    listed = _by_job(posting_search.search_postings(fx.home_root, fx.target, now=MOMENT, jobs=[job_url(WATCHED, 1)]))

    response = _search(fx, "staff ai engineer")
    assert response["source"] == "scan" and response["labels_read"] is True, "no index was built on this home: the scan answered"
    rows = _by_job(response)
    assert sorted(rows) == sorted([job_url(WATCHED, 1), job_url(WATCHED, 2), job_url(WATCHED, 3), job_url(UNWATCHED, 1), job_url(UNWATCHED, 2)])
    both = {fx.default_profile_id, fx.second_profile_id}
    # In both profiles' lists; the first one is assessed, with the state its list shows.
    first = rows[job_url(WATCHED, 1)]
    assert {item["profile_id"] for item in first["profiles"]} == both
    shown = listed[job_url(WATCHED, 1)]
    assert first["assessment"] == {"state": shown["state"], "profile_id": shown["profile_id"], "assessed_at": shown["assessment"]["assessed_at"]}
    assert shown["state"] != "not_assessed" and first["application"] is None
    # The application is the latest status, as the Jobs list says it.
    second = rows[job_url(WATCHED, 2)]
    assert second["application"] == {"status": "interview_scheduled", "since": "2026-10-07T10:00:00Z"} and second["assessment"] is None
    assert {item["profile_id"] for item in second["profiles"]} == both
    third = rows[job_url(WATCHED, 3)]
    assert third["assessment"] is None and third["application"] is None
    assert all(item["state"] == "not_assessed" for item in third["profiles"])
    # A stored posting no profile's list holds: found, no label.
    for n in (1, 2):
        lone = rows[job_url(UNWATCHED, n)]
        assert lone["profiles"] == [] and lone["assessment"] is None and lone["application"] is None
        assert lone["company_key"] == f"lever:{UNWATCHED}"
    # The profiles the rows name, the default first; nothing of the user's text is in a row.
    assert [item["profile_id"] for item in response["profiles"]] == [fx.default_profile_id, fx.second_profile_id]
    assert response["profiles"][0]["is_default"] is True and response["profiles"][1]["label"] == SECOND_LABEL
    # "Staff Engineer" by the strict rule: the two the second profile alone holds, and the three "Staff AI Engineer".
    staff = _by_job(_search(fx, "staff engineer"))
    assert {item["profile_id"] for item in staff[job_url(WATCHED, 4)]["profiles"]} == {fx.second_profile_id}
    # No rank, no fit number.
    assert not {"rank_score", "fit", "score", "score_text"} & set(first)


def test_a_posting_assessed_by_its_address_is_labelled_from_the_assessment_store(fx: PostingsFixture) -> None:
    """A job no profile's list holds as assessed (the design's 21 jobs assessed by address) still says it is assessed."""

    from gigai.scout.pipeline.store import pipeline_path

    posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url(WATCHED, 3)], approve=True, now=MOMENT)
    # The read model's rows of that job are gone (as for a posting that left every profile's list): the files remain.
    conn = sqlite3.connect(pipeline_path(fx.home_root, fx.target))
    conn.execute("DELETE FROM posting WHERE job = ?", (job_url(WATCHED, 3),))
    conn.commit()
    conn.close()
    row = _by_job(_search(fx, "staff ai engineer"))[job_url(WATCHED, 3)]
    assert row["profiles"] == []
    assert row["assessment"] is not None and row["assessment"]["state"] != "not_assessed"
    assert row["assessment"]["profile_id"] in {fx.default_profile_id, fx.second_profile_id} and row["assessment"]["assessed_at"]


def test_the_index_and_the_scan_give_the_same_labelled_rows(fx: PostingsFixture) -> None:
    posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url(WATCHED, 1)], approve=True, now=MOMENT)
    _record(fx, 2, "applied", "2026-10-06T10:00:00Z")
    by_scan = {title: _search(fx, title) for title in ("staff ai engineer", "staff engineer")}
    by_scan_all = _search(fx, "staff engineer", show_all=True, company="quiet harbor")
    assert all(response["source"] == "scan" and response["index"]["reason"] == "missing" for response in by_scan.values())
    assert search_index.rebuild_from_index(fx.home_root).available
    for title, expected in by_scan.items():
        by_index = _search(fx, title)
        assert by_index["source"] == "index" and by_index["index"] == {"used": True, "reason": None}
        assert by_index["postings"] == expected["postings"] and by_index["counts"] == expected["counts"], title
        assert by_index["profiles"] == expected["profiles"] and by_index["filters"] == expected["filters"]
    assert _search(fx, "staff engineer", show_all=True, company="quiet harbor")["postings"] == by_scan_all["postings"]
    assert len(by_scan_all["postings"]["rows"]) == 2


@pytest.mark.parametrize("indexed", [False, True], ids=["scan", "index"])
def test_a_search_leaves_a_gig_home_byte_identical(fx: PostingsFixture, indexed: bool) -> None:
    posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url(WATCHED, 1)], approve=True, now=MOMENT)
    _record(fx, 2, "applied", "2026-10-06T10:00:00Z")
    if indexed:
        assert search_index.rebuild_from_index(fx.home_root).available
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]

    def searches() -> list[str]:
        printed = []
        for args in (["staff ai engineer"], ["staff engineer", "--all"], ["staff ai engineer, staff engineer", "--json"],
                     ["staff engineer", "--company", "acme", "--location", "remote", "--limit", "2", "--offset", "1"], ["no such title"]):
            result = CliRunner().invoke(cli, ["scout", "jobs", "search", *args, *home])
            assert result.exit_code == 0, result.output
            # The JSON says when it was read (checked_at, the window's edge): compared by its rows, counts and source.
            printed.append(json.dumps([json.loads(result.output)[key] for key in ("postings", "counts", "source")]) if "--json" in args else result.output)
        for title in ("staff ai engineer", "staff engineer"):
            _search(fx, title)
            _search(fx, title, show_all=True, include_removed=True)
        return printed

    first = searches()  # one read of the home first: a first open makes SQLite's -shm beside a WAL file
    assert json.loads(first[2])[2] == ("index" if indexed else "scan")
    before = _snapshot(fx.home_root)
    target_before = _snapshot(fx.target)
    assert searches() == first
    after = _snapshot(fx.home_root)
    assert sorted(after) == sorted(before), "no file was created or removed"
    assert [name for name in before if after[name] != before[name]] == [], "no file changed"
    assert _snapshot(fx.target) == target_before
    assert not (fx.home_root / "cache" / "scout" / "companies" / "last-search.json").exists()
    assert search_index.search_index_path(fx.home_root).exists() is indexed, "a search never builds the index"


def test_the_first_search_of_a_home_changes_no_record(fx: PostingsFixture) -> None:
    """The stricter form, before any read: everything but SQLite's own sidecar files is byte-identical."""

    def records(root: Path) -> dict[str, bytes]:
        return {name: data for name, data in _snapshot(root).items() if not name.endswith(("-shm", "-wal"))}

    before = records(fx.home_root)
    result = CliRunner().invoke(cli, ["scout", "jobs", "search", "staff ai engineer", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert result.exit_code == 0, result.output
    assert records(fx.home_root) == before


def test_the_command_prints_the_page_then_the_total(fx: PostingsFixture) -> None:
    _record(fx, 2, "applied", "2026-10-06T10:00:00Z")
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]
    assert search_index.rebuild_from_index(fx.home_root).available
    search_index.close(fx.home_root)

    result = CliRunner().invoke(cli, ["scout", "jobs", "search", "Staff AI Engineer", *home])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith('Showing 1-5, newest first: "Staff AI Engineer" (') and "last" in lines[0]
    rows = [line for line in lines if line.startswith("  2")]
    assert len(rows) == 5 and all(TITLE_BOTH in line for line in rows)
    assert any("[in: " in line and SECOND_LABEL in line and "[applied]" in line for line in rows)
    assert job_url(UNWATCHED, 1) in result.output
    # The page comes first, the total after it.
    total = lines.index("5 postings match.")
    assert total > max(lines.index(line) for line in rows)
    assert lines[total + 1] == "Not ranked. Save as a profile to rank."
    assert "The search index did not answer" not in result.output

    printed = CliRunner().invoke(cli, ["scout", "jobs", "search", "Staff AI Engineer", "--json", *home])
    assert printed.exit_code == 0, printed.output
    payload = json.loads(printed.output)
    assert payload["schema_version"] == "scout-free-search:1" and payload["source"] == "index"
    assert payload["counts"] == {"shown": 5, "more": False, "total": 5, "total_all": 5, "hidden": 0}
    assert [row["title"] for row in payload["postings"]["rows"]] == [TITLE_BOTH] * 5
    posted = [row["posted"] for row in payload["postings"]["rows"]]
    assert posted == sorted(posted, reverse=True), "newest posted first"

    paged = json.loads(CliRunner().invoke(cli, ["scout", "jobs", "search", "staff engineer", "--all", "--limit", "2", "--offset", "2", "--json", *home]).output)
    assert paged["query"]["all"] is True and paged["filters"] is None and paged["counts"]["shown"] == 2 and paged["counts"]["more"] is True
    assert paged["counts"]["total"] == 7

    words = CliRunner().invoke(cli, ["scout", "jobs", "search", "staff engineer", "--company", "quiet", "--location", "remote", *home])
    assert words.exit_code == 0 and "Showing 1-2" in words.output and "company quiet" in words.output and "2 postings match." in words.output
    # Whole words: "qui" is no word of "Quiet Harbor".
    part = CliRunner().invoke(cli, ["scout", "jobs", "search", "staff engineer", "--company", "qui", *home])
    assert part.exit_code == 0 and part.output.splitlines()[0].startswith("No stored posting matches")


def test_a_title_with_no_match_is_a_sentence_and_exit_0(fx: PostingsFixture) -> None:
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]
    result = CliRunner().invoke(cli, ["scout", "jobs", "search", "underwater basket weaver", *home])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    # No index on this home: the command says the scan ran, then the sentence.
    assert lines[0].startswith("The search index did not answer (missing)") and "gigai scout sources update" in lines[0]
    assert lines[1].startswith('No stored posting matches "underwater basket weaver" (')
    assert "Not ranked" not in result.output
    printed = json.loads(CliRunner().invoke(cli, ["scout", "jobs", "search", "underwater basket weaver", "--json", *home]).output)
    assert printed["postings"]["rows"] == [] and printed["counts"]["total"] == 0


def test_a_request_for_nothing_is_refused(fx: PostingsFixture) -> None:
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]
    refused = CliRunner().invoke(cli, ["scout", "jobs", "search", *home])
    assert refused.exit_code != 0 and "type a title" in refused.output
    as_json = CliRunner().invoke(cli, ["scout", "jobs", "search", "of the", "--json", *home])
    assert as_json.exit_code == 1 and json.loads(as_json.output)["error"]["code"] == "invalid_value"
    assert "WHOLE word" in CliRunner().invoke(cli, ["scout", "jobs", "search", "--help"]).output
