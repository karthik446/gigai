"""Focused durable public Scout acquisition lifecycle evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.acquisition_records import (
    ScoutAcquisitionError,
    import_public_rows,
    read_public_acquisition_status,
    read_public_rows_file,
    resume_public_acquisition,
)
from gigai.workpad import resolve_workpad

from tests.behaviors.scout_proposals_tools.test_scout03_native_records import _options


pytestmark = pytest.mark.scout_acquisition


def _rows() -> list[dict[str, object]]:
    return [
        {"opportunity_id": "opportunity_a", "snapshot_id": "snapshot_1", "title": "Synthetic A", "source_snapshot": {"locator": "fixture:a", "status": "captured"}},
        {"opportunity_id": "opportunity_a", "snapshot_id": "snapshot_1", "duplicate_of": "opportunity_a", "source_snapshot": {"locator": "fixture:a-duplicate", "status": "captured"}},
        {"opportunity_id": "opportunity_b", "snapshot_id": "snapshot_2", "error": "fixture timeout", "source_snapshot": {"locator": "fixture:b", "status": "failed"}},
        {"opportunity_id": "opportunity_c", "snapshot_id": "snapshot_3", "acquisition_state": "excluded", "excluded_reason": "fixture exclusion", "source_snapshot": {"locator": "fixture:c", "status": "excluded"}},
    ]


def test_import_deadline_fresh_status_resume_and_exact_replay(tmp_path: Path) -> None:
    created, _options_value = _options(tmp_path)
    resolved = resolve_workpad(home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=created.gig_id, allow_semantic_state=True)
    ticks = iter((0.0, 0.0, 2.0))
    partial = import_public_rows(resolved=resolved, batch_id="fixture-batch", rows=_rows(), deadline_seconds=1, clock=lambda: next(ticks))
    assert partial.processed == partial.next_index == 1
    fresh = read_public_acquisition_status(resolved=resolved, batch_id="fixture-batch")
    assert fresh.processed == 1 and fresh.stop_reason == "deadline"
    final = resume_public_acquisition(resolved=resolved, batch_id="fixture-batch", deadline_seconds=1, clock=lambda: 0.0)
    assert final.stop_reason == "completed" and final.processed == 4
    assert len(final.considered) == len(final.duplicates) == len(final.failures) == len(final.exclusions) == 1
    replay = import_public_rows(resolved=resolved, batch_id="fixture-batch", rows=_rows(), deadline_seconds=1, clock=lambda: 0.0)
    assert replay.journal_head == final.journal_head
    assert len(list((resolved.path / "records/scout-acquisition/fixture-batch/progress").glob("*.json"))) == 2


@pytest.mark.cli
def test_normal_cli_import_and_fresh_status(tmp_path: Path) -> None:
    created, _options_value = _options(tmp_path)
    rows_file = tmp_path / "rows.json"
    rows_file.write_bytes(json.dumps(_rows(), separators=(",", ":")).encode())
    args = ["scout-acquisition", "import", "--batch-id", "cli-batch", "--rows-file", str(rows_file), "--deadline-seconds", "30", "--gig", created.gig_id, "--target", str(tmp_path / "target"), "--home", str(tmp_path / "home"), "--json"]
    imported = CliRunner().invoke(cli, args)
    assert imported.exit_code == 0, imported.output
    status = CliRunner().invoke(cli, ["scout-acquisition", "status", "--batch-id", "cli-batch", "--gig", created.gig_id, "--target", str(tmp_path / "target"), "--home", str(tmp_path / "home"), "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["result"]["processed"] == 4


def test_changed_input_private_fields_and_unsafe_batch_are_refused(tmp_path: Path) -> None:
    created, _options_value = _options(tmp_path)
    resolved = resolve_workpad(home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=created.gig_id, allow_semantic_state=True)
    import_public_rows(resolved=resolved, batch_id="immutable", rows=_rows(), deadline_seconds=30, clock=lambda: 0.0)
    changed = [dict(_rows()[0], title="changed"), *_rows()[1:]]
    with pytest.raises(ScoutAcquisitionError, match="different input"):
        import_public_rows(resolved=resolved, batch_id="immutable", rows=changed, deadline_seconds=30, clock=lambda: 0.0)
    with pytest.raises(ScoutAcquisitionError, match="unknown or private"):
        import_public_rows(resolved=resolved, batch_id="private", rows=[dict(_rows()[0], profile_preferences={"salary": "private"})], deadline_seconds=30, clock=lambda: 0.0)
    with pytest.raises(ScoutAcquisitionError, match="unsafe"):
        import_public_rows(resolved=resolved, batch_id="../escape", rows=_rows(), deadline_seconds=30, clock=lambda: 0.0)


def _redirect(path: Path, *, directory: bool) -> Path:
    backup = path.with_name(path.name + "-real")
    path.rename(backup)
    path.symlink_to(backup.name, target_is_directory=directory)
    return backup


@pytest.mark.parametrize("relative,directory", [
    ("records", True),
    ("records/scout-acquisition", True),
    ("records/scout-acquisition/fixture-batch", True),
    ("records/scout-acquisition/fixture-batch/progress", True),
    ("records/scout-acquisition/fixture-batch/input.json", False),
    ("records/scout-acquisition/fixture-batch/progress/revision.json", False),
])
def test_every_acquisition_path_component_rejects_redirect_before_read_or_write(tmp_path: Path, relative: str, directory: bool) -> None:
    created, _options_value = _options(tmp_path)
    resolved = resolve_workpad(home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=created.gig_id, allow_semantic_state=True)
    import_public_rows(resolved=resolved, batch_id="fixture-batch", rows=_rows(), deadline_seconds=30, clock=lambda: 0.0)
    progress = resolved.path / "records/scout-acquisition/fixture-batch/progress"
    final_progress = next(progress.glob("*.json"))
    selected = resolved.path / relative
    if relative.endswith("revision.json"):
        selected = final_progress
    _redirect(selected, directory=directory)
    head = subprocess.run(["git", "-C", str(resolved.path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    progress_count = len(list((resolved.path / "records/scout-acquisition/fixture-batch/progress-real" if relative.endswith("/progress") else progress).glob("*.json"))) if directory and relative.endswith("/progress") else 1
    for action in (
        lambda: read_public_acquisition_status(resolved=resolved, batch_id="fixture-batch"),
        lambda: import_public_rows(resolved=resolved, batch_id="fixture-batch", rows=_rows(), deadline_seconds=30, clock=lambda: 0.0),
        lambda: resume_public_acquisition(resolved=resolved, batch_id="fixture-batch", deadline_seconds=30, clock=lambda: 0.0),
    ):
        with pytest.raises(ScoutAcquisitionError) as error:
            action()
        assert error.value.code == "acquisition_path_unsafe"
    assert subprocess.run(["git", "-C", str(resolved.path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip() == head
    if directory and relative.endswith("/progress"):
        assert progress_count == len(list((resolved.path / "records/scout-acquisition/fixture-batch/progress-real").glob("*.json")))


@pytest.mark.parametrize("final_symlink", [False, True])
@pytest.mark.cli
def test_shared_rows_loader_rejects_parent_and_final_symlink_for_normal_cli(tmp_path: Path, final_symlink: bool) -> None:
    created, _options_value = _options(tmp_path)
    rows_parent = tmp_path / "rows-real"
    rows_parent.mkdir()
    rows = rows_parent / "public.json"
    rows.write_bytes(json.dumps(_rows(), separators=(",", ":")).encode())
    if final_symlink:
        target = rows.with_name("public-real.json")
        rows.rename(target)
        rows.symlink_to(target.name)
    else:
        redirected = tmp_path / "rows-link"
        rows_parent.rename(redirected)
        rows_parent.symlink_to(redirected.name, target_is_directory=True)
    with pytest.raises(ScoutAcquisitionError) as error:
        read_public_rows_file(rows)
    assert error.value.code == "acquisition_source_unsafe"
    result = CliRunner().invoke(cli, [
        "scout-acquisition", "import", "--batch-id", "rows-safety", "--rows-file", str(rows),
        "--deadline-seconds", "30", "--gig", created.gig_id, "--target", str(tmp_path / "target"),
        "--home", str(tmp_path / "home"), "--json",
    ])
    assert result.exit_code != 0
    assert "acquisition_source_unsafe" in result.output


@pytest.mark.parametrize("final_symlink", [False, True])
@pytest.mark.cli
def test_copied_wrapper_uses_shared_rows_loader_for_redirects(tmp_path: Path, final_symlink: bool) -> None:
    created, _options_value = _options(tmp_path)
    resolved = resolve_workpad(home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=created.gig_id, allow_semantic_state=True)
    wrapper = resolved.path / "gig.py"
    repo_root = Path(__file__).resolve().parents[3]
    shutil.copyfile(repo_root / "src/gigai/scout/data/gig.py", wrapper)
    rows_parent = tmp_path / "copied-rows"
    rows_parent.mkdir()
    rows = rows_parent / "public.json"
    rows.write_bytes(json.dumps(_rows(), separators=(",", ":")).encode())
    if final_symlink:
        target = rows.with_name("public-real.json")
        rows.rename(target)
        rows.symlink_to(target.name)
    else:
        redirected = tmp_path / "copied-rows-real"
        rows_parent.rename(redirected)
        rows_parent.symlink_to(redirected.name, target_is_directory=True)
    result = subprocess.run([
        sys.executable, str(wrapper), "--home", str(tmp_path / "home"), "--target", str(tmp_path / "target"),
        "acquisition", "import", "--batch-id", "copied-rows", "--rows-file", str(rows),
    ], check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "acquisition_source_unsafe" in result.stdout
