from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.index import IndexError, read_index, rebuild_index
from gigai.diagnostics import run_doctor
from gigai.lifecycle import create_offline
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _configured(tmp_path: Path) -> tuple[Path, Path]:
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
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    return home, target


def _uuids():
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 32)
    )
    return lambda: next(values)


def test_index_rebuild_is_disposable_deterministic_and_idempotent(
    tmp_path: Path,
) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="indexed-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    first = rebuild_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    initial_bytes = (created.workpad / "state.sqlite").read_bytes()
    second = read_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    assert second == first
    assert (created.workpad / "state.sqlite").read_bytes() == initial_bytes
    (created.workpad / "state.sqlite").unlink()
    rebuilt = read_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    assert rebuilt.as_dict() == first.as_dict()
    (created.workpad / "state.sqlite").write_bytes(b"not a SQLite database")
    repaired = read_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    assert repaired.as_dict() == first.as_dict()


def test_index_never_conceals_authoritative_workpad_divergence(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="divergent-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    rebuild_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    (created.workpad / "gig.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(IndexError, match="divergence"):
        read_index(
            workpad=created.workpad,
            project_id=created.project_id,
            gig_id=created.gig_id,
        )


def test_offline_doctor_reports_authoritative_journal_and_index_health(
    tmp_path: Path,
) -> None:
    home, target = _configured(tmp_path)
    create_offline(
        home_root=home,
        requested_target=target,
        name="doctor-index-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    report = run_doctor(home)
    checks = {check.id: check for check in report.checks}
    assert checks["journal.index"].status == "PASS"
    assert checks["journal.index"].evidence_safe_to_share == ("managed_workpads=1",)


def test_read_commands_return_stable_json_without_a_target_delta(
    tmp_path: Path,
) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="read-surface-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    before = tuple(
        sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))
    )
    runner = CliRunner()
    common = (created.gig_id, "--target", str(target), "--home", str(home), "--json")
    results = {
        command: runner.invoke(cli, [command, *common])
        for command in ("proposals", "status", "show", "history", "plan")
    }
    gigs = runner.invoke(cli, ["gigs", "--home", str(home), "--json"])
    failures = {
        name: result.output
        for name, result in {**results, "gigs": gigs}.items()
        if result.exit_code != 0
    }
    assert not failures, failures
    payloads = {name: json.loads(result.output) for name, result in results.items()}
    assert payloads["status"]["gig_id"] == created.gig_id
    assert payloads["status"]["proposal_status"] == "proposed"
    assert payloads["plan"]["authority"] == "proposed"
    assert payloads["history"][0]["sequence"] == 1
    assert payloads["proposals"]["proposal_id"] == created.proposal_id
    assert payloads["show"]["gig_id"] == created.gig_id
    assert json.loads(gigs.output) == [
        {"gig_id": created.gig_id, "project_id": created.project_id}
    ]
    after = tuple(
        sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))
    )
    assert after == before
