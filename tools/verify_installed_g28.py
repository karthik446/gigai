"""Verify the G28 readiness surface through the installed console script."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from gigai.roles import RoleReference, resolve_role
from gigai.validators import SCHEMA_NAMES


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return result


def main() -> int:
    if len(SCHEMA_NAMES) != 30:
        raise SystemExit(f"installed G28 schema inventory is {len(SCHEMA_NAMES)}, expected 30")
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
                "--json",
            ],
            env=environment,
        )
        if json.loads(setup.stdout)["schema_version"] != "2.0":
            raise SystemExit("installed setup did not write the current configuration")
        _run([str(executable), "init", "--home", str(home), "--target", str(target), "--json"], env=environment)
        config_text = (home / "config.toml").read_text(encoding="utf-8")
        if 'planner = "offline-default"' not in config_text:
            raise SystemExit("installed setup did not persist the create model selection")

        manifest = {
            "schema_version": "1.0",
            "kind": "behavior_manifest",
            "corpus_id": "installed-g28",
            "corpus_version": "1",
            "contamination": {"rule": "tuning_never_certifies_final"},
            "cases": [
                {
                    "case_id": "installed-create",
                    "case_version": "1",
                    "split": "final_held_out_acceptance",
                    "labels": ["positive"],
                    "expected": ["browser_launched"],
                    "forbidden": ["target_mutated", "run_created"],
                }
            ],
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        observations = {
            "schema_version": "1.0",
            "manifest_digest": _digest(manifest),
            "solver": {"kind": "installed_fixture", "id": "g28-installed", "version": "1"},
            "contamination_check": "clean",
            "tuning_cutoff": "installed-g28",
            "cases": [{"case_id": "installed-create", "observed": ["browser_launched"]}],
        }
        observations_path = root / "observations.json"
        observations_path.write_text(json.dumps(observations), encoding="utf-8")
        report = _run(
            [
                str(executable),
                "eval",
                "behavior",
                "--manifest",
                str(manifest_path),
                "--observations",
                str(observations_path),
                "--split",
                "final_held_out_acceptance",
            ],
            env=environment,
        )
        if json.loads(report.stdout)["candidate_judge_scored"] is not False:
            raise SystemExit("installed eval report overclaimed candidate judge quality")

        process = subprocess.Popen(
            [str(executable), "create", "installed-g28", "--home", str(home), "--no-open", "--json"],
            cwd=target,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stderr is not None
            line = process.stderr.readline().strip()
            if re.fullmatch(r"GigAI local interview: http://127\.0\.0\.1:\d+/session/[A-Za-z0-9_-]+", line) is None:
                raise SystemExit(f"installed create did not launch HTMX: {line!r}")
        finally:
            process.kill()
            process.communicate(timeout=10)
    print("verified installed GigAI G28 evaluation, roles, setup, and browser-first create")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
