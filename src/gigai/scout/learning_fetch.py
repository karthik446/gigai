"""0.1.11.10 Part B, packet G2: the pages a learning pathway reads from the web, and the rules it reads them by.

A course's sources are public documentation pages. No model call reads the web (every call stays locked down:
no tools); GigAI fetches the pages itself, here, and keeps of each one only what the later steps check in code:
its title, its headings that carry an ``id`` (the anchors a practice path may link to) and its links.

THE OUTBOUND POLICY (design section 2, option C). Every request this module sends, a page, a redirect hop or a
``robots.txt``, goes through :meth:`PageFetcher._send`:

* ``https`` only, at a public address: ``find_jobs/outbound_guard.safe_public_target`` vets the URL and its
  host's addresses (no loopback, private, link-local or metadata address; no port, no login), and the request
  connects to that vetted address. A redirect is never followed by the network layer: each hop comes back
  here and is vetted like the first URL, at most :data:`MAX_REDIRECTS` of them.
* ``robots.txt`` is honoured for EVERY host (``find_jobs/robots_guard.RobotsGuard``, the guard the job boards
  are read through: its rules, its day-long cache, its ``Crawl-delay``). A host whose file could not be read is
  not asked.
* at most :data:`RATE_PER_HOST_PER_MINUTE` requests a minute to one host (a wait, not a refusal), at most
  :data:`FETCH_BUDGET` requests per course (:class:`FetchBudget`), and a cancel hook looked at before every
  request and during every wait.
* at most :data:`MAX_BYTES` of a body is read; the rest is dropped, not read.
* the requests say who is asking: ``GigAI/<version> (+address)`` (``find_jobs/board_headers.user_agent``).

What comes back is text written by strangers. Invisible Unicode (zero-width space and joiners, the word joiner,
the byte-order mark, the soft hyphen) is taken out of every title and heading before anything reads it, and a
heading whose ``id`` holds one is not kept (the anchor would not survive the renderer's own strip).

THE NETWORK IS A PARAMETER (:class:`Fetcher`: one request to one vetted address, no redirect). Every test hands
:class:`PageFetcher` a fake web; :class:`HttpxFetcher` is the real one.

Ported from the research spike (``spike9c/fetch.py``): the heading extraction and the ``page_level_citable``
flag (:func:`parse_page`), the bounded same-host crawl (:func:`crawl_lesson`) and the arc checklist by keyword
stages (:data:`ARC_STAGE_KEYWORDS`, :func:`compute_arc_checklist`).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin, urlsplit

from .find_jobs import outbound_guard
from .find_jobs.outbound_guard import PublicTarget

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

    from .find_jobs.robots_guard import RobotsGuard

#: At most this much of a page body is read (``job_input.MAX_BODY_BYTES``: the same cap).
MAX_BYTES = 2 * 1024 * 1024
#: At most this much of a robots.txt is asked for (``robots_guard.MAX_BODY_BYTES``).
ROBOTS_MAX_BYTES = 512 * 1024
#: How many redirects one page fetch follows; each hop is vetted like the first URL.
MAX_REDIRECTS = 3
#: Requests to one host in any 60 seconds.
RATE_PER_HOST_PER_MINUTE = 10
RATE_WINDOW_SECONDS = 60.0
#: Requests one course may send (pages, redirect hops and robots files together).
FETCH_BUDGET = 600
#: Pages one lesson's crawl may ask for.
MAX_PAGES_PER_LESSON = 24
#: A page with no anchorable heading is citable as a page only with at least this much visible text.
MIN_BODY_CHARS_FOR_PAGE_LEVEL = 400

STOP_CANCELLED = "cancelled"
STOP_BUDGET = "budget_exhausted"

NOT_HTTPS = "not_https"
NOT_PUBLIC = "not_public"
NOT_HTML = "not_html"
NETWORK_ERROR = "network_error"
BAD_REDIRECT = "bad_redirect"
TOO_MANY_REDIRECTS = "too_many_redirects"
ROBOTS_DISALLOWED = "robots_disallowed"
ROBOTS_UNKNOWN = "robots_unknown"
LEFT_HOST = "left_host"

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
#: How long one step of a rate-limit wait sleeps before the cancel hook is looked at again.
_WAIT_STEP_SECONDS = 1.0

#: Zero-width space, non-joiner and joiner, the word joiner, the byte-order mark, the soft hyphen (spike 9 found
#: documentation headings that end in U+200B).
INVISIBLE_RE = re.compile("[​-‍⁠﻿­]")
_UNUSABLE_ID = re.compile("[\\s​-‍⁠﻿­\x00-\x1f\x7f]")


def strip_invisible(text: str) -> str:
    """``text`` without the invisible characters of :data:`INVISIBLE_RE`."""

    return INVISIBLE_RE.sub("", text)


def clean_text(text: str) -> str:
    """A fetched title or heading as it is kept: invisible characters out, every run of whitespace one space."""

    return re.sub(r"\s+", " ", strip_invisible(text)).strip()


# ---------------------------------------------------------------------------
# The network (a parameter), the budget, what stops a run
# ---------------------------------------------------------------------------


class FetchStopped(Exception):
    """Nothing more may be asked: ``code`` is ``cancelled`` or ``budget_exhausted``. Never a verdict on one URL."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FetchNetworkError(Exception):
    """A :class:`Fetcher` could not get an answer (no connection, a timeout, a broken response)."""


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RawResponse:
    """One answer of the network: the status, the headers (lower-case names) and at most ``max_bytes + 1`` bytes."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


class Fetcher(Protocol):
    """The network: ONE ``GET`` to the vetted address of ``target``. It follows no redirect and resolves no name."""

    def get(self, target: PublicTarget, *, max_bytes: int) -> RawResponse: ...


class HttpxFetcher:
    """The real network: ``outbound_guard.pinned_stream`` (the vetted address, the certificate checked against the host).

    The client it builds follows no redirect, reads no proxy setting and says ``GigAI/<version> (+address)``.
    """

    def __init__(self, client: "httpx.Client | None" = None) -> None:
        import httpx

        from .find_jobs.board_headers import user_agent

        self._owned = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False, trust_env=False,
            headers={"User-Agent": user_agent(), "Accept": "text/html"},
        )

    def get(self, target: PublicTarget, *, max_bytes: int) -> RawResponse:
        import httpx

        try:
            with outbound_guard.pinned_stream(self._client, target) as response:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        break  # the rest is never read
                headers = {name.lower(): value for name, value in response.headers.items()}
                return RawResponse(response.status_code, headers, b"".join(chunks)[: max_bytes + 1])
        except httpx.HTTPError as exc:
            raise FetchNetworkError(type(exc).__name__) from exc

    def close(self) -> None:
        if self._owned:
            self._client.close()


class FetchBudget:
    """How many requests one course may still send. Shared by every fetch of the course; thread-safe."""

    def __init__(self, limit: int = FETCH_BUDGET) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


# ---------------------------------------------------------------------------
# A page: its title, its anchorable headings, its links (ported from spike9c/fetch.py)
# ---------------------------------------------------------------------------

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})
_BODY_SKIP_TAGS = frozenset({"script", "style"})
#: A ``div``'s class names that mark it as a Sphinx/docutils section wrapper (others are not treated as one).
_SECTION_DIV_CLASSES = frozenset({"section", "doc-section"})
#: Elements that may hold a bare anchor placed immediately before a heading (``<a id=...></a>``, ``<span id=...></span>``).
_ANCHOR_TAGS = frozenset({"a", "span"})
#: A void element: no end tag follows, so it never changes "immediately before the heading".
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr"})
#: Path ends that are index or redirect stubs on documentation hosts (0 headings, a list of links or a script).
STUB_PATH_HINTS = re.compile(r"/(documentation|documentation\.html|books-and-papers|videos|podcasts)/?$", re.I)
#: A page that titles itself a redirect is a stub however long its navigation text is.
REDIRECT_TITLE_RE = re.compile(r"redirect", re.I)
#: Path words that mark a documentation page worth reading before another.
PREFER_RE = re.compile(r"(docs?|tutorial|quickstart|guide|concepts?|streams?|registry|deploy|serving|tracking|api)", re.I)


def _class_names(attrs: Mapping[str, str | None]) -> frozenset[str]:
    return frozenset((attrs.get("class") or "").split())


class _PageParser(HTMLParser):
    """Besides the document's title, its headings and links: each heading's anchor may not sit on the heading itself.

    Sphinx/docutils, Docusaurus and ReadTheDocs commonly put the ``id`` on the enclosing ``<section>`` or
    ``<div class="section">``, on an ``<a>``/``<span>`` placed immediately before the heading, or only reachable
    through a ``headerlink`` permalink inside the heading (``<a class="headerlink" href="#id">``). A heading
    that carries its own ``id`` is never overridden by one of these.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._title_done = False
        self.headings: list[dict[str, str]] = []
        self._heading: dict[str, str] | None = None
        self.links: list[str] = []
        self.body_chars = 0
        self._skip_depth = 0
        #: One entry per still-open ``section``/``article``/``div``: its id if it is a section wrapper, else ``None``.
        self._section_ids: list[str | None] = []
        #: Stack of ids of the still-open ``a``/``span`` elements seen outside a heading (``""`` when one has none).
        self._open_anchor_ids: list[str] = []
        #: The id of the last ``a``/``span`` closed since, cleared as soon as anything else opens or closes.
        self._pending_anchor_id = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "title" and not self._title_done:  # the document's title, not an inline SVG's
            self._in_title = True
        if tag in HEADING_TAGS:
            enclosing = next((found_id for found_id in reversed(self._section_ids) if found_id), "")
            fallback = self._pending_anchor_id or enclosing
            self._heading = {"tag": tag, "id": found.get("id") or "", "text": "", "headerlink_id": "", "fallback_id": fallback}
            self._pending_anchor_id = ""
        elif self._heading is not None:
            if tag == "a" and not self._heading["headerlink_id"] and "headerlink" in _class_names(found):
                anchor = (found.get("href") or "").removeprefix("#")
                if anchor:
                    self._heading["headerlink_id"] = anchor
        if tag == "a" and found.get("href"):
            self.links.append(str(found["href"]))
        if tag in _BODY_SKIP_TAGS:
            self._skip_depth += 1
        if self._heading is None:
            if tag in ("section", "article", "div"):
                is_section = tag != "div" or bool(_class_names(found) & _SECTION_DIV_CLASSES)
                self._section_ids.append((found.get("id") or "") if is_section else None)
                self._pending_anchor_id = ""
            elif tag in _ANCHOR_TAGS:
                self._open_anchor_ids.append(found.get("id") or "")
            elif tag not in _VOID_TAGS:
                self._pending_anchor_id = ""  # anything else opening breaks "immediately before the heading"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        if tag in HEADING_TAGS and self._heading is not None:
            heading = self._heading
            if not heading["id"]:
                heading["id"] = heading["headerlink_id"] or heading["fallback_id"]
            del heading["headerlink_id"]
            del heading["fallback_id"]
            self.headings.append(heading)
            self._heading = None
            self._pending_anchor_id = ""
        if self._heading is None:
            if tag in ("section", "article", "div") and self._section_ids:
                self._section_ids.pop()
            elif tag in _ANCHOR_TAGS and self._open_anchor_ids:
                self._pending_anchor_id = self._open_anchor_ids.pop()
            elif tag not in _VOID_TAGS and tag not in HEADING_TAGS:
                self._pending_anchor_id = ""
        if tag in _BODY_SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._heading is not None:
            self._heading["text"] += data
        if self._skip_depth == 0:
            self.body_chars += len(data.strip())


