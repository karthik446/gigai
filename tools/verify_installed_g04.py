"""Verify G04 target binding through only an installed wheel and console script."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tomllib


REGISTRY_APPLICATION_ID = 0x47494741
REGISTRY_SCHEMA_VERSION = 2


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != expected:
        raise SystemExit(
            f"installed G04 command failed with {result.returncode}: {result.stderr}"
        )
    return result


def _git(
    root: Path,
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", os.fspath(root), *args], cwd=root, env=env)


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file():
        raise SystemExit("installed gigai console script is missing")
    with tempfile.TemporaryDirectory(prefix="gigai-g04-wheel-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workpad = root / "workpad"
        git_target = root / "git-target"
        non_git_target = root / "non-git-target"
        for path in (home, workpad, git_target, non_git_target):
            path.mkdir()
        (home / "tmp").mkdir()
        env = {
            "HOME": os.fspath(home),
            "GIGAI_HOME": os.fspath(home),
            "PATH": os.pathsep.join(
                (os.fspath(executable.parent), "/usr/local/bin", "/usr/bin", "/bin")
            ),
            "TMPDIR": os.fspath(home / "tmp"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        _run(
            [
                os.fspath(executable),
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(workpad),
                "--editor",
                "/usr/bin/true",
                "--json",
            ],
            cwd=git_target,
            env=env,
        )
        _git(git_target, env, "init", "--initial-branch=main", "--quiet")
        (git_target / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        _git(git_target, env, "add", "tracked.txt")
        _git(
            git_target,
            env,
            "-c",
            "user.name=GigAI Wheel Verifier",
            "-c",
            "user.email=wheel@gigai.invalid",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        )
        status_before = _git(
            git_target,
            env,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        first = json.loads(
            _run(
                [os.fspath(executable), "init", "--json"],
                cwd=git_target,
                env=env,
            ).stdout
        )
        project_path = git_target / ".gigai" / "project.toml"
        project_bytes = project_path.read_bytes()
        binding = tomllib.loads(project_bytes.decode("utf-8"))
        exclude = (git_target / ".git" / "info" / "exclude").read_bytes()
        second = json.loads(
            _run(
                [os.fspath(executable), "init", "--json"],
                cwd=git_target,
                env=env,
            ).stdout
        )
        status_after = _git(
            git_target,
            env,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        if status_after != status_before:
            raise SystemExit("installed Git init changed machine-readable status")
        if first["project_id"] != second["project_id"]:
            raise SystemExit("installed Git init was not ID-idempotent")
        if second["binding_created"] or second["registry_changed"] or second["exclude_changed"]:
            raise SystemExit("installed Git init rerun reported an unexpected mutation")
        if binding != {
            "schema_version": "1.0",
            "project_id": first["project_id"],
            "workpad_locator": f"registry:{first['project_id']}",
        }:
            raise SystemExit("installed project binding is not the minimal path-free contract")
        if exclude.splitlines().count(b"/.gigai/") != 1:
            raise SystemExit("installed Git init did not create exactly one exclude entry")
        if project_bytes != project_path.read_bytes():
            raise SystemExit("installed Git init changed project bytes on rerun")

        non_git = json.loads(
            _run(
                [
                    os.fspath(executable),
                    "init",
                    "--target",
                    os.fspath(non_git_target),
                    "--json",
                ],
                cwd=git_target,
                env=env,
            ).stdout
        )
        if non_git["target_kind"] != "non-git" or (non_git_target / ".gigai").exists():
            raise SystemExit("installed non-Git init was not registry-only")

        registry = home / "registry.sqlite"
        connection = sqlite3.connect(f"file:{registry}?mode=ro", uri=True)
        try:
            application_id = connection.execute("PRAGMA application_id").fetchone()
            user_version = connection.execute("PRAGMA user_version").fetchone()
            rows = connection.execute(
                "SELECT project_id, target_kind FROM projects ORDER BY project_id"
            ).fetchall()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        if application_id != (REGISTRY_APPLICATION_ID,):
            raise SystemExit("installed registry application identity is invalid")
        if user_version != (REGISTRY_SCHEMA_VERSION,):
            raise SystemExit("installed registry schema version is invalid")
        if tables != {"projects", "workpads", "active_workpads"}:
            raise SystemExit("installed registry does not expose the exact v2 table set")
        if sorted(kind for _, kind in rows) != ["git", "non-git"]:
            raise SystemExit("installed registry does not contain exactly two verified bindings")
        if (git_target / ".git" / "gigai-init.lock").exists():
            raise SystemExit("installed Git init left its temporary lock behind")

    print("verified installed GigAI G04 Git and non-Git target binding")


if __name__ == "__main__":
    main()
