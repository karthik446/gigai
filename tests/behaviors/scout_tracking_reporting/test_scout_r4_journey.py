"""Synthetic public-path R4 journey checks.

The model transport is deterministic and offline; every durable row still goes
through the normal journal-backed Scout readers and writers.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai import run as run_module
from gigai.canonical import digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from gigai.journal import read_committed_artifact
from gigai.run import ProposalRunRequest, TailorRunRequest, launch_run, read_run_details
from gigai.scout.document_records import read_document_revision, read_final_selection
from gigai.scout.documents import FinalDocumentSelection
from gigai.scout.projection import rebuild_projection, query_projection
from gigai.scout.report import publish_report, read_current_report
from gigai.scout.report_readers import default_reader_set, opportunity_reader
from gigai.scout.proposal_records import read_proposal_revision
from gigai.scout.tailoring import encode_tailoring_bundle
from gigai.adapters.factory import resolve_model_adapter
from gigai.application_events import read_application, record_application
from gigai.cli import cli
from gigai.native_records import create_native_record, update_native_record

from tests.behaviors.scout_proposals_tools.test_scout_proposal_run import _consent, _request_inputs
from tests.behaviors.scout_proposals_tools.test_scout03_native_records import _experience
from tests.behaviors.scout_proposals_tools.test_scout_proposal_execution import _Transport, _config


MODEL = "qwen3.8:latest"
DIGEST = "sha256:" + "a" * 64


class _TailorTransport(httpx.BaseTransport):
    """Deterministic synthetic local runtime response; no network is used."""

    def __init__(self, output: bytes) -> None:
        self.output = output
        self.calls: list[str] = []
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/api/version":
            payload = {"version": "0.34.0"}
        elif request.url.path == "/api/tags":
            payload = {"models": [{"name": MODEL, "digest": DIGEST}]}
        elif request.url.path == "/api/chat":
            payload = {
                "model": MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": self.output.decode("utf-8")},
                "eval_count": 24,
            }
        else:
            payload = {"error": "unexpected"}
        return httpx.Response(200, json=payload, request=request)

    def close(self) -> None:
        self.closed = True


def test_r4_explicit_answer_and_document_actions_require_confirmation() -> None:
    """Consent gates fail before resolving a Gig or touching journal state."""
    runner = CliRunner()
    answer = runner.invoke(cli, [
        "scout-answer", "save", "--record-id", "record_missing",
        "--parent-revision", "revision_missing", "--question-id", "q",
        "--answer-file", "/tmp/missing-answer", "--operation-key", "answer",
        "--gig", "gig_missing", "--json",
    ])
    assert answer.exit_code != 0
    assert "confirm" in answer.output.lower()
    selection = runner.invoke(cli, [
        "scout-documents", "final-select", "--document", "{}",
        "--run-id", "run_missing", "--goal-id", "goal_missing",
        "--invocation-id", "inv_missing", "--output-sha256", "sha256:" + "a" * 64,
        "--operation-key", "selection", "--gig", "gig_missing", "--json",
    ])
    assert selection.exit_code != 0
    assert "confirm" in selection.output.lower()


def test_r4_real_proposal_to_tailor_journey_persists_exact_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use normal discovery/private setup and Run allocation, never reactivation."""
    resolved, posting, selectors = _request_inputs(tmp_path)
    # Tailor selects native saved experience/preferences explicitly; the
    # helper's imported G45 text remains part of the historical fixture but
    # is not smuggled into another purpose for this lane.
    selectors = selectors[1:]
    # First produce a real immutable local proposal revision through the
    # supported proposal Graph/Run entry.  This keeps Tailor's optional
    # proposal association grounded in an actual prior invocation.
    proposal_transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": proposal_transport}
        ),
    )
    proposal_run = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert proposal_run.status == "succeeded"
    proposal_records = list(
        (resolved.path / "records/scout-proposals").glob("*/revisions/*.json")
    )
    assert len(proposal_records) == 1
    proposal_value = json.loads(proposal_records[0].read_text(encoding="utf-8"))
    proposal_selector = {
        "record_id": proposal_value["record_id"],
        "revision_id": proposal_value["revision_id"],
    }
    loaded_proposal = read_proposal_revision(
        resolved=resolved,
        record_id=proposal_selector["record_id"],
        revision_id=proposal_selector["revision_id"],
    )
    assert loaded_proposal["record_id"] == proposal_selector["record_id"]

    bundle = encode_tailoring_bundle(
        {
            "resume": b"# Resume\n\n## Experience\nBuilt synthetic Python tools.\n",
            "cover_letter": b"# Cover Letter\n\nHello synthetic team.\n",
        }
    )
    transport = _TailorTransport(bundle)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        tailor_execution=TailorRunRequest(
            graph_selector="tailor-application",
            model_target="local-proposal",
            posting_selector=posting,
                private_selectors=selectors,
                requested_outputs=("resume", "cover_letter"),
                proposal_selector=proposal_selector,
            ),
    )
    assert result.status == "succeeded"
    assert transport.closed
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "succeeded"
    assert len(details["goal_sets"]["complete"]) == 1
    invocation_paths = list((result.run_path / "model-invocations").glob("*/record.json"))
    assert len(invocation_paths) == 1
    assert any(
        path.name == "result.json" for path in (result.run_path / "scout-tailor").iterdir()
    )
    document_records = list((resolved.path / "records/scout-documents").glob("*/revisions/*/record.json"))
    assert len(document_records) == 2
    for record_path in document_records:
        value = json.loads(record_path.read_text(encoding="utf-8"))
        loaded = read_document_revision(
            resolved=resolved,
            record_id=value["record_id"],
            revision_id=value["revision_id"],
            document_kind=value["document_kind"],
        )
        assert loaded.content_sha256 == value["content_sha256"]
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert "tailor_invocation_recorded" in transitions
    assert transitions[-1] == "run_succeeded"


