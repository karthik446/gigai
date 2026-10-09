"""A board host's ``robots.txt``, read once a day, before Scout asks that host for anything (0.1.11.8).

Why: the 0.1.11.8 providers include systems where every company is its own host
(``<token>.recruitee.com``, ``<token>.pinpointhq.com``, ``<token>.breezy.hr``) and
whose tenants can publish their own rules, and the spike found vendor hosts whose
rules disallow the very endpoint a scraper would use (SmartRecruiters, UKG). Scout
reads public feeds; it does not read what a host's ``robots.txt`` says not to.

Rules (RFC 9309, matched here, not by ``urllib.robotparser``, which knows neither
``*`` nor ``$``): the group for ``GigAI`` (the token of ``board_headers.user_agent``)
when the file has one, else the ``User-agent: *`` group. Within the group the longest
matching pattern decides and ``Allow`` wins a tie; ``*`` is any run of characters and a
trailing ``$`` ends the match. A ``Crawl-delay`` in the group is that host's pace
(:meth:`RobotsGuard.pace`, at most :data:`MAX_CRAWL_DELAY_SECONDS`).

What a host's answer means:

* ``200``: the body's rules, the first :data:`MAX_BODY_BYTES` of it, decoded as UTF-8
  (a byte-order mark is dropped, a bad byte is replaced); the content type is not read;
* a redirect: followed, up to :data:`MAX_REDIRECTS` times, while it stays on the same
  site; one that leaves the site, or a longer chain, is a host with no rules;
* ``404`` / ``410`` / ``401`` / ``403`` and any other ``4xx``: no rules, everything is allowed;
* ``429``, ``5xx``, a timeout or no answer: NOT KNOWN. Nothing on that host is asked for
  :data:`UNKNOWN_TTL_SECONDS`, then the file is asked for again (``robots_unknown``); a ``429`` that names a
  ``Retry-After`` is asked again when that has passed (at least :data:`MIN_RETRY_AFTER_SECONDS`, at most the hour).
  The refusal carries ``host`` and ``retry_in`` (seconds until the file is asked again, only when the host named
  one), which the sources update reads to wait it out or to leave the host's boards for the next update.

A host answers once per :data:`TTL_SECONDS` per process (one lock per host, so four
workers make one request), and the answer is kept on disk under the board cache
(``<cache root>/../robots/<host>.json``) so a background check that restarts does not
ask again. :func:`RobotsGuard.check` raises ``ATSBoardClientError`` with code
``robots_disallowed`` or ``robots_unknown``, which the sources update counts like any
other board failure.

Two ways in. :class:`GuardedClient` wraps the client the board clients are handed
(``ATSBoardClients(robots=...)``): every request a board makes, the list and each detail,
is asked about first. :func:`install` puts the same question on an ``httpx.Client``'s
request hook, for the single-posting paths (the liveness check, a description lookup);
there only a board's own host is asked about, and a refusal is an ``httpx`` error
(:class:`RobotsRefused`), which those paths already read as "did not answer".
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote, urljoin, urlsplit

from .ats_board_clients import ATSBoardClientError
from .board_headers import PRODUCT_TOKEN

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

#: How long one host's rules are trusted before they are read again.
TTL_SECONDS = 24 * 3600
#: How long a host whose robots.txt could not be read (429, 5xx, a timeout) is left alone before it is asked again.
UNKNOWN_TTL_SECONDS = 3600
#: A ``429`` on robots.txt with a shorter ``Retry-After`` than this is asked again after this (never a request per board).
MIN_RETRY_AFTER_SECONDS = 5.0
#: The agent token the rules are read for; a file without a group for it is read for ``*``.
AGENT_TOKEN = PRODUCT_TOKEN
#: At most this much of a robots body is read; the rest is ignored (RFC 9309 asks for at least 500 KiB).
MAX_BODY_BYTES = 512 * 1024
#: How many same-site redirects a robots request follows.
MAX_REDIRECTS = 5
#: A ``Crawl-delay`` longer than this is read as this.
MAX_CRAWL_DELAY_SECONDS = 10.0
ROBOTS_DISALLOWED = "robots_disallowed"
ROBOTS_UNKNOWN = "robots_unknown"

STATE_RULES = "rules"
STATE_NONE = "none"
STATE_UNKNOWN = "unknown"
_STATES = frozenset({STATE_RULES, STATE_NONE, STATE_UNKNOWN})
#: How long a paced wait sleeps before it looks at the stop event again.
_PACE_POLL_SECONDS = 0.25
_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_STARS = re.compile(r"\*+")


def _normalized(value: str) -> str:
    """One spelling for a path or a pattern: non-ASCII percent-encoded, escapes in upper case."""

    encoded = quote(value, safe="/*$?=&%:@!+,;~-._()'[]#")
    return _ESCAPE.sub(lambda match: match.group(0).upper(), encoded)


@dataclass(frozen=True)
class _Rule:
    allow: bool
    length: int
    pattern: "re.Pattern[str]"


@dataclass(frozen=True)
class _Group:
    rules: tuple[_Rule, ...]
    crawl_delay: float | None


def _rule(allow: bool, value: str) -> _Rule | None:
    value = _STARS.sub("*", _normalized(value))
    if not value:
        return None  # ``Disallow:`` with no path disallows nothing
    anchored = value.endswith("$")
    body = value[:-1] if anchored else value
    pattern = ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else "")
    return _Rule(allow, len(value), re.compile(pattern, re.DOTALL))


def parse_groups(body: str) -> dict[str, _Group]:
    """``{agent token (lower case): its rules}``; groups naming the same agent are one group."""

    rules: dict[str, list[_Rule]] = {}
    delays: dict[str, float] = {}
    agents: list[str] = []
    in_rules = False
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            token = re.split(r"[/\s]", value.lower(), maxsplit=1)[0]
            if token:
                agents.append(token)
                rules.setdefault(token, [])
        elif key in ("allow", "disallow"):
            in_rules = True
            found = _rule(key == "allow", value)
            if found is not None:
                for agent in agents:
                    rules[agent].append(found)
        elif key == "crawl-delay":
            in_rules = True
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay > 0 and delay == delay:
                for agent in agents:
                    delays[agent] = max(delays.get(agent, 0.0), min(delay, MAX_CRAWL_DELAY_SECONDS))
    return {agent: _Group(tuple(found), delays.get(agent)) for agent, found in rules.items()}


@dataclass(frozen=True)
class HostRules:
    host: str
    fetched_at: float
    #: The robots body, or ``None`` when the host has no rules or its rules are not known.
    body: str | None
    #: ``rules`` (a body), ``none`` (the host has no robots.txt: everything is allowed) or ``unknown`` (it could not be read: nothing is).
    state: str = STATE_RULES
    #: ``unknown`` only: the ``Retry-After`` (seconds) of the ``429`` that left the file unread; ``None`` when none was named.
    retry_after: float | None = None

    @cached_property
    def group(self) -> _Group | None:
        """The group read for GigAI: its own when the file names it, else the ``*`` group."""

        if self.body is None or self.state != STATE_RULES:
            return None
        groups = parse_groups(self.body)
        return groups.get(AGENT_TOKEN.lower()) or groups.get("*")

    @property
    def crawl_delay(self) -> float | None:
        group = self.group
        return group.crawl_delay if group is not None else None

    def allows(self, url: str) -> bool:
        if self.state == STATE_UNKNOWN:
            return False
        group = self.group
        if group is None:
            return True
        parts = urlsplit(url)
        path = _normalized((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        if path == "/robots.txt":
            return True
        best: _Rule | None = None
        for rule in group.rules:
            if rule.pattern.match(path) is None:
                continue
            if best is None or rule.length > best.length or (rule.length == best.length and rule.allow):
                best = rule
        return best is None or best.allow


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _same_site(first: str, second: str) -> bool:
    """The same host, a host under the other, or two hosts under one two-label domain."""

    if first == second or first.endswith("." + second) or second.endswith("." + first):
        return True
    return first.split(".")[-2:] == second.split(".")[-2:] and first.count(".") >= 1


class RequestTally:
    """A thread-safe count of the robots requests one owner caused (an update adds it to its ``requests``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.value = 0

    def add(self, count: int) -> None:
        if count:
            with self._lock:
                self.value += count


class RobotsGuard:
    """Per-host rules, cached in memory for the process and on disk when a directory is given."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        ttl_seconds: float = TTL_SECONDS,
        unknown_ttl_seconds: float = UNKNOWN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.ttl_seconds = ttl_seconds
        self.unknown_ttl_seconds = unknown_ttl_seconds
        self._clock = clock
        self._sleep = sleep
        self._rules: dict[str, HostRules] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_start: dict[str, float] = {}
        self._reading = threading.local()
        #: Every robots request this guard has made (redirect hops included).
        self.requests = 0

    # -- the question ---------------------------------------------------------------------------

    def check(
        self, client: Any, url: str, provider: str, board_token: str, *, counted: Callable[[int], object] | None = None
    ) -> None:
        """Raise ``ATSBoardClientError`` (``robots_disallowed`` / ``robots_unknown``) when ``url`` may not be asked; else return.

        ``counted`` is told how many robots requests the answer took (none when the host's rules were already held).
        """

        state, requests = self.verdict(client, url)
        if counted is not None and requests:
            counted(requests)
        if state == STATE_UNKNOWN:
            error = ATSBoardClientError(ROBOTS_UNKNOWN, f"{provider} board {board_token!r}: the host's robots.txt could not be read; it is asked again within the hour")
            error.host = _host_of(url)  # type: ignore[attr-defined]
            error.retry_in = self.retry_in(url)  # type: ignore[attr-defined]
            raise error
        if state == ROBOTS_DISALLOWED:
            raise ATSBoardClientError(ROBOTS_DISALLOWED, f"{provider} board {board_token!r}: the host's robots.txt disallows the request")

    def allows(self, client: Any, url: str) -> bool:
        return self.verdict(client, url)[0] == "allowed"

    def verdict(self, client: Any, url: str) -> tuple[str, int]:
        """``("allowed" | "robots_disallowed" | "unknown", robots requests made for this answer)``."""

        host = _host_of(url)
        if not host or getattr(self._reading, "active", False):
            return "allowed", 0  # a request made while reading a robots file (a redirect hop) is that read
        requests = 0
        rules = self._fresh(host)
        if rules is None:
            with self._host_lock(host):
                rules = self._fresh(host)  # another worker may have read it while this one waited
                if rules is None:
                    rules, requests = self._fetch(client, host)
                    with self._lock:
                        self._rules[host] = rules
                        self.requests += requests
                    self._store(rules)
        if rules.state == STATE_UNKNOWN:
            return STATE_UNKNOWN, requests
        return ("allowed" if rules.allows(url) else ROBOTS_DISALLOWED), requests

    def pace(self, url: str, stop: threading.Event | None = None) -> None:
        """Wait until ``url``'s host may be asked again: its ``Crawl-delay`` after the last request to it.

        A host with no ``Crawl-delay`` (or whose rules are not held yet) never waits. The wait is taken in
        short steps and ends early when ``stop`` is set (the caller then decides what a stop means).
        """

        host = _host_of(url)
        if not host:
            return
        with self._lock:
            rules = self._rules.get(host)
            delay = (rules.crawl_delay if rules is not None else None) or 0.0
            now = self._clock()
            last = self._last_start.get(host)
            start = now if last is None else max(now, last + delay)
            self._last_start[host] = start
        while True:
            left = start - self._clock()
            if left <= 0 or (stop is not None and stop.is_set()):
                return
            self._sleep(min(left, _PACE_POLL_SECONDS))

    def retry_in(self, url: str) -> float | None:
        """Seconds until ``url``'s host is asked for its robots.txt again, when its ``429`` named a ``Retry-After``; else ``None``."""

        with self._lock:
            rules = self._rules.get(_host_of(url))
        if rules is None or rules.state != STATE_UNKNOWN or rules.retry_after is None:
            return None
        return max(0.0, self._ttl(rules) - (time.time() - rules.fetched_at))

    def crawl_delay(self, url: str) -> float | None:
        """The ``Crawl-delay`` held for ``url``'s host, or ``None`` (no request is made to find out)."""

        with self._lock:
            rules = self._rules.get(_host_of(url))
        return rules.crawl_delay if rules is not None else None

    # -- reading a host's file -------------------------------------------------------------------

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = self._host_locks[host] = threading.Lock()
            return lock

    def _ttl(self, rules: HostRules) -> float:
        if rules.state != STATE_UNKNOWN:
            return self.ttl_seconds
        if rules.retry_after is not None:
            return min(self.unknown_ttl_seconds, max(rules.retry_after, MIN_RETRY_AFTER_SECONDS))
        return self.unknown_ttl_seconds

    def _fresh(self, host: str) -> HostRules | None:
        now = time.time()
        with self._lock:
            rules = self._rules.get(host)
        if rules is None:
            rules = self._load(host)
            if rules is not None:
                with self._lock:
                    self._rules[host] = rules
        if rules is not None and now - rules.fetched_at < self._ttl(rules):
            return rules
        return None

    def _fetch(self, client: Any, host: str) -> tuple[HostRules, int]:
        import httpx

        now = time.time()
        url = f"https://{host}/robots.txt"
        requests = 0
        self._reading.active = True
        try:
            for _ in range(MAX_REDIRECTS + 1):
                requests += 1
                with self._lock:  # the robots request is a request to the host: its Crawl-delay counts from here
                    self._last_start[host] = max(self._last_start.get(host, 0.0), self._clock())
                try:
                    response = client.get(url, follow_redirects=False)
                except httpx.HTTPError:
                    return HostRules(host, now, None, STATE_UNKNOWN), requests
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    target = urljoin(url, response.headers.get("location") or "")
                    if urlsplit(target).scheme != "https" or not _same_site(host, _host_of(target)):
                        return HostRules(host, now, None, STATE_NONE), requests
                    url = target
                    continue
                if status == 200:
                    body = bytes(response.content)[:MAX_BODY_BYTES].decode("utf-8-sig", errors="replace")
                    return HostRules(host, now, body, STATE_RULES), requests
                if status == 429 or status >= 500:
                    asked = None
                    if status == 429:
                        from .market_acquisition import parse_retry_after

                        asked = parse_retry_after(response.headers.get("retry-after"))
                    return HostRules(host, now, None, STATE_UNKNOWN, asked), requests
                return HostRules(host, now, None, STATE_NONE), requests
            return HostRules(host, now, None, STATE_NONE), requests  # a longer chain than we follow: no rules
        finally:
            self._reading.active = False

    # -- the disk cache (optional) --------------------------------------------------------------

    def _path(self, host: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in host)
        return self.cache_dir / f"{safe}.json"

    def _load(self, host: str) -> HostRules | None:
        path = self._path(host)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("host") != host:
            return None
        fetched_at = payload.get("fetched_at")
        body = payload.get("body")
        if not isinstance(fetched_at, (int, float)) or isinstance(fetched_at, bool):
            return None
        if body is not None and type(body) is not str:
            return None
        # A file written before the state was stored: a body is rules, no body is no rules.
        state = payload.get("state", STATE_RULES if body is not None else STATE_NONE)
        if state not in _STATES or (state == STATE_RULES) != (body is not None):
            return None
        asked = payload.get("retry_after")
        if state != STATE_UNKNOWN or not isinstance(asked, (int, float)) or isinstance(asked, bool):
            asked = None
        return HostRules(host, float(fetched_at), body, state, float(asked) if asked is not None else None)

    def _store(self, rules: HostRules) -> None:
        path = self._path(rules.host)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".json.tmp{threading.get_ident()}")
            temp.write_text(json.dumps({"host": rules.host, "fetched_at": rules.fetched_at, "body": rules.body, "state": rules.state, "retry_after": rules.retry_after}), "utf-8")
            os.replace(temp, path)
        except OSError:
            return


class GuardedClient:
    """The client a board's fetch is handed: every ``get``/``post`` it makes is asked about first.

    One per board fetch (``ATSBoardClients``): the list URL and every detail URL go through :meth:`RobotsGuard.check`,
    so a host that disallows only the detail path keeps its list. Everything else is the wrapped client's. A wrapped
    client that paces hosts itself (``paces_hosts``: the update's throttled client) is not paced again here.
    """

    def __init__(self, client: Any, guard: RobotsGuard, provider: str, board_token: str, *, counted: Callable[[int], object] | None = None) -> None:
        self._client = client
        self._guard = guard
        self._provider = provider
        self._board_token = board_token
        self._counted = counted

    def _ask(self, url: object) -> None:
        self._guard.check(self._client, str(url), self._provider, self._board_token, counted=self._counted)
        if not getattr(self._client, "paces_hosts", False):
            self._guard.pace(str(url))

    def get(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._ask(url)
        return self._client.get(url, *args, **kwargs)

    def post(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._ask(url)
        return self._client.post(url, *args, **kwargs)

    def head(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._ask(url)
        return self._client.head(url, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _refusal(code: str, request: "httpx.Request") -> Exception:
    import httpx

    class RobotsRefused(httpx.RequestError):
        """A request the host's robots.txt does not allow (or whose robots.txt is not known): never sent."""

    error = RobotsRefused(f"{request.url.host}: {code}", request=request)
    error.code = code  # type: ignore[attr-defined]
    return error


def install(client: "httpx.Client", guard: RobotsGuard | None, *, counted: Callable[[int], object] | None = None) -> "httpx.Client":
    """Ask ``guard`` before every request ``client`` sends to a board's own host; returns ``client``.

    For the single-posting paths, which build their own client. A request to any other host (a company's careers
    page) is not asked about. A refusal raises an ``httpx.RequestError`` with ``code`` ``robots_disallowed`` or
    ``robots_unknown`` before anything is sent. ``guard`` ``None`` (the guard is switched off) installs nothing.
    """

    if guard is None:
        return client
    from .providers import source_from_host

    def ask(request: "httpx.Request") -> None:
        if request.url.path == "/robots.txt" or source_from_host(request.url.host) is None:
            return
        url = str(request.url)
        state, requests = guard.verdict(client, url)
        if counted is not None and requests:
            counted(requests)
        if state != "allowed":
            raise _refusal(ROBOTS_UNKNOWN if state == STATE_UNKNOWN else ROBOTS_DISALLOWED, request)
        guard.pace(url)

    client.event_hooks["request"] = [*client.event_hooks.get("request", []), ask]
    return client


_SHARED: RobotsGuard | None = None
_SHARED_LOCK = threading.Lock()
#: ``0`` switches the guard off (the test suite's autouse fixture: no fake board may be asked for a robots file it
#: does not model; the guard's own tests build one explicitly). Anything else, or unset, leaves it on.
ROBOTS_ENV = "GIGAI_SCOUT_ROBOTS"
_OFF = frozenset({"0", "false", "off", "no"})


def robots_enabled(environ: "dict[str, str] | os._Environ[str] | None" = None) -> bool:
    value = (os.environ if environ is None else environ).get(ROBOTS_ENV, "")
    return value.strip().lower() not in _OFF


def shared_guard(cache_root: Path | None, environ: "dict[str, str] | os._Environ[str] | None" = None) -> RobotsGuard | None:
    """The process's guard, on disk next to the board cache (``<cache root>/../robots``), or ``None`` when switched off.

    ``cache_root`` ``None`` (a caller that knows no home) is the guard the process already has, else one in memory.
    """

    global _SHARED
    if not robots_enabled(environ):
        return None
    cache_dir = Path(cache_root).parent / "robots" if cache_root is not None else None
    with _SHARED_LOCK:
        if _SHARED is None or (cache_dir is not None and _SHARED.cache_dir != cache_dir):
            _SHARED = RobotsGuard(cache_dir)
        return _SHARED


__all__ = [
    "AGENT_TOKEN",
    "GuardedClient",
    "HostRules",
    "MAX_BODY_BYTES",
    "MAX_CRAWL_DELAY_SECONDS",
    "MAX_REDIRECTS",
    "MIN_RETRY_AFTER_SECONDS",
    "ROBOTS_DISALLOWED",
    "ROBOTS_ENV",
    "ROBOTS_UNKNOWN",
    "RequestTally",
    "RobotsGuard",
    "TTL_SECONDS",
    "UNKNOWN_TTL_SECONDS",
    "install",
    "parse_groups",
    "robots_enabled",
    "shared_guard",
]
