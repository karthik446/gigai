"""Journal-derived Scout tracker views.

This module is deliberately a reader.  The private Git journal and committed
record artifacts are authority; ``state.sqlite`` is only a disposable cache of
the value returned here.  The small reader interfaces make the R0 boundary
explicit and let R1/R2 supply their reviewed readers without teaching the
tracker to trust model-owned identifiers.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from collections.abc import Callable, Iterable, Mapping, Sequence
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Protocol

from ..application_events import ApplicationEventError, validate_application_links
from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from ..journal import (
    JournalArtifactMissingError,
    JournalError,
    JournalSnapshot,
    read_committed_artifact,
    read_committed_snapshot,
)
from ..private_records import RECORD_DIRECTORY_PATTERN
from ..index import database_lock
from .find_jobs.contracts import (
    AcquireOutput,
    AggregateStatus,
    AssessOutput,
    FindJobsContractError,
    FindJobsRunInput,
    NodeContext,
    NodeReceipt,
    PresentInput,
    PresentOutput,
    PresentPayload,
    aggregate_status,
)
from ..workpad import committed_read_cache, committed_read_cache_active, resolve_workpad


class ScoutProjectionError(RuntimeError):
    """A committed artifact cannot be represented as a tracker view."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProjectionReader(Protocol):
    def __call__(
        self, snapshot: JournalSnapshot, project_id: str, gig_id: str
    ) -> Iterable[Mapping[str, object]]: ...


@dataclass(frozen=True)
class ScoutReaderSet:
    """Cross-lane reader hooks.

    A reader returns host-validated DTOs, never raw model JSON.  Missing hooks
    use conservative readers for records and application events; discovery,
    proposal and Tailor readers remain empty until their lanes publish a
    concrete authority shape.
    """

    opportunities: ProjectionReader | None = None
    proposals: ProjectionReader | None = None
    documents: ProjectionReader | None = None
    evidence: ProjectionReader | None = None
    runs: ProjectionReader | None = None


@dataclass(frozen=True)
class ScoutProjection:
    schema_version: str
    project_id: str
    gig_id: str
    journal_head: str
    opportunities: tuple[dict[str, object], ...] = ()
    proposals: tuple[dict[str, object], ...] = ()
    questions: tuple[dict[str, object], ...] = ()
    documents: tuple[dict[str, object], ...] = ()
    evidence: tuple[dict[str, object], ...] = ()
    applications: tuple[dict[str, object], ...] = ()
    runs: tuple[dict[str, object], ...] = ()
    # S25 F1-a: a read-only profiles view, built from the same pinned
    # snapshot as every other row above -- never a second authority. No
    # reader wiring here (F1-b's job): nothing else in this projection joins
    # against these rows yet.
    profiles: tuple[dict[str, object], ...] = ()
    selected_profile_id: str | None = None
    cursor: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "gig_id": self.gig_id,
            "journal_head": self.journal_head,
            "cursor": dict(self.cursor),
            "opportunities": [dict(item) for item in self.opportunities],
            "proposals": [dict(item) for item in self.proposals],
            "questions": [dict(item) for item in self.questions],
            "documents": [dict(item) for item in self.documents],
            "evidence": [dict(item) for item in self.evidence],
            "applications": [dict(item) for item in self.applications],
            "runs": [dict(item) for item in self.runs],
            "profiles": [dict(item) for item in self.profiles],
            "selected_profile_id": self.selected_profile_id,
        }


_OPPORTUNITY = re.compile(r"^opportunity_[0-9a-f]{32}$")
_RECORD = re.compile(r"^record_[0-9a-f-]{36}$")
_REVISION = re.compile(r"^revision_[0-9a-f-]{36}$")


