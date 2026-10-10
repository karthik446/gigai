"""0.1.11.10 Part B, packet G2: the outbound fetch of a learning pathway (``gigai.scout.learning_fetch``).

1. THE POLICY, on a fake web with a fake name lookup and a fake clock: ``http`` is refused; a private, loopback
   or literal address is refused, also when a redirect leads to it; at most three redirects, each vetted; at
   most 2 MiB of a body is read; at most 10 requests a minute to one host (the 11th waits); a host's
   ``robots.txt`` is asked first and honoured, whatever the host; the course's fetch budget and the cancel hook
   stop everything.
2. A PAGE: the title, the ``h1`` to ``h4`` that carry an id, invisible Unicode taken out, the
   ``page_level_citable`` flag as the spike computes it.
3. THE CRAWL: seeded start pages, links on the same host only, ranked, at most 24 fetches per lesson.
4. THE ARC CHECKLIST by keyword stages.
5. The real network layer (``HttpxFetcher``) against an ``httpx.MockTransport``: the vetted address, the host
   header, the user agent, nothing read past the cap.

Every host, page and address here is made up. No socket is opened: the name lookup is a table.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest

from gigai.scout import learning_fetch as lf
from gigai.scout.find_jobs import outbound_guard
from gigai.scout.find_jobs.outbound_guard import PublicTarget
from gigai.scout.learning_fetch import FetchBudget, FetchNetworkError, FetchStopped, PageFetcher, RawResponse

PUBLIC = "93.184.216.34"
ZWSP, ZWJ, WORD_JOINER, BOM, SOFT_HYPHEN = "​", "‍", "⁠", "﻿", "­"
HTML = {"content-type": "text/html; charset=utf-8"}


def page(title: str, body: str = "") -> RawResponse:
    return RawResponse(200, HTML, f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode("utf-8"))


def redirect(location: str, status: int = 302) -> RawResponse:
    return RawResponse(status, {"location": location})


class FakeWeb:
    """The network as a table ``url -> answer``. An unknown URL (a host's robots.txt too) is a 404."""

    def __init__(self, pages: "Mapping[str, object] | None" = None, clock: "Clock | None" = None) -> None:
        self.pages = dict(pages or {})
        self.asked: list[str] = []
        self.at: list[float] = []
        self.caps: list[int] = []
        self.addresses: list[str] = []
        self.clock = clock

    def get(self, target: PublicTarget, *, max_bytes: int) -> RawResponse:
        self.asked.append(target.url)
        self.caps.append(max_bytes)
        self.addresses.append(target.address)
        if self.clock is not None:
            self.at.append(self.clock())
        answer = self.pages.get(target.url, RawResponse(404))
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, RawResponse)
        return RawResponse(answer.status, answer.headers, answer.body[: max_bytes + 1])

    def pages_asked(self) -> list[str]:
        return [url for url in self.asked if not url.endswith("/robots.txt")]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def names(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str, ...]]:
    """The name lookup as a table: every host is public unless a test says otherwise. No resolver is asked."""

    table: dict[str, tuple[str, ...]] = {}
    monkeypatch.setattr(outbound_guard, "resolve_host", lambda host, timeout=None: table.get(host, (PUBLIC,)))
    return table


def fetcher(web: FakeWeb, clock: Clock | None = None, **options: object) -> PageFetcher:
    clock = clock or Clock()
    return PageFetcher(web, clock=clock, sleep=clock.sleep, **options)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# https only, public addresses only, also after a redirect
# ---------------------------------------------------------------------------


def test_a_page_is_fetched_from_the_vetted_address_after_its_hosts_robots_file() -> None:
    web = FakeWeb({"https://docs.example.com/guide/start": page("Start", '<h2 id="install">Install</h2>')})
    found = fetcher(web).fetch_page("https://docs.example.com/guide/start#install")
    assert found.ok and found.error is None and found.status == 200
    assert found.url == "https://docs.example.com/guide/start"  # the fragment is not sent
    assert found.page is not None and found.page.title == "Start"
    assert web.asked == ["https://docs.example.com/robots.txt", "https://docs.example.com/guide/start"]
    assert set(web.addresses) == {PUBLIC}
    assert web.caps == [lf.ROBOTS_MAX_BYTES, lf.MAX_BYTES]


