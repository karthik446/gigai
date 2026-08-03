from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from gigai.registry import REGISTRY_APPLICATION_ID, open_project_registry
from gigai.standard_pack import pack_digest
from tests.scenarios import (
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    copy_fixture_repository,
)


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


def _roots(tmp_path: Path, name: str) -> ScenarioRoots:
    return ScenarioRoots.create(tmp_path / name)


def _python_executable(installed_gigai: InstalledGigAI) -> Path:
    candidate = installed_gigai.command.executable.parent / "python"
    return candidate.resolve() if candidate.exists() else Path(sys.executable).resolve()


def _git_executable() -> Path:
    isolated_path_git = Path("/usr/bin/git")
    if isolated_path_git.is_file():
        return isolated_path_git.resolve()
    discovered = shutil.which("git", path="/usr/bin:/bin")
    assert discovered is not None
    return Path(discovered).resolve()


def _fresh_setup_changes() -> frozenset[str]:
    digest = pack_digest().removeprefix("sha256:")
    pack_directory = f"packs/builtin/standard/1/{digest}"
    return frozenset(
        {
            "capabilities",
            "catalogs",
            "config.toml",
            "credentials",
            "learning",
            "packs",
            "packs/builtin",
            "packs/builtin/standard",
            "packs/builtin/standard/1",
            pack_directory,
            f"{pack_directory}/standard-pack.json",
        }
    )


def _setup(
    harness: ScenarioHarness,
    roots: ScenarioRoots,
    installed_gigai: InstalledGigAI,
    *,
    name: str,
) -> None:
    harness.run(
        ScenarioSpec(
            name=name,
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(roots.workpad),
                "--editor",
                "/usr/bin/true",
                "--json",
            ),
            expected_home_changes=_fresh_setup_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
        )
    )


def _git_status(root: Path) -> bytes:
    return subprocess.run(
        [
            os.fspath(_git_executable()),
            "-C",
            os.fspath(root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        capture_output=True,
        check=True,
        shell=False,
    ).stdout


def test_installed_git_init_has_exact_path_free_delta_and_idempotent_rerun(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "git-idempotent")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="git-setup")
    exclude = roots.target / ".git" / "info" / "exclude"
    exclude_before = exclude.read_bytes()
    status_before = _git_status(roots.target)

    first = harness.run(
        ScenarioSpec(
            name="git-init",
            argv=("init", "--json"),
            expected_target_changes=frozenset({".gigai", ".gigai/project.toml"}),
            expected_home_changes=frozenset({"registry.sqlite"}),
            allowed_subprocesses=(_git_executable(),),
        )
    )
    binding_bytes = (roots.target / ".gigai" / "project.toml").read_bytes()
    exclude_after = exclude.read_bytes()
    second = harness.run(
        ScenarioSpec(
            name="git-init-rerun",
            argv=("init", "--json"),
            allowed_subprocesses=(_git_executable(),),
        )
    )

    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["project_id"] == second_payload["project_id"]
    assert first_payload["binding_created"] is True
    assert second_payload["binding_created"] is False
    assert second_payload["registry_changed"] is False
    assert second_payload["exclude_changed"] is False
    assert binding_bytes == (roots.target / ".gigai" / "project.toml").read_bytes()
    assert b"/" not in binding_bytes
    assert exclude_after != exclude_before
    assert exclude_after.splitlines().count(b"/.gigai/") == 1
    assert _git_status(roots.target) == status_before
    assert not (roots.target / ".git" / "gigai-init.lock").exists()
    public_evidence = first.stdout + second.stdout
    public_evidence += first.artifact.read_text(encoding="utf-8")
    public_evidence += second.artifact.read_text(encoding="utf-8")
    assert os.fspath(roots.target.resolve()) not in public_evidence
    assert os.fspath(roots.home.resolve()) not in public_evidence


@pytest.mark.parametrize("kind", ("python", "non-python"))
def test_installed_init_preserves_dirty_python_and_non_python_targets(
    tmp_path: Path, installed_gigai: InstalledGigAI, kind: str
) -> None:
    roots = _roots(tmp_path, f"dirty-{kind}")
    copy_fixture_repository(kind, roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name=f"dirty-{kind}-setup")
    tracked = roots.target / ("src/example.py" if kind == "python" else "src/example.js")
    tracked.write_bytes(tracked.read_bytes() + b"\n// user-dirty-byte\n")
    untracked = roots.target / "user-untracked.bin"
    untracked.write_bytes(b"\x00user\xffbytes\n")
    tracked_before = tracked.read_bytes()
    untracked_before = untracked.read_bytes()
    status_before = _git_status(roots.target)

    harness.run(
        ScenarioSpec(
            name=f"dirty-{kind}-init",
            argv=("init", "--json"),
            expected_target_changes=frozenset({".gigai", ".gigai/project.toml"}),
            expected_home_changes=frozenset({"registry.sqlite"}),
            allowed_subprocesses=(_git_executable(),),
        )
    )

    assert tracked.read_bytes() == tracked_before
    assert untracked.read_bytes() == untracked_before
    assert _git_status(roots.target) == status_before


def test_installed_explicit_non_git_init_is_registry_only(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "non-git")
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="non-git-setup")

    result = harness.run(
        ScenarioSpec(
            name="non-git-init",
            argv=("init", "--target", os.fspath(roots.target), "--json"),
            expected_home_changes=frozenset({"registry.sqlite"}),
            allowed_subprocesses=(_git_executable(),),
        )
    )

    payload = json.loads(result.stdout)
    assert payload["target_kind"] == "non-git"
    assert payload["binding_created"] is False
    assert not (roots.target / ".gigai").exists()
    records = open_project_registry(roots.home, create=False)[0].records()
    assert len(records) == 1
    assert records[0].target_locator == os.fspath(roots.target.resolve(strict=True))


