"""Is a posting still open on its board? (0.1.11.4 R1)

A posting the board no longer lists stayed "Matched" until the next sources
update diffed its board, and Scout spent the operator's time and model calls
on it. :func:`check_posting_live` asks the board about ONE posting, only when
that posting is about to be acted on (its page opens, Assess, Generate PDF,
Mark applied), never in a pass over the stored postings.

The answer is ``open``, ``closed`` or ``unknown``:

* Greenhouse: the public single-job endpoint, through
  ``job_input.fetch_missing_description`` (the request Scout already makes for
  a missing description). ``404`` / ``410`` is ``closed``; a job object is
  ``open``.
* Lever and Ashby have no public single-job endpoint: the board's list (one
  request per BOARD, conditional, through ``ats_board_clients``' own cached
  request). ``closed`` only when a ``200`` (or a ``304`` of a kept body) list
  holds neither the posting's URL nor its id.
* Anything else is ``unknown`` and NEVER ``closed``: a ``5xx``, a ``429``, a
  redirect (the client does not follow one), a timeout, a network error, a
  body that is not the expected JSON, a URL on no known board.

One request per posting: the answer is kept for :data:`CACHE_SECONDS` in the
process and in ``<home>/cache/scout/liveness/jobs/`` (a cache as the other
files under ``<home>/cache/scout``: safe to delete; the URL, the state and a
time, no posting text). An ``unknown`` is kept in the process only, for
:data:`UNKNOWN_SECONDS`. Requests to one provider are a polite interval apart
(``AcquireLimits``' own, as a batch's description requests) and time out
after a few seconds. No model is called. The caller is never blocked: an
``unknown`` proceeds, and nothing here raises.

The board lists are kept apart from the sources update's own response cache
(``<home>/cache/scout/liveness/boards``): a body stored here must never make
the next update's request a ``304`` of a body the index has not diffed.

The company's own page (0.1.11.4 7b). A board can hand out the company's own
careers route as the posting's URL, and that page can be down while the board
still lists the job. :func:`board_job_url` builds the posting's address ON the
board from the index's board token and posting id, and it is offered beside
the stored URL whenever that URL is not on the board's host. When the board
answered ``open`` for such a posting AND its page is being opened
(``company_page=True``: one job, never a batch), ONE extra ``GET`` asks the
stored URL: ``404`` / ``410`` is ``down``, a ``2xx`` is ``ok``, everything
else is ``unknown`` (a ``5xx``, a redirect, a timeout say nothing). That
answer is kept for :data:`CACHE_SECONDS` too
(``<home>/cache/scout/liveness/pages/``) and NEVER closes or removes
anything: it only changes which link the page puts first.

The stored URL is the board's text, written by strangers (0.1.11.4 S2), so
that one ``GET`` goes through ``outbound_guard``: the name is resolved ONCE,
a name with any address that is not on the public internet (this machine, a
private network, the cloud metadata address) is never requested (``unknown``,
nothing written), and the connection is made to the vetted address itself,
so no second lookup can answer differently. A board token or posting id that
is not a plain name never becomes part of a board's endpoint (``unknown``).

``GIGAI_SCOUT_POSTING_LIVENESS=0`` turns the check off (every answer is
``unknown``, no request). Under the fixture transport
(``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1``) no request is made either: that
transport answers ``404`` to everything it does not know.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Iterable, Mapping
from urllib.parse import quote, urlsplit

from ...canonical import digest_imported_bytes
from .contracts import FindJobsContractError, normalize_url, parse_board_url
from .outbound_guard import pinned_stream, public_looking_host, safe_path_segment, safe_public_target
from .providers import registry
from .providers import spec as provider_spec

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


OPEN = "open"
CLOSED = "closed"
UNKNOWN = "unknown"
STATES: tuple[str, ...] = (OPEN, CLOSED, UNKNOWN)

#: An ``open`` / ``closed`` answer is not asked for again within this long.
CACHE_SECONDS = 3600.0
#: An ``unknown`` (the board did not answer) is not asked for again within this long; kept in the process only.
UNKNOWN_SECONDS = 300.0
#: One liveness request waits this long at most.
TIMEOUT_SECONDS = 4.0
CONNECT_TIMEOUT_SECONDS = 3.0
#: In one batch, a provider that failed to answer this many times in a row is not asked again (the rest are ``unknown``).
BATCH_GIVE_UP_AFTER = 2

#: ``0`` / ``false`` / ``off`` / ``no``: no liveness request is ever made.
LIVENESS_ENV = "GIGAI_SCOUT_POSTING_LIVENESS"
CACHE_SCHEMA = "scout-posting-liveness:1"

#: What a closed posting's page, PDF and "Mark applied" say. Plain words; the action is never refused.
CLOSED_NOTE = "This posting looks closed: check it before you apply"
#: The typed refusal of an Assess of one closed posting (no model call is made for it).
ERROR_POSTING_CLOSED = "posting_closed"
CLOSED_ASSESS_MESSAGE = "This posting is closed: its board no longer lists it, so it was not assessed."


#: The stored URL of an open posting, asked once when its page opens (only a URL that is not on the board's host).
PAGE_DOWN = "down"
PAGE_OK = "ok"
PAGE_STATES: tuple[str, ...] = (PAGE_DOWN, PAGE_OK, UNKNOWN)
PAGE_CACHE_SCHEMA = "scout-posting-company-page:1"
#: What the job page says when the company's own page answers 404 / 410 and the board still lists the job.
COMPANY_PAGE_DOWN_NOTE = "The company page for this job is down; the job is still open on the board"

_OFF = frozenset({"0", "false", "off", "no"})
#: A URL on one of these is the board's own page, not a company site: no second link, no extra request.
#: 0.1.11.8: from the registry (``providers.py``): every board host suffix, each provider's public job page when the
#: posting id alone names it, and the providers whose LIST answers a liveness check (every one but Greenhouse,
#: whose single-job endpoint is asked instead).
_BOARD_DOMAINS = tuple(sorted({fragment for item in registry().values() for fragment in item.source_hosts}))
_BOARD_JOB_URL = {name: item.job_url for name, item in registry().items() if item.job_url is not None}
_LIST_PROVIDERS = frozenset(name for name in registry() if name != "greenhouse")


@dataclass(frozen=True)
class Liveness:
    """One answer: the ``state``, when the board was asked (``None``: it never was), and whether THIS call asked it."""

    state: str
    checked_at: str | None = None
    requested: bool = False

    @property
    def closed(self) -> bool:
        return self.state == CLOSED


@dataclass(frozen=True)
class _Board:
    urls: frozenset[str]
    ids: frozenset[str]


_LOCK = threading.Lock()  # the caches below
_PACE = threading.Lock()  # one request start at a time; holds the polite interval per provider
_NEXT_REQUEST: dict[str, float] = {}
_ANSWERS: dict[tuple[str, str], tuple[str, str, float]] = {}  # (home, job) -> (state, checked_at, monotonic)
_BOARDS: dict[tuple[str, str, str], tuple[_Board, float]] = {}  # (home, provider, token) -> (list, monotonic)
_PAGES: dict[tuple[str, str], tuple[str, float]] = {}  # (home, job) -> (company page state, monotonic)


def reset_memory() -> None:
    """Forget what this process holds (tests; the file cache stays)."""

    with _LOCK:
        _ANSWERS.clear()
        _BOARDS.clear()
        _PAGES.clear()
        _NEXT_REQUEST.clear()


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """False when the environment turns the check off, or only the fixture transport is there to answer."""

    env = os.environ if environ is None else environ
    if (env.get(LIVENESS_ENV) or "").strip().lower() in _OFF:
        return False
    from .bindings import _test_http_enabled

    return not _test_http_enabled()


def _robots_home(home_root: Path) -> None:
    """Keep the process's robots guard on disk beside this home's board cache (where the sources update keeps it)."""

    from .robots_guard import shared_guard

    shared_guard(Path(home_root) / "cache" / "scout" / "ats-boards")


def liveness_client() -> "httpx.Client":
    """The client of one check: a few seconds, no proxy, and NO redirect is followed (a redirect is ``unknown``).

    It carries the board clients' ``User-Agent`` (``board_headers``: one for every request to a board), and a
    request to a board's host asks that host's ``robots.txt`` first (``robots_guard.install``, the process's
    guard): a host that disallows the address is never asked, and the answer is ``unknown``.
    """

    import httpx

    from .board_headers import board_headers
    from .robots_guard import install, shared_guard

    client = httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS), follow_redirects=False, trust_env=False, headers=board_headers()
    )
    return install(client, shared_guard(None))


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _job_key(url: str) -> str | None:
    try:
        return normalize_url(url)
    except FindJobsContractError:
        return None


def _cache_root(home_root: Path) -> Path:
    return Path(home_root) / "cache" / "scout" / "liveness"


def _answer_path(home_root: Path, job: str) -> Path:
    name = digest_imported_bytes(job.encode("utf-8")).removeprefix("sha256:")[:40]
    return _cache_root(home_root) / "jobs" / f"{name}.json"


def _age_seconds(checked_at: str, moment: datetime) -> float | None:
    try:
        then = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        return None
    return (moment - then).total_seconds()


def _kept(home_root: Path, job: str, moment: datetime) -> Liveness | None:
    """The answer kept for ``job`` while it is fresh: the process's, else the file's. No request."""

    key = (str(home_root), job)
    with _LOCK:
        held = _ANSWERS.get(key)
    if held is not None:
        state, checked_at, at = held
        if time.monotonic() - at < (UNKNOWN_SECONDS if state == UNKNOWN else CACHE_SECONDS):
            return Liveness(state, checked_at)
    try:
        stored = json.loads(_answer_path(home_root, job).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("schema_version") != CACHE_SCHEMA or stored.get("job") != job:
        return None
    state, checked_at = stored.get("state"), stored.get("checked_at")
    if state not in (OPEN, CLOSED) or not isinstance(checked_at, str):
        return None
    age = _age_seconds(checked_at, moment)
    if age is None or not 0 <= age < CACHE_SECONDS:
        return None
    with _LOCK:
        _ANSWERS[key] = (state, checked_at, time.monotonic() - age)
    return Liveness(state, checked_at)


def _keep(home_root: Path, job: str, state: str, checked_at: str) -> None:
    with _LOCK:
        _ANSWERS[(str(home_root), job)] = (state, checked_at, time.monotonic())
    if state == UNKNOWN:
        return
    path = _answer_path(home_root, job)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(
            json.dumps({"schema_version": CACHE_SCHEMA, "job": job, "state": state, "checked_at": checked_at}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        return  # a cache that cannot be written is one more request next time, never a failed action


def _paced(provider: str) -> None:
    """Wait until this provider may be asked again: the acquire path's polite interval, and never under the provider's own floor."""

    from .market_acquisition import AcquireLimits, provider_interval

    gap = provider_interval(provider, AcquireLimits.from_environment().min_request_interval_seconds)
    with _PACE:
        wait = _NEXT_REQUEST.get(provider, 0.0) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _NEXT_REQUEST[provider] = time.monotonic() + gap


def _greenhouse_job_id(url: str) -> str | None:
    from .job_input import _gh_jid, _greenhouse_path_job_id

    jid = _gh_jid(url)
    if jid.isdigit():
        return jid
    try:
        return _greenhouse_path_job_id(url)
    except Exception:  # noqa: BLE001 - a URL httpx cannot read names no job; the answer is ``unknown``
        return None


def _where(url: str, provider: str | None, token: str | None, posting_id: str | None) -> tuple[str, str, str | None] | None:
    """``(provider, board token, posting id)`` to ask about, or ``None`` when no board is known for the URL.

    The token (and a Greenhouse id) becomes a path segment of the board's endpoint: one that is not a plain name
    (``outbound_guard.safe_path_segment``) names no board, so it can never add a segment or a query to that address.
    """

    if not provider or not token:
        parsed = parse_board_url(url)
        if parsed is None:
            return None
        provider, token = parsed
    if not safe_path_segment(token):
        return None
    if provider == "greenhouse":
        posting_id = posting_id or _greenhouse_job_id(url)
        return (provider, token, posting_id) if safe_path_segment(posting_id) else None
    if provider in _LIST_PROVIDERS:
        return provider, token, posting_id
    return None


def _ask_greenhouse(client: "httpx.Client", token: str, posting_id: str, url: str) -> str:
    from . import job_input

    try:
        job_input.fetch_missing_description(client, provider="greenhouse", token=token, posting_id=posting_id, url=url)
    except job_input.PostingTextUnavailable as exc:
        if exc.reason == job_input.REASON_POSTING_REMOVED:
            return CLOSED  # the single-job endpoint answered 404 or 410
        if exc.reason == job_input.REASON_NO_TEXT:
            return OPEN  # a job object with no description is still a listed job
        return UNKNOWN
    except (FindJobsContractError, ValueError):
        return UNKNOWN
    return OPEN


def _read_board(client: "httpx.Client", home_root: Path, provider: str, token: str) -> _Board | None:
    """The board's list as URLs and ids, or ``None`` when no successful list answered."""

    from .ats_board_clients import ATSBoardClientError, BoardCache, BoardFetchStats, _cached_request, _decode_json

    found = provider_spec(provider)
    if found is None:
        return None
    endpoint = found.list_url.format(token=token)
    cache = BoardCache(_cache_root(home_root) / "boards", validator_source=lambda _provider, _url: None)
    try:
        body, _status = _cached_request(client, endpoint, provider, token, cache=cache, stats=BoardFetchStats())
        payload = _decode_json(body, provider, token)
    except (ATSBoardClientError, OSError):
        return None
    jobs = payload if found.jobs_key is None else (payload.get(found.jobs_key) if type(payload) is dict else None)
    if type(jobs) is not list:
        return None
    url_key, id_key = found.url_key, found.id_key
    urls: set[str] = set()
    ids: set[str] = set()
    for job in jobs:
        if type(job) is not dict:
            return None  # not the list this provider sends: nothing is concluded from it
        if isinstance(job.get(id_key), (str, int)) and str(job[id_key]):
            ids.add(str(job[id_key]).lower())
        listed = job.get(url_key)
        if type(listed) is str and listed:
            try:
                urls.add(normalize_url(listed))
            except FindJobsContractError:
                continue
    return _Board(frozenset(urls), frozenset(ids))


