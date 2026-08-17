"""Verify G21 manual occurrences and comparisons from an installed wheel."""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

from gigai.canonical import digest_imported_bytes
from gigai.comparison import compare_occurrences
from gigai.journal import JournalArtifact, record_transition
from gigai.lifecycle import approve_offline, create_offline
from gigai.occurrence import close_occurrence, declare_occurrence, mark_occurrence, trigger_occurrence
from gigai.review import materialize_review_bundle
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import SCHEMA_NAMES
from gigai.workpad import resolve_workpad


def _uuids():
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(100, 240))
    return lambda: next(values)


def _bundle(name: str, reference_id: str, source: bytes) -> tuple[dict[str, object], dict[str, bytes], str]:
    path = f"occurrences/inputs/{name}.csv"
    reference = {
        "reference_id": reference_id,
        "role": "input",
        "kind": "csv",
        "path": path,
        "media_type": "text/csv",
        "content_sha256": digest_imported_bytes(source),
        "size_bytes": len(source),
        "canonical_sha256": None,
        "provenance": {
            "source_kind": "local_file",
            "locator": f"fixture://{name}.csv",
            "acquired_at": "2026-08-10T12:00:00Z",
            "acquisition_method": "installed-g21-fixture",
            "source_revision": None,
        },
        "sensitivity": "public",
        "redaction_status": "not_required",
    }
    bundle = {
        "schema_version": "1.0",
        "bundle_id": f"bundle_{reference_id.removeprefix('ref_')}",
        "bundle_version": 1,
        "created_at": "2026-08-10T12:00:00Z",
        "created_by": {"kind": "operator", "id": "installed-g21"},
        "name": f"installed-{name}",
        "question": f"Verify installed {name} occurrence.",
        "references": [reference],
        "tool_requirements": None,
        "redaction_policy": {
            "mode": "local_only",
            "allowed_reference_ids": [reference_id],
            "policy_version": "installed-g21-v1",
            "detector_version": None,
        },
    }
    return bundle, {path: source}, f"manifests/review-bundles/{name}.json"


def main() -> int:
    if len(SCHEMA_NAMES) != 31:
        raise SystemExit(f"installed G21 schema inventory is {len(SCHEMA_NAMES)}, expected 31")
    with tempfile.TemporaryDirectory(prefix="gigai-g21-installed-") as directory:
        root = Path(directory)
        home = root / "home"
        target = root / "target"
        target.mkdir()
        run_setup(
            build_config(
                home_root=home,
                workpad_root=root / "workpads",
                editor_argv=("/usr/bin/true",),
                open_with_target=False,
            )
        )
        initialize_target(home_root=home, requested_target=target, uuid_factory=_uuids())
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="installed-g21",
            open_editor=False,
            uuid_factory=_uuids(),
        )
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=_uuids(),
        )
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
        bundles = (
            _bundle("market-state", "ref_12345678-1234-4234-9234-123456789abc", b"date,value\n2026-08-10,10\n"),
            _bundle("screener", "ref_22345678-1234-4234-9234-123456789abc", b"symbol,score\nABC,1\n"),
            _bundle("spreadsheet", "ref_32345678-1234-4234-9234-123456789abc", b"month,total\n2026-08,10\n"),
        )
        artifacts: list[JournalArtifact] = []
        paths: dict[str, str] = {}
        for bundle, objects, manifest_path in bundles:
            manifest_bytes = materialize_review_bundle(
                resolved.path, bundle, objects, manifest_relative_path=manifest_path
            )
            paths[bundle["name"]] = manifest_path
            artifacts.extend(JournalArtifact(path, payload) for path, payload in objects.items())
            artifacts.append(JournalArtifact(manifest_path, manifest_bytes))
        record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id="handoff_42345678-1234-4234-9234-123456789abc",
            transition="creation_started",
            body="Installed G21 fixture bundles prepared.",
            artifacts=tuple(artifacts),
        )

        first = declare_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, cadence="daily", occurrence_key="2026-08-10", snapshot_path=paths["installed-market-state"], uuid_factory=_uuids())
        first_run = trigger_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, occurrence_id=first.occurrence_id, wait=True, uuid_factory=_uuids())
        second = declare_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, cadence="daily", occurrence_key="2026-08-11", snapshot_path=paths["installed-market-state"], prior_occurrence_id=first.occurrence_id, uuid_factory=_uuids())
        second_run = trigger_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, occurrence_id=second.occurrence_id, wait=True, uuid_factory=_uuids())
        if first_run.state != "run_terminal" or second_run.state != "run_terminal":
            raise SystemExit("installed daily occurrence did not reach run_terminal")
        comparison, compared = compare_occurrences(home_root=home, requested_target=target, gig_id=created.gig_id, current_occurrence_id=second.occurrence_id, uuid_factory=_uuids())
        if comparison["result"] != "unchanged" or comparison["selected_winner"] is not None or compared.state != "closed":
            raise SystemExit("installed daily comparison was not a winnerless unchanged result")
        close_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, occurrence_id=first.occurrence_id)

        weekly = declare_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, cadence="weekly", occurrence_key="2026-w33", snapshot_path=paths["installed-screener"], uuid_factory=_uuids())
        if mark_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, occurrence_id=weekly.occurrence_id, state="missed", reason="installed external trigger was absent", outcome_actor={"kind": "operator", "id": "installed-g21"}).state != "missed":
            raise SystemExit("installed weekly missed state failed")
        monthly = declare_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, cadence="monthly", occurrence_key="2026-08", snapshot_path=paths["installed-spreadsheet"], uuid_factory=_uuids())
        if trigger_occurrence(home_root=home, requested_target=target, gig_id=created.gig_id, occurrence_id=monthly.occurrence_id, wait=True, uuid_factory=_uuids()).state != "run_terminal":
            raise SystemExit("installed monthly occurrence did not reach run_terminal")
    print("verified installed GigAI G21 daily, weekly, and monthly occurrences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
