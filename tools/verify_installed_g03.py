"""Verify G03 behavior from wheel-installed code and package resources."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from gigai.config import load_config
from gigai.standard_pack import pack_path, verify_standard_pack


def run(executable: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(executable), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=10,
    )


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit("installed gigai console script is missing or not executable")
    with tempfile.TemporaryDirectory(prefix="gigai-wheel-g03-") as directory:
        root = Path(directory)
        home = root / "home"
        workpad = root / "alternate-workpad"
        argv = (
            "setup",
            "--non-interactive",
            "--home",
            os.fspath(home),
            "--workpad-root",
            os.fspath(workpad),
            "--editor",
            "/usr/bin/true",
            "--json",
        )
        first = run(executable, *argv)
        second = run(executable, *argv)
        doctor = run(executable, "doctor", "--home", os.fspath(home), "--json")
        for label, result in (("first setup", first), ("rerun", second), ("doctor", doctor)):
            if result.returncode != 0:
                raise SystemExit(f"installed {label} failed: {result.stderr}")

        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        doctor_payload = json.loads(doctor.stdout)
        if first_payload["config_changed"] is not True:
            raise SystemExit("fresh installed setup did not create configuration")
        if first_payload["standard_pack_changed"] is not True:
            raise SystemExit("fresh installed setup did not materialize the standard pack")
        if second_payload["config_changed"] is not False:
            raise SystemExit("installed setup rerun changed canonical configuration")
        if second_payload["standard_pack_changed"] is not False:
            raise SystemExit("installed setup rerun duplicated the standard pack")
        config = load_config(home)
        if config.workpad_root.resolve(strict=False) != workpad.resolve(strict=False):
            raise SystemExit("installed setup did not preserve the alternate workpad authority")
        if not pack_path(home).is_dir() or not verify_standard_pack(home)[0]:
            raise SystemExit("wheel-installed standard pack did not verify after materialization")
        if doctor_payload["overall_status"] != "PASS":
            raise SystemExit("installed offline doctor did not pass")
        checks = {check["id"]: check["status"] for check in doctor_payload["checks"]}
        for identifier in (
            "mount.atomic_replace",
            "mount.interprocess_lock",
            "adapter.offline",
            "editor.resolved",
        ):
            if checks.get(identifier) != "PASS":
                raise SystemExit(f"installed doctor check {identifier!r} did not pass")
    print("verified installed GigAI G03 setup, idempotency, pack, and offline doctor")


if __name__ == "__main__":
    main()
