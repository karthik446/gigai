"""0110-10-04 A: a re-assessment reads the posting the base assessment read; it never downgrades to a scraped page.

The END outcome, on a synthetic home: a Greenhouse board embedded in a
company's own site (the posting's URL is ``https://www.<company>/jobs?gh_jid=<id>``
and the board token is NOT a label of that host, so the URL alone does not
name the board). ``gigai scout new --yes`` assesses it from the index's
posting text (``ats_board``). The user then saves an answer and re-assesses
the job. Before the fix the re-assessment fetched the company page and
stored its navigation as the posting (``fetch_kind: generic``, no location).
After it, ``fetch_kind``, ``text_sha256``, the location and the stored text
are the ones the base assessment had, and the company page is never asked.

No request leaves the process: the board is put straight into the board
cache, the model is the fixture transport and the job fetch client is a
``MockTransport`` that records what was asked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs import bindings, job_input, job_source
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, ResolvedJob
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import FindJobsContractError, normalize_url
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.quick_assess import find_quick_assessment_by_job_identity

from tests.api_e2e.harness import TEST_HTTP_ENV, TEST_MODEL_ENV, add_resume, setup_and_init, write_offline_find_jobs_config

_TOKEN = "sentrylabs"  # the board token: not a label of the company site's host
_JOB_ID = "7819844003"
_URL = f"https://www.sentryone-example.test/jobs?gh_jid={_JOB_ID}"
_LOCATION = "United States - Remote"
_POSTING_HTML = (
    "<p>SentryOne Example is hiring a Software Engineer to build reliable Python services.</p>"
    "<ul><li>Python in production.</li><li>GCP experience is a plus.</li><li>Docker, Helm and Kubernetes.</li></ul>"
    "<p>Remote within the United States.</p>"
)
_NAV = "Skip to main content"
_COMPANY_PAGE = (
    "<html><head><title>Careers at SentryOne Example</title></head><body>"
    f"<a href='#main'>{_NAV}</a>"
    "<header><div class='banner'>Join us at ExampleCon 2026: register now for the keynote</div>"
    "<nav><ul><li>Platform</li><li>Why SentryOne</li><li>Services</li><li>Partners</li><li>Resources</li><li>About</li></ul></nav></header>"
    "<main><h1>Software Engineer</h1><p>SentryOne Example is hiring a Software Engineer to build reliable Python services. "
    "Python in production. GCP experience is a plus. Docker, Helm and Kubernetes. Remote within the United States. "
    + "We value curiosity, ownership and kindness in everything we build together. " * 6
    + "</p></main><footer><ul><li>Privacy notice</li><li>Cookie settings</li><li>Legal</li></ul></footer></body></html>"
)


def _seed_index(home: Path, target: Path) -> None:
    seen = datetime.now(UTC) - timedelta(days=1)
    jobs = {"jobs": [{
        "id": int(_JOB_ID), "title": "Software Engineer", "absolute_url": _URL, "location": {"name": _LOCATION},
        "updated_at": seen.strftime("%Y-%m-%dT%H:%M:%SZ"), "first_published": seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "company_name": "SentryOne Example", "content": _POSTING_HTML,
    }]}
    add_company_from_url(f"https://boards.greenhouse.io/{_TOKEN}", home, target)
    cache = BoardCache(home / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    cache.store("greenhouse", board_list_url("greenhouse", _TOKEN), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home), cache, ats="greenhouse", slug=_TOKEN, observed_at=index_stamp(seen))


def _record_requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The fixture model for assess; every job fetch answered here and written down."""

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "www.sentryone-example.test":
            return httpx.Response(200, text=_COMPANY_PAGE, headers={"content-type": "text/html; charset=utf-8"}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setenv(TEST_MODEL_ENV, "1")
    monkeypatch.setenv(TEST_HTTP_ENV, "1")
    monkeypatch.setattr(bindings, "_test_provider_handler", handler)
    return asked


def _json(result) -> dict:
    """The command's JSON: the last line (a batch says its progress first)."""

    return json.loads(result.output.strip().splitlines()[-1])


def _stored(home: Path, target: Path):
    item = find_quick_assessment_by_job_identity(home, target, normalize_url(_URL))
    assert item is not None
    return item


def test_answer_then_reassess_keeps_fetch_kind_and_text_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    _seed_index(home, target)
    asked = _record_requests(monkeypatch)
    base_args = ["--home", str(home), "--target", str(target), "--json"]

    # 1. The batch: assessed from the index's posting text.
    batch = runner.invoke(cli, ["scout", "new", "--yes", *base_args])
    assert batch.exit_code == 0, batch.output
    assert _json(batch)["assessed"]["assessed"] == 1, batch.output
    base = _stored(home, target)
    assert (base.job.fetch_kind, base.job.location) == ("ats_board", _LOCATION)
    assert _NAV not in (base.posting_text or "") and "Helm" in (base.posting_text or "")

    # 2. An answer is saved and the job is re-assessed (the posting did not change).
    answered = runner.invoke(
        cli, ["scout", "answer", "cloud:gcp", "--answer-text", "Yes, two years on GCP.", "--reassess", _URL, *base_args]
    )
    assert answered.exit_code == 0, answered.output
    reassessed = _json(answered)["reassessed"]["job"]
    after = _stored(home, target)

    # 3. The end outcome: the same posting, read the same way.
    assert (reassessed["fetch_kind"], reassessed["text_sha256"]) == (base.job.fetch_kind, base.job.text_sha256)
    assert (after.job.fetch_kind, after.job.text_sha256, after.job.location) == ("ats_board", base.job.text_sha256, _LOCATION)
    assert after.posting_text == base.posting_text and _NAV not in (after.posting_text or "")
    assert [entry.trigger for entry in after.history] == ["assess", "reassess"]
    assert asked == []  # the company page was never scraped, and nothing else was asked


def test_a_company_site_gh_jid_url_is_read_from_the_index_posting_on_a_first_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0110-10-03 item 4: the job page's own Assess of the company-site URL joins the index posting too."""

    home, target = setup_and_init(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    _seed_index(home, target)
    asked = _record_requests(monkeypatch)
    listed = runner.invoke(cli, ["scout", "new", "--no-assess", "--home", str(home), "--target", str(target), "--json"])
    assert listed.exit_code == 0, listed.output

    assessed = runner.invoke(cli, ["scout", "assess", "--job-url", _URL, "--home", str(home), "--target", str(target), "--json"])

    assert assessed.exit_code == 0, assessed.output
    job = _json(assessed)["job"]
    assert (job["fetch_kind"], job["location"], job["job_identity"]) == ("ats_board", _LOCATION, normalize_url(_URL))
    assert asked == []


def test_a_generic_page_is_the_posting_without_the_site_navigation() -> None:
    """A page that is the only source: the header, navigation, footer and skip link are not the posting."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_COMPANY_PAGE, headers={"content-type": "text/html; charset=utf-8"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resolved = job_input.resolve_job(AssessJobInput(job_url="https://careers.unknown-example.test/jobs/9"), client=client)

    assert resolved.fetch_kind == "generic"
    assert resolved.text.startswith("Software Engineer") and "Docker, Helm and Kubernetes" in resolved.text
    for boilerplate in (_NAV, "ExampleCon", "Why SentryOne", "Cookie settings"):
        assert boilerplate not in resolved.text


# --- the order of sources (``job_source.resolve_job_for_assessment``) -------------------------


def _job(kind: str, text: str = "The board's own posting text.") -> ResolvedJob:
    return ResolvedJob(
        job_identity=normalize_url(_URL), source_url=_URL, normalized_url=normalize_url(_URL), fetch_kind=kind,
        title="Software Engineer", company=_TOKEN, location=_LOCATION, text=text, text_sha256=job_input._text_digest(text),
    )


class _NoRequest:
    def __enter__(self) -> "_NoRequest":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stored: ResolvedJob | None, indexed: ResolvedJob | None, fetched: object) -> tuple[ResolvedJob, list[str]]:
    calls: list[str] = []

    def index_posting(*_args: object, **_kwargs: object) -> ResolvedJob | None:
        calls.append("index")
        return indexed

    def resolve(_job_input: AssessJobInput, _client: object) -> ResolvedJob:
        calls.append("url")
        if isinstance(fetched, Exception):
            raise fetched
        return fetched  # type: ignore[return-value]

    monkeypatch.setattr(job_source, "stored_ats_posting", lambda *_args, **_kwargs: stored)
    monkeypatch.setattr(job_source, "index_posting", index_posting)
    found = job_source.resolve_job_for_assessment(
        AssessJobInput(job_url=_URL), home_root=tmp_path, target=tmp_path, open_client=_NoRequest, resolve=resolve  # type: ignore[arg-type]
    )
    return found, calls


def test_the_index_posting_is_read_before_the_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found, calls = _resolve(monkeypatch, tmp_path, stored=_job("ats_board"), indexed=_job("ats_board", "The posting as the index has it now."), fetched=_job("generic"))
    assert (found.fetch_kind, found.text, calls) == ("ats_board", "The posting as the index has it now.", ["index"])


def test_a_page_scrape_never_replaces_the_board_text_an_assessment_was_made_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The index no longer holds the posting and the URL is a company page: the stored board text is used again."""

    stored = _job("ats_board")
    found, calls = _resolve(monkeypatch, tmp_path, stored=stored, indexed=None, fetched=_job("generic", "Skip to main content. Menus."))
    assert (found, calls) == (stored, ["index", "url"])


def test_a_posting_the_index_does_not_hold_is_read_from_its_board_by_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = _job("ats_single", "The posting as the board serves it now.")
    found, calls = _resolve(monkeypatch, tmp_path, stored=_job("ats_single"), indexed=None, fetched=fresh)
    assert (found, calls) == (fresh, ["index", "url"])


def test_a_page_is_the_posting_only_when_nothing_else_is_known(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _job("generic", "A posting on a company's own careers page.")
    found, calls = _resolve(monkeypatch, tmp_path, stored=None, indexed=None, fetched=page)
    assert (found, calls) == (page, ["index", "url"])


def test_a_failed_fetch_is_still_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is assessed from old text because a board did not answer."""

    with pytest.raises(FindJobsContractError) as excinfo:
        _resolve(monkeypatch, tmp_path, stored=_job("ats_single"), indexed=None, fetched=FindJobsContractError("job_fetch_failed", "fetching the job failed"))
    assert excinfo.value.code == "job_fetch_failed"
