from __future__ import annotations

from pathlib import Path
import subprocess
import time
import uuid

from gigai.lifecycle import approve_offline, create_offline
from gigai.run import launch_run, read_run_details
from gigai.run import RunError
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


def _fixture(tmp_path: Path, *, git_target: bool = False) -> tuple[Path, Path, str]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    if git_target:
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", target], check=True
        )
        subprocess.run(
            ["git", "-C", str(target), "config", "user.name", "G13 Test"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "config",
                "user.email",
                "g13-test@gigai.invalid",
            ],
            check=True,
        )
        (target / "README.md").write_text("fixture\n")
        subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(target), "commit", "--quiet", "-m", "fixture"], check=True
        )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 40)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="run-proof",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    return home, target, created.gig_id


def test_deterministic_run_is_schema_valid_and_target_preserving(
    tmp_path: Path,
) -> None:
    home, target, gig_id = _fixture(tmp_path)
    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
    )
    details = read_run_details(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        run_id=result.run_id,
    )
    assert result.status == "succeeded"
    assert details["status"] == "succeeded"
    before = (result.run_path / "target-before.json").read_bytes()
    after = (result.run_path / "target-after.json").read_bytes()
    assert before == after
    assert details["aggregate_usage"]["cost_status"] == "not_applicable"
    assert details["aggregate_usage"]["total_tokens"] == 0
    assert details["terminal_handoff"] is not None
    assert details["workpad_commit"]
    assert validate_serialized_contract(
        "run-details.schema.json",
        (result.run_path / "run-details.json").read_bytes(),
    ).valid


def test_repeated_runs_have_disjoint_directories(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(30, 40)
    )
    first = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        uuid_factory=lambda: next(values),
    )
    second = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        uuid_factory=lambda: next(values),
    )
    assert first.run_id != second.run_id
    assert first.run_path != second.run_path
    assert first.run_path.is_dir() and second.run_path.is_dir()


def test_detached_worker_failure_terminalizes_run(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path, git_target=True)

    def mutate_target(step: str) -> None:
        if step == "after_run_started_commit":
            (target / "README.md").write_text("changed after seal\n")

    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=False,
        observer=mutate_target,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
    )
    assert result.status == "running"

    deadline = time.monotonic() + 10
    details = read_run_details(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        run_id=result.run_id,
    )
    while details["status"] == "preparing" and time.monotonic() < deadline:
        time.sleep(0.05)
        details = read_run_details(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            run_id=result.run_id,
        )

    assert details["status"] == "interrupted"
    assert len(list((result.workpad / "handoffs").glob("*-run-interrupted.txt"))) == 1


def test_worker_interruption_is_idempotent_after_parent_reconciliation(
    tmp_path: Path,
) -> None:
    home, target, gig_id = _fixture(tmp_path, git_target=True)

    def mutate_target(step: str) -> None:
        if step == "after_run_started_commit":
            (target / "README.md").write_text("changed after seal\n")

    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        observer=mutate_target,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
    )
    assert result.status == "interrupted"
    assert result.terminal is not None
    assert len(list((result.workpad / "handoffs").glob("*-run-interrupted.txt"))) == 1


def test_preparation_failpoints_leave_no_uncommitted_run(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    for point in (
        "after_brief_write",
        "after_manifest_seal",
        "after_initial_run_details",
    ):

        def stop(step: str, expected: str = point) -> None:
            if step == expected:
                raise RuntimeError(step)

        try:
            launch_run(
                home_root=home,
                requested_target=target,
                gig_id=gig_id,
                observer=stop,
                uuid_factory=lambda: uuid.uuid4(),
            )
        except RuntimeError as exc:
            assert str(exc) == point
        else:
            raise AssertionError("failpoint did not stop preparation")
        assert not list((tmp_path / "workpads").rglob("runs/run_*/run-manifest.json"))


def test_unapproved_version_fails_before_run_allocation(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    try:
        launch_run(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            version=99,
            uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
        )
    except RunError as exc:
        assert "approved" in str(exc)
    else:
        raise AssertionError("unapproved version unexpectedly ran")
    assert not list((tmp_path / "workpads").rglob("runs/run_*/run-manifest.json"))
