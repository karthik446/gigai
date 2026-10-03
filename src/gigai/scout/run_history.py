"""0.1.10.7 M4a (DESIGN 10.2, 10.4, 10.7): old find-jobs runs as read-only history in the read model.

A find-jobs run is no longer the way in: searching is a live query over the
stored index, ranking a background lane, and assessing happens on approval.
The runs that exist stay where they are, read-only, and nothing of theirs is
rewritten or copied. What they ASSESSED is imported once into the project's
``pipeline.sqlite`` (``run_assessment``), so the per-(posting, profile) read
model shows it:

- one row per posting a run assessed itself (an assessment a run carried
  forward belongs to the run that made it, and is imported with that run);
- keyed ``(job, the run's sealed profile)``. A run sealed with NO profile
  goes to the pseudo-profile ``ephemeral``: hidden by default, never ranked,
  never in the pipeline (DESIGN 10.7);
- with the provenance the run sealed: the assess prompt version, the
  constraints digest, the story bank's digest, the profile's sealed identity
  (id, revision, digest), the pinned resume's identity, the posting's content
  digest and the model target. Ids, digests, counts and codes only: no
  posting, resume or answer text (the store checks every value's shape);
- "latest wins": the read model uses a run's assessment only where the
  quick-assess store has nothing newer for the same (profile, job).

IDEMPOTENT: a run is imported in one transaction with its ``run_import``
record, and a run that has one is skipped. A second migration imports
nothing and says so. A run that is not finished is left for a later
migration; a finished run that assessed nothing is recorded with no rows.

NO DATA LOSS: nothing is deleted or moved. A value a run sealed in a shape
the file does not hold (it holds no free text) is left out of the imported
row and counted (``values_dropped``); a posting whose identity is not a
public URL is not imported and counted (``rows_skipped``). Both are still in
the run's own record, which the run routes keep serving.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .pipeline.store import EPHEMERAL_PROFILE, PipelineStore, RunAssessment, fits, pipeline_path

SCHEMA_VERSION = "scout-run-history:1"

#: 0110-8-13: the name of the prompt of a run that sealed none. The assess prompt got its first name ("assess-prompt-v4")
#: together with the field a run seals it in (0110-034b, 0.1.10.4), so an output without the field was made by the wording
#: before that. A name, not a guess of the bytes; never one of the current versions, so such an assessment stays stale.
UNSEALED_PROMPT_VERSION = "assess-prompt-pre-v4"

_ASSESSED = "assessed"


class RunSource(Protocol):
    """Where the runs are read from: the journal by default; a test passes its own."""

    def run_ids(self) -> Sequence[str]: ...

    def evidence(self, run_ids: Sequence[str]) -> Mapping[str, object]: ...


class JournalRuns:
    """The project's committed find-jobs runs (``projection.read_runs_evidence``: one snapshot for all of them)."""

    def __init__(self, resolved: object) -> None:
        self._resolved = resolved

    def run_ids(self) -> list[str]:
        runs = Path(self._resolved.path) / "runs"  # type: ignore[attr-defined]
        try:
            return sorted(path.name for path in runs.glob("run_*") if (path / "run-details.json").is_file())
        except OSError:
            return []

    def evidence(self, run_ids: Sequence[str]) -> Mapping[str, object]:
        from .projection import read_runs_evidence

        return read_runs_evidence(self._resolved, tuple(run_ids))


def _state(verdict: object) -> str:
    from .find_jobs.job_state import _VERDICT_STATES

    return _VERDICT_STATES.get(str(getattr(verdict, "value", verdict)), _ASSESSED) if verdict is not None else _ASSESSED


def _kept(kind: str, value: object, dropped: list[int]) -> str | None:
    """``value`` when it has the shape a ``kind`` column holds; else ``None``, counted."""

    if value is None:
        return None
    if fits(kind, value):
        return value  # type: ignore[return-value]
    dropped[0] += 1
    return None


