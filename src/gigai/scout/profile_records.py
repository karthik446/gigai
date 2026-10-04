"""Journal-backed Scout interested profiles (S25 F1-a).

An interested profile = a resume + job titles/queries pair
(`scout-profile:1`). **Storage layout amendment (this revision, decided by
the coordinator after the original "flat file, overwritten in place" plan
from the S25 spike's Q1 was found to violate a real journal invariant --
see the module docstring's "Storage layout amendment" note below the class
definitions for the full writeup):**

Every profile WRITE is its own write-once file, never an overwrite:
``records/scout-profiles/<profile_id>/writes/<seq>.json`` (``seq`` a
zero-padded, per-profile monotonic write counter -- a label rename is a new
write file too, at a NEW seq, even though it doesn't bump the semantic
``revision``). The *current* profile is the file at the highest seq. The
selected-profile pointer is append-only the same way:
``records/scout-profile-selection/<seq>.json``; current = highest seq.

This keeps ``records/`` write-once (core's `journal._capture_committed_
snapshot` requires "exactly one publisher" for any path it snapshots, with
only two hardcoded exemptions -- ``runs/.../run-details.json`` and
``manifests/capabilities/capmanifest_*.json`` -- and profiles get no third
exemption; core must not learn a Scout-specific path shape). It also
matches this codebase's own existing convention for a record that changes
over time: ``private_records.create_record``'s
``records/<record_id>/revisions/<revision_id>.json`` write-once chain,
one level simpler (a monotonic integer seq instead of a UUID
revision-id + explicit parent-link chain, since a profile's write history
never branches -- there is exactly one writer sequence per profile, never a
merge of concurrent edits).

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (r2 + r2b) for the record's semantic fields, revision-bump
rule, content_digest, and migration/selection design. Approvals:
``orchestrator/reviews/S25-orchestrator-review.md``. The storage LAYOUT
(seq-file append-only shape) supersedes that doc's Q1 "flat file" plan;
the semantic content of a profile record (and the two journal transition
names) are unchanged.

ARCHITECTURE: this module is the ONLY place that invokes the migration
(``ensure_default_profile``/``selected_profile``). Core (`gigai.index`,
`gigai.run` read paths) never imports `gigai.scout` and must not call these
functions directly.

NO READER WIRING HERE (F1-b's job): nothing in acquire/assess/present/
discovery/prep/CLI/API reads profiles yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, NoReturn
import re
import uuid

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..journal import (
    JournalArtifact,
    JournalSnapshot,
    JournalTransition,
    read_committed_snapshot,
    run_with_journal_writer,
)
from ..validators import validate_serialized_contract
from ..workpad import ResolvedWorkpad
from .. import private_records
from .find_jobs.contracts import (
    MAX_AGE_DAYS_MAXIMUM,
    FindJobsConfig,
    PinnedResume,
    WorkModePreference,
    is_location_placeholder,
)

SCHEMA_VERSION = "scout-profile:1"
SELECTION_SCHEMA_VERSION = "scout-profile-selection:1"
PROFILE_TRANSITION = "scout_profile_revised"
SELECTION_TRANSITION = "scout_profile_selected"

_PROFILE_ID = re.compile(
    r"^profile_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROFILES_ROOT = "records/scout-profiles/"
_SELECTION_ROOT = "records/scout-profile-selection/"
_WRITE_PATH = re.compile(
    r"^records/scout-profiles/(?P<profile_id>profile_[0-9a-f-]{36})/writes/(?P<seq>[0-9]{12})\.json$"
)
_SELECTION_PATH = re.compile(r"^records/scout-profile-selection/(?P<seq>[0-9]{12})\.json$")


class ProfileRecordError(ValueError):
    """Redacted refusal from the journal-backed profile service."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NoResumeAvailable(Exception):
    """Raised (caught internally) when the resolved gig has no committed resume yet."""


_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")
_LOCATION_MAX = 200
SEARCH_SETTINGS_KEYS = ("countries", "location", "max_age_days", "work_mode")


@dataclass(frozen=True)
class ProfileSearchSettings:
    """0110-022: one profile's OWN search settings (location, work mode, countries, posted window).

    Stored only on a profile that is not the default one (``default_profile``);
    a record without them reads as "same as default": the shared
    ``find-jobs.json`` the setup wrote. ``location`` is the search area (the
    candidate location the rank and assess prompts read), never the Resume
    display contact line. ``max_age_days`` ``None`` keeps the default's
    posted window (its ``max_age_days`` or fixed ``published_after``).
    """

    location: str | None
    work_mode: str
    countries: tuple[str, ...]
    max_age_days: int | None

    def to_json(self) -> dict[str, object]:
        return {
            "location": self.location,
            "work_mode": self.work_mode,
            "countries": list(self.countries),
            "max_age_days": self.max_age_days,
        }

    @classmethod
    def from_json(cls, value: object) -> "ProfileSearchSettings":
        if not isinstance(value, Mapping) or set(value) != set(SEARCH_SETTINGS_KEYS):
            raise ProfileRecordError("scout_profile_invalid", "profile search_settings is malformed")
        location = value["location"]
        work_mode = value["work_mode"]
        countries = value["countries"]
        max_age_days = value["max_age_days"]
        if location is not None and (
            not isinstance(location, str)
            or not location.strip()
            or len(location) > _LOCATION_MAX
            or is_location_placeholder(location)
        ):
            raise ProfileRecordError("scout_profile_invalid", "search_settings.location must be a city or area, or null")
        if not isinstance(work_mode, str) or work_mode not in {item.value for item in WorkModePreference}:
            raise ProfileRecordError("scout_profile_invalid", "search_settings.work_mode must be remote, hybrid, onsite or any")
        if not isinstance(countries, (list, tuple)) or not all(
            isinstance(code, str) and _COUNTRY_CODE.fullmatch(code) for code in countries
        ):
            raise ProfileRecordError("scout_profile_invalid", "search_settings.countries must be ISO-3166 alpha-2 codes")
        if max_age_days is not None and (
            not isinstance(max_age_days, int)
            or isinstance(max_age_days, bool)
            or not (1 <= max_age_days <= MAX_AGE_DAYS_MAXIMUM)
        ):
            raise ProfileRecordError(
                "scout_profile_invalid", f"search_settings.max_age_days must be an integer from 1 to {MAX_AGE_DAYS_MAXIMUM}, or null"
            )
        return cls(
            location=None if location is None else location.strip(),
            work_mode=work_mode,
            countries=tuple(countries),
            max_age_days=max_age_days,
        )

    @classmethod
    def from_config(cls, config: FindJobsConfig) -> "ProfileSearchSettings":
        """The default's settings, as a new profile is prefilled with them."""

        return cls(
            location=config.location,
            work_mode=config.effective_work_mode.value,
            countries=tuple(config.countries),
            max_age_days=config.max_age_days,
        )


