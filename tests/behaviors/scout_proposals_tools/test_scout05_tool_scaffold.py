from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from gigai.lifecycle import select_active_workpad
from gigai.native_records import create_native_record
from gigai.private_records import migrate_workpad_layout
from gigai.registry import open_project_registry
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad
from tests.behaviors.scout_proposals_tools.test_scout03_c3_tools import _approved_tool, _receipt
from tests.behaviors.scout_proposals_tools.test_scout04_input_integration import _profile


_REPO = Path(__file__).parents[3]
_SOURCE = _REPO / "src/gigai/scout/data/gig.py"


def _copy_source(workpad: Path) -> Path:
    destination = workpad / "gig.py"
    destination.write_bytes(_SOURCE.read_bytes())
    return destination


def _run(source: Path, home: Path, target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_root = str(_REPO / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(source), "--home", str(home), "--target", str(target), *args],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout)


def _add_unapproved_gig(*, home: Path, project_id: str, gig_id: str) -> Path:
    workpad = provision_workpad(
        home_root=home,
        project_id=project_id,
        gig_id=gig_id,
    ).path
    migrate_workpad_layout(workpad=workpad, project_id=project_id, gig_id=gig_id)
    return workpad


def _no_active_two_gig_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, str, str]:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    project_id = initialize_target(home_root=home, requested_target=target).project_id
    gig_a = "gig_00000000-0000-4000-8000-000000000051"
    gig_b = "gig_00000000-0000-4000-8000-000000000052"
    workpad_a = _add_unapproved_gig(
        home=home,
        project_id=project_id,
        gig_id=gig_a,
    )
    workpad_b = _add_unapproved_gig(
        home=home,
        project_id=project_id,
        gig_id=gig_b,
    )
    b_record = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_b,
        content=_profile(),
        actor={"kind": "operator", "id": "fixture-user"},
        origin="user_reported",
        operation_key="fixture-b-record",
    )
    assert b_record.created
    return home, target, workpad_a, workpad_b, gig_a, gig_b


def test_two_copied_gigs_have_independent_editable_wrappers(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _home_one, _target_one, _gig_one, workpad_one, _invocation_one = _approved_tool(one)
    _home_two, _target_two, _gig_two, workpad_two, _invocation_two = _approved_tool(two)
    source_one = _copy_source(workpad_one)
    source_two = _copy_source(workpad_two)
    original = source_one.read_bytes()
    assert source_two.read_bytes() == original
    source_one.write_bytes(original + b"\n# independent Gig customization\n")
    assert source_one.read_bytes() != source_two.read_bytes()
    assert source_two.read_bytes() == original


def test_same_project_wrapper_uses_its_registered_gig_while_b_is_active(
    tmp_path: Path,
) -> None:
    home, target, gig_a, workpad_a, invocation = _approved_tool(tmp_path)
    project_id = json.loads(
        (workpad_a / "manifests/workpad-layout.json").read_text()
    )["project_id"]
    gig_b = "gig_00000000-0000-4000-8000-000000000053"
    workpad_b = _add_unapproved_gig(
        home=home,
        project_id=project_id,
        gig_id=gig_b,
    )
    b_record = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_b,
        content=_profile(),
        actor={"kind": "operator", "id": "fixture-user"},
        origin="user_reported",
        operation_key="fixture-active-b-record",
    )
    assert b_record.created
    source_a, source_b = _copy_source(workpad_a), _copy_source(workpad_b)
    select_active_workpad(home_root=home, requested_target=target, gig_id=gig_b)
    with open_project_registry(home, create=False)[0].transaction() as transaction:
        active = transaction.find_active_workpad(project_id)
        assert active is not None and active.gig_id == gig_b

    a_list = _payload(_run(source_a, home, target, "list"))
    b_list = _payload(_run(source_b, home, target, "list"))
    assert a_list["ok"] is True
    assert b_list["ok"] is True
    assert a_list["result"]["records"] == []  # type: ignore[index]
    assert any(row["record_id"] == b_record.record_id for row in b_list["result"]["records"])  # type: ignore[index]

    create = _run(
        source_a,
        home,
        target,
        "create",
        "--capability-id",
        str(invocation["capability_id"]),
        "--actor-id",
        "same-project-agent",
        "--operation-key",
        "same-project-a-create",
        "--input-json",
        json.dumps({"content": _profile()}),
    )
    created = _payload(create)
    assert create.returncode == 0, create.stderr
    a_record_id = created["result"]["result"]["record_id"]  # type: ignore[index]
    assert created["result"]["origin"] == "agent_supplied"  # type: ignore[index]
    with open_project_registry(home, create=False)[0].transaction() as transaction:
        active = transaction.find_active_workpad(project_id)
        assert active is not None and active.gig_id == gig_b
    a_receipt = _receipt(workpad_a, home, target, gig_a)
    b_receipt = _receipt(workpad_b, home, target, gig_b)
    assert a_receipt["gig_id"] == gig_a
    assert b_receipt["gig_id"] == gig_b
    assert a_record_id != b_record.record_id


