"""0.1.11.4 S3: the page fetch of a STORED posting URL never reaches this machine or its network. Synthetic only.

An assessment (or a tailoring) of a posting the index holds with no description, whose board gives none either, falls
through to the page at the posting's URL. That URL is the board's text, written by strangers; the page's body (up to
2 MB) is read and sent to the model. A stored URL at a private address, or one that redirects to one, would hand the
model a page only this machine can reach. The END outcomes, on a synthetic home and on ``resolve_job`` itself:

- a stored URL whose name resolves to ``127.0.0.1`` / ``10.x`` / ``169.254.169.254`` / ``::1``: the page is never
  requested, the model is never called, the assessment fails ``job_text_unavailable`` (the posting has no text here);
- a public stored URL that redirects to ``http://169.254.169.254/`` or to a name resolving to ``10.0.0.5``: the redirect
  is not followed, nothing is read;
- a chain of public hops works, each hop looked up once and connected to at its own vetted address; a sixth redirect
  stops the fetch; an ``http`` stored URL is refused; a name that answers public first and private after (rebinding)
  is looked up once and never connected to privately; the 2 MiB body cap holds;
- a URL the USER types (the posting store does not hold it) is fetched as it always was, a local-network one included.

No request and no name lookup leaves the process: ``httpx.MockTransport`` and ``tests.support.fake_dns``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.scout import postings, quick_assess, tailored_resume
from gigai.scout.find_jobs import job_input, job_source
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
from gigai.scout.find_jobs.contracts import FindJobsContractError, normalize_url
from gigai.scout.quick_assess import QuickAssessError, run_quick_assessment
from gigai.scout.tailored_resume import TailorError, TailorRequest, resolve_tailor_job

from tests.support.fake_dns import PUBLIC_V4, PUBLIC_V6, FakeDNS, install_fake_dns
from tests.support.greenhouse_fixtures import gh_job, seed_greenhouse
from tests.support.pipeline_fixtures import config as fixture_config
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago

#: The red run (HEAD has no flag): the stored fetch is then the plain one, and the end-outcome tests fail on the request made.
TRUST_STORED = getattr(job_input, "TRUST_STORED", None)

HOST = "careers.example-co.com"
STORED = f"https://{HOST}/careers/job-title/?gh_jid=8236603"
TOKEN = "examplesecurity"  # the board token: not a label of the company site's host
SECOND_HOST = "jobs.example-apply.com"
THIRD_HOST = "www.example-careers.org"
SECOND_V4 = "151.101.1.69"
THIRD_V4 = "8.8.4.4"
INTRANET = "intranet.example-co.com"
LOCAL = "http://nas.lan:8080/jobs/9"  # what a user may well type: a page on their own network
SECRET = "INTERNAL-ONLY-METADATA-TOKEN"
NOT_PUBLIC = ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.2", "172.16.0.9", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "100.64.0.1"]

_PAGE_TEXT = (
    "Staff AI Engineer. Own the Python inference services. Requirements: 5+ years of Python in production; Kubernetes; "
    "Terraform; GCP experience is a plus. Remote within the United States. "
    + "You will design, build and run the services our customers depend on every day. " * 6
)
_PAGE = f"<html><head><title>Staff AI Engineer - Example Co</title></head><body><main><h1>Staff AI Engineer</h1><p>{_PAGE_TEXT}</p></main></body></html>"


def _html(text: str = _PAGE) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


class _Web:
    """What stands in for every host. ``pages`` maps a posting host to its answer; anything else is the secret page (so a
    request that should never be made hands back text the test can look for)."""

    def __init__(self, pages: dict[str, object] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: dict[str, object] = dict(pages or {})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", "")
        if host == "boards-api.greenhouse.io":
            return httpx.Response(404, json={"error": "not found"})  # the board gives no description for this posting
        answer = self.pages.get(host)
        if callable(answer):
            return answer(request)  # type: ignore[no-any-return]
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, str):
            return httpx.Response(302, headers={"location": answer}, text=SECRET)
        return _html(f"<html><body><main><p>{SECRET} {_PAGE_TEXT}</p></main></body></html>")

    def client(self) -> httpx.Client:
        """As ``job_input.job_fetch_client``: it follows up to five redirects by itself."""

        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True, max_redirects=5, trust_env=False)

    @property
    def seen(self) -> list[str]:
        """Every request as ``<the URL's own host><path>``: an address for a pinned request, a name for a plain one."""

        return [f"{request.url.host}{request.url.path}" for request in self.requests]

    @property
    def pages_seen(self) -> list[str]:
        return [where for where, request in zip(self.seen, self.requests) if request.headers.get("host") != "boards-api.greenhouse.io"]


