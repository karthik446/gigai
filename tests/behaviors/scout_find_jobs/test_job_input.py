"""P4: one job URL (or pasted text) resolves to posting text, offline.

Every network call goes through ``httpx.MockTransport``; the handler records
the request sequence so each test asserts WHICH endpoints were tried, not
just the final text.  Plan: ``PLAN-api-first-granular.md`` §P4, operator
answer 6 (Greenhouse single-job endpoint first, board listing as fallback).
"""

from __future__ import annotations

import json

import httpx
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.find_jobs.contracts import FindJobsContractError, normalize_url
from gigai.scout.find_jobs.job_input import (
    MAX_BODY_BYTES,
    MIN_POSTING_TEXT_CHARS,
    job_fetch_client,
    resolve_job,
)


_GENERIC_HTML = (
    "<html><head><title>Backend Engineer &amp; Platform - Example Careers</title>"
    "<script>window.tracker = 'do-not-show';</script></head><body>"
    "<h1>Backend Engineer</h1>"
    "<p>Example Corp builds reliable Python services for a growing customer base. "
    "We are hiring a backend engineer to own our ingestion pipeline end to end.</p>"
    "<ul><li>Design and operate HTTP services in Python.</li>"
    "<li>Own reliability: tracing, alerting, and on-call for what you build.</li>"
    "<li>Review code and mentor engineers across the platform group.</li>"
    "<li>Five or more years building production backend systems.</li>"
    "<li>Depth in Python and PostgreSQL; comfort with distributed systems.</li></ul>"
    "<p>Example Corp is unable to sponsor visas for this position.</p>"
    "</body></html>"
)
_JS_SHELL_HTML = (
    "<!doctype html><html><head><title>Job Application</title>"
    '<script src="https://boards.greenhouse.io/embed/job_app.js"></script>'
    "<script>window.__GH__={board:'acme'};</script></head>"
    '<body><div id="app"></div></body></html>'
)
_GH_CONTENT = (
    "&lt;p&gt;Acme builds reliable Python services.&lt;/p&gt;"
    "&lt;ul&gt;&lt;li&gt;Own the ingestion pipeline.&lt;/li&gt;"
    "&lt;li&gt;Operate services in production.&lt;/li&gt;&lt;/ul&gt;"
)


def _gh_job(job_id: int, title: str) -> dict:
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "location": {"name": "Denver, CO"},
        "updated_at": "2026-09-22T00:00:00Z",
        "content": _GH_CONTENT,
    }