def test_installed_init_requires_valid_setup_before_any_target_mutation(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "missing-config")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    result = ScenarioHarness(installed_gigai.command, roots).run(
        ScenarioSpec(
            name="missing-config-init",
            argv=("init", "--json"),
            expected_exit_codes=frozenset({1}),
        )
    )

    assert "run 'gigai setup'" in result.stderr
    assert not (roots.target / ".gigai").exists()
    assert not (roots.home / "registry.sqlite").exists()


def test_installed_init_refuses_invalid_config_before_target_or_registry_mutation(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "invalid-config")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="invalid-config-setup")
    (roots.home / "config.toml").write_text("not = [valid toml", encoding="utf-8")

    result = harness.run(
        ScenarioSpec(
            name="invalid-config-init",
            argv=("init", "--json"),
            expected_exit_codes=frozenset({1}),
        )
    )

    assert "not valid UTF-8 TOML" in result.stderr
    assert not (roots.target / ".gigai").exists()
    assert not (roots.home / "registry.sqlite").exists()


@pytest.mark.parametrize("failure", ("tracked", "malformed", "read-only"))
def test_installed_git_init_refuses_invalid_target_state_without_replacement(
    tmp_path: Path, installed_gigai: InstalledGigAI, failure: str
) -> None:
    roots = _roots(tmp_path, f"target-{failure}")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name=f"target-{failure}-setup")
    binding = roots.target / ".gigai" / "project.toml"
    if failure in {"tracked", "malformed"}:
        binding.parent.mkdir()
        binding.write_text("not = [valid toml", encoding="utf-8")
    if failure == "tracked":
        subprocess.run(
            [os.fspath(_git_executable()), "-C", os.fspath(roots.target), "add", ".gigai"],
            capture_output=True,
            check=True,
            shell=False,
        )
    original_mode = roots.target.stat().st_mode & 0o777
    if failure == "read-only":
        roots.target.chmod(0o555)
    try:
        result = harness.run(
            ScenarioSpec(
                name=f"target-{failure}-init",
                argv=("init", "--json"),
                expected_exit_codes=frozenset({1}),
                allowed_subprocesses=(_git_executable(),),
            )
        )
    finally:
        if failure == "read-only":
            roots.target.chmod(original_mode)

    expected = {
        "tracked": "tracked .gigai content is refused",
        "malformed": "not valid UTF-8 TOML",
        "read-only": "target root is read-only",
    }[failure]
    assert expected in result.stderr
    assert not (roots.home / "registry.sqlite").exists()


def test_installed_default_non_git_and_broken_alias_fail_without_registry(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "unavailable-targets")
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="unavailable-targets-setup")
    implicit = harness.run(
        ScenarioSpec(
            name="implicit-non-git",
            argv=("init", "--json"),
            expected_exit_codes=frozenset({1}),
            allowed_subprocesses=(_git_executable(),),
        )
    )
    broken = roots.target / "broken-alias"
    broken.symlink_to(roots.target / "missing", target_is_directory=True)
    broken_result = harness.run(
        ScenarioSpec(
            name="broken-alias",
            argv=("init", "--target", os.fspath(broken), "--json"),
            expected_exit_codes=frozenset({1}),
        )
    )

    assert "use --target" in implicit.stderr
    assert "broken alias" in broken_result.stderr
    assert not (roots.home / "registry.sqlite").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="/tmp alias semantics are macOS-specific")