def _stored_fetch(web: _Web, url: str = STORED) -> ResolvedJob:
    """``resolve_job`` as ``job_source`` calls it for a URL the posting store holds."""

    kwargs = {} if TRUST_STORED is None else {"trust": TRUST_STORED}
    with web.client() as client:
        return job_input.resolve_job(AssessJobInput(job_url=url), client=client, **kwargs)


def _refused(web: _Web, url: str = STORED) -> FindJobsContractError:
    try:
        found = _stored_fetch(web, url)
    except FindJobsContractError as exc:
        return exc
    raise AssertionError(f"the page was read: {web.seen}; {SECRET in found.text=}")


# --- the fetch itself: a stored URL that resolves to this machine or its network --------------------------------


@pytest.mark.parametrize("address", NOT_PUBLIC)
def test_a_stored_url_whose_name_resolves_to_a_non_public_address_is_never_requested(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [address]})
    web = _Web()

    error = _refused(web)

    assert web.seen == [], f"a request was made to a name that resolves to {address}"
    assert error.code == "job_text_unavailable" and SECRET not in str(error) and address not in str(error)
    assert dns.calls == [HOST]


def test_one_private_answer_among_public_ones_refuses_the_stored_url(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4, "10.0.0.5"]})
    web = _Web()

    assert _refused(web).code == "job_text_unavailable"
    assert web.seen == []


@pytest.mark.parametrize("url", [
    f"http://{HOST}/careers/job-title/?gh_jid=8236603", f"https://{HOST}:8443/jobs/9", "https://127.0.0.1/jobs/9",
    "https://[::1]/jobs/9", "http://169.254.169.254/latest/meta-data/", "https://localhost/jobs/9", "https://jobs.corp.internal/jobs/9", LOCAL,
])
def test_a_stored_url_that_is_not_plain_https_at_a_public_looking_name_is_refused_without_a_lookup(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    dns = install_fake_dns(monkeypatch, default=[PUBLIC_V4])
    web = _Web()

    assert _refused(web, url).code == "job_text_unavailable"
    assert web.seen == [] and dns.calls == []


# --- every redirect hop is vetted the same way ---------------------------------------------------------------------


@pytest.mark.parametrize("location", [
    "http://169.254.169.254/latest/meta-data/", f"https://{INTRANET}/admin", f"http://{SECOND_HOST}/jobs/9", "https://10.0.0.5/admin",
    f"https://{SECOND_HOST}:8443/jobs/9", f"https://user:secret@{SECOND_HOST}/jobs/9", "//169.254.169.254/latest/", "https://[::1]/x", "file:///etc/hosts",
])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_of_a_public_stored_url_to_a_refused_address_is_not_followed(monkeypatch: pytest.MonkeyPatch, status: int, location: str) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4], SECOND_HOST: [SECOND_V4], INTRANET: ["10.0.0.5"]})
    web = _Web({HOST: httpx.Response(status, headers={"location": location}, text=SECRET)})

    error = _refused(web)

    assert web.seen == [f"{PUBLIC_V4}/careers/job-title/"], "the redirect was followed"
    assert error.code == "job_text_unavailable" and SECRET not in str(error)
    assert dns.calls in ([HOST], [HOST, INTRANET])  # only a redirect to a plain https name is looked up at all


