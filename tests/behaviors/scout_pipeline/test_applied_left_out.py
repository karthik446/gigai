"""0.1.11.5 packet AP: a posting you ALREADY APPLIED to is left out of the Jobs list unless you ask. Synthetic only.

Like a weak fit (``posting_search._hidden``): a posting with an application (applied and every later application
state: interview scheduled, offer received, rejected, withdrawn) is left out of

- the default list and its counts (``counts.matched``: "Showing 1-N of M"; ``counts.by_state.not_assessed``:
  "N not assessed" and the pool of "Assess top 50");
- every assess batch a FILTER selects ("Assess these" / "Assess all" without named postings, `gigai scout new`);

unless the ``applied`` state is asked for (then only those are listed) or the posting is NAMED (its URL).
``counts.applied`` is the chip's number: how many the other filters select, listed or not. A posting with no
application is untouched. The application events are read ONCE per request, whatever the size of the list.
"""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from click.testing import CliRunner
import pytest

from gigai.application_events import record_application
from gigai.cli import cli
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs import job_state
from gigai.workpad import committed_read_cache

from tests.support.fit_fixtures import weak_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, freeze_scout_new_clock, job_url, lever_job

SLUG = "acme-health"
APPLIED, REJECTED, WITHDRAWN = 1, 2, 3  # the postings with an application; 4 and 5 have none
LEFT_OUT = sorted(job_url(SLUG, n) for n in (APPLIED, REJECTED, WITHDRAWN))
LISTED = sorted(job_url(SLUG, n) for n in (4, 5))
LINE = "3 you already applied to are left out: gigai scout jobs list --state applied"


def _record(fx: PostingsFixture, n: int, kind: str, occurred_at: str, *, url: str | None = None) -> None:
    with committed_read_cache():
        result = record_application(
            resolved=fx.base.gig.resolved,
            data={
                "external_ref": url or job_url(SLUG, n), "event_kind": kind, "occurred_at": occurred_at, "timezone": "UTC",
                "operation_key": f"applied-left-out-{uuid.uuid4()}",
            },
            confirm=True,
        )
    assert result["status"] == "recorded"


def _apply(fx: PostingsFixture) -> None:
    """One applied, one applied then rejected, one applied, interviewed and withdrawn: all three count as applied."""

    _record(fx, APPLIED, "applied", "2026-10-01T10:00:00Z")
    _record(fx, REJECTED, "applied", "2026-10-01T11:00:00Z")
    _record(fx, REJECTED, "rejected", "2026-10-02T11:00:00Z")
    _record(fx, WITHDRAWN, "applied", "2026-10-01T12:00:00Z")
    _record(fx, WITHDRAWN, "interview_scheduled", "2026-10-02T12:00:00Z")
    _record(fx, WITHDRAWN, "withdrawn", "2026-10-03T12:00:00Z")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed(SLUG, [lever_job(SLUG, n) for n in range(1, 6)], seen_at=days_ago(1))
    return fixture


def _search(fx: PostingsFixture, **more: object) -> dict:
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW, **more)  # type: ignore[arg-type]


def _these(fx: PostingsFixture, **more: object) -> dict:
    return posting_search.assess_these(fx.home_root, fx.target, now=NOW, **more)  # type: ignore[arg-type]


def _jobs(response: dict) -> list[str]:
    return sorted(row["job_identity"] for row in response["postings"]["rows"])


def _batches(monkeypatch: pytest.MonkeyPatch, module: object) -> list[list[str]]:
    """Every batch ``module`` hands to the assess path, as the job identities it was asked to assess."""

    seen: list[list[str]] = []
    real = module._assess  # type: ignore[attr-defined]

    def watched(pairs, *args: object, **kwargs: object):
        seen.append(sorted(job for job, _owner in pairs))
        return real(pairs, *args, **kwargs)

    monkeypatch.setattr(module, "_assess", watched)
    return seen


def test_the_default_list_and_its_counts_leave_out_every_applied_posting(fx: PostingsFixture) -> None:
    before = _search(fx)
    assert len(_jobs(before)) == 5 and before["counts"]["applied"] == 0
    _apply(fx)
    listed = _search(fx)
    assert _jobs(listed) == LISTED, "applied, rejected and withdrawn are all left out"
    counts = listed["counts"]
    assert (counts["matched"], counts["shown"]) == (2, 2), "Showing 1-N of M counts what is listed"
    assert counts["by_state"] == {"not_assessed": 2}, "'N not assessed' (the pool of 'Assess top 50') does not count them"
    assert counts["applied"] == 3, "the chip's number: they exist"
    # A posting with no application is untouched: the same rows as before anything was applied to.
    assert [row for row in before["postings"]["rows"] if row["job_identity"] in LISTED] == listed["postings"]["rows"]
    assert all(row["application"] is None for row in listed["postings"]["rows"])


