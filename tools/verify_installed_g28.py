"""Verify the G28 readiness surface through the installed console script."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile

from gigai.roles import RoleReference, resolve_role
from gigai.validators import SCHEMA_NAMES
sys.path.insert(0, str(Path(__file__).resolve().parent))
from installed_schema_expectations import EXPECTED_LEGACY_SCHEMA_NAMES


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return result


def main() -> int:
    if tuple(SCHEMA_NAMES) != EXPECTED_LEGACY_SCHEMA_NAMES:
        raise SystemExit(
            "installed G28 schema identity mismatch: "
            f"expected={EXPECTED_LEGACY_SCHEMA_NAMES!r}, actual={tuple(SCHEMA_NAMES)!r}"
        )
    if "role-reference.schema.json" not in SCHEMA_NAMES:
        raise SystemExit("installed role-reference schema is missing")
    registered = resolve_role(
        {"namespace": "model_invocation", "id": "proposal-questioner", "version": 1},
        namespace="model_invocation",
    )
    if registered.reference != RoleReference("model_invocation", "proposal-questioner"):
        raise SystemExit("installed role registry did not resolve structured role")
    if resolve_role("diagnostic", namespace="model_invocation").status != "legacy_unresolved":
        raise SystemExit("installed role registry guessed a legacy role")

    executable = Path(sys.prefix) / "bin" / "gigai"
    if not executable.is_file():
        raise SystemExit(f"installed gigai console script is missing: {executable}")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="gigai-g28-installed-") as raw_root:
        root = Path(raw_root)
        home = root / "home"
        workpads = root / "workpads"
        target = root / "target"
        target.mkdir()
        setup = _run(
            [
                str(executable),
                "setup",
                "--non-interactive",
                "--home",
                str(home),
                "--workpad-root",
                str(workpads),
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
            ],
            env=environment,
        )
        if json.loads(setup.stdout)["schema_version"] != "2.0":
            raise SystemExit("installed setup did not write the current configuration")
        _run(
            [
                str(executable),
                "init",
                "--home",
                str(home),
                "--target",
                str(target),
                "--username",
                "installed-verifier",
                "--json",
            ],
            env=environment,
        )
        config_text = (home / "config.toml").read_text(encoding="utf-8")
        if 'planner = "remote"' not in config_text:
            raise SystemExit("installed setup did not persist the create model selection")

        invocation_path = root / "invocation.json"
        invocation_path.write_text(
            json.dumps(
                {
                    "protocol_version": "1",
                    "invocation_id": "inv_g28-installed",
                    "trigger": "$gigai",
                    "actor": {
                        "kind": "agent",
                        "id": "codex",
                        "session_id": "g28-test",
                    },
                    "command": "create",
                    "target": {"home": str(home), "project": "g28-project"},
                    "input": {
                        "intent": "Create a bounded installed proposal.",
                        "proposal": {"summary": "Inspect the fixture repository."},
                    },
                    "requested": {
                        "roles": ["gig_creator"],
                        "models": ["remote"],
                        "capabilities": [],
                    },
                    "consent": [],
                }
            ),
            encoding="utf-8",
        )
        created = _run(
            [
                str(executable),
                "create",
                "installed-g28",
                "--home",
                str(home),
                "--target",
                str(target),
                "--invocation",
                str(invocation_path),
                "--json",
            ],
            env=environment,
        )
        payload = json.loads(created.stdout)
        if payload["status"] != "proposed" or payload["authority_created"] is not False:
            raise SystemExit("installed create did not preserve the proposal-only boundary")
    print("verified installed GigAI G28 evaluation, roles, setup, and browser-first create")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
