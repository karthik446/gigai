from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes
from gigai.native_records import (
    archive_native_record,
    create_native_record,
    create_task_override,
    update_native_record,
)
from gigai.journal import run_with_journal_writer
from gigai.run import RunError, launch_run, read_run_details
from gigai.run_plan import RunPlanError, create_run_plan, read_run_plan
from gigai.scout_inputs import ScoutInputError, resolve_external_input
from gigai.validators import validate_serialized_contract
from gigai.workpad import resolve_workpad
from tests.test_scout04_external_recording import _envelope, _fixture


def _fact(
    value: str, *, state: str = "known", context: str | None = None
) -> dict[str, object]:
    return {
        "state": state,
        "value": value if state == "known" else None,
        "context": context,
        "provenance": {"kind": "user_reported", "source_refs": []},
        "conflict_refs": [],
    }


def _profile(scope: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "profile_preferences",
        "scope": scope
        or {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {
            "hard_constraints": {
                "geography": _fact("Denver"),
                "work_mode": _fact("remote"),
                "seniority": _fact("senior"),
                "employment_type": _fact("full_time"),
                "compensation": _fact("120000 USD annual"),
            },
            "soft_priorities": {"industry": _fact("climate")},
            "sponsorship_need": _fact("needs_sponsorship"),
            "employer_sponsorship": _fact("", state="unknown"),
            "eligibility": _fact("eligible", context="US work authorization"),
        },
    }


def _experience(*, answered: bool = False) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "experience_qa",
        "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {
            "questions": [
                {
                    "question_id": "harness-engineering",
                    "prompt": "Describe harness engineering experience.",
                    "state": "answered" if answered else "missing",
                    "answer": "Built reproducible test harnesses."
                    if answered
                    else None,
                    "provenance": {
                        "kind": "user_reported",
                        "source_refs": [],
                    }
                    if answered
                    else None,
                }
            ]
        },
    }


def _native_ref(result, *, context: str | None = None) -> dict[str, object]:
    return {
        "family": "scout_record",
        "record_id": result.record_id,
        "revision_id": result.revision_id,
        "scope": {
            "mode": "run_override" if context else "saved_default",
            "task_context_id": context,
        },
    }


def _plan_input(
    input_refs: list[dict[str, object]], predecessor=None
) -> dict[str, object]:
    return {
        "graph_selector": "career",
        "gig_version": None,
        "selection_record": None,
        "input_refs": input_refs,
        "output_kinds": ["report"],
        "predecessor": predecessor,
    }


