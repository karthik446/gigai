"""0.1.10.7 PL6 (DESIGN 10.1, 10.7): the per-(posting, profile) read model.

One row per ``(job identity, profile)`` for every posting of the stored
company index that an ACTIVE profile matches (a deleted or archived profile
has no rows). It is what ``gigai scout new`` reads, and what the Jobs page by
posting will read.

WHERE IT LIVES: the ``posting`` table of the project's ``pipeline.sqlite``
(DESIGN 10.1's cache), not a derive on every request. A request that derived
it would read every company's index file and board body again; the cache
turns a request into a few ``stat`` calls and one SQL read (the timing gate
in ``tests/behaviors/scout_pipeline/test_posting_read_model.py`` measures
both). The table is a cache and never authority: deleting it loses nothing,
the next read builds it again.

NO TEXT in the table: ids, digests, numbers and codes (``COLUMN_KINDS``). A
posting's title, company, location and text are read back from the company
index and the board cache for the rows a response serves
(:func:`posting_rows`).

WHAT A ROW CARRIES: ``first_seen`` / ``published_at`` / ``removed_at`` from the
index; the profile's rank score from the home's rank score cache (never a
model call here: a posting nothing ranked yet has none); the job state
(``job_state``: not assessed / needs answers / matched / not a match /
tailored), why a stored assessment is stale, its requirement counts and open
questions (from the quick-assess store, or from what an old find-jobs run
assessed when that is the newer of the two: ``run_history.py`` imports it, the
run's records are not copied); the Scout label and Scout ATS score the pipeline stored; and
``match_rank``, this profile's place among the profiles the posting matches
(1 is the best tag; 0110-8-01, 0110-8-12: the profile that tailored a resume
for it, then a profile with a CURRENT assessment, then one with a stale
assessment, then the highest rank score, then the default profile. A rank
score that arrives later never moves a posting away from the profile that
assessed it). ``state`` is the verdict state; a tailored resume is the
``tailored`` flag beside it, never a state that replaces the verdict.
0110-10-02: ``fit`` is the assessment's fit number, and a needs-answers
posting with a low fit number AND a low rank score has the state ``weak_fit``
(``fit.py``: the rule, its numbers and the setting that tunes them).
Application events are not cached: they are journal records, read when a
response is built.

MATCHING is ``index_search.read_indexed_boards`` with each active profile's
own effective config (its titles, and its own location, work mode, countries
and posted window when it has them): the same rule a search uses.

INVALIDATION, per profile, by two digests kept in ``posting_build``:

- ``match_digest``: the profile's settings digest, the index files' stamps
  (name, mtime, size), the title-tag store's, the watched boards and the UTC
  day (the posted window moves with it). When it differs, the profile's
  boards are looked at again (below: only the ones that differ are matched),
  so a posting whose content changed gets its new ``listing_digest``.
- ``facts_digest``: the profile's resume digest, the rank model, and the
  stamps of the stores a row's facts come from (assessments, tailored
  resumes, Scout labels, the rank score cache, and what a stale check reads).
  When only it differs, the stored rows get their facts again; the index is
  not read.

A read that finds both digests unchanged reads the table as it is.

0110-9-01 (the operator's home: 290,000 postings, 10,350 companies). What a
build costs scales with the COMPANIES and with the postings that do NOT
match, so:

- ONE BUILD AT A TIME, shared by every caller of :func:`refresh` in the
  process (requests, the pipeline's rank lane): a caller that arrives while
  one runs waits for it and then reads what it wrote, it never starts its
  own. ``wait`` (the server's read routes) bounds that wait: past it the
  caller gets the rows as stored (``builds``: ``stale``) while the build
  goes on in its own thread, or, when a profile has no stored rows yet,
  :class:`PostingModelPreparing` with how far the build is
  (:func:`model_status` says the same without reading anything).
- INCREMENTAL, per board: ``posting_board`` keeps what each board's rows of
  a profile were matched from (the profile's settings, the tag store, the
  board's index file stamp). Only a board whose stamp differs is matched
  again, a few boards at a time (one transaction each, so a build that is
  interrupted goes on where it stopped), and no more of the index is held
  in memory than those few boards.
- THE POSTED WINDOW, once a UTC day (0.1.10.11, cause C8): the day is in the
  profile's ``match_digest`` and NOT in a board's stamp. Time only moves a
  posting out of the window, never into it, so the first read of a new day
  looks at the listed rows the window has moved past (a row's stored
  ``published_at``, the date the window judged, is before
  ``now - max_age_days``) and nothing else. When the day is ALL that moved
  (the common case), each such row is put to the search's own rule on its
  board's index entry and dropped when the rule says too old: no board is
  matched and the watched boards are not read from the journal (4 s of a
  fresh process on the operator-sized home). When more than the day moved,
  the boards holding such a row are matched again with the ones whose stamp
  differs. A day that moves no posting past the edge reads nothing. (A clock
  that went BACK a day matches every board, as every new day did before.)
- A title is decided once per build, however many postings carry it; the
  tag store is compared by what it holds (``TagStore.stamp``), never by its
  ``-wal`` file, which comes and goes with every reader's connection.
- The watched boards are read from the journal only when the committed
  watchlist changed (its Git tree id); their digest is kept in
  ``posting_source`` so a fresh process does not read 10,000 records to
  learn that nothing changed.

No subprocess is started per posting: the journal is read a fixed number of
times per refresh (the profiles, the watched boards, each profile's resume),
inside one ``committed_read_cache`` so the workpad is checked once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote, unquote

from ..canonical import digest_imported_bytes
from .pipeline.store import PipelineStore, PostingBuild, PostingRecord, RunAssessment, pipeline_path

#: Bump when what a row is matched by, or what its facts are read from, changes.
# One above every side of the 0.1.10.8 merge, so stored rows rebuild once whichever build wrote them:
# :2 was 0110-8-06 (one digest rule), :3 was 0110-8-05 (a known function tag vetoes a generic title).
# :5 is 0110-9-01: rows are matched and stamped per board (``posting_board``).
# :6 is 0.1.10.11 (C8): a board's stamp no longer holds the UTC day, and a row's ``published_at`` is the date the posted
# window judged (the index's). One build of every board after the upgrade; the stored rows are served meanwhile.
MATCH_VERSION = "posting-match:6"
# :3 is 0110-10-02: a row carries its fit number, and a weak fit has its own state.
FACTS_VERSION = "posting-facts:3"

STATE_ACTIVE = "active"
BUILD_FULL = "matched"
BUILD_FACTS = "facts"
BUILD_UNCHANGED = "unchanged"
#: Served as stored while a build runs in its own thread (``refresh(wait=...)``).
BUILD_STALE = "stale"

STATE_READY = "ready"
STATE_PREPARING = "preparing"
STATE_REFRESHING = "refreshing"
STATE_UNKNOWN = "unknown"
PHASE_IDLE = "idle"
PHASE_MATCHING = "matching"
PHASE_FACTS = "facts"
STATUS_SCHEMA_VERSION = "scout-postings-status:1"

#: How much of the index one step of a build holds in memory: index files of about this many bytes, or this many boards.
CHUNK_BYTES = 6_000_000
CHUNK_BOARDS = 400
#: A build of at most this many boards is small: a caller with a ``wait`` waits for it (up to its ``wait``). A larger one
#: (the first build of a large index) is answered at once from what is stored, or with ``PostingModelPreparing``.
SMALL_BUILD_BOARDS = 500
#: A build of EVERY board the server would start for a change that is not the user's own (the tag store while titles
#: are being tagged) is not started sooner than this after the last one ended; the rows are served as stored
#: meanwhile. A company's update is never held back: only its own board is matched again. (A new UTC day is no longer
#: such a build: it drops the rows that left the posted window and matches no board for it, 0.1.10.11.)
REBUILD_MIN_SECONDS = 60.0

_logger = logging.getLogger("gigai.scout.server")


class PostingModelError(ValueError):
    """The read model cannot be built for this folder; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PostingModelPreparing(PostingModelError):
    """The first build is still running (``refresh(wait=...)`` only): ``progress`` is :func:`model_status`'s answer."""

    def __init__(self, progress: Mapping[str, object]) -> None:
        super().__init__("preparing", "the postings are being prepared; ask again in a moment")
        self.progress = dict(progress)