def test_r4_malformed_tailor_output_fails_after_preserving_invocation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed local call with an invalid bundle never becomes a document success."""
    resolved, posting, selectors = _request_inputs(tmp_path)
    selectors = selectors[1:]
    # Treat the answered experience revision as an explicit answer source so
    # the host records its question association rather than inferring one
    # from the model output.
    selectors = ({**selectors[0], "purpose": "answer"}, *selectors[1:])
    transport = _TailorTransport(b"model reasoning without the requested bundle")
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        tailor_execution=TailorRunRequest(
            graph_selector="tailor-application",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
            requested_outputs=("resume",),
        ),
    )
    assert result.status == "failed"
    assert transport.closed
    assert not (result.run_path / "scout-tailor" / "result.json").exists()
    assert len(list((result.run_path / "model-invocations").glob("*/record.json"))) == 1
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "failed"
    assert details["goal_sets"]["failed"]


def test_r4_full_tailor_report_application_journey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complete the public synthetic journey and reload it from journal authority."""
    resolved, posting, selectors = _request_inputs(tmp_path)
    missing_answer = create_native_record(
        home_root=tmp_path / "home", requested_target=tmp_path / "target",
        gig_id=resolved.gig_id, content=_experience(answered=False),
        actor={"kind": "operator", "id": "synthetic-user"}, origin="user_reported",
        operation_key="r4-answer-question",
    )
    answer_file = tmp_path / "synthetic-answer.txt"
    answer_file.write_text("Built reproducible synthetic test harnesses.", encoding="utf-8")
    answer_cli = CliRunner().invoke(
        cli,
        ["scout-answer", "save", "--record-id", missing_answer.record_id,
         "--parent-revision", missing_answer.revision_id, "--question-id", "leadership-01",
         "--answer-file", str(answer_file), "--operation-key", "r4-answer-save", "--confirm",
         "--gig", resolved.gig_id, "--home", str(tmp_path / "home"),
         "--target", str(tmp_path / "target"), "--json"],
    )
    assert answer_cli.exit_code == 0, answer_cli.output
    answer_result = json.loads(answer_cli.output)
    selectors = (
        {"purpose": "experience", "selector": {"family": "scout_record", "record_id": answer_result["record_id"], "revision_id": answer_result["revision_id"], "scope": {"mode": "saved_default", "task_context_id": None}}},
        selectors[2],
    )
    # The answered native revision is selected with an explicit answer
    selectors = ({**selectors[0], "purpose": "answer"}, *selectors[1:])
    proposal_transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": proposal_transport}
        ),
    )
    proposal_run = launch_run(
        home_root=tmp_path / "home", requested_target=tmp_path / "target",
        gig_id=resolved.gig_id, wait=True, operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment", model_target="local-proposal",
            posting_selector=posting, private_selectors=selectors,
        ),
    )
    assert proposal_run.status == "succeeded"
    proposal_path = next((resolved.path / "records/scout-proposals").glob("*/revisions/*.json"))
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    first_proposal_ref = {"record_id": proposal["record_id"], "revision_id": proposal["revision_id"]}
    first_record = read_proposal_revision(resolved=resolved, **first_proposal_ref)
    assert first_record["assessment"]["status"] == "complete"
    assert first_record["answer_associations"]

    # Reassess after a committed preference revision.  The first immutable
    # proposal remains readable and retains its original input digest.
    profile_selector = selectors[1]["selector"]
    changed_profile = __import__("tests.behaviors.scout_proposals_tools.test_scout03_native_records", fromlist=["_profile"])._profile()
    changed_profile["payload"]["hard_constraints"]["geography"]["value"] = "Boulder"
    changed = update_native_record(
        home_root=tmp_path / "home", requested_target=tmp_path / "target",
        gig_id=resolved.gig_id, record_id=profile_selector["record_id"],
        parent_revision=profile_selector["revision_id"], content=changed_profile,
        actor={"kind": "operator", "id": "synthetic-user"}, origin="user_reported",
        operation_key="r4-preference-reassessment",
    )
    reassessed_selectors = (
        selectors[0],
        {"purpose": "preferences", "selector": {**profile_selector, "revision_id": changed.revision_id}},
    )
    reassessed_run = launch_run(
        home_root=tmp_path / "home", requested_target=tmp_path / "target",
        gig_id=resolved.gig_id, wait=True, operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment", model_target="local-proposal",
            posting_selector=posting, private_selectors=reassessed_selectors,
        ),
    )
    assert reassessed_run.status == "succeeded"
    reassessed_path = next(
        path for path in (resolved.path / "records/scout-proposals").glob("*/revisions/*.json")
        if path != proposal_path
    )
    reassessed = json.loads(reassessed_path.read_text(encoding="utf-8"))
    proposal_ref = {"record_id": reassessed["record_id"], "revision_id": reassessed["revision_id"]}
    assert reassessed["input_revisions"] != first_record["input_revisions"]
    assert read_proposal_revision(resolved=resolved, **first_proposal_ref)["input_revisions"] == first_record["input_revisions"]

    bundle = encode_tailoring_bundle({
        "resume": b"# Resume\n\n## Experience\nBuilt synthetic Python tools.\n",
        "cover_letter": b"# Cover Letter\n\nHello synthetic team.\n",
    })
    tailor_transport = _TailorTransport(bundle)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": tailor_transport}
        ),
    )
    tailor = launch_run(
        home_root=tmp_path / "home", requested_target=tmp_path / "target",
        gig_id=resolved.gig_id, wait=True, operator_consent=_consent(),
        tailor_execution=TailorRunRequest(
            graph_selector="tailor-application", model_target="local-proposal",
            posting_selector=posting, private_selectors=selectors,
            requested_outputs=("resume", "cover_letter"), proposal_selector=proposal_ref,
        ),
    )
    assert tailor.status == "succeeded"
    tailor_result_bytes, _ = read_committed_artifact(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        path=f"runs/{tailor.run_id}/scout-tailor/result.json",
    )
    tailor_result = parse_json_bytes(tailor_result_bytes)
    assert isinstance(tailor_result, dict)
    actual_provenance = {
        "authority": "tailor_run", "run_id": tailor.run_id,
        "goal_id": tailor_result["goal_id"],
        "invocation_id": tailor_result["invocation_id"],
        "output_sha256": tailor_result["output_sha256"],
    }
    document_paths = list((resolved.path / "records/scout-documents").glob("*/revisions/*/record.json"))
    assert len(document_paths) == 2
    documents = []
    for path in document_paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        documents.append(read_document_revision(
            resolved=resolved, record_id=value["record_id"], revision_id=value["revision_id"],
            document_kind=value["document_kind"],
        ))
    selection = FinalDocumentSelection(
        documents[0].opportunity_id, documents[0].snapshot_id, tuple(documents), "synthetic-user"
    )
    selection_args: list[str] = []
    for item in documents:
        selection_args.extend(("--document", json.dumps({
            "document_kind": item.document_kind, "record_id": item.record_id,
            "revision_id": item.revision_id,
        }, sort_keys=True, separators=(",", ":"))))
    selection_cli = CliRunner().invoke(
        cli,
        ["scout-documents", "final-select", *selection_args,
         "--run-id", str(actual_provenance["run_id"]), "--goal-id", str(actual_provenance["goal_id"]),
         "--invocation-id", str(actual_provenance["invocation_id"]), "--output-sha256", str(actual_provenance["output_sha256"]),
         "--operation-key", "r4-final-selection", "--selected-by", "synthetic-user", "--confirm",
         "--gig", resolved.gig_id, "--home", str(tmp_path / "home"), "--target", str(tmp_path / "target"), "--json"],
    )
    assert selection_cli.exit_code == 0, selection_cli.output
    selected_payload = json.loads(selection_cli.output)
    assert selected_payload["status"] == "recorded"
    persisted_selection = read_final_selection(
        resolved=resolved, opportunity_id=selection.opportunity_id, snapshot_id=selection.snapshot_id
    )
    assert selected_payload["selection"] == persisted_selection
    assert persisted_selection["opportunity"] == {
        "opportunity_id": selection.opportunity_id,
        "snapshot_id": selection.snapshot_id,
    }
    expected_selected_documents = {
        (item.document_kind, item.record_id, item.revision_id, item.content_sha256)
        for item in documents
    }
    persisted_documents = persisted_selection["documents"]
    assert isinstance(persisted_documents, list)
    assert {
        (
            item["document_kind"],
            item["record_id"],
            item["revision_id"],
            item["content_sha256"],
        )
        for item in persisted_documents
    } == expected_selected_documents

    application = record_application(
        resolved=resolved,
        data={
            "operation_key": "r4-application-saved",
            "opportunity_ref": selection.opportunity_id,
            "event_kind": "saved",
            "occurred_at": "2026-09-11T12:00:00-06:00",
            "timezone": "America/Denver",
            "document_refs": [
                {
                    "kind": "scout_document", "record_id": item.record_id,
                    "revision_id": item.revision_id, "document_kind": item.document_kind,
                    "content_sha256": item.content_sha256,
                    "opportunity_ref": item.opportunity_id, "snapshot_id": item.snapshot_id,
                    "tailor_run_id": actual_provenance["run_id"],
                    "tailor_goal_id": actual_provenance["goal_id"],
                    "tailor_invocation_id": actual_provenance["invocation_id"],
                }
                for item in documents
            ],
            "notes": "synthetic explicit application intent",
        },
        confirm=True, opportunity_reader=opportunity_reader(resolved),
    )
    assert application["status"] == "recorded"
    applied_data = {
        "operation_key": "r4-application-applied",
        "opportunity_ref": selection.opportunity_id,
        "event_kind": "applied",
        "occurred_at": "2026-09-11T12:30:00-06:00",
        "timezone": "America/Denver",
        "document_refs": application["event"]["document_refs"],
        "notes": "synthetic explicit applied intent",
        "supersedes": application["event"]["event_id"],
    }
    applied = record_application(
        resolved=resolved, data=applied_data, confirm=True,
        opportunity_reader=opportunity_reader(resolved),
    )
    assert applied["status"] == "recorded"
    retried = record_application(
        resolved=resolved, data=applied_data, confirm=True,
        opportunity_reader=opportunity_reader(resolved),
    )
    assert retried["status"] == "already_recorded"
    correction = record_application(
        resolved=resolved,
        data={
            "operation_key": "r4-application-correction",
            "opportunity_ref": selection.opportunity_id,
            "event_kind": "rejected",
            "occurred_at": "2026-09-11T13:00:00-06:00",
            "timezone": "America/Denver", "document_refs": application["event"]["document_refs"],
            "notes": "synthetic correction",
            "supersedes": applied["event"]["event_id"],
        },
        confirm=True, opportunity_reader=opportunity_reader(resolved),
    )
    assert correction["status"] == "recorded"
    assert read_application(resolved=resolved, opportunity_ref=selection.opportunity_id)["current_status"] == "rejected"

    projection = rebuild_projection(resolved=resolved, readers=default_reader_set(resolved))
    assert projection.proposals and projection.documents and projection.applications
    opportunity_rows = [
        item
        for item in projection.opportunities
        if item.get("opportunity_id") == selection.opportunity_id
        and item.get("snapshot_id") == selection.snapshot_id
    ]
    assert len(opportunity_rows) == 1
    assert {
        (item.get("opportunity_id"), item.get("snapshot_id"))
        for item in projection.proposals
    } == {(selection.opportunity_id, selection.snapshot_id)}
    proposal_identity_rows = {
        (item.get("record_id"), item.get("revision_id"))
        for item in projection.proposals
    }
    assert proposal_identity_rows == {
        (first_proposal_ref["record_id"], first_proposal_ref["revision_id"]),
        (proposal_ref["record_id"], proposal_ref["revision_id"]),
    }
    selected_projection_documents = {
        (
            item.get("document_kind"),
            item.get("record_id"),
            item.get("revision_id"),
            item.get("content_sha256"),
        )
        for item in projection.documents
        if item.get("selected") is True
    }
    assert selected_projection_documents == expected_selected_documents
    local_run_rows = {
        str(item.get("run_id")): item
        for item in projection.runs
        if item.get("kind") == "scout-local"
    }
    for run in (proposal_run, reassessed_run, tailor):
        assert run.run_id in local_run_rows
        assert local_run_rows[run.run_id]["status"] == "succeeded"
        assert local_run_rows[run.run_id]["path"] == f"runs/{run.run_id}/run-details.json"
    proposal_result_paths = [
        path.relative_to(resolved.path).as_posix()
        for run in (proposal_run, reassessed_run)
        for path in (run.run_path / "scout-proposals").glob("*/result.json")
    ]
    tailor_result_path = f"runs/{tailor.run_id}/scout-tailor/result.json"
    assert len(proposal_result_paths) == 2
    for result_path in [*proposal_result_paths, tailor_result_path]:
        run_id = result_path.split("/", 2)[1]
        assert result_path in local_run_rows[run_id]["result_paths"]
        result_bytes, _result_publisher = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=result_path,
        )
        result_ref = {
            "path": result_path,
            "content_sha256": digest_imported_bytes(result_bytes),
            "size_bytes": len(result_bytes),
        }
        details_value = parse_json_bytes(
            (resolved.path / str(local_run_rows[run_id]["path"])).read_bytes()
        )
        assert isinstance(details_value, dict)
        goals = details_value["goals"]
        assert isinstance(goals, list)
        assert any(
            isinstance(goal.get("evidence"), list)
            and any(
                evidence.get("path") == result_ref["path"]
                and evidence.get("content_sha256") == result_ref["content_sha256"]
                and evidence.get("size_bytes") == result_ref["size_bytes"]
                for evidence in goal["evidence"]
                if isinstance(evidence, dict)
            )
            for goal in goals
            if isinstance(goal, dict)
        )
        matching_evidence = [
            item
            for item in projection.evidence
            if item.get("run_id") == run_id and item.get("path") == result_path
        ]
        assert len(matching_evidence) == 1
        assert matching_evidence[0]["status"] == "reported"
    assert query_projection(projection).execute("select count(*) from documents").fetchone()[0] >= 2
    report = publish_report(resolved=resolved, projection=projection)
    assert Path(report["path"]).is_file()
    fresh = __import__("gigai.workpad", fromlist=["resolve_workpad"]).resolve_workpad(
        home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=resolved.gig_id,
        allow_semantic_state=True,
    )
    current = read_current_report(resolved=fresh)
    assert current["stale"] is False
    html = Path(current["resolved_path"]).read_bytes()
    assert b"Proposal content" in html
    assert b"Application history" in html
    assert b"Documents and checks" in html
    assert b"rejected" in html

    # Exercise the public report/application command mapping against the same
    # journal-backed workpad; records above were still written through their
    # authenticated service APIs so this does not turn the journey into a
    # canned CLI fixture.
    report_status = CliRunner().invoke(
        cli,
        ["scout-report", "status", "--gig", resolved.gig_id,
         "--home", str(tmp_path / "home"), "--target", str(tmp_path / "target"), "--json"],
    )
    assert report_status.exit_code == 0, report_status.output
    assert json.loads(report_status.output)["stale"] is False
    app_history = CliRunner().invoke(
        cli,
        ["application", "history", "--gig", resolved.gig_id, "--opportunity", selection.opportunity_id,
         "--home", str(tmp_path / "home"), "--target", str(tmp_path / "target"), "--json"],
    )
    assert app_history.exit_code == 0, app_history.output
    assert json.loads(app_history.output)["current_status"] == "rejected"
