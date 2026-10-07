"""0.1.11.4 S2: a request built from a stored posting URL is never steered at this machine or its network. Synthetic only.

A posting's URL is the board's text, written by strangers. The company-page check (7b) makes ONE ``GET`` to it. A
public-looking name can RESOLVE to ``127.0.0.1``, a ``10.x`` host or the cloud metadata address, and can answer one
address to a check and another to the connection (rebinding). The END outcomes, on ``check_company_page`` and on the job
read's liveness:

- a name resolving to a loopback / private / link-local / CGNAT / unique-local / mapped address: ``unknown``, the
  transport is NEVER called, no cache file is written;
- one private answer among public ones: the same; a name that does not resolve: the same;
- a public answer: the request goes to THAT address (the URL's host is the address; ``Host`` and the TLS server name
  stay the posting's host), after exactly one lookup, also when a second lookup would have said ``127.0.0.1``;
- a redirect is never followed; a board token or posting id that is not a plain name never reaches a board's endpoint.

No request and no name lookup leaves the process: ``httpx.MockTransport`` (or a fake ``httpcore`` network backend)
and ``tests.support.fake_dns``.
"""

from __future__ import annotations

from pathlib import Path
import ssl

import httpcore
import httpx
import pytest

from gigai.scout import postings
from gigai.scout.find_jobs import job_input, posting_live
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.find_jobs.contracts import FindJobsContractError

from tests.support.fake_dns import PUBLIC_V4, PUBLIC_V6, FakeDNS, install_fake_dns
from tests.support.greenhouse_fixtures import gh_detail, gh_job, posting_text, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, build_postings_fixture, days_ago

try:  # the red run (HEAD has no guard): the end-outcome tests then fail on the request that was made
    from gigai.scout.find_jobs import outbound_guard
except ImportError:  # pragma: no cover - only at a commit before the guard
    outbound_guard = None  # type: ignore[assignment]

HOST = "careers.example-co.com"
PAGE = f"https://{HOST}/careers/job-title/?gh_jid=8236603"
_UPDATED = "2026-10-02T09:00:00Z"

NOT_PUBLIC = [
    "127.0.0.1", "10.0.0.5", "172.16.0.9", "192.168.1.2", "169.254.169.254", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "100.64.0.1",
    "0.0.0.0", "::", "224.0.0.1", "ff02::1", "240.0.0.1", "255.255.255.255", "::ffff:10.0.0.5", "::ffff:169.254.169.254", "fd12:3456:789a::1",
    "fec0::1", "2002:7f00:1::1", "2002:a00:5::1", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "192.0.2.1", "198.18.0.1", "2001:db8::1",
    "2002:808:808::1", "64:ff9b::808:808",  # 6to4 / NAT64 of a public IPv4 address: refused whatever they carry
]


class _Site:
    """What stands in for every host: counts the requests it is given and answers ``status``."""

    def __init__(self, status: int = 404, *, location: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.location = location

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("host") == "boards-api.greenhouse.io":
            token, job_id = request.url.path.strip("/").split("/")[2], int(request.url.path.rstrip("/").split("/")[-1])
            return httpx.Response(200, json=gh_detail(gh_job(token, job_id, TITLE_BOTH), posting_text(job_id)))
        return httpx.Response(self.status, headers={"location": self.location} if self.location else None, text="<html></html>")

    def client(self, *, follow_redirects: bool = False) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=follow_redirects)

    @property
    def seen(self) -> list[str]:
        return [f"{request.url.host}{request.url.path}" for request in self.requests]


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> _Site:
    web = _Site()
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")  # the suite switches the check off; these flows are about it
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    monkeypatch.setattr(posting_live, "liveness_client", lambda: web.client())
    posting_live.reset_memory()
    return web


def _cache_files(home: Path) -> list[str]:
    return sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())


# --- a name that resolves to this machine or its network is never requested ---------------------------------


@pytest.mark.parametrize("address", NOT_PUBLIC)
def test_a_public_looking_name_that_resolves_to_a_non_public_address_is_never_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, address: str
) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [address]})

    answer = posting_live.check_company_page(tmp_path, url=PAGE, now=NOW)

    assert site.seen == [], f"a request was made to a name that resolves to {address}"
    assert answer == "unknown"
    assert _cache_files(tmp_path) == [], "a refused address wrote a file"
    assert dns.calls == [HOST]


