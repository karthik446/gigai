"""Verify the G26 builder contract from an installed GigAI distribution."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile

from gigai.canonical import canonical_json_bytes
from gigai.lifecycle import approve_interview_session, build_interview_proposal, start_interview
from gigai.proposal_interview import answer_question
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


SHA = "sha256:" + "1" * 64
NOW = "2026-08-13T00:00:00Z"


def _artifact(path: str) -> dict[str, object]:
    return {"path": path, "content_sha256": SHA, "media_type": "text/plain", "size_bytes": 1}


def main() -> int:
    if len(SCHEMA_NAMES) != 30:
        raise SystemExit(f"installed G26 schema inventory is {len(SCHEMA_NAMES)}, expected 30")
    session = {
        "schema_version": "1.0",
        "record_version": 1,
        "session_id": "session_00000000-0000-4000-8000-000000000001",
        "project_id": "project_00000000-0000-4000-8000-000000000002",
        "gig_id": "gig_00000000-0000-4000-8000-000000000003",
        "request_kind": "create",
        "state": "clarify",
        "revision": 1,
        "parent_revision": None,
        "round": 1,
        "max_rounds": 8,
        "intent": {"text_artifact": _artifact("intent.txt"), "content_sha256": SHA, "answered_at": NOW, "actor": {"kind": "operator", "id": "installed-g26"}},
        "references": [],
        "questions": [{"question_id": "main-drive", "answer_type": "text", "required": True, "options": [], "depends_on": [], "rationale": "Define the drive.", "provenance": "fixture://g26"}],
        "answers": [],
        "model_selection": {"target_name": "offline-default", "endpoint_name": "offline", "model": "fixture-v1", "adapter": "deterministic", "readiness": "usable", "selection_actor": {"kind": "operator", "id": "installed-g26"}, "selection_digest": SHA},
        "policy": {"network": "local_only", "credential_reference": None, "budget": {"max_model_calls": 1, "max_tool_calls": 0, "max_tokens": 64, "max_cost": None, "currency": None, "max_wall_time_ms": 60000, "max_parallel_goals": 1}, "cancellation": "operator_or_timeout"},
        "accounting": {"model_calls": 0, "input_tokens": None, "output_tokens": None, "elapsed_ms": 0, "cost": None, "cost_currency": None},
        "draft": None,
        "terminal_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    report = validate_serialized_contract("gig-builder-session.schema.json", canonical_json_bytes(session))
    if not report.valid:
        raise SystemExit(f"installed G26 session contract failed: {report.as_dict()}")
    malformed = dict(session)
    malformed["state"] = "blocked"
    malformed["terminal_reason"] = None
    if validate_serialized_contract("gig-builder-session.schema.json", canonical_json_bytes(malformed)).valid:
        raise SystemExit("installed G26 blocked-session guard did not refuse a missing reason")
    with tempfile.TemporaryDirectory(prefix="gigai-g26-installed-") as raw_root:
        root = Path(raw_root)
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
        initialize_target(home_root=home, requested_target=target)
        started = start_interview(
            home_root=home,
            requested_target=target,
            name="installed-g26",
            request="Build a local review Gig.",
            reference_paths=(),
        )
        answered = answer_question(started.session, "scope", "Build a local review Gig.")
        built = build_interview_proposal(
            home_root=home,
            requested_target=target,
            start=started,
            session=answered,
            model_target="offline-default",
            reference_bytes={},
        )
        proposal = json.loads(
            (started.workpad / "manifests/gig-proposal.json").read_text(encoding="utf-8")
        )
        approved = approve_interview_session(
            home_root=home,
            requested_target=target,
            start=started,
            session=built,
            existing_proposal_id=proposal["proposal_id"],
        )
        if approved.state != "approved" or not (started.workpad / "manifests/active-gig-version.json").is_file():
            raise SystemExit("installed G26 builder replay did not reach approved state")
    print("verified installed GigAI G26 builder contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
