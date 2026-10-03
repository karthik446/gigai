"""P9c: ``GET /api/runs`` -- every find-jobs run for this target, newest
first, with per-run counts, for the dashboard's "last run"/"new since last
run" panels, the Profiles run-history table, and the Find-jobs past-run
picker (all dropped by P9a for lack of this route -- see
``orchestrator/workers/P9a-app-views.md``'s "Dropped fields").

Read-only. Runs are discovered the same way ``run_plan_already_handed_off``
already does (``run.py``'s own convention): a ``glob`` of ``runs/run_*`` on
the RESOLVED WORKPAD PATH, never a full journal replay to enumerate run ids
-- there is no committed index of "every run_id" to read instead.

run-reads-fast (uat-bug-022): this route built every run's results
(``run_status`` plus ``build_present_payload``, about 140 git subprocesses
a run) on every call; at 500 postings a run that was 16 s. It now reads
every run it has not seen with ONE committed snapshot
(``projection.read_runs_evidence``) and keeps a finished run's row (its
status, counts, profile and start time: a few hundred bytes), filed under
the digest of the run's ``run-details.json`` on disk. A later call reads
that small file for each run and nothing else; a run whose details are
replaced is read again. A run that is still going is read on every call,
its status from ``run_status`` as before. No results payload is built.

``?status=<status>`` keeps the runs with that status and ``?limit=N``
(1..500) the newest N of what the filters keep. With a limit the runs are
read newest first, a few at a time, until N are found: "the newest
succeeded run of this profile" (what the Jobs page opens on) reads that run
and not the history behind it.

``profile_id``: read from the run's own sealed
``runs/{run_id}/sealed/find-jobs-run-input.json`` (``FindJobsRunInput.
profile_ref.profile_id``, S25 F1-b, additive/optional). A run sealed before
F1-b shipped carries no ``profile_ref`` at all; for those LEGACY runs this
falls back to the gig's current ``selected_profile()`` (the F1-a migration
target every such gig has exactly one of) -- matching the task spec's own
wording, "legacy runs -> the migrated default profile."

Counts come from the run's own sealed outputs, ``outputs/acquire.json`` and
``outputs/assess.json``, counted where they are read (``run_reads.
run_counts``; no counts file is written): ``found``
= every acquired row (``AcquireOutput.rows``), ``new`` = rows whose
``outcome`` is ``RowOutcome.NEW``, ``assessed`` = every produced
assessment (``AssessOutput.assessments``), ``matched`` = assessments whose
``verdict`` is ``Verdict.MATCHED_ABOVE_THRESHOLD`` (a pre-P2 assessment
carries no ``verdict`` at all -- ``None`` never counts as matched).
"""

from __future__ import annotations

from http import HTTPStatus
import threading
from urllib.parse import parse_qs, urlsplit

from .common import reads_committed
from .run_reads import RunReadsRoutesMixin, _query_int, run_counts

RUNS_LIST_LIMIT_MAX = 500
# How many runs a limited list reads at a time while it looks for its N.
_LIMITED_READ_SIZE = 4

_RUN_ROW_CACHE_LOCK = threading.Lock()
# (workpad, run_id) -> (digest of run-details.json on disk, the run's row).
# Finished runs only: their sealed outputs have one publisher and never
# change. One small row per run this process has listed.
_run_row_cache: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}


def _run_ids_newest_first(resolved) -> list[str]:
    """Every ``run_*`` directory under this workpad's ``runs/`` -- newest
    (lexicographically greatest ``started_at``, tie-broken by run_id) first.

    Mirrors ``run.py``'s own ``(resolved.path / "runs").glob("run_*/...")``
    convention (see ``_reject_...``'s sibling check in that module) rather
    than inventing a second enumeration mechanism. A malformed or
    in-flight (not yet committed) run-details file is skipped, never
    raised -- an enumeration route must not 500 over one bad neighbor.
    """

    from ....canonical import parse_json_bytes

    runs_dir = resolved.path / "runs"
    if not runs_dir.is_dir():
        return []
    entries: list[tuple[str, str]] = []
    for run_dir in runs_dir.glob("run_*"):
        details_path = run_dir / "run-details.json"
        if details_path.is_symlink() or not details_path.is_file():
            continue
        try:
            details = parse_json_bytes(details_path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(details, dict):
            continue
        run_id = details.get("run_id")
        if not isinstance(run_id, str) or run_id != run_dir.name:
            continue
        started_at = details.get("started_at")
        entries.append((started_at if isinstance(started_at, str) else "", run_id))
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [run_id for _started_at, run_id in entries]


def _run_profile_id(*, resolved, run_id: str, default_profile_id: str | None) -> str | None:
    """This run's ``profile_ref.profile_id``, or the migrated default for a
    legacy run sealed before S25 F1-b (see module docstring)."""

    from ....journal import JournalArtifactMissingError, read_committed_artifact
    from ....canonical import parse_json_bytes
    from ..contracts import FindJobsContractError, FindJobsRunInput

    path = f"runs/{run_id}/sealed/find-jobs-run-input.json"
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=path,
        )
    except JournalArtifactMissingError:
        return default_profile_id
    try:
        run_input = FindJobsRunInput.from_json(parse_json_bytes(raw))
    except (ValueError, FindJobsContractError):
        return default_profile_id
    if run_input.profile_ref is not None:
        return run_input.profile_ref.profile_id
    return default_profile_id