MASTER_SELECTION_KEYS = (
    "excludes", "item_ids", "master_revision_id", "pins", "resume_revision_id", "selector_version", "skills", "source", "synced_revision_id",
)
MASTER_SELECTION_SOURCES = ("migration", "index", "titles", "refresh")


@dataclass(frozen=True)
class ProfileMasterSelection:
    """0.1.10.9 master P3: which lines of the master resume this profile's resume shows.

    ``resume_ref`` stays what every reader reads: it pins the selection's
    VIEW, a resume like any other. This key says what that view is made of.

    * ``item_ids``: every entry and line of the master the view shows, in
      the order it prints them; ``skills``: the skills it lists, by name.
    * ``pins`` / ``excludes``: lines a later selection always or never
      shows. Stored and carried through every write; nothing sets them yet.
    * ``master_revision_id``: the master revision the selection was MADE
      from (the migration, the first selection or a refresh). Lines the
      master gained after it are the ones offered ("3 new lines: refresh?").
    * ``synced_revision_id``: the master revision the view's text was last
      brought up to date with (an edited or retired shown line moves it).
    * ``resume_revision_id``: the resume revision the selection describes.
      A profile whose ``resume_ref`` was since replaced by hand no longer
      shows this selection, and nothing rewrites that resume.
    * ``source``: ``migration`` (the profile's own old resume, kept as it
      was), ``index`` (the postings its titles match in the local index),
      ``titles`` (its titles alone) or ``refresh``.

    Not one of the revision-bumping fields: what a selection changes for a
    reader is the resume it pins, and ``resume_ref`` is. A record without
    the key reads as before and keeps its bytes and ``content_digest``.
    """

    item_ids: tuple[str, ...]
    skills: tuple[str, ...]
    master_revision_id: str
    synced_revision_id: str
    resume_revision_id: str
    selector_version: str
    source: str
    pins: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "item_ids": list(self.item_ids),
            "skills": list(self.skills),
            "pins": list(self.pins),
            "excludes": list(self.excludes),
            "master_revision_id": self.master_revision_id,
            "synced_revision_id": self.synced_revision_id,
            "resume_revision_id": self.resume_revision_id,
            "selector_version": self.selector_version,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, value: object) -> "ProfileMasterSelection":
        if not isinstance(value, Mapping) or set(value) != set(MASTER_SELECTION_KEYS):
            raise ProfileRecordError("scout_profile_invalid", "profile master_selection is malformed")
        lists = {key: value[key] for key in ("item_ids", "skills", "pins", "excludes")}
        texts = {key: value[key] for key in ("master_revision_id", "synced_revision_id", "resume_revision_id", "selector_version", "source")}
        if not all(isinstance(items, (list, tuple)) and all(isinstance(item, str) and item for item in items) for items in lists.values()):
            raise ProfileRecordError("scout_profile_invalid", "master_selection lists must hold non-empty strings")
        if not all(isinstance(text, str) and text for text in texts.values()) or texts["source"] not in MASTER_SELECTION_SOURCES:
            raise ProfileRecordError("scout_profile_invalid", "profile master_selection is malformed")
        return cls(
            item_ids=tuple(lists["item_ids"]),
            skills=tuple(lists["skills"]),
            pins=tuple(lists["pins"]),
            excludes=tuple(lists["excludes"]),
            master_revision_id=texts["master_revision_id"],
            synced_revision_id=texts["synced_revision_id"],
            resume_revision_id=texts["resume_revision_id"],
            selector_version=texts["selector_version"],
            source=texts["source"],
        )


@dataclass(frozen=True)
class ProfileRecord:
    schema_version: str
    profile_id: str
    seq: int
    revision: int
    label: str
    state: str
    origin: str
    resume_ref: PinnedResume
    titles: tuple[str, ...]
    titles_to_avoid: tuple[str, ...]
    queries: tuple[str, ...]
    content_digest: str
    created_at: str
    updated_at: str
    parent_seq: int | None
    # 0110-022: additive and optional. ``None`` (every record written before
    # it, and the default profile always) is "same as default"; the key is
    # then left out of the written file, so such a record's bytes and
    # ``content_digest`` are exactly what they were.
    search_settings: ProfileSearchSettings | None = None
    # 0.1.10.9 master P3: additive and optional, like ``search_settings``.
    # ``None`` (every record written before it) is "this profile owns its
    # resume, as before"; the key is then left out of the written file.
    master_selection: ProfileMasterSelection | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "seq": self.seq,
            "revision": self.revision,
            "label": self.label,
            "state": self.state,
            "origin": self.origin,
            "resume_ref": self.resume_ref.to_json(),
            "titles": list(self.titles),
            "titles_to_avoid": list(self.titles_to_avoid),
            "queries": list(self.queries),
            "content_digest": self.content_digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "parent_seq": self.parent_seq,
        }
        if self.search_settings is not None:
            value["search_settings"] = self.search_settings.to_json()
        if self.master_selection is not None:
            value["master_selection"] = self.master_selection.to_json()
        return value

    @classmethod
    def from_json(cls, value: object) -> "ProfileRecord":
        if not isinstance(value, Mapping):
            raise ProfileRecordError("scout_profile_invalid", "profile record must be an object")
        try:
            resume_ref = PinnedResume.from_json(value["resume_ref"])
            parent_seq = value["parent_seq"]
            search_settings = value.get("search_settings")
            master_selection = value.get("master_selection")
            return cls(
                schema_version=str(value["schema_version"]),
                profile_id=str(value["profile_id"]),
                seq=int(value["seq"]),
                revision=int(value["revision"]),
                label=str(value["label"]),
                state=str(value["state"]),
                origin=str(value["origin"]),
                resume_ref=resume_ref,
                titles=tuple(str(item) for item in value["titles"]),
                titles_to_avoid=tuple(str(item) for item in value["titles_to_avoid"]),
                queries=tuple(str(item) for item in value["queries"]),
                content_digest=str(value["content_digest"]),
                created_at=str(value["created_at"]),
                updated_at=str(value["updated_at"]),
                parent_seq=None if parent_seq is None else int(parent_seq),
                search_settings=None if search_settings is None else ProfileSearchSettings.from_json(search_settings),
                master_selection=None if master_selection is None else ProfileMasterSelection.from_json(master_selection),
            )
        except (KeyError, TypeError) as exc:
            raise ProfileRecordError("scout_profile_invalid", "profile record is malformed") from exc