def test_installed_tmp_and_private_tmp_spellings_converge(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "tmp-alias")
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="tmp-alias-setup")
    environment = {
        "HOME": os.fspath(roots.home),
        "GIGAI_HOME": os.fspath(roots.home),
        "PATH": os.pathsep.join(
            (os.fspath(installed_gigai.command.executable.parent), "/usr/bin", "/bin")
        ),
        "TMPDIR": os.fspath(roots.home / "tmp"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    with tempfile.TemporaryDirectory(prefix="gigai-g04-", dir="/tmp") as lexical:
        resolved = os.fspath(Path(lexical).resolve(strict=True))
        results = [
            subprocess.run(
                [
                    os.fspath(installed_gigai.command.executable),
                    "init",
                    "--target",
                    spelling,
                    "--json",
                ],
                cwd=roots.target,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            for spelling in (lexical, resolved)
        ]

    assert [result.returncode for result in results] == [0, 0]
    payloads = [json.loads(result.stdout) for result in results]
    assert payloads[0]["project_id"] == payloads[1]["project_id"]
    assert len(open_project_registry(roots.home, create=False)[0].records()) == 1


@pytest.mark.parametrize("failure", ("corrupt", "version", "read-only"))
def test_installed_init_fails_closed_on_registry_corruption_or_version(
    tmp_path: Path, installed_gigai: InstalledGigAI, failure: str
) -> None:
    roots = _roots(tmp_path, f"registry-{failure}")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name=f"registry-{failure}-setup")
    registry = roots.home / "registry.sqlite"
    if failure == "corrupt":
        registry.write_bytes(b"not-a-gigai-registry")
        registry.chmod(0o600)
        expected = "unreadable"
    elif failure == "version":
        connection = sqlite3.connect(registry)
        try:
            connection.execute(f"PRAGMA application_id = {REGISTRY_APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 99")
            connection.execute(
                "CREATE TABLE projects ("
                "project_id TEXT PRIMARY KEY NOT NULL, "
                "target_locator TEXT NOT NULL UNIQUE, "
                "target_kind TEXT NOT NULL"
                ") WITHOUT ROWID"
            )
            connection.commit()
        finally:
            connection.close()
        registry.chmod(0o600)
        expected = "unsupported"
    else:
        connection = sqlite3.connect(registry)
        try:
            connection.execute(f"PRAGMA application_id = {REGISTRY_APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                "CREATE TABLE projects ("
                "project_id TEXT PRIMARY KEY NOT NULL, "
                "target_locator TEXT NOT NULL UNIQUE, "
                "target_kind TEXT NOT NULL CHECK (target_kind IN ('git', 'non-git'))"
                ") WITHOUT ROWID"
            )
            connection.commit()
        finally:
            connection.close()
        registry.chmod(0o400)
        expected = "read-only"

    try:
        result = harness.run(
            ScenarioSpec(
                name=f"registry-{failure}-init",
                argv=("init", "--json"),
                expected_exit_codes=frozenset({1}),
                allowed_subprocesses=(_git_executable(),),
            )
        )
    finally:
        if failure == "read-only":
            registry.chmod(0o600)

    assert expected in result.stderr
    assert "Traceback" not in result.stderr
    assert not (roots.target / ".gigai").exists()


def test_two_installed_init_processes_converge_without_lock_or_duplicate(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "concurrent")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)
    _setup(harness, roots, installed_gigai, name="concurrent-setup")
    environment = {
        "HOME": os.fspath(roots.home),
        "GIGAI_HOME": os.fspath(roots.home),
        "PATH": os.pathsep.join(
            (os.fspath(installed_gigai.command.executable.parent), "/usr/bin", "/bin")
        ),
        "TMPDIR": os.fspath(roots.home / "tmp"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    argv = [os.fspath(installed_gigai.command.executable), "init", "--json"]
    before = _git_status(roots.target)
    processes = [
        subprocess.Popen(
            argv,
            cwd=roots.target,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    completed = [process.communicate(timeout=15) for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    payloads = [json.loads(stdout) for stdout, _ in completed]
    assert payloads[0]["project_id"] == payloads[1]["project_id"]
    assert len(open_project_registry(roots.home, create=False)[0].records()) == 1
    exclude = (roots.target / ".git" / "info" / "exclude").read_bytes()
    assert exclude.splitlines().count(b"/.gigai/") == 1
    assert _git_status(roots.target) == before
    assert not (roots.target / ".git" / "gigai-init.lock").exists()