@dataclass(frozen=True)
class Page:
    """What is kept of one fetched page. ``headings`` are the ``h1`` to ``h4`` that carry an ``id``, in page order."""

    url: str
    title: str
    headings: tuple[Mapping[str, str], ...] = ()
    body_chars: int = 0
    #: Citable as a whole page (no anchor): real text, a deep path, a title that is not a redirect's, no usable heading.
    page_level_citable: bool = False
    truncated: bool = False
    #: Every ``href`` of the page as written (relative ones included). Not part of :meth:`to_json`.
    links: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "url": self.url, "title": self.title, "headings": [dict(heading) for heading in self.headings],
            "body_chars": self.body_chars, "page_level_citable": self.page_level_citable, "truncated": self.truncated,
        }


def is_deep_path(url: str) -> bool:
    """Whether ``url`` names a page under a section, not a host's or a section's home page."""

    path = urlsplit(url).path
    return bool(re.match(r"^/.+/.+", path)) or path.count("/") > 1


def is_stub_path(url: str) -> bool:
    return bool(STUB_PATH_HINTS.search(urlsplit(url).path))


def parse_page(url: str, body: str, *, truncated: bool = False) -> Page:
    """The title, the anchorable headings and the links of one HTML body. Never raises on bad markup."""

    parser = _PageParser()
    try:
        parser.feed(body)
    except Exception:  # noqa: BLE001 - html.parser on a stranger's markup: what was read so far is kept
        pass
    headings = tuple(
        {"tag": heading["tag"], "id": heading["id"], "text": clean_text(heading["text"])}
        for heading in parser.headings
        if heading["id"] and _UNUSABLE_ID.search(heading["id"]) is None
    )
    title = clean_text(parser.title)
    citable = (
        len(headings) == 0
        and parser.body_chars >= MIN_BODY_CHARS_FOR_PAGE_LEVEL
        and is_deep_path(url)
        and bool(title)
        and not REDIRECT_TITLE_RE.search(title)
        and not is_stub_path(url)
    )
    return Page(
        url=url, title=title, headings=headings, body_chars=parser.body_chars, page_level_citable=citable,
        truncated=truncated, links=tuple(parser.links),
    )


