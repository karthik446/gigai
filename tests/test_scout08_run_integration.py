"""Real v2 external-recording tailoring flow over imported G45 inputs."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.private_records import import_reference, import_run_input
from gigai.scout_tailoring import encode_tailoring_bundle
from gigai.workpad import resolve_workpad

from tests.test_scout07_discovery_run_flow import _envelope, _fixture


def _request(*, outputs: list[str], posting: bytes, candidate: bytes, resume: bytes | None = None) -> dict[str, object]:
    resume = resume or b"# Resume\n\n## Experience\nPython\n"
    candidate_start = candidate.index(b"Python")
    resume_start = resume.index(b"Python")
    source_span = {
        "source_id": "candidate",
        "start_byte": candidate_start,
        "end_byte": candidate_start + 6,
        "quote_sha256": digest_imported_bytes(candidate[candidate_start:candidate_start + 6]),
    }
    draft_span = {
        "document_kind": "resume",
        "start_byte": resume_start,
        "end_byte": resume_start + 6,
        "quote_sha256": digest_imported_bytes(resume[resume_start:resume_start + 6]),
        "document_sha256": digest_imported_bytes(resume),
        "source_id": "candidate",
        "source_start_byte": candidate_start,
        "source_end_byte": candidate_start + 6,
        "source_quote_sha256": source_span["quote_sha256"],
        "source_role": "candidate_evidence",
    }
    posting_span = {
        "source_id": "posting", "start_byte": 0, "end_byte": 6,
        "quote_sha256": digest_imported_bytes(posting[:6]),
    }
    return {
        "schema_version": "scout-tailoring-request:1",
        "requested_outputs": outputs,
        "source_roles": {
            "posting": [{"source_id": "posting", "input_index": 0}],
            "candidate_evidence": [{"source_id": "candidate", "input_index": 1}],
        },
        "requirements": [{
            "requirement_id": "python", "statement": "Python", "posting_ref": posting_span,
            "candidate_evidence_refs": [source_span], "assessment": "supported",
            "draft_evidence_refs": [draft_span],
        }],
        "claim_evidence": [{
            "claim_id": "tools", "statement": "Python", "status": "supported",
            "evidence_refs": [source_span], "included": True,
            "draft_evidence_refs": [draft_span],
        }],
        "gaps": [], "questions": [], "posting_terms": ["Python"],
    }


def _journal_state(home: Path, target: Path, gig_id: str) -> tuple[str, dict[str, bytes]]:
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id)
    snapshot = external_recording._snapshot(resolved)
    return snapshot.head, dict(snapshot.artifacts)


def _run(tmp_path: Path, *, outputs: list[str]) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object], bytes, bytes, bytes]:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting = b"Python required in Denver\n"
    candidate = b"Built reliable Python tools\n"
    request = _request(outputs=outputs, posting=posting, candidate=candidate)
    posting_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=posting, label="posting")
    candidate_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=candidate, label="resume")
    request_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(request), label="tailoring-request")
    refs = [{"family": "g45_run_input", "id": item.item_id} for item in (posting_input, candidate_input, request_input)]
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("tailor-plan", {"graph_selector": "tailor-application", "gig_version": None, "selection_record": None, "input_refs": refs, "output_kinds": ["tailoring"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("tailor-start", {"run_plan_id": plan.payload["run_plan_id"]}))
    selected = plan.payload["inputs"]
    documents = {"resume": b"# Resume\n\n## Experience\nPython\n"}
    if "cover_letter" in outputs:
        documents["cover_letter"] = b"# Cover Letter\n\nI can help with Python.\n"
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000075.tailoring")
    packet = renderer.build_tailoring_packet(project_id=plan.payload["project_id"], gig_id=gig_id, gig_version=plan.payload["gig_version"], graph_version=1, run_id=started.payload["run_id"], selected_inputs=selected, request=request, documents=documents, source_bytes={"posting": posting, "candidate": candidate})
    bundle = encode_tailoring_bundle(packet.documents)
    output = {"kind": "tailoring", "markdown": bundle.decode(), "sidecar": {"document_sha256": digest_imported_bytes(bundle), "output_kind": "tailoring", "run_id": started.payload["run_id"], "selected_inputs": selected}, "domain_sidecar": {"schema_id": "urn:gigai:scout:tailoring-packet:1", "value": packet.sidecar}, "supporting_artifacts": []}
    check = {"kind": "tailoring-completion", "markdown": "# Tailoring completion\n\npass\n", "sidecar": {"evidence_kind": "tailoring-completion", "run_id": started.payload["run_id"], "output_sha256": digest_imported_bytes(bundle), "result": "pass"}}
    checkpoint = external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("tailor-checkpoint", {"run_id": started.payload["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [output, check], "reason": "synthetic tailoring packet"}))
    output_ref, check_ref = checkpoint.payload["artifacts"]
    submit_input = {"run_id": started.payload["run_id"], "parent_checkpoint": checkpoint.payload["checkpoint_id"], "output_refs": [output_ref], "check_refs": [check_ref["sidecar"]], "disclosure": {"execution": "unobserved", "actor_report": "declared"}}
    submitted = external_recording.submit_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("tailor-submit", submit_input))
    return plan.payload, started.payload, submitted.payload, submit_input, bundle, posting, candidate


def _checkpoint_context(tmp_path: Path, *, extra_requests: int = 0) -> dict[str, object]:
    """Build a real public Run and producer artifacts, stopping before checkpoint."""
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting = b"Python required in Denver\n"
    candidate = b"Built reliable Python tools\n"
    request = _request(outputs=["resume"], posting=posting, candidate=candidate)
    imported = [
        import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=posting, label="posting"),
        import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=candidate, label="resume"),
        import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(request), label="tailoring-request"),
    ]
    for index in range(extra_requests):
        ambiguous_request = dict(request)
        ambiguous_request["posting_terms"] = ["Python", "Go"]
        imported.append(import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(ambiguous_request), label=f"tailoring-request-{index + 2}"))
    refs = [{"family": "g45_run_input", "id": item.item_id} for item in imported]
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("malformed-tailor-plan", {"graph_selector": "tailor-application", "gig_version": None, "selection_record": None, "input_refs": refs, "output_kinds": ["tailoring"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("malformed-tailor-start", {"run_plan_id": plan.payload["run_plan_id"]}))
    selected = plan.payload["inputs"]
    documents = {"resume": b"# Resume\n\n## Experience\nPython\n"}
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000075.tailoring")
    packet = renderer.build_tailoring_packet(project_id=plan.payload["project_id"], gig_id=gig_id, gig_version=plan.payload["gig_version"], graph_version=1, run_id=started.payload["run_id"], selected_inputs=selected, request=request, documents=documents, source_bytes={"posting": posting, "candidate": candidate})
    bundle = encode_tailoring_bundle(packet.documents)
    output = {"kind": "tailoring", "markdown": bundle.decode(), "sidecar": {"document_sha256": digest_imported_bytes(bundle), "output_kind": "tailoring", "run_id": started.payload["run_id"], "selected_inputs": selected}, "domain_sidecar": {"schema_id": "urn:gigai:scout:tailoring-packet:1", "value": packet.sidecar}, "supporting_artifacts": []}
    check = {"kind": "tailoring-completion", "markdown": "# Tailoring completion\n\npass\n", "sidecar": {"evidence_kind": "tailoring-completion", "run_id": started.payload["run_id"], "output_sha256": digest_imported_bytes(bundle), "result": "pass"}}
    return {"home": home, "target": target, "gig_id": gig_id, "plan": plan.payload, "started": started.payload, "output": output, "check": check, "renderer": renderer}


def _resign_domain(context: dict[str, object]) -> None:
    output = context["output"]
    domain = output["domain_sidecar"]["value"]
    domain["packet_sha256"] = context["renderer"]._packet_digest(domain)


def test_resume_only_and_exact_submit_replay(tmp_path: Path) -> None:
    plan, _started, receipt, submit_input, bundle, _posting, _candidate = _run(tmp_path, outputs=["resume"])
    assert plan["output_contract"]["content_sha256"].startswith("sha256:")
    request_ref = plan["tailoring_request"]
    assert request_ref["input_index"] == 2
    assert request_ref["run_input_id"] == plan["inputs"][2]["run_input_id"]
    assert request_ref["snapshot_ref"] == plan["inputs"][2]["snapshot_ref"]
    assert receipt["outcome"] == "succeeded"
    assert receipt["outputs"][0]["kind"] == "tailoring"
    before_head, before_artifacts = _journal_state(tmp_path / "home", tmp_path / "target", str(plan["gig_id"]))
    replay = external_recording.submit_v2(home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=plan["gig_id"], envelope=_envelope("tailor-submit", submit_input))
    after_head, after_artifacts = _journal_state(tmp_path / "home", tmp_path / "target", str(plan["gig_id"]))
    assert bundle.startswith(b"# Scout tailoring bundle")
    assert replay.created is False
    assert replay.payload == receipt
    assert after_head == before_head
    assert after_artifacts == before_artifacts


def test_both_documents_complete_and_changed_draft_is_new_checkpoint(tmp_path: Path) -> None:
    plan, started, receipt, _submit_input, bundle, posting, candidate = _run(tmp_path, outputs=["resume", "cover_letter"])
    assert receipt["outcome"] == "succeeded"
    assert b"## Document: cover_letter" in bundle

    home, target, gig_id = tmp_path / "home", tmp_path / "target", str(plan["gig_id"])
    before_successor_head, before_successor_artifacts = _journal_state(home, target, gig_id)
    changed_resume = b"# Resume\n\n## Experience\nPython and Go\n"
    changed_request = _request(outputs=["resume", "cover_letter"], posting=posting, candidate=candidate, resume=changed_resume)
    changed_request_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(changed_request), label="tailoring-request-revision")
    previous_inputs = plan["inputs"]
    successor_refs = [
        {"family": "g45_run_input", "id": item["run_input_id"]}
        for item in previous_inputs[:2]
    ] + [{"family": "g45_run_input", "id": changed_request_input.item_id}]
    successor = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("tailor-successor-plan", {
            "graph_selector": "tailor-application", "gig_version": None,
            "selection_record": None, "input_refs": successor_refs,
            "output_kinds": ["tailoring"],
            "predecessor": {"kind": "run", "run_id": started["run_id"]},
        }),
    )
    successor_started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("tailor-successor-start", {"run_plan_id": successor.payload["run_plan_id"]}),
    )
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000075.tailoring")
    successor_packet = renderer.build_tailoring_packet(
        project_id=successor.payload["project_id"], gig_id=gig_id,
        gig_version=successor.payload["gig_version"], graph_version=1,
        run_id=successor_started.payload["run_id"],
        selected_inputs=successor.payload["inputs"], request=changed_request,
        documents={"resume": changed_resume, "cover_letter": b"# Cover Letter\n\nI can help with Python.\n"},
        source_bytes={"posting": posting, "candidate": candidate},
    )
    successor_bundle = encode_tailoring_bundle(successor_packet.documents)
    successor_output = {
        "kind": "tailoring", "markdown": successor_bundle.decode(),
        "sidecar": {"document_sha256": digest_imported_bytes(successor_bundle), "output_kind": "tailoring", "run_id": successor_started.payload["run_id"], "selected_inputs": successor.payload["inputs"]},
        "domain_sidecar": {"schema_id": "urn:gigai:scout:tailoring-packet:1", "value": successor_packet.sidecar},
        "supporting_artifacts": [],
    }
    successor_check = {
        "kind": "tailoring-completion", "markdown": "# Tailoring completion\n\npass\n",
        "sidecar": {"evidence_kind": "tailoring-completion", "run_id": successor_started.payload["run_id"], "output_sha256": digest_imported_bytes(successor_bundle), "result": "pass"},
    }
    successor_checkpoint = external_recording.checkpoint_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_envelope("tailor-successor-checkpoint", {
            "run_id": successor_started.payload["run_id"], "parent_checkpoint": None,
            "questions": [], "artifact_refs": [successor_output, successor_check],
            "reason": "changed draft successor tailoring packet",
        }),
    )
    successor_output_ref, successor_check_ref = successor_checkpoint.payload["artifacts"]
    successor_submit_input = {
        "run_id": successor_started.payload["run_id"],
        "parent_checkpoint": successor_checkpoint.payload["checkpoint_id"],
        "output_refs": [successor_output_ref], "check_refs": [successor_check_ref["sidecar"]],
        "disclosure": {"execution": "unobserved", "actor_report": "declared"},
    }
    successor_receipt = external_recording.submit_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_envelope("tailor-successor-submit", successor_submit_input),
    )
    assert successor_receipt.payload["outcome"] == "succeeded"
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000075.tailoring")
    initial_packet = renderer.build_tailoring_packet(
        project_id=plan["project_id"], gig_id=gig_id, gig_version=plan["gig_version"],
        graph_version=1, run_id=started["run_id"], selected_inputs=plan["inputs"],
        request=_request(outputs=["resume", "cover_letter"], posting=posting, candidate=candidate),
        documents={"resume": b"# Resume\n\n## Experience\nPython\n", "cover_letter": b"# Cover Letter\n\nI can help with Python.\n"},
        source_bytes={"posting": posting, "candidate": candidate},
    )
    new_resume_entry = successor_packet.sidecar["documents"]["resume"]
    new_draft_entry = successor_packet.sidecar["claim_evidence"][0]["draft_evidence_refs"][0]
    assert new_resume_entry["document_sha256"] != digest_imported_bytes(b"# Resume\n\n## Experience\nPython\n")
    assert new_resume_entry["checks"] != initial_packet.sidecar["documents"]["resume"]["checks"]
    assert successor_packet.sidecar["source_status"] == initial_packet.sidecar["source_status"]
    assert new_draft_entry["document_sha256"] == digest_imported_bytes(changed_resume)
    assert new_draft_entry["quote_sha256"] == digest_imported_bytes(b"Python")
    assert successor_bundle != bundle
    after_successor_head, after_successor_artifacts = _journal_state(home, target, gig_id)
    assert after_successor_head != before_successor_head
    assert all(after_successor_artifacts[path] == data for path, data in before_successor_artifacts.items())


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unsupported_claim", "tailoring_domain_invalid"),
        ("source_bytes", "tailoring_packet_artifact_mismatch"),
        ("wrong_origin", "tailoring_domain_origin_mismatch"),
        ("wrong_domain", "external_domain_unsupported"),
    ],
)
def test_tailoring_refuses_malformed_producer_without_publication(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    context = _checkpoint_context(tmp_path)
    output, check = context["output"], context["check"]
    if mutation == "unsupported_claim":
        claim = output["domain_sidecar"]["value"]["claim_evidence"][0]
        claim["status"] = "unsupported"
        claim["included"] = True
    elif mutation == "source_bytes":
        artifact = output["domain_sidecar"]["value"]["source_artifacts"][0]
        artifact["content_base64"] = "d3Jvbmc="
        _resign_domain(context)
    elif mutation == "wrong_origin":
        output["domain_sidecar"]["value"]["origin"]["run_id"] = "run_00000000-0000-4000-8000-000000000099"
        _resign_domain(context)
    elif mutation == "wrong_domain":
        output["domain_sidecar"]["schema_id"] = "urn:gigai:scout:unknown-packet:1"

    before = _journal_state(context["home"], context["target"], context["gig_id"])
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.checkpoint_v2(
            home_root=context["home"], requested_target=context["target"], gig_id=context["gig_id"],
            envelope=_envelope("malformed-tailor-checkpoint", {
                "run_id": context["started"]["run_id"], "parent_checkpoint": None,
                "questions": [], "artifact_refs": [output, check], "reason": f"malformed producer: {mutation}",
            }),
        )
    after = _journal_state(context["home"], context["target"], context["gig_id"])
    assert refused.value.code == expected_code
    assert after == before


def test_tailoring_refuses_requested_output_mismatch_without_publication(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting_bytes = b"Python required in Denver\n"
    candidate_bytes = b"Built reliable Python tools\n"
    request = _request(outputs=["resume"], posting=posting_bytes, candidate=candidate_bytes)
    posting = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=posting_bytes, label="posting")
    candidate = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=candidate_bytes, label="resume")
    request_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(request), label="tailoring-request")
    refs = [{"family": "g45_run_input", "id": item.item_id} for item in (posting, candidate, request_input)]
    before = _journal_state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.plan_v2(
            home_root=home, requested_target=target, gig_id=gig_id,
            envelope=_envelope("wrong-output-kind", {
                "graph_selector": "tailor-application", "gig_version": None,
                "selection_record": None, "input_refs": refs,
                "output_kinds": ["not-tailoring"], "predecessor": None,
            }),
        )
    after = _journal_state(home, target, gig_id)
    assert refused.value.code == "external_authority_mismatch"
    assert after == before


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing", "tailoring_input_missing"),
        ("duplicate", "tailoring_input_mismatch"),
        ("wrong_family", "tailoring_input_missing"),
        ("source_role_reuse", "tailoring_input_mismatch"),
        ("foreign", "external_record_not_found"),
        ("changed", "tailoring_input_mismatch"),
    ],
)
def test_tailoring_request_is_sealed_before_public_plan(
    tmp_path: Path, case: str, expected_code: str
) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting_bytes = b"Python required in Denver\n"
    candidate_bytes = b"Built reliable Python tools\n"
    request = _request(outputs=["resume"], posting=posting_bytes, candidate=candidate_bytes)
    posting = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=posting_bytes, label="posting")
    candidate = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=candidate_bytes, label="resume")
    refs = [{"family": "g45_run_input", "id": item.item_id} for item in (posting, candidate)]
    if case == "missing":
        pass
    elif case == "duplicate":
        first = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(request), label="tailoring-request")
        changed = dict(request)
        changed["posting_terms"] = ["Python", "Go"]
        second = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(changed), label="tailoring-request-duplicate")
        refs.extend({"family": "g45_run_input", "id": item.item_id} for item in (first, second))
    elif case == "wrong_family":
        request_file = tmp_path / "request.md"
        request_file.write_bytes(canonical_json_bytes(request))
        reference = import_reference(home_root=home, requested_target=target, gig_id=gig_id, kind="resume", source=request_file, label="tailoring-request-reference")
        refs.append({"family": "g45_reference", "id": reference.item_id})
    elif case == "source_role_reuse":
        reused = dict(request)
        reused["source_roles"] = dict(request["source_roles"])
        reused["source_roles"]["candidate_evidence"] = [{"source_id": "candidate", "input_index": 2}]
        request_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(reused), label="tailoring-request-reused")
        refs.append({"family": "g45_run_input", "id": request_input.item_id})
    elif case == "foreign":
        foreign_root = tmp_path / "foreign"
        foreign_root.mkdir()
        foreign_home, foreign_target, foreign_gig, _ = _fixture(foreign_root)
        request_input = import_run_input(home_root=foreign_home, requested_target=foreign_target, gig_id=foreign_gig, data=canonical_json_bytes(request), label="tailoring-request-foreign")
        refs.append({"family": "g45_run_input", "id": request_input.item_id})
    else:
        changed = dict(request)
        changed["source_roles"] = dict(request["source_roles"])
        changed["source_roles"]["candidate_evidence"] = [{"source_id": "candidate", "input_index": 99}]
        request_input = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(changed), label="tailoring-request-changed")
        refs.append({"family": "g45_run_input", "id": request_input.item_id})
    before = _journal_state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.plan_v2(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                f"tailoring-request-{case}",
                {
                    "graph_selector": "tailor-application",
                    "gig_version": None,
                    "selection_record": None,
                    "input_refs": refs,
                    "output_kinds": ["tailoring"],
                    "predecessor": None,
                },
            ),
        )
    after = _journal_state(home, target, gig_id)
    assert refused.value.code == expected_code
    assert after == before