def _digest(*parts: object) -> str:
    rendered = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return digest_imported_bytes(rendered.encode("utf-8"))


def stamp(value: object) -> str | None:
    """``value`` (an ISO instant) as a fixed-width UTC stamp, so stamps compare as strings; ``None`` when it is not one."""

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return found.st_mtime_ns, found.st_size


def board_key(ats: str, slug: str) -> str:
    """``<ats>:<percent-encoded slug>``: the ``board`` column (the company index file's own key)."""

    return f"{ats}:{quote(slug, safe='')}"


def split_board(board: str) -> tuple[str, str]:
    ats, _, encoded = board.partition(":")
    return ats, unquote(encoded)


# --- one profile's inputs -------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileView:
    """One active profile as the read model sees it."""

    profile_id: str
    label: str
    is_default: bool
    resume_record_id: str
    resume_revision_id: str
    resume_digest: str
    settings_digest: str
    config: object  # the profile's effective FindJobsConfig
    record: object  # the ProfileRecord


@dataclass(frozen=True)
class RefreshResult:
    """What one :func:`refresh` did, per active profile, and what it cost."""

    profiles: tuple[ProfileView, ...]
    builds: Mapping[str, str]  # profile_id -> matched | facts | unchanged
    rows: int
    dropped: int
    #: The resolved gig the profiles were read from, for a caller that reads more of it.
    resolved: object = None

    def to_json(self) -> dict[str, object]:
        return {"profiles": len(self.profiles), "builds": dict(self.builds), "rows": self.rows, "dropped": self.dropped}


def _shared_config(home_root: Path, target: Path):
    from ..canonical import parse_json_bytes
    from .find_jobs.contracts import FindJobsConfig
    from .find_jobs.effective_config import saved_work_mode, with_saved_work_mode

    path = target / "find-jobs.json"
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("no find-jobs.json")
        config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
    except (OSError, ValueError) as exc:
        raise PostingModelError("config_unavailable", "this folder has no readable find-jobs.json; finish the Scout setup first") from exc
    return with_saved_work_mode(config, saved_work_mode(home_root=home_root, target=target))


def active_profiles(home_root: Path, target: Path, resolved: object | None = None) -> tuple[object, tuple[ProfileView, ...]]:
    """``(resolved gig, the active profiles)``: the default profile first, then by label. Deleted and archived ones are left out."""

    from ..workpad import resolve_workpad
    from . import profile_records
    from .find_jobs.effective_config import overlay_selected_profile

    home_root, target = Path(home_root), Path(target)
    if resolved is None:
        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except Exception as exc:  # noqa: BLE001 - any failure to resolve the gig is "no Scout gig here", by its code
            raise PostingModelError("target_unavailable", "no Scout gig is available for this folder; run `gigai scout install`") from exc
    # Migrates the default profile on first read, like every profile-aware path.
    profile_records.selected_profile(resolved, home_root=home_root, target=target)
    records = profile_records.list_profiles(resolved)
    default = profile_records.default_profile(records)
    default_id = None if default is None else default.profile_id
    shared = _shared_config(home_root, target)
    views = []
    for record in records:
        if record.state != STATE_ACTIVE:
            continue
        config = overlay_selected_profile(shared, record)
        views.append(
            ProfileView(
                profile_id=record.profile_id,
                label=record.label,
                is_default=record.profile_id == default_id,
                resume_record_id=record.resume_ref.record_id,
                resume_revision_id=record.resume_ref.revision_id,
                resume_digest=record.resume_ref.content_sha256,
                settings_digest=_digest("settings", config.digest(), list(record.titles), list(record.titles_to_avoid)),
                config=config,
                record=record,
            )
        )
    views.sort(key=lambda view: (not view.is_default, view.label.casefold(), view.profile_id))
    return resolved, tuple(views)


# --- the stamps a build is compared by ------------------------------------------------------


def _index_files(home_root: Path) -> dict[str, tuple[int, int]]:
    """``file name -> (mtime, size)`` of every company index file. No file is opened."""

    root = home_root / "cache" / "scout" / "companies"
    files: dict[str, tuple[int, int]] = {}
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and ":" in entry.name:
                    found = entry.stat()
                    files[entry.name] = (found.st_mtime_ns, found.st_size)
    except OSError:
        pass
    return files


def _tags_stamp(home_root: Path) -> object:
    """What the title-tag store holds (``TagStore.stamp``); ``None`` with no store. Never its ``-wal`` file (0110-9-01)."""

    from .find_jobs.title_query import open_tag_store

    store = open_tag_store(home_root)
    if store is None:
        return None
    try:
        return list(store.stamp())
    except (OSError, sqlite3.Error):
        return ["unreadable", _stat(store.path)]
    finally:
        try:
            store.close()
        except sqlite3.Error:
            pass


def _index_stamp(files: Mapping[str, tuple[int, int]], tags: object) -> str:
    """The company index (names, mtimes, sizes) and the title-tag store, as one digest."""

    return _digest("index", sorted((name, *found) for name, found in files.items()), tags)


def _scout_root(home_root: Path, target: Path) -> Path:
    return pipeline_path(home_root, target).parent.parent


def _facts_stamp(home_root: Path, target: Path, view: ProfileView, rank_model: str | None) -> str:
    from .assessment_basis import _watched_files
    from .find_jobs.model_rank import PROMPT_VERSION, cache_dir
    from .find_jobs.rank_digest import DIGEST_VERSION
    from .find_jobs.refresh_tick import SETTINGS_FILENAME
    from .fit import fit_setting

    root = _scout_root(home_root, target)
    stores = [
        _stat(root / name / view.profile_id) for name in ("quick_assess", "resumes", "label", "ats", "quick_assess_tailored")
    ]
    try:
        watched = [_stat(path) for path in _watched_files(home_root, target)]
    except Exception:  # noqa: BLE001 - an unbound folder has nothing to watch; the stores' own stamps still count
        watched = []
    return _digest(
        FACTS_VERSION, view.resume_digest, view.settings_digest, rank_model, PROMPT_VERSION, DIGEST_VERSION, stores, watched,
        _stat(cache_dir(home_root)), fit_setting(home_root, target, path=root / SETTINGS_FILENAME).stamp(),
    )


def _master_stamp(home_root: Path, target: Path, resolved: object) -> str | None:
    """The stored master resume's revision id (kept per journal head, ``tailor_master.stored_master``).

    ``None`` without a master, and while an assessment reads the profile's own
    resume (``assess_master.ASSESS_INPUT``): what ``resume_changed`` compares
    is then the profile's resume, whose digest the facts stamp already holds.
    """

    from . import assess_master
    from .tailor_master import stored_master

    if assess_master.ASSESS_INPUT != assess_master.INPUT_EVIDENCE:
        return None
    try:
        stored = stored_master(home_root, target, resolved=resolved)
    except Exception:  # noqa: BLE001 - a master that cannot be read marks nothing stale; never a failed read of the postings
        return None
    return None if stored is None else stored.revision.revision_id


def rank_model_key(home_root: Path, target: Path) -> str | None:
    """``<adapter kind>:<model>`` as the rank score cache keys it, from the configuration alone; ``None`` when it names none."""

    from ..config import load_config
    from . import proposal_execution
    from .quick_assess import _default_model_target

    try:
        kind = _default_model_target(target).value
        config = load_config(home_root)
        name = proposal_execution._resolve_configured_target_name_for_adapter(config, kind)
        model = next(item.model for item in config.model_targets if item.name == name)
    except Exception:  # noqa: BLE001 - no usable model target means no rank score to look up, never a failed read
        return None
    return f"{kind}:{model}"


# --- the facts of one row -------------------------------------------------------------------


