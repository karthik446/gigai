from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.package import initialize_project_package
from gigai.registry import ProjectRecord, WorkpadRecord, open_project_registry
from gigai.setup import build_config, run_setup


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir(parents=True)
    target.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target], check=True
    )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    return home, target


def _git_add(target: Path, path: str) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-f", path], check=True)


def test_init_creates_portable_package_and_exact_private_excludes(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)

    result = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    package = target / ".gigai" / "packages" / payload["package_id"]
    assert (package / "package.json").is_file()
    exclude = (target / ".git" / "info" / "exclude").read_bytes()
    assert exclude.splitlines().count(b"/.gigai/") == 0
    for entry in (
        b"/.gigai/project.toml",
        b"/.gigai/local/",
        b"/.gigai/runs/",
        b"/.gigai/cache/",
        b"/.gigai/snapshots/",
        b"/.gigai/locks/",
    ):
        assert exclude.splitlines().count(entry) == 1

    rerun = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--json"]
    )
    assert rerun.exit_code == 0, rerun.output
    rerun_payload = json.loads(rerun.output)
    assert rerun_payload["package_id"] == payload["package_id"]
    assert rerun_payload["exclude_changed"] is False


def test_init_is_idempotent_after_portable_package_is_tracked(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    initial = initialize_project_package(home_root=home, requested_target=target)
    _git_add(target, initial.package_root.relative_to(target).as_posix())

    result = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["package_id"] == initial.package_id
    assert payload["reconciled"] is False


def test_unbound_tracked_package_requires_explicit_adoption(tmp_path: Path) -> None:
    source_home, target = _setup(tmp_path)
    initial = initialize_project_package(home_root=source_home, requested_target=target)
    _git_add(target, initial.package_root.relative_to(target).as_posix())
    (target / ".gigai" / "project.toml").unlink()

    fresh_home = tmp_path / "fresh-home"
    fresh_home.mkdir()
    run_setup(
        build_config(
            home_root=fresh_home,
            workpad_root=tmp_path / "fresh-workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    plain = CliRunner().invoke(
        cli, ["init", "--home", str(fresh_home), "--target", str(target)]
    )
    assert plain.exit_code != 0
    assert "explicit --adopt-package --confirm" in plain.output
    assert not (target / ".gigai" / "project.toml").exists()

    adopted = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(fresh_home),
            "--target",
            str(target),
            "--adopt-package",
            "--confirm",
            "--json",
        ],
    )
    assert adopted.exit_code == 0, adopted.output
    assert json.loads(adopted.output)["adopted"] is True


def test_adopt_package_preserves_existing_v016_binding(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    initial = initialize_project_package(
        home_root=home, requested_target=target
    )
    package_path = initial.package_root.relative_to(target).as_posix()
    _git_add(target, package_path)
    exclude = target / ".git" / "info" / "exclude"
    existing = exclude.read_bytes()
    exclude.write_bytes(existing + b"/.gigai/\n")

    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(home),
            "--target",
            str(target),
            "--adopt-package",
            "--confirm",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["adopted"] is True
    assert payload["package_id"] == initial.package_id
    assert (target / ".gigai" / "project.toml").is_file()
    assert (target / ".git" / "info" / "exclude").read_bytes().splitlines().count(
        b"/.gigai/"
    ) == 0


def test_adopt_package_refuses_tracked_private_content(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    initial = initialize_project_package(
        home_root=home, requested_target=target
    )
    _git_add(target, initial.package_root.relative_to(target).as_posix())
    private = target / ".gigai" / "runs" / "private.txt"
    private.parent.mkdir(parents=True)
    private.write_text("private\n", encoding="utf-8")
    _git_add(target, ".gigai/runs/private.txt")

    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(home),
            "--target",
            str(target),
            "--adopt-package",
            "--confirm",
        ],
    )

    assert result.exit_code != 0
    assert "tracked private" in result.output


