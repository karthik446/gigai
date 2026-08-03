"""Verify G05 using only wheel-installed product code and console behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from gigai.workpad import WORKPAD_GITIGNORE, provision_workpad


GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


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
            f"installed G05 command failed with {result.returncode}: {result.stderr}"
        )
    return result


def _git(root: Path, env: dict[str, str], *args: str, expected: int = 0) -> str:
    return _run(
        ["git", "-C", os.fspath(root), *args],
        cwd=root,
        env=env,
        expected=expected,
    ).stdout


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file():
        raise SystemExit("installed gigai console script is missing")
    with tempfile.TemporaryDirectory(prefix="gigai-g05-wheel-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workpad_root = root / "workpads"
        target = root / "target"
        temporary_root = root / "tmp"
        for path in (home, workpad_root, target, temporary_root):
            path.mkdir()
        env = {
            "HOME": os.fspath(home),
            "GIGAI_HOME": os.fspath(home),
            "PATH": os.pathsep.join(
                (os.fspath(executable.parent), "/usr/local/bin", "/usr/bin", "/bin")
            ),
            "TMPDIR": os.fspath(temporary_root),
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
                os.fspath(workpad_root),
                "--editor",
                "/usr/bin/true",
                "--json",
            ],
            cwd=target,
            env=env,
        )
        _git(target, env, "init", "--initial-branch=main", "--quiet")
        binding = json.loads(
            _run(
                [os.fspath(executable), "init", "--json"],
                cwd=target,
                env=env,
            ).stdout
        )
        project_id = binding["project_id"]
        provisioned = provision_workpad(
            home_root=home,
            project_id=project_id,
            gig_id=GIG_ID,
        )
        expected = (
            workpad_root.resolve(strict=True)
            / "projects"
            / project_id
            / "gigs"
            / GIG_ID
        )
        if provisioned.path != expected:
            raise SystemExit("installed G05 provisioner chose a non-deterministic path")
        if {path.name for path in expected.iterdir()} != {".git", ".gitignore"}:
            raise SystemExit("installed G05 provisioner created semantic or extra state")
        if (expected / ".gitignore").read_bytes() != WORKPAD_GITIGNORE:
            raise SystemExit("installed G05 workpad ignore contract differs")
        _git(expected, env, "rev-parse", "--verify", "HEAD", expected=128)
        if _git(expected, env, "remote"):
            raise SystemExit("installed G05 workpad unexpectedly has a remote")
        for key, value in (
            ("gigai.project-id", project_id),
            ("gigai.gig-id", GIG_ID),
            ("user.name", "GigAI Journal"),
            ("user.email", "local@gigai.invalid"),
        ):
            if _git(expected, env, "config", "--local", "--get", key).strip() != value:
                raise SystemExit(f"installed G05 workpad marker {key!r} differs")

        path_result = _run(
            [os.fspath(executable), "workpad", "path", GIG_ID],
            cwd=target,
            env=env,
        )
        if path_result.stdout != f"{expected}\n":
            raise SystemExit("installed workpad path did not return the registered path")
        no_active = _run(
            [os.fspath(executable), "workpad", "path"],
            cwd=target,
            env=env,
            expected=1,
        )
        if "no_active_gig" not in no_active.stderr:
            raise SystemExit("installed no-ID path did not return typed no_active_gig")
        opened = _run(
            [os.fspath(executable), "open", GIG_ID],
            cwd=target,
            env=env,
        )
        if os.fspath(expected) in opened.stdout:
            raise SystemExit("installed open leaked a private workpad locator")
        for command in ("provision", "activate", "select"):
            _run(
                [os.fspath(executable), "workpad", command],
                cwd=target,
                env=env,
                expected=2,
            )

        connection = sqlite3.connect(f"file:{home / 'registry.sqlite'}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()
            rows = connection.execute(
                "SELECT gig_id, project_id, workpad_locator FROM workpads"
            ).fetchall()
            active = connection.execute("SELECT * FROM active_workpads").fetchall()
        finally:
            connection.close()
        if version != (2,) or rows != [(GIG_ID, project_id, os.fspath(expected))]:
            raise SystemExit("installed G05 registry row or schema version differs")
        if active:
            raise SystemExit("installed G05 provisioning selected an active Gig")

    print("verified installed GigAI G05 private unborn workpad and read/open surface")


if __name__ == "__main__":
    main()