class _Facts:
    """One profile's stores, listed once: a posting with nothing stored costs two hashes and three set lookups."""

    def __init__(
        self, home_root: Path, target: Path, resolved: object, view: ProfileView, rank_model: str | None,
        run_latest: Mapping[str, RunAssessment] | None = None,
    ) -> None:
        from .find_jobs.job_state import JobStateSources, _stored_names
        from .find_jobs.model_rank import cache_dir
        from .find_jobs.refresh_tick import SETTINGS_FILENAME
        from .fit import fit_setting

        self.home_root, self.target, self.view = home_root, target, view
        #: The newest imported run assessment per job for this profile (0.1.10.7 M4a): used when nothing newer is stored.
        self._run_latest = run_latest or {}
        root = _scout_root(home_root, target)
        self._fit = fit_setting(home_root, target, path=root / SETTINGS_FILENAME)
        self._assessed = _stored_names(root / "quick_assess" / view.profile_id)
        self._tailored = _stored_names(root / "resumes" / view.profile_id)
        self._labelled = _stored_names(root / "label" / view.profile_id)
        # ``events={}``: application events are journal records, read when a response is built, not cached here.
        self._sources = JobStateSources(home_root=home_root, target=target, resolved=resolved, events={})
        self._rank_dir = cache_dir(home_root)
        self._ranked = _stored_names(self._rank_dir)
        self._rank_inputs = self._rank_key_inputs(resolved, rank_model) if self._ranked and rank_model else None
        self._rank_model = rank_model

    def _rank_key_inputs(self, resolved: object, rank_model: str) -> tuple[str, str] | None:
        from .find_jobs.model_rank import _hex, prefs_digest
        from .find_jobs.rank_digest import resume_digest
        from .find_jobs.rank_run import rank_prefs
        from .find_jobs.resume_input import resume_for_profile

        del rank_model
        try:
            resume = resume_for_profile(self.view.record, resolved=resolved, home_root=self.home_root, target=self.target)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - a resume that cannot be read has no rank score to look up
            return None
        prefs = rank_prefs(self.view.config)  # type: ignore[arg-type]
        return _hex({"resume_digest": resume_digest(resume.text, prefs)}), prefs_digest(prefs)

    def rank_score(self, listing_digest: str) -> int | None:
        from .find_jobs.model_rank import _read_cached, cache_key

        if self._rank_inputs is None:
            return None
        resume_sha, prefs_sha = self._rank_inputs
        key = cache_key(
            content_sha256=listing_digest, resume_digest_sha256=resume_sha, prefs_sha256=prefs_sha, model=self._rank_model or ""
        )
        if key not in self._ranked:
            return None
        hit = _read_cached(self._rank_dir / f"{key}.json")
        return None if hit is None else int(hit["score"])  # type: ignore[call-overload]

    def of(self, row: PostingRecord) -> PostingRecord:
        """``row`` with its facts as the stores hold them now."""

        from .find_jobs.job_state import NOT_ASSESSED, _identity_digest, derive_job_state, quick_assessment_fact
        from .fit import fit_percent, plain_percent, shown_state
        from .pipeline.steps import read_label

        profile_id = self.view.profile_id
        key = _identity_digest(row.job)
        item = self._sources.quick_assessment(row.job, profile_id) if key in self._assessed else None
        tailored = key in self._tailored and self._sources.tailored_at(row.job, profile_id)[0]
        state, stale, assessed_at, met, requirements, questions = NOT_ASSESSED, None, None, None, None, 0
        fit: int | None = None
        if item is not None:
            fact = quick_assessment_fact(item, basis_stale=self._sources.basis.reason(item))
            derived = derive_job_state(
                assessments=(fact,), current_content_sha256=row.listing_digest if row.listing_known else None
            )
            state, stale = derived.state, derived.assessment_stale
            assessed_at = stamp(item.updated_at or item.created_at)
            requirements = len(item.result.matrix)
            met = sum(1 for entry in item.result.matrix if entry.status.value == "met")
            questions = len(item.result.structured_questions) or len(item.result.questions)
            fit = fit_percent(item.result.matrix)
        ran = self._run_latest.get(row.job)
        if ran is not None and (assessed_at is None or (ran.assessed_at or "") > assessed_at):
            # An old run's assessment, newer than anything in the quick store (DESIGN 10.4: latest wins).
            state, stale = ran.state, self._run_stale(ran, row)
            assessed_at, met, requirements, questions = ran.assessed_at, ran.reqs_met, ran.reqs_total, ran.open_questions
            fit = plain_percent(met, requirements)  # a run's row has counts only: no classes to weight by
        # 0110-8-12: a tailored resume is the ``tailored`` flag below; the verdict state stays.
        label, ats_score = None, None
        if key in self._labelled:
            record = read_label(self.home_root, self.target, profile_id, row.job)
            if record is not None:
                value, score = record.get("label"), record.get("ats_score")
                label = value if isinstance(value, str) else None
                ats_score = score if type(score) is int and score >= 0 else None
        rank_score = self.rank_score(row.listing_digest)
        # 0110-10-02: a needs-answers posting with few requirements met AND a low rank is a weak fit (``fit.py``).
        state = shown_state(state, fit, rank_score, self._fit)
        return replace(
            row, rank_score=rank_score, state=state, stale_code=stale, assessed_at=assessed_at,
            reqs_met=met, reqs_total=requirements, open_questions=questions, tailored=bool(tailored), label=label,
            ats_score=ats_score, pinned_digest=self.view.resume_digest, settings_digest=self.view.settings_digest, fit=fit,
        )


    def _run_stale(self, ran: RunAssessment, row: PostingRecord) -> str | None:
        """Why an imported run assessment is not what the profile would get now: the quick store's own order of reasons.

        The posting's text first, then the prompt version, then the candidate
        settings. The story-bank rule is not applied: it needs the questions'
        ids, which are in the run's record and not in the imported row.
        """

        from .assessment_basis import REASON_OLDER_PROMPT, REASON_SETTINGS_CHANGED
        from .assessment_core import CURRENT_ASSESS_PROMPT_VERSIONS
        from .find_jobs.job_state import STALE_POSTING_CHANGED

        if row.listing_known and ran.listing_digest and ran.listing_digest != row.listing_digest:
            return STALE_POSTING_CHANGED
        if ran.prompt_version not in CURRENT_ASSESS_PROMPT_VERSIONS:
            return REASON_OLDER_PROMPT
        try:
            current = self._sources.basis.current(self.view.profile_id)
        except Exception:  # noqa: BLE001 - settings that cannot be read say nothing about staleness
            current = None
        if current is not None and ran.constraints_digest != current.constraints_digest:
            return REASON_SETTINGS_CHANGED
        return None


# --- the index, read once for every profile ------------------------------------------------


class _IndexOnce:
    """A ``CompanyIndex`` whose files are read once per refresh, however many profiles are matched."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.root = inner.root  # type: ignore[attr-defined]
        self._entries: dict[tuple[str, str], object] = {}

    def read(self, ats: str, slug: str) -> object:
        key = (ats, slug)
        if key not in self._entries:
            self._entries[key] = self._inner.read(ats, slug)  # type: ignore[attr-defined]
        return self._entries[key]

    def by_url(self) -> dict[str, tuple[str, object]]:
        """``normalized url -> (board key, indexed posting)`` for every posting of the entries read, removed ones included."""

        from .find_jobs.contracts import FindJobsContractError, normalize_url

        found: dict[str, tuple[str, object]] = {}
        for (ats, slug), entry in self._entries.items():
            if entry is None:
                continue
            board = board_key(ats, slug)
            for posting in entry.postings.values():  # type: ignore[attr-defined]
                try:
                    found[normalize_url(posting.url)] = (board, posting)
                except FindJobsContractError:
                    continue
        return found


def _watched(home_root: Path, target: Path) -> tuple[object, ...]:
    from .find_jobs.watchlist import list_active

    try:
        return tuple(list_active(home_root, target))
    except Exception:  # noqa: BLE001 - no readable watchlist is no boards to match, never a failed read
        return ()


def _memo_matcher(roles: Sequence[str], store: object | None) -> object:
    """The search's own ``TitleMatcher`` that decides each distinct title once (its counts are then of titles, and unused here)."""

    from .find_jobs.title_query import TitleMatcher

    class _Memo(TitleMatcher):
        def __init__(self) -> None:
            super().__init__(roles, store)  # type: ignore[arg-type]
            self._decided: dict[str, bool] = {}

        def matches(self, title: str) -> bool:
            found = self._decided.get(title)
            if found is None:
                found = self._decided[title] = super().matches(title)
            return found

    return _Memo()