@dataclass(frozen=True)
class Fetched:
    """One page fetch. ``page`` is set exactly when ``error`` is ``None``; ``url`` is where the last request went."""

    requested_url: str
    url: str
    status: int | None = None
    error: str | None = None
    page: Page | None = None
    redirects: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.page is not None


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RobotsResponse:
    """What ``RobotsGuard`` reads of an answer (the three attributes of an ``httpx.Response`` it uses)."""

    status_code: int
    headers: Mapping[str, str]
    content: bytes


class _RobotsClient:
    """The client ``RobotsGuard`` is handed: its robots requests go through the same policy as a page's."""

    def __init__(self, owner: "PageFetcher") -> None:
        self._owner = owner

    def get(self, url: str, follow_redirects: bool = False) -> _RobotsResponse:  # noqa: ARG002 - the guard follows its own
        import httpx

        try:
            raw = self._owner._send(url, ROBOTS_MAX_BYTES)
        except (_Refused, FetchNetworkError) as exc:
            # The guard reads an ``httpx`` error as "this host's rules are not known": nothing on it is asked.
            raise httpx.TransportError(str(exc)) from exc
        return _RobotsResponse(raw.status, raw.headers, raw.body)


class PageFetcher:
    """Every request of one course. ``fetcher`` is the network; the rest is the policy of the module docstring.

    ``robots`` is the guard to ask (its cache and its ``Crawl-delay`` pace); with none given, one in memory is
    made: there is no way to fetch without one. ``clock`` and ``sleep`` are the rate limit's (a test passes a
    fake pair); ``cancel`` answers "should this stop now".
    """

    def __init__(
        self, fetcher: Fetcher, *, robots: "RobotsGuard | None" = None, budget: FetchBudget | None = None,
        cancel: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = time.sleep, rate_per_minute: int = RATE_PER_HOST_PER_MINUTE,
    ) -> None:
        if robots is None:
            from .find_jobs.robots_guard import RobotsGuard

            robots = RobotsGuard(clock=clock, sleep=sleep)
        self._fetcher = fetcher
        self._robots = robots
        self._robots_client = _RobotsClient(self)
        self.budget = budget or FetchBudget()
        self._cancel = cancel or (lambda: False)
        self._clock = clock
        self._sleep = sleep
        self._rate = rate_per_minute
        self._sent: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        #: Requests sent (pages, redirect hops, robots files), and how many of them were robots files.
        self.requests = 0
        self.robots_requests = 0

    # -- one request ------------------------------------------------------------------------------

    def _check_cancel(self) -> None:
        if self._cancel():
            raise FetchStopped(STOP_CANCELLED)

    def _throttle(self, host: str) -> None:
        """Wait until ``host`` has had fewer than the rate's requests in the last minute, then count this one."""

        while True:
            with self._lock:
                now = self._clock()
                sent = self._sent.setdefault(host, deque())
                while sent and now - sent[0] >= RATE_WINDOW_SECONDS:
                    sent.popleft()
                if len(sent) < self._rate:
                    sent.append(now)
                    return
                wait = RATE_WINDOW_SECONDS - (now - sent[0])
            self._check_cancel()
            self._sleep(min(wait, _WAIT_STEP_SECONDS))

    def _send(self, url: str, max_bytes: int) -> RawResponse:
        """The ONE place a request leaves from: cancel, the public-address vet, the budget, the rate limit, the network."""

        self._check_cancel()
        target = outbound_guard.safe_public_target(url)
        if target is None:
            raise _Refused(NOT_PUBLIC)
        if not self.budget.take():
            raise FetchStopped(STOP_BUDGET)
        self._throttle(target.host)
        with self._lock:
            self.requests += 1
        return self._fetcher.get(target, max_bytes=max_bytes)

    def _robots_verdict(self, url: str) -> str | None:
        """``None`` when ``url`` may be asked, else ``robots_disallowed`` or ``robots_unknown``."""

        state, asked = self._robots.verdict(self._robots_client, url)
        if asked:
            with self._lock:
                self.robots_requests += asked
        if state == "allowed":
            return None
        return ROBOTS_DISALLOWED if state == ROBOTS_DISALLOWED else ROBOTS_UNKNOWN

    # -- one page -----------------------------------------------------------------------------------

    def fetch_page(self, url: str) -> Fetched:
        """Fetch one HTML page under the policy. A URL that may not be asked or did not answer is ``error``, never a raise.

        Raises only :class:`FetchStopped` (cancelled, or the course's budget is spent). The fragment of ``url``
        is not sent.
        """

        requested = url if isinstance(url, str) else ""
        current = requested.split("#", 1)[0].strip()
        status: int | None = None
        for hop in range(MAX_REDIRECTS + 1):

            def refused(code: str) -> Fetched:
                return Fetched(requested_url=requested, url=current, status=status, error=code, redirects=hop)

            try:
                scheme = urlsplit(current).scheme
            except ValueError:
                return refused(NOT_PUBLIC)
            if scheme != "https":
                return refused(NOT_HTTPS)
            if outbound_guard.safe_public_target(current) is None:
                return refused(NOT_PUBLIC)  # an address literal, a port, a login, a private address: not even robots is asked
            verdict = self._robots_verdict(current)
            if verdict is not None:
                return refused(verdict)
            self._robots.pace(current)
            try:
                raw = self._send(current, MAX_BYTES)
            except _Refused as exc:
                return refused(exc.code)
            except FetchNetworkError:
                return refused(NETWORK_ERROR)
            status = raw.status
            if status in _REDIRECTS:
                location = raw.headers.get("location") or ""
                if not location.strip():
                    return refused(BAD_REDIRECT)
                try:
                    current = urljoin(current, location.strip()).split("#", 1)[0]
                except ValueError:
                    return refused(BAD_REDIRECT)
                continue
            if status != 200:
                return refused(f"http_{status}")
            content_type = raw.headers.get("content-type") or ""
            if content_type and "html" not in content_type.lower():
                return refused(NOT_HTML)
            truncated = len(raw.body) > MAX_BYTES
            page = parse_page(current, _decode(raw.body[:MAX_BYTES], content_type), truncated=truncated)
            return Fetched(requested_url=requested, url=current, status=status, page=page, redirects=hop)
        return Fetched(requested_url=requested, url=current, status=status, error=TOO_MANY_REDIRECTS, redirects=MAX_REDIRECTS)