@pytest.mark.parametrize("answers", [
    [PUBLIC_V4, "10.0.0.5"], ["127.0.0.1", PUBLIC_V4], [PUBLIC_V4, PUBLIC_V6, "169.254.169.254"], [PUBLIC_V6, "::1"], [PUBLIC_V4, "fe80::1"],
])
def test_one_private_answer_among_public_ones_refuses_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, answers: list[str]
) -> None:
    install_fake_dns(monkeypatch, {HOST: answers})

    assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == "unknown"
    assert site.seen == [] and _cache_files(tmp_path) == []


def test_a_name_that_does_not_resolve_is_unknown_and_is_never_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site) -> None:
    dns = install_fake_dns(monkeypatch)  # every name: no such host

    assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == "unknown"
    assert site.seen == [] and _cache_files(tmp_path) == []
    assert dns.calls == [HOST]


def test_a_resolver_that_does_not_answer_in_time_is_unknown_and_is_never_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site) -> None:
    import threading

    release = threading.Event()

    def hangs(_seen: int) -> list[str]:
        release.wait(30)
        return [PUBLIC_V4]

    install_fake_dns(monkeypatch, {HOST: hangs})
    monkeypatch.setattr(outbound_guard, "RESOLVE_TIMEOUT_SECONDS", 0.05)
    try:
        assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == "unknown"
        assert site.seen == []
    finally:
        release.set()


def test_a_refused_name_is_not_looked_up_again_within_the_hour_in_one_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: ["10.0.0.5"]})

    for _ in range(3):
        assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == "unknown"

    assert dns.calls == [HOST] and site.seen == [] and _cache_files(tmp_path) == []


# --- a public answer: the request goes to the vetted address, and nothing resolves the name again -------------


@pytest.mark.parametrize(("address", "status", "expected"), [(PUBLIC_V4, 404, "down"), (PUBLIC_V4, 200, "ok"), (PUBLIC_V6, 410, "down"), ("::ffff:93.184.216.34", 200, "ok")])
def test_a_public_address_is_the_one_connected_to_and_the_host_and_tls_name_stay_the_postings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, address: str, status: int, expected: str
) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [address]})
    site.status = status

    assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == expected

    assert dns.calls == [HOST], "the name was looked up more than once"
    [request] = site.requests
    assert request.method == "GET"
    assert request.url.scheme == "https" and request.url.host == address and request.url.port is None, request.url  # the connection's target
    assert request.url.path == "/careers/job-title/" and request.url.query == b"gh_jid=8236603"
    assert request.headers["host"] == HOST  # the site still sees its own name
    assert request.extensions["sni_hostname"] == HOST  # and the certificate is checked against it
    # The answer is kept the hour as before: no lookup and no request the second time, nor in a new process.
    posting_live.reset_memory()
    assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == expected
    assert dns.calls == [HOST] and len(site.requests) == 1


def test_a_name_that_answers_public_first_and_loopback_after_is_asked_once_and_connected_to_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site
) -> None:
    """Rebinding: the check and the connection never see two answers, because there is one lookup."""

    dns = install_fake_dns(monkeypatch, {HOST: lambda seen: [PUBLIC_V4] if seen == 0 else ["127.0.0.1"]})

    assert posting_live.check_company_page(tmp_path, url=PAGE, now=NOW) == "down"

    assert len(dns.calls) == 1, dns.calls
    assert [request.url.host for request in site.requests] == [PUBLIC_V4]


class _Network(httpcore.NetworkBackend):
    """The real ``httpx`` transport's network: records where a TCP connection is opened and what TLS is asked to verify."""

    def __init__(self) -> None:
        self.tcp: list[tuple[str, int]] = []
        self.tls: list[tuple[str | None, bool, ssl.VerifyMode]] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[no-untyped-def]
        self.tcp.append((host, port))
        return _Wire(self)


class _Wire(httpcore.NetworkStream):
    def __init__(self, network: _Network) -> None:
        self.network = network

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # type: ignore[no-untyped-def]
        self.network.tls.append((server_hostname, ssl_context.check_hostname, ssl_context.verify_mode))
        raise httpcore.ConnectError("the fake network ends at the handshake")

    def read(self, max_bytes, timeout=None):  # type: ignore[no-untyped-def]
        return b""

    def write(self, buffer, timeout=None):  # type: ignore[no-untyped-def]
        return None

    def close(self) -> None:
        return None

    def get_extra_info(self, info):  # type: ignore[no-untyped-def]
        return None