def run_rows(run_id: str, evidence: object) -> tuple[str, list[RunAssessment], int, int]:
    """``(profile_id, rows, rows_skipped, values_dropped)`` for one finished run. Pure: reads ``evidence`` only."""

    from .postings import stamp

    run_input = getattr(evidence, "run_input", None)
    profile_ref = getattr(run_input, "profile_ref", None)
    sealed_id = getattr(profile_ref, "profile_id", None)
    # A run sealed with no profile is nobody's: ``ephemeral``. Never the profile selected now (DESIGN 10.7).
    profile_id = EPHEMERAL_PROFILE
    if profile_ref is not None and isinstance(sealed_id, str) and fits("id", sealed_id):
        profile_id = sealed_id
    owned = profile_id != EPHEMERAL_PROFILE
    output = getattr(evidence, "assess_output", None)
    if output is None:
        return profile_id, [], 0, 0
    dropped = [0]
    pinned = output.pinned_resume
    bank = getattr(output, "story_bank", None)
    revision = getattr(profile_ref, "revision", None) if owned else None
    shared = {
        "assessed_at": stamp(getattr(evidence, "started_at", None)),
        # A version the run sealed in a shape the file does not hold is dropped and counted, as before; one it never
        # sealed is named.
        "prompt_version": (
            UNSEALED_PROMPT_VERSION if getattr(output, "prompt_version", None) is None
            else _kept("id", output.prompt_version, dropped)
        ),
        "constraints_digest": _kept("digest", getattr(output, "constraints_digest", None), dropped),
        "bank_digest": _kept("digest", getattr(bank, "bank_digest", None), dropped),
        "profile_revision": revision if type(revision) is int and revision >= 0 else None,
        "profile_digest": _kept("digest", getattr(profile_ref, "content_digest", None), dropped) if owned else None,
        "pinned_record": _kept("id", pinned.record_id, dropped),
        "pinned_revision": _kept("id", pinned.revision_id, dropped),
        "pinned_digest": _kept("digest", pinned.content_sha256, dropped),
        "model_target": _kept("id", getattr(output.model_target, "value", output.model_target), dropped),
        "adapter": _kept("id", output.producer.adapter, dropped),
    }
    rows: list[RunAssessment] = []
    skipped = 0
    for item in output.assessments:
        job = item.posting.normalized_url
        if not fits("job", job):
            skipped += 1
            continue
        rows.append(
            RunAssessment(
                run_id=run_id, job=job, profile_id=profile_id, state=_state(item.verdict),
                reqs_met=sum(1 for entry in item.matrix if entry.status.value == "met"), reqs_total=len(item.matrix),
                open_questions=len(item.structured_questions) or len(item.questions),
                listing_digest=_kept("digest", item.posting.content_sha256, dropped), **shared,  # type: ignore[arg-type]
            )
        )
    return profile_id, rows, skipped, dropped[0]


