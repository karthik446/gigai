from __future__ import annotations

from importlib.metadata import requires, version
import json
import os
from pathlib import Path
import sys

import pytest

from tests.scenarios import (
    CommandTarget,
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    ScenarioViolation,
    TreeManifest,
    copy_fixture_repository,
    create_recording_substitute,
    invoke_recording_substitute,
)


PLANNED_COMMANDS = (
    "create",
    "run",
    "goals",
    "preview",
    "rehearse",
    "eval",
)


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


def scenario_roots(tmp_path: Path, name: str) -> ScenarioRoots:
    return ScenarioRoots.create(tmp_path / name)


def python_probe(code: str) -> CommandTarget:
    return CommandTarget(
        executable=Path(sys.executable).resolve(),
        argv_prefix=("-c", code),
        allowed_read_roots=(
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path(__file__).resolve().parents[1],
        ),
    )


def test_runtime_metadata_declares_click_and_console_script_is_installed(
    installed_gigai: InstalledGigAI,
) -> None:
    runtime_requirements = requires("gigai") or []

    assert any(requirement.lower().startswith("click>=") for requirement in runtime_requirements)
    assert not any(
        requirement.lower().startswith("click") and "extra == 'test'" in requirement.lower()
        for requirement in runtime_requirements
    )
    assert installed_gigai.command.executable.is_file()
    assert os.access(installed_gigai.command.executable, os.X_OK)


def test_installed_help_version_and_goal_approved_commands_are_the_only_surface(
    tmp_path: Path,
    installed_gigai: InstalledGigAI,
) -> None:
    roots = scenario_roots(tmp_path, "installed-surface")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)

    help_result = harness.run(ScenarioSpec(name="help", argv=("--help",)))
    version_result = harness.run(ScenarioSpec(name="version", argv=("--version",)))
    bare_result = harness.run(
        ScenarioSpec(name="bare", argv=(), expected_exit_codes=frozenset({2}))
    )
    planned_result = harness.run(
        ScenarioSpec(name="planned-command", argv=("create",), expected_exit_codes=frozenset({2}))
    )

    assert help_result.argv[0] == os.fspath(installed_gigai.command.executable)
    assert "Usage: gigai [OPTIONS] [COMMAND] [ARGS]..." in help_result.stdout
    assert "--help" in help_result.stdout
    assert "--version" in help_result.stdout
    assert "Commands:" in help_result.stdout
    assert "doctor" in help_result.stdout
    assert "init" in help_result.stdout
    assert "open" in help_result.stdout
    assert "setup" in help_result.stdout
    assert "workpad" in help_result.stdout
    for command in PLANNED_COMMANDS:
        assert command not in help_result.stdout
    assert version_result.stdout == f"gigai {version('gigai')}\n"
    assert "Choose 'setup', 'doctor', 'init', 'workpad', or 'open'" in bare_result.stderr
    assert "No such command 'create'" in planned_result.stderr

    for result in (help_result, version_result, bare_result, planned_result):
        assert result.target_before == result.target_after
        assert result.workpad_before == result.workpad_after
        assert result.home_before == result.home_after
        assert result.fixtures_before == result.fixtures_after
        assert result.guard_events == ()


@pytest.mark.parametrize(
    ("kind", "expected_file"),
    (("python", "src/example.py"), ("non-python", "src/example.js")),
)
def test_equivalent_manifest_mechanics_cover_python_and_non_python_repositories(
    tmp_path: Path,
    installed_gigai: InstalledGigAI,
    kind: str,
    expected_file: str,
) -> None:
    roots = scenario_roots(tmp_path, f"fixture-{kind}")
    copy_fixture_repository(kind, roots.target, fixture_root=roots.fixtures)
    harness = ScenarioHarness(installed_gigai.command, roots)

    result = harness.run(ScenarioSpec(name=f"help-{kind}", argv=("--help",)))

    assert result.target_before.git.present is True
    assert result.target_before.git.head is not None
    assert result.target_before.git.branch == "main"
    assert result.target_before.git.status == ()
    assert expected_file in {entry.path for entry in result.target_before.files}
    assert result.target_before == result.target_after
    assert result.fixtures_before == result.fixtures_after
    assert f"{kind}/{expected_file}" in {
        entry.path for entry in result.fixtures_before.files
    }