def _decode(body: bytes, content_type: str) -> str:
    named = re.search(r"charset=([\w-]+)", content_type, re.I)
    try:
        return body.decode(named.group(1) if named else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The bounded same-host crawl of one lesson (ported from spike9c/fetch.py)
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z0-9-]+")
_COMMON_WORDS = frozenset(
    "the and for with from into that this how what why when are was were its your our their you can will use using "
    "used about over under between across more most than then them they not but all any each per via".split()
)


def lesson_words(texts: Iterable[str]) -> list[str]:
    """The words a lesson's candidate links are ranked by: those of its subtopic titles and technologies, once each."""

    words: list[str] = []
    for text in texts:
        for word in _WORD.findall(str(text).lower()):
            if len(word) > 2 and word not in _COMMON_WORDS and word not in words:
                words.append(word)
    return words


def rank_score(url: str, words: Sequence[str]) -> int:
    """How much a link is worth a fetch: 2 for a documentation word in its path, 1 for each lesson word in it."""

    path = urlsplit(url).path.lower()
    score = 2 if PREFER_RE.search(path) else 0
    return score + sum(1 for word in words if word.lower() in path)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def same_host_links(page_url: str, links: Iterable[str], host: str) -> list[str]:
    """The ``https`` links of a page that stay on ``host``, absolute, without a fragment, each once, in page order."""

    found: list[str] = []
    for href in links:
        try:
            absolute = urljoin(page_url, href).split("#", 1)[0]
            parts = urlsplit(absolute)
        except ValueError:
            continue
        if parts.scheme != "https" or (parts.hostname or "").lower() != host:
            continue
        if absolute not in found:
            found.append(absolute)
    return found


