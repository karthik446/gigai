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


def _roots(tmp_path: Path, name: str) -> ScenarioRoots:
    return ScenarioRoots.create(tmp_path / name)


def _pack_relative() -> str:
    return (
        "packs/builtin/standard/1/"
        f"{pack_digest().removeprefix('sha256:')}/standard-pack.json"
    )


def _fresh_home_changes() -> frozenset[str]:
    pack_file = _pack_relative()
    pack_directory = pack_file.rsplit("/", 1)[0]
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
            pack_directory,
            pack_file,
        }
    )


def _python_executable(installed_gigai: InstalledGigAI) -> Path:
    candidate = installed_gigai.command.executable.parent / "python"
    return candidate.resolve() if candidate.exists() else Path(sys.executable).resolve()


def test_installed_fresh_setup_and_rerun_are_exactly_idempotent(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "fresh-idempotent")
    harness = ScenarioHarness(installed_gigai.command, roots)
    argv = (
        "setup",
        "--non-interactive",
        "--workpad-root",
        os.fspath(roots.workpad),
        "--editor",
        "/usr/bin/true",
        "--editor-arg=--wait",
        "--credential-ref",
        "provider=environment:GIGAI_PROVIDER_TOKEN",
        "--json",
    )
    first = harness.run(
        ScenarioSpec(
            name="fresh-setup",
            argv=argv,
            expected_home_changes=_fresh_home_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("GIGAI_PROVIDER_TOKEN", "provider-secret-canary"),),
        )
    )
    second = harness.run(
        ScenarioSpec(
            name="rerun-setup",
            argv=argv,
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("GIGAI_PROVIDER_TOKEN", "provider-secret-canary"),),
        )
    )

    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["config_changed"] is True
    assert first_payload["standard_pack_changed"] is True
    assert second_payload["config_changed"] is False
    assert second_payload["standard_pack_changed"] is False
    assert first.target_before == first.target_after == second.target_before == second.target_after
    assert first.workpad_before == first.workpad_after
    assert second.workpad_before == second.workpad_after
    assert first.guard_events == second.guard_events == ()
    config = (roots.home / "config.toml").read_text(encoding="utf-8")
    combined = config + first.stdout + second.stdout
    combined += first.artifact.read_text(encoding="utf-8")
    combined += second.artifact.read_text(encoding="utf-8")
    assert "provider-secret-canary" not in combined
    assert config.count("[[credentials]]") == 1
    assert config.count("name = \"standard\"") == 1


def test_installed_doctor_is_offline_read_only_and_proves_the_configured_mount(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "offline-doctor")
    harness = ScenarioHarness(installed_gigai.command, roots)
    harness.run(
        ScenarioSpec(
            name="doctor-prerequisite-setup",
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(roots.workpad),
                "--editor",
                "/usr/bin/true",
                "--credential-ref",
                "provider=environment:GIGAI_PROVIDER_TOKEN",
                "--json",
            ),
            expected_home_changes=_fresh_home_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("GIGAI_PROVIDER_TOKEN", "doctor-secret-canary"),),
        )
    )
    result = harness.run(
        ScenarioSpec(
            name="offline-doctor",
            argv=("doctor", "--json"),
            allowed_subprocesses=(_python_executable(installed_gigai),),
            extra_env=(("GIGAI_PROVIDER_TOKEN", "doctor-secret-canary"),),
        )
    )
    payload = json.loads(result.stdout)

    assert payload["overall_status"] == "PASS"
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["mount.atomic_replace"]["status"] == "PASS"
    assert checks["mount.interprocess_lock"]["status"] == "PASS"
    assert checks["path.workpad"]["evidence_safe_to_share"] == [
        "configured_path_available=true",
        "configured_path_writable=true",
    ]
    assert checks["adapter.offline"]["evidence_safe_to_share"] == [
        "network_used=false",
        "credential_used=false",
    ]
    assert result.home_before == result.home_after
    assert result.workpad_before == result.workpad_after
    assert result.target_before == result.target_after
    assert result.guard_events == ()
    assert "doctor-secret-canary" not in result.stdout
    assert "doctor-secret-canary" not in result.artifact.read_text(encoding="utf-8")


def test_installed_setup_preserves_an_alternate_authoritative_mount(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "alternate-mount")
    alternate = roots.workpad / "external-volume"
    result = ScenarioHarness(installed_gigai.command, roots).run(
        ScenarioSpec(
            name="alternate-mount",
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(alternate),
                "--editor",
                "/usr/bin/true",
                "--json",
            ),
            expected_home_changes=_fresh_home_changes(),
            expected_workpad_changes=frozenset({"external-volume"}),
            allowed_subprocesses=(_python_executable(installed_gigai),),
        )
    )
    payload = json.loads(result.stdout)

    assert payload["workpad_root"] == os.fspath(alternate)
    assert f'workpad_root = "{alternate}"' in (
        roots.home / "config.toml"
    ).read_text(encoding="utf-8")
    assert not (roots.home / "workpads").exists()