@dataclass(frozen=True)
class ProfileSelection:
    schema_version: str
    seq: int
    selected_profile_id: str
    updated_at: str
    parent_seq: int | None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "selected_profile_id": self.selected_profile_id,
            "updated_at": self.updated_at,
            "parent_seq": self.parent_seq,
        }

    @classmethod
    def from_json(cls, value: object) -> "ProfileSelection":
        if not isinstance(value, Mapping):
            raise ProfileRecordError("scout_profile_selection_invalid", "selection record must be an object")
        try:
            parent_seq = value["parent_seq"]
            return cls(
                schema_version=str(value["schema_version"]),
                seq=int(value["seq"]),
                selected_profile_id=str(value["selected_profile_id"]),
                updated_at=str(value["updated_at"]),
                parent_seq=None if parent_seq is None else int(parent_seq),
            )
        except (KeyError, TypeError) as exc:
            raise ProfileRecordError("scout_profile_selection_invalid", "selection record is malformed") from exc


@dataclass(frozen=True)
class MigrationResult:
    profile_id: str
    revision: int
    created: bool  # False on an idempotent re-run


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fail(code: str, message: str) -> NoReturn:
    raise ProfileRecordError(code, message)


def _write_path(profile_id: str, seq: int) -> str:
    return f"{_PROFILES_ROOT}{profile_id}/writes/{seq:012d}.json"


def _selection_path(seq: int) -> str:
    return f"{_SELECTION_ROOT}{seq:012d}.json"


def _content_digest(
    *,
    titles: tuple[str, ...],
    titles_to_avoid: tuple[str, ...],
    queries: tuple[str, ...],
    resume_ref: PinnedResume,
    search_settings: ProfileSearchSettings | None = None,
) -> str:
    """The ONE place a profile's ``content_digest`` is computed.

    Recomputed unconditionally on every write, over exactly the four
    revision-bumping fields (canonicalized), never accepted from a caller --
    a hand-edited or partially-written file can never carry a stale digest
    because nothing ever trusts one that wasn't just recomputed here (open
    question 9's recommended default, matching ``FindJobsConfig.digest()``'s
    own "computed, not accepted" convention).

    0110-022: a profile's own ``search_settings`` are a fifth
    revision-bumping field, in the payload ONLY when the profile has them,
    so a record without them digests exactly as before.
    """

    payload: dict[str, object] = {
        "titles": list(titles),
        "titles_to_avoid": list(titles_to_avoid),
        "queries": list(queries),
        "resume_ref": resume_ref.to_json(),
    }
    if search_settings is not None:
        payload["search_settings"] = search_settings.to_json()
    return digest_imported_bytes(canonical_json_bytes(payload))


def _validated(record: ProfileRecord) -> bytes:
    encoded = canonical_json_bytes(record.to_json())
    report = validate_serialized_contract("scout-profile.schema.json", encoded)
    if not report.valid:
        _fail("scout_profile_invalid", "profile record failed its strict schema")
    return encoded


def _validated_selection(selection: ProfileSelection) -> bytes:
    encoded = canonical_json_bytes(selection.to_json())
    report = validate_serialized_contract("scout-profile-selection.schema.json", encoded)
    if not report.valid:
        _fail("scout_profile_selection_invalid", "selection record failed its strict schema")
    return encoded


def _all_profile_writes(artifacts: Mapping[str, bytes]) -> dict[str, list[ProfileRecord]]:
    """Every committed write file, grouped by ``profile_id``, seq-ascending.

    Fails closed (never silently falls back to an older write) on: a
    partially written or unparseable file (``scout_profile_write_corrupt``),
    a schema-invalid file (``scout_profile_write_schema_invalid``), a write
    whose ``profile_id``/``seq`` fields disagree with its own path
    (``scout_profile_write_identity_mismatch``), a duplicate seq for the
    same profile (``scout_profile_write_duplicate_seq``), or a seq gap in a
    profile's write sequence (``scout_profile_write_seq_gap``).
    """

    by_profile: dict[str, list[ProfileRecord]] = {}
    for path, raw in artifacts.items():
        match = _WRITE_PATH.fullmatch(path)
        if match is None:
            continue
        try:
            value = parse_json_bytes(raw)
        except ValueError as exc:
            raise ProfileRecordError("scout_profile_write_corrupt", "committed profile write is not JSON") from exc
        if not validate_serialized_contract("scout-profile.schema.json", raw).valid:
            _fail("scout_profile_write_schema_invalid", "committed profile write failed its strict schema")
        record = ProfileRecord.from_json(value)
        if record.profile_id != match.group("profile_id") or record.seq != int(match.group("seq")):
            _fail("scout_profile_write_identity_mismatch", "profile write identity differs from its path")
        by_profile.setdefault(record.profile_id, []).append(record)
    for profile_id, records in by_profile.items():
        records.sort(key=lambda item: item.seq)
        _validate_write_sequence(
            [item.seq for item in records],
            duplicate_code="scout_profile_write_duplicate_seq",
            gap_code="scout_profile_write_seq_gap",
            subject=f"profile {profile_id}",
        )
    return by_profile


def _validate_write_sequence(seqs: list[int], *, duplicate_code: str, gap_code: str, subject: str) -> None:
    """Fail closed on a duplicate or missing seq in an ascending write chain.

    Pulled out of ``_all_profile_writes``/``_all_selection_writes`` so the
    pure seq-arithmetic check (independent of path parsing/schema
    validation) is directly unit-testable on its own -- a real committed
    pair of files can only produce a genuine duplicate seq through a caller
    bug in ``next_seq`` computation (each file's OWN path already ties its
    seq to a unique filename), so this is exercised directly rather than by
    contriving two colliding real commits.
    """

    if len(seqs) != len(set(seqs)):
        _fail(duplicate_code, f"{subject} has a duplicate write seq")
    if seqs != list(range(1, len(seqs) + 1)):
        _fail(gap_code, f"{subject} has a gap in its write sequence")


