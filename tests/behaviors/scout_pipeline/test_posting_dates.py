"""0110-10-14: a posting's date is in the Jobs rows, the one-job read and the terminal lines, each under its own name.

Two dates are stored for a posting and they are not the same thing:

- ``published_at`` is the BOARD's own date, the one the 7 / 30 days window judges. What a board means by it depends on
  the board: Lever's ``createdAt`` and Ashby's ``publishedAt`` are the day the posting went up; Greenhouse's list
  gives ``updated_at``, the posting's last change. ``published_kind`` says which (``posted`` | ``updated``), so an
  update is never shown as "posted".
- ``first_seen`` is when Scout first stored the posting (the update that first listed it): what "new since" judges.
  It is also served as ``first_seen_at``.

The END outcome, on a synthetic home (no request leaves the process): three postings, one of each kind, read through
``GET /api/postings`` (``search_postings``), ``gigai scout jobs list``, ``gigai scout new`` and ``GET /api/jobs?url=``.
Before the fix no row carried ``published_at`` and no line of text said a date.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings, scout_new
from gigai.scout.find_jobs import company_names
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import ATSProvider, normalize_url
from gigai.scout.find_jobs.watchlist import add_company_from_url

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_GH_TOKEN = "harborgrid"
_GH_URL = "https://www.harborgrid-example.test/jobs?gh_jid=4411"
#: Seen by Scout a day ago; the boards' own dates are older.
_SEEN = days_ago(1)
_POSTED = days_ago(10)  # Lever: createdAt
_UPDATED = days_ago(3)  # Greenhouse: updated_at


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _seed(fx: PostingsFixture) -> None:
    dated = lever_job("acme", 1, created=_POSTED)
    undated = lever_job("acme", 2)
    del undated["createdAt"]  # a board that gives no date
    fx.seed("acme", [dated, undated], seen_at=_SEEN)
    stamp = _UPDATED.strftime("%Y-%m-%dT%H:%M:%SZ")
    jobs = {"jobs": [{
        "id": 4411, "title": TITLE_BOTH, "absolute_url": _GH_URL, "location": {"name": "Remote - United States"},
        "updated_at": stamp, "company_name": "Harbor Grid", "content": "<p>Harbor Grid is hiring a Staff AI Engineer for its Python services.</p>",
    }]}
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

    # Lever: the day the posting went up. Greenhouse: its last change, said as such. No board date: none, and no kind.
    assert (dated["published_at"], dated["published_kind"]) == (postings.stamp(index_stamp(_POSTED)), "posted")
    assert (greenhouse["published_at"], greenhouse["published_kind"]) == (postings.stamp(index_stamp(_UPDATED)), "updated")
    assert (undated["published_at"], undated["published_kind"]) == (None, None)
    # When Scout first stored it: the same for all three, under the clear name and the one it always had.
    for row in (dated, undated, greenhouse):
        assert row["first_seen_at"] == row["first_seen"] == seen
    # The two dates are different facts: the board's is never the day Scout saw the posting.
    assert dated["published_at"] != dated["first_seen_at"]

    # One job, read by its URL: the same three facts, on the posting and on its grid row.
    job = _Routes()._job_aggregate(normalize_url(_GH_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None
    job = company_names.with_company_names(job, fx.home_root)
    for shown in (job["posting"], job["index_posting"]):  # type: ignore[index]
        assert (shown["published_at"], shown["published_kind"], shown["first_seen_at"]) == (greenhouse["published_at"], "updated", seen)

    # The terminal says the date each row has, with its own word: never an update or a first sighting as "posted".
    for command in (["scout", "jobs", "list"], ["scout", "new", "--no-assess"]):
        printed = CliRunner().invoke(cli, [*command, "--home", str(fx.home_root), "--target", str(fx.target)])
        assert printed.exit_code == 0, printed.output
        text = " ".join(printed.output.split())
        assert f"posted {_POSTED:%Y-%m-%d}" in text, printed.output
        assert f"updated {_UPDATED:%Y-%m-%d}" in text, printed.output
        assert f"first seen {_SEEN:%Y-%m-%d}" in text, printed.output
        assert text.count("posted 20") == 1 and text.count("updated 20") == 1 and text.count("first seen 20") == 1, printed.output


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
    assert scout_new.PUBLISHED_KINDS["greenhouse"] == "updated"  # ats_board_clients: Greenhouse's list gives `updated_at`
