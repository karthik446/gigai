"""0110-10-03 (c) and (d): one job read by its company-site URL says everything the index holds, and says its company by name.

The END outcome, on a synthetic home. A Greenhouse board embedded in a
company's own site: the posting's URL is
``https://www.<company>/jobs?gh_jid=<id>`` and the board token
(``sentrylabs``) is not a label of that host. The job is assessed from the
index (``scout new``'s path), no find-jobs run ever acquired it. Before the
fix ``GET /api/jobs?url=<that URL>`` answered ``rank: null``, no pay, no
work-mode fit and no H-1B (it joined those from a run's row only) and
``posting.company`` was the board token. After it the same read has the
index posting's rank score, stated pay, location, work mode, work-mode fit
and H-1B, ``company`` is the company's name and the token is
``company_slug``.

No request leaves the process: the board is put straight into the board
cache and the model is the fixture's scripted one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import postings
from gigai.scout.find_jobs import company_names, job_source
from gigai.scout.find_jobs.api import runs as runs_api
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.company_catalog import CompanyH1B
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import ATSProvider, normalize_url
from gigai.scout.find_jobs.model_rank import _hex, cache_dir, cache_key, prefs_digest
from gigai.scout.find_jobs.rank_digest import resume_digest
from gigai.scout.find_jobs.rank_run import rank_prefs
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.quick_assess import run_quick_assessment

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import RESUME, assessment
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago

_TOKEN = "sentrylabs"  # the board token: not a label of the company site's host
_NAME = "SentryOne Example"
_JOB_ID = "7819844003"
_URL = f"https://www.sentryone-example.test/jobs?gh_jid={_JOB_ID}"
_LOCATION = "United States - Remote"
_POSTING_HTML = (
    "<p>SentryOne Example is hiring a Staff AI Engineer to build reliable Python services.</p>"
    "<ul><li>5+ years of Python in production.</li><li>GCP experience.</li><li>Docker, Helm and Kubernetes.</li></ul>"
    "<p>Remote within the United States.</p>"
)


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _seed(fx: PostingsFixture) -> None:
    seen = days_ago(1)
    stamp = seen.strftime("%Y-%m-%dT%H:%M:%SZ")
    jobs = {"jobs": [{
        "id": int(_JOB_ID), "title": TITLE_BOTH, "absolute_url": _URL, "location": {"name": _LOCATION},
        "updated_at": stamp, "first_published": stamp, "company_name": _NAME, "content": _POSTING_HTML,
        "pay_input_ranges": [{"min_cents": 18_000_000, "max_cents": 22_000_000, "currency_type": "USD"}],
    }]}
    add_company_from_url(f"https://boards.greenhouse.io/{_TOKEN}", fx.home_root, fx.target)
    fx.cache.store("greenhouse", board_list_url("greenhouse", _TOKEN), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="greenhouse", slug=_TOKEN, company=_NAME, observed_at=index_stamp(seen))


def _rank(fx: PostingsFixture, done: postings.RefreshResult, identity: str, score: int) -> None:
    """A rank score in the home's score cache for the default profile, written as the ranker writes it."""

    store = postings.open_store(fx.home_root, fx.target)
    row = next(item for item in store.postings(jobs={identity}) if item.profile_id == fx.default_profile_id)
    view = next(item for item in done.profiles if item.profile_id == fx.default_profile_id)
    prefs = rank_prefs(view.config)  # type: ignore[arg-type]
    model = postings.rank_model_key(fx.home_root, fx.target)
    assert model is not None
    key = cache_key(
        content_sha256=row.listing_digest, resume_digest_sha256=_hex({"resume_digest": resume_digest(RESUME, prefs)}),
        prefs_sha256=prefs_digest(prefs), model=model,
    )
    cache_dir(fx.home_root).mkdir(parents=True, exist_ok=True)
    (cache_dir(fx.home_root) / f"{key}.json").write_text(json.dumps({"score": score, "reasons": [], "blockers": []}), encoding="utf-8")


def _served(fx: PostingsFixture, identity: str) -> dict:
    """``GET /api/jobs?url=`` as the server writes it: the aggregate, then the response's company naming."""

    body = _Routes()._job_aggregate(identity, home_root=fx.home_root, target=fx.target)
    assert body is not None
    return company_names.with_company_names(body, fx.home_root)  # type: ignore[return-value]


