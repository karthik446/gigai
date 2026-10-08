"""A board host's ``robots.txt``, read once a day, before Scout asks that host for a list (0.1.11.8).

Why: the 0.1.11.8 providers include systems where every company is its own host
(``<token>.recruitee.com``, ``<token>.pinpointhq.com``, ``<token>.breezy.hr``) and
whose tenants can publish their own rules, and the spike found vendor hosts whose
rules disallow the very endpoint a scraper would use (SmartRecruiters, UKG). Scout
reads public feeds; it does not read what a host's ``robots.txt`` says not to.

Rules (``urllib.robotparser``): the ``User-agent: *`` group and, when present, a
group for ``GigAI`` (the token of ``bindings.user_agent``). A host answers once per
:data:`TTL_SECONDS` per process, and the answer is kept on disk under the board
cache (``<cache root>/../robots/<host>.json``) so a background check that restarts
does not ask again. A host with no readable ``robots.txt`` (404, 401, a redirect, a
network error, a non-text body) has no rules: the request goes ahead. Only an
explicit disallow stops it: :func:`check` raises ``ATSBoardClientError`` with code
``robots_disallowed``, which the sources update counts like any other board failure
and the board reads as unreadable until the next day's check.

The guard is one request per host per day. For the three vendor-API providers that is
three requests a day in all; for a subdomain provider it is one per company per day
beside the list request itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING
from urllib import robotparser
from urllib.parse import urlsplit

from .ats_board_clients import ATSBoardClientError

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

#: How long one host's rules are trusted before they are read again.
TTL_SECONDS = 24 * 3600
#: The agent token the rules are read for, beside ``*``.
AGENT_TOKEN = "GigAI"
#: A robots body larger than this is not read (a misconfigured host serving a page): no rules.
MAX_BODY_BYTES = 512 * 1024
ROBOTS_DISALLOWED = "robots_disallowed"


@dataclass(frozen=True)
class HostRules:
    host: str
    fetched_at: float
    #: The robots body, or ``None`` when the host has no readable rules.
    body: str | None

    def allows(self, url: str) -> bool:
        if self.body is None:
            return True
        parser = robotparser.RobotFileParser()
        parser.parse(self.body.splitlines())
        return parser.can_fetch(AGENT_TOKEN, url) and parser.can_fetch("*", url)


class RobotsGuard:
    """Per-host rules, cached in memory for the process and on disk when a directory is given."""

    def __init__(self, cache_dir: Path | None = None, *, ttl_seconds: float = TTL_SECONDS) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.ttl_seconds = ttl_seconds
        self._rules: dict[str, HostRules] = {}
        self._lock = threading.Lock()

    def check(self, client: "httpx.Client", url: str, provider: str, board_token: str) -> None:
        """Raise ``ATSBoardClientError(robots_disallowed)`` when the host's rules disallow ``url``; else return."""

        if not self.allows(client, url):
            raise ATSBoardClientError(ROBOTS_DISALLOWED, f"{provider} board {board_token!r}: the host's robots.txt disallows the feed")

    def allows(self, client: "httpx.Client", url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return True
        rules = self._fresh(host)
        if rules is None:
            rules = self._fetch(client, host)
            with self._lock:
                self._rules[host] = rules
            self._store(rules)
        return rules.allows(url)

    def _fresh(self, host: str) -> HostRules | None:
        now = time.time()
        with self._lock:
            rules = self._rules.get(host)
        if rules is None:
            rules = self._load(host)
            if rules is not None:
                with self._lock:
                    self._rules[host] = rules
        if rules is not None and now - rules.fetched_at < self.ttl_seconds:
            return rules
        return None

    def _fetch(self, client: "httpx.Client", host: str) -> HostRules:
        import httpx

        now = time.time()
        try:
            response = client.get(f"https://{host}/robots.txt")
        except httpx.HTTPError:
            return HostRules(host, now, None)
        if response.status_code != 200 or len(response.content) > MAX_BODY_BYTES:
            return HostRules(host, now, None)
        content_type = response.headers.get("content-type", "")
        if "html" in content_type.lower():
            return HostRules(host, now, None)
        try:
            body = response.content.decode("utf-8")
        except UnicodeDecodeError:
            return HostRules(host, now, None)
        return HostRules(host, now, body)

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
        return HostRules(host, float(fetched_at), body)

    def _store(self, rules: HostRules) -> None:
        path = self._path(rules.host)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps({"host": rules.host, "fetched_at": rules.fetched_at, "body": rules.body}), "utf-8")
            os.replace(temp, path)
        except OSError:
            return


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
    """The process's guard, on disk next to the board cache (``<cache root>/../robots``), or ``None`` when switched off."""

    global _SHARED
    if not robots_enabled(environ):
        return None
    cache_dir = Path(cache_root).parent / "robots" if cache_root is not None else None
    with _SHARED_LOCK:
        if _SHARED is None or (cache_dir is not None and _SHARED.cache_dir != cache_dir):
            _SHARED = RobotsGuard(cache_dir)
        return _SHARED


__all__ = ["AGENT_TOKEN", "HostRules", "ROBOTS_DISALLOWED", "ROBOTS_ENV", "RobotsGuard", "TTL_SECONDS", "robots_enabled", "shared_guard"]
