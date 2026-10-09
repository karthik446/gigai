"""0.1.11.9 RB2: a job posted once per country has ONE assessment, one application and one set of tags. Synthetic only.

0.1.11.8 made the copies of one job one ROW (``job_copies``, ``canonical_job``); 0.1.11.9 keeps one assessment and
one resume per JOB. Until this packet the two did not meet: the stores were keyed by each posting's own address, so
an assessment made by a non-canonical copy's URL sat under that copy and the job's row still read "not assessed", a
role that found only another copy was not among the row's tags, and an application on a copy the list's filters left
out did not show.

The postings are ``tests/support/copies_fixtures.py``'s: one job posted in seven countries, here also in the US
(posting 14, so the canonical one), on a made-up Lever board.

Pinned, on the END outcome (what is on disk, what the list serves):

- assess by a NON-canonical copy's URL: one model call, exactly ONE assessment file, under the canonical posting's
  key; the Jobs list's one row shows it assessed, so does every copy's own row; naming another copy afterwards has
  nothing to assess; two copies named at once are one model call;
- the three stores read and write ONE file whichever copy is named;
- an application on EITHER copy shows on the row, also on a copy the list's filters leave out;
- the row's tags are the roles that found ANY copy, and a role filter keeps the row for a role that found only
  another copy (the row stays the canonical posting);
- a job that already has an assessment under one posting stays keyed by it when a new canonical posting appears.

No network, no real home; the scripted model of ``pipeline_fixtures``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import uuid

import pytest

from gigai.application_events import record_application
from gigai.scout import posting_search, profile_records, suggestions, tailored_resume
from gigai.scout.find_jobs.job_key import canonical_identity, copies_of, job_key
from gigai.scout.find_jobs.job_state import JobStateSources
from gigai.scout.job_store_layout import job_digest
from gigai.scout.profile_records import ProfileSearchSettings
from gigai.scout.quick_assess import quick_assess_dir, quick_assess_path, quick_assess_write_path, read_quick_assessment
from gigai.workpad import committed_read_cache

from tests.support.copies_fixtures import ONE_JOB, SLUG, anywhere_profile, board, copy_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url
from tests.support.scout_profile_fixtures import uuids

NEWEST = NOW - timedelta(hours=3)
US, ESTONIA, LATVIA = job_url(SLUG, 14), job_url(SLUG, 1), job_url(SLUG, 3)
#: The eight postings of the one job: the US one, then the seven countries.
COPIES = [US, *(job_url(SLUG, n) for n in ONE_JOB)]


def _us_copy() -> dict[str, object]:
    return copy_job(SLUG, 14, "Staff Engineer", "Denver, CO", "US", NEWEST + timedelta(minutes=20))


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed(SLUG, [*board(newest=NEWEST), _us_copy()], seen_at=days_ago(1))
    return fixture


@pytest.fixture
def anywhere(fx: PostingsFixture) -> str:
    return anywhere_profile(fx)


def _search(fx: PostingsFixture, profile_id: str | None = None, **more: object) -> dict:
    roles = {} if profile_id is None else {"profile_ids": [profile_id]}
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW, limit=200, **roles, **more)  # type: ignore[arg-type]


def _rows(response: dict) -> dict[str, dict]:
    return {row["job_identity"]: row for row in response["postings"]["rows"]}


def _assess(fx: PostingsFixture, *jobs: str) -> dict:
    return posting_search.assess_these(fx.home_root, fx.target, jobs=list(jobs), approve=True, include_low_rank=True, now=NOW)


def _stored_files(fx: PostingsFixture) -> list[str]:
    store = quick_assess_dir(fx.home_root, fx.target)
    return sorted(path.relative_to(store).as_posix() for path in store.rglob("*.json"))


def _apply(fx: PostingsFixture, job: str) -> None:
    with committed_read_cache():
        recorded = record_application(
            resolved=fx.base.gig.resolved,
            data={"external_ref": job, "event_kind": "applied", "occurred_at": "2026-10-02T10:00:00Z", "timezone": "UTC", "operation_key": f"copies-{uuid.uuid4()}"},
            confirm=True,
        )
    assert recorded["status"] == "recorded"


def test_the_copies_of_a_job_share_one_key_the_canonical_posting(fx: PostingsFixture) -> None:
    for copy in COPIES:
        assert copies_of(fx.home_root, copy)[0] == US and set(copies_of(fx.home_root, copy)) == set(COPIES), copy
        assert canonical_identity(fx.home_root, copy) == US and job_key(fx.home_root, fx.target, copy) == US, copy
    # Another description under the same title, another title, a URL no board holds, pasted text: each its own.
    for alone in (job_url(SLUG, 12), job_url(SLUG, 13), "https://jobs.lever.co/point-example/nowhere", "text:sha256:" + "a" * 64):
        assert job_key(fx.home_root, fx.target, alone) == alone, alone
    # The three stores name ONE file for the job, for a read and for a write, whichever copy is named.
    profile = fx.default_profile_id
    for path_of in (quick_assess_path, quick_assess_write_path, tailored_resume.tailored_resume_path, tailored_resume.tailored_resume_write_path, suggestions.suggestions_path, suggestions.suggestions_write_path):
        found = {path_of(fx.home_root, fx.target, profile, copy) for copy in COPIES}
        assert len(found) == 1 and next(iter(found)).name == f"{job_digest(US)}.json", path_of.__name__


def test_assessing_by_a_non_canonical_copys_url_makes_the_jobs_one_assessment(fx: PostingsFixture, anywhere: str) -> None:
    before = _rows(_search(fx, anywhere, us_only=False))
    assert before[US]["copies"] == 8 and before[US]["state"] == "not_assessed" and _stored_files(fx) == []
    calls = fx.base.model.calls

    done = _assess(fx, LATVIA)

    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 1 and done["assessed"]["failed"] == [], done["assessed"]
    assert fx.base.model.calls == calls + 1
    # Exactly ONE assessment, in the per-job folder, under the CANONICAL posting's key; it is the canonical posting's.
    assert _stored_files(fx) == [f"job/{job_digest(US)}.json"]
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, LATVIA)
    assert stored is not None and stored.job.job_identity == US and stored.job.location == "Denver, CO"
    assert all(read_quick_assessment(fx.home_root, fx.target, anywhere, copy) == stored for copy in COPIES)
    # The Jobs list: the job's one row shows it assessed ...
    rows = _rows(_search(fx, anywhere, us_only=False))
    assert rows[US]["copies"] == 8 and rows[US]["state"] != "not_assessed" and rows[US]["assessment"] is not None
    assert not set(rows) & set(COPIES[1:])
    # ... and so does each copy's own row when every posting is listed, and the copy read by its address (the job page).
    each = _rows(_search(fx, anywhere, us_only=False, collapse=False))
    assert {each[copy]["state"] for copy in COPIES} == {rows[US]["state"]}
    by_address = posting_search.search_postings(fx.home_root, fx.target, jobs=[LATVIA], now=NOW)["postings"]["rows"]
    assert [(row["job_identity"], row["state"]) for row in by_address] == [(LATVIA, rows[US]["state"])]
    with committed_read_cache():
        sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
        assert sources.quick_assessment(ESTONIA, fx.default_profile_id) == stored
    # Another copy, or the canonical posting, named afterwards: nothing to assess, no model call, still one file.
    for named in ((ESTONIA,), (US,), (ESTONIA, US, LATVIA)):
        again = posting_search.assess_these(fx.home_root, fx.target, jobs=list(named), approve=True, include_low_rank=True, now=NOW)
        assert again["status"] == "nothing_to_assess" and again["assessed"] is None, named
    assert fx.base.model.calls == calls + 1 and _stored_files(fx) == [f"job/{job_digest(US)}.json"]


def test_two_copies_named_at_once_are_one_model_call(fx: PostingsFixture, anywhere: str) -> None:
    calls = fx.base.model.calls
    ask = posting_search.assess_these(fx.home_root, fx.target, jobs=[ESTONIA, LATVIA], include_low_rank=True, now=NOW)
    assert ask["status"] == "ask" and ask["counts"]["selected"] == 2 and ask["counts"]["to_assess"] == 1 and ask["counts"]["batch"] == 1

    done = _assess(fx, ESTONIA, LATVIA)

    assert done["assessed"] == {**done["assessed"], "requested": 1, "assessed": 1, "failed": []}
    assert fx.base.model.calls == calls + 1 and _stored_files(fx) == [f"job/{job_digest(US)}.json"]
    assert {row["state"] for row in done["postings"]["rows"]} != {"not_assessed"}


def test_an_application_on_either_copy_shows_on_the_jobs_row(fx: PostingsFixture, anywhere: str) -> None:
    # US only is on in this setup: the Latvia copy is not among the postings the list selects. The job is its US posting.
    listed = _rows(_search(fx, anywhere))
    assert US in listed and listed[US]["copies"] == 1 and listed[US]["application"] is None

    _apply(fx, LATVIA)

    # The job is applied to: its row leaves the list, and "Applied" lists it once, with the application.
    assert US not in _rows(_search(fx, anywhere))
    applied = _search(fx, anywhere, states=["applied"])
    assert [(row["job_identity"], row["application"]["status"]) for row in applied["postings"]["rows"]] == [(US, "applied")]
    assert applied["counts"]["applied"] == 1
    wide = _search(fx, anywhere, us_only=False, states=["applied"])["postings"]["rows"]
    assert [(row["job_identity"], row["copies"], row["application"]["since"]) for row in wide] == [(US, 8, "2026-10-02T10:00:00Z")]
    # The job read by any copy's address says applied too.
    with committed_read_cache():
        sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
        assert {sources.state_for(copy, profile_id=fx.default_profile_id).state for copy in (US, ESTONIA, LATVIA)} == {"applied"}


def test_an_application_on_the_canonical_posting_shows_on_a_copys_row(fx: PostingsFixture, anywhere: str) -> None:
    _apply(fx, US)
    each = _search(fx, anywhere, us_only=False, collapse=False, states=["applied"])["postings"]["rows"]
    assert {row["job_identity"] for row in each} == set(COPIES) and {row["application"]["status"] for row in each} == {"applied"}
    assert not set(_rows(_search(fx, anywhere, us_only=False))) & set(COPIES)


def test_the_rows_tags_are_the_roles_that_found_any_copy_and_a_role_filter_judges_the_row(fx: PostingsFixture, anywhere: str) -> None:
    # A role whose search is Estonia only: it finds the Estonia copy and never the canonical (US) posting.
    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    estonia = profile_records.create_profile(
        resolved, label="Estonia", titles=("staff engineer",), titles_to_avoid=(), queries=("staff engineer",), resume_ref=default.resume_ref,
        search_settings=ProfileSearchSettings(countries=("EE",), location=None, max_age_days=None, work_mode="any"), uuid_factory=uuids(71),
    ).profile_id
    each = _rows(_search(fx, us_only=False, collapse=False))
    assert estonia in {tag["profile_id"] for tag in each[ESTONIA]["tags"]}, "the Estonia role found the Estonia copy"
    assert estonia not in {tag["profile_id"] for tag in each[US]["tags"]}, "and not the canonical posting"

    row = _rows(_search(fx, us_only=False))[US]

    # ONE row, the canonical posting, tagged with the roles that found ANY copy: the canonical posting's own first.
    tags = [tag["profile_id"] for tag in row["tags"]]
    own = [tag["profile_id"] for tag in each[US]["tags"]]
    assert row["copies"] == 8 and tags[: len(own)] == own and set(tags) == set(own) | {estonia} and anywhere in tags
    assert len(tags) == len(set(tags)) and [tag["match_rank"] for tag in row["tags"]] == list(range(1, len(tags) + 1))
    assert {item["profile_id"] for item in row["profiles"]} == set(tags)
    # A role filter acts on the row: the Estonia role keeps the job, shown by its canonical posting with every tag.
    filtered = _rows(_search(fx, estonia, us_only=False))
    assert set(filtered) & set(COPIES) == {US}
    assert filtered[US]["copies"] == 8 and [tag["profile_id"] for tag in filtered[US]["tags"]] == tags
    # What US only left out is counted for the role asked about: its one posting (Estonia), not the other roles' six.
    assert _search(fx, estonia)["counts"]["us_only_left_out"] == 1 and _search(fx, anywhere)["counts"]["us_only_left_out"] >= 7
    # Every posting listed on its own: the filter judges each posting, as before.
    assert set(_rows(_search(fx, estonia, us_only=False, collapse=False))) & set(COPIES) == {ESTONIA}
    # "Assess these" for that role is the job, once: its canonical posting.
    ask = posting_search.assess_these(fx.home_root, fx.target, profile_id=estonia, states=["not_assessed"], include_low_rank=True, now=NOW, us_only=False)
    assert [row["job_identity"] for row in ask["postings"]["rows"] if row["job_identity"] in COPIES] == [US]


def test_a_job_assessed_under_one_posting_stays_keyed_by_it_when_a_new_canonical_posting_appears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed(SLUG, board(newest=NEWEST), seen_at=days_ago(1))  # the seven countries, no US posting yet
    anywhere = anywhere_profile(fx)
    earliest = job_url(SLUG, 7)  # posting n went up n minutes before NEWEST: 7 is the earliest
    assert job_key(fx.home_root, fx.target, LATVIA) == earliest
    calls = fx.base.model.calls
    assert _assess(fx, LATVIA)["assessed"]["assessed"] == 1 and _stored_files(fx) == [f"job/{job_digest(earliest)}.json"]

    # The same job is now posted in the US too: the row is the US posting, the job's records stay where they are.
    fx.seed(SLUG, [*board(newest=NEWEST), _us_copy()], seen_at=days_ago(0.5))

    assert canonical_identity(fx.home_root, LATVIA) == US and job_key(fx.home_root, fx.target, US) == earliest
    row = _rows(_search(fx, anywhere, us_only=False))[US]
    assert row["copies"] == 8 and row["state"] != "not_assessed"
    again = posting_search.assess_these(fx.home_root, fx.target, jobs=[US], approve=True, include_low_rank=True, now=NOW)
    assert again["status"] == "nothing_to_assess" and fx.base.model.calls == calls + 1
    assert _stored_files(fx) == [f"job/{job_digest(earliest)}.json"]
