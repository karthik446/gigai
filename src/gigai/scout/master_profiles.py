"""Profiles on the master resume (0.1.10.9 master P3): a profile's selection, its view, the migration.

A profile keeps its titles and search settings and gains a SELECTION of the
master (``profile_records.ProfileMasterSelection``: the ids it shows). The
selection's 2-page VIEW is an ordinary resume record, and the profile's
``resume_ref`` pins it: every existing reader of "the profile's resume"
keeps reading a resume and none of them changes. A view is one record per
profile (``view_record_id``) with a revision per change, stored through the
reference import like any resume; it is made of master lines only, so it
holds no contact data.

STICKY (the operator's decision 5). A selection changes only when

* a line it shows is edited or retired in the master: ``sync_views`` prints
  the view again from the same ids (the edited wording, the retired line
  gone) and moves the profile's pin. Nothing else is touched: no new line
  comes in, no line is re-ranked;
* the user refreshes: ``refresh_selection`` selects again from the whole
  master, with the lines the profile shows now as the prior.

Lines the master gained since the selection was made are only OFFERED
(``SelectionStatus.offer``: "3 new master lines: refresh?").

A profile whose resume was replaced by hand after its selection was made
(``resume add --profile``) is DETACHED: the selection is no longer what it
shows, ``sync_views`` leaves that resume alone, and only an explicit refresh
makes a selection again.

A NEW PROFILE's first selection (decision 7) comes from the postings its
titles match in the local index, with no model: the keywords those postings
ask for most often are a stand-in posting (``index_stand_in``), and the
selection is a job selection against it. With no matching posting it is the
standing pick for the profile's titles.

MIGRATION (``migrate``; ``gigai scout resume master init`` without
``--from``). The master is built from the resumes the profiles hold
(``master_migration.plan_migration``: merge, union, near-duplicates folded,
conflicts asked). Each profile's first selection is its own old resume: the
ids of the lines that came from it. Its ``resume_ref`` is NOT touched, so the
resume every reader reads is byte for byte the one it was, the profile keeps
its revision and content digest, and nothing stored goes stale: assessments,
rank scores and tailored resumes keep their basis. Old resume records are
never deleted.

Nothing here calls a model or makes a request, and nothing logs, prints or
returns a line of a resume in an error.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import tempfile

from ..canonical import EntityPrefix, derive_deterministic_id, digest_imported_bytes
from ..private_records import PrivateRecordError, create_record, import_reference, list_revisions, read_record
from ..workpad import ResolvedWorkpad, committed_read_cache, resolve_workpad
from . import profile_records
from .find_jobs.contracts import FindJobsContractError, PinnedResume
from .master_migration import MigrationPlan, MigrationResumeError, SourceResume, plan_migration
from .master_resume import KIND_SKILLS, Master, MasterResumeError
from .master_selection import SELECTOR_VERSION, Measure, Selected, SelectionPosting, SelectionProfile, render_selection, select
from .master_store import ACTORS, MASTER_FILE_NAME, MasterImport, MasterStoreError, StoredMaster, import_master, load_master, master_revisions, strip_contact
from .posting_keywords import extract_keywords
from .profile_records import ProfileMasterSelection, ProfileRecord
from .resume_pii import contact_findings

#: The label a view is stored under: what the Resume page names the profile's resume.
VIEW_FILE_NAME = "master-selection.md"
#: How many matching postings the stand-in posting is made from, and how far into the watchlist the index is read for them.
INDEX_SAMPLE = 40
INDEX_BOARDS_STEP = 50
INDEX_BOARDS_MAX = 600
#: A keyword is a must-have of the stand-in when this share of the sample asks for it as one; a nice-to-have at the lower share.
STAND_IN_MUST_SHARE = 0.25
STAND_IN_NICE_SHARE = 0.10
STAND_IN_TERMS = 15

_NO_MASTER = (
    "there is no master resume yet; make one from your profiles' resumes with `gigai scout resume master init`, "
    "or from a file with `gigai scout resume master init --from FILE`"
)


class MasterProfileError(ValueError):
    """A profile's selection that cannot be read or made; ``code`` is stable, the message is for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _resolve(home_root: Path, target: Path) -> ResolvedWorkpad:
    return resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)


