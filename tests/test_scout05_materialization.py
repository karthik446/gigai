from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

import gigai.default_init as default_init
from gigai.default_init import DefaultInitError, initialize_defaults
from gigai.project_binding import load_project_binding
from gigai.scout_materialization import ScoutMaterialization
from gigai.scout_template import scout_candidate_inventory
from gigai.setup import build_config, run_setup
from gigai.canonical import parse_json_bytes
from gigai.validators import validate_serialized_contract


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    return home, target


def _artifact_commits(workpad: Path, path: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(workpad), "log", "--format=%H", "--", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _publish_second_fixture_publisher(workpad: Path, path: str, data: bytes) -> None:
    """Create an intentionally conflicting committed artifact in a disposable workpad."""
    candidate = workpad / path
    candidate.write_bytes(data)
    subprocess.run(["git", "-C", str(workpad), "add", "--", path], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workpad),
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "synthetic conflicting artifact publisher",
        ],
        check=True,
    )


def test_explicit_scout_candidate_materializes_inert_source_and_pending_proposal(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    result = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = result.instances[0]
    assert instance.status == "approval_required"
    assert result.scout_status == "approval_required"
    assert load_project_binding(target).active_gig_id is None
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    assert (workpad / "gig.py").is_file()
    assert (workpad / "goalgraphs/research-role.md").is_file()
    assert (workpad / "ui/template.html").is_file()
    proposal = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert proposal["kind"] == "create"
    assert proposal["base_gig_version"] is None
    assert proposal["parent_proposal_id"] is None
    binding = parse_json_bytes((workpad / "manifests/template-instance-binding.json").read_bytes())
    assert binding["schema_version"] == "2.0"
    assert binding["proposal_id"] == proposal["proposal_id"]
    assert binding["approval"] == {"state": "unapproved", "approved_version": None}
    assert (workpad / binding["software_inventory"]["path"]).is_file()
    binding_report = validate_serialized_contract(
        "template-instance-binding.schema.json",
        (workpad / "manifests/template-instance-binding.json").read_bytes(),
    )
    assert binding_report.valid, binding_report.as_dict()


def test_invalid_written_scout_binding_is_refused_before_binding_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = _setup(tmp_path)

    def malformed_materialization(**_kwargs: object) -> ScoutMaterialization:
        digest = "sha256:" + "0" * 64
        return ScoutMaterialization(
            source_digest=digest,
            inventory_ref={},
            proposal_id="gp_00000000-0000-4000-8000-000000000001",
            proposal_ref={},
            approval_state="unapproved",
            customized_paths=(),
        )

    monkeypatch.setattr(
        default_init, "materialize_scout_candidate", malformed_materialization
    )
    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=scout_candidate_inventory(),
        )
    assert refused.value.code == "template_reconciliation_required"
    assert "registered schema validation" in str(refused.value)
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    assert not (workpad / "manifests/template-instance-binding.json").exists()
    assert not _artifact_commits(
        workpad, "manifests/template-instance-binding.json"
    )


def test_scout_rerun_preserves_edit_and_reuses_pending_proposal(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    first = initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    custom = b"# locally customized Scout\n"
    (workpad / "README.md").write_bytes(custom)
    second = initialize_defaults(home_root=home, requested_target=target, username=None, inventory=scout_candidate_inventory())
    assert second.instances[0].gig_id == first.instances[0].gig_id
    assert second.instances[0].status == "approval_required"
    assert (workpad / "README.md").read_bytes() == custom
    proposal = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert proposal["proposal_id"] in second.instances[0].next_action


def test_scout_interruption_after_proposal_resumes_same_source_and_proposal(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)

    def interrupt(step: str) -> None:
        if step == "candidate_proposal_prepared":
            raise RuntimeError("stop after candidate proposal")

    with pytest.raises(RuntimeError, match="candidate proposal"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=scout_candidate_inventory(),
            observer=interrupt,
        )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    first = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    resumed = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=scout_candidate_inventory(),
    )
    assert resumed.instances[0].status == "approval_required"
    assert parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())["proposal_id"] == first["proposal_id"]


def test_scout_instances_are_private_per_project(tmp_path: Path) -> None:
    home, first_target = _setup(tmp_path)
    second_target = tmp_path / "second-target"
    second_target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", second_target], check=True)
    first = initialize_defaults(home_root=home, requested_target=first_target, username="owner", inventory=scout_candidate_inventory())
    second = initialize_defaults(home_root=home, requested_target=second_target, username="owner", inventory=scout_candidate_inventory())
    workpads = list((tmp_path / "workpads").glob("projects/*/gigs/*"))
    by_gig = {path.name: path for path in workpads}
    first_root = by_gig[first.instances[0].gig_id]
    second_root = by_gig[second.instances[0].gig_id]
    (first_root / "ui/style.css").write_text("/* local edit */\n", encoding="utf-8")
    assert (second_root / "ui/style.css").read_bytes() != b"/* local edit */\n"
    assert first_root != second_root


def test_scout_resume_refuses_redirected_editable_destination(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)

    def interrupt(step: str) -> None:
        if step == "source_snapshot_published":
            raise RuntimeError("stop after inert snapshot")

    with pytest.raises(RuntimeError, match="inert snapshot"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=scout_candidate_inventory(),
            observer=interrupt,
        )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    foreign = tmp_path / "foreign.py"
    foreign.write_text("foreign", encoding="utf-8")
    (workpad / "gig.py").symlink_to(foreign)
    with pytest.raises(DefaultInitError, match="redirected"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username=None,
            inventory=scout_candidate_inventory(),
        )


def test_scout_source_inventory_multi_publisher_refuses_without_republication(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)

    def interrupt(step: str) -> None:
        if step == "source_snapshot_published":
            raise RuntimeError("stop after source snapshot")

    with pytest.raises(RuntimeError, match="source snapshot"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=scout_candidate_inventory(),
            observer=interrupt,
        )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    relative_inventory = inventory_path.relative_to(workpad).as_posix()
    before = _artifact_commits(workpad, relative_inventory)
    _publish_second_fixture_publisher(
        workpad, relative_inventory, inventory_path.read_bytes() + b"\n"
    )
    conflicted = _artifact_commits(workpad, relative_inventory)
    assert len(conflicted) == 2
    before_resume = tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "log", "--format=%H"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username=None,
            inventory=scout_candidate_inventory(),
        )
    assert refused.value.code == "template_reconciliation_required"
    assert "software inventory cannot be authenticated" in str(refused.value)
    assert _artifact_commits(workpad, relative_inventory) == conflicted
    assert _artifact_commits(workpad, relative_inventory) != before
    assert tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "log", "--format=%H"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    ) == before_resume