def test_a_chain_of_public_hops_is_followed_and_each_hop_is_connected_to_at_its_own_vetted_address(monkeypatch: pytest.MonkeyPatch) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4], SECOND_HOST: [SECOND_V4, PUBLIC_V6], THIRD_HOST: [THIRD_V4]})
    web = _Web({HOST: f"https://{SECOND_HOST}/go?to=9", SECOND_HOST: "/jobs/9", THIRD_HOST: _html()})
    web.pages[SECOND_HOST] = lambda request: (
        httpx.Response(302, headers={"location": "/moved?to=9"}) if request.url.path == "/go" else httpx.Response(307, headers={"location": f"https://{THIRD_HOST}/jobs/9#apply"})
    )

    found = _stored_fetch(web)

    assert [(str(request.url), request.headers["host"], request.extensions.get("sni_hostname")) for request in web.requests] == [
        (f"https://{PUBLIC_V4}/careers/job-title/?gh_jid=8236603", HOST, HOST),
        (f"https://{SECOND_V4}/go?to=9", SECOND_HOST, SECOND_HOST),
        (f"https://{SECOND_V4}/moved?to=9", SECOND_HOST, SECOND_HOST),  # a relative redirect stays on the posting's host, vetted again
        (f"https://{THIRD_V4}/jobs/9", THIRD_HOST, THIRD_HOST),
    ]
    assert dns.calls == [HOST, SECOND_HOST, SECOND_HOST, THIRD_HOST]  # one lookup per hop, none by the transport
    assert (found.fetch_kind, found.title, found.job_identity) == ("generic", "Staff AI Engineer - Example Co", normalize_url(STORED))
    assert "Kubernetes" in found.text and SECRET not in found.text


def test_five_redirects_are_followed_and_a_sixth_stops_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4]})
    hops: list[int] = []

    def page(limit: int):  # type: ignore[no-untyped-def]
        def answer(_request: httpx.Request) -> httpx.Response:
            hops.append(len(hops))
            return httpx.Response(302, headers={"location": f"/hop/{len(hops)}"}) if len(hops) <= limit else _html()
        return answer

    web = _Web({HOST: page(5)})
    assert "Kubernetes" in _stored_fetch(web).text
    assert len(web.requests) == 6

    hops.clear()
    web = _Web({HOST: page(6)})
    error = _refused(web)
    assert len(web.requests) == 6, "a sixth redirect was followed"
    assert error.code == "job_fetch_failed" and "too many redirects" in str(error)


def test_a_name_that_answers_public_first_and_private_after_is_looked_up_once_and_never_connected_to_privately(monkeypatch: pytest.MonkeyPatch) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: lambda seen: [PUBLIC_V4] if seen == 0 else ["127.0.0.1"]})
    web = _Web({HOST: _html()})

    found = _stored_fetch(web)

    assert dns.calls == [HOST], "the name was looked up more than once"
    assert [request.url.host for request in web.requests] == [PUBLIC_V4]
    assert "Kubernetes" in found.text