def view_record_id(resolved: ResolvedWorkpad, profile_id: str) -> str:
    """The record a profile's selection views are revisions of: derived, so a profile has one and it is found without a search."""

    return derive_deterministic_id(
        EntityPrefix.RECORD.value,
        {"family": "scout-master-selection-view", "project_id": resolved.project_id, "gig_id": resolved.gig_id, "profile_id": profile_id},
    )


def selection_ids(selected: Selected) -> tuple[str, ...]:
    """What a ``Selected`` shows as a stored selection's ``item_ids``: every entry and line, in print order."""

    entries = tuple(item for entry_id, bullets in selected.entries.items() for item in (entry_id, *bullets))
    return (*selected.summary, *entries, *selected.other)


def _store_view(resolved: ResolvedWorkpad, home_root: Path, target: Path, profile_id: str, markdown: str) -> PinnedResume:
    """``markdown`` as the next revision of the profile's view record (the same content again writes nothing); its pin."""

    if contact_findings(markdown):
        # Master lines are contact-free at import; a view that the detector flags is never stored.
        raise MasterProfileError("selection_view_contact_data", "the selection's view would hold what looks like contact data; nothing was stored")
    encoded = markdown.encode("utf-8")
    digest = digest_imported_bytes(encoded)
    record_id = view_record_id(resolved, profile_id)
    revisions = list_revisions(resolved=resolved, record_id=record_id)
    parent = str(revisions[-1]["revision_id"]) if revisions else None
    if revisions:
        content = revisions[-1].get("content")
        snapshot = content.get("snapshot_ref") if isinstance(content, dict) else None
        if isinstance(snapshot, dict) and snapshot.get("content_sha256") == digest:
            return PinnedResume(record_id=record_id, revision_id=str(parent), content_sha256=digest)
    content_hex = digest[len("sha256:"):]
    # Resolved: the import refuses a source below a redirected (symlinked) parent, and the system temp directory is one on macOS.
    directory = Path(tempfile.mkdtemp(prefix="gigai-master-view-")).resolve()
    try:
        source = directory / VIEW_FILE_NAME
        source.write_bytes(encoded)
        reference = import_reference(
            home_root=home_root, requested_target=target, gig_id=resolved.gig_id, kind="resume", source=source,
            operation_key=f"scout-master-view:{content_hex}",
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    key = digest_imported_bytes(f"{profile_id}\n{parent or 'first'}\n{digest}".encode("utf-8"))[len("sha256:"):]
    written = create_record(
        home_root=home_root, requested_target=target, gig_id=resolved.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=reference.item_id, actor={"kind": "gigai", "id": "master-selection"},
        origin="imported", operation_key=f"scout-master-view-record:{key}", record_id=record_id, parent_revision=parent,
    )
    return PinnedResume(record_id=record_id, revision_id=written.revision_id, content_sha256=digest)


# --- status: what changed in the master for a profile -----------------------------------------


@dataclass(frozen=True)
class SelectionStatus:
    """One profile against the master as it is now."""

    profile_id: str
    label: str
    state: str
    selection: ProfileMasterSelection | None
    #: The profile's resume is the selection's view (false: replaced by hand since; ``None``: no selection).
    attached: bool | None
    current_revision: int
    #: The master revision the selection was made from, by number (``None``: no selection, or a revision the master no longer lists).
    made_from_revision: int | None
    #: Lines the master gained since the selection was made that it does not show: offered, never added by themselves.
    new_lines: tuple[str, ...] = ()
    #: Shown entries and lines the master words differently now, and those it no longer has.
    changed: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    skills_retired: tuple[str, ...] = ()

    @property
    def stale(self) -> bool:
        """The view no longer says what the master says for the lines it shows."""

        return bool(self.attached and (self.changed or self.retired or self.skills_retired))

    @property
    def offer(self) -> str | None:
        count = len(self.new_lines)
        if not self.attached or not count:
            return None
        return f"{count} new master line{'' if count == 1 else 's'}: refresh?"

    def to_json(self) -> dict[str, object]:
        selection = self.selection
        return {
            "profile_id": self.profile_id, "label": self.label, "state": self.state,
            "has_selection": selection is not None, "attached": self.attached,
            "source": selection.source if selection else None,
            "selector_version": selection.selector_version if selection else None,
            "shown": len(selection.item_ids) if selection else 0,
            "skills": len(selection.skills) if selection else 0,
            "pins": list(selection.pins) if selection else [], "excludes": list(selection.excludes) if selection else [],
            "master_revision": self.current_revision, "made_from_revision": self.made_from_revision,
            "new_lines": list(self.new_lines), "changed": list(self.changed), "retired": list(self.retired),
            "skills_retired": list(self.skills_retired), "stale": self.stale, "offer": self.offer,
        }


def _facts(master: Master) -> dict[str, object]:
    """What each id says: a line's text, an entry's heading and its lines."""

    facts: dict[str, object] = {entry.id: (entry.heading, entry.sublines) for entry in master.entries.values()}
    facts.update({item.id: item.text for item in master.items.values()})
    return facts


class _Masters:
    """The master's revisions for one command: the current one, and an older one read once when a selection names it."""

    def __init__(self, home_root: Path, target: Path) -> None:
        self._home_root, self._target = home_root, target
        stored = load_master(home_root=home_root, target=target)
        if stored is None:
            raise MasterProfileError("master_not_found", _NO_MASTER)
        self.current: StoredMaster = stored
        self._numbers = {item.revision_id: item.revision for item in master_revisions(home_root=home_root, target=target)}
        self._read: dict[str, Master | None] = {stored.revision.revision_id: stored.master}

    def number(self, revision_id: str) -> int | None:
        return self._numbers.get(revision_id)

    def at(self, revision_id: str) -> Master | None:
        if revision_id not in self._read:
            number = self._numbers.get(revision_id)
            older = load_master(home_root=self._home_root, target=self._target, revision=number) if number is not None else None
            self._read[revision_id] = older.master if older is not None else None
        return self._read[revision_id]


def _attached(home_root: Path, profile: ProfileRecord) -> bool:
    from .contact_cleanup import same_resume_revision

    selection = profile.master_selection
    return selection is not None and same_resume_revision(home_root, selection.resume_revision_id, profile.resume_ref.revision_id)


def _status(home_root: Path, masters: _Masters, profile: ProfileRecord) -> SelectionStatus:
    selection = profile.master_selection
    current = masters.current
    if selection is None:
        return SelectionStatus(profile.profile_id, profile.label, profile.state, None, None, current.revision.revision, None)
    now = _facts(current.master)
    made_from = masters.at(selection.master_revision_id)
    synced = masters.at(selection.synced_revision_id)
    shown = set(selection.item_ids)
    new_lines: tuple[str, ...] = ()
    if made_from is not None and made_from is not current.master:
        new_lines = tuple(
            item.id for item in current.master.items.values()
            if item.kind != KIND_SKILLS and item.id not in made_from.items and item.id not in shown
        )
    before = _facts(synced) if synced is not None else {}
    listed = {name.casefold() for name in current.master.skills()}
    return SelectionStatus(
        profile.profile_id, profile.label, profile.state, selection, _attached(home_root, profile),
        current.revision.revision, masters.number(selection.master_revision_id),
        new_lines=new_lines,
        changed=tuple(item_id for item_id in selection.item_ids if item_id in now and item_id in before and now[item_id] != before[item_id]),
        retired=tuple(item_id for item_id in selection.item_ids if item_id not in now),
        skills_retired=tuple(name for name in selection.skills if name.casefold() not in listed),
    )


def _profiles(resolved: ResolvedWorkpad, home_root: Path, target: Path, profile_id: str | None) -> list[ProfileRecord]:
    # Migrates the default profile on first read, like every profile-aware path.
    profile_records.selected_profile(resolved, home_root=home_root, target=target)
    profiles = [item for item in profile_records.list_profiles(resolved) if item.state != "deleted"]
    if profile_id is None:
        return profiles
    found = [item for item in profiles if item.profile_id == profile_id]
    if not found:
        raise MasterProfileError("profile_not_found", f"profile {profile_id!r} is not stored in this Scout home")
    return found


def _survey(home_root: Path, target: Path, profile_id: str | None) -> tuple[ResolvedWorkpad, _Masters, list[tuple[ProfileRecord, SelectionStatus]]]:
    """The profiles (or ``profile_id``) and where each stands against the master: one read of the journal for all of them."""

    resolved = _resolve(home_root, target)
    # Outside the read cache: resolving the profiles migrates the default one on a home that has none yet.
    profiles = _profiles(resolved, home_root, target, profile_id)
    with committed_read_cache():
        masters = _Masters(home_root, target)
        return resolved, masters, [(profile, _status(home_root, masters, profile)) for profile in profiles]


def selection_statuses(*, home_root: Path, target: Path, profile_id: str | None = None) -> list[SelectionStatus]:
    """Every profile (or ``profile_id``) against the master as it is now. Reads only."""

    return [status for _profile, status in _survey(home_root, target, profile_id)[2]]


# --- writing a selection -----------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionChange:
    """What ``sync_views`` or ``refresh_selection`` did, or would do, for one profile."""

    profile_id: str
    label: str
    #: ``first`` | ``refreshed`` | ``synced`` | ``unchanged``
    action: str
    source: str
    written: bool
    resume_ref: PinnedResume | None
    shown: int
    skills: int
    pages: int | None = None
    fits: bool | None = None
    #: Against the selection the profile had: ids that came in and ids that went.
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    #: How many matching postings of the local index the stand-in posting was made from (a refresh, a first selection).
    postings: int = 0
    record: ProfileRecord | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id, "label": self.label, "action": self.action, "source": self.source, "written": self.written,
            "resume_ref": self.resume_ref.to_json() if self.resume_ref is not None else None,
            "shown": self.shown, "skills": self.skills, "pages": self.pages, "fits": self.fits,
            "added": list(self.added), "removed": list(self.removed), "changed": list(self.changed), "retired": list(self.retired),
            "postings": self.postings,
        }


