from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.config import load_config
from gigai.discovery import DiscoveryError, build_discovery_artifacts
from gigai.journal import reconcile_journal
from gigai.lifecycle import persist_discovery_manifest, start_interview
from gigai.proposal_interview import Question
from gigai.proposal_interview import ReferenceDecision
from gigai.question_generation import (
    G27_DISCOVERY_PROMPT,
    QuestionGenerationError,
    generate_model_questions,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


def _started(tmp_path: Path):
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    started = start_interview(
        home_root=home,
        requested_target=target,
        name="adaptive-proof",
        request="Define a reusable repository review Gig.",
        reference_paths=(),
    )
    return home, started


def test_g27_builds_valid_manifest_with_truthful_capability_boundary(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)
    session = generate_model_questions(
        config=config,
        model_target="offline-default",
        session=started.session,
        reference_bytes={},
        prompt_name=G27_DISCOVERY_PROMPT,
    )

    built = build_discovery_artifacts(
        config=config,
        model_target="offline-default",
        session=session,
        reference_bytes={},
    )
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json",
        canonical_json_bytes(built.manifest),
    )
    assert report.valid, report.as_dict()
    assert len(built.manifest["question_rounds"][1]["questions"]) == 3
    capabilities = {
        item["capability_id"]: item for item in built.manifest["capabilities"]
    }
    assert capabilities["model_invocation"]["status"] == "usable"
    assert capabilities["target_effect"]["status"] == "unsupported"
    assert built.manifest["research_plan"]["network"] == "local_only"
    assert all(item.path.startswith(("review/", "manifests/")) for item in built.artifacts)


def test_g27_refuses_a_sixth_direction_question(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)
    extra = tuple(
        Question(
            f"direction-{index}",
            "text",
            False,
            (),
            ("scope",),
            "This question changes the reusable Gig definition.",
            "model://test/g27",
        )
        for index in range(6)
    )
    session = replace(started.session, questions=started.session.questions + extra)
    with pytest.raises(DiscoveryError, match="five-question ceiling"):
        build_discovery_artifacts(
            config=config,
            model_target="offline-default",
            session=session,
            reference_bytes={},
        )


def test_g27_rejects_six_questions_before_session_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)
    response = {
        "questions": [
            {
                "question_id": f"question-{index}",
                "answer_type": "text",
                "required": False,
                "options": [],
                "depends_on": ["scope"],
                "rationale": "This changes the Gig definition.",
                "provenance": "model://test/g27",
            }
            for index in range(6)
        ]
    }
    monkeypatch.setattr(
        "gigai.question_generation.invoke_bounded",
        lambda *args, **kwargs: SimpleNamespace(output_text=json.dumps(response)),
    )
    with pytest.raises(QuestionGenerationError, match="5-question ceiling"):
        generate_model_questions(
            config=config,
            model_target="offline-default",
            session=started.session,
            reference_bytes={},
            prompt_name=G27_DISCOVERY_PROMPT,
        )


def test_g27_improve_manifest_requires_bounded_g20_context(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)
    session = replace(started.session, request_kind="improve")
    summary = canonical_json_bytes(
        {
            "schema_version": "1.0",
            "kind": "g27_improve_context",
            "learning_record_ids": ["learning_12345678-1234-4234-9234-123456789abc"],
            "active_version": 1,
            "omitted_content_policy": "raw_unselected_and_hidden_context_excluded",
        }
    )
    built = build_discovery_artifacts(
        config=config,
        model_target="offline-default",
        session=session,
        reference_bytes={},
        improve_context={
            "learning_record_ids": ["learning_12345678-1234-4234-9234-123456789abc"],
            "active_version": 1,
            "max_source_bytes": len(summary),
            "omitted_content_policy": "raw_unselected_and_hidden_context_excluded",
        },
        improve_summary_bytes=summary,
    )
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(built.manifest)
    )
    assert report.valid, report.as_dict()
    assert built.manifest["improve_context"]["learning_record_ids"] == [
        "learning_12345678-1234-4234-9234-123456789abc"
    ]


def test_g27_refuses_improve_manifest_without_context_summary(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    with pytest.raises(DiscoveryError, match="bounded G20 context"):
        build_discovery_artifacts(
            config=load_config(home),
            model_target="offline-default",
            session=replace(started.session, request_kind="improve"),
            reference_bytes={},
        )


def test_g27_refuses_selected_reference_digest_drift(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    reference_id = "ref_12345678-1234-4234-9234-123456789abc"
    selected = ReferenceDecision(
        reference_id,
        "sha256:" + "a" * 64,
        "selected",
    )
    session = replace(started.session, references=(selected,))
    with pytest.raises(DiscoveryError, match="reference bytes"):
        build_discovery_artifacts(
            config=load_config(home),
            model_target="offline-default",
            session=session,
            reference_bytes={reference_id: b"tampered"},
        )


def test_g27_selected_reference_uses_the_common_effect_vocabulary(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    content = b"selected reference bytes"
    reference_id = "ref_12345678-1234-4234-9234-123456789abc"
    selected = ReferenceDecision(reference_id, digest_imported_bytes(content), "selected")
    session = replace(started.session, references=(selected,))
    built = build_discovery_artifacts(
        config=load_config(home),
        model_target="offline-default",
        session=session,
        reference_bytes={reference_id: content},
    )
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(built.manifest)
    )
    assert report.valid, report.as_dict()
    local_read = next(
        item for item in built.manifest["capabilities"]
        if item["capability_id"] == "local_reference_read"
    )
    assert local_read["effects"] == ["read_target"]


def test_g27_reconciles_interrupted_discovery_manifest_publish(tmp_path: Path) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)

    def interrupt(step: str) -> None:
        if step == "after_artifact_replace":
            raise RuntimeError(step)

    with pytest.raises(RuntimeError, match="after_artifact_replace"):
        persist_discovery_manifest(
            start=started,
            session=started.session,
            config=config,
            model_target="offline-default",
            reference_bytes={},
            observer=interrupt,
        )

    workpad = started.workpad
    manifest_path = workpad / "manifests/gig-discovery-manifest.json"
    assert manifest_path.is_file()
    assert not any(
        path.name.endswith("gig-discovery-manifest-written.txt")
        for path in (workpad / "handoffs").iterdir()
    )

    recovered = reconcile_journal(
        workpad=workpad,
        project_id=started.project_id,
        gig_id=started.gig_id,
    )
    assert recovered.reconciled is True
    assert validate_serialized_contract(
        "gig-discovery-manifest.schema.json", manifest_path.read_bytes()
    ).valid
    assert any(
        path.name.endswith("gig-discovery-manifest-written.txt")
        for path in (workpad / "handoffs").iterdir()
    )


def test_g27_malformed_model_response_creates_no_discovery_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, started = _started(tmp_path)
    config = load_config(home)
    monkeypatch.setattr(
        "gigai.question_generation.invoke_bounded",
        lambda *args, **kwargs: SimpleNamespace(output_text="not-json"),
    )
    with pytest.raises(QuestionGenerationError, match="malformed JSON"):
        generate_model_questions(
            config=config,
            model_target="offline-default",
            session=started.session,
            reference_bytes={},
            prompt_name=G27_DISCOVERY_PROMPT,
        )
    assert not (started.workpad / "manifests/gig-discovery-manifest.json").exists()