def test_a_redirect_back_to_a_name_that_now_answers_privately_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rebinding across hops: the second hop is the same name, looked up again and refused; nothing connects to ``127.0.0.1``."""

    dns = install_fake_dns(monkeypatch, {HOST: lambda seen: [PUBLIC_V4] if seen == 0 else ["127.0.0.1"]})
    web = _Web({HOST: f"https://{HOST}/admin"})

    error = _refused(web)

    assert error.code == "job_text_unavailable"
    assert [request.url.host for request in web.requests] == [PUBLIC_V4] and dns.calls == [HOST, HOST]


def test_the_body_cap_holds_for_a_stored_url(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4]})
    web = _Web({HOST: httpx.Response(200, content=b"posting text " * (3 * 1024 * 1024 // 13), headers={"content-type": "text/plain; charset=utf-8"})})

    found = _stored_fetch(web)

    size = len(found.text.encode("utf-8"))
    assert job_input.MAX_BODY_BYTES - 64 <= size <= job_input.MAX_BODY_BYTES  # ~2 MiB kept, the third MiB dropped


@pytest.mark.parametrize(("answer", "code", "said"), [
    (httpx.Response(404, text="gone"), "job_fetch_failed", "HTTP 404"),
    (httpx.Response(302, text="no location"), "job_fetch_failed", "HTTP 302"),
    (httpx.Response(200, text="<html><body><script>app()</script></body></html>"), "job_text_unavailable", "no posting text"),
])
def test_a_public_stored_page_that_fails_or_is_empty_fails_as_any_page_does(monkeypatch: pytest.MonkeyPatch, answer: httpx.Response, code: str, said: str) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4]})
    web = _Web({HOST: answer})

    error = _refused(web)

    assert (error.code, said in str(error)) == (code, True)
    assert len(web.requests) == 1


def test_a_stored_page_that_does_not_answer_is_a_failed_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4]})

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    error = _refused(_Web({HOST: down}))

    assert error.code == "job_fetch_failed" and "network error (ConnectError)" in str(error)


def test_an_unknown_trust_is_refused_before_any_request() -> None:
    web = _Web()

    with web.client() as client, pytest.raises(ValueError):
        job_input.resolve_job(AssessJobInput(job_url=STORED), client=client, trust="board")  # type: ignore[call-arg]
    assert web.seen == []


# --- a URL the USER types is fetched as it always was -------------------------------------------------------------


@pytest.mark.parametrize("url", [LOCAL, "http://192.168.1.20/careers/9", "https://jobs.corp.internal:8443/jobs/9", "http://localhost:3000/jobs/9"])
def test_a_url_the_user_types_on_their_own_network_is_still_fetched_as_given(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    dns = install_fake_dns(monkeypatch, default=["10.0.0.5"])
    web = _Web()

    with web.client() as client:
        found = job_input.resolve_job(AssessJobInput(job_url=url), client=client)

    assert [str(request.url) for request in web.requests] == [url]  # the URL as typed: no vetting, no address in its place
    assert web.requests[0].headers["host"] == httpx.URL(url).netloc.decode("ascii") and "sni_hostname" not in web.requests[0].extensions
    assert dns.calls == []
    assert found.fetch_kind == "generic" and SECRET in found.text


def test_a_url_the_user_types_still_has_its_redirects_followed_by_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    dns = install_fake_dns(monkeypatch, default=["10.0.0.5"])
    web = _Web({"nas.lan:8080": "http://printer.lan/jobs/9", "printer.lan": _html()})

    with web.client() as client:
        found = job_input.resolve_job(AssessJobInput(job_url=LOCAL), client=client)

    assert [str(request.url) for request in web.requests] == [LOCAL, "http://printer.lan/jobs/9"]
    assert dns.calls == [] and "Kubernetes" in found.text


# --- the END outcome: an assessment of a stored posting -----------------------------------------------------------


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web: _Web, *, url: str = STORED) -> PostingsFixture:
    """A home whose index holds ONE Greenhouse posting at ``url`` with no description; the board gives none either (404)."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    own = {**gh_job(TOKEN, 8236603, TITLE_BOTH), "absolute_url": url}
    seed_greenhouse(fx, TOKEN, [own], seen_at=days_ago(1))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    assert job_source.index_posting(fx.home_root, fx.target, normalize_url(url)) is None  # held, no text: the fixture's point
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(quick_assess, "job_fetch_client", web.client)
    monkeypatch.setattr(tailored_resume, "job_fetch_client", web.client)
    fx.base.model.assess_prompts.clear()
    return fx


def _assess(fx: PostingsFixture, url: str = STORED):  # type: ignore[no-untyped-def]
    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )


def _assessment_refused(fx: PostingsFixture, web: _Web, url: str = STORED) -> QuickAssessError:
    try:
        _assess(fx, url)
    except QuickAssessError as exc:
        return exc
    raise AssertionError(f"the posting was assessed from a page that must not be read: {web.pages_seen}; model calls {fx.base.model.calls}")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_assessing_a_stored_posting_whose_url_resolves_to_a_non_public_address_reads_no_page_and_calls_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    web = _Web()
    fx = _home(tmp_path, monkeypatch, web)
    dns = install_fake_dns(monkeypatch, {HOST: [address]})

    error = _assessment_refused(fx, web)

    assert web.pages_seen == [], f"the stored URL was requested though it resolves to {address}"
    assert web.seen == [f"boards-api.greenhouse.io/v1/boards/{TOKEN}/jobs/8236603"]  # the board's own API was asked, and gave nothing
    assert fx.base.model.calls == 0, "the model was called"
    assert (error.code, SECRET in str(error)) == ("job_text_unavailable", False)
    assert quick_assess.find_quick_assessment_by_job_identity(fx.home_root, fx.target, normalize_url(STORED)) is None
    assert dns.calls == [HOST]


@pytest.mark.parametrize("location", ["http://169.254.169.254/latest/meta-data/", f"https://{INTRANET}/admin"])
def test_assessing_a_stored_posting_whose_page_redirects_off_the_public_internet_follows_nothing_and_calls_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    web = _Web({HOST: location})
    fx = _home(tmp_path, monkeypatch, web)
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4], INTRANET: ["10.0.0.5"]})

    error = _assessment_refused(fx, web)

    assert web.pages_seen == [f"{PUBLIC_V4}/careers/job-title/"], "the redirect was followed"
    assert fx.base.model.calls == 0 and error.code == "job_text_unavailable"
    assert all(SECRET not in prompt for prompt in fx.base.model.assess_prompts)