@dataclass(frozen=True)
class Crawl:
    """One lesson's crawl. ``pages``: a fetched page (``Page.to_json`` plus ``depth``) or ``{url, error, depth}``."""

    host: str
    pages: tuple[Mapping[str, object], ...]
    fetch_count: int
    #: Seeds on another host than the first seed's: not asked.
    skipped: tuple[str, ...] = ()
    #: ``budget_exhausted`` when the course's budget ended the crawl early; else ``None``.
    stopped: str | None = None

    @property
    def ok_pages(self) -> list[Mapping[str, object]]:
        return [page for page in self.pages if "headings" in page]

    def to_json(self) -> dict[str, object]:
        return {
            "host": self.host, "pages": [dict(page) for page in self.pages], "fetch_count": self.fetch_count,
            "skipped": list(self.skipped), "stopped": self.stopped, "arc_checklist": compute_arc_checklist(self.pages),
        }


def crawl_lesson(
    fetcher: PageFetcher, start_urls: Sequence[str], words: Sequence[str] = (), *, max_pages: int = MAX_PAGES_PER_LESSON,
) -> Crawl:
    """The seeded start pages of one lesson, then the pages they link to on the SAME host, two links deep at most.

    The host is the first start URL's. Depth 0 is every start page on that host; depth 1 is their links and
    depth 2 the links of those, each pool ranked by :func:`rank_score` (ties keep page order) with index and
    redirect stubs left out. A page with no anchorable heading is not expanded. At most ``max_pages`` fetches,
    failures included. A cancel raises :class:`FetchStopped`; a spent budget ends the crawl with what it has.
    """

    host = _host(start_urls[0]) if start_urls else ""
    seeds = [url.split("#", 1)[0] for url in start_urls]
    skipped = tuple(url for url in seeds if _host(url) != host)
    seen: set[str] = set()
    pages: list[Mapping[str, object]] = []
    count = 0
    stopped: str | None = None

    def visit(urls: Sequence[str], depth: int, *, expand_stubs: bool) -> list[str]:
        nonlocal count, stopped
        pool: list[str] = []
        for url in urls:
            if stopped is not None or count >= max_pages:
                break
            if url in seen:
                continue
            seen.add(url)
            try:
                fetched = fetcher.fetch_page(url)
            except FetchStopped as exc:
                if exc.code != STOP_BUDGET:
                    raise
                stopped = exc.code
                break
            count += 1
            seen.add(fetched.url)
            if not fetched.ok or fetched.page is None:
                pages.append({"url": url, "error": fetched.error, "depth": depth})
                continue
            if _host(fetched.url) != host:
                pages.append({"url": url, "error": LEFT_HOST, "depth": depth})
                continue
            page = fetched.page
            pages.append({**page.to_json(), "depth": depth})
            if not page.headings and (not expand_stubs or is_stub_path(page.url)):
                continue  # an index or redirect stub: its links are not worth the budget
            pool.extend(link for link in same_host_links(page.url, page.links, host) if link not in seen)
        ranked = [url for url in dict.fromkeys(pool) if not is_stub_path(url)]
        ranked.sort(key=lambda url: -rank_score(url, words))
        return ranked

    first = visit([url for url in seeds if _host(url) == host], 0, expand_stubs=True)
    second = visit(first, 1, expand_stubs=False)
    visit(second, 2, expand_stubs=False)
    return Crawl(host=host, pages=tuple(pages), fetch_count=count, skipped=skipped, stopped=stopped)