def test_http_is_refused_and_nothing_is_asked() -> None:
    web = FakeWeb({"http://docs.example.com/guide/start": page("Start")})
    found = fetcher(web).fetch_page("http://docs.example.com/guide/start")
    assert not found.ok and found.error == lf.NOT_HTTPS and found.page is None
    assert web.asked == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/admin/page",  # an address literal
        "https://localhost/admin/page",
        "https://printer.local/admin/page",  # a private suffix
        "https://docs.example.com:8443/guide/start",  # a port
        "https://user:secret@docs.example.com/guide/start",  # a login
        "https://[::1]/admin/page",
    ],
)
def test_a_url_that_does_not_name_a_public_https_host_is_refused_without_a_request(url: str) -> None:
    web = FakeWeb()
    found = fetcher(web).fetch_page(url)
    assert found.error == lf.NOT_PUBLIC
    assert web.asked == []


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.20", "127.0.0.1", "169.254.169.254", "100.64.0.9", "::1", "fd00::1"])
def test_a_public_looking_name_that_resolves_to_a_private_address_is_refused(names: dict[str, tuple[str, ...]], address: str) -> None:
    names["intranet.example.com"] = (address,)
    web = FakeWeb({"https://intranet.example.com/wiki/page": page("Internal wiki")})
    found = fetcher(web).fetch_page("https://intranet.example.com/wiki/page")
    assert found.error == lf.NOT_PUBLIC
    assert web.asked == []


def test_one_private_address_among_public_ones_refuses_the_host(names: dict[str, tuple[str, ...]]) -> None:
    names["mixed.example.com"] = (PUBLIC, "10.0.0.5")
    web = FakeWeb({"https://mixed.example.com/docs/page": page("Docs")})
    assert not fetcher(web).fetch_page("https://mixed.example.com/docs/page").ok
    assert web.asked == []


def test_a_redirect_to_a_private_address_is_refused_and_the_private_host_is_never_asked(names: dict[str, tuple[str, ...]]) -> None:
    names["intranet.example.com"] = ("10.0.0.5",)
    web = FakeWeb({
        "https://docs.example.com/guide/moved": redirect("https://intranet.example.com/wiki/page"),
        "https://intranet.example.com/wiki/page": page("Internal wiki"),
    })
    found = fetcher(web).fetch_page("https://docs.example.com/guide/moved")
    assert not found.ok and found.page is None and found.redirects == 1
    assert found.error == lf.NOT_PUBLIC
    assert not any("intranet" in url for url in web.asked)


@pytest.mark.parametrize("location, code", [
    ("https://169.254.169.254/latest/meta-data/", lf.NOT_PUBLIC),
    ("http://docs.example.com/guide/plain", lf.NOT_HTTPS),
    ("", lf.BAD_REDIRECT),
])
def test_a_redirect_to_an_address_literal_or_to_http_is_refused(location: str, code: str) -> None:
    web = FakeWeb({"https://docs.example.com/guide/moved": redirect(location)})
    found = fetcher(web).fetch_page("https://docs.example.com/guide/moved")
    assert found.error == code
    assert web.pages_asked() == ["https://docs.example.com/guide/moved"]


def test_three_redirects_are_followed_each_vetted_and_a_fourth_is_refused() -> None:
    hops = {f"https://docs.example.com/hop/{n}": redirect(f"/hop/{n + 1}", 301) for n in range(1, 5)}
    web = FakeWeb({**hops, "https://docs.example.com/hop/4": page("Arrived", '<h1 id="top">Arrived</h1>')})
    found = fetcher(web).fetch_page("https://docs.example.com/hop/1")
    assert found.ok and found.url == "https://docs.example.com/hop/4" and found.redirects == 3
    assert found.requested_url == "https://docs.example.com/hop/1"

    web = FakeWeb({**hops, "https://docs.example.com/hop/5": page("Too far")})
    found = fetcher(web).fetch_page("https://docs.example.com/hop/1")
    assert found.error == lf.TOO_MANY_REDIRECTS and found.page is None
    assert "https://docs.example.com/hop/5" not in web.asked


def test_a_redirect_to_another_public_host_asks_that_hosts_robots_file_first() -> None:
    web = FakeWeb({
        "https://old.example.com/docs/page": redirect("https://new.example.org/docs/page"),
        "https://new.example.org/robots.txt": RawResponse(200, {}, b"User-agent: *\nDisallow: /docs/\n"),
        "https://new.example.org/docs/page": page("Moved docs"),
    })
    found = fetcher(web).fetch_page("https://old.example.com/docs/page")
    assert found.error == lf.ROBOTS_DISALLOWED
    assert "https://new.example.org/docs/page" not in web.asked