def test_the_real_transport_opens_its_connection_to_the_vetted_address_and_verifies_the_real_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site
) -> None:
    """Below ``httpx``: the product's own client, its real transport, a recording network. The name answers loopback to any later lookup."""

    dns = install_fake_dns(monkeypatch, {HOST: lambda seen: [PUBLIC_V4, PUBLIC_V6] if seen == 0 else ["127.0.0.1"]})
    network = _Network()
    real = httpx.Client(timeout=httpx.Timeout(posting_live.TIMEOUT_SECONDS, connect=posting_live.CONNECT_TIMEOUT_SECONDS), follow_redirects=False, trust_env=False)
    assert isinstance(real._transport, httpx.HTTPTransport)
    real._transport._pool = httpcore.ConnectionPool(ssl_context=httpx.create_ssl_context(verify=True, trust_env=False), network_backend=network)

    with real:
        answer = posting_live.check_company_page(tmp_path, url=PAGE, client=real, now=NOW)

    assert answer == "unknown"  # the fake network refuses the handshake
    assert network.tcp == [(PUBLIC_V4, 443)], "the connection was not opened to the vetted address"
    assert network.tls == [(HOST, True, ssl.CERT_REQUIRED)], "the certificate is not verified against the posting's host"
    assert len(dns.calls) == 1, dns.calls
    assert site.seen == []


# --- no redirect is followed ------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_unknown_and_is_never_followed_even_by_a_client_that_follows_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, status: int
) -> None:
    dns = install_fake_dns(monkeypatch, {HOST: [PUBLIC_V4], "intranet.example-co.com": ["10.0.0.5"]})
    site.status, site.location = status, "https://intranet.example-co.com/admin"

    with site.client(follow_redirects=True) as following:
        answer = posting_live.check_company_page(tmp_path, url=PAGE, client=following, now=NOW)

    assert answer == "unknown"
    assert site.seen == [f"{PUBLIC_V4}/careers/job-title/"], "the redirect was followed"
    assert dns.calls == [HOST]


# --- the job read: a stored posting whose URL resolves to this machine ----------------------------------------


def test_the_job_read_of_a_stored_posting_whose_url_resolves_to_loopback_asks_the_board_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    own = {**gh_job("examplesecurity", 8236603, TITLE_BOTH, updated_at=_UPDATED), "absolute_url": PAGE}
    seed_greenhouse(fx, "examplesecurity", [own], seen_at=days_ago(1), details={8236603: (posting_text(8236603), _UPDATED)})
    postings.refresh(fx.home_root, fx.target, now=NOW)
    web = _Site()
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(posting_live, "liveness_client", lambda: web.client())
    posting_live.reset_memory()
    install_fake_dns(monkeypatch, {HOST: ["127.0.0.1"]})
    store = postings.open_store(fx.home_root, fx.target)
    try:
        job = next(row.job for row in store.postings(live=False) if "example-co.com" in row.job)
    finally:
        store.close()

    live = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)

    assert web.seen == ["boards-api.greenhouse.io/v1/boards/examplesecurity/jobs/8236603"], "the stored URL was requested"
    assert (live.state, live.company_page, live.company_page_note) == ("open", "unknown", None)
    assert live.board_url == "https://job-boards.greenhouse.io/examplesecurity/jobs/8236603"  # the board link needs no request
    assert not (fx.home_root / "cache" / "scout" / "liveness" / "pages").exists()


# --- the rules themselves ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("address", NOT_PUBLIC + ["", "not-an-address", "fe80::1%en0", "127.1", "0x7f.0.0.1"])
def test_an_address_that_is_not_on_the_public_internet_is_not_public(address: str) -> None:
    assert outbound_guard.is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "::ffff:8.8.8.8"])
def test_a_public_address_is_public(address: str) -> None:
    assert outbound_guard.is_public_address(address) is True