class _Fixture:
    """A recording MockTransport handler over a small fake ATS/career-site world."""

    def __init__(self, *, single_job_status: int = 200, board_status: int = 200, page_status: int = 200) -> None:
        self.requests: list[str] = []
        self.single_job_status = single_job_status
        self.board_status = board_status
        self.page_status = page_status

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=True, max_redirects=5)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.host}{request.url.path}")
        host, path = request.url.host, request.url.path
        if host == "boards-api.greenhouse.io" and path == "/v1/boards/acme/jobs/101":
            if self.single_job_status != 200:
                return httpx.Response(self.single_job_status, json={"error": "gone"}, request=request)
            job = _gh_job(101, "Software Engineer")
            job["company_name"] = "Acme"
            return httpx.Response(200, json=job, request=request)
        if host == "boards-api.greenhouse.io" and path == "/v1/boards/acme/jobs/202":
            return httpx.Response(404, json={"error": "not found"}, request=request)
        if host == "boards-api.greenhouse.io" and path == "/v1/boards/acme/jobs":
            if self.board_status != 200:
                return httpx.Response(self.board_status, json={"error": "down"}, request=request)
            return httpx.Response(
                200, json={"jobs": [_gh_job(101, "Software Engineer"), _gh_job(202, "Staff Platform Engineer")]}, request=request
            )
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}:
            if self.page_status != 200:
                return httpx.Response(self.page_status, text="<html>maintenance</html>", request=request)
            return httpx.Response(200, text=_JS_SHELL_HTML, headers={"content-type": "text/html"}, request=request)
        if host == "api.lever.co" and path == "/v0/postings/acme":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "0b1c2d3e-aaaa-4bbb-8ccc-000000000001",
                        "text": "Staff Backend Engineer",
                        "hostedUrl": "https://jobs.lever.co/acme/0b1c2d3e-aaaa-4bbb-8ccc-000000000001?lever-source=LinkedIn",
                        "categories": {"location": "Remote - US"},
                        "createdAt": 1758499200000,
                        "descriptionPlain": "Acme is hiring a staff backend engineer to lead the data platform.",
                    }
                ],
                request=request,
            )
        if host == "careers.example.test":
            if path == "/jobs/9":
                return httpx.Response(200, text=_GENERIC_HTML, headers={"content-type": "text/html; charset=utf-8"}, request=request)
            if path == "/old/9":
                return httpx.Response(302, headers={"location": "https://careers.example.test/jobs/9"}, request=request)
            if path == "/huge":
                body = b"<html><body><p>" + (b"posting text " * (3 * 1024 * 1024 // 13)) + b"</p></body></html>"
                return httpx.Response(200, content=body, headers={"content-type": "text/html"}, request=request)
            if path == "/empty":
                return httpx.Response(200, text="<html><body><div id='root'></div><script>boot()</script></body></html>", request=request)
            if path == "/down":
                return httpx.Response(503, text="<html>secret body text 12345</html>", request=request)
            if path == "/boom":
                raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404, json={"error": "no fixture route"}, request=request)


# --- pasted text ------------------------------------------------------------


def test_pasted_text_resolves_without_any_network_call() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    text = "  Staff Engineer at Example.\n\nOwn the platform.  "
    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        resolved = resolve_job(AssessJobInput(job_text=text), client=client)

    stripped = text.strip()
    digest = digest_imported_bytes(stripped.encode("utf-8"))
    assert resolved.fetch_kind == "pasted"
    assert resolved.text == stripped
    assert resolved.text_sha256 == digest
    assert resolved.job_identity == "text:" + digest
    assert resolved.source_url is None and resolved.normalized_url is None
    assert (resolved.title, resolved.company, resolved.location) == ("", "", "")


def test_whitespace_only_pasted_text_is_job_text_unavailable() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_text="   \n\t"), client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))))
    assert excinfo.value.code == "job_text_unavailable"


# --- Greenhouse ----------------------------------------------------------------


def test_greenhouse_url_uses_the_single_job_endpoint_first() -> None:
    fixture = _Fixture()
    url = "https://boards.greenhouse.io/acme/jobs/101?gh_src=abc123"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)

    assert fixture.requests == ["GET boards-api.greenhouse.io/v1/boards/acme/jobs/101"]
    assert resolved.fetch_kind == "ats_single"
    assert resolved.title == "Software Engineer"
    assert resolved.company == "Acme"
    assert resolved.location == "Denver, CO"
    assert resolved.text == "Acme builds reliable Python services.\nOwn the ingestion pipeline.\nOperate services in production."
    assert resolved.text_sha256 == digest_imported_bytes(resolved.text.encode("utf-8"))
    assert resolved.source_url == url
    assert resolved.normalized_url == normalize_url(url) == "https://boards.greenhouse.io/acme/jobs/101"
    assert resolved.job_identity == resolved.normalized_url


def test_greenhouse_js_shell_falls_back_to_the_board_listing() -> None:
    """Single-job 404 + a script-only page -> the board row matched by URL."""

    fixture = _Fixture()
    url = "https://boards.greenhouse.io/acme/jobs/202"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)

    assert fixture.requests == [
        "GET boards-api.greenhouse.io/v1/boards/acme/jobs/202",
        "GET boards.greenhouse.io/acme/jobs/202",
        "GET boards-api.greenhouse.io/v1/boards/acme/jobs",
    ]
    assert resolved.fetch_kind == "ats_board"
    assert resolved.title == "Staff Platform Engineer"
    assert resolved.company == "acme"
    assert resolved.location == "Denver, CO"
    assert "Own the ingestion pipeline." in resolved.text
    assert resolved.job_identity == resolved.normalized_url == url