def _board(client: "httpx.Client", home_root: Path, provider: str, token: str) -> tuple[_Board | None, bool]:
    """``(list, requested)``: the process's list of this board while it is fresh, else one request for it."""

    key = (str(home_root), provider, token)
    with _LOCK:
        held = _BOARDS.get(key)
    if held is not None and time.monotonic() - held[1] < CACHE_SECONDS:
        return held[0], False
    _paced(provider)
    found = _read_board(client, home_root, provider, token)
    if found is not None:
        with _LOCK:
            _BOARDS[key] = (found, time.monotonic())
    return found, True


def _listed(board: _Board, job: str, url: str, posting_id: str | None) -> bool:
    if job in board.urls:
        return True
    if posting_id and posting_id.lower() in board.ids:
        return True
    try:
        segments = {part.lower() for part in urlsplit(url).path.split("/") if part}
    except ValueError:
        return True  # a URL that cannot be read is never concluded to be gone
    return bool(segments & board.ids)


def check_posting_live(
    home_root: Path,
    *,
    url: str,
    provider: str | None = None,
    token: str | None = None,
    posting_id: str | None = None,
    client: "httpx.Client | None" = None,
    now: datetime | None = None,
) -> Liveness:
    """``open`` | ``closed`` | ``unknown`` for the posting at ``url``; at most one request, none within the hour.

    ``provider`` / ``token`` / ``posting_id`` are the index's (a pipeline
    row); without them they are read from the URL (a job assessed by its
    link), and a URL on no known board is ``unknown`` with no request.
    ``client`` is the caller's (a batch shares one); else one is opened and
    closed here. Never raises.
    """

    home_root = Path(home_root)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    job = _job_key(url) if isinstance(url, str) else None
    if job is None:
        return Liveness(UNKNOWN)
    kept = _kept(home_root, job, moment)
    if kept is not None:
        return kept
    if not enabled():
        return Liveness(UNKNOWN)
    where = _where(url, provider, token, posting_id)
    if where is None:
        return Liveness(UNKNOWN)
    provider, token, posting_id = where
    own = client is None
    requested = True
    try:
        if own:
            _robots_home(home_root)
            client = liveness_client()
        if provider == "greenhouse":
            _paced(provider)
            state = _ask_greenhouse(client, token, posting_id or "", url)  # type: ignore[arg-type]
        else:
            board, requested = _board(client, home_root, provider, token)  # type: ignore[arg-type]
            state = UNKNOWN if board is None else (OPEN if _listed(board, job, url, posting_id) else CLOSED)
    except Exception:  # noqa: BLE001 - the check never fails the action it precedes; what went wrong is ``unknown``
        state = UNKNOWN
    finally:
        if own and client is not None:
            client.close()
    checked_at = _stamp(moment)
    _keep(home_root, job, state, checked_at)
    return Liveness(state, checked_at, requested)