def _pages(markdown: str) -> int:
    from .master_selection import _shipped_measure  # lazy: the layout engine is a large native library

    return _shipped_measure(markdown)[0]


def _write_selection(
    resolved: ResolvedWorkpad, home_root: Path, target: Path, profile: ProfileRecord, markdown: str, selection: ProfileMasterSelection,
) -> tuple[ProfileRecord, PinnedResume]:
    """Store the view, point the profile at it with its selection, and re-open what a new resume re-opens."""

    from .pipeline import triggers as pipeline_triggers

    pin = _store_view(resolved, home_root, target, profile.profile_id, markdown)
    record = profile_records.write_profile(
        resolved, profile_id=profile.profile_id, resume_ref=pin, master_selection=replace(selection, resume_revision_id=pin.revision_id),
    )
    if pin != profile.resume_ref:
        pipeline_triggers.profile_changed(home_root, target, profile.profile_id)  # never raises
    return record, pin


def after_master_change(*, home_root: Path, target: Path, profile_id: str | None = None) -> tuple[list[SelectionChange], list[SelectionStatus]]:
    """What every write of the master ends with: ``(the views brought up to date, every profile's status)``.

    A view that shows a line the master has since edited or retired is
    printed again from the same ids, and its profile is written; an attached,
    current view and a detached profile are left alone. The statuses are
    those read before the views were written: they carry the offers for the
    lines the master gained, which a sync never changes.
    """

    resolved, masters, surveyed = _survey(home_root, target, profile_id)
    current = masters.current
    changes: list[SelectionChange] = []
    for profile, status in surveyed:
        selection = status.selection
        if selection is None or not status.stale:
            continue
        has = _facts(current.master)
        listed = {name.casefold() for name in current.master.skills()}
        kept = tuple(item_id for item_id in selection.item_ids if item_id in has)
        skills = tuple(name for name in selection.skills if name.casefold() in listed)
        markdown = render_selection(current.master, kept, skills)
        pages = _pages(markdown)
        record, pin = _write_selection(
            resolved, home_root, target, profile, markdown,
            replace(selection, item_ids=kept, skills=skills, synced_revision_id=current.revision.revision_id),
        )
        changes.append(SelectionChange(
            profile.profile_id, profile.label, "synced", selection.source, True, pin, len(kept), len(skills), pages,
            changed=status.changed, retired=(*status.retired, *(f"skill:{name}" for name in status.skills_retired)), record=record,
        ))
    return changes, [status for _profile, status in surveyed]