def _board_records(
    view: ProfileView, keys: Sequence[str], rows: Iterable[object], previous: Mapping[str, Sequence[PostingRecord]],
    index: _IndexOnce, built_at: str,
) -> list[PostingRecord]:
    """The rows of the boards ``keys`` for this profile, without facts: its live matches, and the rows it had whose
    posting the board no longer lists (kept, with ``removed_at``)."""

    from .find_jobs.contracts import FindJobsContractError, normalize_url
    from .find_jobs.model_rank import content_digest
    from .pipeline.store import fits

    matched: dict[str, list[object]] = {}
    for row in rows:
        matched.setdefault(board_key(row.provider.value, row.board_token), []).append(row)  # type: ignore[attr-defined]
    found: dict[str, PostingRecord] = {}
    for key in keys:
        live, had = matched.get(key, ()), previous.get(key, ())
        if not live and not had:
            continue
        entry = index.read(*split_board(key))
        by_url: dict[str, object] = {}
        for posting in (entry.postings.values() if entry is not None else ()):  # type: ignore[attr-defined]
            try:
                by_url[normalize_url(posting.url)] = posting
            except FindJobsContractError:
                continue
        seen: set[str] = set()
        for row in live:
            posting = by_url.get(row.normalized_url)  # type: ignore[attr-defined]
            if posting is None:
                continue
            first_seen = stamp(posting.first_seen)  # type: ignore[attr-defined]
            if first_seen is None or not fits("job", row.normalized_url) or not fits("board", key):  # type: ignore[attr-defined]
                continue  # not a shape the file holds: the posting is left out rather than stored as text
            seen.add(row.normalized_url)  # type: ignore[attr-defined]
            # The date the posted window judged is the INDEX's (``index_search._keep``); the cached row's is the same
            # date unless its body dropped it. Kept so a new day knows which boards hold a posting the window left.
            published_at = stamp(posting.published_at) or stamp(row.published_at)  # type: ignore[attr-defined]
            found[row.normalized_url] = PostingRecord(  # type: ignore[attr-defined]
                job=row.normalized_url, profile_id=view.profile_id, board=key, first_seen=first_seen,  # type: ignore[attr-defined]
                published_at=published_at, removed_at=None, listing_digest=content_digest(row),  # type: ignore[attr-defined,arg-type]
                listing_known=bool(row.content_sha256 and row.text), rank_score=None, match_rank=1, state="not_assessed",  # type: ignore[attr-defined]
                stale_code=None, assessed_at=None, reqs_met=None, reqs_total=None, open_questions=0, tailored=False,
                label=None, ats_score=None, pinned_digest=view.resume_digest, settings_digest=view.settings_digest,
                updated_at=built_at,
            )
        for row in had:
            if row.job in seen or row.job in found:
                continue
            posting = by_url.get(row.job)
            removed_at = stamp(getattr(posting, "removed_at", None)) if posting is not None else None
            if removed_at is not None:
                found[row.job] = replace(row, removed_at=removed_at, updated_at=built_at)
    return list(found.values())


def _matched_rows(
    view: ProfileView, boards: Sequence[object], index: _IndexOnce, home_root: Path, now: datetime, built_at: str
) -> list[PostingRecord]:
    """The profile's live matches over ``boards`` as rows without facts: ``index_search``'s own rule, no board request."""

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.index_search import read_indexed_boards
    from .find_jobs.title_query import open_tag_store

    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    rows, _failures, _summary = read_indexed_boards(
        boards, index=index, cache=cache, config=view.config, now=now, remember_search=False,  # type: ignore[arg-type]
        tags=open_tag_store(home_root), home_root=home_root,
    )
    keys = [board_key(board.provider.value, board.board_token) for board in boards]  # type: ignore[attr-defined]
    return _board_records(view, keys, rows, {}, index, built_at)


def profile_posting_rows(view: ProfileView, home_root: Path, target: Path, now: datetime) -> list[object]:
    """The profile's live matches as the index's own ``PostingRow``s, matched from the WHOLE index: no board request.

    It reads every company's index file. A caller that already has the
    profile's stored rows asks :func:`posting_rows` for them instead.
    """

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.index_search import read_indexed_boards
    from .find_jobs.title_query import open_tag_store

    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    rows, _failures, _summary = read_indexed_boards(
        _watched(home_root, target), index=CompanyIndex.for_home(home_root), cache=cache, config=view.config, now=now,  # type: ignore[arg-type]
        remember_search=False, tags=open_tag_store(home_root), home_root=home_root,
    )
    return list(rows)


def posting_rows(home_root: Path, rows: Iterable[PostingRecord]) -> list[object]:
    """The index's own ``PostingRow`` (what the rank lane ranks) for each of ``rows`` its board still lists.

    0110-9-01: read from the boards these rows are on only (one index file and
    one cached body per board), never from the whole index.
    """

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex, cached_posting_rows
    from .find_jobs.contracts import FindJobsContractError, normalize_url

    home_root = Path(home_root)
    by_board: dict[str, set[str]] = {}
    for row in rows:
        if row.removed_at is None:
            by_board.setdefault(row.board, set()).add(row.job)
    index = CompanyIndex.for_home(home_root)
    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    found: list[object] = []
    for board, jobs in sorted(by_board.items()):
        ats, slug = split_board(board)
        entry = index.read(ats, slug)
        if entry is None:
            continue
        wanted = []
        for posting in entry.live():
            try:
                if normalize_url(posting.url) in jobs:
                    wanted.append(posting.posting_id)
            except FindJobsContractError:
                continue
        found.extend(cached_posting_rows(cache, ats, slug, wanted).rows.values())
    return found


class TagPending:
    """0110-8-05: is a served row matched by a GENERIC title alone while its posting's function tag is not known yet?

    The matcher is the search's own (``title_query.TitleMatcher``), one per
    profile, asked for the rows a response serves only. Never raises: with no
    tag store nothing is pending (the plain rule decided, as it always did).
    """

    def __init__(self, home_root: Path, views: Iterable[ProfileView]) -> None:
        from .find_jobs.title_query import TitleMatcher, open_tag_store

        store = open_tag_store(Path(home_root))
        self._matchers = {view.profile_id: TitleMatcher(view.config.roles, store) for view in views}  # type: ignore[attr-defined]

    def __call__(self, profile_id: str, title: str | None) -> bool:
        matcher = self._matchers.get(profile_id)
        return bool(matcher is not None and matcher.generic_roles and title and matcher.decide(title).tag_pending)


# --- refresh --------------------------------------------------------------------------------


def _best_tag_order(
    rows: Iterable[tuple[str, str, int | None, str, int, str | None, int]], default_id: str | None
) -> list[tuple[int, str, str]]:
    """``(match_rank, job, profile_id)`` for the rows whose place among their posting's profiles changed.

    0110-8-01 / 0110-8-12: the profile that tailored a resume first, then a
    current assessment, then a stale one, then no assessment; the rank score
    orders profiles only inside one of those groups. Rank scores of two
    profiles come from different resumes and arrive at different times, so
    they never move a posting away from the profile that assessed it.
    """

    by_job: dict[str, list[tuple[str, int | None, str, int, str | None, int]]] = {}
    for job, profile_id, rank_score, state, match_rank, stale_code, tailored in rows:
        by_job.setdefault(job, []).append((profile_id, rank_score, state, match_rank, stale_code, tailored))

    def place(item: tuple[str, int | None, str, int, str | None, int]) -> tuple[bool, int, int, bool, str]:
        profile_id, rank_score, state, _current, stale_code, tailored = item
        assessed = 2 if state == "not_assessed" else 1 if stale_code is not None else 0
        return not tailored, assessed, -(rank_score if rank_score is not None else -1), profile_id != default_id, profile_id

    changed = []
    for job, group in by_job.items():
        group.sort(key=place)
        for rank, item in enumerate(group, start=1):
            if rank != item[3]:
                changed.append((rank, job, item[0]))
    return changed


def open_store(home_root: Path, target: Path) -> PipelineStore:
    return PipelineStore(pipeline_path(Path(home_root), Path(target)))


# --- the watched boards: read from the journal only when the committed watchlist changed -----