def test_a_company_site_gh_jid_job_reads_with_its_index_postings_rank_pay_location_and_h1b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    monkeypatch.setattr(
        runs_api, "_catalog_h1b_index", lambda: {(ATSProvider.GREENHOUSE, _TOKEN): CompanyH1B(approvals=12, fiscal_years=("2025", "2026"))}
    )
    _seed(fx)
    identity = normalize_url(_URL)
    done = postings.refresh(fx.home_root, fx.target, now=NOW)
    _rank(fx, done, identity, 91)

    # Assessed by its URL, through the product's own resolver: the index posting, no request (0110-10-04).
    fx.base.model.assessed = assessment(met=2)
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    assert (stored.job.fetch_kind, stored.job.job_identity) == ("ats_board", identity)
    postings.refresh(fx.home_root, fx.target, now=NOW)

    job = _served(fx, identity)

    # No run acquired it: everything below is the index posting's, joined by the URL alone.
    assert job["runs"] == []
    posting = job["posting"]
    assert (posting["fetch_kind"], posting["location"], posting["title"]) == ("ats_board", _LOCATION, TITLE_BOTH)
    assert posting["salary"] == "USD 180,000-220,000"
    assert posting["work_mode"] == "remote"
    assert (posting["provider"], posting["board_token"]) == ("greenhouse", _TOKEN)
    assert job["rank"] == {"normalized_url": identity, "score": 91, "profile_id": fx.default_profile_id, "source": "posting_index"}
    assert job["h1b"] == {"approvals": 12, "fiscal_years": ["2025", "2026"]}
    assert job["work_mode_fit"]["mode"] == "remote" and job["work_mode_fit"]["passes"] is True
    grid = job["index_posting"]
    assert (grid["job_identity"], grid["rank_score"], grid["salary"], grid["location"]) == (identity, 91, "USD 180,000-220,000", _LOCATION)
    assert job["job_state"]["state"] == "matched"

    # (c) The company is said by NAME; the board token is company_slug. Wherever the response shows the posting.
    for shown in (posting, grid):
        assert (shown["company"], shown["company_slug"], shown["company_name"]) == (_NAME, _TOKEN, _NAME)


def test_an_index_posting_nobody_assessed_is_still_one_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    _seed(fx)
    identity = normalize_url(_URL)
    postings.refresh(fx.home_root, fx.target, now=NOW)
    calls = fx.base.model.calls

    job = _served(fx, identity)

    assert fx.base.model.calls == calls  # a read: no model call
    assert job["assessments"] == [] and job["job_state"]["state"] == "not_assessed"
    assert (job["posting"]["company"], job["posting"]["company_slug"], job["posting"]["location"]) == (_NAME, _TOKEN, _LOCATION)
    assert "Helm" in job["posting"]["text"] and job["rank"] is None
    assert job["links"]["assess"]["body"] == {"job": {"job_url": identity}}
    # A URL the index does not hold, and nothing else names: still no job.
    assert _Routes()._job_aggregate(normalize_url("https://www.sentryone-example.test/jobs?gh_jid=1"), home_root=fx.home_root, target=fx.target) is None


def test_index_job_never_raises_and_never_creates_a_store(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    target.mkdir()

    assert job_source.index_job(home, target, normalize_url(_URL)) is None
    assert not any(home.rglob("*.sqlite"))


@pytest.mark.parametrize(
    ("stored", "token", "expected"),
    [
        # A board token the index names.
        ("sentrylabs", None, {"company": _NAME, "company_slug": "sentrylabs", "company_name": _NAME}),
        # A slug the index does not hold: the slug rule names it, and it is still the token.
        ("osprey-lane", None, {"company": "Osprey Lane", "company_slug": "osprey-lane", "company_name": "Osprey Lane"}),
        # A name a page gave is not a token.
        ("Harbor Lantern Inc", None, {"company": "Harbor Lantern Inc", "company_slug": None, "company_name": "Harbor Lantern Inc"}),
        # A run's posting row says its own token.
        ("Harbor Lantern Inc", "harborlantern", {"company": "Harbor Lantern Inc", "company_slug": "harborlantern", "company_name": "Harbor Lantern Inc"}),
    ],
)
def test_a_response_names_the_company_and_keeps_the_token_as_company_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: str, token: str | None, expected: dict[str, object],
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    node: dict[str, object] = {"normalized_url": _URL, "company": stored, "title": "Staff AI Engineer"}
    if token is not None:
        node["board_token"] = token
    payload = {"rows": [node], "story": {"company": "my own words"}}

    named = company_names.with_company_names(payload, fx.home_root)

    assert {key: named["rows"][0][key] for key in expected} == expected  # type: ignore[index]
    assert node == {key: value for key, value in node.items()} and "company_slug" not in node  # nothing given is changed
    assert named["story"] == {"company": "my own words"}  # type: ignore[index]
    # Naming a named response again changes nothing.
    assert company_names.with_company_names(named, fx.home_root) == named