def test_external_plan_pins_native_revisions_and_keeps_old_history(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, posting = _fixture(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    profile = create_native_record(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="profile",
    )
    experience = create_native_record(
        **options,
        content=_experience(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="experience-missing",
    )
    inputs = [
        {"family": "g45_run_input", "id": posting},
        _native_ref(profile),
        _native_ref(experience),
    ]
    old = external_recording.plan(
        **options, envelope=_envelope("native-old", _plan_input(inputs))
    )
    assert old.payload["inputs"][1]["native_kind"] == "profile_preferences"
    assert old.payload["inputs"][2]["scope"] == {
        "mode": "saved_default",
        "task_context_id": None,
        "base": None,
    }
    assert validate_serialized_contract(
        "external-recording-plan.schema.json", canonical_json_bytes(old.payload)
    ).valid
    assert validate_serialized_contract(
        "external-recording-invocation.schema.json",
        canonical_json_bytes(old.payload["invocation"]),
    ).valid
    assert not external_recording.plan(
        **options, envelope=_envelope("native-old", _plan_input(inputs))
    ).created
    # A later saved answer creates a new native revision, but cannot replace
    # the exact revision already sealed into the old Plan when it is redeemed.
    answered = update_native_record(
        **options,
        record_id=experience.record_id,
        parent_revision=experience.revision_id,
        content=_experience(answered=True),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="experience-answer",
    )
    revised_profile_content = _profile()
    revised_profile_content["payload"]["soft_priorities"]["industry"] = _fact(
        "clean-energy"
    )
    profile_update = update_native_record(
        **options,
        record_id=profile.record_id,
        parent_revision=profile.revision_id,
        content=revised_profile_content,
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="profile-update",
    )
    started = external_recording.start(
        **options,
        envelope=_envelope(
            "native-old-start", {"run_plan_id": old.payload["run_plan_id"]}
        ),
    )
    checkpoint = external_recording.checkpoint(
        **options,
        envelope=_envelope(
            "native-waiting",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [
                    {
                        "id": "harness",
                        "state": "missing",
                        "prompt": "Need user-reported harness experience?",
                    }
                ],
                "artifact_refs": [],
                "reason": "native answer is missing",
            },
        ),
    )
    assert old.payload["inputs"][1]["revision_id"] == profile.revision_id
    assert old.payload["inputs"][2]["revision_id"] == experience.revision_id
    successor = external_recording.plan(
        **options,
        envelope=_envelope(
            "native-successor",
            _plan_input(
                [
                    {"family": "g45_run_input", "id": posting},
                    _native_ref(profile_update),
                    _native_ref(answered),
                ],
                {
                    "kind": "checkpoint",
                    "run_id": started.payload["run_id"],
                    "checkpoint_id": checkpoint.payload["checkpoint_id"],
                },
            ),
        ),
    )
    assert successor.payload["run_plan_id"] != old.payload["run_plan_id"]
    assert external_recording.start(
        **options,
        envelope=_envelope(
            "native-successor-start", {"run_plan_id": successor.payload["run_plan_id"]}
        ),
    ).created
    archived = archive_native_record(
        **options,
        record_id=experience.record_id,
        parent_revision=answered.revision_id,
        actor={"kind": "operator", "id": "local-user"},
        operation_key="experience-archive",
    )
    assert archived.state == "archived"
    continued = external_recording.checkpoint(
        **options,
        envelope=_envelope(
            "native-old-unchanged",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                "questions": [
                    {
                        "id": "harness",
                        "state": "answered",
                        "prompt": "Need user-reported harness experience?",
                    }
                ],
                "artifact_refs": [],
                "reason": "old pinned input remains historical evidence",
            },
        ),
    )
    assert continued.payload["sequence"] == 2


def test_native_scope_is_closed_and_override_contexts_cannot_mix(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, posting = _fixture(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    base = create_native_record(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="base",
    )
    first = create_task_override(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="override-one",
        base={"record_id": base.record_id, "revision_id": base.revision_id},
    )
    second = create_task_override(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="override-two",
        base={"record_id": base.record_id, "revision_id": base.revision_id},
    )
    assert first.task_context_id and second.task_context_id
    with pytest.raises(external_recording.ExternalRecordingError) as missing_scope:
        external_recording.plan(
            **options,
            envelope=_envelope(
                "missing-native-scope",
                _plan_input(
                    [
                        {"family": "g45_run_input", "id": posting},
                        {
                            "family": "scout_record",
                            "record_id": first.record_id,
                            "revision_id": first.revision_id,
                        },
                    ]
                ),
            ),
        )
    assert missing_scope.value.code == "external_record_not_found"
    wrong = _native_ref(first, context=second.task_context_id)
    with pytest.raises(external_recording.ExternalRecordingError) as wrong_scope:
        external_recording.plan(
            **options,
            envelope=_envelope(
                "wrong-native-scope",
                _plan_input([{"family": "g45_run_input", "id": posting}, wrong]),
            ),
        )
    assert wrong_scope.value.code == "external_record_not_found"
    with pytest.raises(external_recording.ExternalRecordingError) as mixed:
        external_recording.plan(
            **options,
            envelope=_envelope(
                "mixed-native-scopes",
                _plan_input(
                    [
                        {"family": "g45_run_input", "id": posting},
                        _native_ref(first, context=first.task_context_id),
                        _native_ref(second, context=second.task_context_id),
                    ]
                ),
            ),
        )
    assert mixed.value.code == "external_input_mismatch"
    accepted = external_recording.plan(
        **options,
        envelope=_envelope(
            "default-plus-one-override",
            _plan_input(
                [
                    {"family": "g45_run_input", "id": posting},
                    _native_ref(base),
                    _native_ref(first, context=first.task_context_id),
                ]
            ),
        ),
    )
    assert accepted.created
    missing_scope = deepcopy(accepted.payload)
    missing_scope["inputs"][2].pop("scope")
    assert not validate_serialized_contract(
        "external-recording-plan.schema.json", canonical_json_bytes(missing_scope)
    ).valid
    unknown_scope_member = deepcopy(accepted.payload)
    unknown_scope_member["inputs"][2]["scope"]["latest"] = True
    assert not validate_serialized_contract(
        "external-recording-plan.schema.json",
        canonical_json_bytes(unknown_scope_member),
    ).valid


def test_managed_readers_and_provider_route_refuse_external_native_inputs(
    tmp_path: Path,
) -> None:
    home, target, gig_id, workpad, posting = _fixture(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    profile = create_native_record(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="reader-profile",
    )
    external = external_recording.plan(
        **options,
        envelope=_envelope(
            "reader-external",
            _plan_input(
                [
                    {"family": "g45_run_input", "id": posting},
                    _native_ref(profile),
                ]
            ),
        ),
    )
    with pytest.raises(RunPlanError) as managed_plan:
        read_run_plan(**options, run_plan_id=external.payload["run_plan_id"])
    assert managed_plan.value.code == "external_plan_family_refused"
    with pytest.raises(RunError, match="external_plan_family_refused"):
        launch_run(
            **options,
            run_plan_id=external.payload["run_plan_id"],
            operator_consent={
                "schema_version": "1.0",
                "kind": "operator_run_consent",
                "action": "run",
                "actor": {"kind": "operator", "id": "local-user"},
                "source": "direct_cli_confirm",
            },
        )
    started = external_recording.start(
        **options,
        envelope=_envelope(
            "reader-external-start", {"run_plan_id": external.payload["run_plan_id"]}
        ),
    )
    with pytest.raises(RunError, match="external_run_family_refused"):
        read_run_details(**options, run_id=started.payload["run_id"])
    native_blob = (
        workpad
        / "records"
        / profile.record_id
        / "blobs"
        / f"{profile.revision_id}.json"
    )
    with pytest.raises(RunPlanError) as private_ingress:
        create_run_plan(
            **options,
            graph_selector="career",
            input_paths=(native_blob,),
        )
    assert private_ingress.value.code == "private_provider_disclosure_refused"


def test_native_resolver_refuses_forged_foreign_tampered_and_uncommitted_refs(
    tmp_path: Path,
) -> None:
    home, target, gig_id, workpad, _posting = _fixture(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    profile = create_native_record(
        **options,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="resolver-profile",
    )
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    snapshot = run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(
            ("records/", "references/", "run-inputs/")
        ),
    )
    selected = _native_ref(profile)
    sealed = resolve_external_input(resolved, snapshot, selected)
    assert sealed["content"]["family"] == "jsl_blob"
    forged = deepcopy(selected)
    forged["scope"] = {
        "mode": "run_override",
        "task_context_id": "task_context_00000000-0000-4000-8000-000000000099",
    }
    with pytest.raises(ScoutInputError, match="scope"):
        resolve_external_input(resolved, snapshot, forged)
    foreign = deepcopy(selected)
    foreign["record_id"] = "record_00000000-0000-4000-8000-000000000099"
    with pytest.raises(ScoutInputError, match="unavailable"):
        resolve_external_input(resolved, snapshot, foreign)
    redirected_id = "record_00000000-0000-4000-8000-000000000098"
    redirected = workpad / "records" / redirected_id
    redirected.parent.mkdir(exist_ok=True)
    redirected.symlink_to(
        workpad / "records" / profile.record_id, target_is_directory=True
    )
    with pytest.raises(ScoutInputError, match="unavailable"):
        resolve_external_input(
            resolved,
            snapshot,
            {
                "family": "scout_record",
                "record_id": redirected_id,
                "revision_id": profile.revision_id,
                "scope": {"mode": "saved_default", "task_context_id": None},
            },
        )
    blob_path = sealed["content"]["blob_ref"]["path"]
    assert isinstance(blob_path, str)
    corrupt = replace(snapshot, artifacts={**snapshot.artifacts, blob_path: b"{}"})
    with pytest.raises(ScoutInputError, match="unavailable"):
        resolve_external_input(resolved, corrupt, selected)