def sync_views(*, home_root: Path, target: Path, profile_id: str | None = None) -> list[SelectionChange]:
    """Bring every stale view up to date with the master: the same ids, printed again. One entry per profile touched."""

    return after_master_change(home_root=home_root, target=target, profile_id=profile_id)[0]


def index_stand_in(home_root: Path, target: Path, profile: ProfileRecord, master: Master) -> tuple[SelectionPosting | None, int]:
    """``(a stand-in posting, how many postings it was made from)`` for what this profile's titles match in the local index.

    Reads the index the way the search does (the profile's titles and its own
    search settings, no request), a few companies at a time in the
    watchlist's order, and stops at ``INDEX_SAMPLE`` postings with text or
    after ``INDEX_BOARDS_MAX`` companies. The keywords most of them ask for
    are the stand-in's requirements. ``(None, 0)`` where there is no index,
    no setup, or no match: every failure to read is "nothing matched".
    """

    from . import postings
    from .find_jobs.ats_board_clients import BoardCache
    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.index_search import read_indexed_boards
    from .find_jobs.title_query import TitleMatcher, open_tag_store
    from .find_jobs.watchlist import list_active

    sample: list[object] = []
    try:
        _resolved, views = postings.active_profiles(home_root, target)
        view = next((item for item in views if item.profile_id == profile.profile_id), None)
        if view is None:
            return None, 0
        boards = sorted(
            list_active(home_root, target),
            key=lambda board: (board.first_seen.query_key.startswith("catalog:"), board.provider.value, board.board_token),
        )[:INDEX_BOARDS_MAX]
        index = CompanyIndex.for_home(home_root)
        cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
        tags = open_tag_store(home_root)
        matcher = TitleMatcher(view.config.roles, tags)  # type: ignore[attr-defined]
        now = datetime.now(timezone.utc)
        for start in range(0, len(boards), INDEX_BOARDS_STEP):
            rows, _failures, _summary = read_indexed_boards(
                boards[start:start + INDEX_BOARDS_STEP], index=index, cache=cache, config=view.config, now=now,  # type: ignore[arg-type]
                remember_search=False, tags=tags, home_root=home_root, title_matcher=matcher,
            )
            sample += [row for row in rows if (row.text or "").strip()]
            if len(sample) >= INDEX_SAMPLE:
                break
    except (postings.PostingModelError, FindJobsContractError, PrivateRecordError, sqlite3.Error, OSError, ValueError, RuntimeError):
        return None, 0
    sample = sample[:INDEX_SAMPLE]
    if not sample:
        return None, 0
    vocabulary = master.skills()
    must: dict[str, int] = {}
    named: dict[str, int] = {}
    for row in sample:
        keywords = extract_keywords(row.text, title=row.title, skills=vocabulary)  # type: ignore[attr-defined]
        for term in keywords.must:
            must[term] = must.get(term, 0) + 1
        for term in (*keywords.must, *keywords.nice):
            named[term] = named.get(term, 0) + 1
    wanted = [term for term in sorted(must, key=lambda term: (-must[term], term)) if must[term] >= STAND_IN_MUST_SHARE * len(sample)][:STAND_IN_TERMS]
    plus = [
        term for term in sorted(named, key=lambda term: (-named[term], term))
        if term not in wanted and named[term] >= STAND_IN_NICE_SHARE * len(sample)
    ][:STAND_IN_TERMS]
    if not wanted and not plus:
        return None, len(sample)
    text = "Requirements:\n" + "".join(f"- {term}\n" for term in wanted) + "\nNice to have:\n" + "".join(f"- {term}\n" for term in plus)
    return SelectionPosting(title=profile.titles[0] if profile.titles else profile.label, text=text), len(sample)