def test_same_project_wrapper_works_without_any_active_gig(tmp_path: Path) -> None:
    home, target, workpad_a, workpad_b, gig_a, gig_b = _no_active_two_gig_fixture(tmp_path)
    source_a, source_b = _copy_source(workpad_a), _copy_source(workpad_b)
    with open_project_registry(home, create=False)[0].transaction() as transaction:
        project = transaction.find_project(
            json.loads((workpad_a / "manifests/workpad-layout.json").read_text())["project_id"]
        )
        assert project is not None
        assert transaction.find_active_workpad(project.project_id) is None

    a_list = _run(source_a, home, target, "list")
    b_list = _run(source_b, home, target, "list")
    assert a_list.returncode == 0, a_list.stderr
    assert b_list.returncode == 0, b_list.stderr
    assert _payload(a_list)["result"]["records"] == []  # type: ignore[index]
    b_records = _payload(b_list)["result"]["records"]  # type: ignore[index]
    assert b_records
    assert all(row["record_id"] for row in b_records)
    b_read = _payload(_run(source_b, home, target, "read", b_records[0]["record_id"]))
    assert b_read["result"]["record"]["gig_id"] == gig_b  # type: ignore[index]
    assert gig_a not in json.dumps(b_read)
    with open_project_registry(home, create=False)[0].transaction() as transaction:
        project = transaction.find_project(
            json.loads((workpad_a / "manifests/workpad-layout.json").read_text())["project_id"]
        )
        assert project is not None
        assert transaction.find_active_workpad(project.project_id) is None


def test_relocated_or_symlinked_wrapper_fails_closed(tmp_path: Path) -> None:
    home, target, _gig_id, workpad, _invocation = _approved_tool(tmp_path)
    source = _copy_source(workpad)
    foreign_target = tmp_path / "foreign-target"
    foreign_target.mkdir()
    foreign_result = _run(source, home, foreign_target, "list")
    foreign_payload = _payload(foreign_result)
    assert foreign_result.returncode == 2
    assert foreign_payload["error"]["code"] == "wrapper_ownership_refused"  # type: ignore[index]

    relocated_dir = tmp_path / "relocated"
    relocated_dir.mkdir()
    relocated = relocated_dir / "gig.py"
    relocated.write_bytes(source.read_bytes())
    relocated_result = _run(relocated, home, target, "list")
    relocated_payload = _payload(relocated_result)
    assert relocated_result.returncode == 2
    assert relocated_payload["error"]["code"] == "wrapper_ownership_refused"  # type: ignore[index]

    linked = tmp_path / "linked-gig.py"
    linked.symlink_to(source)
    linked_result = _run(linked, home, target, "list")
    linked_payload = _payload(linked_result)
    assert linked_result.returncode == 2
    assert linked_payload["error"]["code"] == "wrapper_ownership_refused"  # type: ignore[index]