def _current_profiles(artifacts: Mapping[str, bytes]) -> dict[str, ProfileRecord]:
    """The current (highest-seq) write for every profile_id."""

    return {profile_id: writes[-1] for profile_id, writes in _all_profile_writes(artifacts).items()}


def _existing_default_profile(current: Mapping[str, ProfileRecord]) -> ProfileRecord | None:
    for record in current.values():
        if record.origin == "migrated_default":
            return record
    return None


def default_profile(profiles: Iterable[ProfileRecord]) -> ProfileRecord | None:
    """0110-022: the profile that uses the shared setup settings (``find-jobs.json``).

    The migrated default when there is one, else the oldest profile ("the
    first time the user creates it, it's the default profile"); archived or
    not. Every other profile may store its own ``search_settings``.
    """

    ordered = sorted(profiles, key=lambda item: (item.created_at, item.profile_id))
    for record in ordered:
        if record.origin == "migrated_default":
            return record
    return ordered[0] if ordered else None


def _all_selection_writes(artifacts: Mapping[str, bytes]) -> list[ProfileSelection]:
    """Every committed selection write, seq-ascending.

    Fails closed the same way ``_all_profile_writes`` does: corrupt/
    unparseable (``scout_profile_selection_write_corrupt``), schema-invalid
    (``scout_profile_selection_write_schema_invalid``), a seq that disagrees
    with its own path (``scout_profile_selection_write_identity_mismatch``),
    a duplicate seq (``scout_profile_selection_write_duplicate_seq``), or a
    seq gap (``scout_profile_selection_write_seq_gap``).
    """

    writes: list[ProfileSelection] = []
    for path, raw in artifacts.items():
        match = _SELECTION_PATH.fullmatch(path)
        if match is None:
            continue
        try:
            value = parse_json_bytes(raw)
        except ValueError as exc:
            raise ProfileRecordError("scout_profile_selection_write_corrupt", "committed selection write is not JSON") from exc
        if not validate_serialized_contract("scout-profile-selection.schema.json", raw).valid:
            _fail("scout_profile_selection_write_schema_invalid", "committed selection write failed its strict schema")
        selection = ProfileSelection.from_json(value)
        if selection.seq != int(match.group("seq")):
            _fail("scout_profile_selection_write_identity_mismatch", "selection write identity differs from its path")
        writes.append(selection)
    writes.sort(key=lambda item: item.seq)
    _validate_write_sequence(
        [item.seq for item in writes],
        duplicate_code="scout_profile_selection_write_duplicate_seq",
        gap_code="scout_profile_selection_write_seq_gap",
        subject="selection",
    )
    return writes


def _current_selection(artifacts: Mapping[str, bytes]) -> ProfileSelection | None:
    writes = _all_selection_writes(artifacts)
    return writes[-1] if writes else None


def _resolve_newest_resume_for_gig(resolved: ResolvedWorkpad, *, home_root: Path, target: Path) -> PinnedResume:
    """The SAME "newest committed resume" query as ``run.resolve_newest_resume_details``,

    reimplemented here ONLY so ``gig_id`` stays pinned to ``resolved.gig_id``
    throughout. ``run.resolve_newest_resume_details`` cannot be reused as-is:
    it hardcodes ``gig_id=None`` at its own internal ``resolve_workpad`` call
    (``run.py:236-241``), which always re-resolves whichever gig is currently
    the *active* one for the target -- calling it from a migration already
    scoped to an explicit, possibly-inactive gig would silently read the
    ACTIVE gig's resume instead of the requested gig's. Every call below
    pins ``gig_id=resolved.gig_id`` explicitly (never ``None``), matching the
    S25 prototype's own fix (``orchestrator/research/S25-profiles/
    migrate.py::_resolve_newest_resume_for_gig``).
    """

    imports = private_records.list_imports(
        home_root=home_root,
        requested_target=target,
        family="reference",
        gig_id=resolved.gig_id,
    )
    resume_imports = [
        item
        for item in imports
        if item.get("kind") == "resume" and isinstance(item.get("reference_id"), str)
    ]
    if not resume_imports:
        raise NoResumeAvailable("no committed resume is available for this gig")

    snapshot = private_records._private_snapshot(resolved)
    record_ids = sorted(
        {
            Path(path).parts[1]
            for path in snapshot.artifacts
            if len(Path(path).parts) == 4
            and Path(path).parts[0] == "records"
            and Path(path).parts[1].startswith("record_")
            and Path(path).parts[2] == "revisions"
        }
    )
    linked: list[tuple[dict[str, object], str, str, dict[str, object]]] = []
    for imported in resume_imports:
        reference_id = imported["reference_id"]
        assert isinstance(reference_id, str)
        for record_id in record_ids:
            try:
                revisions = private_records.list_revisions(
                    resolved=resolved, record_id=record_id, snapshot=snapshot
                )
            except Exception:
                continue
            if not revisions:
                continue
            revision = revisions[-1]
            content = revision.get("content")
            if not isinstance(content, Mapping):
                continue
            if content.get("family") != "g45_reference" or content.get("reference_id") != reference_id:
                continue
            revision_id = revision.get("revision_id")
            if not isinstance(revision_id, str):
                continue
            linked.append((imported, record_id, revision_id, dict(content)))
    if not linked:
        raise NoResumeAvailable("no committed resume record revision is available for this gig")
    _imported, record_id, revision_id, _content_ref = max(
        linked,
        key=lambda item: (str(item[0].get("created_at", "")), str(item[2])),
    )
    selected = private_records.read_record(
        home_root=home_root,
        requested_target=target,
        record_id=record_id,
        revision_id=revision_id,
        content=True,
        gig_id=resolved.gig_id,  # pinned; never re-resolved
    )
    content = selected.get("content")
    if not isinstance(content, bytes):
        raise NoResumeAvailable("resume content is unavailable for this gig")
    digest = digest_imported_bytes(content)
    return PinnedResume(record_id=record_id, revision_id=revision_id, content_sha256=digest)