def test_greenhouse_board_fallback_matches_by_job_id_across_hosts() -> None:
    """``job-boards.greenhouse.io`` vs the board's ``boards.greenhouse.io`` URLs
    differ by host; the numeric job id still finds the row."""

    fixture = _Fixture()
    url = "https://job-boards.greenhouse.io/acme/jobs/202"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)
    assert fixture.requests == [
        "GET boards-api.greenhouse.io/v1/boards/acme/jobs/202",
        "GET job-boards.greenhouse.io/acme/jobs/202",
        "GET boards-api.greenhouse.io/v1/boards/acme/jobs",
    ]
    assert resolved.fetch_kind == "ats_board"
    assert resolved.title == "Staff Platform Engineer"
    assert resolved.normalized_url == url  # identity is the URL the caller gave, normalized


def test_greenhouse_every_path_failing_is_job_fetch_failed_and_redacted() -> None:
    fixture = _Fixture(single_job_status=500, board_status=500, page_status=503)
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101"), client=client)
    assert excinfo.value.code == "job_fetch_failed"
    message = str(excinfo.value)
    assert "maintenance" not in message and "gone" not in message  # never a response body
    assert "--job-text" in message


def test_greenhouse_job_missing_from_board_is_job_text_unavailable() -> None:
    fixture = _Fixture()
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/303"), client=client)
    assert excinfo.value.code == "job_text_unavailable"
    assert "--job-text" in str(excinfo.value)


# --- Lever (no single-job JSON; board listing) -------------------------------


def test_lever_shell_falls_back_to_the_board_and_matches_the_uuid_slug_by_url() -> None:
    fixture = _Fixture()
    url = "https://jobs.lever.co/acme/0b1c2d3e-aaaa-4bbb-8ccc-000000000001"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)

    assert fixture.requests == [
        "GET jobs.lever.co/acme/0b1c2d3e-aaaa-4bbb-8ccc-000000000001",
        "GET api.lever.co/v0/postings/acme",
    ]
    assert resolved.fetch_kind == "ats_board"
    assert resolved.title == "Staff Backend Engineer"
    assert resolved.location == "Remote - US"
    assert "staff backend engineer" in resolved.text.lower()


# --- generic pages -------------------------------------------------------------


def test_generic_page_is_fetched_once_and_stripped() -> None:
    fixture = _Fixture()
    url = "https://careers.example.test/jobs/9?utm_source=newsletter"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)

    assert fixture.requests == ["GET careers.example.test/jobs/9"]
    assert resolved.fetch_kind == "generic"
    assert resolved.title == "Backend Engineer & Platform - Example Careers"
    assert (resolved.company, resolved.location) == ("", "")
    assert resolved.text.startswith("Backend Engineer & Platform - Example Careers\nBackend Engineer\nExample Corp builds reliable Python services")
    assert "do-not-show" not in resolved.text  # script bodies are dropped
    assert "<" not in resolved.text
    assert resolved.normalized_url == "https://careers.example.test/jobs/9"
    assert resolved.job_identity == resolved.normalized_url
    assert resolved.source_url == url


def test_redirect_is_followed_and_identity_stays_the_given_url() -> None:
    fixture = _Fixture()
    url = "https://careers.example.test/old/9"
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)

    assert fixture.requests == ["GET careers.example.test/old/9", "GET careers.example.test/jobs/9"]
    assert resolved.fetch_kind == "generic"
    assert resolved.text.startswith("Backend Engineer")
    assert resolved.source_url == url
    assert resolved.normalized_url == url == resolved.job_identity