# ---------------------------------------------------------------------------
# The arc checklist (ported from spike9c/fetch.py)
# ---------------------------------------------------------------------------

#: The arc of learning a tool, in the order an engineer walks it, and the words that mark a page as a stage's.
#: A page "has" a stage when its URL, title or headings (id or text) hold one of the stage's keywords. Computed
#: in code from the fetched pages, never by a model. The list is the spike's: loose on purpose (one page per
#: listed stage is what the practice-path step asks for) and tuned on three lessons only.
ARC_STAGE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("first_run", ("quickstart", "getting-started", "getting started", "first run", "tutorial")),
    ("core_object_model", ("concepts", "architecture", "design", "core concepts", "intro", "introduction")),
    ("persist_compare", ("compare", "search", "query", "persist", "point-in-time", "offline", "online")),
    ("package_register_promote", ("registry", "model-registry", "register", "alias", "package", "project")),
    (
        "serve_deploy_locally",
        (
            "serving", "serve a model", "models serve", "inference server", "scoring endpoint",
            "deploy a model", "deploy-model", "model deployment", "rest api for a model",
        ),
    ),
    ("reproduce_automate", ("reproduce", "ci", "docker", "compose", "pin", "environment", "automate")),
    (
        "production_like",
        (
            "production", "cluster", "multi-container", "backend", "postgres", "minio", "scale",
            "tracking server", "backend store",
        ),
    ),
    ("windowing", ("window", "windowing", "tumbling", "hopping", "aggregate", "aggregation")),
    ("retention", ("retention", "topic-configs", "topic configs", "log.retention")),
    ("compaction", ("compaction", "compact")),
    ("materialize", ("materialize", "materialization")),
)


