"""Three-Run public v3 research reuse and direct-history boundaries."""

from __future__ import annotations

import base64
from importlib import import_module
from pathlib import Path
import subprocess

import pytest

from gigai import external_recording
from gigai.canonical import digest_imported_bytes
from tests.test_scout06_research_inputs import _complete, _env, _research


def _head(workpad: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"], text=True
    ).strip()


def _successor(
    *, home: Path, target: Path, gig_id: str, selected: dict[str, object], key: str
) -> dict[str, object]:
    request = {
        "graph_selector": "research-role",
        "gig_version": None,
        "selection_record": None,
        "input_refs": [
            {
                "family": "role_request",
                "role_title": "Forward Deployed Engineer",
                "role_context": f"{key} context",
            },
            selected,
        ],
        "output_kinds": ["research"],
        "predecessor": None,
    }
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(f"{key}-plan", request),
    )
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(f"{key}-start", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    renderer = import_module(
        "gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000076.research"
    )
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    packet = renderer.build_research_packet(
        project_id=plan.payload["project_id"],
        gig_id=gig_id,
        gig_version=plan.payload["gig_version"],
        graph_id=plan.payload["goal_graph_id"],
        graph_version=1,
        run_id=started.payload["run_id"],
        selected_inputs=plan.payload["inputs"],
        research=_research(),
        artifact_bytes={"role_capture": capture, "role_review": review},
    )
    digest = digest_imported_bytes(packet.markdown)
    output = {
        "kind": "research",
        "markdown": packet.markdown.decode(),
        "sidecar": {
            "document_sha256": digest,
            "output_kind": "research",
            "run_id": started.payload["run_id"],
            "selected_inputs": plan.payload["inputs"],
        },
        "domain_sidecar": {
            "schema_id": "urn:gigai:scout:research-packet:3",
            "value": packet.sidecar,
        },
        "supporting_artifacts": [
            {
                "artifact_id": name,
                "media_type": "text/plain",
                "content_base64": base64.b64encode(data).decode(),
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
            for name, data in {"role_capture": capture, "role_review": review}.items()
        ],
    }
    check = {
        "kind": "research-role-completion",
        "markdown": f"# {key} completion\n\npass\n",
        "sidecar": {
            "evidence_kind": "research-role-completion",
            "run_id": started.payload["run_id"],
            "output_sha256": digest,
            "result": "pass",
        },
    }
    checkpoint = external_recording.checkpoint_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            f"{key}-checkpoint",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [],
                "artifact_refs": [output, check],
                "reason": f"synthetic {key} research reuse",
            },
        ),
    )
    assert checkpoint.created
    output_ref, check_ref = checkpoint.payload["artifacts"]
    submit_input = {
        "run_id": started.payload["run_id"],
        "parent_checkpoint": checkpoint.payload["checkpoint_id"],
        "output_refs": [output_ref],
        "check_refs": [check_ref["sidecar"]],
        "disclosure": {"execution": "unobserved", "actor_report": "declared"},
    }
    submitted = external_recording.submit_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(f"{key}-submit", submit_input),
    )
    assert submitted.created and submitted.payload["outcome"] == "succeeded"
    return {
        "plan": plan.payload,
        "started": started.payload,
        "receipt": submitted.payload,
        "raw": {
            "family": "scout_research",
            "run_id": started.payload["run_id"],
            "receipt_id": submitted.payload["receipt_id"],
            "output_kind": "research",
        },
        "selected": next(
            item for item in plan.payload["inputs"] if item.get("family") == "scout_research"
        ),
        "submit_envelope": _env(f"{key}-submit", submit_input),
    }


def test_three_run_reuse_validates_only_direct_history_and_replays_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed second Run is a valid direct input to a third Run.

    Before the correction, the third checkpoint refused with
    ``historical research input ancestry is too deep``.  The spy makes the
    bounded rule observable: the third Run hydrates the second Run's committed
    tuple, but never opens the first Run's bytes.
    """
    home, target, gig_id, resolved, _snapshot, first_raw, _plan, first_started, _receipt = _complete(
        tmp_path
    )
    first_run_id = first_started["run_id"]
    second = _successor(
        home=home, target=target, gig_id=gig_id, selected=first_raw, key="second"
    )
    second_run_id = second["started"]["run_id"]
    before_third = dict(external_recording._snapshot(resolved).artifacts)

    import gigai.scout_research_inputs as research_inputs

    hydrated_paths: list[str] = []
    original_read = research_inputs.read_committed_artifact

    def spy_read(*args, **kwargs):
        path = kwargs.get("path")
        if isinstance(path, str):
            hydrated_paths.append(path)
        return original_read(*args, **kwargs)

    monkeypatch.setattr(research_inputs, "read_committed_artifact", spy_read)
    third = _successor(
        home=home,
        target=target,
        gig_id=gig_id,
        selected=second["raw"],
        key="third",
    )
    assert third["receipt"]["outcome"] == "succeeded"
    assert third["selected"] == next(
        item for item in third["plan"]["inputs"] if item.get("family") == "scout_research"
    )
    assert third["selected"]["output"] == second["receipt"]["outputs"][0]
    assert any(path.startswith(f"runs/{second_run_id}/") for path in hydrated_paths)
    assert not any(path.startswith(f"runs/{first_run_id}/") for path in hydrated_paths)

    after_third = external_recording._snapshot(resolved).artifacts
    prior_paths = {
        path
        for path in before_third
        if path.startswith(f"runs/{first_run_id}/") or path.startswith(f"runs/{second_run_id}/")
    }
    assert {path: after_third[path] for path in prior_paths} == {
        path: before_third[path] for path in prior_paths
    }
    replay = external_recording.submit_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=third["submit_envelope"],
    )
    assert not replay.created
    assert replay.payload == third["receipt"]


@pytest.mark.parametrize("mutation", ["missing", "foreign", "tampered"])
def test_three_run_direct_history_refusals_do_not_publish(
    tmp_path: Path, mutation: str
) -> None:
    home, target, gig_id, resolved, _snapshot, first_raw, _plan, _started, _receipt = _complete(
        tmp_path
    )
    second = _successor(
        home=home, target=target, gig_id=gig_id, selected=first_raw, key="second"
    )
    direct = second["raw"]
    if mutation == "missing":
        direct = {**direct, "receipt_id": "receipt_00000000-0000-4000-8000-000000000099"}
    elif mutation == "foreign":
        direct = {**direct, "run_id": "run_00000000-0000-4000-8000-000000000099"}
    else:
        path = resolved.path / second["selected"]["output"]["markdown"]["path"]
        original = path.read_bytes()
        path.write_bytes(original + b"\ntampered\n")
    head_before = _head(resolved.path)
    try:
        with pytest.raises(Exception) as refused:
            external_recording.plan_v2(
                home_root=home,
                requested_target=target,
                gig_id=gig_id,
                envelope=_env(
                    f"bad-{mutation}",
                    {
                        "graph_selector": "research-role",
                        "gig_version": None,
                        "selection_record": None,
                        "input_refs": [
                            {
                                "family": "role_request",
                                "role_title": "Forward Deployed Engineer",
                                "role_context": "third context",
                            },
                            direct,
                        ],
                        "output_kinds": ["research"],
                        "predecessor": None,
                    },
                ),
            )
        if mutation == "tampered":
            assert "uncommitted divergence" in str(refused.value)
        else:
            assert getattr(refused.value, "code", None) == "external_record_not_found"
    finally:
        if mutation == "tampered":
            path.write_bytes(original)
    assert _head(resolved.path) == head_before