_WATCHLIST_PREFIX = "records/scout-watchlist/"
_WATCHLIST_SOURCE = "watchlist"
_BOARDS_LOCK = threading.Lock()
#: workpad path -> (journal head, watchlist tree id, the boards or None, their digest)
_BOARDS: dict[str, tuple[str | None, str | None, tuple[object, ...] | None, str]] = {}


class _Boards:
    """The active watched boards and their digest; the boards are read from the journal only when asked for."""

    def __init__(self, home_root: Path, target: Path, key: str | None, head: str | None, tree: str | None, digest: str | None, boards: tuple[object, ...] | None) -> None:
        self._home_root, self._target, self._key, self._head, self._tree = home_root, target, key, head, tree
        self._boards = boards
        #: The watchlist could not be read just now: no boards for this read, and nothing of it is kept.
        self.unread = False
        self.digest = digest if digest is not None else _boards_digest(self.list())

    def list(self) -> tuple[object, ...]:
        if self._boards is None:
            from .find_jobs.watchlist import list_active

            try:
                self._boards = tuple(list_active(self._home_root, self._target))
            except Exception:  # noqa: BLE001 - no readable watchlist is no boards to match (``_watched``'s rule), and it is not kept
                self.unread = True
                return ()
            if self._key is not None and self._tree is not None:
                with _BOARDS_LOCK:
                    _BOARDS[self._key] = (self._head, self._tree, self._boards, _boards_digest(self._boards))
        return self._boards


def _boards(home_root: Path, target: Path, resolved: object, store: PipelineStore) -> _Boards:
    """The watched boards as of the committed watchlist: kept in the process by the journal head, and their digest in
    ``posting_source`` by the watchlist's Git tree id, so neither a request nor a fresh process reads every record again."""

    from ..journal import committed_tree_id
    from ..workpad import workpad_head_without_git

    try:
        root = Path(resolved.path)  # type: ignore[attr-defined]
        key, head = os.fspath(root), workpad_head_without_git(root)
    except Exception:  # noqa: BLE001 - no workpad path to key by: the boards are read as before
        return _Boards(home_root, target, None, None, None, None, None)
    with _BOARDS_LOCK:
        kept = _BOARDS.get(key)
    if kept is not None and head is not None and kept[0] == head:
        return _Boards(home_root, target, key, head, kept[1], kept[3], kept[2])
    try:
        tree = committed_tree_id(root, _WATCHLIST_PREFIX)
    except Exception:  # noqa: BLE001 - git cannot say: the boards are read as before
        tree = None
    if tree is None:
        return _Boards(home_root, target, None, None, None, None, None)
    if kept is not None and kept[1] == tree:
        with _BOARDS_LOCK:
            _BOARDS[key] = (head, tree, kept[2], kept[3])
        return _Boards(home_root, target, key, head, tree, kept[3], kept[2])
    source = _digest("watchlist-tree:1", tree)
    stored = store.posting_source(_WATCHLIST_SOURCE)
    if stored is not None and stored[0] == source:
        with _BOARDS_LOCK:
            _BOARDS[key] = (head, tree, None, stored[1])
        return _Boards(home_root, target, key, head, tree, stored[1], None)
    found = _Boards(home_root, target, key, head, tree, None, None)
    if not found.unread:
        store.set_posting_source(_WATCHLIST_SOURCE, source, found.digest)
    return found


# --- refresh: plan (what differs), apply (build it) ------------------------------------------


@dataclass
class _Plan:
    """What one refresh found: per active profile, whether its rows are matched again, get their facts again, or stay."""

    home_root: Path
    target: Path
    moment: datetime
    built_at: str
    resolved: object
    views: tuple[ProfileView, ...]
    files: Mapping[str, tuple[int, int]]
    tags: object
    boards: _Boards
    rank_model: str | None
    day: str
    history: tuple[int, object]
    previous: Mapping[str, PostingBuild]
    digests: Mapping[str, tuple[str, str]]
    kinds: dict[str, str]
    drop_profiles: bool
    force: bool
    #: Served by the request's own wait, whatever ran last: a profile with no build yet, or whose settings the user changed.
    urgent: bool = False
    matched: dict[str, bool] = field(default_factory=dict)
    #: The profiles whose match differs by the UTC day alone (a later day): the posted window is all there is to apply.
    window_only: frozenset[str] = frozenset()

    @property
    def match_needed(self) -> bool:
        return any(kind == BUILD_FULL for kind in self.kinds.values())


def _plan(
    home_root: Path, target: Path, *, store: PipelineStore, now: datetime | None, force: bool, resolved: object | None
) -> _Plan:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    built_at = stamp(moment)
    assert built_at is not None
    resolved, views = active_profiles(home_root, target, resolved)
    builds = store.posting_builds()
    files = _index_files(home_root)
    tags = _tags_stamp(home_root)
    index_stamp = _index_stamp(files, tags)
    rank_model = rank_model_key(home_root, target)
    boards = _boards(home_root, target, resolved, store)
    day = moment.date().isoformat()
    # What old runs assessed (0.1.10.7 M4a): a build made before an import gets its facts again.
    history = store.run_history_stamp()
    # 0.1.10.9 master P7: the master's revision, which the ``resume_changed`` stale check reads when assessments read
    # the master. Nothing without a master, and nothing while they read the profile's own resume.
    master = _master_stamp(home_root, target, resolved)
    digests: dict[str, tuple[str, str]] = {}
    kinds: dict[str, str] = {}
    window_only: set[str] = set()
    urgent = force
    for view in views:
        previous = builds.get(view.profile_id)
        facts_digest = _facts_stamp(home_root, target, view, rank_model)
        if history[0]:
            facts_digest = _digest(facts_digest, "run-history", list(history))
        if master is not None:
            facts_digest = _digest(facts_digest, "master", master)
        match_digest = _digest(MATCH_VERSION, view.settings_digest, index_stamp, boards.digest, day)
        if boards.unread and previous is not None and not force:
            # The watchlist could not be read just now (a journal write in flight): the stored rows are not thrown
            # away for it. They are matched again by the next read that can read it.
            match_digest = previous.match_digest
        digests[view.profile_id] = (match_digest, facts_digest)
        if force or previous is None or previous.match_digest != match_digest:
            kinds[view.profile_id] = BUILD_FULL
            urgent = urgent or previous is None or previous.settings_digest != view.settings_digest
            if not force and previous is not None and previous.built_at[:10] < day:
                # The same settings, index, tags and boards as the last build, on a later day: only the day moved.
                before = _digest(MATCH_VERSION, view.settings_digest, index_stamp, boards.digest, previous.built_at[:10])
                if before == previous.match_digest:
                    window_only.add(view.profile_id)
        elif previous.facts_digest != facts_digest:
            kinds[view.profile_id] = BUILD_FACTS
        else:
            kinds[view.profile_id] = BUILD_UNCHANGED
    return _Plan(
        home_root=home_root, target=target, moment=moment, built_at=built_at, resolved=resolved, views=views, files=files,
        tags=tags, boards=boards, rank_model=rank_model, day=day, history=history, previous=builds, digests=digests,
        kinds=kinds, drop_profiles=bool(set(builds) - {view.profile_id for view in views}), force=force, urgent=urgent,
        window_only=frozenset(window_only),
    )


Progress = Callable[[str, int, int], None]


def _window_cutoff(view: ProfileView, moment: datetime) -> str | None:
    """The search's own cutoff at ``moment`` (``filters.published_cutoff``: ``now - max_age_days``, or the fixed
    ``published_after``, which never moves) as a stamp; ``None`` when the config's fixed date does not parse (the
    match itself says so, for the boards it reads)."""

    from .find_jobs.filters import published_cutoff

    try:
        return stamp(published_cutoff(view.config, now=moment))  # type: ignore[arg-type]
    except ValueError:
        return None


def _past_window(rows: Sequence[PostingRecord], cutoff: str) -> list[PostingRecord]:
    """The listed rows whose date is before the cutoff. A row with no date is never dropped by the window."""

    return [row for row in rows if row.removed_at is None and row.published_at is not None and row.published_at < cutoff]


def _left_window(view: ProfileView, rows: Mapping[str, Sequence[PostingRecord]], moment: datetime) -> set[str]:
    """The boards holding a listed posting of this profile that the posted window has moved past at ``moment``."""

    cutoff = _window_cutoff(view, moment)
    return set() if cutoff is None else {board for board, held in rows.items() if _past_window(held, cutoff)}


