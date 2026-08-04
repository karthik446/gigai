from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from gigai.standard_pack import pack_digest
from tests.scenarios import InstalledGigAI, ScenarioHarness, ScenarioRoots, ScenarioSpec


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


def _fresh_home_changes() -> frozenset[str]:
    pack = (
        "packs/builtin/standard/1/"
        f"{pack_digest().removeprefix('sha256:')}/standard-pack.json"
    )
    directory = pack.rsplit("/", 1)[0]
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
            directory,
            pack,
        }
    )


def _python_executable(installed_gigai: InstalledGigAI) -> Path:
    candidate = installed_gigai.command.executable.parent / "python"
    return candidate.resolve() if candidate.exists() else Path(sys.executable).resolve()


def test_installed_offline_doctor_uses_factory_without_network_or_secret_access(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = ScenarioRoots.create(tmp_path / "g11-offline-factory")
    harness = ScenarioHarness(installed_gigai.command, roots)
    canary = "g11-scenario-secret-canary"
    setup = harness.run(
        ScenarioSpec(
            name="g11-remote-config",
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(roots.workpad),
                "--editor",
                "/usr/bin/true",
                "--credential-ref",
                "openai=environment:G11_SCENARIO_TOKEN",
                "--endpoint",
                "openai=openai_api:openai",
                "--model-target",
                "cheap=openai:gpt-test",
                "--target-output-limit",
                "cheap=8",
                "--json",
            ),
            expected_home_changes=_fresh_home_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("G11_SCENARIO_TOKEN", canary),),
        )
    )
    doctor = harness.run(
        ScenarioSpec(
            name="g11-offline-doctor",
            argv=("doctor", "--json"),
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("G11_SCENARIO_TOKEN", canary),),
        )
    )

    payload = json.loads(doctor.stdout)
    assert payload["scope"] == "installation"
    assert payload["overall_status"] == "PASS"
    assert {check["id"] for check in payload["checks"]} >= {"adapter.offline", "credential.openai"}
    assert doctor.guard_events == ()
    combined = setup.stdout + doctor.stdout + setup.artifact.read_text(encoding="utf-8")
    combined += doctor.artifact.read_text(encoding="utf-8")
    assert canary not in combined