def refresh_selection(
    *, home_root: Path, target: Path, profile_id: str, dry_run: bool = False, today: date | None = None, measure: Measure | None = None,
) -> SelectionChange:
    """Select again from the whole master for one profile and, unless ``dry_run``, store the view and the selection.

    The first selection of a profile that has none; a refresh of one that
    has. The posting is the stand-in from the local index (``index_stand_in``);
    the prior is the lines the profile shows now, else its titles.
    """

    resolved = _resolve(home_root, target)
    profile = _profiles(resolved, home_root, target, profile_id)[0]
    with committed_read_cache():
        current = _Masters(home_root, target).current
    before = profile.master_selection
    attached = _attached(home_root, profile)
    posting, postings_read = index_stand_in(home_root, target, profile, current.master)
    selected = select(
        current.master,
        SelectionProfile(
            titles=tuple(profile.titles), base_ids=tuple(before.item_ids) if before is not None and attached else None,
            profile_id=profile.profile_id, label=profile.label,
        ),
        posting, today=today, measure=measure,
    )
    item_ids, skills = selection_ids(selected), tuple(selected.skills)
    markdown = render_selection(current.master, item_ids, skills)
    had = set(before.item_ids) if before is not None and attached else set()
    source = "refresh" if before is not None else ("index" if posting is not None else "titles")
    action = "refreshed" if before is not None else "first"
    selection = ProfileMasterSelection(
        item_ids=item_ids, skills=skills, pins=before.pins if before else (), excludes=before.excludes if before else (),
        master_revision_id=current.revision.revision_id, synced_revision_id=current.revision.revision_id,
        resume_revision_id=profile.resume_ref.revision_id, selector_version=selected.selector_version, source=source,
    )
    change = SelectionChange(
        profile.profile_id, profile.label, action, source, False, None, len(item_ids), len(skills), selected.pages, selected.fits,
        added=tuple(item_id for item_id in item_ids if before is not None and item_id not in had),
        removed=tuple(item_id for item_id in (before.item_ids if before is not None and attached else ()) if item_id not in set(item_ids)),
        postings=postings_read,
    )
    if dry_run:
        return change
    record, pin = _write_selection(resolved, home_root, target, profile, markdown, selection)
    return replace(change, written=True, resume_ref=pin, record=record)


