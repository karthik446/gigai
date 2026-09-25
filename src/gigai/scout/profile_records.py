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
from typing import Callable, Mapping, NoReturn
import re
import uuid

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..journal import (
    JournalArtifact,
    JournalTransition,
    run_with_journal_writer,
)
from ..validators import validate_serialized_contract
from ..workpad import ResolvedWorkpad
from .. import private_records
from .find_jobs.contracts import FindJobsConfig, PinnedResume

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

    def to_json(self) -> dict[str, object]:
        return {
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

    @classmethod
    def from_json(cls, value: object) -> "ProfileRecord":
        if not isinstance(value, Mapping):
            raise ProfileRecordError("scout_profile_invalid", "profile record must be an object")
        try:
            resume_ref = PinnedResume.from_json(value["resume_ref"])
            parent_seq = value["parent_seq"]
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


def _content_digest(*, titles: tuple[str, ...], titles_to_avoid: tuple[str, ...], queries: tuple[str, ...], resume_ref: PinnedResume) -> str:
    """The ONE place a profile's ``content_digest`` is computed.

    Recomputed unconditionally on every write, over exactly the four
    revision-bumping fields (canonicalized), never accepted from a caller --
    a hand-edited or partially-written file can never carry a stale digest
    because nothing ever trusts one that wasn't just recomputed here (open
    question 9's recommended default, matching ``FindJobsConfig.digest()``'s
    own "computed, not accepted" convention).
    """

    payload = {
        "titles": list(titles),
        "titles_to_avoid": list(titles_to_avoid),
        "queries": list(queries),
        "resume_ref": resume_ref.to_json(),
    }
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
    # writer acquisition first, and only resolve the resume (and re-open
    # the writer for the actual create) when we're truly about to create
    # one. The resume lookup CANNOT be moved inside the create operation's
    # own writer callback below: `_resolve_newest_resume_for_gig` calls
    # `private_records.list_imports`, which opens its OWN
    # `run_with_journal_writer` -- nesting that inside an already-held
    # writer lock deadlocks (the per-workpad lock is not reentrant).
    def _check_existing(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        return _existing_default_profile(current)

    existing = run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=_check_existing,
    )
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
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ProfileRecord:
    """Mint a brand-new, operator-named profile (F1-c's "create").

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

        digest = _content_digest(
            titles=titles, titles_to_avoid=titles_to_avoid, queries=queries, resume_ref=resume_ref
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

    ensure_default_profile(resolved, home_root=home_root, target=target, uuid_factory=uuid_factory)

    def read(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        selection = _current_selection(snapshot.artifacts)
        if selection is None:
            return None
        current = _current_profiles(snapshot.artifacts)
        record = current.get(selection.selected_profile_id)
        if record is None:
            raise ProfileRecordError("scout_profile_selection_dangling", "selected profile is not committed")
        return record

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )


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
    """

    if not _PROFILE_ID.fullmatch(profile_id):
        _fail("scout_profile_invalid", "profile_id is not a valid profile identity")

    def operation(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT, _SELECTION_ROOT))
        current = _current_profiles(snapshot.artifacts)
        existing = current.get(profile_id)
        if existing is None:
            _fail("scout_profile_unavailable", "profile is not committed in this gig")

        new_label = existing.label if label is None else label
        new_state = existing.state if state is None else state
        new_titles = existing.titles if titles is None else tuple(titles)
        new_titles_to_avoid = existing.titles_to_avoid if titles_to_avoid is None else tuple(titles_to_avoid)
        new_queries = existing.queries if queries is None else tuple(queries)
        new_resume_ref = existing.resume_ref if resume_ref is None else resume_ref

        content_changed = (
            new_titles != existing.titles
            or new_titles_to_avoid != existing.titles_to_avoid
            or new_queries != existing.queries
            or new_resume_ref != existing.resume_ref
        )
        new_revision = existing.revision + 1 if content_changed else existing.revision
        new_digest = _content_digest(
            titles=new_titles, titles_to_avoid=new_titles_to_avoid, queries=new_queries, resume_ref=new_resume_ref
        )

        previous_selection = _current_selection(snapshot.artifacts)
        is_archiving_selected = (
            new_state == "archived"
            and existing.state != "archived"
            and previous_selection is not None
            and previous_selection.selected_profile_id == profile_id
        )
        artifacts: list[JournalArtifact] = []
        artifact_refs: list[dict[str, object]] = []
        new_selection: ProfileSelection | None = None
        if is_archiving_selected:
            if replacement_profile_id is None:
                _fail(
                    "profile_archive_requires_replacement",
                    "archiving the selected profile requires a replacement selection",
                )
            replacement = current.get(replacement_profile_id)
            if replacement is None or replacement.profile_id == profile_id or replacement.state == "archived":
                _fail(
                    "profile_archive_requires_replacement",
                    "replacement profile must be another committed, non-archived profile",
                )
            next_selection_seq = 1 if previous_selection is None else previous_selection.seq + 1
            new_selection = ProfileSelection(
                schema_version=SELECTION_SCHEMA_VERSION,
                seq=next_selection_seq,
                selected_profile_id=replacement_profile_id,
                updated_at=_now(),
                parent_seq=None if previous_selection is None else previous_selection.seq,
            )
        elif replacement_profile_id is not None:
            _fail("scout_profile_invalid", "replacement_profile_id is only valid when archiving the selected profile")

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

        body = "Archived the selected scout profile with a replacement selection." if new_selection is not None else "Revised a scout profile record."
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

    def read(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT,))
        current = _current_profiles(snapshot.artifacts)
        return tuple(sorted(current.values(), key=lambda item: (item.created_at, item.profile_id)))

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )


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

    def read(writer):
        snapshot = writer.snapshot((_PROFILES_ROOT,))
        writes = _all_profile_writes(snapshot.artifacts).get(profile_id, [])
        for record in reversed(writes):  # newest seq first
            if record.revision != revision:
                continue
            recomputed = _content_digest(
                titles=record.titles,
                titles_to_avoid=record.titles_to_avoid,
                queries=record.queries,
                resume_ref=record.resume_ref,
            )
            if recomputed != content_digest or record.content_digest != content_digest:
                continue
            return record
        _fail(
            "scout_profile_revision_unavailable",
            "no committed profile write matches the requested revision and content digest",
        )

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )


__all__ = [
    "MigrationResult",
    "NoResumeAvailable",
    "ProfileRecord",
    "ProfileRecordError",
    "ProfileSelection",
    "create_profile",
    "ensure_default_profile",
    "list_profiles",
    "retrieve_profile_revision",
    "selected_profile",
    "switch_selected_profile",
    "write_profile",
]