def page_text_blob(page: Mapping[str, Any]) -> str:
    parts = [str(page.get("url", "")), str(page.get("title", ""))]
    for heading in page.get("headings", []):
        parts.append(str(heading.get("id", "")))
        parts.append(str(heading.get("text", "")))
    return " ".join(parts).lower()


def compute_arc_checklist(pages: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, object]]]:
    """``{stage: [{page_index, url, title}]}`` for the stages some fetched page matches; a stage with none is absent.

    ``page_index`` is the page's place among the pages that were fetched (the failed ones are not counted), which
    is the number a prompt shows the page under.
    """

    ok_pages = [page for page in pages if "headings" in page]
    checklist: dict[str, list[dict[str, object]]] = {}
    for stage, keywords in ARC_STAGE_KEYWORDS:
        hits = [
            {"page_index": index, "url": page["url"], "title": page.get("title", "")}
            for index, page in enumerate(ok_pages)
            if any(keyword in page_text_blob(page) for keyword in keywords)
        ]
        if hits:
            checklist[stage] = hits
    return checklist


__all__ = [
    "ARC_STAGE_KEYWORDS",
    "BAD_REDIRECT",
    "FETCH_BUDGET",
    "INVISIBLE_RE",
    "LEFT_HOST",
    "MAX_BYTES",
    "MAX_PAGES_PER_LESSON",
    "MAX_REDIRECTS",
    "MIN_BODY_CHARS_FOR_PAGE_LEVEL",
    "NETWORK_ERROR",
    "NOT_HTML",
    "NOT_HTTPS",
    "NOT_PUBLIC",
    "RATE_PER_HOST_PER_MINUTE",
    "ROBOTS_DISALLOWED",
    "ROBOTS_UNKNOWN",
    "STOP_BUDGET",
    "STOP_CANCELLED",
    "TOO_MANY_REDIRECTS",
    "Crawl",
    "FetchBudget",
    "FetchNetworkError",
    "FetchStopped",
    "Fetched",
    "Fetcher",
    "HttpxFetcher",
    "Page",
    "PageFetcher",
    "RawResponse",
    "clean_text",
    "compute_arc_checklist",
    "crawl_lesson",
    "is_deep_path",
    "is_stub_path",
    "lesson_words",
    "page_text_blob",
    "parse_page",
    "rank_score",
    "same_host_links",
    "strip_invisible",
]