def test_help_import_and_read_only_operations_do_not_mutate_workpad(tmp_path: Path) -> None:
    home, target, _gig_id, workpad, _invocation = _approved_tool(tmp_path)
    source = _copy_source(workpad)
    head_before = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status_before = subprocess.run(
        ["git", "-C", str(workpad), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    help_result = _run(source, home, target, "--help")
    assert help_result.returncode == 0
    assert all(word in help_result.stdout for word in ("list", "read", "context", "create"))
    import_result = subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path(__import__('sys').argv[1])", str(source)],
        cwd=_REPO,
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_result.returncode == 0, import_result.stderr
    for command in ("list", "context"):
        result = _run(source, home, target, command)
        assert result.returncode == 0, result.stderr
        assert _payload(result)["ok"] is True
    head_after = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status_after = subprocess.run(
        ["git", "-C", str(workpad), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert head_after == head_before
    assert status_after == status_before


def test_unapproved_create_returns_typed_next_action_without_publication(tmp_path: Path) -> None:
    home, target, _gig_id, workpad, _invocation = _approved_tool(tmp_path)
    source = _copy_source(workpad)
    result = _run(
        source,
        home,
        target,
        "create",
        "--capability-id",
        "cap_00000000-0000-4000-8000-000000000099",
        "--actor-id",
        "agent-scaffold-test",
        "--operation-key",
        "unapproved-scaffold-create",
        "--input-json",
        json.dumps({"content": _profile()}),
    )
    payload = _payload(result)
    assert result.returncode == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool_capability_refused"  # type: ignore[index]
    assert payload["next_action"]["kind"] == "proposal_required"  # type: ignore[index]
    assert not list((workpad / "records/operations").glob("*unapproved-scaffold-create*"))


def test_approved_create_then_fresh_process_read_list_context_without_http(tmp_path: Path) -> None:
    home, target, _gig_id, workpad, invocation = _approved_tool(tmp_path)
    source = _copy_source(workpad)
    content = _profile()
    create = _run(
        source,
        home,
        target,
        "create",
        "--capability-id",
        str(invocation["capability_id"]),
        "--actor-id",
        "approved-tool-agent",
        "--operation-key",
        "fresh-process-scaffold-create",
        "--input-json",
        json.dumps({"content": content}),
    )
    created = _payload(create)
    assert create.returncode == 0, create.stderr
    assert created["ok"] is True
    record_id = created["result"]["result"]["record_id"]  # type: ignore[index]
    assert created["result"]["origin"] == "agent_supplied"  # type: ignore[index]

    listing = _payload(_run(source, home, target, "list"))
    assert any(row["record_id"] == record_id for row in listing["result"]["records"])  # type: ignore[index]
    reading = _payload(_run(source, home, target, "read", str(record_id)))
    assert reading["result"]["record"]["record_id"] == record_id  # type: ignore[index]
    context = _payload(_run(source, home, target, "context"))
    assert any(row["record_id"] == record_id for row in context["result"]["context"]["records"])  # type: ignore[index]
    source_text = _SOURCE.read_text(encoding="utf-8")
    assert "urllib" not in source_text.lower()
    assert "http://" not in source_text.lower()


def test_update_and_archive_require_an_explicit_approved_crud_binding(tmp_path: Path) -> None:
    home, target, _gig_id, workpad, _invocation = _approved_tool(tmp_path)
    source = _copy_source(workpad)
    for command in ("update", "archive"):
        arguments = [
            command,
            "record_00000000-0000-4000-8000-000000000001",
            "--parent-revision",
            "revision_00000000-0000-4000-8000-000000000001",
            "--capability-id",
            "cap_00000000-0000-4000-8000-000000000041",
            "--actor-id",
            "agent-scaffold-test",
            "--operation-key",
            f"unapproved-{command}",
        ]
        if command == "update":
            arguments.extend(["--input-json", json.dumps({"content": _profile()})])
        result = _run(source, home, target, *arguments)
        payload = _payload(result)
        assert result.returncode == 2
        assert payload["error"]["code"] == "tool_binding_invalid"  # type: ignore[index]
        assert payload["next_action"]["kind"] == "proposal_required"  # type: ignore[index]
