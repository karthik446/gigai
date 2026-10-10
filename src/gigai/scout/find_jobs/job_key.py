"""0.1.11.9 RB2: THE identity a job's records are kept under when the job is posted more than once.

The same job posted once per country is ONE job (``job_copies.copy_key``: the board, the company, the title and
the description; ``canonical_job.pick_canonical`` names its canonical posting). A job has one assessment, one
resume and one suggestion record (``job_store_layout``), so every copy must end at ONE identity before a record
is read or written. :func:`job_key` is that identity, and the only place the rule is written:

1. the copies of ``job_identity`` are the postings of its board that share its copy key, in the canonical
   order (:func:`copies_of`: the US posting, else the earliest posted, then the posting id). The company index
   says so: one company file, kept while its stamp does not move. Unlike a list, which collapses only the
   copies its filters select, this is every copy the board holds: the key does not depend on a filter, a role
   or the order of reads;
2. a job nothing is stored for is keyed by its CANONICAL posting (:func:`canonical_identity`);
3. a job that already has records under one of its copies (assessed before this rule, or by an older GigAI)
   stays keyed by that copy, so what is stored is never left behind: the first copy in the canonical order with
   a stored resume, else the first with a stored assessment. A file's name is looked for; no record is opened.

A posting with no stored description, one no board holds, a pasted posting (``text:...``) and a posting with no
copy are their own key. Reads only: the company index, the search index when a URL does not name its board, and
the names of the stores' files. Nothing is written, no model, no request.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time

from .canonical_job import canonical_order
from .job_copies import PLACE_US, copy_key, place_of

@dataclass(frozen=True, slots=True)
class BoardPosting:
    """What the company index says of one posting, for US only and the copies (``posting_search`` reads the same)."""

    title: str
    company: str
    location: str
    #: ``job_copies.place_of``: ``us``, ``unclear`` or ``other``.
    place: str
    #: The digest of the title and the description (``None``: no description stored, never merged).
    content: str | None
    posting_id: str


#: ``(home, board) -> (the company file's stamp, {identity: its copies, the canonical one first}, {identity: its
#: facts})``. Only postings that HAVE a copy are in a board's first map; every posting is in the second. ONE read of a
#: company file serves both, and the lists' own read of it (``board_postings``).
_BOARDS: dict[tuple[str, str], tuple[object, dict[str, tuple[str, ...]], dict[str, BoardPosting]]] = {}
#: ``(home, identity) -> its board``, for a URL that does not name its board (the search index said so once).
_BOARD_OF: dict[tuple[str, str], str] = {}
#: ``(home, identity) -> when the search index held no posting with this address``: it is not asked again for a
#: minute (the lookup is one scan of the index's URLs, for every address asked about at once).
_NOT_FOUND: dict[tuple[str, str], float] = {}
_NOT_FOUND_SECONDS = 60.0
_LOCK = threading.Lock()
_KEPT_BOARDS = 16384
_KEPT_IDENTITIES = 16384

_NO_COPIES: Mapping[str, tuple[str, ...]] = {}
_NO_POSTINGS: Mapping[str, BoardPosting] = {}


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return found.st_mtime_ns, found.st_size


def _split(board: str) -> tuple[str, str]:
    from urllib.parse import unquote

    ats, _, encoded = board.partition(":")
    return ats, unquote(encoded)


def _grouped(entry: object) -> tuple[dict[str, tuple[str, ...]], dict[str, BoardPosting]]:
    """``({identity: (the canonical copy, then the others)}, {identity: its facts})``: the first for every posting of
    ``entry`` that has a copy, the second for every posting."""

    from .contracts import FindJobsContractError, normalize_url
    from .search_index import _stamp

    company = entry.company if type(entry.company) is str else ""  # type: ignore[attr-defined]
    groups: dict[object, list[tuple[str, bool, str, str]]] = {}
    facts: dict[str, BoardPosting] = {}
    for posting_id, posting in entry.postings.items():  # type: ignore[attr-defined]
        try:
            identity = normalize_url(posting.url)
        except FindJobsContractError:
            continue
        place = place_of(posting.location, posting.countries)
        content = posting.content_sha256 or None
        facts[identity] = BoardPosting(posting.title, company, posting.location, place, content, posting_id)
        key = copy_key(entry.key, company, posting.title, content, bool(posting.removed))  # type: ignore[attr-defined]
        if key is None:
            continue  # no stored description: never a copy of another posting
        posted = _stamp(posting.published_at) or _stamp(posting.first_seen) or ""
        groups.setdefault(key, []).append((identity, place == PLACE_US, posted, str(posting_id)))
    found: dict[str, tuple[str, ...]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = canonical_order(members, us=lambda item: item[1], posted=lambda item: item[2], posting_id=lambda item: item[3])
        copies = tuple(dict.fromkeys(item[0] for item in ordered))
        if len(copies) > 1:
            for identity in copies:
                found[identity] = copies
    return found, facts


def _board(home_root: Path, board: str) -> tuple[Mapping[str, tuple[str, ...]], Mapping[str, BoardPosting]]:
    """One company file, read once and kept while its stamp (mtime, size) does not move. A board the index cannot
    name or read has nothing."""

    from .company_index import CompanyIndex

    index = CompanyIndex.for_home(Path(home_root))
    try:
        ats, slug = _split(board)
        stat = _stat(index.path(ats, slug))
    except ValueError:
        return _NO_COPIES, _NO_POSTINGS
    if stat is None:
        return _NO_COPIES, _NO_POSTINGS
    key = (os.fspath(home_root), board)
    with _LOCK:
        kept = _BOARDS.get(key)
    if kept is None or kept[0] != stat:
        try:
            entry = index.read(ats, slug)
        except ValueError:
            return _NO_COPIES, _NO_POSTINGS
        kept = (stat, *(({}, {}) if entry is None else _grouped(entry)))
        with _LOCK:
            if len(_BOARDS) >= _KEPT_BOARDS and key not in _BOARDS:
                del _BOARDS[next(iter(_BOARDS))]
            _BOARDS[key] = kept
    return kept[1], kept[2]


def board_copies(home_root: Path, board: str) -> Mapping[str, tuple[str, ...]]:
    """``{identity: its copies, the canonical one first}`` for the postings of ``board`` that have a copy."""

    return _board(home_root, board)[0]


def board_postings(home_root: Path, board: str) -> Mapping[str, BoardPosting]:
    """``{identity: what the company index says of it}`` for every posting of ``board`` (the same read as the copies)."""

    return _board(home_root, board)[1]


def _boards_of(home_root: Path, identities: Iterable[str]) -> dict[str, str]:
    """``{identity: the board whose company file holds it}`` for the ``identities`` a board holds. The URL itself
    names it for most systems (no read); the search index is asked about ALL the others at once (one scan of its
    URLs, narrowed by each address's host and path), once per identity. An index that cannot answer names none:
    the company files are never scanned for this."""

    import sqlite3
    from urllib.parse import urlsplit

    from . import search_index
    from .company_index import CompanyIndex
    from .contracts import FindJobsContractError, normalize_url, parse_board_url

    home = os.fspath(home_root)
    found: dict[str, str] = {}
    asked: dict[str, tuple[str, str]] = {}
    index = None
    for job_identity in dict.fromkeys(identities):
        if type(job_identity) is not str or not job_identity or job_identity.startswith("text:"):
            continue
        with _LOCK:
            kept = _BOARD_OF.get((home, job_identity))
            missed = _NOT_FOUND.get((home, job_identity))
        if kept is not None:
            found[job_identity] = kept
            continue
        if missed is not None and time.monotonic() - missed < _NOT_FOUND_SECONDS:
            continue
        where = parse_board_url(job_identity)
        if where is not None:
            index = index or CompanyIndex.for_home(Path(home_root))
            for token in dict.fromkeys((where[1], where[1].lower())):
                try:
                    if _stat(index.path(where[0], token)) is not None:
                        found[job_identity] = _key(where[0], token)
                        break
                except ValueError:
                    continue
            if job_identity in found:
                continue
        try:
            parts = urlsplit(job_identity)
            host = parts.hostname or ""
        except ValueError:
            continue
        bare = parts.path in ("", "/")
        # The stored URL holds the address's path as written and its host in some case (``normalize_url`` folds no
        # more than that). SQLite folds ASCII only: another host is found by its path alone.
        asked[job_identity] = (host if bare or host.isascii() else "", "" if bare else parts.path)
    if not asked:
        return found
    try:
        held = search_index.boards_with_url_parts(Path(home_root), asked.values())
    except (sqlite3.Error, OSError, ValueError):
        return found
    if not held.available:
        return found
    answered: dict[str, str] = {}
    for url, board in held.boards:
        try:
            identity = normalize_url(url)
        except FindJobsContractError:
            continue
        if identity in asked:
            answered.setdefault(identity, board)  # the first one: a live posting before a removed one, then the newest
    now = time.monotonic()
    with _LOCK:
        for job_identity in asked:
            board = answered.get(job_identity)
            if board is None:
                if len(_NOT_FOUND) >= _KEPT_IDENTITIES:
                    _NOT_FOUND.clear()
                _NOT_FOUND[(home, job_identity)] = now
                continue
            if len(_BOARD_OF) >= _KEPT_IDENTITIES:
                del _BOARD_OF[next(iter(_BOARD_OF))]
            _BOARD_OF[(home, job_identity)] = board
            found[job_identity] = board
    return found


def _board_of(home_root: Path, job_identity: str) -> str | None:
    """The board whose company file holds ``job_identity``, or ``None`` (:func:`_boards_of` of one address)."""

    return _boards_of(home_root, (job_identity,)).get(job_identity)


def board_of(home_root: Path, job_identity: str) -> str | None:
    """The board (``<ats>:<slug>``) whose company file holds ``job_identity``, or ``None``: no board, a pasted
    posting, an index that cannot be read. As :func:`copies_of` finds it; it never raises."""

    if type(job_identity) is not str or not job_identity or job_identity.startswith("text:"):
        return None
    try:
        return _board_of(Path(home_root), job_identity)
    except Exception:  # noqa: BLE001 - as ``copies_of``: an index that cannot be read names no board
        return None


def find_boards(home_root: Path, identities: Iterable[str] = (), *, known: Mapping[str, str] | None = None) -> None:
    """Names the boards of many jobs ahead of their :func:`copies_of` / :func:`job_key`, for a caller that has many.

    0.1.11.9 PERF1. A URL that does not name its board costs one scan of the search index's URLs, and a list has
    several such jobs (an employer's own careers address), each looked up again by every read of a stored record.
    ``known`` is what the caller's own rows say (``{identity: its row's board}``): kept when the URL names no board
    and that board's company file does hold the posting, so nothing is scanned for it. The search index is asked
    about all the others at once. It changes no answer and never raises.
    """

    from .contracts import parse_board_url

    try:
        home = os.fspath(home_root)
        asked = list(identities)
        for job_identity, board in (known or {}).items():
            with _LOCK:
                kept = (home, job_identity) in _BOARD_OF
            if kept:
                continue
            if type(board) is str and board and parse_board_url(job_identity) is None and job_identity in board_postings(Path(home_root), board):
                with _LOCK:
                    if len(_BOARD_OF) >= _KEPT_IDENTITIES:
                        del _BOARD_OF[next(iter(_BOARD_OF))]
                    _BOARD_OF[(home, job_identity)] = board
            else:
                asked.append(job_identity)
        _boards_of(Path(home_root), asked)
    except Exception:  # noqa: BLE001 - as ``copies_of``: an index that cannot be read says nothing
        return


def _key(ats: str, slug: str) -> str:
    from urllib.parse import quote

    return f"{ats}:{quote(slug, safe='')}"


def copies_of(home_root: Path, job_identity: str, *, board: str | None = None) -> tuple[str, ...]:
    """Every copy of ``job_identity`` its board holds, the canonical one first; ``(job_identity,)`` when it has none.

    ``board`` (``<ats>:<slug>``, a read-model row's own) saves finding it.
    """

    if type(job_identity) is not str or not job_identity or job_identity.startswith("text:"):
        return (job_identity,)
    try:
        where = board or _board_of(Path(home_root), job_identity)
        if where is None:
            return (job_identity,)
        return board_copies(Path(home_root), where).get(job_identity) or (job_identity,)
    except Exception:  # noqa: BLE001 - an index that cannot be read says nothing of copies: the job is its own
        return (job_identity,)


def canonical_identity(home_root: Path, job_identity: str, *, board: str | None = None) -> str:
    """The canonical posting of ``job_identity``'s job (``canonical_job.pick_canonical`` over every copy its board holds)."""

    return copies_of(home_root, job_identity, board=board)[0]


def _holders(store: Path, copies: tuple[str, ...]) -> list[str]:
    """The ``copies`` the store at ``store`` holds a record for (the per-job folder or a role's; never a pasted resume's)."""

    from ..job_store_layout import EPHEMERAL_FOLDER, RECORD_SUFFIX, job_digest

    try:
        folders = [folder for folder in store.iterdir() if folder.name != EPHEMERAL_FOLDER and folder.is_dir()]
    except OSError:
        return []
    return [copy for copy in copies if any((folder / f"{job_digest(copy)}{RECORD_SUFFIX}").is_file() for folder in folders)]


def kept_copy(project_dir: Path, copies: tuple[str, ...]) -> str:
    """Which of a job's ``copies`` (the canonical one first) its records are kept under: rule 3 of the module docstring."""

    from ..job_store_layout import ASSESSMENTS, RESUMES, SUGGESTIONS

    if len(copies) < 2:
        return copies[0]
    for store in (RESUMES, ASSESSMENTS, SUGGESTIONS):
        held = _holders(Path(project_dir) / store, copies)
        if held:
            return held[0]
    return copies[0]


def job_key(home_root: Path, target: Path, job_identity: str, *, board: str | None = None) -> str:
    """The identity ``job_identity``'s records are read and written under. See the module docstring.

    A job with no copy costs one ``stat`` of its company file (and nothing at all when its board cannot be named).
    """

    copies = copies_of(home_root, job_identity, board=board)
    if len(copies) < 2:
        return job_identity
    from .discovery.storage import project_id

    try:
        project_dir = Path(home_root) / "scout" / project_id(Path(home_root), Path(target))
    except Exception:  # noqa: BLE001 - no Scout project: nothing is stored, the canonical posting is the key
        return copies[0]
    return kept_copy(project_dir, copies)


__all__ = ["BoardPosting", "board_copies", "board_of", "board_postings", "canonical_identity", "copies_of", "find_boards", "job_key", "kept_copy"]
