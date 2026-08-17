from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.config import load_config
from gigai.discovery import DiscoveryError, build_discovery_artifacts
from gigai.lifecycle import start_interview
from gigai.proposal_interview import Question
from gigai.question_generation import G27_DISCOVERY_PROMPT, generate_model_questions
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
