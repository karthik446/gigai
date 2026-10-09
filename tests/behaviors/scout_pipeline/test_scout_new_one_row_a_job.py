"""0.1.11.9 NEW1: ``gigai scout new`` and ``GET /api/new`` show ONE row a job, with US only. Synthetic only.

0.1.11.8 made the copies of one job one row of the Jobs list and gave the list US only; ``scout new`` still listed
and counted every posting. Here it is the list's own rule (``job_copies.copy_key``, ``canonical_job.pick_canonical``)
and the list's own switch and default (``posting_search.us_only_setting``).

The postings, on a made-up Lever board (``nova-example``):

- 1-5: "Staff Engineer" with ONE description in three US cities (1-3) and two other countries (4, 5): ONE job. Posting
  ``n`` went up ``n`` minutes before the newest, so posting 5 is the earliest of all and posting 3 the earliest in
  the US: the canonical posting is 3;
- 6, 7: "Staff Engineer, Data" (another description) in two countries, none the US: one job, only abroad;
- 8: "Staff Engineer, Search" in the US: a job with no copy.

Pinned, on the END outcome (the rows and counts the command and the route serve, the model calls a yes makes):

(1) the one job is ONE row, its canonical posting, the other locations on the row; the counts and the message say
    1 new job;
(2) US only ON (the default of this US setup) hides the job posted only abroad and leaves it out of every count;
    ``--no-us-only`` shows it; the default is the Jobs list's;
(3) the API and the CLI agree row for row and count for count on one home;
(4) "to assess" and the three questions count jobs, and a batch makes ONE model call a job.

No network, no real home; the scripted model of ``pipeline_fixtures``.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs import job_copies
from gigai.scout.job_store_layout import job_digest
from gigai.scout.quick_assess import quick_assess_dir

from tests.behaviors.scout_pipeline.test_scout_new_uat import _old_run
from tests.support.copies_fixtures import OTHER_ROLE, anywhere_profile, copy_job
from tests.support.fit_fixtures import seed_rank
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, freeze_scout_new_clock, job_url

SLUG = "nova-example"
NEWEST = NOW - timedelta(hours=3)
US_CITIES = ("Austin, TX", "Denver, CO", "Remote - United States")
ABROAD = ("Remote Estonia", "Remote Germany")
#: The one job: its canonical posting (the earliest of the US ones), then the other US ones, then the ones abroad.
JOB = job_url(SLUG, 3)
JOB_US = [job_url(SLUG, n) for n in (3, 2, 1)]
JOB_ALL = [*JOB_US, job_url(SLUG, 5), job_url(SLUG, 4)]
#: The job posted only abroad (its earliest posting), and the job with no copy.
ONLY_ABROAD, ALONE = job_url(SLUG, 7), job_url(SLUG, 8)


def _one_job() -> list[dict[str, object]]:
    jobs = [copy_job(SLUG, n, "Staff Engineer", place, "US", NEWEST) for n, place in enumerate(US_CITIES, start=1)]
    return jobs + [copy_job(SLUG, n, "Staff Engineer", place, None, NEWEST) for n, place in enumerate(ABROAD, start=4)]


def _more() -> list[dict[str, object]]:
    return [
        copy_job(SLUG, 6, "Staff Engineer, Data", "Remote Poland", None, NEWEST, description=OTHER_ROLE),
        copy_job(SLUG, 7, "Staff Engineer, Data", "Remote Spain", None, NEWEST, description=OTHER_ROLE),
        copy_job(SLUG, 8, "Staff Engineer, Search", "Remote - United States", "US", NEWEST, description="Own search. Requirements: Python; Lucene."),
    ]


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    freeze_scout_new_clock(monkeypatch)
    anywhere_profile(fixture)  # no country setting: its list holds the postings abroad too
    return fixture


def _new(fx: PostingsFixture, **more: object) -> dict:
    more.setdefault("peek", True)
    return scout_new.scout_new(fx.home_root, fx.target, now=NOW, **more)  # type: ignore[arg-type]


def _rows(response: dict) -> dict[str, dict]:
    return {row["job_identity"]: row for row in response["postings"]["rows"]}


def _stored_files(fx: PostingsFixture) -> list[str]:
    store = quick_assess_dir(fx.home_root, fx.target)
    return sorted(path.relative_to(store).as_posix() for path in store.rglob("*.json"))


def test_one_job_in_three_us_cities_and_two_countries_is_one_row_and_one_new_job(fx: PostingsFixture) -> None:
    fx.seed(SLUG, _one_job(), seen_at=days_ago(1))

    # Every location: the canonical posting is a US one (the earliest of the three), although two abroad are earlier.
    found = _new(fx, us_only=False)
    (row,) = found["postings"]["rows"]
    assert (row["job_identity"], row["location"], row["copies"]) == (JOB, "Remote - United States", 5)
    assert [member["job_identity"] for member in row["members"]] == JOB_ALL
    assert row["locations"] == ["Remote - United States", "Denver, CO", "Austin, TX", "Remote Germany", "Remote Estonia"]
    assert row["locations_text"] == "Remote - United States; Denver, CO; Austin, TX +2" and row["location_unclear"] is False
    counts = found["counts"]
    assert (counts["new"], counts["shown"], counts["to_assess"], counts["postings"], counts["us_only_left_out"]) == (1, 1, 1, 5, 0)
    assert max(item["new"] for item in counts["by_profile"]) == 1  # a role tags the JOB once, however many copies it found
    assert str(found["message"]).startswith("1 new job since ") and "They stand for 5 postings" in found["message"]
    assert found["question"]["new"] == 1 and found["question"]["to_assess"] == 1 and found["question"]["text"].startswith("1 new job")
    assert found["us_only"] == {"on": False, "default": True, "rule": job_copies.US_ONLY_RULE}

    # The default (US only on): the same ONE job, by its US postings; the two abroad are not copies of the row.
    default = _new(fx)
    (row,) = default["postings"]["rows"]
    assert (row["job_identity"], row["copies"], row["locations"]) == (JOB, 3, ["Remote - United States", "Denver, CO", "Austin, TX"])
    assert (default["counts"]["new"], default["counts"]["postings"], default["counts"]["us_only_left_out"]) == (1, 3, 0)
    assert default["us_only"]["on"] is True and str(default["message"]).startswith("1 new job since ")

    # The table: one row, with the locations and how many postings it stands for.
    shown = scout_new.render(found)
    cells = [line.split(" | ")[0].strip() for line in shown.splitlines()]
    assert shown.count("Staff Engineer") == 1 and "5 postings" in cells and "Denver, CO; Austin, TX +2" in cells


def test_us_only_hides_a_job_posted_only_abroad_and_its_count_and_the_default_is_the_jobs_lists(fx: PostingsFixture) -> None:
    fx.seed(SLUG, [*_one_job(), *_more()], seen_at=days_ago(1))

    assert posting_search.us_only_setting(fx.home_root, fx.target) is True
    default = _new(fx)
    assert set(_rows(default)) == {JOB, ALONE} and default["us_only"] == {"on": True, "default": True, "rule": job_copies.US_ONLY_RULE}
    counts = default["counts"]
    assert (counts["new"], counts["shown"], counts["to_assess"], counts["postings"], counts["us_only_left_out"]) == (2, 2, 2, 4, 1)
    assert default["question"]["new"] == 2 and default["question"]["to_assess"] == 2
    assert "1 new job posted only outside the US is left out (US only): gigai scout new --no-us-only" in default["message"]
    assert _new(fx, us_only=True)["counts"] == counts
    # The default's yes names no switch; a call that named one hands it to its yes, so the yes selects what was counted.
    assert default["question"]["yes"]["cli"] == f"gigai scout new --yes --since {default['since']}"
    assert "us_only" not in default["question"]["yes"]["api"]["body"]

    # Off: the job abroad is new too, as ONE row (its earliest posting), and it is counted.
    every = _new(fx, us_only=False)
    rows = _rows(every)
    assert set(rows) == {JOB, ALONE, ONLY_ABROAD} and (rows[ONLY_ABROAD]["copies"], rows[ONLY_ABROAD]["locations"]) == (2, ["Remote Spain", "Remote Poland"])
    counts = every["counts"]
    assert (counts["new"], counts["shown"], counts["to_assess"], counts["postings"], counts["us_only_left_out"]) == (3, 3, 3, 8, 0)
    assert every["us_only"]["on"] is False and "outside the US" not in every["message"]
    assert every["question"]["yes"]["cli"] == f"gigai scout new --yes --since {every['since']} --no-us-only"
    assert every["question"]["yes"]["api"]["body"] == {"assess": True, "since": every["since"], "us_only": False}
    assert every["yours_hint"]["cli"].endswith(" --no-us-only") and every["yours_hint"]["api"]["path"].endswith("&us_only=0")

    # The Jobs list's "new" window holds the same jobs under the same switch (the row keys are the canonical postings).
    for switch, response in ((None, default), (False, every)):
        listed = posting_search.search_postings(fx.home_root, fx.target, window="new", now=NOW, limit=200, us_only=switch)
        assert {row["job_identity"] for row in listed["postings"]["rows"]} == set(_rows(response)), switch

    # A setup whose countries do not hold the US: off by default, as the list is.
    from gigai.scout import postings

    real = postings._shared_config

    def elsewhere(home: Path, target: Path) -> object:
        from dataclasses import fields

        config = real(home, target)
        return config.__class__(**{**{item.name: getattr(config, item.name) for item in fields(config) if item.init}, "countries": ("DE",)})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(postings, "_shared_config", elsewhere)
        other = _new(fx)
    assert other["us_only"]["on"] is False and other["us_only"]["default"] is False and other["counts"]["new"] == 3


def test_the_api_and_the_cli_agree_row_for_row_and_count_for_count(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    for name in ("GIGAI_SCOUT_AUTO_REFRESH", "GIGAI_SCOUT_MODEL_TAGS", "GIGAI_SCOUT_POSTING_LIVENESS"):
        monkeypatch.setenv(name, "0")
    fx.seed(SLUG, [*_one_job(), *_more()], seen_at=days_ago(1))
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def api(query: str) -> dict:
            with urllib.request.urlopen(f"{base}/api/new{query}", timeout=120) as response:
                assert response.status == 200
                return json.loads(response.read())

        def command(*flags: str) -> dict:
            run = CliRunner().invoke(cli, fx.cli("--peek", "--json", *flags))
            assert run.exit_code == 0, run.output
            return json.loads(run.output)

        for query, flags, jobs in (("", (), 2), ("?us_only=1", ("--us-only",), 2), ("?us_only=0", ("--no-us-only",), 3)):
            served, printed = api(query), command(*flags)
            rows = served["postings"]["rows"]
            assert len(rows) == jobs and served["counts"]["new"] == jobs, query
            # Row for row (every key of every row) and count for count; the three questions too.
            assert rows == printed["postings"]["rows"], query
            assert served["counts"] == printed["counts"] and served["us_only"] == printed["us_only"], query
            for key in ("question", "low_rank_question", "stale_question", "message", "status", "since"):
                assert served[key] == printed[key], (query, key)
        one = next(row for row in rows if row["job_identity"] == JOB)
        assert one["copies"] == 5 and [member["job_identity"] for member in one["members"]] == JOB_ALL

        # What the route refuses: a switch that is neither 1 nor 0.
        try:
            urllib.request.urlopen(f"{base}/api/new?us_only=maybe", timeout=120)
        except urllib.error.HTTPError as error:
            assert error.code == 422 and json.loads(error.read())["error"]["code"] == "invalid_value"
        else:
            raise AssertionError("us_only=maybe was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)

    # The table says what the JSON says: one line a job, the count of jobs, what US only left out.
    table = CliRunner().invoke(cli, fx.cli("--peek", "--no-assess"))
    assert table.exit_code == 0, table.output
    assert table.output.startswith("2 new jobs since ") and "3 postings" in [line.split(" | ")[0].strip() for line in table.output.splitlines()]
    assert "1 new job posted only outside the US is left out (US only)" in table.output


def test_the_three_questions_count_jobs_and_a_batch_asks_for_one_assessment_a_job(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fx.seed(SLUG, [*_one_job(), *_more()], seen_at=days_ago(1))
    # The job with no copy is ranked low: the low-rank question is about ONE job.
    seed_rank(fx, monkeypatch, {ALONE: 20}, anywhere_id(fx))

    asked = _new(fx, us_only=False)
    counts = asked["counts"]
    assert (counts["new"], counts["to_assess"], counts["low_rank_skipped"], counts["only_stale"]) == (3, 2, 1, 0)
    assert (asked["question"]["new"], asked["question"]["to_assess"], asked["question"]["batch"]) == (3, 2, 2)
    assert asked["question"]["estimate"]["calls"] == 2 and "3 new jobs" in asked["question"]["text"]
    assert (asked["low_rank_question"]["skipped"], asked["low_rank_question"]["batch"]) == (1, 1)
    assert asked["low_rank_question"]["yes"]["cli"].endswith(" --no-us-only")
    # With US only (the default) the job abroad is in no count and no batch.
    default = _new(fx)
    assert (default["counts"]["new"], default["counts"]["to_assess"], default["question"]["estimate"]["calls"]) == (2, 1, 1)

    # The yes: ONE model call a job (two jobs, seven postings), each stored once, under its canonical posting.
    calls = fx.base.model.calls
    done = _new(fx, us_only=False, assess=True)
    assert (done["assessed"]["requested"], done["assessed"]["assessed"], done["assessed"]["failed"]) == (2, 2, [])
    assert fx.base.model.calls == calls + 2
    assert _stored_files(fx) == sorted(f"job/{job_digest(job)}.json" for job in (JOB, ONLY_ABROAD))
    assert done["counts"]["to_assess"] == 0 and {row["state"] for job, row in _rows(done).items() if job != ALONE} != {"not_assessed"}
    assert _rows(done)[JOB]["copies"] == 5 and done["counts"]["new"] == 3

    # Its own yes: the one low-ranked job, one more call.
    low = _new(fx, us_only=False, assess=True, include_low_rank=True)
    assert low["assessed"]["requested"] == 1 and fx.base.model.calls == calls + 3 and low["low_rank_question"] is None


def test_the_old_assessment_question_counts_jobs_and_follows_us_only(fx: PostingsFixture) -> None:
    fx.seed(SLUG, [*_one_job(), *_more()], seen_at=days_ago(1))
    owner = anywhere_id(fx)
    # An old run assessed every posting of the one job, and both postings of the job abroad.
    _old_run(fx, [*JOB_ALL, job_url(SLUG, 6), job_url(SLUG, 7)], profile_id=owner)

    default = _new(fx)
    assert default["counts"]["only_stale"] == 1 and default["stale_question"]["to_reassess"] == 1
    assert default["stale_question"]["estimate"]["calls"] == 1 and default["stale_question"]["text"].startswith("1 has only an old assessment")
    every = _new(fx, us_only=False)
    assert every["counts"]["only_stale"] == 2 and every["stale_question"]["to_reassess"] == 2
    assert every["stale_question"]["yes"]["api"]["body"] == {"assess": False, "reassess_stale": True, "since": every["since"], "us_only": False}

    # The yes re-assesses each JOB once.
    calls = fx.base.model.calls
    done = _new(fx, us_only=False, assess=False, reassess_stale=True)
    assert (done["reassessed"]["requested"], done["reassessed"]["assessed"]) == (2, 2) and fx.base.model.calls == calls + 2
    assert done["stale_question"] is None and done["counts"]["only_stale"] == 0


def anywhere_id(fx: PostingsFixture) -> str:
    from gigai.scout import profile_records
    from tests.support.copies_fixtures import ANYWHERE_LABEL

    return next(item.profile_id for item in profile_records.list_profiles(fx.base.gig.resolved) if item.label == ANYWHERE_LABEL)