def test_the_applied_filter_lists_only_them_and_the_chip_number_stays(fx: PostingsFixture) -> None:
    _apply(fx)
    only = _search(fx, states=["applied"])
    assert _jobs(only) == LEFT_OUT and only["counts"]["matched"] == 3 and only["counts"]["applied"] == 3
    statuses = {row["job_identity"]: row["application"]["status"] for row in only["postings"]["rows"]}
    assert statuses == {job_url(SLUG, APPLIED): "applied", job_url(SLUG, REJECTED): "rejected", job_url(SLUG, WITHDRAWN): "withdrawn"}
    # Another state filter alone never lists them; beside "applied" it adds its own postings.
    assert _jobs(_search(fx, states=["not_assessed"])) == LISTED
    assert _jobs(_search(fx, states=["applied", "not_assessed"])) == sorted(LEFT_OUT + LISTED)
    # The other filters narrow the chip's number like the list (here: words no posting has).
    assert _search(fx, query="zzzz-no-such-word")["counts"]["applied"] == 0


def test_an_assess_batch_a_filter_selects_never_holds_an_applied_posting(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(fx)
    for filters in ({}, {"states": ["not_assessed"]}, {"window": "7d"}):
        ask = _these(fx, **filters)
        assert ask["status"] == "ask", filters
        assert (ask["question"]["selected"], ask["question"]["to_assess"], ask["question"]["batch"]) == (2, 2, 2), filters
        assert ask["question"]["text"].startswith("Assess 2 postings"), filters
    seen = _batches(monkeypatch, posting_search)
    done = _these(fx, approve=True)
    assert done["status"] == "assessed" and done["assessed"]["requested"] == 2
    assert seen == [LISTED], "the batch is the two postings with no application"
    assert _jobs(done) == LISTED


def test_a_named_applied_posting_is_still_assessed(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(fx)
    named = [job_url(SLUG, APPLIED), job_url(SLUG, WITHDRAWN)]
    ask = _these(fx, jobs=named)
    assert ask["status"] == "ask" and ask["not_found"] == []
    assert (ask["question"]["selected"], ask["question"]["to_assess"]) == (2, 2)
    seen = _batches(monkeypatch, posting_search)
    done = _these(fx, jobs=named, approve=True)
    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 2 and seen == [sorted(named)]
    assert _jobs(done) == sorted(named)
    by_cli = CliRunner().invoke(
        cli, ["scout", "jobs", "assess", job_url(SLUG, REJECTED), "--home", str(fx.home_root), "--target", str(fx.target), "--json"]
    )
    assert by_cli.exit_code == 0, by_cli.output
    asked = json.loads(by_cli.output)
    assert asked["status"] == "ask" and asked["question"]["to_assess"] == 1 and asked["not_found"] == []
    # Assessed or not, it stays out of the default list: only the filter (or its URL) shows it.
    assert _jobs(_search(fx)) == LISTED and _search(fx)["counts"]["applied"] == 3


def test_jobs_list_says_how_many_are_left_out_and_how_to_show_them(fx: PostingsFixture) -> None:
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]
    plain = CliRunner().invoke(cli, ["scout", "jobs", "list", *home])
    assert plain.exit_code == 0 and "left out" not in plain.output, "no application: no line"
    _apply(fx)
    text = CliRunner().invoke(cli, ["scout", "jobs", "list", *home])
    assert text.exit_code == 0, text.output
    lines = text.output.splitlines()
    assert lines[0].startswith("2 posting(s) match") and lines[1] == LINE
    assert all(job not in text.output for job in LEFT_OUT) and all(job in text.output for job in LISTED)
    printed = CliRunner().invoke(cli, ["scout", "jobs", "list", *home, "--json"])
    assert printed.exit_code == 0, printed.output
    counts = json.loads(printed.output)["counts"]
    assert (counts["matched"], counts["applied"]) == (2, 3)
    asked = CliRunner().invoke(cli, ["scout", "jobs", "list", "--state", "applied", *home])
    assert asked.exit_code == 0 and asked.output.splitlines()[0].startswith("3 posting(s) match")
    assert "left out" not in asked.output and all(job in asked.output for job in LEFT_OUT)
    one = posting_search.applied_left_out_line(1, {"states": []})
    assert one == "1 you already applied to is left out: gigai scout jobs list --state applied"


def test_scout_new_leaves_them_out_of_what_is_new_and_of_its_batch(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_scout_new_clock(monkeypatch)  # the command reads the clock: the postings are seeded relative to NOW
    def new(**more: object) -> dict:
        return scout_new.scout_new(fx.home_root, fx.target, now=NOW, peek=True, **more)  # type: ignore[arg-type]

    before = new()
    assert (before["counts"]["new"], before["counts"]["to_assess"], before["counts"]["applied"]) == (5, 5, 0)
    assert "left out" not in before["message"]
    _apply(fx)
    ask = new()
    assert ask["status"] == "ask"
    assert (ask["counts"]["new"], ask["counts"]["to_assess"], ask["counts"]["shown"], ask["counts"]["applied"]) == (2, 2, 2, 3)
    assert _jobs(ask) == LISTED
    assert ask["message"].startswith("2 new postings since ")
    assert ask["message"].endswith("3 new postings you already applied to are left out: gigai scout jobs list --state applied")
    assert ask["question"]["text"].startswith("2 new postings ("), ask["question"]["text"]
    text = CliRunner().invoke(cli, fx.cli("--peek", "--no-assess"))
    assert text.exit_code == 0 and "3 new postings you already applied to are left out" in text.output
    printed = CliRunner().invoke(cli, [*fx.cli("--peek", "--no-assess"), "--json"])
    assert printed.exit_code == 0 and json.loads(printed.output)["counts"]["applied"] == 3
    seen = _batches(monkeypatch, scout_new)
    done = new(assess=True)
    assert done["assessed"]["requested"] == 2 and seen == [LISTED], "the yes assesses only the two with no application"
    assert _jobs(done) == LISTED and done["counts"]["applied"] == 3


def test_the_events_are_read_once_for_a_list_of_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed(SLUG, [lever_job(SLUG, n) for n in range(1, 106)], seen_at=days_ago(1))
    for n in range(1, 6):
        _record(fixture, n, "applied", f"2026-10-0{n}T10:00:00Z")
    reads: list[int] = []
    real = job_state.read_application_events
    monkeypatch.setattr(job_state, "read_application_events", lambda resolved: reads.append(1) or real(resolved))
    listed = _search(fixture, limit=100)
    assert (listed["counts"]["matched"], listed["counts"]["shown"], listed["counts"]["applied"]) == (100, 100, 5)
    assert len(reads) == 1, "one read of the events for a list of 100 rows"
    reads.clear()
    assert len(_search(fixture, limit=100, states=["applied"])["postings"]["rows"]) == 5 and len(reads) == 1
    reads.clear()
    ask = _these(fixture)
    assert ask["question"]["to_assess"] == 100 and ask["question"]["batch"] == 50 and len(reads) == 1
    reads.clear()
    new = scout_new.scout_new(fixture.home_root, fixture.target, now=NOW, peek=True)
    assert (new["counts"]["new"], new["counts"]["applied"]) == (100, 5) and len(reads) == 1, "and one for `scout new`"


def test_an_applied_weak_fit_is_listed_by_the_applied_filter_and_not_counted_as_a_weak_fit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    weak = weak_fixture(fixture, monkeypatch)["weak"]
    before = _search(fixture)["counts"]
    assert (before["weak_fit"], before["applied"], before["matched"]) == (1, 0, 3)
    assert _jobs(_search(fixture, states=["weak_fit"])) == [weak]
    _record(fixture, 0, "applied", "2026-10-01T10:00:00Z", url=weak)
    counts = _search(fixture)["counts"]
    assert (counts["weak_fit"], counts["applied"], counts["matched"]) == (0, 1, 3), "its chip is 'Applied' now: each chip's number is what it lists"
    assert _jobs(_search(fixture, states=["weak_fit"])) == []
    listed = _search(fixture, states=["applied"])
    assert _jobs(listed) == [weak] and listed["postings"]["rows"][0]["state"] == "weak_fit", "listed with its own state untouched"