def test_redirect_is_an_http_error_when_the_client_does_not_follow() -> None:
    fixture = _Fixture()
    with httpx.Client(transport=httpx.MockTransport(fixture), follow_redirects=False) as client:
        with pytest.raises(FindJobsContractError) as excinfo:
            resolve_job(AssessJobInput(job_url="https://careers.example.test/old/9"), client=client)
    assert excinfo.value.code == "job_fetch_failed"
    assert "HTTP 302" in str(excinfo.value)


def test_body_is_capped_at_two_mib() -> None:
    fixture = _Fixture()
    with fixture.client() as client:
        resolved = resolve_job(AssessJobInput(job_url="https://careers.example.test/huge"), client=client)
    assert resolved.fetch_kind == "generic"
    size = len(resolved.text.encode("utf-8"))
    assert MAX_BODY_BYTES - 64 <= size <= MAX_BODY_BYTES  # ~2 MiB kept, the third MiB dropped
    assert resolved.text.startswith("posting text posting text")


def test_script_only_page_on_a_non_ats_host_is_job_text_unavailable() -> None:
    fixture = _Fixture()
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="https://careers.example.test/empty"), client=client)
    assert excinfo.value.code == "job_text_unavailable"
    assert fixture.requests == ["GET careers.example.test/empty"]  # not an ATS host: no board fallback


def test_http_error_is_job_fetch_failed_without_the_body() -> None:
    fixture = _Fixture()
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="https://careers.example.test/down?token=SECRET"), client=client)
    assert excinfo.value.code == "job_fetch_failed"
    message = str(excinfo.value)
    assert "HTTP 503" in message and "careers.example.test" in message
    assert "secret body text" not in message and "SECRET" not in message


def test_network_error_is_job_fetch_failed() -> None:
    fixture = _Fixture()
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="https://careers.example.test/boom"), client=client)
    assert excinfo.value.code == "job_fetch_failed"
    assert "ConnectError" in str(excinfo.value)


def test_malformed_url_fails_closed_before_any_request() -> None:
    fixture = _Fixture()
    with fixture.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url="ftp://careers.example.test/jobs/9"), client=client)
    assert excinfo.value.code == "invalid_value"
    assert fixture.requests == []


# --- input shape ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"job_url": "https://careers.example.test/jobs/9", "job_text": "pasted"},
        {"job_url": "", "job_text": ""},
    ],
)
def test_both_or_neither_inputs_are_rejected(kwargs: dict) -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessJobInput(**kwargs)
    assert excinfo.value.code == "job_input_invalid"


# --- the shared child-process fixture (bindings) -----------------------------


def test_bindings_fixture_routes_serve_the_three_p4_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``job_fetch_client()`` under the test-HTTP seam sees the same fixtures the
    spawned run child does: a Greenhouse single job, a generic page, a JS shell."""

    from gigai.scout.find_jobs import bindings

    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    with job_fetch_client() as client:
        single = resolve_job(AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101"), client=client)
        generic = resolve_job(AssessJobInput(job_url="https://careers.example.test/jobs/9"), client=client)
        shell = client.get("https://boards.greenhouse.io/acme/jobs/101")
        # The pre-P4 host-only board route is untouched: the listing still serves job 101.
        board = json.loads(client.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").content)

    assert single.fetch_kind == "ats_single"
    assert single.company == "Acme" and single.text == "Build reliable Python services."
    assert generic.fetch_kind == "generic"
    assert generic.title == "Backend Engineer - Example Careers"
    assert len(generic.text) >= MIN_POSTING_TEXT_CHARS
    from gigai.scout.find_jobs.ats_board_clients import html_to_text

    assert shell.status_code == 200 and len(html_to_text(shell.text)) < MIN_POSTING_TEXT_CHARS
    assert [job["id"] for job in board["jobs"]] == ["101"]
    assert bindings._test_http_enabled() is True