# --- the company's own page: the board's address of the posting, and one request to the stored URL ----------


def _host(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host:
        return None
    return host.lower().rstrip(".")


def is_company_site(url: str) -> bool:
    """True when ``url`` is a web address that is NOT on a board's own host (the company's own careers page)."""

    host = _host(url) if isinstance(url, str) else None
    if host is None:
        return False
    return not any(host == domain or host.endswith("." + domain) for domain in _BOARD_DOMAINS)


def board_job_url(provider: str | None, token: str | None, posting_id: str | None) -> str | None:
    """The posting's address on its board, from the INDEX's board token and posting id; ``None`` when it cannot be built."""

    pattern = _BOARD_JOB_URL.get(provider or "")
    if pattern is None or not token or not posting_id:
        return None
    posting_id = str(posting_id)
    if not all(char.isascii() and (char.isalnum() or char in "-_") for char in posting_id):
        return None
    if provider == "greenhouse" and not posting_id.isdigit():
        return None
    return pattern.format(token=quote(token, safe=""), id=posting_id)


def _askable_page(url: str) -> bool:
    """Whether the stored URL may be asked at all: plain ``https`` at a public-looking name (no address literal, port or login).

    No lookup is made here; what the name RESOLVES to is ``outbound_guard.safe_public_target``'s to refuse.
    """

    return public_looking_host(url) is not None


def _page_path(home_root: Path, job: str) -> Path:
    name = digest_imported_bytes(job.encode("utf-8")).removeprefix("sha256:")[:40]
    return _cache_root(home_root) / "pages" / f"{name}.json"


def _kept_page(home_root: Path, job: str, moment: datetime) -> str | None:
    """The company page's answer kept for ``job`` while it is fresh: the process's, else the file's. No request."""

    key = (str(home_root), job)
    with _LOCK:
        held = _PAGES.get(key)
    if held is not None and time.monotonic() - held[1] < CACHE_SECONDS:
        return held[0]
    try:
        stored = json.loads(_page_path(home_root, job).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("schema_version") != PAGE_CACHE_SCHEMA or stored.get("job") != job:
        return None
    state, checked_at = stored.get("company_page"), stored.get("checked_at")
    if state not in PAGE_STATES or not isinstance(checked_at, str):
        return None
    age = _age_seconds(checked_at, moment)
    if age is None or not 0 <= age < CACHE_SECONDS:
        return None
    with _LOCK:
        _PAGES[key] = (state, time.monotonic() - age)
    return state


def _keep_page(home_root: Path, job: str, state: str, checked_at: str) -> None:
    """Every answer is kept the hour, an ``unknown`` too: the company's site is asked once an hour per job, whatever it said."""

    with _LOCK:
        _PAGES[(str(home_root), job)] = (state, time.monotonic())
    path = _page_path(home_root, job)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(
            json.dumps({"schema_version": PAGE_CACHE_SCHEMA, "job": job, "company_page": state, "checked_at": checked_at}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        return  # as the liveness answer: one more request next time, never a failed read


def check_company_page(
    home_root: Path, *, url: str, client: "httpx.Client | None" = None, request: bool = True, now: datetime | None = None
) -> str:
    """``down`` | ``ok`` | ``unknown`` for the stored URL of ONE posting the board says is open; one ``GET`` at most, none within the hour.

    ``404`` / ``410`` is ``down``; a ``2xx`` is ``ok``; anything else (a
    ``5xx``, a ``429``, a redirect, which is not followed, a timeout, a
    network error) is ``unknown``. The body is never read. Only a company-site
    URL is asked (:func:`is_company_site`), only over ``https`` at a public
    name, never when the check is off. Never raises, never writes a posting.

    S2: the name is resolved once and the request connects to that vetted
    address (``outbound_guard``). A name that gives no address, or any
    address off the public internet, is not requested: ``unknown``, held in
    this process for the hour (no lookup per page open) and written nowhere.
    """

    home_root = Path(home_root)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    job = _job_key(url) if isinstance(url, str) else None
    if job is None or not is_company_site(url):
        return UNKNOWN
    kept = _kept_page(home_root, job, moment)
    if kept is not None:
        return kept
    if not request or not enabled() or not _askable_page(url):
        return UNKNOWN
    page = safe_public_target(url)
    if page is None:
        with _LOCK:
            _PAGES[(str(home_root), job)] = (UNKNOWN, time.monotonic())
        return UNKNOWN
    own = client is None
    state = UNKNOWN
    try:
        if own:
            _robots_home(home_root)
            client = liveness_client()
        _paced("page:" + page.host)
        with pinned_stream(client, page) as response:  # type: ignore[arg-type]
            status = response.status_code
        if status in (404, 410):
            state = PAGE_DOWN
        elif 200 <= status < 300:
            state = PAGE_OK
    except Exception:  # noqa: BLE001 - a company site that does not answer says nothing about the job; ``unknown``
        state = UNKNOWN
    finally:
        if own and client is not None:
            client.close()
    _keep_page(home_root, job, state, _stamp(moment))
    return state


@dataclass(frozen=True)
class LiveTarget:
    """One posting of a batch: its job identity and URL, and the index's board and id when it has them."""

    job: str
    url: str
    provider: str | None = None
    token: str | None = None
    posting_id: str | None = None


def check_many(home_root: Path, targets: Iterable[LiveTarget], *, now: datetime | None = None) -> dict[str, Liveness]:
    """``job -> Liveness`` for the postings a batch is about to act on, one shared client, one after another.

    A provider that did not answer :data:`BATCH_GIVE_UP_AFTER` times in a row
    is not asked for the rest of the batch: those are ``unknown`` (and
    proceed), so a board that is down costs a few seconds, never the batch.
    """

    found: dict[str, Liveness] = {}
    misses: dict[str, int] = {}
    client: "httpx.Client | None" = None
    try:
        for target in targets:
            where = _where(target.url, target.provider, target.token, target.posting_id)
            name = where[0] if where is not None else ""
            if misses.get(name, 0) >= BATCH_GIVE_UP_AFTER:
                job = _job_key(target.url)
                kept = _kept(Path(home_root), job, (now or datetime.now(UTC)).astimezone(UTC)) if job else None
                found[target.job] = kept or Liveness(UNKNOWN)
                continue
            if client is None and enabled():
                _robots_home(home_root)
                client = liveness_client()
            answer = check_posting_live(
                home_root, url=target.url, provider=target.provider, token=target.token, posting_id=target.posting_id,
                client=client, now=now,
            )
            found[target.job] = answer
            if answer.requested:
                misses[name] = misses.get(name, 0) + 1 if answer.state == UNKNOWN else 0
    finally:
        if client is not None:
            client.close()
    return found


def closed_skipped_text(batch: Mapping[str, object]) -> str | None:
    """What a batch's plain result says of the postings it left out as closed (``closed_skipped``); ``None`` when it left none."""

    skipped = batch.get("closed_skipped")
    if type(skipped) is not int or skipped < 1:
        return None
    one = skipped == 1
    return (
        f"Skipped {skipped} closed posting{'' if one else 's'}: {'its board no longer lists it' if one else 'their boards no longer list them'}. "
        f"No model call was made for {'it' if one else 'them'}; {'it is' if one else 'they are'} under Removed now."
    )


# --- a job of this project: the stored row decides first, a closed answer is written to it ------------------


@dataclass(frozen=True)
class JobLiveness:
    """What a read or an action says about one job: the state, when the board was asked, since when it is closed."""

    state: str
    checked_at: str | None = None
    closed_at: str | None = None
    requested: bool = False
    #: 7b: ``down`` | ``ok`` | ``unknown`` for the stored URL when it is the company's own page (asked only for an open
    #: posting whose page is being opened), and the posting's address on its board (``None``: the stored URL is the
    #: board's own, the address cannot be built, the job has no stored row, or it is closed).
    company_page: str = UNKNOWN
    board_url: str | None = None

    @property
    def closed(self) -> bool:
        return self.state == CLOSED

    @property
    def note(self) -> str | None:
        return CLOSED_NOTE if self.closed else None

    @property
    def company_page_note(self) -> str | None:
        return COMPANY_PAGE_DOWN_NOTE if self.state == OPEN and self.company_page == PAGE_DOWN and self.board_url else None

    def to_json(self) -> dict[str, object]:
        return {
            "state": self.state, "checked_at": self.checked_at, "closed_at": self.closed_at, "note": self.note,
            "company_page": self.company_page, "company_page_note": self.company_page_note, "board_url": self.board_url,
        }


def _stored_rows(home_root: Path, target: Path, jobs: Iterable[str]) -> tuple[object | None, dict[str, list[object]]]:
    """``(store, job -> its read-model rows)``; ``(None, {})`` when the project has no posting store. The caller closes the store."""

    import sqlite3

    from ...workpad import WorkpadError
    from .. import postings
    from ..pipeline.store import PipelineStoreError, pipeline_path

    try:
        if not pipeline_path(home_root, target).is_file():
            return None, {}  # nothing is created for a check
        store = postings.open_store(home_root, target)
    except (PipelineStoreError, WorkpadError, sqlite3.Error, OSError, ValueError):
        return None, {}
    found: dict[str, list[object]] = {}
    try:
        for row in store.postings(jobs=set(jobs), live=False):
            found.setdefault(row.job, []).append(row)
    except (PipelineStoreError, sqlite3.Error, OSError, ValueError):
        store.close()
        return None, {}
    return store, found


def _mark_closed(store: object | None, job: str, moment: datetime) -> str:
    """Write ``removed_at`` to the job's stored rows (when it has any); returns the time it is closed since."""

    import sqlite3

    from .. import postings
    from ..pipeline.store import PipelineStoreError

    closed_at = postings.stamp(moment) or _stamp(moment)
    if store is not None:
        try:
            store.mark_posting_removed(job, closed_at)  # type: ignore[attr-defined]
        except (PipelineStoreError, sqlite3.Error, OSError):
            pass  # the answer is still served; the next check writes it
    return closed_at


def jobs_liveness(
    home_root: Path, target: Path, jobs: Iterable[str], *, request: bool = True, company_page: bool = False, now: datetime | None = None
) -> dict[str, JobLiveness]:
    """``job identity -> JobLiveness`` for the jobs about to be acted on (a page open, an Assess, a PDF, "Mark applied").

    A job whose stored row already has ``removed_at`` is ``closed`` with NO
    request. Otherwise the board is asked (:func:`check_many`; the index's
    board and id for a stored row, else the job's own URL). A ``closed``
    answer is written to the job's stored rows (``PipelineStore.mark_posting_removed``),
    so every read that leaves removed postings out leaves it out; a job with
    no stored row (assessed by its link) is only reported. ``request=False``
    answers from what is stored and kept, never asking a board. Pasted text
    (``text:`` identities) has no posting to ask about: ``unknown``.

    7b: a stored, not-removed row whose URL is the company's own page carries
    ``board_url`` (built from the index's board token and posting id; no
    request). ``company_page=True`` (the job read of ONE page; never a batch)
    also asks that URL once when the board answered ``open``
    (:func:`check_company_page`); whatever it answers, nothing is written to
    the posting.
    """

    from .. import postings

    home_root, target = Path(home_root), Path(target)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    wanted = list(dict.fromkeys(jobs))
    answers: dict[str, JobLiveness] = {job: JobLiveness(UNKNOWN) for job in wanted}
    urls = [job for job in wanted if job.startswith(("http://", "https://"))]
    if not urls:
        return answers
    store, rows = _stored_rows(home_root, target, urls)
    try:
        targets: list[LiveTarget] = []
        ask: list[object] = []
        on_board: dict[str, str] = {}  # job -> its address on the board (a stored row whose URL is a company site)
        for job in urls:
            held = rows.get(job, [])
            removed = next((row.removed_at for row in held if row.removed_at), None)  # type: ignore[attr-defined]
            if removed:
                answers[job] = JobLiveness(CLOSED, closed_at=removed)
            elif held:
                ask.append(held[0])
            else:
                targets.append(LiveTarget(job=job, url=job))
        if ask:
            try:
                texts = postings.posting_texts(home_root, ask)  # type: ignore[arg-type]
            except (OSError, ValueError, LookupError):
                texts = {}
            for row in ask:
                text = texts.get(row.job)  # type: ignore[attr-defined]
                provider, token = postings.split_board(row.board)  # type: ignore[attr-defined]
                item = LiveTarget(
                    job=row.job, url=(getattr(text, "url", None) or row.job), provider=provider, token=token,  # type: ignore[attr-defined]
                    posting_id=getattr(text, "posting_id", None),
                )
                targets.append(item)
                if is_company_site(item.url):
                    where = _where(item.url, provider, token, item.posting_id)  # a Greenhouse id is also read from ``gh_jid``
                    board_url = board_job_url(*where) if where is not None else None
                    if board_url:
                        on_board[item.job] = board_url
        if request:
            found = check_many(home_root, targets, now=moment)
        else:
            found = {}
            for item in targets:
                key = _job_key(item.url)
                found[item.job] = (_kept(home_root, key, moment) if key else None) or Liveness(UNKNOWN)
        for item in targets:
            answer = found.get(item.job) or Liveness(UNKNOWN)
            closed_at = _mark_closed(store if item.job in rows else None, item.job, moment) if answer.closed else None
            board_url = None if answer.closed else on_board.get(item.job)
            page = UNKNOWN
            if board_url and answer.state == OPEN:
                # The board lists the job: is the company's own page there? ``company_page`` asks it (one page, never a
                # batch); otherwise only what is kept is said.
                page = check_company_page(home_root, url=item.url, request=request and company_page, now=moment)
            answers[item.job] = JobLiveness(answer.state, answer.checked_at, closed_at, answer.requested, page, board_url)
    finally:
        if store is not None:
            store.close()  # type: ignore[attr-defined]
    return answers


def job_liveness(
    home_root: Path, target: Path, job: str, *, request: bool = True, company_page: bool = False, now: datetime | None = None
) -> JobLiveness:
    """:func:`jobs_liveness` for one job; ``company_page=True`` when its page is being opened (7b)."""

    return jobs_liveness(home_root, target, [job], request=request, company_page=company_page, now=now)[job]


def closed_before_assess(home_root: Path, target: Path, job_url: str | None) -> JobLiveness | None:
    """Before an Assess of ONE job by its link: the job's liveness when it is closed (the caller refuses with
    :data:`ERROR_POSTING_CLOSED`, no model call), else ``None``. Pasted text is never asked about."""

    from .job_state import normalize_job_identity

    if not job_url:
        return None
    try:
        live = job_liveness(home_root, target, normalize_job_identity(job_url))
    except FindJobsContractError:
        return None  # not a URL the check reads: the assessment itself says what is wrong with it
    return live if live.closed else None


__all__ = [
    "JobLiveness",
    "job_liveness",
    "jobs_liveness",
    "BATCH_GIVE_UP_AFTER",
    "CACHE_SECONDS",
    "CLOSED",
    "CLOSED_ASSESS_MESSAGE",
    "CLOSED_NOTE",
    "COMPANY_PAGE_DOWN_NOTE",
    "PAGE_DOWN",
    "PAGE_OK",
    "PAGE_STATES",
    "board_job_url",
    "check_company_page",
    "is_company_site",
    "ERROR_POSTING_CLOSED",
    "LIVENESS_ENV",
    "LiveTarget",
    "Liveness",
    "OPEN",
    "STATES",
    "UNKNOWN",
    "check_many",
    "check_posting_live",
    "closed_before_assess",
    "closed_skipped_text",
    "enabled",
    "liveness_client",
    "reset_memory",
]
