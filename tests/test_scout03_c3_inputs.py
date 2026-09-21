from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from gigai import external_recording
from gigai.native_records import create_native_record
from tests.test_scout04_input_integration import (
    _experience,
    _native_ref,
    _plan_input,
    _profile,
)
from tests.test_scout04_external_recording import _envelope, _fixture


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(Path(sys.executable).with_name("gigai")), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_invocation(path: Path, key: str, typed: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(
            {
                "origin": "direct_cli",
                "actor": {"kind": "operator", "id": "local-user"},
                "operation_key": key,
                "input": typed,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_fresh_cli_question_answer_and_successor_external_plan(tmp_path: Path) -> None:
    home, target, gig_id, _workpad, posting = _fixture(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    profile = create_native_record(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="fresh-profile",
    )
    experience = create_native_record(
        **options,
        content=_experience(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="fresh-experience",
    )
    old = external_recording.plan(
        **options,
        envelope=_envelope(
            "fresh-old-plan",
            _plan_input(
                [
                    {"family": "g45_run_input", "id": posting},
                    _native_ref(profile),
                    _native_ref(experience),
                ]
            ),
        ),
    )
    started = external_recording.start(
        **options,
        envelope=_envelope(
            "fresh-old-start", {"run_plan_id": old.payload["run_plan_id"]}
        ),
    )
    checkpoint = external_recording.checkpoint(
        **options,
        envelope=_envelope(
            "fresh-old-question",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [
                    {
                        "id": "harness",
                        "state": "missing",
                        "prompt": "Need harness-engineering experience?",
                    }
                ],
                "artifact_refs": [],
                "reason": "awaiting an explicitly saved user answer",
            },
        ),
    )
    common = ["--home", str(home), "--target", str(target), "--gig", gig_id, "--json"]
    context = _cli("record", "native", "context", *common)
    assert context.returncode == 0, context.stderr
    assert json.loads(context.stdout)["outstanding_questions"] == [
        {
            "question_id": "harness-engineering",
            "record_id": experience.record_id,
            "state": "missing",
        }
    ]
    answer_file = tmp_path / "answered-experience.json"
    answer_file.write_text(json.dumps(_experience(answered=True)), encoding="utf-8")
    answered = _cli(
        "record",
        "native",
        "update",
        "--id",
        experience.record_id,
        "--parent-revision",
        experience.revision_id,
        "--content-file",
        str(answer_file),
        "--operation-key",
        "fresh-cli-answer",
        *common,
    )
    assert answered.returncode == 0, answered.stderr
    answer = json.loads(answered.stdout)
    assert answer["record_id"] == experience.record_id
    old_view = external_recording.inspect(**options, run_id=started.payload["run_id"])
    assert old_view["run"]["status"] == "waiting_input"
    assert old.payload["inputs"][2]["revision_id"] == experience.revision_id
    successor_request = _write_invocation(
        tmp_path / "successor-plan.json",
        "fresh-cli-successor-plan",
        _plan_input(
            [
                {"family": "g45_run_input", "id": posting},
                _native_ref(profile),
                {
                    "family": "scout_record",
                    "record_id": answer["record_id"],
                    "revision_id": answer["revision_id"],
                    "scope": {"mode": "saved_default", "task_context_id": None},
                },
            ],
            {
                "kind": "checkpoint",
                "run_id": started.payload["run_id"],
                "checkpoint_id": checkpoint.payload["checkpoint_id"],
            },
        ),
    )
    successor = _cli(
        "external",
        "plan",
        "--invocation",
        str(successor_request),
        *common,
    )
    assert successor.returncode == 0, successor.stderr
    successor_payload = json.loads(successor.stdout)
    start_request = _write_invocation(
        tmp_path / "successor-start.json",
        "fresh-cli-successor-start",
        {"run_plan_id": successor_payload["run_plan_id"]},
    )
    successor_start = _cli(
        "external",
        "start",
        "--invocation",
        str(start_request),
        *common,
    )
    assert successor_start.returncode == 0, successor_start.stderr
    assert json.loads(successor_start.stdout)["mode"] == "external_agent_recording"
