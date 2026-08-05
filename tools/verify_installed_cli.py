"""Verify the current truthful CLI surface against an installed wheel."""

from __future__ import annotations

from importlib.metadata import distribution, version
import os
from pathlib import Path
import subprocess
import sys


def run(*args: str) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit("installed gigai console script is missing or not executable")
    return subprocess.run(
        [os.fspath(executable), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def main() -> None:
    metadata = distribution("gigai")
    requirements = metadata.requires or []
    if not any(item.lower().startswith("click>=") for item in requirements):
        raise SystemExit("installed wheel metadata does not declare Click at runtime")
    if not any(
        item.name == "gigai" and item.value == "gigai.cli:cli"
        for item in metadata.entry_points
    ):
        raise SystemExit("installed wheel metadata does not register gigai.cli:cli")

    help_result = run("--help")
    version_result = run("--version")
    bare_result = run()
    incomplete_create_result = run("create")

    if help_result.returncode != 0:
        raise SystemExit(f"gigai --help failed: {help_result.stderr}")
    if version_result.returncode != 0:
        raise SystemExit(f"gigai --version failed: {version_result.stderr}")
    if version_result.stdout != f"gigai {version('gigai')}\n":
        raise SystemExit("gigai --version did not use installed distribution metadata")
    if "Commands:" not in help_result.stdout:
        raise SystemExit("gigai --help did not expose the approved command group")
    for command in (
        "setup",
        "doctor",
        "init",
        "create",
        "feedback",
        "revise",
        "approve",
        "reject",
        "gigs",
        "proposals",
        "status",
        "show",
        "history",
        "plan",
        "workpad",
        "check",
        "open",
    ):
        if command not in help_result.stdout:
            raise SystemExit(f"gigai --help omitted approved command {command!r}")
    for command in ("run", "goals"):
        if command in help_result.stdout:
            raise SystemExit(f"gigai --help exposed undeclared command {command!r}")
    if bare_result.returncode == 0 or incomplete_create_result.returncode == 0:
        raise SystemExit("the CLI exposed an undeclared operational success path")

    workpad_help = run("workpad", "--help")
    if workpad_help.returncode != 0 or "path" not in workpad_help.stdout:
        raise SystemExit("gigai workpad did not expose the approved path operation")
    for forbidden in ("provision", "create", "activate", "select"):
        if forbidden in workpad_help.stdout:
            raise SystemExit(
                f"gigai workpad exposed forbidden lifecycle operation {forbidden!r}"
            )

    print(
        "verified installed GigAI CLI: help, version, setup, doctor, init, create, "
        "feedback, revise, approve, reject, gigs, proposals, status, show, history, "
        "plan, workpad path, check, and open only"
    )


if __name__ == "__main__":
    main()