def _run_row(evidence, *, status: str) -> dict[str, object]:
    """One run's row, but for the default a legacy run's ``profile_id`` falls back to."""

    profile_ref = evidence.run_input.profile_ref if evidence.run_input is not None else None
    return {
        "run_id": evidence.run_id,
        "created_at": evidence.started_at,
        "profile_id": profile_ref.profile_id if profile_ref is not None else None,
        "status": status,
        "counts": run_counts(evidence),
    }


def _finished_status(evidence) -> str:
    from ..contracts import AggregateStatus

    try:
        return AggregateStatus(evidence.details_status).value
    except ValueError:
        return evidence.status.value


def _run_rows(backend, resolved, run_ids: list[str]) -> list[dict[str, object]]:
    """Every listed run's row, reading only the runs this process has no row for."""

    from ...projection import read_runs_evidence, working_run_details_digest

    workpad = str(resolved.path)
    rows: dict[str, dict[str, object]] = {}
    unread: dict[str, str | None] = {}
    for run_id in run_ids:
        digest = working_run_details_digest(resolved, run_id)
        with _RUN_ROW_CACHE_LOCK:
            kept = _run_row_cache.get((workpad, run_id))
        if kept is not None and kept[0] == digest:
            rows[run_id] = kept[1]
        else:
            unread[run_id] = digest

    evidence_by_run = read_runs_evidence(resolved, tuple(unread)) if unread else {}
    for run_id, digest in unread.items():
        evidence = evidence_by_run[run_id]
        if evidence.terminal:
            row = _run_row(evidence, status=_finished_status(evidence))
            if digest is not None and digest == working_run_details_digest(resolved, run_id):
                with _RUN_ROW_CACHE_LOCK:
                    _run_row_cache[(workpad, run_id)] = (digest, row)
        else:
            try:
                status_response = backend.run_status(run_id)
            except LookupError:
                continue  # run-details vanished between the glob and this read -- skip, don't fail the whole list
            row = _run_row(evidence, status=status_response.status.value)
        rows[run_id] = row
    return [rows[run_id] for run_id in run_ids if run_id in rows]


class RunsListRoutesMixin(RunReadsRoutesMixin):
    """``Handler`` mixin: ``GET /api/runs``, and ``run_reads.py``'s run page reads.

    ``RunReadsRoutesMixin`` is composed through this class so the handler's
    own list of mixins (``server._make_handler``) is left as it is.
    """

    @reads_committed
    def _handle_get_runs_list(self) -> None:
        from ....workpad import resolve_workpad

        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return

        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        # A blank filter is no filter, as before; a blank limit is refused.
        filter_profile_id = (query.get("profile_id") or [None])[0] or None
        filter_status = (query.get("status") or [None])[0] or None
        include_deleted = (query.get("include_deleted") or [""])[0] in ("1", "true")
        try:
            limit = _query_int(query, "limit", minimum=1, maximum=RUNS_LIST_LIMIT_MAX)
        except ValueError:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                f"limit must be a whole number from 1 to {RUNS_LIST_LIMIT_MAX}",
            )
            return

        try:
            resolved = resolve_workpad(
                home_root=backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True,
            )
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return

        # 0110-047: runs of a deleted profile stay readable, but not in the default list
        # (they show when `profile_id` names that profile, or with include_deleted=true).
        deleted_ids = frozenset() if include_deleted else self._deleted_profile_ids(resolved)
        run_ids = _run_ids_newest_first(resolved)
        read_size = len(run_ids) if limit is None else max(limit, _LIMITED_READ_SIZE)
        default_profile_id: str | None = None
        default_read = False
        runs: list[dict[str, object]] = []
        for start in range(0, len(run_ids), max(read_size, 1)):
            for row in _run_rows(backend, resolved, run_ids[start : start + read_size]):
                profile_id = row["profile_id"]
                if profile_id is None:
                    if not default_read:
                        default_profile_id = self._default_profile_id(resolved)
                        default_read = True
                    profile_id = default_profile_id
                if filter_profile_id is not None and profile_id != filter_profile_id:
                    continue
                if profile_id in deleted_ids and profile_id != filter_profile_id:
                    continue
                if filter_status is not None and row["status"] != filter_status:
                    continue
                runs.append({**row, "profile_id": profile_id})
            if limit is not None and len(runs) >= limit:
                del runs[limit:]
                break

        self._write_json(HTTPStatus.OK, {"schema_version": "scout-runs-list-response:1", "runs": runs})

    def _deleted_profile_ids(self, resolved) -> frozenset[str]:
        from ...profile_records import ProfileRecordError, list_profiles

        try:
            return frozenset(item.profile_id for item in list_profiles(resolved) if item.state == "deleted")
        except ProfileRecordError:
            return frozenset()

    def _default_profile_id(self, resolved) -> str | None:
        """The migrated default profile: what a run sealed before F1-b ran against."""

        from ...profile_records import ProfileRecordError, selected_profile

        backend = self._backend
        try:
            selection = selected_profile(resolved, home_root=backend.home_root, target=backend.target)
        except ProfileRecordError:
            selection = None
        return selection.profile_id if selection is not None else None


__all__ = ["RunsListRoutesMixin"]
