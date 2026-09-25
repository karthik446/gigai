"""Journal-derived Scout tracker views.

This module is deliberately a reader.  The private Git journal and committed
record artifacts are authority; ``state.sqlite`` is only a disposable cache of
the value returned here.  The small reader interfaces make the R0 boundary
explicit and let R1/R2 supply their reviewed readers without teaching the
tracker to trust model-owned identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable, Mapping
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol

from ..application_events import ApplicationEventError, validate_application_links
from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from ..journal import JournalArtifactMissingError, JournalSnapshot, read_committed_artifact, run_with_journal_writer
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
from ..workpad import resolve_workpad


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


def rebuild_projection(*, resolved: Any, readers: ScoutReaderSet | None = None) -> ScoutProjection:
    """Build from authority and cache only the closed existing Scout rows."""
    if readers is None:
        # Production report generation uses committed R1/R2/Run readers bound
        # to this resolved Gig.  ``projection_from_snapshot`` intentionally
        # keeps its empty default so injected fixtures remain explicit.
        from .report_readers import default_reader_set
        readers = default_reader_set(resolved)

    def read(writer):
        # Handoff text is journal metadata, not a standalone artifact and has
        # no self-reference entry. Readers derive sequence from the immutable
        # records below; excluding it avoids treating a handoff as a payload.
        snapshot = writer.snapshot(("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/"))
        return projection_from_snapshot(snapshot=snapshot, project_id=resolved.project_id, gig_id=resolved.gig_id, readers=readers)

    projection = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=read)
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


def _read_node_receipt(*, resolved: Any, run_id: str, goal_slug: str) -> NodeReceipt | None:
    path = f"runs/{run_id}/receipts/{goal_slug}.json"
    value = _read_run_json(resolved=resolved, path=path)
    if value is None:
        return None
    try:
        return NodeReceipt.from_json(value)
    except FindJobsContractError as exc:
        raise ScoutProjectionError("present_payload_receipt_invalid", f"{path} is not a valid node receipt") from exc


def _read_node_output(*, resolved: Any, run_id: str, goal_slug: str, dto: type) -> Any | None:
    path = f"runs/{run_id}/outputs/{goal_slug}.json"
    value = _read_run_json(resolved=resolved, path=path)
    if value is None:
        return None
    try:
        return dto.from_json(value)
    except FindJobsContractError as exc:
        raise ScoutProjectionError("present_payload_output_invalid", f"{path} is not a valid {dto.__name__}") from exc


def build_present_payload(*, home_root: Path, target: Path | None, run_id: str) -> PresentPayload:
    """Rebuild the present-node payload for one run from committed evidence.

    Snapshot-derived and rebuildable: every field comes from the run's sealed
    input plus each node's committed output/receipt at the I-2 storage
    convention (``runs/{run_id}/{outputs,receipts}/{acquire,assess,present}.json``).
    A goal that has not run yet is simply absent; this never mutates
    application/tracking state.
    """
    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None)
    run_input = _read_run_input(resolved=resolved, run_id=run_id)

    acquire_output = _read_node_output(resolved=resolved, run_id=run_id, goal_slug="acquire", dto=AcquireOutput)
    assess_output = _read_node_output(resolved=resolved, run_id=run_id, goal_slug="assess", dto=AssessOutput)

    rows = acquire_output.rows if acquire_output is not None else ()
    failures = list(acquire_output.failures) if acquire_output is not None else []
    if assess_output is not None:
        failures.extend(assess_output.failures)

    assessments = assess_output.assessments if assess_output is not None else ()
    not_assessed = assess_output.not_assessed if assess_output is not None else ()
    pinned_resume = assess_output.pinned_resume if assess_output is not None else run_input.pinned_resume

    receipts = tuple(
        receipt
        for receipt in (
            _read_node_receipt(resolved=resolved, run_id=run_id, goal_slug=slug)
            for slug in _GOAL_SLUGS
        )
        if receipt is not None
    )
    status = AggregateStatus(aggregate_status(receipt.status for receipt in receipts)) if receipts else AggregateStatus.PENDING

    return PresentPayload(
        run_id=run_id,
        config=run_input.config,
        pinned_resume=pinned_resume,
        rows=tuple(rows),
        failures=tuple(failures),
        assessments=tuple(assessments),
        not_assessed=tuple(not_assessed),
        node_receipts=receipts,
        status=status,
    )


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
    "build_present_payload", "present_node",
    "projection_from_snapshot", "query_projection", "read_cached_projection", "rebuild_projection",
]
