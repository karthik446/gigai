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


def run(
    executable: Path, *args: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(executable), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=10,
    )


def _diagnostics(result: subprocess.CompletedProcess[str]) -> str:
    """Keep machine-readable CLI failures visible without weakening assertions."""

    streams = []
    if result.stdout.strip():
        streams.append(f"stdout={result.stdout.strip()}")
    if result.stderr.strip():
        streams.append(f"stderr={result.stderr.strip()}")
    return "; ".join(streams) or "<no diagnostic output>"


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit("installed gigai console script is missing or not executable")
    with tempfile.TemporaryDirectory(prefix="gigai-wheel-g03-") as directory:
        root = Path(directory)
        home = root / "home"
        workpad = root / "alternate-workpad"
        home.mkdir()
        (home / "tmp").mkdir()
        # Pin HOME/PATH and configure an explicit deterministic-style remote
        # target, exactly as tools/verify_installed_g04.py does. Without this,
        # `setup --non-interactive` falls through to auto-discovering Codex or
        # Claude on PATH via a login-shell PATH hydration; on a clean runner
        # with neither installed (e.g. a fresh GitHub Actions image) that
        # discovery finds nothing and setup_command (src/gigai/cli.py:1213)
        # raises "no usable model runtime is configured", which
        # `_raise_cli_error` reports as a JSON payload on stdout, not stderr
        # (src/gigai/cli.py:170-182) -- exactly the "installed first setup
        # failed: " with no message seen in CI run 35660274375. Whether that
        # discovery accidentally succeeds is an unrelated, unpinned fact about
        # the host running the verifier, not something this test should rely
        # on either way.
        env = {
            "HOME": os.fspath(home),
            "GIGAI_HOME": os.fspath(home),
            "PATH": os.pathsep.join(
                (os.fspath(executable.parent), "/usr/local/bin", "/usr/bin", "/bin")
            ),
            "TMPDIR": os.fspath(home / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        argv = (
            "setup",
            "--non-interactive",
            "--home",
            os.fspath(home),
            "--workpad-root",
            os.fspath(workpad),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:gpt-test",
            "--create-model-target",
            "remote",
            "--json",
        )
        first = run(executable, *argv, env=env)
        second = run(executable, *argv, env=env)
        doctor = run(executable, "doctor", "--home", os.fspath(home), "--json", env=env)
        for label, result in (("first setup", first), ("rerun", second), ("doctor", doctor)):
            if result.returncode != 0:
                raise SystemExit(f"installed {label} failed: {_diagnostics(result)}")

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
        # WARN, not PASS, is the correct offline outcome here: setup configured
        # an unset `GIGAI_PROVIDER_TOKEN` credential reference (see the setup
        # argv above), and doctor legitimately warns that the reference isn't
        # materialized yet. tools/verify_installed_g11.py asserts the same
        # WARN for the identical reason. Only the checks unrelated to that
        # reference must remain PASS.
        if doctor_payload["overall_status"] != "WARN":
            raise SystemExit("installed offline doctor did not report the expected warning")
        checks = {check["id"]: check["status"] for check in doctor_payload["checks"]}
        if checks.get("credential.provider") != "WARN":
            raise SystemExit("installed doctor did not warn on the unset credential reference")
        for identifier in (
            "config.valid",
            "path.home",
            "path.workpad",
            "mount.atomic_replace",
            "mount.interprocess_lock",
            "editor.resolved",
        ):
            if checks.get(identifier) != "PASS":
                raise SystemExit(f"installed doctor check {identifier!r} did not pass")
    print("verified installed GigAI G03 setup, idempotency, pack, and offline doctor")


if __name__ == "__main__":
    main()
