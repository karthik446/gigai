"""P9c: ``GET /api/runs`` -- every find-jobs run for this target, newest
first, with per-run counts, for the dashboard's "last run"/"new since last
run" panels, the Profiles run-history table, and the Find-jobs past-run
picker (all dropped by P9a for lack of this route -- see
``orchestrator/workers/P9a-app-views.md``'s "Dropped fields").

Read-only. Runs are discovered the same way ``run_plan_already_handed_off``
already does (``run.py``'s own convention): a ``glob`` of ``runs/run_*`` on
the RESOLVED WORKPAD PATH, never a full journal replay to enumerate run ids
-- there is no committed index of "every run_id" to read instead. Each
run's own fields then come from exactly the same calls ``GET
/api/runs/{run_id}``/``.../results`` already make
(``self._backend.run_status``/``self._backend.run_results``via
``build_present_payload``) -- this route adds no new replay depth per run,
per the task's own "never replays the journal per run beyond what the
existing run routes already do" rule.

``profile_id``: read from the run's own sealed
``runs/{run_id}/sealed/find-jobs-run-input.json`` (``FindJobsRunInput.
profile_ref.profile_id``, S25 F1-b, additive/optional). A run sealed before
F1-b shipped carries no ``profile_ref`` at all; for those LEGACY runs this
falls back to the gig's current ``selected_profile()`` (the F1-a migration
target every such gig has exactly one of) -- matching the task spec's own
wording, "legacy runs -> the migrated default profile."

Counts come from the run's own sealed outputs (never re-derived): ``found``
= every acquired row (``AcquireOutput.rows``), ``new`` = rows whose
``outcome`` is ``RowOutcome.NEW``, ``assessed`` = every produced
assessment (``AssessOutput.assessments``), ``matched`` = assessments whose
``verdict`` is ``Verdict.MATCHED_ABOVE_THRESHOLD`` (a pre-P2 assessment
carries no ``verdict`` at all -- ``None`` never counts as matched).
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit


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


def _run_counts(payload) -> dict[str, int]:
    from ..contracts import RowOutcome, Verdict

    found = len(payload.rows)
    new = sum(1 for row in payload.rows if row.outcome == RowOutcome.NEW)
    assessed = len(payload.assessments)
    matched = sum(1 for item in payload.assessments if item.verdict == Verdict.MATCHED_ABOVE_THRESHOLD)
    return {"found": found, "new": new, "assessed": assessed, "matched": matched}


class RunsListRoutesMixin:
    """``Handler`` mixin: ``GET /api/runs``."""

    def _handle_get_runs_list(self) -> None:
        from ....workpad import resolve_workpad
        from ...profile_records import ProfileRecordError, selected_profile
        from ...projection import build_present_payload

        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return

        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        filter_profile_id = (query.get("profile_id") or [None])[0]

        try:
            resolved = resolve_workpad(
                home_root=backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True,
            )
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return

        try:
            default_selection = selected_profile(resolved, home_root=backend.home_root, target=target)
        except ProfileRecordError:
            default_selection = None
        default_profile_id = default_selection.profile_id if default_selection is not None else None

        runs: list[dict[str, object]] = []
        for run_id in _run_ids_newest_first(resolved):
            try:
                status_response = backend.run_status(run_id)
            except LookupError:
                continue  # run-details vanished between the glob and this read -- skip, don't fail the whole list
            profile_id = _run_profile_id(resolved=resolved, run_id=run_id, default_profile_id=default_profile_id)
            if filter_profile_id is not None and profile_id != filter_profile_id:
                continue
            try:
                payload = build_present_payload(home_root=backend.home_root, target=target, run_id=run_id)
            except LookupError:
                continue
            created_at = None
            details_path = resolved.path / "runs" / run_id / "run-details.json"
            try:
                from ....canonical import parse_json_bytes

                details = parse_json_bytes(details_path.read_bytes())
                if isinstance(details, dict) and isinstance(details.get("started_at"), str):
                    created_at = details["started_at"]
            except (OSError, ValueError):
                created_at = None
            runs.append({
                "run_id": run_id,
                "created_at": created_at,
                "profile_id": profile_id,
                "status": status_response.status.value,
                "counts": _run_counts(payload),
            })

        self._write_json(HTTPStatus.OK, {"schema_version": "scout-runs-list-response:1", "runs": runs})


__all__ = ["RunsListRoutesMixin"]
