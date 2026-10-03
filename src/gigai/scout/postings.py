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
(1 is the best tag: the highest rank score, then the profile that has an
assessment, then the default profile). Application events are not cached:
they are journal records, read when a response is built.

MATCHING is ``index_search.read_indexed_boards`` with each active profile's
own effective config (its titles, and its own location, work mode, countries
and posted window when it has them): the same rule a search uses.

INVALIDATION, per profile, by two digests kept in ``posting_build``:

- ``match_digest``: the profile's settings digest, the index files' stamps
  (name, mtime, size), the title-tag store's, the watched boards and the UTC
  day (the posted window moves with it). When it differs, the profile is
  matched again from the index, so a posting whose content changed gets its
  new ``listing_digest``.
- ``facts_digest``: the profile's resume digest, the rank model, and the
  stamps of the stores a row's facts come from (assessments, tailored
  resumes, Scout labels, the rank score cache, and what a stale check reads).
  When only it differs, the stored rows get their facts again; the index is
  not read.

A read that finds both digests unchanged reads the table as it is.

No subprocess is started per posting: the journal is read a fixed number of
times per refresh (the profiles, the watched boards, each profile's resume),
inside one ``committed_read_cache`` so the workpad is checked once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from urllib.parse import quote, unquote

from ..canonical import digest_imported_bytes
from .pipeline.store import PipelineStore, PostingBuild, PostingRecord, RunAssessment, pipeline_path

#: Bump when what a row is matched by, or what its facts are read from, changes.
MATCH_VERSION = "posting-match:1"
FACTS_VERSION = "posting-facts:1"

STATE_ACTIVE = "active"
BUILD_FULL = "matched"
BUILD_FACTS = "facts"
BUILD_UNCHANGED = "unchanged"


class PostingModelError(ValueError):
    """The read model cannot be built for this folder; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def _index_stamp(home_root: Path) -> str:
    """The company index and title-tag store as they are on disk: names, mtimes, sizes. No file is opened."""

    root = home_root / "cache" / "scout" / "companies"
    files: list[tuple[str, int, int]] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and ":" in entry.name:
                    found = entry.stat()
                    files.append((entry.name, found.st_mtime_ns, found.st_size))
    except OSError:
        pass
    files.sort()
    tags = home_root / "cache" / "scout" / "tags.sqlite"
    return _digest("index", files, _stat(tags), _stat(Path(f"{tags}-wal")))


def _scout_root(home_root: Path, target: Path) -> Path:
    return pipeline_path(home_root, target).parent.parent


def _facts_stamp(home_root: Path, target: Path, view: ProfileView, rank_model: str | None) -> str:
    from .assessment_basis import _watched_files
    from .find_jobs.model_rank import PROMPT_VERSION, cache_dir
    from .find_jobs.rank_digest import DIGEST_VERSION

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
        _stat(cache_dir(home_root)),
    )


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

        self.home_root, self.target, self.view = home_root, target, view
        #: The newest imported run assessment per job for this profile (0.1.10.7 M4a): used when nothing newer is stored.
        self._run_latest = run_latest or {}
        root = _scout_root(home_root, target)
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

        from .find_jobs.job_state import NOT_ASSESSED, TAILORED, _identity_digest, derive_job_state, quick_assessment_fact
        from .pipeline.steps import read_label

        profile_id = self.view.profile_id
        key = _identity_digest(row.job)
        item = self._sources.quick_assessment(row.job, profile_id) if key in self._assessed else None
        tailored = key in self._tailored and self._sources.tailored_at(row.job, profile_id)[0]
        state, stale, assessed_at, met, requirements, questions = NOT_ASSESSED, None, None, None, None, 0
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
        ran = self._run_latest.get(row.job)
        if ran is not None and (assessed_at is None or (ran.assessed_at or "") > assessed_at):
            # An old run's assessment, newer than anything in the quick store (DESIGN 10.4: latest wins).
            state, stale = ran.state, self._run_stale(ran, row)
            assessed_at, met, requirements, questions = ran.assessed_at, ran.reqs_met, ran.reqs_total, ran.open_questions
        if tailored:
            state = TAILORED
        label, ats_score = None, None
        if key in self._labelled:
            record = read_label(self.home_root, self.target, profile_id, row.job)
            if record is not None:
                value, score = record.get("label"), record.get("ats_score")
                label = value if isinstance(value, str) else None
                ats_score = score if type(score) is int and score >= 0 else None
        return replace(
            row, rank_score=self.rank_score(row.listing_digest), state=state, stale_code=stale, assessed_at=assessed_at,
            reqs_met=met, reqs_total=requirements, open_questions=questions, tailored=bool(tailored), label=label,
            ats_score=ats_score, pinned_digest=self.view.resume_digest, settings_digest=self.view.settings_digest,
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


def _matched_rows(
    view: ProfileView, boards: Sequence[object], index: _IndexOnce, home_root: Path, now: datetime, built_at: str
) -> list[PostingRecord]:
    """The profile's live matches as rows without facts: ``index_search``'s own rule, no board request."""

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.index_search import read_indexed_boards
    from .find_jobs.model_rank import content_digest
    from .find_jobs.title_query import open_tag_store
    from .pipeline.store import fits

    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    rows, _failures, _summary = read_indexed_boards(
        boards, index=index, cache=cache, config=view.config, now=now, remember_search=False,  # type: ignore[arg-type]
        tags=open_tag_store(home_root), home_root=home_root,
    )
    by_url = index.by_url()
    found: dict[str, PostingRecord] = {}
    for row in rows:
        indexed = by_url.get(row.normalized_url)
        if indexed is None:
            continue
        board, posting = indexed
        first_seen = stamp(posting.first_seen)  # type: ignore[attr-defined]
        if first_seen is None or not fits("job", row.normalized_url) or not fits("board", board):
            continue  # not a shape the file holds: the posting is left out rather than stored as text
        found[row.normalized_url] = PostingRecord(
            job=row.normalized_url, profile_id=view.profile_id, board=board, first_seen=first_seen,
            published_at=stamp(row.published_at), removed_at=None, listing_digest=content_digest(row),
            listing_known=bool(row.content_sha256), rank_score=None, match_rank=1, state="not_assessed", stale_code=None,
            assessed_at=None, reqs_met=None, reqs_total=None, open_questions=0, tailored=False, label=None, ats_score=None,
            pinned_digest=view.resume_digest, settings_digest=view.settings_digest, updated_at=built_at,
        )
    return list(found.values())


def profile_posting_rows(view: ProfileView, home_root: Path, target: Path, now: datetime) -> list[object]:
    """The profile's live matches as the index's own ``PostingRow``s (what the rank lane ranks): no board request."""

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


def _removed_rows(previous: Iterable[PostingRecord], matched: set[str], index: _IndexOnce, built_at: str) -> list[PostingRecord]:
    """Rows the profile had whose posting the board no longer lists: kept, with ``removed_at``."""

    by_url = index.by_url()
    kept = []
    for row in previous:
        if row.job in matched:
            continue
        indexed = by_url.get(row.job)
        removed_at = stamp(getattr(indexed[1], "removed_at", None)) if indexed is not None else None
        if removed_at is not None:
            kept.append(replace(row, removed_at=removed_at, updated_at=built_at))
    return kept


# --- refresh --------------------------------------------------------------------------------


def _best_tag_order(rows: Iterable[tuple[str, str, int | None, str, int]], default_id: str | None) -> list[tuple[int, str, str]]:
    """``(match_rank, job, profile_id)`` for the rows whose place among their posting's profiles changed."""

    by_job: dict[str, list[tuple[str, int | None, str, int]]] = {}
    for job, profile_id, rank_score, state, match_rank in rows:
        by_job.setdefault(job, []).append((profile_id, rank_score, state, match_rank))
    changed = []
    for job, group in by_job.items():
        group.sort(key=lambda item: (-(item[1] if item[1] is not None else -1), item[2] == "not_assessed", item[0] != default_id, item[0]))
        for place, (profile_id, _score, _state, current) in enumerate(group, start=1):
            if place != current:
                changed.append((place, job, profile_id))
    return changed


def open_store(home_root: Path, target: Path) -> PipelineStore:
    return PipelineStore(pipeline_path(Path(home_root), Path(target)))


def refresh(
    home_root: Path,
    target: Path,
    *,
    store: PipelineStore | None = None,
    now: datetime | None = None,
    force: bool = False,
    resolved: object | None = None,
) -> RefreshResult:
    """Bring the read model in step with the index, the profiles and the stores; rebuilds only what changed.

    ``force`` matches every active profile again whatever the digests say
    (the full rebuild). Raises :class:`PostingModelError` when the folder has
    no Scout gig or no readable config.
    """

    from ..workpad import committed_read_cache

    # One workpad check and one committed read per journal family for the whole refresh (0110-044), not one each.
    with committed_read_cache():
        return _refresh(Path(home_root), Path(target), store=store, now=now, force=force, resolved=resolved)


def _refresh(
    home_root: Path, target: Path, *, store: PipelineStore | None, now: datetime | None, force: bool, resolved: object | None
) -> RefreshResult:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    built_at = stamp(moment)
    assert built_at is not None
    resolved, views = active_profiles(home_root, target, resolved)
    opened = store if store is not None else open_store(home_root, target)
    try:
        builds = opened.posting_builds()
        dropped = opened.keep_posting_profiles(view.profile_id for view in views) if set(builds) - {view.profile_id for view in views} else 0
        index_stamp = _index_stamp(home_root)
        rank_model = rank_model_key(home_root, target)
        boards = _watched(home_root, target)
        boards_digest = _boards_digest(boards)
        day = moment.date().isoformat()
        index: _IndexOnce | None = None
        done: dict[str, str] = {}
        # What old runs assessed (0.1.10.7 M4a): a build made before an import gets its facts again.
        history = opened.run_history_stamp()
        for view in views:
            previous = builds.get(view.profile_id)
            facts_digest = _facts_stamp(home_root, target, view, rank_model)
            if history[0]:
                facts_digest = _digest(facts_digest, "run-history", list(history))
            match_digest = _digest(MATCH_VERSION, view.settings_digest, index_stamp, boards_digest, day)
            if force or previous is None or previous.match_digest != match_digest:
                if index is None:
                    from .find_jobs.company_index import CompanyIndex

                    index = _IndexOnce(CompanyIndex.for_home(home_root))
                rows = _matched_rows(view, boards, index, home_root, moment, built_at)
                rows += _removed_rows(opened.postings(profile_id=view.profile_id, live=False), {row.job for row in rows}, index, built_at)
                kind = BUILD_FULL
            elif previous is not None and previous.facts_digest != facts_digest:
                rows = [replace(row, updated_at=built_at) for row in opened.postings(profile_id=view.profile_id, live=False)]
                kind = BUILD_FACTS
            else:
                done[view.profile_id] = BUILD_UNCHANGED
                continue
            run_latest = (
                {item.job: item for item in opened.run_assessments(profile_id=view.profile_id, latest=True)} if history[0] else {}
            )
            facts = _Facts(home_root, target, resolved, view, rank_model, run_latest)
            rows = [facts.of(row) for row in rows]
            opened.replace_postings(
                PostingBuild(view.profile_id, match_digest, facts_digest, view.resume_digest, view.settings_digest, len(rows), built_at),
                rows,
            )
            done[view.profile_id] = kind
        if dropped or any(kind != BUILD_UNCHANGED for kind in done.values()):
            default_id = next((view.profile_id for view in views if view.is_default), None)
            opened.set_match_ranks(_best_tag_order(opened.posting_rank_inputs(), default_id))
        return RefreshResult(views, done, opened.posting_count(), dropped, resolved)
    finally:
        if store is None:
            opened.close()


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


def posting_texts(home_root: Path, rows: Iterable[PostingRecord]) -> dict[str, PostingText]:
    """Title, company, location, work mode, stated pay and text for ``rows``' postings, by job identity.

    Read from the company index and the board cache (no request); one index
    file and one board body per company that has a row here. A posting the
    board no longer lists has its indexed title and location and no text.
    """

    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex, cached_posting_rows
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
        entry = index.read(ats, slug)
        if entry is None:
            continue
        wanted: dict[str, tuple[str, object]] = {}
        for posting_id, posting in entry.postings.items():
            try:
                job = normalize_url(posting.url)
            except FindJobsContractError:
                continue
            if job in jobs:
                wanted[posting_id] = (job, posting)
        cached = cached_posting_rows(cache, ats, slug, list(wanted), allow_stale=True).rows
        for posting_id, (job, posting) in wanted.items():
            row = cached.get(posting_id)
            if row is None:
                mode = derive_work_mode(posting.location, None)  # type: ignore[attr-defined]
                found[job] = PostingText(posting.title, entry.company, posting.location, posting.url, None, mode.mode, None)  # type: ignore[attr-defined]
                continue
            mode = derive_work_mode(row.location, row.work_mode)
            found[job] = PostingText(row.title, row.company, row.location, row.url, row.text, mode.mode, _salary(row.pay))
    return found


__all__ = [
    "BUILD_FACTS",
    "BUILD_FULL",
    "BUILD_UNCHANGED",
    "PostingModelError",
    "PostingText",
    "ProfileView",
    "RefreshResult",
    "active_profiles",
    "board_key",
    "open_store",
    "posting_texts",
    "profile_posting_rows",
    "rank_model_key",
    "refresh",
    "split_board",
    "stamp",
]