@pytest.mark.parametrize("url", [
    f"http://{HOST}/x", f"https://{HOST}:8443/x", f"https://user:secret@{HOST}/x", f"https://user@{HOST}/x", "https://127.0.0.1/x", "https://[::1]/x",
    "https://2130706433/x", "https://localhost/x", "https://intranet/x", "https://jobs.corp.internal/x", "https://printer.local/x", "https://nas.lan/x",
    "ftp://example-co.com/x", "https:///x", "", "https://[not-an-address]/x",
])
def test_a_url_that_is_not_plain_https_at_a_public_looking_name_has_no_target_and_no_lookup(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    dns = install_fake_dns(monkeypatch, default=[PUBLIC_V4])

    assert outbound_guard.safe_public_target(url) is None
    assert dns.calls == []


def test_the_target_of_a_public_url_is_pinned_to_its_first_vetted_address(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_dns(monkeypatch, {HOST: [PUBLIC_V6, PUBLIC_V4]})

    target = outbound_guard.safe_public_target(f"https://{HOST.upper()}.:443/a%20b/?q=1#top")

    assert target is not None
    assert (target.host, target.address) == (HOST, PUBLIC_V6)
    assert target.pinned_url == f"https://[{PUBLIC_V6}]/a%20b/?q=1"


# --- a board token or posting id never changes a board's endpoint ---------------------------------------------

BAD_NAMES = ["evil.com/x", "../", "..", ".", "a@b", "a\nb", "a b", "a?b=c", "a#b", "a%2Fb", "a\\b", "@evil.com", "", "-a", "a/../../v2/x"]


@pytest.mark.parametrize("token", BAD_NAMES)
@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby"])
def test_a_board_token_that_is_not_a_plain_name_never_reaches_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, provider: str, token: str
) -> None:
    install_fake_dns(monkeypatch)
    url = "https://www.example-co.com/careers/job-title/?gh_jid=5"

    answer = posting_live.check_posting_live(tmp_path, url=url, provider=provider, token=token, posting_id="5", now=NOW)

    assert site.seen == [], f"a request was built from the token {token!r}"
    assert answer.state == "unknown"


@pytest.mark.parametrize("posting_id", ["5/../../../v2/x", "../5", "5?content=false", "5#x", "5@evil.com", "5\n6", "5 6", "evil.com/x"])
def test_a_greenhouse_posting_id_that_is_not_a_plain_name_never_reaches_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site, posting_id: str
) -> None:
    install_fake_dns(monkeypatch)

    answer = posting_live.check_posting_live(
        tmp_path, url="https://www.example-co.com/careers/job-title/", provider="greenhouse", token="examplesecurity", posting_id=posting_id, now=NOW,
    )

    assert site.seen == [] and answer.state == "unknown"


def test_a_plain_token_and_id_still_ask_the_board_at_its_fixed_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: _Site) -> None:
    install_fake_dns(monkeypatch)

    answer = posting_live.check_posting_live(
        tmp_path, url="https://www.example-co.com/careers/job-title/?gh_jid=8236603", provider="greenhouse", token="example-security_1.eu", posting_id="8236603", now=NOW,
    )

    assert answer.state == "open"
    [request] = site.requests
    assert (request.url.host, request.url.path) == ("boards-api.greenhouse.io", "/v1/boards/example-security_1.eu/jobs/8236603")


@pytest.mark.parametrize(("token", "posting_id"), [(token, "5") for token in BAD_NAMES] + [("examplesecurity", "5/../../x"), ("examplesecurity", "5?x=1")])
def test_the_single_job_request_is_never_built_from_a_token_or_id_that_is_not_a_plain_name(site: _Site, token: str, posting_id: str) -> None:
    with site.client() as client, pytest.raises(job_input.PostingTextUnavailable):
        job_input.fetch_missing_description(
            client, provider="greenhouse", token=token, posting_id=posting_id, url="https://www.example-co.com/careers/job-title/?gh_jid=5",
        )

    assert site.seen == []


@pytest.mark.parametrize("token", ["../../v2/x", "a/b", "a%2F..%2Fb", "a@evil.com"])
def test_a_for_token_on_an_assessed_url_that_is_not_a_plain_name_makes_no_request(site: _Site, token: str) -> None:
    """``?for=<token>`` on a company URL names the embedded Greenhouse board: it is the URL's text, so it is held to the same rule."""

    url = str(httpx.URL("https://www.example-co.com/careers/job-title/", params={"gh_jid": "5", "for": token}))

    with site.client() as client, pytest.raises(FindJobsContractError) as refused:
        job_input.resolve_job(AssessJobInput(job_url=url), client=client)

    assert refused.value.code == "job_fetch_failed"
    assert site.seen == []


def test_a_name_is_a_safe_path_segment_only_when_it_is_plain() -> None:
    safe = outbound_guard.safe_path_segment

    assert all(safe(name) for name in ("acme", "Acme-Co", "acme_co", "acme.co", "1password", "8236603", "ea7b507b-0000-4000-8000-000000000000"))
    assert not any(safe(name) for name in [*BAD_NAMES, None, 5, b"acme", "acmé", "a\tb", "a\x00b", "a:b", "a;b", "a&b"])


def test_fake_dns_never_asks_a_real_resolver_for_a_name() -> None:
    dns = FakeDNS()

    with pytest.raises(OSError):
        dns("www.example-co.com", 443)
    assert dns.calls == ["www.example-co.com"]