def test_second_home_install_preserves_package_identity_without_authority(
    tmp_path: Path,
) -> None:
    source_home, source_target = _setup(tmp_path / "source")
    source = initialize_project_package(
        home_root=source_home, requested_target=source_target
    )
    destination_home, destination_target = _setup(tmp_path / "destination")
    source_path = source.package_root

    result = CliRunner().invoke(
        cli,
        [
            "package",
            "install",
            str(source_path),
            "--target",
            str(destination_target),
            "--home",
            str(destination_home),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["package_id"] == source.package_id
    assert payload["package_digest"] == source.package_digest
    installed = (
        destination_target / ".gigai" / "packages" / source.package_id / "package.json"
    )
    assert installed.is_file()
    assert not (destination_target / ".gigai" / "project.toml").exists()
    records = list((destination_home / "local" / "package-installations").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["package_id"] == source.package_id
    assert record["content_digest"] == source.package_digest


def test_upgrade_migrates_v016_config_keeps_backup_and_is_idempotent(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    config = home / "config.toml"
    original = '''schema_version = "1.0"
credentials = []

[paths]
home_root = "HOME_ROOT"
workpad_root = "WORKPAD_ROOT"

[editor]
argv = ["/usr/bin/true"]
open_with_target = false

[[endpoints]]
name = "offline"
adapter = "deterministic"

[[model_targets]]
name = "offline-default"
endpoint = "offline"
model = "fixture-v1"

[[profiles]]
name = "default"
planner = "offline-default"
critic = "offline-default"
adjudicator = "offline-default"

[standard_pack]
name = "standard"
version = "1"
content_digest = "sha256:test"
'''.replace("HOME_ROOT", str(home)).replace("WORKPAD_ROOT", str(tmp_path / "workpads"))
    config.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "upgrade",
            "--home",
            str(home),
            "--target",
            str(target),
            "--confirm",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    backup = home / "local" / "migrations" / "v0.1.6-to-v0.1.7" / "config.toml.v0.1.6.bak"
    record = home / "local" / "migrations" / "v0.1.6-to-v0.1.7" / "migration.json"
    assert backup.read_text(encoding="utf-8") == original
    assert 'schema_version = "2.0"' in config.read_text(encoding="utf-8")
    assert json.loads(record.read_text(encoding="utf-8"))["package_id"] == payload["package_id"]

    rerun = CliRunner().invoke(
        cli,
        [
            "upgrade",
            "--home",
            str(home),
            "--target",
            str(target),
            "--confirm",
            "--json",
        ],
    )
    assert rerun.exit_code == 0, rerun.output
    assert json.loads(rerun.output)["package_id"] == payload["package_id"]
    assert json.loads(record.read_text(encoding="utf-8"))["package_id"] == payload["package_id"]


def test_upgrade_preserves_populated_registry_and_workpad_evidence(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    initial = initialize_project_package(home_root=home, requested_target=target)
    gig_id = "gig_12345678-1234-4234-9234-123456789abc"
    workpad = tmp_path / "workpads" / "projects" / initial.project_id / "gigs" / gig_id
    workpad.mkdir(parents=True)
    (workpad / "journal.jsonl").write_text('{"sequence":1}\n', encoding="utf-8")
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        transaction.insert_workpad(
            WorkpadRecord(
                gig_id=gig_id,
                project_id=initial.project_id,
                workpad_locator=str(workpad.resolve()),
            )
        )
        transaction.select_active_workpad(initial.project_id, gig_id)

    config = home / "config.toml"
    original = '''schema_version = "1.0"
credentials = []

[paths]
home_root = "HOME_ROOT"
workpad_root = "WORKPAD_ROOT"

[editor]
argv = ["/usr/bin/true"]
open_with_target = false

[[endpoints]]
name = "offline"
adapter = "deterministic"

[[model_targets]]
name = "offline-default"
endpoint = "offline"
model = "fixture-v1"

[[profiles]]
name = "default"
planner = "offline-default"
critic = "offline-default"
adjudicator = "offline-default"

[standard_pack]
name = "standard"
version = "1"
content_digest = "sha256:test"
'''.replace("HOME_ROOT", str(home)).replace("WORKPAD_ROOT", str(tmp_path / "workpads"))
    config.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "upgrade",
            "--home",
            str(home),
            "--target",
            str(target),
            "--confirm",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["registry_preserved"] is True
    migration = home / "local" / "migrations" / "v0.1.6-to-v0.1.7" / "migration.json"
    migration_record = json.loads(migration.read_text(encoding="utf-8"))
    assert migration_record["registry_fingerprint"]
    assert migration_record["workpad_fingerprint"]
    migrated, _ = open_project_registry(home, create=False)
    assert migrated.records() == (
        ProjectRecord(
            project_id=initial.project_id,
            target_locator=str(target.resolve()),
            target_kind="git",
        ),
    )
    with migrated.transaction() as transaction:
        assert transaction.find_workpad(gig_id) == WorkpadRecord(
            gig_id=gig_id,
            project_id=initial.project_id,
            workpad_locator=str(workpad.resolve()),
        )


def test_upgrade_failure_restores_predecessor_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = _setup(tmp_path)
    config = home / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('schema_version = "2.0"', 'schema_version = "1.0"')
        .replace('capabilities = ["text"]\n', '')
        .replace('max_output_tokens = 64\n', ''),
        encoding="utf-8",
    )
    predecessor = config.read_bytes()

    import gigai.package as package_module

    def fail_initialization(**_kwargs: object) -> object:
        raise package_module.PackageError("simulated package publication failure")

    monkeypatch.setattr(package_module, "initialize_project_package", fail_initialization)
    result = CliRunner().invoke(
        cli,
        [
            "upgrade",
            "--home",
            str(home),
            "--target",
            str(target),
            "--confirm",
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert config.read_bytes() == predecessor
    assert not (home / "registry.sqlite").exists()
    assert not (target / ".gigai").exists()
    assert (home / "local" / "migrations" / "v0.1.6-to-v0.1.7" / "config.toml.v0.1.6.bak").read_bytes() == predecessor


def test_upgrade_post_publication_verification_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = _setup(tmp_path)
    config = home / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('schema_version = "2.0"', 'schema_version = "1.0"')
        .replace('capabilities = ["text"]\n', '')
        .replace('max_output_tokens = 64\n', ''),
        encoding="utf-8",
    )
    predecessor = config.read_bytes()

    import gigai.package as package_module

    fingerprints = iter(("sha256:before", "sha256:after"))
    monkeypatch.setattr(
        package_module,
        "_tree_fingerprint",
        lambda _root: next(fingerprints),
    )
    result = CliRunner().invoke(
        cli,
        [
            "upgrade",
            "--home",
            str(home),
            "--target",
            str(target),
            "--confirm",
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert config.read_bytes() == predecessor
    assert not (home / "registry.sqlite").exists()
    assert not (target / ".gigai").exists()