def _apply_window(
    plan: _Plan, store: PipelineStore, view: ProfileView, rows: dict[str, list[PostingRecord]], stamps: Mapping[str, str]
) -> set[str]:
    """Only the day moved: drop this profile's listed rows that the posted window has left. No board is matched.

    Each row past the cutoff is put to the search's own rule (``filters.published_too_old`` on the board's index
    entry, what ``index_search`` asks) and dropped when the rule says too old; the board's other rows and its stamp
    stay as they are, which is what a match of the board would store: nothing else of it moved. Returns the boards
    this could not settle (no index entry, or a row the entry does not list): those are matched the long way.
    """

    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.contracts import FindJobsContractError, normalize_url
    from .find_jobs.filters import published_too_old
    from .find_jobs.index_search import _indexed_row

    cutoff = _window_cutoff(view, plan.moment)
    if cutoff is None:
        return set()
    index = CompanyIndex.for_home(plan.home_root)
    unsettled: set[str] = set()
    for board, held in sorted(rows.items()):
        past = _past_window(held, cutoff)
        if not past:
            continue
        entry = index.read(*split_board(board))
        if entry is None or board not in stamps:
            unsettled.add(board)
            continue
        listed: dict[str, object] = {}
        for posting in entry.live():
            try:
                listed[normalize_url(posting.url)] = posting
            except FindJobsContractError:
                continue
        if any(row.job not in listed for row in past):
            unsettled.add(board)
            continue
        leaving = {row.job for row in past if published_too_old(_indexed_row(entry, listed[row.job]), view.config, now=plan.moment)}  # type: ignore[arg-type]
        if leaving:
            rows[board] = [row for row in held if row.job not in leaving]
            store.replace_board_postings(view.profile_id, {board: stamps[board]}, rows[board])
    return unsettled


def _match(plan: _Plan, store: PipelineStore, views: Sequence[ProfileView], facts_of: Callable[[ProfileView], _Facts], progress: Progress | None) -> None:
    """Match again the boards whose stamp differs, for each of ``views``, a few boards at a time."""

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.index_search import read_indexed_boards
    from .find_jobs.title_query import open_tag_store

    home_root = plan.home_root
    want: dict[str, dict[str, str]] = {}
    stale: dict[str, set[str]] = {}
    previous: dict[str, dict[str, list[PostingRecord]]] = {}
    stored: dict[str, dict[str, str]] = {}
    unsettled: dict[str, set[str]] = {}
    for view in views:
        stored[view.profile_id] = store.posting_board_stamps(view.profile_id)
        had: dict[str, list[PostingRecord]] = {}
        for row in store.postings(profile_id=view.profile_id, live=False):
            had.setdefault(row.board, []).append(row)
        previous[view.profile_id] = had
        if view.profile_id in plan.window_only:
            # 0.1.10.11 (C8): only the day moved. The rows the window left are dropped; no board is matched for it.
            unsettled[view.profile_id] = _apply_window(plan, store, view, had, stored[view.profile_id])
    # Boards are matched for a profile unless the day was all that moved and the window settled it. The watched boards
    # are read (from the journal, in a fresh process) only then.
    matching = {view.profile_id for view in views if unsettled.get(view.profile_id, True)}
    watched = (
        {board_key(board.provider.value, board.board_token): board for board in plan.boards.list()}  # type: ignore[attr-defined]
        if matching else {}
    )
    for view in views:
        had = previous[view.profile_id]
        if view.profile_id not in matching:
            stale[view.profile_id] = set()
            plan.matched[view.profile_id] = False
            continue
        # The day is not in a board's stamp (0.1.10.11, C8): a new day matches the boards whose posting left the window.
        rule = _digest(MATCH_VERSION, view.settings_digest, plan.tags)
        want[view.profile_id] = {key: _digest(rule, plan.files.get(f"{key}.json")) for key in watched}
        built = plan.previous.get(view.profile_id)
        # Time moves a posting out of the window only. A clock behind the day of the last build could move one back in.
        every = plan.force or (built is not None and plan.day < built.built_at[:10])
        if every:
            left: set[str] = set()
        elif view.profile_id in unsettled:
            left = unsettled[view.profile_id]
        else:
            left = _left_window(view, had, plan.moment)
        stale[view.profile_id] = {
            key for key, value in want[view.profile_id].items() if every or stored[view.profile_id].get(key) != value or key in left
        }
        gone = (set(stored[view.profile_id]) | set(had)) - set(watched)
        if gone:
            store.replace_board_postings(view.profile_id, {key: None for key in gone}, [])
        plan.matched[view.profile_id] = len(stale[view.profile_id]) == len(watched)
    todo = sorted(set().union(*stale.values())) if stale else []
    total, done = len(todo), 0
    if progress is not None:
        progress(PHASE_MATCHING, 0, total)
    if not todo:
        return
    tags = open_tag_store(home_root)
    matchers = {view.profile_id: _memo_matcher(view.config.roles, tags) for view in views}  # type: ignore[attr-defined]
    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    chunk: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal chunk, size, done
        if not chunk:
            return
        index = _IndexOnce(CompanyIndex.for_home(home_root))  # only this chunk's companies are held
        for view in views:
            keys = [key for key in chunk if key in stale[view.profile_id]]
            if not keys:
                continue
            rows, _failures, _summary = read_indexed_boards(
                [watched[key] for key in keys], index=index, cache=cache, config=view.config, now=plan.moment,  # type: ignore[arg-type]
                remember_search=False, tags=tags, home_root=home_root, title_matcher=matchers[view.profile_id],  # type: ignore[arg-type]
            )
            records = _board_records(view, keys, rows, previous[view.profile_id], index, plan.built_at)
            facts = facts_of(view) if records else None
            store.replace_board_postings(
                view.profile_id, {key: want[view.profile_id][key] for key in keys},
                [facts.of(record) for record in records] if facts is not None else [],
            )
        done += len(chunk)
        chunk, size = [], 0
        if progress is not None:
            progress(PHASE_MATCHING, done, total)

    for key in todo:
        chunk.append(key)
        size += plan.files.get(f"{key}.json", (0, 0))[1]
        if size >= CHUNK_BYTES or len(chunk) >= CHUNK_BOARDS:
            flush()
    flush()


def _apply(plan: _Plan, store: PipelineStore, progress: Progress | None = None, *, served: Mapping[str, str] | None = None) -> RefreshResult:
    """Build what ``plan`` found to differ. ``served``: what to answer per profile instead of what was done (``stale``)."""

    views = plan.views
    dropped = store.keep_posting_profiles(view.profile_id for view in views) if plan.drop_profiles else 0
    kept: dict[str, _Facts] = {}

    def facts_of(view: ProfileView) -> _Facts:
        found = kept.get(view.profile_id)
        if found is None:
            run_latest = (
                {item.job: item for item in store.run_assessments(profile_id=view.profile_id, latest=True)} if plan.history[0] else {}
            )
            found = kept[view.profile_id] = _Facts(plan.home_root, plan.target, plan.resolved, view, plan.rank_model, run_latest)
        return found

    matching = [view for view in views if plan.kinds[view.profile_id] == BUILD_FULL]
    if matching:
        _match(plan, store, matching, facts_of, progress)
    for view in views:
        kind = plan.kinds[view.profile_id]
        if kind == BUILD_UNCHANGED:
            continue
        match_digest, facts_digest = plan.digests[view.profile_id]
        previous = plan.previous.get(view.profile_id)
        build = PostingBuild(view.profile_id, match_digest, facts_digest, view.resume_digest, view.settings_digest, 0, plan.built_at)
        if kind == BUILD_FULL:
            # Rows of the boards matched just now have their facts; the others get them when the facts differ.
            again = not plan.matched.get(view.profile_id, False) and (previous is None or previous.facts_digest != facts_digest)
            if not again:
                store.finish_posting_build(build)
                continue
        elif previous is not None:
            build = replace(build, match_digest=previous.match_digest)
        if progress is not None:
            progress(PHASE_FACTS, 0, 0)
        facts = facts_of(view)
        rows = [facts.of(replace(row, updated_at=plan.built_at)) for row in store.postings(profile_id=view.profile_id, live=False)]
        store.replace_postings(build, rows)
    if dropped or any(kind != BUILD_UNCHANGED for kind in plan.kinds.values()):
        default_id = next((view.profile_id for view in views if view.is_default), None)
        store.set_match_ranks(_best_tag_order(store.posting_rank_inputs(), default_id))
    return RefreshResult(views, dict(served) if served is not None else dict(plan.kinds), store.posting_count(), dropped, plan.resolved)