def first_selection(*, home_root: Path, target: Path, profile_id: str) -> ProfileRecord | None:
    """A new profile's first selection, when a master is stored; ``None`` (nothing written) when there is no master yet."""

    if load_master(home_root=home_root, target=target) is None:
        return None
    return refresh_selection(home_root=home_root, target=target, profile_id=profile_id).record


# --- the migration ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """What ``migrate`` found and did.

    ``status``: ``needs_answers`` (a conflict is not answered: nothing was
    written), ``dry_run`` (nothing was written), ``created`` / ``revised``
    (the master was written), ``unchanged`` (the master already held every
    line, or no profile was left to migrate)."""

    status: str
    plan: MigrationPlan | None
    stored: StoredMaster | None
    #: ``(the profile, its selection)`` for every profile that got its first selection (with ``dry_run``: would get).
    profiles: tuple[tuple[ProfileRecord, ProfileMasterSelection | None], ...] = ()
    written: MasterImport | None = None
    contact_removed: tuple[tuple[str, str, int], ...] = ()


def _resume_sources(
    resolved: ResolvedWorkpad, home_root: Path, target: Path, pending: list[ProfileRecord],
) -> tuple[list[SourceResume], list[tuple[str, str, int]]]:
    """The distinct resumes ``pending`` pin, newest first, and what the privacy strip took out of them (label, kind, line)."""

    by_digest: dict[str, list[ProfileRecord]] = {}
    for profile in pending:
        by_digest.setdefault(profile.resume_ref.content_sha256, []).append(profile)
    found: list[tuple[str, SourceResume]] = []
    removed: list[tuple[str, str, int]] = []
    for digest, holders in by_digest.items():
        labels = tuple(profile.label for profile in holders)
        ref = holders[0].resume_ref
        stored = read_record(
            home_root=home_root, requested_target=target, record_id=ref.record_id, revision_id=ref.revision_id, content=True, gig_id=resolved.gig_id,
        )
        data = stored.get("content")
        try:
            text = data.decode("utf-8") if isinstance(data, bytes) else None
        except UnicodeDecodeError:
            text = None
        if text is None:
            raise MasterProfileError("migration_resume_unreadable", f"the resume of profile {', '.join(labels)} cannot be read as text")
        try:
            clean, gone = strip_contact(text)
        except MasterResumeError as exc:
            # A heading that is only a link (0.1.10.11): by line number, as every resume that does not read.
            raise MasterProfileError(
                "migration_resume_unreadable",
                f"the resume of profile {', '.join(labels)} does not read as a resume ({exc}). Fix the file and add it again with "
                "`gigai scout resume add FILE --profile ID`, or store a master you wrote with `master init --from FILE`",
            ) from exc
        removed += [(labels[0], kind, line) for kind, line in gone.lines]
        # A link taken out of a heading that is kept (a resume stored before the import took them): the link was not imported.
        removed += [(labels[0], "link", line) for line, _words, _under in gone.headings]
        found.append((str(stored.get("created_at", "")), SourceResume(digest, clean, labels, tuple(sorted({line for _kind, line in gone.lines})))))
    found.sort(key=lambda item: (item[0], item[1].key), reverse=True)
    return [source for _created, source in found], removed


def migrate(
    *, home_root: Path, target: Path, answers: dict[str, str] | None = None, actor: str = "operator", revision: int | None = None,
    dry_run: bool = False,
) -> Migration:
    """Build the master from the resumes the profiles hold and give each profile its first selection: its own old resume.

    Only profiles without a selection take part; a master already stored is
    what their resumes are merged into (``revision`` names the revision that
    was read, as for every write of the master). No profile's ``resume_ref``
    is written. With an unanswered conflict nothing is written at all.
    """

    if actor not in ACTORS:
        raise MasterStoreError("master_actor_invalid", "the writer is operator or agent")
    resolved = _resolve(home_root, target)
    profiles = _profiles(resolved, home_root, target, None)
    stored = load_master(home_root=home_root, target=target)
    pending = [profile for profile in profiles if profile.master_selection is None]
    if not profiles:
        raise MasterProfileError(
            "migration_no_profiles",
            "no profile holds a resume yet: add one with `gigai scout resume add FILE`, or store a master directly with `master init --from FILE`",
        )
    if not pending:
        return Migration("unchanged", None, stored)
    sources, removed = _resume_sources(resolved, home_root, target, pending)
    try:
        plan = plan_migration(sources, base=stored.master if stored is not None else None, answers=answers)
    except MigrationResumeError as exc:
        labels = next((", ".join(source.profiles) for source in sources if source.key == exc.key), "")
        raise MasterProfileError(
            "migration_resume_unreadable",
            f"the resume of profile {labels} does not read as a resume ({exc}). Fix the file and add it again with "
            "`gigai scout resume add FILE --profile ID`, or store a master you wrote with `master init --from FILE`",
        ) from exc

    def selection_of(profile: ProfileRecord, revision_id: str) -> ProfileMasterSelection:
        made = plan.selection(profile.resume_ref.content_sha256)
        return ProfileMasterSelection(
            item_ids=made.item_ids, skills=made.skills, master_revision_id=revision_id, synced_revision_id=revision_id,
            resume_revision_id=profile.resume_ref.revision_id, selector_version=SELECTOR_VERSION, source="migration",
        )

    if plan.unanswered or dry_run:
        return Migration(
            "needs_answers" if plan.unanswered else "dry_run", plan, stored, tuple((profile, None) for profile in pending),
            contact_removed=tuple(removed),
        )
    # Resolved: see _store_view.
    directory = Path(tempfile.mkdtemp(prefix="gigai-master-migration-")).resolve()
    try:
        source = directory / MASTER_FILE_NAME
        source.write_text(plan.master.markdown(), encoding="utf-8")
        written = import_master(home_root=home_root, target=target, source=source, actor=actor, revision=revision)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    revision_id = written.stored.revision.revision_id
    migrated = []
    for profile in pending:
        selection = selection_of(profile, revision_id)
        # ``resume_ref`` is not passed: the profile keeps its resume, its revision and its content digest.
        migrated.append((profile_records.write_profile(resolved, profile_id=profile.profile_id, master_selection=selection), selection))
    return Migration(written.status, plan, written.stored, tuple(migrated), written, tuple(removed))


__all__ = [
    "INDEX_SAMPLE",
    "MasterProfileError",
    "Migration",
    "SelectionChange",
    "SelectionStatus",
    "VIEW_FILE_NAME",
    "after_master_change",
    "first_selection",
    "index_stand_in",
    "migrate",
    "refresh_selection",
    "selection_ids",
    "selection_statuses",
    "sync_views",
    "view_record_id",
]