@pytest.mark.parametrize(
    ("body", "needle"),
    (
        ("not = [valid toml", "not valid UTF-8 TOML"),
        ('schema_version = "99.0"\n', "unsupported"),
    ),
)
def test_installed_setup_refuses_corrupt_or_incompatible_config_without_mutation(
    tmp_path: Path,
    installed_gigai: InstalledGigAI,
    body: str,
    needle: str,
) -> None:
    roots = _roots(tmp_path, "bad-config-" + ("syntax" if body.startswith("not") else "version"))
    (roots.home / "config.toml").write_text(body, encoding="utf-8")
    before = (roots.home / "config.toml").read_bytes()
    result = ScenarioHarness(installed_gigai.command, roots).run(
        ScenarioSpec(
            name="bad-config",
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(roots.workpad),
                "--editor",
                "/usr/bin/true",
            ),
            expected_exit_codes=frozenset({1}),
        )
    )

    assert needle in result.stderr
    assert (roots.home / "config.toml").read_bytes() == before
    assert result.home_before == result.home_after
    assert result.workpad_before == result.workpad_after
    assert result.target_before == result.target_after
    assert result.guard_events == ()


def test_installed_interactive_setup_reviews_effects_before_applying(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "interactive-setup")
    result = ScenarioHarness(installed_gigai.command, roots).run(
        ScenarioSpec(
            name="interactive-setup",
            argv=("setup", "--editor", "/usr/bin/true"),
            stdin="\n\n\n\n\n",
            expected_home_changes=_fresh_home_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
        )
    )

    assert "Authoritative workpad root" in result.stdout
    assert "Editor argv:" in result.stdout
    assert "Apply this setup?" in result.stdout
    assert result.exit_code == 0


def test_installed_setup_refuses_read_only_config_without_partial_mutation(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "read-only-config")
    harness = ScenarioHarness(installed_gigai.command, roots)
    setup_argv = (
        "setup",
        "--non-interactive",
        "--workpad-root",
        os.fspath(roots.workpad),
        "--editor",
        "/usr/bin/true",
    )
    harness.run(
        ScenarioSpec(
            name="read-only-prerequisite",
            argv=setup_argv,
            expected_home_changes=_fresh_home_changes(),
            allowed_subprocesses=(_python_executable(installed_gigai),),
        )
    )
    config_path = roots.home / "config.toml"
    config_path.chmod(0o400)
    before = config_path.read_bytes()
    result = harness.run(
        ScenarioSpec(
            name="read-only-config",
            argv=setup_argv,
            expected_exit_codes=frozenset({1}),
        )
    )

    assert "read-only" in result.stderr
    assert "no changes were made" in result.stderr
    assert config_path.read_bytes() == before
    assert result.home_before == result.home_after
    assert result.workpad_before == result.workpad_after
    assert result.target_before == result.target_after


def test_installed_doctor_fails_on_missing_config_without_creating_state(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "doctor-missing-config")
    result = ScenarioHarness(installed_gigai.command, roots).run(
        ScenarioSpec(
            name="doctor-missing-config",
            argv=("doctor", "--json"),
            expected_exit_codes=frozenset({1}),
        )
    )
    payload = json.loads(result.stdout)

    assert payload["overall_status"] == "FAIL"
    assert payload["checks"][0]["id"] == "config.valid"
    assert "run 'gigai setup'" in payload["checks"][0]["summary"]
    assert result.home_before == result.home_after
    assert result.workpad_before == result.workpad_after
    assert result.target_before == result.target_after


def test_installed_doctor_never_falls_back_when_configured_mount_disappears(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = _roots(tmp_path, "doctor-missing-mount")
    alternate = roots.workpad / "removable-volume"
    harness = ScenarioHarness(installed_gigai.command, roots)
    harness.run(
        ScenarioSpec(
            name="missing-mount-prerequisite",
            argv=(
                "setup",
                "--non-interactive",
                "--workpad-root",
                os.fspath(alternate),
                "--editor",
                "/usr/bin/true",
            ),
            expected_home_changes=_fresh_home_changes(),
            expected_workpad_changes=frozenset({"removable-volume"}),
            allowed_subprocesses=(_python_executable(installed_gigai),),
        )
    )
    alternate.rmdir()
    result = harness.run(
        ScenarioSpec(
            name="doctor-missing-mount",
            argv=("doctor", "--json"),
            expected_exit_codes=frozenset({1}),
        )
    )
    payload = json.loads(result.stdout)
    checks = {check["id"]: check for check in payload["checks"]}

    assert payload["overall_status"] == "FAIL"
    assert checks["path.workpad"]["status"] == "FAIL"
    assert checks["mount.atomic_replace"]["status"] == "FAIL"
    assert checks["mount.interprocess_lock"]["status"] == "FAIL"
    assert not (roots.home / "workpads").exists()
    assert result.home_before == result.home_after
    assert result.workpad_before == result.workpad_after