def test_manifest_detects_exact_content_and_git_changes(tmp_path: Path) -> None:
    roots = scenario_roots(tmp_path, "manifest-diff")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    before = TreeManifest.capture(roots.target)

    source = roots.target / "src" / "example.py"
    source.write_text("def message() -> str:\n    return 'changed'\n", encoding="utf-8")
    after = TreeManifest.capture(roots.target)

    assert before.changed_paths(after) == frozenset({"src/example.py", "@git"})
    before_file = next(item for item in before.files if item.path == "src/example.py")
    after_file = next(item for item in after.files if item.path == "src/example.py")
    assert before_file.sha256 != after_file.sha256
    assert before.git.working_diff_sha256 != after.git.working_diff_sha256


def test_undeclared_target_write_fails_but_exact_allowlist_passes(tmp_path: Path) -> None:
    roots = scenario_roots(tmp_path, "target-write-denied")
    command = python_probe("from pathlib import Path; Path('touched.txt').write_text('exact\\n')")
    harness = ScenarioHarness(command, roots)

    with pytest.raises(ScenarioViolation) as failure:
        harness.run(ScenarioSpec(name="unexpected-target-write", argv=()))

    assert any(
        violation.startswith("unexpected_target_changes")
        for violation in failure.value.result.violations
    )

    allowed_roots = scenario_roots(tmp_path, "target-write-allowed")
    allowed_result = ScenarioHarness(command, allowed_roots).run(
        ScenarioSpec(
            name="declared-target-write",
            argv=(),
            expected_target_changes=frozenset({"touched.txt"}),
        )
    )
    assert allowed_result.target_before.changed_paths(allowed_result.target_after) == frozenset(
        {"touched.txt"}
    )


def test_scenario_replaces_ambient_home_workpad_and_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_home = Path.home().resolve()
    monkeypatch.setenv("HOME", os.fspath(real_home))
    monkeypatch.setenv("GIGAI_HOME", os.fspath(real_home / ".gigai"))
    monkeypatch.setenv("GIGAI_WORKPAD_ROOT", os.fspath(real_home / "configured-workpads"))
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    roots = scenario_roots(tmp_path, "ambient-isolation")
    command = python_probe(
        "import json, os; print(json.dumps({key: os.environ.get(key) for key in "
        "('HOME', 'GIGAI_HOME', 'GIGAI_WORKPAD_ROOT', 'OPENAI_API_KEY')}, sort_keys=True))"
    )

    result = ScenarioHarness(command, roots, real_home=real_home).run(
        ScenarioSpec(name="ambient-isolation", argv=())
    )
    observed = json.loads(result.stdout)

    assert observed == {
        "GIGAI_HOME": os.fspath(roots.home),
        "GIGAI_WORKPAD_ROOT": os.fspath(roots.workpad),
        "HOME": os.fspath(roots.home),
        "OPENAI_API_KEY": None,
    }
    assert "ambient-secret" not in result.artifact.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ("HOME", "PYTHONPATH", "GIGAI_HARNESS_ALLOWED_WRITE_ROOTS"))
def test_scenario_cannot_override_harness_policy(tmp_path: Path, key: str) -> None:
    roots = scenario_roots(tmp_path, f"reserved-{key.lower().replace('_', '-')}")
    harness = ScenarioHarness(python_probe("pass"), roots)

    with pytest.raises(ValueError, match="harness-owned environment key"):
        harness.run(
            ScenarioSpec(
                name="reserved-environment",
                argv=(),
                extra_env=((key, "override"),),
            )
        )