def test_tailoring_a_stored_posting_whose_url_resolves_to_loopback_reads_no_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = _Web()
    fx = _home(tmp_path, monkeypatch, web)
    install_fake_dns(monkeypatch, {HOST: ["127.0.0.1"]})

    with pytest.raises(TailorError) as refused:
        resolve_tailor_job(
            TailorRequest(job=AssessJobInput(job_url=STORED), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
            home_root=fx.home_root, target=fx.target,
        )

    assert refused.value.code == "job_text_unavailable"
    assert web.pages_seen == [] and fx.base.model.calls == 0


def test_a_stored_posting_at_a_public_address_is_still_assessed_from_its_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = _Web({HOST: f"https://{SECOND_HOST}/jobs/9", SECOND_HOST: _html()})
    fx = _home(tmp_path, monkeypatch, web)
    dns = install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4], SECOND_HOST: [SECOND_V4]})

    done = _assess(fx)

    assert web.pages_seen == [f"{PUBLIC_V4}/careers/job-title/", f"{SECOND_V4}/jobs/9"]
    assert dns.calls == [HOST, SECOND_HOST]
    assert done.job.fetch_kind == "generic" and fx.base.model.calls == 1
    assert "Kubernetes" in fx.base.model.assess_prompts[-1] and SECRET not in fx.base.model.assess_prompts[-1]


def test_a_local_network_url_the_user_types_is_still_fetched_and_assessed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same home, a URL the posting store does not hold: the user's own input, fetched as given."""

    web = _Web({"nas.lan:8080": _html()})
    fx = _home(tmp_path, monkeypatch, web)
    dns = install_fake_dns(monkeypatch, default=["10.0.0.5"])

    done = _assess(fx, LOCAL)

    assert [str(request.url) for request in web.requests] == [LOCAL]
    assert dns.calls == []
    assert done.job.fetch_kind == "generic" and fx.base.model.calls == 1 and "Kubernetes" in fx.base.model.assess_prompts[-1]


# --- which branch sets the flag (``job_source``) ------------------------------------------------------------------


def _trust_given(web: _Web, home: Path, target: Path, url: str) -> list[dict[str, object]]:
    given: list[dict[str, object]] = []

    def resolve(job: AssessJobInput, _client: object, **stored: object):  # type: ignore[no-untyped-def]
        given.append(stored)
        return job_input.resolve_job(AssessJobInput(job_text="A posting's text.", title=job.title), client=None)  # type: ignore[arg-type]

    job_source.resolve_job_for_assessment(AssessJobInput(job_url=url), home_root=home, target=target, open_client=web.client, resolve=resolve)
    return given


def test_only_a_url_the_posting_store_holds_is_fetched_as_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = _Web()
    fx = _home(tmp_path, monkeypatch, web)
    install_fake_dns(monkeypatch)

    assert _trust_given(web, fx.home_root, fx.target, STORED) == [{"trust": "stored"}]
    assert _trust_given(web, fx.home_root, fx.target, STORED + "&utm_source=share") == [{"trust": "stored"}]  # the same posting, by its identity
    assert _trust_given(web, fx.home_root, fx.target, LOCAL) == [{}]
    assert _trust_given(web, fx.home_root, fx.target, f"https://{HOST}/careers/another-job/") == [{}]
    assert job_source.index_holds(fx.home_root, fx.target, normalize_url(STORED)) is True
    assert job_source.index_holds(fx.home_root, fx.target, normalize_url(LOCAL)) is False


def test_a_folder_with_no_posting_store_holds_nothing_and_nothing_is_created_for_the_question(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))

    assert job_source.index_holds(tmp_path, tmp_path, normalize_url(STORED)) is False
    assert _trust_given(_Web(), tmp_path, tmp_path, STORED) == [{}]
    assert sorted(tmp_path.rglob("*")) == before


def test_fake_dns_is_the_only_resolver_these_tests_reach() -> None:
    dns = FakeDNS()

    with pytest.raises(OSError):
        dns(HOST, 443)
    assert dns.calls == [HOST]