@pytest.mark.parametrize("answer, code", [
    (RawResponse(404), "http_404"),
    (RawResponse(500), "http_500"),
    (RawResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.7"), lf.NOT_HTML),
    (FetchNetworkError("ConnectTimeout"), lf.NETWORK_ERROR),
])
def test_a_page_that_does_not_answer_with_html_is_an_error_not_a_raise(answer: object, code: str) -> None:
    web = FakeWeb({"https://docs.example.com/guide/start": answer})
    found = fetcher(web).fetch_page("https://docs.example.com/guide/start")
    assert found.error == code and found.page is None and not found.ok


# ---------------------------------------------------------------------------
# The size cap
# ---------------------------------------------------------------------------


def test_at_most_two_mebibytes_of_a_body_are_read() -> None:
    assert lf.MAX_BYTES == 2 * 1024 * 1024
    early = '<h2 id="early">Early</h2>'
    padding = "<p>" + "x" * (lf.MAX_BYTES + 5000) + "</p>"
    body = f"<html><head><title>Long page</title></head><body>{early}{padding}<h2 id=\"late\">Late</h2></body></html>"
    web = FakeWeb({"https://docs.example.com/guide/long": RawResponse(200, HTML, body.encode("utf-8"))})
    found = fetcher(web).fetch_page("https://docs.example.com/guide/long")
    assert found.ok and found.page is not None
    assert found.page.truncated is True
    assert [heading["id"] for heading in found.page.headings] == ["early"]  # what stood past the cap was never read
    assert web.caps[-1] == lf.MAX_BYTES


def test_a_page_under_the_cap_is_not_marked_truncated() -> None:
    web = FakeWeb({"https://docs.example.com/guide/short": page("Short")})
    found = fetcher(web).fetch_page("https://docs.example.com/guide/short")
    assert found.page is not None and found.page.truncated is False


# ---------------------------------------------------------------------------
# The rate limit (a fake clock)
# ---------------------------------------------------------------------------


def test_the_eleventh_request_to_one_host_in_a_minute_waits_for_the_window() -> None:
    assert lf.RATE_PER_HOST_PER_MINUTE == 10
    clock = Clock()
    web = FakeWeb({f"https://docs.example.com/guide/p{n}": page(f"Page {n}") for n in range(12)}, clock)
    web.pages["https://other.example.org/guide/p0"] = page("Other host")
    pages = fetcher(web, clock)
    for n in range(9):  # with the robots file: ten requests to the host, all at once
        assert pages.fetch_page(f"https://docs.example.com/guide/p{n}").ok
    assert clock.slept == [] and len(web.asked) == 10
    assert pages.fetch_page("https://other.example.org/guide/p0").ok  # another host has its own minute
    assert clock.slept == []

    assert pages.fetch_page("https://docs.example.com/guide/p9").ok  # the eleventh
    assert sum(clock.slept) == pytest.approx(60.0)
    first, eleventh = web.at[0], web.at[-1]
    assert eleventh - first >= 60.0
    assert pages.fetch_page("https://docs.example.com/guide/p10").ok  # the window moved on: no second wait yet
    assert sum(clock.slept) == pytest.approx(60.0)


def test_no_minute_holds_more_than_ten_requests_to_one_host() -> None:
    clock = Clock()
    web = FakeWeb({f"https://docs.example.com/guide/p{n}": page(f"Page {n}") for n in range(35)}, clock)
    pages = fetcher(web, clock)
    for n in range(35):
        pages.fetch_page(f"https://docs.example.com/guide/p{n}")
    assert len(web.at) == 36
    for index, moment in enumerate(web.at):
        assert sum(1 for other in web.at[index:] if other - moment < 60.0) <= 10


# ---------------------------------------------------------------------------
# robots.txt, for every host
# ---------------------------------------------------------------------------


def test_a_robots_disallow_is_honoured_and_the_page_is_never_asked() -> None:
    web = FakeWeb({
        "https://docs.example.com/robots.txt": RawResponse(200, {}, b"User-agent: *\nDisallow: /private/\n"),
        "https://docs.example.com/private/notes": page("Private notes"),
        "https://docs.example.com/guide/start": page("Start"),
    })
    pages = fetcher(web)
    assert pages.fetch_page("https://docs.example.com/private/notes").error == lf.ROBOTS_DISALLOWED
    assert pages.fetch_page("https://docs.example.com/guide/start").ok
    assert web.asked == ["https://docs.example.com/robots.txt", "https://docs.example.com/guide/start"]  # the file once
    assert pages.robots_requests == 1 and pages.requests == 2


def test_the_rules_named_for_gigai_win_over_the_rules_for_everyone() -> None:
    rules = b"User-agent: *\nAllow: /\n\nUser-agent: GigAI\nDisallow: /guide/\n"
    web = FakeWeb({"https://docs.example.com/robots.txt": RawResponse(200, {}, rules), "https://docs.example.com/guide/start": page("Start")})
    assert fetcher(web).fetch_page("https://docs.example.com/guide/start").error == lf.ROBOTS_DISALLOWED


def test_a_host_whose_robots_file_cannot_be_read_is_not_asked() -> None:
    for answer in (RawResponse(503), FetchNetworkError("ReadTimeout")):
        web = FakeWeb({"https://docs.example.com/robots.txt": answer, "https://docs.example.com/guide/start": page("Start")})
        found = fetcher(web).fetch_page("https://docs.example.com/guide/start")
        assert found.error == lf.ROBOTS_UNKNOWN
        assert web.pages_asked() == []


def test_every_host_is_asked_for_its_own_robots_file() -> None:
    web = FakeWeb({f"https://{host}/docs/page": page("Docs") for host in ("a.example.com", "b.example.org", "c.example.net")})
    pages = fetcher(web)
    for host in ("a.example.com", "b.example.org", "c.example.net"):
        assert pages.fetch_page(f"https://{host}/docs/page").ok
    assert [url for url in web.asked if url.endswith("/robots.txt")] == [
        "https://a.example.com/robots.txt", "https://b.example.org/robots.txt", "https://c.example.net/robots.txt",
    ]


# ---------------------------------------------------------------------------
# The budget and the cancel hook
# ---------------------------------------------------------------------------


def test_the_budget_stops_the_fetching() -> None:
    assert lf.FETCH_BUDGET == 600 and FetchBudget().limit == 600
    web = FakeWeb({f"https://docs.example.com/guide/p{n}": page(f"Page {n}") for n in range(5)})
    budget = FetchBudget(3)
    pages = fetcher(web, budget=budget)
    assert pages.fetch_page("https://docs.example.com/guide/p0").ok  # the robots file and the page: 2
    assert pages.fetch_page("https://docs.example.com/guide/p1").ok  # 3
    with pytest.raises(FetchStopped) as stopped:
        pages.fetch_page("https://docs.example.com/guide/p2")
    assert stopped.value.code == lf.STOP_BUDGET
    assert len(web.asked) == 3 and budget.used == 3 and budget.left == 0
    with pytest.raises(FetchStopped):
        pages.fetch_page("https://docs.example.com/guide/p3")
    assert len(web.asked) == 3


def test_a_refused_url_costs_no_budget() -> None:
    budget = FetchBudget(1)
    web = FakeWeb()
    assert fetcher(web, budget=budget).fetch_page("http://docs.example.com/guide/start").error == lf.NOT_HTTPS
    assert budget.used == 0


def test_cancel_stops_before_anything_is_asked() -> None:
    web = FakeWeb({"https://docs.example.com/guide/start": page("Start")})
    with pytest.raises(FetchStopped) as stopped:
        fetcher(web, cancel=lambda: True).fetch_page("https://docs.example.com/guide/start")
    assert stopped.value.code == lf.STOP_CANCELLED
    assert web.asked == []


def test_cancel_ends_a_rate_limit_wait() -> None:
    clock = Clock()
    web = FakeWeb({f"https://docs.example.com/guide/p{n}": page(f"Page {n}") for n in range(12)}, clock)
    flag = {"stop": False}
    pages = fetcher(web, clock, cancel=lambda: flag["stop"] or sum(clock.slept) >= 3)
    for n in range(9):
        pages.fetch_page(f"https://docs.example.com/guide/p{n}")
    with pytest.raises(FetchStopped) as stopped:
        pages.fetch_page("https://docs.example.com/guide/p9")
    assert stopped.value.code == lf.STOP_CANCELLED
    assert sum(clock.slept) < 5 and len(web.asked) == 10  # it did not sit out the minute, and nothing more was sent


# ---------------------------------------------------------------------------
# A page: title, headings, invisible Unicode, the page-level flag
# ---------------------------------------------------------------------------


def test_headings_with_an_id_are_kept_and_invisible_unicode_is_stripped() -> None:
    body = (
        f'<h1 id="top">Tracking{ZWSP}</h1>'
        f'<h2 id="runs">Runs{ZWJ} and{WORD_JOINER}   experiments{BOM}</h2>'
        '<h2>No id here</h2>'
        f'<h3 id="soft">Auto{SOFT_HYPHEN}logging</h3>'
        '<h4 id="deep">Deep &amp; narrow</h4>'
        '<h5 id="too-deep">Too deep</h5>'
        f'<h2 id="bad{ZWSP}id">An id nothing can link to</h2>'
        '<script>var hidden = "<h2 id=x>not a heading</h2>";</script>'
        '<a href="/guide/next">Next</a><a href="https://other.example.org/page">Elsewhere</a>'
    )
    found = lf.parse_page("https://docs.example.com/guide/tracking", f"<html><head><title> ML{ZWSP}flow \n Tracking </title></head><body>{body}</body></html>")
    assert found.title == "MLflow Tracking"
    assert [dict(heading) for heading in found.headings] == [
        {"tag": "h1", "id": "top", "text": "Tracking"},
        {"tag": "h2", "id": "runs", "text": "Runs and experiments"},
        {"tag": "h3", "id": "soft", "text": "Autologging"},
        {"tag": "h4", "id": "deep", "text": "Deep & narrow"},
    ]
    assert found.links == ("/guide/next", "https://other.example.org/page")
    for character in (ZWSP, ZWJ, WORD_JOINER, BOM, SOFT_HYPHEN):
        assert character not in found.title and all(character not in heading["text"] for heading in found.headings)
    assert lf.clean_text(f"a{ZWSP}b \n c{SOFT_HYPHEN}") == "ab c"
    assert set(found.to_json()) == {"url", "title", "headings", "body_chars", "page_level_citable", "truncated"}


def test_a_heading_with_no_id_of_its_own_uses_its_nearest_enclosing_sections_id() -> None:
    html = '<section id="intro"><h2>Intro</h2><p>text</p></section>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert [dict(heading) for heading in found.headings] == [{"tag": "h2", "id": "intro", "text": "Intro"}]


def test_a_heading_with_no_id_of_its_own_uses_the_nearest_enclosing_div_class_section_id() -> None:
    html = '<div class="section" id="intro"><h2>Intro</h2></div>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert [dict(heading) for heading in found.headings] == [{"tag": "h2", "id": "intro", "text": "Intro"}]


def test_a_plain_div_between_a_section_and_its_heading_does_not_hide_the_sections_id() -> None:
    html = '<section id="outer"><div class="note"><p>a hint</p></div><h2>Heading</h2></section>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert [dict(heading) for heading in found.headings] == [{"tag": "h2", "id": "outer", "text": "Heading"}]


def test_a_headerlink_permalink_inside_the_heading_gives_it_its_id() -> None:
    html = '<h2>Intro<a class="headerlink" href="#intro" title="Link to this heading">#</a></h2>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert found.headings[0]["id"] == "intro"


def test_an_anchor_or_span_placed_immediately_before_a_heading_gives_it_its_id() -> None:
    for wrapper in ('<a id="intro"></a>', '<span id="intro"></span>'):
        html = f"{wrapper}<h2>Intro</h2>"
        found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
        assert [dict(heading) for heading in found.headings] == [{"tag": "h2", "id": "intro", "text": "Intro"}]


def test_an_anchor_not_immediately_before_a_heading_is_not_used() -> None:
    html = '<a id="intro"></a><p>some other content</p><h2>Intro</h2>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert found.headings == ()  # no usable anchor nearby: the heading has none, so it is dropped like any id-less heading


def test_a_headings_own_id_is_never_overridden_by_an_enclosing_section_or_a_preceding_anchor() -> None:
    html = '<a id="before"></a><section id="outer"><h2 id="own">Intro</h2></section>'
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert [dict(heading) for heading in found.headings] == [{"tag": "h2", "id": "own", "text": "Intro"}]


def test_a_heading_with_no_id_anywhere_near_it_stays_without_one() -> None:
    html = "<div><p>intro text</p><h2>Intro</h2></div>"
    found = lf.parse_page("https://docs.example.com/a/b", f"<html><head><title>T</title></head><body>{html}</body></html>")
    assert found.headings == ()  # no section, no headerlink, no preceding anchor: same as before the fix


def test_only_the_documents_own_title_is_its_title() -> None:
    html = "<html><head><title>Real title</title></head><body><svg><title>Icon</title></svg><p>text</p></body></html>"
    assert lf.parse_page("https://docs.example.com/a/b", html).title == "Real title"


def test_broken_markup_is_read_as_far_as_it_goes() -> None:
    found = lf.parse_page("https://docs.example.com/a/b", '<title>Half</title><h2 id="one">One</h2><h2 id="two">Unclosed')
    assert found.title == "Half" and [heading["id"] for heading in found.headings] == ["one"]


TEXT = "<p>" + "word " * 100 + "</p>"  # 500 visible characters


@pytest.mark.parametrize("url, title, body, expected", [
    ("https://docs.example.com/43/configuration/topic-configs/", "Topic configs", TEXT, True),
    ("https://docs.example.com/43/configuration/topic-configs/", "Topic configs", TEXT + '<h2 id="retention">Retention</h2>', False),  # it has an anchor
    ("https://docs.example.com/43/configuration/topic-configs/", "Topic configs", TEXT + "<h2>No id</h2>", True),  # a heading nothing can link to
    ("https://docs.example.com/", "Home", TEXT, False),  # a host's home page
    ("https://docs.example.com/intro", "Intro", TEXT, False),  # a section's home page
    ("https://docs.example.com/43/configuration/topic-configs/", "", TEXT, False),  # no title
    ("https://docs.example.com/43/configuration/topic-configs/", "Documentation Redirect", TEXT, False),  # a redirect page
    ("https://docs.example.com/43/documentation/", "Documentation", TEXT, False),  # a stub path
    ("https://docs.example.com/43/configuration/topic-configs/", "Topic configs", "<p>" + "w" * 380 + "</p>", False),  # too little text (the title counts)
    ("https://docs.example.com/43/configuration/topic-configs/", "Topic configs", "<script>" + "w" * 900 + "</script><p>short</p>", False),  # script is not text
])
def test_the_page_level_citable_flag(url: str, title: str, body: str, expected: bool) -> None:
    found = lf.parse_page(url, f"<html><head><title>{title}</title></head><body>{body}</body></html>")
    assert found.page_level_citable is expected


def test_a_deep_path_and_a_stub_path() -> None:
    assert lf.is_deep_path("https://docs.example.com/docs/latest/tracking/")
    assert lf.is_deep_path("https://docs.example.com/docs/")
    assert not lf.is_deep_path("https://docs.example.com/intro")
    assert not lf.is_deep_path("https://docs.example.com/")
    assert lf.is_stub_path("https://docs.example.com/documentation/") and lf.is_stub_path("https://docs.example.com/a/videos")
    assert not lf.is_stub_path("https://docs.example.com/documentation/streams/")


# ---------------------------------------------------------------------------
# The crawl
# ---------------------------------------------------------------------------


def links(*hrefs: str) -> str:
    return "".join(f'<a href="{href}">link</a>' for href in hrefs)


def test_the_crawl_stays_on_the_seed_host_and_reaches_two_links_deep() -> None:
    web = FakeWeb({
        "https://docs.example.com/docs/": page("Docs home", '<h1 id="top">Docs</h1>' + links("/docs/tracking/", "https://other.example.org/blog/post", "http://docs.example.com/docs/plain", "#top", "/docs/tracking/#runs")),
        "https://docs.example.com/docs/tracking/": page("Tracking", '<h1 id="t">Tracking</h1>' + links("/docs/tracking/registry/", "https://cdn.example.net/asset/x")),
        "https://docs.example.com/docs/tracking/registry/": page("Registry", '<h1 id="r">Registry</h1>' + links("/docs/tracking/registry/deeper/")),
        "https://docs.example.com/docs/tracking/registry/deeper/": page("Three links deep"),
        "https://other.example.org/blog/post": page("Another host"),
    })
    crawl = lf.crawl_lesson(fetcher(web), ["https://docs.example.com/docs/", "https://other.example.org/blog/post"])
    assert crawl.host == "docs.example.com"
    assert [(item["url"], item["depth"]) for item in crawl.pages] == [
        ("https://docs.example.com/docs/", 0),
        ("https://docs.example.com/docs/tracking/", 1),
        ("https://docs.example.com/docs/tracking/registry/", 2),
    ]
    assert crawl.fetch_count == 3
    assert crawl.skipped == ("https://other.example.org/blog/post",)  # a seed on another host is not asked
    assert all("docs.example.com" in url for url in web.asked)
    assert "https://docs.example.com/docs/tracking/registry/deeper/" not in web.asked  # three links deep: never


def test_the_crawl_is_bounded_at_24_pages_and_ranks_what_it_asks_for() -> None:
    assert lf.MAX_PAGES_PER_LESSON == 24
    plain = [f"/misc/page-{n}" for n in range(40)]
    wanted = ["/docs/feature-store/quickstart", "/docs/materialize", "/guide/online"]
    web = FakeWeb({"https://docs.example.com/start/here": page("Start", '<h1 id="top">Start</h1>' + links(*plain, *wanted))})
    for path in plain + wanted:
        web.pages[f"https://docs.example.com{path}"] = page(path, '<h1 id="top">Page</h1>')
    clock = Clock()
    crawl = lf.crawl_lesson(fetcher(web, clock), ["https://docs.example.com/start/here"], ["feature", "store", "materialize", "online"])
    assert crawl.fetch_count == 24 and len(crawl.pages) == 24
    assert len(web.pages_asked()) == 24
    asked = [str(item["url"]).removeprefix("https://docs.example.com") for item in crawl.pages]
    # docs path word + lesson words first: /docs/feature-store/quickstart scores 2+2, /docs/materialize 2+1, /guide/online 2+1.
    assert asked[:4] == ["/start/here", "/docs/feature-store/quickstart", "/docs/materialize", "/guide/online"]
    assert asked[4:] == plain[:20]  # the rest in page order


def test_the_crawl_does_not_expand_a_stub_and_records_a_failed_page() -> None:
    web = FakeWeb({
        "https://docs.example.com/docs/": page("Docs", '<h1 id="top">Docs</h1>' + links("/docs/stub/", "/docs/gone/", "/docs/videos/")),
        "https://docs.example.com/docs/stub/": page("Index of links", links("/docs/behind-the-stub/")),  # no heading with an id
        "https://docs.example.com/docs/behind-the-stub/": page("Hidden", '<h1 id="h">Hidden</h1>'),
    })
    crawl = lf.crawl_lesson(fetcher(web), ["https://docs.example.com/docs/"])
    by_url = {item["url"]: item for item in crawl.pages}
    assert by_url["https://docs.example.com/docs/gone/"] == {"url": "https://docs.example.com/docs/gone/", "error": "http_404", "depth": 1}
    assert "https://docs.example.com/docs/behind-the-stub/" not in web.asked
    assert "https://docs.example.com/docs/videos/" not in web.asked  # a known stub path is not asked at all
    assert len(crawl.ok_pages) == 2 and crawl.fetch_count == 3


def test_a_spent_budget_ends_the_crawl_with_what_it_has_and_a_cancel_raises() -> None:
    pages = {"https://docs.example.com/docs/": page("Docs", '<h1 id="top">Docs</h1>' + links(*(f"/docs/p{n}/" for n in range(6))))}
    pages.update({f"https://docs.example.com/docs/p{n}/": page(f"P{n}", '<h1 id="t">T</h1>') for n in range(6)})
    web = FakeWeb(pages)
    crawl = lf.crawl_lesson(fetcher(web, budget=FetchBudget(4)), ["https://docs.example.com/docs/"])
    assert crawl.stopped == lf.STOP_BUDGET and crawl.fetch_count == 3 and len(web.asked) == 4
    assert crawl.to_json()["stopped"] == lf.STOP_BUDGET

    with pytest.raises(FetchStopped):
        lf.crawl_lesson(fetcher(FakeWeb(pages), cancel=lambda: True), ["https://docs.example.com/docs/"])


def test_lesson_words_and_the_rank_score() -> None:
    assert lf.lesson_words(["Point-in-time joins", "The online store and the offline store", "Feast"]) == [
        "point-in-time", "joins", "online", "store", "offline", "feast",
    ]
    assert lf.rank_score("https://docs.example.com/docs/online-store/", ["online", "store"]) == 4
    assert lf.rank_score("https://docs.example.com/blog/hiring/", ["online", "store"]) == 0
    assert lf.same_host_links("https://docs.example.com/a/b", ["c", "/d#x", "https://docs.example.com/d", "https://x.example.org/e", "mailto:a@example.com"], "docs.example.com") == [
        "https://docs.example.com/a/c", "https://docs.example.com/d",
    ]


# ---------------------------------------------------------------------------
# The arc checklist
# ---------------------------------------------------------------------------


def test_the_arc_checklist_names_the_stages_the_fetched_pages_cover() -> None:
    assert [stage for stage, _keywords in lf.ARC_STAGE_KEYWORDS] == [
        "first_run", "core_object_model", "persist_compare", "package_register_promote", "serve_deploy_locally",
        "reproduce_automate", "production_like", "windowing", "retention", "compaction", "materialize",
    ]
    pages = [
        {"url": "https://docs.example.com/docs/getting-started/", "title": "First steps", "headings": [], "depth": 0},
        {"url": "https://docs.example.com/docs/gone/", "error": "http_404", "depth": 1},  # not a fetched page: not numbered
        {"url": "https://docs.example.com/docs/models/", "title": "Models", "headings": [{"tag": "h2", "id": "model-registry", "text": "Register a model"}], "depth": 1},
        {"url": "https://docs.example.com/docs/topic/", "title": "Topic settings", "headings": [{"tag": "h2", "id": "keep", "text": "Retention and Log Compaction"}], "depth": 1},
        {"url": "https://docs.example.com/docs/faq/", "title": "FAQ", "headings": [], "depth": 2},
    ]
    checklist = lf.compute_arc_checklist(pages)
    assert checklist["first_run"] == [{"page_index": 0, "url": "https://docs.example.com/docs/getting-started/", "title": "First steps"}]
    assert [hit["page_index"] for hit in checklist["package_register_promote"]] == [1]
    assert [hit["page_index"] for hit in checklist["retention"]] == [2]
    assert [hit["page_index"] for hit in checklist["compaction"]] == [2]  # two stages, never one merged one
    assert "windowing" not in checklist and "materialize" not in checklist and "serve_deploy_locally" not in checklist
    assert lf.compute_arc_checklist([]) == {}


def test_a_tracking_server_page_does_not_count_for_the_serving_stage() -> None:
    pages = [
        {"url": "https://mlflow.org/docs/latest/tracking-server.html", "title": "MLflow Tracking Server", "headings": [], "depth": 1},
    ]
    checklist = lf.compute_arc_checklist(pages)
    assert "serve_deploy_locally" not in checklist
    assert [hit["page_index"] for hit in checklist["production_like"]] == [0]  # a tracking/backend-store mention counts there instead


def test_a_local_inference_server_page_counts_for_the_serving_stage() -> None:
    pages = [
        {"url": "https://mlflow.org/docs/latest/deployment/deploy-model-locally.html", "title": "Deploy MLflow Model as a Local Inference Server", "headings": [], "depth": 1},
    ]
    checklist = lf.compute_arc_checklist(pages)
    assert [hit["page_index"] for hit in checklist["serve_deploy_locally"]] == [0]


def test_a_crawls_json_carries_its_arc_checklist() -> None:
    web = FakeWeb({"https://docs.example.com/docs/quickstart/": page("Quickstart", '<h1 id="serve">Serve a model</h1>')})
    stored = lf.crawl_lesson(fetcher(web), ["https://docs.example.com/docs/quickstart/"]).to_json()
    assert set(stored["arc_checklist"]) == {"first_run", "serve_deploy_locally"}  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# The real network layer, against a mock transport
# ---------------------------------------------------------------------------


def test_the_httpx_fetcher_asks_the_vetted_address_as_gigai_and_reads_no_more_than_the_cap() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/html", "Location": "/elsewhere"}, content=b"a" * 5000)

    real = lf.HttpxFetcher()
    try:
        assert real._client.follow_redirects is False and real._client.trust_env is False
        agent = real._client.headers["user-agent"]
    finally:
        real.close()
    assert agent.startswith("GigAI/") and "(+https://" in agent

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": agent})
    target = PublicTarget(url="https://docs.example.com/guide/start?x=1", host="docs.example.com", address=PUBLIC)
    answer = lf.HttpxFetcher(client).get(target, max_bytes=1000)
    assert answer.status == 200 and answer.headers["content-type"] == "text/html"
    assert len(answer.body) == 1001  # one byte past the cap says "there was more"; the rest is dropped
    request = seen[0]
    assert str(request.url) == f"https://{PUBLIC}/guide/start?x=1"
    assert request.headers["host"] == "docs.example.com" and request.headers["user-agent"].startswith("GigAI/")
    assert request.extensions["sni_hostname"] == "docs.example.com"

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no answer", request=request)

    with pytest.raises(FetchNetworkError):
        lf.HttpxFetcher(httpx.Client(transport=httpx.MockTransport(failing))).get(target, max_bytes=1000)