def _rows(reader: ProjectionReader | None, snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    if reader is None:
        return []
    try:
        values = reader(snapshot, project, gig)
        result = [dict(item) for item in values]
    except ScoutProjectionError:
        raise
    except Exception as exc:
        raise ScoutProjectionError("projection_reader_failed", "Scout reader failed") from exc
    for item in result:
        if not isinstance(item, dict):
            raise ScoutProjectionError("projection_reader_invalid", "Scout reader returned a non-object")
    return result


def _record_rows(snapshot: JournalSnapshot, project: str, gig: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Read exact committed private revisions without trusting SQLite."""
    record_ids = sorted(
        {
            Path(path).parts[1]
            for path in snapshot.artifacts
            if len(Path(path).parts) == 4
            and Path(path).parts[0] == "records"
            and Path(path).parts[2] == "revisions"
            and Path(path).parts[3].endswith(".json")
        }
    )
    records: list[dict[str, object]] = []
    questions: list[dict[str, object]] = []
    # list_revisions needs a ResolvedWorkpad, so validate and replay the
    # revision bytes locally here.  We only follow direct parent links and
    # reject ambiguous chains; no "latest" projection is accepted from a
    # caller or model.
    for record_id in record_ids:
        if not _RECORD.fullmatch(record_id):
            raise ScoutProjectionError("projection_record_invalid", "record identity is invalid")
        prefix = f"records/{record_id}/revisions/"
        revisions: dict[str, dict[str, object]] = {}
        for path, raw in snapshot.artifacts.items():
            if not (path.startswith(prefix) and path.endswith(".json")):
                continue
            try:
                value = parse_json_bytes(raw)
            except ValueError as exc:
                raise ScoutProjectionError("projection_record_invalid", "record revision is not JSON") from exc
            if not isinstance(value, dict) or value.get("record_id") != record_id or value.get("project_id") != project or value.get("gig_id") != gig:
                raise ScoutProjectionError("projection_record_scope_refused", "record revision scope differs")
            revision_id = value.get("revision_id")
            if not isinstance(revision_id, str) or not _REVISION.fullmatch(revision_id) or revision_id in revisions:
                raise ScoutProjectionError("projection_record_invalid", "record revision identity is invalid")
            revisions[revision_id] = value
        roots = [item for item in revisions.values() if item.get("parent_revision") is None]
        if not revisions or len(roots) != 1:
            raise ScoutProjectionError("projection_record_invalid", "record revision chain has no unique root")
        ordered = [roots[0]]
        seen = {str(roots[0]["revision_id"])}
        while True:
            children = [item for item in revisions.values() if item.get("parent_revision") == ordered[-1]["revision_id"]]
            if len(children) > 1:
                raise ScoutProjectionError("projection_record_invalid", "record revision chain branches")
            if not children:
                break
            child = children[0]
            child_id = str(child["revision_id"])
            if child_id in seen:
                raise ScoutProjectionError("projection_record_invalid", "record revision chain cycles")
            seen.add(child_id)
            ordered.append(child)
        if len(seen) != len(revisions):
            raise ScoutProjectionError("projection_record_invalid", "record revision chain is disconnected")
        current = ordered[-1]
        records.append({
            "record_id": record_id,
            "revision_id": current["revision_id"],
            "kind": current.get("kind"),
            "state": current.get("state"),
            "origin": current.get("origin"),
            "location": f"records/{record_id}/revisions/{current['revision_id']}.json",
            "content_sha256": digest_imported_bytes(canonical_json_bytes(current.get("content"))),
        })
        content = current.get("content")
        payload = None
        if isinstance(content, dict) and content.get("family") == "jsl_blob":
            blob_ref = content.get("blob_ref")
            blob_path = blob_ref.get("path") if isinstance(blob_ref, dict) else None
            blob_raw = snapshot.artifacts.get(blob_path) if isinstance(blob_path, str) else None
            if blob_raw is not None:
                try:
                    sidecar = parse_json_bytes(blob_raw)
                except ValueError as exc:
                    raise ScoutProjectionError("projection_record_invalid", "native sidecar is not JSON") from exc
                payload = sidecar.get("payload") if isinstance(sidecar, dict) else None
            if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
                for question in payload["questions"]:
                    if isinstance(question, dict) and isinstance(question.get("question_id"), str):
                        questions.append({
                            "question_id": question["question_id"],
                            "record_id": record_id,
                            "revision_id": current["revision_id"],
                            "state": question.get("state", "unknown"),
                            "prompt": question.get("prompt", ""),
                            "answer": question.get("answer") if question.get("state") == "answered" else None,
                        })
    return records, questions


def _event_ref_value(event: Mapping[str, object]) -> object:
    """Return an application event's one posting identity.

    A1: core's committed event carries exactly one of ``opportunity_ref``
    (a Discover opportunity) or ``external_ref`` (a bounded opaque string --
    here, a find-jobs posting's ``normalized_url``, joined below). Grouping
    and the report both need this generic identity regardless of which
    field is present.
    """
    if "opportunity_ref" in event:
        return event.get("opportunity_ref")
    return event.get("external_ref")


def _application_rows(snapshot: JournalSnapshot, project: str, gig: str, opportunity_reader: ProjectionReader | None = None) -> list[dict[str, object]]:
    sequences: dict[str, int] = {}
    for handoff_path, raw in snapshot.artifacts.items():
        if not (handoff_path.startswith("handoffs/") and handoff_path.endswith(".txt")):
            continue
        try:
            metadata, _body = parse_json_front_matter(raw)
        except ValueError:
            continue
        sequence = metadata.get("sequence")
        refs = metadata.get("artifact_refs")
        if type(sequence) is int and isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                    sequences[ref["path"]] = sequence
    values: list[dict[str, object]] = []
    for path in sorted(snapshot.artifacts):
        if not (path.startswith("records/applications/events/") and path.endswith(".json")):
            continue
        try:
            event = validate_application_links(
                parse_json_bytes(snapshot.artifacts[path]),
                snapshot=snapshot,
                project_id=project,
                gig_id=gig,
                opportunity_reader=opportunity_reader,
            )
        except ApplicationEventError as exc:
            raise ScoutProjectionError("projection_application_invalid", str(exc)) from exc
        values.append({
            **event,
            "event_path": path,
            "journal_sequence": sequences.get(path, 0),
            "opportunity_verified": False,
        })
    values.sort(key=lambda item: (str(item.get("occurred_at", "")), int(item.get("journal_sequence", 0)), str(item.get("event_id", ""))))
    by_opportunity: dict[str, list[dict[str, object]]] = {}
    for event in values:
        by_opportunity.setdefault(str(_event_ref_value(event)), []).append(event)
    for group in by_opportunity.values():
        superseded = {item.get("supersedes") for item in group}
        for item in group:
            item["current"] = item.get("event_id") not in superseded
            item["current_status"] = item.get("event_kind") if item["current"] else None
    return values


def _profile_rows(snapshot: JournalSnapshot, project: str, gig: str) -> tuple[list[dict[str, object]], str | None]:
    """Read profiles (S25 F1-a) directly off the pinned snapshot.

    Profiles are append-only write-file chains (each edit is a NEW file at
    the next seq under ``records/scout-profiles/<profile_id>/writes/``,
    never an overwrite -- see ``profile_records``'s module docstring,
    "Storage layout amendment"); the CURRENT profile is the write at the
    highest seq. This never runs the migration
    (``profile_records.ensure_default_profile``/``selected_profile``): the
    projection is a read-only cache of whatever is already committed, and
    the migration is invoked only from Scout's own read paths per the
    architecture rule (never from core, never from this reader).
    """

    from .profile_records import ProfileRecordError, _current_profiles, _current_selection

    try:
        current = _current_profiles(snapshot.artifacts)
        selection = _current_selection(snapshot.artifacts)
    except ProfileRecordError as exc:
        raise ScoutProjectionError("projection_profile_invalid", "committed profile or selection is invalid") from exc

    profiles = [record.to_json() for record in current.values()]
    profiles.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("profile_id", ""))))
    selected_profile_id = selection.selected_profile_id if selection is not None else None
    return profiles, selected_profile_id


def _verify_opportunities(applications: list[dict[str, object]], opportunities: list[dict[str, object]]) -> None:
    identities = {
        (str(item.get("opportunity_id")), str(item.get("snapshot_id")))
        for item in opportunities
        if _OPPORTUNITY.fullmatch(str(item.get("opportunity_id"))) and isinstance(item.get("snapshot_id"), str)
    }
    for event in applications:
        # external_ref events name no Discover opportunity at all (A1); only
        # opportunity_ref events can verify against this Discover identity
        # set. external_ref's own join is a separate, find-jobs-side lookup
        # (see _posting_rows / A1's join below), not "verified" in this sense.
        event["opportunity_verified"] = "opportunity_ref" in event and any(
            item[0] == event.get("opportunity_ref") for item in identities
        )


def _verify_proposals(proposals: list[dict[str, object]], opportunities: list[dict[str, object]]) -> None:
    """Annotate proposal associations without allowing them to create jobs."""
    identities = {
        (str(item.get("opportunity_id")), str(item.get("snapshot_id")))
        for item in opportunities
        if _OPPORTUNITY.fullmatch(str(item.get("opportunity_id"))) and isinstance(item.get("snapshot_id"), str)
    }
    for proposal in proposals:
        proposal["opportunity_verified"] = (
            str(proposal.get("opportunity_id")), str(proposal.get("snapshot_id"))
        ) in identities


def _posting_by_normalized_url(snapshot: JournalSnapshot) -> dict[str, dict[str, object]]:
    """Read every committed find-jobs posting off this snapshot, by normalized_url.

    A1: the Scout-side join for ``external_ref`` events. ``runs/<run_id>/
    outputs/acquire.json`` is the same committed ``AcquireOutput`` shape
    ``interview_prep.posting`` reads for prep (``PostingRow.normalized_url``
    is find-jobs' own posting identity, a separate system from Discover's
    ``opportunity_id`` -- see that module's docstring). Malformed acquire
    output at a path this snapshot happened to include is skipped rather
    than failing the whole projection: this join is best-effort, not
    authority over whether the event itself is valid.
    """
    postings: dict[str, dict[str, object]] = {}
    for path in sorted(snapshot.artifacts):
        if not (path.startswith("runs/") and path.endswith("/outputs/acquire.json")):
            continue
        try:
            value = parse_json_bytes(snapshot.artifacts[path])
            acquire = AcquireOutput.from_json(value)
        except (ValueError, FindJobsContractError):
            continue
        for row in acquire.rows:
            postings.setdefault(row.posting.normalized_url, row.posting.to_json())
    return postings


def _link_external_refs(applications: list[dict[str, object]], snapshot: JournalSnapshot) -> None:
    """Annotate each external_ref application with its find-jobs posting, if any.

    A1 (5): an external_ref matching no posting still shows -- unlinked, not
    dropped, not an error; ``linked_posting`` is ``None`` in that case. An
    opportunity_ref event is untouched (``linked_posting`` stays absent).
    """
    postings: dict[str, dict[str, object]] | None = None
    for event in applications:
        if "external_ref" not in event:
            continue
        if postings is None:
            postings = _posting_by_normalized_url(snapshot)
        event["linked_posting"] = postings.get(str(event.get("external_ref")))


def projection_from_snapshot(*, snapshot: JournalSnapshot, project_id: str, gig_id: str, readers: ScoutReaderSet | None = None, require_opportunity_links: bool = False) -> ScoutProjection:
    """Build a deterministic view from one pinned journal snapshot.

    ``readers`` is the R0 fixture/reader seam.  A strict caller can require all
    application events to resolve to an actual selected opportunity; the
    default preserves visible legacy event history as explicitly unresolved.
    """
    readers = readers or ScoutReaderSet()
    records, questions = _record_rows(snapshot, project_id, gig_id)
    opportunities = _rows(readers.opportunities, snapshot, project_id, gig_id)
    applications = _application_rows(snapshot, project_id, gig_id, readers.opportunities)
    proposals = _rows(readers.proposals, snapshot, project_id, gig_id)
    documents = _rows(readers.documents, snapshot, project_id, gig_id)
    evidence = _rows(readers.evidence, snapshot, project_id, gig_id)
    runs = _rows(readers.runs, snapshot, project_id, gig_id)
    profiles, selected_profile_id = _profile_rows(snapshot, project_id, gig_id)
    _verify_opportunities(applications, opportunities)
    _verify_proposals(proposals, opportunities)
    _link_external_refs(applications, snapshot)
    if require_opportunity_links and any(not item["opportunity_verified"] for item in applications):
        raise ScoutProjectionError("projection_opportunity_missing", "application event does not name a committed opportunity")
    cursor = {"schema_version": "scout-projection:1", "journal_head": snapshot.head}
    return ScoutProjection(
        schema_version="scout-projection:1",
        project_id=project_id,
        gig_id=gig_id,
        journal_head=snapshot.head,
        opportunities=tuple(opportunities),
        proposals=tuple(proposals),
        questions=tuple(questions),
        documents=tuple(documents),
        evidence=tuple(evidence),
        applications=tuple(applications),
        runs=tuple(runs),
        profiles=tuple(profiles),
        selected_profile_id=selected_profile_id,
        cursor=cursor,
    )


def query_projection(projection: ScoutProjection) -> sqlite3.Connection:
    """Expose a read-only-style SQLite query view over a projection value.

    The returned in-memory database is disposable and has no authority.  It is
    intentionally separate from the workpad's shared state database until the
    R4 integration owner registers these rows in the closed index inventory.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE opportunities (opportunity_id TEXT, snapshot_id TEXT, payload BLOB NOT NULL);
        CREATE TABLE proposals (opportunity_id TEXT, revision_id TEXT, status TEXT, payload BLOB NOT NULL);
        CREATE TABLE questions (question_id TEXT, state TEXT, payload BLOB NOT NULL);
        CREATE TABLE documents (record_id TEXT, revision_id TEXT, payload BLOB NOT NULL);
        CREATE TABLE evidence (evidence_id TEXT, payload BLOB NOT NULL);
        CREATE TABLE applications (event_id TEXT PRIMARY KEY, opportunity_ref TEXT, event_kind TEXT, current INTEGER, payload BLOB NOT NULL);
        CREATE TABLE runs (run_id TEXT, status TEXT, payload BLOB NOT NULL);
        CREATE TABLE profiles (profile_id TEXT, state TEXT, origin TEXT, payload BLOB NOT NULL);
        CREATE TABLE profile_selection (selected_profile_id TEXT);
        CREATE TABLE scout_cursor (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    connection.executemany("INSERT INTO opportunities VALUES (?, ?, ?)", [(item.get("opportunity_id"), item.get("snapshot_id"), canonical_json_bytes(item)) for item in projection.opportunities])
    connection.executemany("INSERT INTO proposals VALUES (?, ?, ?, ?)", [(item.get("opportunity_id"), item.get("revision_id"), item.get("status"), canonical_json_bytes(item)) for item in projection.proposals])
    connection.executemany("INSERT INTO questions VALUES (?, ?, ?)", [(item.get("question_id"), item.get("state"), canonical_json_bytes(item)) for item in projection.questions])
    connection.executemany("INSERT INTO documents VALUES (?, ?, ?)", [(item.get("record_id"), item.get("revision_id"), canonical_json_bytes(item)) for item in projection.documents])
    connection.executemany("INSERT INTO evidence VALUES (?, ?)", [(item.get("evidence_id") or item.get("claim_id"), canonical_json_bytes(item)) for item in projection.evidence])
    connection.executemany("INSERT INTO applications VALUES (?, ?, ?, ?, ?)", [(item.get("event_id"), item.get("opportunity_ref"), item.get("event_kind"), int(bool(item.get("current"))), canonical_json_bytes(item)) for item in projection.applications])
    connection.executemany("INSERT INTO runs VALUES (?, ?, ?)", [(item.get("run_id"), item.get("status"), canonical_json_bytes(item)) for item in projection.runs])
    connection.executemany("INSERT INTO profiles VALUES (?, ?, ?, ?)", [(item.get("profile_id"), item.get("state"), item.get("origin"), canonical_json_bytes(item)) for item in projection.profiles])
    connection.execute("INSERT INTO profile_selection VALUES (?)", (projection.selected_profile_id,))
    connection.executemany("INSERT INTO scout_cursor VALUES (?, ?)", [("journal_head", projection.journal_head), ("schema_version", projection.schema_version)])
    connection.commit()
    return connection


def read_cached_projection(*, workpad: Path) -> ScoutProjection:
    """Read the disposable Scout cache, refusing malformed cache payloads."""
    try:
        connection = sqlite3.connect(f"file:{workpad / 'state.sqlite'}?mode=ro", uri=True)
        row = connection.execute("SELECT payload FROM scout_meta WHERE key = 'report_projection'").fetchone()
    except sqlite3.Error as exc:
        raise ScoutProjectionError("projection_cache_unavailable", "Scout projection cache is unavailable") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    if row is None:
        raise ScoutProjectionError("projection_cache_missing", "Scout projection cache has not been rebuilt")
    try:
        payload = json.loads(bytes(row[0]))
        if not isinstance(payload, dict):
            raise ValueError("projection payload is not an object")
        fields = {"schema_version", "project_id", "gig_id", "journal_head", "cursor", "opportunities", "proposals", "questions", "documents", "evidence", "applications", "runs", "profiles", "selected_profile_id"}
        if set(payload) != fields:
            raise ValueError("projection payload has unexpected fields")
        return ScoutProjection(**payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScoutProjectionError("projection_cache_invalid", "Scout projection cache is malformed") from exc


# journal-read-scope: every family a projection reader reads, named. The
# private records themselves (``records/<record_id>/``) are selected by their
# ID shape in ``read_projection_snapshot``. A family that is absent here (the
# ATS watchlist, the operation receipts) is one no reader below reads, so its
# size never costs a projection anything. A reader that starts reading a new
# family must add it here.
PROJECTION_SNAPSHOT_PREFIXES = (
    "records/applications/",
    "records/scout-documents/",
    "records/scout-proposals/",
    "records/scout-profiles/",
    "records/scout-profile-selection/",
    "runs/",
    "run-plans/",
    "references/",
    "run-inputs/",
    "manifests/",
)
_PROJECTION_RUNS_PREFIX = ("runs/",)
_PROJECTION_OTHER_PREFIXES = tuple(prefix for prefix in PROJECTION_SNAPSHOT_PREFIXES if prefix not in _PROJECTION_RUNS_PREFIX)


def read_projection_snapshot(resolved: Any) -> JournalSnapshot:
    """The committed evidence the projection reads, at one head, without the
    journal writer lock (a projection is a read)."""

    # Handoff text is journal metadata, not a standalone artifact and has
    # no self-reference entry. Readers derive sequence from the immutable
    # records below; excluding it avoids treating a handoff as a payload.
    read = dict(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
    records = (("records/", RECORD_DIRECTORY_PATTERN),)
    if committed_read_cache_active():
        # 0110-033: inside a read the runs are kept apart from the rest. A
        # kept snapshot is read again when a commit touched it, and the runs
        # are megabytes: a recorded application then costs the small half,
        # not every run's sealed outputs. Each artifact is checked on its
        # own, so the two halves at one head are the whole snapshot.
        # Read side by side: each half is mostly waiting for git.
        other: list[object] = []

        def read_rest() -> None:
            try:
                with committed_read_cache():
                    other.append(read_committed_snapshot(prefixes=_PROJECTION_OTHER_PREFIXES, child_prefixes=records, **read))
            except BaseException as exc:  # noqa: BLE001 - raised again below, on the caller's thread
                other.append(exc)

        beside = threading.Thread(target=read_rest, name="scout-projection-read", daemon=True)
        beside.start()
        try:
            runs = read_committed_snapshot(prefixes=_PROJECTION_RUNS_PREFIX, **read)
        finally:
            beside.join()
        rest = other[0]
        if isinstance(rest, BaseException):
            raise rest
        assert isinstance(rest, JournalSnapshot)
        if runs.head == rest.head:
            token = (runs.read_token, rest.read_token) if runs.read_token is not None and rest.read_token is not None else None
            return JournalSnapshot(rest.head, {**rest.artifacts, **runs.artifacts}, token)
    return read_committed_snapshot(prefixes=PROJECTION_SNAPSHOT_PREFIXES, child_prefixes=records, **read)


def rebuild_projection(*, resolved: Any, readers: ScoutReaderSet | None = None) -> ScoutProjection:
    """Build from authority and cache only the closed existing Scout rows."""
    if readers is None:
        # Production report generation uses committed R1/R2/Run readers bound
        # to this resolved Gig.  ``projection_from_snapshot`` intentionally
        # keeps its empty default so injected fixtures remain explicit.
        from .report_readers import default_reader_set
        readers = default_reader_set(resolved)

    projection = projection_from_snapshot(
        snapshot=read_projection_snapshot(resolved), project_id=resolved.project_id, gig_id=resolved.gig_id, readers=readers,
    )
    # These rows are a cache only.  The integration owner must add the cursor
    # to the shared index inventory before release; this lane never treats it
    # as a source of truth.
    with database_lock(resolved.path):
        connection = sqlite3.connect(resolved.path / "state.sqlite")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS scout_meta (key TEXT PRIMARY KEY, payload BLOB NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO scout_meta(key,payload) VALUES (?,?)", ("report_projection", canonical_json_bytes(projection.as_dict())))
            connection.commit()
        finally:
            connection.close()
    return projection


_GOAL_SLUGS = ("acquire", "assess", "present")


def _read_run_json(*, resolved: Any, path: str, allow_replaced_run_details: bool = False) -> dict[str, object] | None:
    """Read one committed run artifact as JSON, or ``None`` before it exists.

    A node that has not run yet leaves no evidence at its conventional path;
    that is ``pending``, not a projection failure.
    """
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=path,
            allow_replaced_run_details=allow_replaced_run_details,
        )
    except JournalArtifactMissingError:
        return None
    try:
        value = parse_json_bytes(raw)
    except ValueError as exc:
        raise ScoutProjectionError("present_payload_artifact_invalid", f"{path} is not JSON") from exc
    if not isinstance(value, dict):
        raise ScoutProjectionError("present_payload_artifact_invalid", f"{path} is not a JSON object")
    return value


def _read_run_input(*, resolved: Any, run_id: str) -> FindJobsRunInput:
    """Read the sealed find-jobs run input I-2 writes once at run start.

    This is the authoritative config/cap/rule/model-target/pinned-resume
    snapshot for the run; there is no fallback to re-reading the project's
    live ``find-jobs.json``, since a run must reflect exactly what it sealed.
    """
    path = f"runs/{run_id}/sealed/find-jobs-run-input.json"
    value = _read_run_json(resolved=resolved, path=path)
    if value is None:
        raise ScoutProjectionError("present_payload_run_input_missing", "the run's sealed find-jobs input has not been published")
    try:
        return FindJobsRunInput.from_json(value)
    except FindJobsContractError as exc:
        raise ScoutProjectionError("present_payload_run_input_invalid", "the run's sealed find-jobs input is malformed") from exc


# run-reads-fast (uat-bug-022): one run's committed evidence, read in one pass.
#
# ``build_present_payload`` used to read the run input, two outputs and three
# receipts with one ``read_committed_artifact`` each: about 10 git
# subprocesses per artifact, 60 per payload, and ``GET /api/runs`` built a
# payload per run on top of the one ``run_status`` builds. A run's directory
# is now read with ONE committed snapshot (about 17 subprocesses whatever the
# run holds, and one snapshot for several runs), and a finished run's
# evidence is kept: a sealed artifact has one publisher and never changes.
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "blocked", "cancelled", "interrupted"})
# A snapshot that meets a journal transition in flight is retried under the
# writer lock. A run read does not queue behind a long transition for it: the
# path-by-path read below takes no lock.
_RUN_EVIDENCE_LOCK_TIMEOUT_SECONDS = 1.0
# The runs a page is reading now: the one on screen and the one before it.
# ``GET /api/runs`` keeps its own small row per run (``api/runs_list.py``).
_RUN_EVIDENCE_CACHE_SIZE = 2
_RUN_EVIDENCE_CACHE_LOCK = threading.Lock()
# The Scout server's one log (``find_jobs/api/server.py``'s LOGGER_NAME).
_run_read_logger = logging.getLogger("gigai.scout.server")
_run_evidence_cache: "OrderedDict[tuple[str, str], tuple[str, RunEvidence]]" = OrderedDict()


@dataclass(frozen=True)
class RunEvidence:
    """What one find-jobs run has committed so far.

    ``details`` is the committed ``run-details.json``; a node that has not
    run yet has no output and no receipt, and ``run_input`` is ``None``
    until the run has sealed it.
    """

    run_id: str
    details: Mapping[str, object] | None
    run_input: FindJobsRunInput | None
    acquire_output: AcquireOutput | None
    assess_output: AssessOutput | None
    receipts: tuple[NodeReceipt, ...]

    @property
    def details_status(self) -> str | None:
        value = self.details.get("status") if self.details is not None else None
        return value if isinstance(value, str) else None

    @property
    def started_at(self) -> str | None:
        value = self.details.get("started_at") if self.details is not None else None
        return value if isinstance(value, str) else None

    @property
    def terminal(self) -> bool:
        return self.details_status in _TERMINAL_RUN_STATUSES

    @property
    def status(self) -> AggregateStatus:
        """The receipts' aggregate: what a payload's ``status`` is."""

        if not self.receipts:
            return AggregateStatus.PENDING
        return AggregateStatus(aggregate_status(receipt.status for receipt in self.receipts))


def working_run_details_digest(resolved: Any, run_id: str) -> str | None:
    """The digest of the run's ``run-details.json`` as it is on disk now.

    What a kept read of a finished run is filed under: a run whose details
    are replaced is read again. ``None`` when the file cannot be read.
    """

    path = resolved.path / "runs" / run_id / "run-details.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return digest_imported_bytes(path.read_bytes())
    except OSError:
        return None


def _committed_run_bytes(resolved: Any, path: str) -> bytes | None:
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=path,
            allow_replaced_run_details=True,
        )
    except JournalArtifactMissingError:
        return None
    return raw


def _snapshot_run_artifacts(resolved: Any, run_ids: Sequence[str]) -> Mapping[str, bytes] | None:
    """Every committed artifact of ``run_ids``, or ``None`` when no snapshot can be taken now."""

    try:
        return read_committed_snapshot(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            prefixes=tuple(f"runs/{run_id}/" for run_id in run_ids),
            lock_timeout_seconds=_RUN_EVIDENCE_LOCK_TIMEOUT_SECONDS,
        ).artifacts
    except JournalError as exc:
        # A run that is writing right now (a file on disk its commit has not
        # reached), or a busy writer lock: read path by path, as before.
        # Said in the log: the path-by-path read is the slow one.
        _run_read_logger.info(
            "run evidence is read path by path, no snapshot could be taken: runs=%s (%s)",
            ",".join(run_ids),
            type(exc).__name__,
        )
        return None


def _run_evidence_from(run_id: str, read: Callable[[str], bytes | None]) -> tuple[RunEvidence, str | None]:
    """``read``'s artifacts as one run's evidence, and the digest of its committed details."""

    def parsed(path: str) -> dict[str, object] | None:
        raw = read(path)
        if raw is None:
            return None
        try:
            value = parse_json_bytes(raw)
        except ValueError as exc:
            raise ScoutProjectionError("present_payload_artifact_invalid", f"{path} is not JSON") from exc
        if not isinstance(value, dict):
            raise ScoutProjectionError("present_payload_artifact_invalid", f"{path} is not a JSON object")
        return value

    def output(goal_slug: str, dto: type) -> Any | None:
        path = f"runs/{run_id}/outputs/{goal_slug}.json"
        value = parsed(path)
        if value is None:
            return None
        try:
            return dto.from_json(value)
        except FindJobsContractError as exc:
            raise ScoutProjectionError("present_payload_output_invalid", f"{path} is not a valid {dto.__name__}") from exc

    def receipt(goal_slug: str) -> NodeReceipt | None:
        path = f"runs/{run_id}/receipts/{goal_slug}.json"
        value = parsed(path)
        if value is None:
            return None
        try:
            return NodeReceipt.from_json(value)
        except FindJobsContractError as exc:
            raise ScoutProjectionError("present_payload_receipt_invalid", f"{path} is not a valid node receipt") from exc

    details_raw = read(f"runs/{run_id}/run-details.json")
    details: dict[str, object] | None = None
    if details_raw is not None:
        try:
            value = parse_json_bytes(details_raw)
        except ValueError:
            value = None
        details = value if isinstance(value, dict) else None

    run_input: FindJobsRunInput | None = None
    sealed = parsed(f"runs/{run_id}/sealed/find-jobs-run-input.json")
    if sealed is not None:
        try:
            run_input = FindJobsRunInput.from_json(sealed)
        except FindJobsContractError as exc:
            raise ScoutProjectionError("present_payload_run_input_invalid", "the run's sealed find-jobs input is malformed") from exc

    evidence = RunEvidence(
        run_id=run_id,
        details=details,
        run_input=run_input,
        acquire_output=output("acquire", AcquireOutput),
        assess_output=output("assess", AssessOutput),
        receipts=tuple(item for item in (receipt(slug) for slug in _GOAL_SLUGS) if item is not None),
    )
    return evidence, (digest_imported_bytes(details_raw) if details_raw is not None else None)


def _kept_run_evidence(resolved: Any, run_id: str, digest: str | None) -> RunEvidence | None:
    if digest is None:
        return None
    key = (str(resolved.path), run_id)
    with _RUN_EVIDENCE_CACHE_LOCK:
        kept = _run_evidence_cache.get(key)
        if kept is None or kept[0] != digest:
            return None
        _run_evidence_cache.move_to_end(key)
        return kept[1]


def _keep_run_evidence(resolved: Any, evidence: RunEvidence, digest: str) -> None:
    key = (str(resolved.path), evidence.run_id)
    with _RUN_EVIDENCE_CACHE_LOCK:
        _run_evidence_cache[key] = (digest, evidence)
        _run_evidence_cache.move_to_end(key)
        while len(_run_evidence_cache) > _RUN_EVIDENCE_CACHE_SIZE:
            _run_evidence_cache.popitem(last=False)


def read_runs_evidence(resolved: Any, run_ids: Sequence[str]) -> dict[str, RunEvidence]:
    """The committed evidence of each of ``run_ids``, with one snapshot for all of them.

    A run read while it is writing falls back to a snapshot of its own, then
    to the path-by-path read; the other runs keep the one snapshot's result.
    """

    found: dict[str, RunEvidence] = {}
    working: dict[str, str | None] = {}
    for run_id in dict.fromkeys(run_ids):
        digest = working_run_details_digest(resolved, run_id)
        kept = _kept_run_evidence(resolved, run_id, digest)
        if kept is not None:
            found[run_id] = kept
        else:
            working[run_id] = digest
    if not working:
        return found

    artifacts = _snapshot_run_artifacts(resolved, tuple(working))
    if artifacts is None and len(working) > 1:
        for run_id in working:
            found.update(read_runs_evidence(resolved, (run_id,)))
        return found
    for run_id, digest in working.items():
        read = artifacts.get if artifacts is not None else (lambda path: _committed_run_bytes(resolved, path))
        evidence, committed_digest = _run_evidence_from(run_id, read)
        # Kept only when what is on disk is what was committed: the key a
        # later read looks it up by is the file on disk.
        if evidence.terminal and digest is not None and digest == committed_digest:
            _keep_run_evidence(resolved, evidence, digest)
        found[run_id] = evidence
    return found


def read_run_evidence(resolved: Any, run_id: str) -> RunEvidence:
    """One run's committed evidence (``read_runs_evidence`` for one run)."""

    return read_runs_evidence(resolved, (run_id,))[run_id]


def present_payload_from(evidence: RunEvidence) -> PresentPayload:
    """The present-node payload of a run, from its committed evidence."""

    run_input = evidence.run_input
    if run_input is None:
        raise ScoutProjectionError("present_payload_run_input_missing", "the run's sealed find-jobs input has not been published")
    acquire_output = evidence.acquire_output
    assess_output = evidence.assess_output

    rows = acquire_output.rows if acquire_output is not None else ()
    failures = list(acquire_output.failures) if acquire_output is not None else []
    if assess_output is not None:
        failures.extend(assess_output.failures)

    assessments = assess_output.assessments if assess_output is not None else ()
    not_assessed = assess_output.not_assessed if assess_output is not None else ()
    pinned_resume = assess_output.pinned_resume if assess_output is not None else run_input.pinned_resume

    return PresentPayload(
        run_id=evidence.run_id,
        config=run_input.config,
        pinned_resume=pinned_resume,
        rows=tuple(rows),
        failures=tuple(failures),
        assessments=tuple(assessments),
        not_assessed=tuple(not_assessed),
        node_receipts=evidence.receipts,
        status=evidence.status,
    )


def build_present_payload(*, home_root: Path, target: Path | None, run_id: str, resolved: Any | None = None) -> PresentPayload:
    """Rebuild the present-node payload for one run from committed evidence.

    Snapshot-derived and rebuildable: every field comes from the run's sealed
    input plus each node's committed output/receipt at the I-2 storage
    convention (``runs/{run_id}/{outputs,receipts}/{acquire,assess,present}.json``).
    A goal that has not run yet is simply absent; this never mutates
    application/tracking state. ``resolved`` is the caller's own resolved
    workpad, when it has one already.
    """
    if resolved is None:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None)
    return present_payload_from(read_run_evidence(resolved, run_id))


def present_node(context: NodeContext, input: PresentInput, *, home_root: Path, target: Path | None) -> PresentOutput:
    """The ``present`` node: a rebuildable projection, never a tracking write.

    ``input.batch_ref``/``input.assessment_ref`` name the committed acquire and
    assess evidence for this run; ``input.node_receipts`` are the receipts
    already assembled by the caller (I-2/I-3) for the nodes that have run so
    far, including this node's own predecessors.  This node performs no
    application/tracking mutation of its own.
    """
    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None)
    run_input = _read_run_input(resolved=resolved, run_id=context.run_id)

    acquire = _read_run_json(resolved=resolved, path=input.batch_ref)
    if acquire is None:
        raise ScoutProjectionError("present_batch_ref_missing", "present_node's batch_ref has not been committed")
    try:
        acquire_output = AcquireOutput.from_json(acquire)
    except FindJobsContractError as exc:
        raise ScoutProjectionError("present_batch_ref_invalid", "present_node's batch_ref is not a valid AcquireOutput") from exc

    assess_output = None
    if input.assessment_ref is not None:
        assess = _read_run_json(resolved=resolved, path=input.assessment_ref)
        if assess is None:
            raise ScoutProjectionError("present_assessment_ref_missing", "present_node's assessment_ref has not been committed")
        try:
            assess_output = AssessOutput.from_json(assess)
        except FindJobsContractError as exc:
            raise ScoutProjectionError("present_assessment_ref_invalid", "present_node's assessment_ref is not a valid AssessOutput") from exc

    failures = list(acquire_output.failures)
    if assess_output is not None:
        failures.extend(assess_output.failures)
    pinned_resume = assess_output.pinned_resume if assess_output is not None else run_input.pinned_resume
    status = AggregateStatus(aggregate_status(receipt.status for receipt in input.node_receipts)) if input.node_receipts else AggregateStatus.PENDING

    payload = PresentPayload(
        run_id=context.run_id,
        config=run_input.config,
        pinned_resume=pinned_resume,
        rows=acquire_output.rows,
        failures=tuple(failures),
        assessments=assess_output.assessments if assess_output is not None else (),
        not_assessed=assess_output.not_assessed if assess_output is not None else (),
        node_receipts=input.node_receipts,
        status=status,
    )
    return PresentOutput(payload=payload, aggregate_status=status)


__all__ = [
    "ProjectionReader", "ScoutProjection", "ScoutProjectionError", "ScoutReaderSet",
    "RunEvidence", "build_present_payload", "present_node", "present_payload_from",
    "read_run_evidence", "read_runs_evidence", "working_run_details_digest",
    "projection_from_snapshot", "query_projection", "read_cached_projection", "rebuild_projection",
]