@pytest.mark.parametrize(
    ("name", "code", "extra_env", "violation"),
    (
        (
            "network-probe",
            "import socket; socket.getaddrinfo('example.com', 443)",
            (),
            "network_access",
        ),
        (
            "real-home-probe",
            "import os; from pathlib import Path; Path(os.environ['PROBE_PATH']).read_bytes()",
            (("PROBE_PATH", os.fspath(Path.home() / ".ssh" / "config")),),
            "real_home_access",
        ),
        (
            "undeclared-write-probe",
            "import os; from pathlib import Path; Path(os.environ['PROBE_PATH']).write_text('x')",
            (("PROBE_PATH", "{forbidden}"),),
            "undeclared_write",
        ),
        (
            "subprocess-network-probe",
            "import subprocess; subprocess.run(['curl', 'https://example.com'], check=False)",
            (),
            "undeclared_subprocess",
        ),
    ),
)
def test_process_guard_fails_closed(
    tmp_path: Path,
    name: str,
    code: str,
    extra_env: tuple[tuple[str, str], ...],
    violation: str,
) -> None:
    roots = scenario_roots(tmp_path, name)
    resolved_env = tuple(
        (key, os.fspath(roots.scenario / "forbidden") if value == "{forbidden}" else value)
        for key, value in extra_env
    )
    harness = ScenarioHarness(python_probe(code), roots)

    with pytest.raises(ScenarioViolation) as failure:
        harness.run(
            ScenarioSpec(
                name=name,
                argv=(),
                extra_env=resolved_env + (("API_TOKEN", "do-not-publish-this"),),
            )
        )

    result = failure.value.result
    assert violation in result.violations
    artifact_text = result.artifact.read_text(encoding="utf-8")
    assert "do-not-publish-this" not in artifact_text
    assert os.fspath(Path.home()) not in artifact_text
    artifact = json.loads(artifact_text)
    assert artifact["argv"]
    assert artifact["environment"]
    assert artifact["exit_code"] != 0
    assert artifact["duration_ns"] > 0
    assert artifact["guard_events"]
    assert artifact["target_before"] == artifact["target_after"]
    assert artifact["workpad_before"] == artifact["workpad_after"]


@pytest.mark.parametrize("boundary", ("editor", "adapter", "tool"))
def test_recording_substitutes_preserve_structured_argv_without_a_shell(
    tmp_path: Path,
    boundary: str,
) -> None:
    executable = create_recording_substitute(tmp_path, boundary)
    log = tmp_path / f"{boundary}.json"
    literal_argument = "$(touch shell-was-used)"

    completed = invoke_recording_substitute(
        executable,
        ("--wait", "file with spaces.md", literal_argument),
        boundary=boundary,
        log=log,
    )

    assert completed.returncode == 0
    assert json.loads(log.read_text(encoding="utf-8")) == {
        "argv": ["--wait", "file with spaces.md", literal_argument],
        "boundary": boundary,
    }
    assert not (tmp_path / "shell-was-used").exists()


def test_scenario_allows_only_an_explicit_recording_subprocess(tmp_path: Path) -> None:
    roots = scenario_roots(tmp_path, "allowed-recorder")
    executable = create_recording_substitute(roots.fixtures, "editor")
    log = roots.artifacts / "editor.json"
    command = python_probe(
        "import os, subprocess; subprocess.run([os.environ['EDITOR'], '--wait', "
        "'file with spaces.md'], check=True)"
    )

    result = ScenarioHarness(command, roots).run(
        ScenarioSpec(
            name="allowed-recorder",
            argv=(),
            allowed_subprocesses=(executable,),
            extra_env=(
                ("EDITOR", os.fspath(executable)),
                ("GIGAI_RECORDING_BOUNDARY", "editor"),
                ("GIGAI_RECORDING_LOG", os.fspath(log)),
            ),
        )
    )

    assert result.violations == ()
    assert json.loads(log.read_text(encoding="utf-8")) == {
        "argv": ["--wait", "file with spaces.md"],
        "boundary": "editor",
    }


def test_recording_substitute_rejects_shell_string_argv(tmp_path: Path) -> None:
    executable = create_recording_substitute(tmp_path, "editor")

    with pytest.raises(TypeError, match="never a shell string"):
        invoke_recording_substitute(
            executable,
            "--wait document.md",  # type: ignore[arg-type]
            boundary="editor",
            log=tmp_path / "editor.json",
        )
