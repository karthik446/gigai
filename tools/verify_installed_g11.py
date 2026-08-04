"""Verify G11's installed model-port boundary without making a provider call."""

from __future__ import annotations

from importlib.metadata import distribution
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from gigai.adapters import resolve_model_adapter
from gigai.config import load_config


def _run(
    executable: Path, *args: str, env: dict[str, str], expected: int = 0
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [os.fspath(executable), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        shell=False,
    )
    if result.returncode != expected:
        raise SystemExit(
            f"installed G11 command {args!r} exited {result.returncode}: {result.stderr}"
        )
    return result


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file():
        raise SystemExit("installed gigai console script is missing")
    requirements = distribution("gigai").requires or []
    if not any(item.lower().startswith("httpx>=") for item in requirements):
        raise SystemExit("installed wheel does not declare httpx as a runtime dependency")

    with tempfile.TemporaryDirectory(prefix="gigai-g11-wheel-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workpad = root / "workpad"
        home.mkdir()
        workpad.mkdir()
        env = {
            "HOME": os.fspath(home),
            "GIGAI_HOME": os.fspath(home),
            "PATH": os.pathsep.join((os.fspath(executable.parent), "/usr/bin", "/bin")),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        _run(
            executable,
            "setup",
            "--non-interactive",
            "--workpad-root",
            os.fspath(workpad),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "openai=environment:G11_MISSING_TOKEN",
            "--endpoint",
            "openai=openai_api:openai",
            "--model-target",
            "cheap=openai:gpt-test",
            "--target-output-limit",
            "cheap=8",
            "--json",
            env=env,
        )
        config = load_config(home)
        binding = resolve_model_adapter(config, "cheap")
        request = binding.request(role="installed-verifier", prompt="offline-only")
        if (
            binding.target.endpoint.adapter != "openai_api"
            or request.max_output_tokens != 8
            or request.target_capabilities != frozenset({"text"})
        ):
            raise SystemExit("installed factory did not resolve the configured model target")

        offline = _run(executable, "doctor", "--json", env=env)
        payload = json.loads(offline.stdout)
        if payload["scope"] != "installation" or payload["overall_status"] != "WARN":
            raise SystemExit("ordinary installed doctor did not remain an offline warning-only check")
        live_missing = _run(
            executable,
            "doctor",
            "--live",
            "--model-target",
            "cheap",
            "--json",
            env=env,
            expected=1,
        )
        if "Bearer " in live_missing.stdout + live_missing.stderr:
            raise SystemExit("installed live refusal exposed authorization material")
        missing_pair = _run(executable, "doctor", "--model-target", "cheap", env=env, expected=2)
        if "must be supplied together" not in missing_pair.stderr:
            raise SystemExit("installed doctor accepted a non-explicit live target")

    print("verified installed GigAI G11 port, factory, offline doctor, and live refusal")


if __name__ == "__main__":
    main()