def _read_snapshot(
    resolved: ResolvedWorkpad,
    prefixes: tuple[str, ...] = (_PROFILES_ROOT, _SELECTION_ROOT),
) -> JournalSnapshot:
    """The committed profile records, read without the journal writer lock."""

    return read_committed_snapshot(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        prefixes=prefixes,
    )


def ensure_default_profile(
    resolved: ResolvedWorkpad,
    *,
    home_root: Path,
    target: Path,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    resolved_resume: tuple[str, PinnedResume] | None = None,
) -> MigrationResult | None:
    """Add-only, idempotent, first-read migration: default profile + its selection.

    Writes ``records/scout-profiles/<new-id>/writes/000000000001.json`` (seq
    1, ``revision: 1``, ``origin: "migrated_default"``, titles/titles_to_
    avoid/queries from the shared target-level ``find-jobs.json``,
    ``resume_ref`` from the resolved gig's newest committed resume) AND
    ``records/scout-profile-selection/000000000001.json`` selecting it, as
    ONE journal commit (Active-profile authority, decision 3: the migration
    must never leave a gig with a profile but no selection). Returns
    ``None`` -- never fabricates a profile -- when the resolved gig has
    nothing to migrate: no ``find-jobs.json`` at the shared target level, or
    no resume committed yet FOR THIS GIG.

    Explicit ``resolved`` only: this function has no opinion on "which gig"
    and never re-resolves "the active gig" internally (r1 fix). Never edits
    ``find-jobs.json``, never edits/deletes the resume record, never touches
    ``runs/``.

    Idempotent: a second call against the same gig finds the existing
    ``origin == "migrated_default"`` profile and no-ops (no second commit,
    no second profile). Defensive: if a selection already exists (not
    possible in 0.1.9's F1 window, but checked anyway), it is never
    overwritten.

    F1-b1-r1: ``resolved_resume`` is an OPTIONAL ``(gig_id, PinnedResume)``
    pre-resolved pin -- a caller that has ALREADY resolved "the newest
    resume" for this exact gig (e.g. ``server.py``'s ``read_config``, which
    needs it again right after for ``resume_details()`` and would otherwise
    warm ``run.py``'s own resume cache only to have this function replay
    the identical, expensive lookup a second time) can hand it in here
    instead of making this function resolve it again via
    ``_resolve_newest_resume_for_gig``. The given ``gig_id`` MUST equal
    ``resolved.gig_id``, checked explicitly -- never trusted silently -- so
    a caller cannot (even by a coding mistake) pin one gig's profile to
    another gig's resume; a mismatch raises ``ProfileRecordError``. Every
    other caller (the common case) passes nothing and this function
    resolves the pin itself, pinned to ``resolved.gig_id`` throughout,
    exactly as before.
    """

    if resolved_resume is not None and resolved_resume[0] != resolved.gig_id:
        _fail(
            "scout_profile_resume_gig_mismatch",
            "resolved_resume's gig_id does not match the gig being migrated",
        )

    config_path = Path(target) / "find-jobs.json"
    if config_path.is_symlink() or not config_path.is_file():
        return None
    try:
        config = FindJobsConfig.from_json(parse_json_bytes(config_path.read_bytes()))
    except Exception:
        return None

    # api-e2e finding (coordinator, 2026-09-25): the setup flow writes
    # find-jobs.json TWICE -- once as the STARTER placeholder config
    # (`scout_cli.write_starter_find_jobs_config`, before the operator has
    # entered anything), then again with the real interview answers
    # (`ScoutFindJobsBackend._update_find_jobs_config`). A read that lands
    # between those two writes (e.g. the setup interview's own GET /api/
    # setup, which is profile-aware once F1-b's read paths land) would
    # otherwise migrate the PLACEHOLDER roles into a real, committed
    # default-profile write -- and since a profile's titles win over the
    # shared file once one exists (F1-b's own precedence rule), the real
    # roles written moments later would be silently ignored forever. Never
    # migrate while the file still IS the starter placeholder, verbatim
    # (compare the two revision-bumping fields the migration would copy,
    # not the whole file, so an operator who deliberately kept every other
    # starter default but typed real roles still migrates normally).
    from .scout_cli import STARTER_FIND_JOBS_CONFIG

    if (
        config.roles == STARTER_FIND_JOBS_CONFIG.roles
        and config.merged_queries == STARTER_FIND_JOBS_CONFIG.merged_queries
    ):
        return None

    # F1-b1-r1 (coordinator's lane, uat-bug-008 regression): the idempotent
    # no-op path -- by far the common case, since a gig migrates at most
    # once -- used to pay for `_resolve_newest_resume_for_gig`'s expensive
    # "newest committed resume" lookup (a `private_records.list_imports` +
    # `read_record` scan, itself another full journal-writer acquisition)
    # EVERY call, even when no resume was ever going to be needed because a
    # default profile already exists. `read_config()` calls this on every
    # request, so that unconditional lookup alone doubled /api/config's
    # per-request cost (the same expensive query `resume_details()` already
    # makes, right next to this call).
    #
    # Fixed: check for an existing default profile in a SEPARATE, cheap
    # read first, and only resolve the resume (and open the writer for the
    # actual create) when we're truly about to create one. The resume
    # lookup is deliberately NOT inside the create operation's own writer
    # callback below: the per-workpad lock is not reentrant, so nothing
    # that might itself need the writer lock may run while it is held.
    #
    # journal-read-scope: that existence check is a read, so it no longer
    # takes the writer lock at all (`read_committed_snapshot`). Only a gig
    # whose default profile is actually MISSING reaches the writer below,
    # which checks again under the lock before it creates anything.
    existing = _existing_default_profile(_current_profiles(_read_snapshot(resolved).artifacts))
    if existing is not None:
        return MigrationResult(profile_id=existing.profile_id, revision=existing.revision, created=False)

    if resolved_resume is not None:
        pinned = resolved_resume[1]
    else:
        try:
            pinned = _resolve_newest_resume_for_gig(resolved, home_root=home_root, target=target)
        except NoResumeAvailable:
            return None

    def operation(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        existing = _existing_default_profile(current)
        if existing is not None:
            return MigrationResult(profile_id=existing.profile_id, revision=existing.revision, created=False)

        profile_id = f"profile_{uuid_factory()}"
        titles = tuple(config.roles)
        titles_to_avoid: tuple[str, ...] = ()
        queries = tuple(config.merged_queries)
        digest = _content_digest(titles=titles, titles_to_avoid=titles_to_avoid, queries=queries, resume_ref=pinned)
        now = _now()
        record = ProfileRecord(
            schema_version=SCHEMA_VERSION,
            profile_id=profile_id,
            seq=1,
            revision=1,
            label="default",
            state="active",
            origin="migrated_default",
            resume_ref=pinned,
            titles=titles,
            titles_to_avoid=titles_to_avoid,
            queries=queries,
            content_digest=digest,
            created_at=now,
            updated_at=now,
            parent_seq=None,
        )
        record_bytes = _validated(record)
        record_path = _write_path(profile_id, 1)
        artifacts = [JournalArtifact(record_path, record_bytes)]
        artifact_refs = [_artifact_ref(record_path, record_bytes)]

        existing_selection = _current_selection(snapshot.artifacts)
        if existing_selection is None:
            selection = ProfileSelection(
                schema_version=SELECTION_SCHEMA_VERSION,
                seq=1,
                selected_profile_id=profile_id,
                updated_at=now,
                parent_seq=None,
            )
            selection_bytes = _validated_selection(selection)
            selection_path = _selection_path(1)
            artifacts.append(JournalArtifact(selection_path, selection_bytes))
            artifact_refs.append(_artifact_ref(selection_path, selection_bytes))

        writer.record(
            JournalTransition(
                f"handoff_{uuid_factory()}",
                PROFILE_TRANSITION,
                "Migrated find-jobs.json roles/queries + the newest resume into a default scout profile (S25 F1-a add-only migration).",
                tuple(artifacts),
                {"artifact_refs": artifact_refs},
            )
        )
        return MigrationResult(profile_id=profile_id, revision=1, created=True)

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def _artifact_ref(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": "application/json",
        "size_bytes": len(data),
    }


def create_profile(
    resolved: ResolvedWorkpad,
    *,
    label: str,
    titles: tuple[str, ...],
    titles_to_avoid: tuple[str, ...],
    queries: tuple[str, ...],
    resume_ref: PinnedResume,
    search_settings: ProfileSearchSettings | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ProfileRecord:
    """Mint a brand-new, operator-named profile (F1-c's "create").

    0110-022: ``search_settings`` are the new profile's own location, work
    mode, countries and posted window (the caller prefills them from the
    default). They are stored only when another profile already exists: the
    first profile of a gig is the default one and uses the setup settings.

    Distinct from ``ensure_default_profile`` (migration-only: fixed
    ``origin="migrated_default"``, ``label="default"``, titles/queries
    derived from ``find-jobs.json``, and bundles the gig's initial
    selection write in the same commit) and from ``write_profile`` (edits
    an EXISTING ``profile_id``, never mints one). This is the third write
    shape: seq 1, revision 1, ``origin="operator_created"``, exactly the
    caller-given content -- same single write path (schema-validated
    ``ProfileRecord``, ``content_digest`` computed the one place
    ``_content_digest`` does it), the same journal writer lock, and the
    same ``PROFILE_TRANSITION`` name every other profile write uses.

    Never touches the gig's selection: creating a profile does not select
    it (F1-c's caller switches separately via ``switch_selected_profile``
    if that's what the operator asked for). Works the same whether or not
    a migrated default profile already exists in this gig -- there is no
    dependency on ``ensure_default_profile`` having run first; a gig with
    no committed resume/``find-jobs.json`` yet (so no default profile) can
    still have its first profile created this way, since ``resume_ref`` is
    caller-supplied here, never re-resolved from "newest".
    """

    def operation(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        profile_id = f"profile_{uuid_factory()}"
        while profile_id in current:  # astronomically unlikely; defensive only
            profile_id = f"profile_{uuid_factory()}"

        own_settings = search_settings if current else None
        digest = _content_digest(
            titles=titles,
            titles_to_avoid=titles_to_avoid,
            queries=queries,
            resume_ref=resume_ref,
            search_settings=own_settings,
        )
        now = _now()
        record = ProfileRecord(
            schema_version=SCHEMA_VERSION,
            profile_id=profile_id,
            seq=1,
            revision=1,
            label=label,
            state="active",
            origin="operator_created",
            resume_ref=resume_ref,
            titles=titles,
            titles_to_avoid=titles_to_avoid,
            queries=queries,
            content_digest=digest,
            created_at=now,
            updated_at=now,
            parent_seq=None,
            search_settings=own_settings,
        )
        record_bytes = _validated(record)
        path = _write_path(profile_id, 1)
        writer.record(
            JournalTransition(
                f"handoff_{uuid_factory()}",
                PROFILE_TRANSITION,
                "Created a new scout profile.",
                (JournalArtifact(path, record_bytes),),
                {"artifact_refs": [_artifact_ref(path, record_bytes)]},
            )
        )
        return record

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def selected_profile(
    resolved: ResolvedWorkpad,
    *,
    home_root: Path,
    target: Path,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ProfileRecord | None:
    """Read the gig's selected profile, migrating on first read if needed.

    Runs ``ensure_default_profile`` first (migrate-on-first-read, the same
    "heal on next command" shape as ``workpad.ensure_run_local_artifact_
    excludes``), then reads back the resulting (or pre-existing) selection
    and its current profile write. Returns ``None`` only when there is truly
    nothing to migrate onto (no ``find-jobs.json``, or no committed resume
    for this gig) AND no selection already exists.
    """

    # journal-read-scope: one lock-free read answers the common case. The
    # migration only ever adds a default profile that is missing, so a
    # snapshot that already holds one needs no migration and no second read.
    snapshot = _read_snapshot(resolved)
    current = _current_profiles(snapshot.artifacts)
    if _existing_default_profile(current) is None:
        ensure_default_profile(resolved, home_root=home_root, target=target, uuid_factory=uuid_factory)
        snapshot = _read_snapshot(resolved)
        current = _current_profiles(snapshot.artifacts)

    selection = _current_selection(snapshot.artifacts)
    if selection is None:
        return None
    record = current.get(selection.selected_profile_id)
    if record is None:
        raise ProfileRecordError("scout_profile_selection_dangling", "selected profile is not committed")
    return record


def switch_selected_profile(
    resolved: ResolvedWorkpad,
    *,
    profile_id: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ProfileSelection:
    """Append a new selection write pointing at ``profile_id``, one commit.

    Never bundled with a profile's own content edit (Active-profile
    authority, decision 2). Refuses a ``profile_id`` that is not a
    committed, non-archived profile in this gig. Concurrency: the next
    ``seq`` is computed from a snapshot taken INSIDE the journal writer's
    critical section (``run_with_journal_writer`` holds the per-workpad
    writer lock for the whole ``operation`` call), so two concurrent
    switches cannot pick the same seq -- the second writer's ``operation``
    call only starts once the first has released the lock, at which point
    its own snapshot already reflects the first write.
    """

    if not _PROFILE_ID.fullmatch(profile_id):
        _fail("scout_profile_invalid", "profile_id is not a valid profile identity")

    def operation(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        target_profile = current.get(profile_id)
        if target_profile is None:
            _fail("scout_profile_unavailable", "profile is not committed in this gig")
        if target_profile.state == "deleted":
            _fail("scout_profile_deleted", "a deleted profile cannot be selected")
        if target_profile.state == "archived":
            _fail("scout_profile_archived", "an archived profile cannot be selected")
        previous = _current_selection(snapshot.artifacts)
        next_seq = 1 if previous is None else previous.seq + 1
        selection = ProfileSelection(
            schema_version=SELECTION_SCHEMA_VERSION,
            seq=next_seq,
            selected_profile_id=profile_id,
            updated_at=_now(),
            parent_seq=None if previous is None else previous.seq,
        )
        selection_bytes = _validated_selection(selection)
        path = _selection_path(next_seq)
        writer.record(
            JournalTransition(
                f"handoff_{uuid_factory()}",
                SELECTION_TRANSITION,
                "Switched the gig's selected scout profile.",
                (JournalArtifact(path, selection_bytes),),
                {"artifact_refs": [_artifact_ref(path, selection_bytes)]},
            )
        )
        return selection

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def write_profile(
    resolved: ResolvedWorkpad,
    *,
    profile_id: str,
    label: str | None = None,
    state: str | None = None,
    titles: tuple[str, ...] | None = None,
    titles_to_avoid: tuple[str, ...] | None = None,
    queries: tuple[str, ...] | None = None,
    resume_ref: PinnedResume | None = None,
    replacement_profile_id: str | None = None,
    search_settings: ProfileSearchSettings | None = None,
    clear_search_settings: bool = False,
    master_selection: ProfileMasterSelection | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ProfileRecord:
    """The ONE write function for an existing profile's content or metadata.

    Every call appends a NEW write file at the next seq for this
    ``profile_id`` (never overwrites the previous one -- see the module's
    "Storage layout amendment"). Applies the revision-bump rule (Q1):
    ``titles``/``titles_to_avoid``/``queries``/``resume_ref`` bump
    ``revision`` when their value actually changes; ``label``/``state``
    never do (but still get their own new seq/write file, since every edit
    is append-only). ``content_digest`` is always recomputed here (never
    accepted as input) over the post-edit
    ``{titles, titles_to_avoid, queries, resume_ref}``.

    Archiving (``state="archived"``) the gig's currently selected profile
    requires ``replacement_profile_id`` naming another committed,
    non-archived profile; the archive write and the reselection write are
    committed as one transition (Active-profile authority, decision 4).

    0110-022: ``search_settings`` replaces the profile's own location, work
    mode, countries and posted window (a revision bump when they change);
    ``clear_search_settings`` drops them, back to "same as default". The
    default profile (``default_profile``) never stores any: giving them for
    it is refused with ``scout_profile_default_search_settings``.

    0.1.10.9 master P3: ``master_selection`` replaces the profile's
    selection of the master resume (``ProfileMasterSelection``). Like
    ``label`` it never bumps ``revision`` by itself: the resume a reader
    reads is ``resume_ref``, and a selection that changes it passes the new
    pin in the same call. Left out, the profile keeps the one it has.
    """

    if not _PROFILE_ID.fullmatch(profile_id):
        _fail("scout_profile_invalid", "profile_id is not a valid profile identity")

    def operation(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        existing = current.get(profile_id)
        if existing is None:
            _fail("scout_profile_unavailable", "profile is not committed in this gig")
        if existing.state == "deleted":
            _fail("scout_profile_deleted", "profile is deleted")
        if state not in (None, "active", "archived", "deleted"):
            _fail("scout_profile_invalid", "state must be active, archived or deleted")

        new_label = existing.label if label is None else label
        new_state = existing.state if state is None else state
        new_titles = existing.titles if titles is None else tuple(titles)
        new_titles_to_avoid = existing.titles_to_avoid if titles_to_avoid is None else tuple(titles_to_avoid)
        new_queries = existing.queries if queries is None else tuple(queries)
        new_resume_ref = existing.resume_ref if resume_ref is None else resume_ref
        if search_settings is not None and clear_search_settings:
            _fail("scout_profile_invalid", "give search_settings or clear_search_settings, not both")
        if search_settings is not None:
            default = default_profile(current.values())
            if default is not None and default.profile_id == profile_id:
                _fail(
                    "scout_profile_default_search_settings",
                    "the default profile uses the setup settings; change its location, work mode, countries and posted window in Settings",
                )
        if clear_search_settings:
            new_search_settings = None
        else:
            new_search_settings = existing.search_settings if search_settings is None else search_settings

        content_changed = (
            new_titles != existing.titles
            or new_titles_to_avoid != existing.titles_to_avoid
            or new_queries != existing.queries
            or new_resume_ref != existing.resume_ref
            or new_search_settings != existing.search_settings
        )
        new_revision = existing.revision + 1 if content_changed else existing.revision
        new_digest = _content_digest(
            titles=new_titles,
            titles_to_avoid=new_titles_to_avoid,
            queries=new_queries,
            resume_ref=new_resume_ref,
            search_settings=new_search_settings,
        )

        previous_selection = _current_selection(snapshot.artifacts)
        is_deleting = new_state == "deleted"
        replacement_id = replacement_profile_id
        selected_leaves = (
            new_state in ("archived", "deleted")
            and existing.state == "active"
            and previous_selection is not None
            and previous_selection.selected_profile_id == profile_id
        )
        artifacts: list[JournalArtifact] = []
        artifact_refs: list[dict[str, object]] = []
        new_selection: ProfileSelection | None = None
        if is_deleting:
            if replacement_id is not None:
                _fail("scout_profile_invalid", "replacement_profile_id is only valid when archiving the selected profile")
            default = default_profile(current.values())
            if default is not None and default.profile_id == profile_id:
                _fail(
                    "scout_profile_default_delete",
                    "the default profile uses the setup settings and cannot be deleted",
                )
            others = [item for item in current.values() if item.profile_id != profile_id and item.state == "active"]
            if not others:
                _fail("scout_profile_last_active", "the only profile cannot be deleted; add another one first")
            if selected_leaves:
                replacement = default if default is not None and default.state == "active" else min(
                    others, key=lambda item: (item.created_at, item.profile_id)
                )
                replacement_id = replacement.profile_id
        elif selected_leaves:
            if replacement_id is None:
                _fail(
                    "profile_archive_requires_replacement",
                    "archiving the selected profile requires a replacement selection",
                )
            replacement = current.get(replacement_id)
            if (
                replacement is None
                or replacement.profile_id == profile_id
                or replacement.state != "active"
            ):
                _fail(
                    "profile_archive_requires_replacement",
                    "replacement profile must be another committed, non-archived profile",
                )
        elif replacement_id is not None:
            _fail("scout_profile_invalid", "replacement_profile_id is only valid when archiving the selected profile")
        if selected_leaves:
            next_selection_seq = 1 if previous_selection is None else previous_selection.seq + 1
            new_selection = ProfileSelection(
                schema_version=SELECTION_SCHEMA_VERSION,
                seq=next_selection_seq,
                selected_profile_id=replacement_id,
                updated_at=_now(),
                parent_seq=None if previous_selection is None else previous_selection.seq,
            )

        next_seq = existing.seq + 1
        now = _now()
        record = ProfileRecord(
            schema_version=SCHEMA_VERSION,
            profile_id=profile_id,
            seq=next_seq,
            revision=new_revision,
            label=new_label,
            state=new_state,
            origin=existing.origin,
            resume_ref=new_resume_ref,
            titles=new_titles,
            titles_to_avoid=new_titles_to_avoid,
            queries=new_queries,
            content_digest=new_digest,
            created_at=existing.created_at,
            updated_at=now,
            parent_seq=existing.seq,
            search_settings=new_search_settings,
            master_selection=existing.master_selection if master_selection is None else master_selection,
        )
        record_bytes = _validated(record)
        record_path = _write_path(profile_id, next_seq)
        artifacts.append(JournalArtifact(record_path, record_bytes))
        artifact_refs.append(_artifact_ref(record_path, record_bytes))
        if new_selection is not None:
            selection_bytes = _validated_selection(new_selection)
            selection_path = _selection_path(new_selection.seq)
            artifacts.append(JournalArtifact(selection_path, selection_bytes))
            artifact_refs.append(_artifact_ref(selection_path, selection_bytes))

        if is_deleting:
            body = "Deleted a scout profile (kept in history)."
        elif new_selection is not None:
            body = "Archived the selected scout profile with a replacement selection."
        else:
            body = "Revised a scout profile record."
        writer.record(
            JournalTransition(
                f"handoff_{uuid_factory()}",
                PROFILE_TRANSITION,
                body,
                tuple(artifacts),
                {"artifact_refs": artifact_refs},
            )
        )
        return record

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def list_profiles(resolved: ResolvedWorkpad) -> tuple[ProfileRecord, ...]:
    """Read every committed profile's CURRENT write in this gig, newest-created first."""

    current = _current_profiles(_read_snapshot(resolved, (_PROFILES_ROOT,)).artifacts)
    return tuple(sorted(current.values(), key=lambda item: (item.created_at, item.profile_id)))


def retrieve_profile_revision(
    resolved: ResolvedWorkpad,
    *,
    profile_id: str,
    revision: int,
    content_digest: str,
) -> ProfileRecord:
    """Reproduce profile revision N's exact field values.

    With the append-only write-file layout, this is a direct scan (no git
    log needed, per the coordinator's storage-layout amendment): every
    write for ``profile_id`` is still committed and readable by path, newest
    seq first. Find the newest write whose ``revision == N`` AND whose
    ``content_digest`` equals the caller's sealed value together. Per the
    bump rule, a label/state-only edit does NOT bump revision, so more than
    one write can legitimately carry ``revision == N`` (a rename between N
    and N+1 gets its own seq but keeps the same revision/content_digest);
    taking the newest such match is safe because every such write shares
    byte-identical digest-covered content by construction.

    A revision/digest pair with zero matching writes is a hard failure,
    never a silent fallback to the current write.
    """

    if not _PROFILE_ID.fullmatch(profile_id):
        _fail("scout_profile_invalid", "profile_id is not a valid profile identity")

    snapshot = _read_snapshot(resolved, (_PROFILES_ROOT,))
    writes = _all_profile_writes(snapshot.artifacts).get(profile_id, [])
    for record in reversed(writes):  # newest seq first
        if record.revision != revision:
            continue
        recomputed = _content_digest(
            titles=record.titles,
            titles_to_avoid=record.titles_to_avoid,
            queries=record.queries,
            resume_ref=record.resume_ref,
            search_settings=record.search_settings,
        )
        if recomputed != content_digest or record.content_digest != content_digest:
            continue
        return record
    _fail(
        "scout_profile_revision_unavailable",
        "no committed profile write matches the requested revision and content digest",
    )


__all__ = [
    "MASTER_SELECTION_KEYS",
    "MASTER_SELECTION_SOURCES",
    "MigrationResult",
    "NoResumeAvailable",
    "ProfileMasterSelection",
    "ProfileRecord",
    "ProfileRecordError",
    "ProfileSearchSettings",
    "ProfileSelection",
    "SEARCH_SETTINGS_KEYS",
    "create_profile",
    "default_profile",
    "ensure_default_profile",
    "list_profiles",
    "retrieve_profile_revision",
    "selected_profile",
    "switch_selected_profile",
    "write_profile",
]
