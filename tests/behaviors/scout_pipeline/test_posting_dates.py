"""0110-10-14: a posting's date is in the Jobs rows, the one-job read and the terminal lines, each under its own name.

Two dates are stored for a posting and they are not the same thing:

- ``published_at`` is the BOARD's own date, the one the 7 / 30 days window judges: the day the posting went up
  (Greenhouse's ``first_published``, Lever's ``createdAt``, Ashby's ``publishedAt``). ``published_kind`` says what
  the date is (``posted``; ``updated`` only for a board kind nobody listed), so an update is never shown as "posted".
- ``updated_at`` is the board's last change to the posting, when it gives one (Greenhouse's ``updated_at``). It is
  never the posted date: a Greenhouse posting with no ``first_published`` has no posted date at all.
- ``first_seen`` is when Scout first stored the posting (the update that first listed it): what "new since" judges.
  It is also served as ``first_seen_at``.

The END outcome, on a synthetic home (no request leaves the process): four postings, one of each kind, read through
``GET /api/postings`` (``search_postings``), ``gigai scout jobs list``, ``gigai scout new`` and ``GET /api/jobs?url=``.
Before the fix no row carried ``published_at`` and no line of text said a date.

The correction (0.1.10.11): Greenhouse's posted date used to be its list's ``updated_at``, so a posting up for two
months and edited three days ago was "3 days old" to the 7 / 30 days filters and to the profile's "posted within"
cutoff. The filters, the cutoff and the "newest posted" order (``sort=newest_posted``) judge by the posted date.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings, scout_new
from gigai.scout.find_jobs import ats_board_clients, company_names
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import ATSProvider, normalize_url
from gigai.scout.find_jobs.watchlist import add_company_from_url

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_GH_TOKEN = "harborgrid"
_GH_URL = "https://www.harborgrid-example.test/jobs?gh_jid=4411"
_GH_UNDATED_URL = "https://www.harborgrid-example.test/jobs?gh_jid=4412"
_GH_OLD_URL = "https://www.harborgrid-example.test/jobs?gh_jid=4413"
#: Seen by Scout a day ago; the boards' own dates are older.
_SEEN = days_ago(1)
_POSTED = days_ago(10)  # Lever: createdAt
_GH_POSTED = days_ago(40)  # Greenhouse: first_published
_GH_OLD = days_ago(70)  # Greenhouse: first_published, before the profile's "posted within" (60 days)
_UPDATED = days_ago(3)  # Greenhouse: updated_at, the posting's last change


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _seed(fx: PostingsFixture) -> None:
    dated = lever_job("acme", 1, created=_POSTED)
    undated = lever_job("acme", 2)
    del undated["createdAt"]  # a board that gives no date
    fx.seed("acme", [dated, undated], seen_at=_SEEN)

    def iso(moment: datetime) -> str:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def greenhouse_job(job_id: int, url: str, first_published: datetime | None) -> dict[str, object]:
        return {
            "id": job_id, "title": TITLE_BOTH, "absolute_url": url, "location": {"name": "Remote - United States"},
            "updated_at": iso(_UPDATED), "first_published": None if first_published is None else iso(first_published),
            "company_name": "Harbor Grid", "content": f"<p>Harbor Grid is hiring Staff AI Engineer {job_id} for its Python services.</p>",
        }

    # Three Greenhouse postings, all edited three days ago: one up for 40 days, one the board gives no first day for,
    # one up for 70 days.
    jobs = {"jobs": [greenhouse_job(4411, _GH_URL, _GH_POSTED), greenhouse_job(4412, _GH_UNDATED_URL, None), greenhouse_job(4413, _GH_OLD_URL, _GH_OLD)]}
    add_company_from_url(f"https://boards.greenhouse.io/{_GH_TOKEN}", fx.home_root, fx.target)
    fx.cache.store("greenhouse", board_list_url("greenhouse", _GH_TOKEN), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="greenhouse", slug=_GH_TOKEN, company="Harbor Grid", observed_at=index_stamp(_SEEN))


def _rows(fx: PostingsFixture) -> dict[str, dict[str, object]]:
    response = posting_search.search_postings(fx.home_root, fx.target, now=NOW)
    scout_new.check_response(response)
    return {row["job_identity"]: row for row in response["postings"]["rows"]}  # type: ignore[index,union-attr]


def test_a_jobs_row_carries_the_boards_date_what_it_means_and_when_scout_first_saw_the_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    _seed(fx)
    seen = postings.stamp(index_stamp(_SEEN))

    rows = _rows(fx)
    dated, undated, greenhouse = rows[job_url("acme", 1)], rows[job_url("acme", 2)], rows[normalize_url(_GH_URL)]
    greenhouse_undated = rows[normalize_url(_GH_UNDATED_URL)]
    updated = postings.stamp(index_stamp(_UPDATED))

    # Lever and Greenhouse: the day the posting went up. No board date: none, and no kind.
    assert (dated["published_at"], dated["published_kind"]) == (postings.stamp(index_stamp(_POSTED)), "posted")
    assert (greenhouse["published_at"], greenhouse["published_kind"]) == (postings.stamp(index_stamp(_GH_POSTED)), "posted")
    assert (undated["published_at"], undated["published_kind"]) == (None, None)
    # The board's last change is its own value beside the posted date, and never stands in for one: the Greenhouse
    # posting with no first day has no posted date. Lever's list gives no last change.
    assert greenhouse["updated_at"] == updated and greenhouse["updated_at"] > greenhouse["published_at"]
    assert (greenhouse_undated["published_at"], greenhouse_undated["published_kind"], greenhouse_undated["updated_at"]) == (None, None, updated)
    assert dated["updated_at"] is None and undated["updated_at"] is None
    # When Scout first stored it: the same for all, under the clear name and the one it always had.
    for row in (dated, undated, greenhouse, greenhouse_undated):
        assert row["first_seen_at"] == row["first_seen"] == seen
    # The two dates are different facts: the board's is never the day Scout saw the posting.
    assert dated["published_at"] != dated["first_seen_at"]

    # One job, read by its URL: the same three facts, on the posting and on its grid row.
    job = _Routes()._job_aggregate(normalize_url(_GH_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None
    job = company_names.with_company_names(job, fx.home_root)
    for shown in (job["posting"], job["index_posting"]):  # type: ignore[index]
        assert (shown["published_at"], shown["published_kind"], shown["first_seen_at"]) == (greenhouse["published_at"], "posted", seen)
        assert shown["updated_at"] == updated

    # The terminal says the date each row has, with its own word: never an update or a first sighting as "posted".
    for command in (["scout", "jobs", "list"], ["scout", "new", "--no-assess"]):
        printed = CliRunner().invoke(cli, [*command, "--home", str(fx.home_root), "--target", str(fx.target)])
        assert printed.exit_code == 0, printed.output
        text = " ".join(printed.output.split())
        assert f"posted {_POSTED:%Y-%m-%d}" in text, printed.output
        assert f"posted {_GH_POSTED:%Y-%m-%d}" in text, printed.output
        assert f"first seen {_SEEN:%Y-%m-%d}" in text, printed.output
        # A posting's last change is never its one date in the terminal: the Greenhouse posting says when it went up.
        assert text.count("posted 20") == 2 and text.count("updated 20") == 0 and text.count("first seen 20") == 2, printed.output


def test_the_filters_the_cutoff_and_the_newest_posted_order_judge_by_the_posted_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's posting: up for weeks, edited three days ago. It is not "3 days old" to anything."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    _seed(fx)
    lever, lever_undated = job_url("acme", 1), job_url("acme", 2)
    greenhouse, greenhouse_undated = normalize_url(_GH_URL), normalize_url(_GH_UNDATED_URL)

    def listed(**filters: object) -> list[str]:
        response = posting_search.search_postings(fx.home_root, fx.target, now=NOW, **filters)  # type: ignore[arg-type]
        scout_new.check_response(response)
        return [row["job_identity"] for row in response["postings"]["rows"]]  # type: ignore[index,union-attr]

    # The profile's "posted within" (60 days): the posting up for 70 days is not matched, whenever it was last edited.
    assert set(listed()) == {lever, lever_undated, greenhouse, greenhouse_undated}
    # 7 days and 30 days: by the day the posting went up (40 and 10 days ago), not by its last change (3 days ago). A
    # posting the board gives no date for is judged by when Scout first saw it (yesterday), as before.
    assert set(listed(window="7d")) == {lever_undated, greenhouse_undated}
    assert set(listed(window="30d")) == {lever, lever_undated, greenhouse_undated}

    # Newest posted first: the same date the window judges, the newest first; an undated posting by its first sighting.
    newest = listed(sort="newest_posted")
    assert newest[2:] == [lever, greenhouse] and set(newest[:2]) == {lever_undated, greenhouse_undated}
    # The default order is what it was (the fit, the rank, then the newest seen): asking for it by name changes nothing.
    assert listed(sort="fit") == listed()
    response = posting_search.search_postings(fx.home_root, fx.target, now=NOW, sort="newest_posted")
    assert response["filters"]["sort"] == "newest_posted"  # type: ignore[index]
    assert posting_search.search_postings(fx.home_root, fx.target, now=NOW)["filters"]["sort"] == "fit"  # type: ignore[index]
    with pytest.raises(posting_search.PostingSearchError) as refused:
        posting_search.search_postings(fx.home_root, fx.target, now=NOW, sort="oldest")
    assert refused.value.code == "invalid_value"


def test_a_home_from_before_never_shows_a_last_change_as_posted_and_the_next_update_dates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upgrade. The index file holds Greenhouse's ``updated_at`` as ``published_at`` (what the version before wrote)."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    _seed(fx)
    path = fx.index.path("greenhouse", _GH_TOKEN)
    stored = json.loads(path.read_text(encoding="utf-8"))
    for posting in stored["postings"].values():
        posting.pop("published_kind", None)
        posting["published_at"] = posting["updated_at"]
    path.write_text(json.dumps(stored), encoding="utf-8")
    updated = postings.stamp(index_stamp(_UPDATED))
    greenhouse, undated, old = normalize_url(_GH_URL), normalize_url(_GH_UNDATED_URL), normalize_url(_GH_OLD_URL)

    # Before any update: no row says its last change (3 days ago) is the day it was posted. The cached board body
    # this home already holds gives the real day; a posting it gives none for is "first seen".
    rows = _rows(fx)
    for job in (greenhouse, undated, old):
        assert rows[job]["published_at"] != updated and rows[job]["updated_at"] == updated
    assert (rows[greenhouse]["published_at"], rows[greenhouse]["published_kind"]) == (postings.stamp(index_stamp(_GH_POSTED)), "posted")
    assert (rows[undated]["published_at"], rows[undated]["published_kind"]) == (None, None)
    # The index has no posted date for the 70-days-old posting yet, so the "posted within" cutoff still keeps it.
    assert old in rows

    # The next sources update, for a board that did not change (no request here: the cached body is read again).
    later = NOW + timedelta(hours=2)
    change = refresh_company(fx.index, fx.cache, ats="greenhouse", slug=_GH_TOKEN, company="Harbor Grid", observed_at=index_stamp(later))
    assert not change.has_changes

    response = posting_search.search_postings(fx.home_root, fx.target, now=later)
    after = {row["job_identity"]: row for row in response["postings"]["rows"]}  # type: ignore[index,union-attr]
    assert old not in after  # up for 70 days: outside the profile's 60
    assert (after[greenhouse]["published_at"], after[greenhouse]["published_kind"]) == (postings.stamp(index_stamp(_GH_POSTED)), "posted")
    # Nothing is "new" or changed for it: every posting was first seen when it was.
    assert {job: row["first_seen_at"] for job, row in after.items()} == {job: rows[job]["first_seen_at"] for job in after}
    assert response["counts"]["new"] == len(after) == 4  # type: ignore[index]


def test_the_date_line_names_what_the_date_is() -> None:
    row = {"published_at": "2026-09-23T15:00:00.000000Z", "published_kind": "posted", "first_seen_at": "2026-10-02T15:00:00.000000Z"}

    assert scout_new.posted_text(row) == "posted 2026-09-23"
    assert scout_new.posted_text({**row, "published_kind": "updated"}) == "updated 2026-09-23"
    assert scout_new.posted_text({**row, "published_at": None, "published_kind": None}) == "first seen 2026-10-02"
    # A row from before the fields existed (an older stored response): its one date, said as what it is.
    assert scout_new.posted_text({"first_seen": "2026-10-02T15:00:00.000000Z"}) == "first seen 2026-10-02"
    assert scout_new.posted_text({}) == ""


def test_every_board_kind_says_what_its_date_means() -> None:
    """A new provider cannot be added without saying whether its date is the posting day or the last change."""

    assert set(scout_new.PUBLISHED_KINDS) == {provider.value for provider in ATSProvider}
    assert set(scout_new.PUBLISHED_KINDS.values()) <= {"posted", "updated"}
    # ats_board_clients: Greenhouse's `first_published`, Lever's `createdAt`, Ashby's `publishedAt`; 0.1.11.8: Workable's
    # `published_on`, Rippling's `createdOn` (detail), Gem's `first_published_at`, Recruitee's `published_at`, Breezy's
    # `published_date`; Pinpoint's feed has no date (its postings are undated, the entry is never read).
    assert scout_new.PUBLISHED_KINDS == {
        "greenhouse": "posted", "lever": "posted", "ashby": "posted", "workable": "posted", "rippling": "posted",
        "gem": "posted", "recruitee": "posted", "pinpoint": "posted", "breezy": "posted",
    }
    # The table names the list field each date is read from (Pinpoint has none: no field, undated postings); a
    # provider nobody listed is the weaker claim.
    assert set(ats_board_clients.PUBLISHED_FIELDS) == set(scout_new.PUBLISHED_KINDS) - {"pinpoint"}
    unlisted = SimpleNamespace(board="smartrecruiters:acme", published_at="2026-09-23T15:00:00.000000Z", first_seen="2026-10-02T15:00:00.000000Z")
    assert scout_new.posting_dates(unlisted)["published_kind"] == "updated"  # type: ignore[arg-type]