def migrate_runs(
    home_root: Path, target: Path, *, source: RunSource | None = None, store: PipelineStore | None = None
) -> dict[str, object]:
    """Import what every finished run assessed that is not imported yet; returns the counts (``scout-run-history:1``).

    Raises ``PostingModelError`` (``target_unavailable``) when the folder has
    no Scout gig. Reading the runs is the only cost: a second call reads no
    run and imports nothing.
    """

    home_root, target = Path(home_root), Path(target)
    if source is None:
        from ..workpad import resolve_workpad
        from .postings import PostingModelError

        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except Exception as exc:  # noqa: BLE001 - any failure to resolve the gig is "no Scout gig here", by its code
            raise PostingModelError("target_unavailable", "no Scout gig is available for this folder; run `gigai scout install`") from exc
        source = JournalRuns(resolved)
    found = [run_id for run_id in source.run_ids() if fits("id", run_id)]
    if not found and store is None and not pipeline_path(home_root, target).is_file():
        # No run and no pipeline file yet: a look never creates the file.
        return {
            "schema_version": SCHEMA_VERSION, "runs": 0, "runs_imported": 0, "runs_already_imported": 0, "runs_not_finished": 0,
            "runs_unreadable": 0, "assessments_imported": 0, "ephemeral_assessments": 0, "rows_skipped": 0, "values_dropped": 0,
            "by_profile": [], "imported": [],
        }
    opened = store if store is not None else PipelineStore(pipeline_path(home_root, target))
    try:
        already = opened.imported_runs()
        todo = [run_id for run_id in found if run_id not in already]
        counts = {
            "runs": len(found), "runs_imported": 0, "runs_already_imported": len(found) - len(todo), "runs_not_finished": 0,
            "runs_unreadable": 0, "assessments_imported": 0, "ephemeral_assessments": 0, "rows_skipped": 0, "values_dropped": 0,
        }
        by_profile: dict[str, int] = {}
        imported: list[dict[str, object]] = []
        try:
            evidence = source.evidence(todo) if todo else {}
        except Exception:  # noqa: BLE001 - runs that cannot be read are left for a later migration, never half-imported
            evidence, counts["runs_unreadable"] = {}, len(todo)
        for run_id in todo:
            item = evidence.get(run_id)
            if item is None:
                continue
            if not getattr(item, "terminal", False):
                counts["runs_not_finished"] += 1
                continue
            profile_id, rows, skipped, dropped = run_rows(run_id, item)
            written = opened.import_run(run_id, profile_id, rows)
            if written is None:  # another process imported it between the listing and this write
                counts["runs_already_imported"] += 1
                continue
            counts["runs_imported"] += 1
            counts["assessments_imported"] += written
            counts["rows_skipped"] += skipped
            counts["values_dropped"] += dropped
            if profile_id == EPHEMERAL_PROFILE:
                counts["ephemeral_assessments"] += written
            by_profile[profile_id] = by_profile.get(profile_id, 0) + written
            imported.append({"run_id": run_id, "profile_id": profile_id, "assessments": written})
        return {
            "schema_version": SCHEMA_VERSION,
            **counts,
            "by_profile": [{"profile_id": key, "assessments": by_profile[key]} for key in sorted(by_profile)],
            "imported": imported,
        }
    finally:
        if store is None:
            opened.close()


def basis_json(item: RunAssessment) -> dict[str, object]:
    """The provenance of one imported run assessment, as a response serves it: ids, digests and numbers."""

    return {
        "origin": f"run:{item.run_id}",
        "run_id": item.run_id,
        # A row imported before 0110-8-13 has an empty column for a run that sealed no version: the same name.
        "prompt_version": item.prompt_version or UNSEALED_PROMPT_VERSION,
        "prompt_sealed": item.prompt_version not in (None, UNSEALED_PROMPT_VERSION),
        "constraints_digest": item.constraints_digest,
        "story_bank_digest": item.bank_digest,
        "profile_ref": None if item.profile_id == EPHEMERAL_PROFILE else {
            "profile_id": item.profile_id, "revision": item.profile_revision, "content_digest": item.profile_digest,
        },
        "resume": {"record_id": item.pinned_record, "revision_id": item.pinned_revision, "content_sha256": item.pinned_digest},
        "posting_sha256": item.listing_digest,
        "model_target": item.model_target,
        "model": item.adapter,
    }


def history_row(item: RunAssessment, *, active: bool) -> dict[str, object]:
    """One imported run assessment as a history row. ``hidden``: not in the default view (no active profile owns it)."""

    return {
        "job_identity": item.job,
        "profile_id": item.profile_id,
        "state": item.state,
        "met": item.reqs_met,
        "requirements": item.reqs_total,
        "open_questions": item.open_questions,
        "assessed_at": item.assessed_at,
        "hidden": not active,
        "basis": basis_json(item),
    }


__all__ = ["SCHEMA_VERSION", "UNSEALED_PROMPT_VERSION", "JournalRuns", "RunSource", "basis_json", "history_row", "migrate_runs", "run_rows"]