# --- one build at a time ----------------------------------------------------------------------


class _Flight:
    """One project's build in this process: who is building, how far it is, and what the last one left."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.plan_lock = threading.Lock()
        self.building = False
        self.started = 0.0
        self.finished = 0.0
        self.phase, self.done, self.total = PHASE_IDLE, 0, 0
        #: The running build knows how many boards it matches (a caller with a ``wait`` waits only for a small one).
        self.counted = False
        #: Builds that matched the index, started in this process (the timing gate counts them).
        self.builds = 0
        #: How many boards the last finished build matched (one, after one company's update).
        self.last_boards = 0
        #: The tag store the last build matched with: when it differs, every board is matched again.
        self.rule: tuple[object] | None = None
        #: ``updated_at`` of the rows the RUNNING build writes (:func:`building_stamp`); ``None`` when none runs.
        self.built_at: str | None = None
        self.views: tuple[ProfileView, ...] = ()
        self.resolved: object = None
        #: Every active profile has stored rows a caller can be served while a build runs.
        self.ready = False
        self.known = False
        self.error: BaseException | None = None

    def progress(self, phase: str, done: int, total: int) -> None:
        with self.cond:
            self.phase, self.done, self.total = phase, done, total
            if not self.counted:
                self.counted = True
                self.cond.notify_all()

    def status(self) -> dict[str, object]:
        if self.building:
            state = STATE_REFRESHING if self.ready else STATE_PREPARING
            percent = min(99, int(100 * self.done / self.total)) if self.total else 0
        else:
            state, percent = (STATE_READY, 100) if self.known else (STATE_UNKNOWN, 0)
        return {
            "schema_version": STATUS_SCHEMA_VERSION, "state": state, "percent": percent,
            "phase": self.phase if self.building else PHASE_IDLE,
            "boards_done": self.done if self.building else 0, "boards_total": self.total if self.building else 0,
            "builds": self.builds, "last_boards": self.last_boards,
        }

    def begin(self, plan: _Plan) -> None:
        with self.cond:
            self.building, self.started, self.error = True, time.monotonic(), None
            self.builds += 1
            self.phase, self.done, self.total, self.counted = PHASE_MATCHING, 0, 0, False
            self.views, self.resolved = plan.views, plan.resolved
            self.built_at = plan.built_at
            self.ready = all(view.profile_id in plan.previous for view in plan.views)

    def seen(self, plan: _Plan) -> None:
        """A read found the stored rows current (or gave them their facts): there is something to serve."""

        with self.cond:
            self.views, self.resolved, self.ready, self.known = plan.views, plan.resolved, True, True

    def end(self, plan: _Plan | None, error: BaseException | None = None) -> None:
        with self.cond:
            self.building, self.finished, self.error = False, time.monotonic(), error
            self.phase, self.built_at = PHASE_IDLE, None
            if plan is not None and error is None:
                self.last_boards = self.total
                self.rule = (plan.tags,)
                self.views, self.resolved, self.ready, self.known = plan.views, plan.resolved, True, True
            self.cond.notify_all()


_FLIGHTS_LOCK = threading.Lock()
_FLIGHTS: dict[tuple[str, str], _Flight] = {}


def _flight(home_root: Path, target: Path) -> _Flight:
    key = (os.fspath(home_root), os.fspath(target))
    with _FLIGHTS_LOCK:
        found = _FLIGHTS.get(key)
        if found is None:
            found = _FLIGHTS[key] = _Flight()
        return found


def model_status(home_root: Path, target: Path) -> dict[str, object]:
    """How the read model is in THIS process, from memory alone: ``scout-postings-status:1``.

    ``state``: ``preparing`` (the first build runs: a profile has no rows to
    serve yet), ``refreshing`` (a build runs, the stored rows are served),
    ``ready``, or ``unknown`` (nothing has asked for the postings yet).
    ``percent`` is the share of boards the running build has matched.
    """

    return _flight(Path(home_root), Path(target)).status()


def building_stamp(home_root: Path, target: Path) -> str | None:
    """The ``updated_at`` the rows of the build RUNNING in this process carry; ``None`` when none runs.

    A build writes its rows a few boards at a time, and each profile's place among a posting's profiles
    (``match_rank``) only when it ends. A caller served the rows as stored (``refresh(wait=...)``, ``builds``:
    ``stale``) that is about to ACT on rows, not show them, leaves the rows with this stamp alone: they are not
    final. (After a build that was interrupted, such rows are the ones newer than their profile's ``posting_build``.)
    """

    flight = _flight(Path(home_root), Path(target))
    with flight.cond:
        return flight.built_at if flight.building else None


def refresh(
    home_root: Path,
    target: Path,
    *,
    store: PipelineStore | None = None,
    now: datetime | None = None,
    force: bool = False,
    resolved: object | None = None,
    wait: float | None = None,
    progress: Progress | None = None,
) -> RefreshResult:
    """Bring the read model in step with the index, the profiles and the stores; rebuilds only what changed.

    One build at a time in the process: a caller that finds one running waits
    for it. ``force`` matches every board of every active profile again
    whatever the stamps say (the full rebuild). ``wait`` (seconds; the
    server's read routes): how long a caller waits for a build that matches
    the index before it is answered from the rows as stored (``builds``:
    ``stale``) or, with no stored rows yet, :class:`PostingModelPreparing`;
    the build then runs in its own thread. ``None`` waits for it. ``progress``
    gets ``(phase, boards done, boards in all)`` from a build this call runs
    itself. Raises :class:`PostingModelError` when the folder has no Scout
    gig or no readable config.
    """

    from ..workpad import committed_read_cache

    # One workpad check and one committed read per journal family for the whole refresh (0110-044), not one each.
    with committed_read_cache():
        return _refresh(Path(home_root), Path(target), store=store, now=now, force=force, resolved=resolved, wait=wait, progress=progress)


def _refresh(
    home_root: Path, target: Path, *, store: PipelineStore | None, now: datetime | None, force: bool, resolved: object | None,
    wait: float | None = None, progress: Progress | None = None,
) -> RefreshResult:
    flight = _flight(home_root, target)
    opened = store if store is not None else open_store(home_root, target)
    started_here = False
    try:
        while True:
            with flight.cond:
                if flight.building:
                    if wait is None:
                        flight.cond.wait(1.0)
                        continue
                    left = wait - (time.monotonic() - flight.started)
                    if left > 0 and flight.counted and flight.total <= SMALL_BUILD_BOARDS:
                        flight.cond.wait(left)  # a small build: its end, or the caller's patience
                        if not flight.building:
                            continue
                    elif left > 0 and not flight.counted:
                        flight.cond.wait(min(left, 0.05))  # not counted yet: small or large is known in a moment
                        continue
                    return _served(flight, opened)
                if started_here and flight.error is not None:
                    raise flight.error  # the build this call started failed: its error, not another try
            with flight.plan_lock:
                if flight.building:
                    continue
                plan = _plan(home_root, target, store=opened, now=now, force=force, resolved=resolved)
                if not plan.match_needed:
                    result = _apply(plan, opened)
                    flight.seen(plan)
                    return result
                recent = flight.finished and time.monotonic() - flight.finished < REBUILD_MIN_SECONDS
                every_board = flight.rule is not None and flight.rule != (plan.tags,)
                if wait is not None and recent and every_board and not plan.urgent and flight.error is None:
                    # The tags moved again right after a build: the rows as stored (their facts current).
                    served = {profile_id: BUILD_STALE if kind == BUILD_FULL else kind for profile_id, kind in plan.kinds.items()}
                    for view in plan.views:
                        if plan.kinds[view.profile_id] == BUILD_FULL:
                            previous = plan.previous[view.profile_id]
                            differs = previous.facts_digest != plan.digests[view.profile_id][1]
                            plan.kinds[view.profile_id] = BUILD_FACTS if differs else BUILD_UNCHANGED
                    return _apply(plan, opened, served=served)
                flight.begin(plan)
            if wait is None:
                return _build(flight, plan, opened, progress)
            started_here = True
            threading.Thread(target=_build_in_background, args=(flight, plan), name="scout-posting-model", daemon=True).start()
    finally:
        if store is None:
            opened.close()


def _served(flight: _Flight, store: PipelineStore) -> RefreshResult:
    """What a caller that did not wait gets: the rows as stored, or :class:`PostingModelPreparing`."""

    if not flight.ready:
        raise PostingModelPreparing(flight.status())
    return RefreshResult(flight.views, {view.profile_id: BUILD_STALE for view in flight.views}, store.posting_count(), 0, flight.resolved)


def _build(flight: _Flight, plan: _Plan, store: PipelineStore, progress: Progress | None) -> RefreshResult:
    def report(phase: str, done: int, total: int) -> None:
        flight.progress(phase, done, total)
        if progress is not None:
            progress(phase, done, total)

    try:
        result = _apply(plan, store, report)
    except BaseException as exc:  # noqa: BLE001 - re-raised: the waiting callers are let go first
        flight.end(None, exc)
        raise
    flight.end(plan)
    return result


def _build_in_background(flight: _Flight, plan: _Plan) -> None:
    from ..workpad import committed_read_cache

    began = time.monotonic()
    try:
        store = open_store(plan.home_root, plan.target)
        try:
            with committed_read_cache():
                result = _build(flight, plan, store, None)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a thread's last boundary: the error is kept for the caller that asks next
        _logger.warning("postings: the build did not finish (%s)", type(exc).__name__)
        if flight.building:
            flight.end(None, exc)
        return
    _logger.info("postings: built in %.1f s (%d rows)", time.monotonic() - began, result.rows)


def _boards_digest(boards: Sequence[object]) -> str:
    return _digest("boards", sorted(f"{board.provider.value}:{board.board_token}" for board in boards))  # type: ignore[attr-defined]


# --- serving rows: the text, read back from the index ---------------------------------------


@dataclass(frozen=True)
class PostingText:
    """What the index and the board cache hold for one posting: public text, written by strangers."""

    title: str
    company: str
    location: str
    url: str
    text: str | None
    work_mode: str
    salary: str | None
    #: 0110-8-02: where the posting lives (``greenhouse:acme`` and the provider's id), so a missing description can be fetched for it alone.
    board: str | None = None
    posting_id: str | None = None
    #: 0110-8-11: the company index's own name for the board ("Garner Health"); ``company`` is the cached row's (the board token).
    company_name: str | None = None


def _salary(pay: object | None) -> str | None:
    if pay is None:
        return None
    low, high, currency = getattr(pay, "min", None), getattr(pay, "max", None), getattr(pay, "currency", "")
    period = getattr(getattr(pay, "period", None), "value", None)

    def amount(value: object) -> str:
        return f"{value:,.0f}" if isinstance(value, (int, float)) else ""

    if low is not None and high is not None and low != high:
        text = f"{amount(low)}-{amount(high)}"
    else:
        text = amount(low if low is not None else high)
    if not text:
        return None
    return f"{currency} {text}".strip() + (f" per {period}" if period else "")


#: 0110-9-01: what was read for a board's postings, kept in the process while the board's index file and cached body
#: are the same files (mtime and size; for Greenhouse also the folder its detail files are in). A page of 50 postings is on about 50 companies, and reading it meant parsing
#: every one of their index files and board bodies on every request. Only the postings asked for are kept (the
#: matched ones), the least recently read boards leave first.
_TEXTS_LOCK = threading.Lock()
_TEXTS: dict[tuple[str, str], tuple[tuple[object, object], dict[str, "PostingText | None"]]] = {}
TEXT_CACHE_BOARDS = 4000


def _kept_texts(key: tuple[str, str], stamp_: tuple[object, object], jobs: set[str]) -> dict[str, "PostingText | None"] | None:
    """The kept answer for every one of ``jobs`` (``None``: the posting is not on the board), or ``None`` when one is not kept."""

    with _TEXTS_LOCK:
        kept = _TEXTS.get(key)
        if kept is None or kept[0] != stamp_ or not jobs <= kept[1].keys():
            return None
        _TEXTS[key] = _TEXTS.pop(key)  # read last: leaves last
        return {job: kept[1][job] for job in jobs}


def _keep_texts(key: tuple[str, str], stamp_: tuple[object, object], found: Mapping[str, "PostingText | None"]) -> None:
    with _TEXTS_LOCK:
        kept = _TEXTS.pop(key, None)
        merged = dict(kept[1]) if kept is not None and kept[0] == stamp_ else {}
        merged.update(found)
        _TEXTS[key] = (stamp_, merged)
        while len(_TEXTS) > TEXT_CACHE_BOARDS:
            del _TEXTS[next(iter(_TEXTS))]


def posting_texts(home_root: Path, rows: Iterable[PostingRecord]) -> dict[str, PostingText]:
    """Title, company, location, work mode, stated pay and text for ``rows``' postings, by job identity.

    Read from the company index and the board cache (no request); one index
    file and one board body per company that has a row here, and not again
    while those two files are unchanged. A posting the board no longer lists
    has its indexed title and location and no text.
    """

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex, board_list_url, cached_posting_rows
    from .find_jobs.contracts import FindJobsContractError, normalize_url
    from .find_jobs.work_mode import derive_work_mode

    home_root = Path(home_root)
    by_board: dict[str, set[str]] = {}
    for row in rows:
        by_board.setdefault(row.board, set()).add(row.job)
    index = CompanyIndex.for_home(home_root)
    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    found: dict[str, PostingText] = {}
    for board, jobs in sorted(by_board.items()):
        ats, slug = split_board(board)
        try:
            body = cache._paths(ats, board_list_url(ats, slug))[1]
            # A Greenhouse posting's text is in a detail file of its own, in the same folder: the folder's own stamp
            # moves when any file in it is written.
            files = (_stat(index.path(ats, slug)), (_stat(body), _stat(body.parent) if ats == "greenhouse" else None))
        except ValueError:
            continue  # not a board the index can name: it has no file
        key = (os.fspath(home_root), board)
        kept = _kept_texts(key, files, jobs)
        if kept is not None:
            found.update({job: text for job, text in kept.items() if text is not None})
            continue
        entry = index.read(ats, slug)
        if entry is None:
            continue
        wanted: dict[str, tuple[str, object]] = {}
        for posting_id, posting in entry.postings.items():
            try:
                # A row's job is the normalized URL: a URL that is one of them as written needs no normalizing.
                job = posting.url if posting.url in jobs else normalize_url(posting.url)
            except FindJobsContractError:
                continue
            if job in jobs:
                wanted[posting_id] = (job, posting)
        cached = cached_posting_rows(cache, ats, slug, list(wanted), allow_stale=True).rows
        read: dict[str, PostingText | None] = dict.fromkeys(jobs)
        for posting_id, (job, posting) in wanted.items():
            row = cached.get(posting_id)
            if row is None:
                mode = derive_work_mode(posting.location, None)  # type: ignore[attr-defined]
                read[job] = PostingText(posting.title, entry.company, posting.location, posting.url, None, mode.mode, None, board, posting_id, entry.company)  # type: ignore[attr-defined]
                continue
            mode = derive_work_mode(row.location, row.work_mode)
            read[job] = PostingText(row.title, row.company, row.location, row.url, row.text, mode.mode, _salary(row.pay), board, posting_id, entry.company)
        found.update({job: text for job, text in read.items() if text is not None})
        _keep_texts(key, files, read)
    return found


__all__ = [
    "BUILD_FACTS",
    "BUILD_FULL",
    "BUILD_STALE",
    "BUILD_UNCHANGED",
    "PostingModelError",
    "PostingModelPreparing",
    "PostingText",
    "ProfileView",
    "RefreshResult",
    "TagPending",
    "active_profiles",
    "board_key",
    "building_stamp",
    "model_status",
    "open_store",
    "posting_rows",
    "posting_texts",
    "profile_posting_rows",
    "rank_model_key",
    "refresh",
    "split_board",
    "stamp",
]
