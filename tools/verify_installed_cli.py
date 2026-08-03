"""Verify G02 behavior against an installed wheel, not the source checkout."""

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
    planned_result = run("init")

    if help_result.returncode != 0:
        raise SystemExit(f"gigai --help failed: {help_result.stderr}")
    if version_result.returncode != 0:
        raise SystemExit(f"gigai --version failed: {version_result.stderr}")
    if version_result.stdout != f"gigai {version('gigai')}\n":
        raise SystemExit("gigai --version did not use installed distribution metadata")
    if "Commands:" in help_result.stdout:
        raise SystemExit("gigai --help exposed an undeclared command group")
    if bare_result.returncode == 0 or planned_result.returncode == 0:
        raise SystemExit("the minimal CLI exposed an undeclared operational success path")

    print("verified installed GigAI CLI: --help and --version only")


if __name__ == "__main__":
    main()
