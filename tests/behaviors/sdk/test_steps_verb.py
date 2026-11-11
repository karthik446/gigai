"""BK2: `gigai <gig> steps [set]`, the step overrides in `status`, and the `gig check` warning, run as the CLI.

The fixture gig `eval-fixture` is installed into a scratch site; GIGAI_HOME is a scratch home whose config.toml holds
the targets of `test_ctx_model.make_home`. No model is called except the deterministic one.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.behaviors.gigs.test_loader import CLI, _add_fixture, _run
from tests.behaviors.sdk.test_ctx_model import A_TEXT, B_TEXT, make_home

GIG = "eval-fixture"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    _add_fixture(directory, GIG)
    return directory


@pytest.fixture
def home(tmp_path: Path) -> Path:
    make_home(tmp_path / "home")
    return tmp_path / "home"


def _gigai(tmp_path: Path, site: Path, home: Path, *argv: str, code: int = 0) -> subprocess.CompletedProcess:
    completed = _run(tmp_path, site, *argv, env_extra={"GIGAI_HOME": str(home)})
    assert completed.returncode == code, (completed.stdout, completed.stderr)
    return completed


def _steps(tmp_path: Path, site: Path, home: Path) -> dict[str, dict[str, object]]:
    return json.loads(_gigai(tmp_path, site, home, GIG, "steps", "--json").stdout)


def _file(home: Path) -> Path:
    return home / "gigs" / GIG / "steps.toml"


def test_steps_lists_each_step_with_its_target_and_source(tmp_path: Path, site: Path, home: Path) -> None:
    rows = _steps(tmp_path, site, home)

    assert {step: (row["target"], row["source"]) for step, row in rows.items()} == {
        "answer": ("deterministic-a", "manifest"),
        "judge": ("deterministic-b", "manifest"),
        "solve": ("deterministic-a", "manifest"),
    }
    assert rows["judge"]["settings"] == {"temperature": 0.0}
    text = _gigai(tmp_path, site, home, GIG, "steps").stdout
    assert "answer: deterministic-a (deterministic fixture-a, manifest)" in text
    assert "steps" in _gigai(tmp_path, site, home, GIG, "--help").stdout


def test_ask_calls_each_steps_target(tmp_path: Path, site: Path, home: Path) -> None:
    answer = json.loads(_gigai(tmp_path, site, home, GIG, "ask", "answer", "2+2").stdout)
    judge = json.loads(_gigai(tmp_path, site, home, GIG, "ask", "judge", "2+2").stdout)

    assert (answer["output"], judge["output"]) == (A_TEXT, B_TEXT)


def test_steps_set_writes_a_private_steps_toml_that_steps_status_and_calls_follow(tmp_path: Path, site: Path, home: Path) -> None:
    written = _gigai(tmp_path, site, home, GIG, "steps", "set", "judge", "deterministic-a")

    assert str(_file(home)) in written.stdout
    assert stat.S_IMODE(_file(home).stat().st_mode) == 0o600
    assert tomllib.loads(_file(home).read_text(encoding="utf-8")) == {"steps": {"judge": "deterministic-a"}}
    assert (_steps(tmp_path, site, home)["judge"]["target"], _steps(tmp_path, site, home)["judge"]["source"]) == ("deterministic-a", "override")
    status = json.loads(_gigai(tmp_path, site, home, GIG, "status", "--json").stdout)
    assert status["step_overrides"] == {"judge": "deterministic-a"}
    assert "step override: judge -> deterministic-a" in _gigai(tmp_path, site, home, GIG, "status").stdout
    assert json.loads(_gigai(tmp_path, site, home, GIG, "ask", "judge", "2+2").stdout)["output"] == A_TEXT

    removed = _gigai(tmp_path, site, home, GIG, "steps", "set", "judge", "-")
    assert "override removed" in removed.stdout
    assert tomllib.loads(_file(home).read_text(encoding="utf-8")) == {"steps": {}}
    assert json.loads(_gigai(tmp_path, site, home, GIG, "status", "--json").stdout)["step_overrides"] == {}
    assert "no override to remove" in _gigai(tmp_path, site, home, GIG, "steps", "set", "judge", "-").stdout


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("judge", "no-such-target"), "'no-such-target'"),
        (("summarise", "deterministic-a"), "[steps].summarise"),
        (("judge", "claude-cli"), "step 'judge' sets 'temperature', which the claude_cli target 'claude-cli' cannot carry"),
    ],
)
def test_steps_set_refuses_without_writing(tmp_path: Path, site: Path, home: Path, argv: tuple[str, str], message: str) -> None:
    refused = _gigai(tmp_path, site, home, GIG, "steps", "set", *argv, code=1)

    assert message in refused.stderr
    assert "Traceback" not in refused.stderr
    assert not _file(home).exists()


@pytest.mark.parametrize(
    ("content", "mode", "message"),
    [
        ("not = [toml\n", 0o600, "is not TOML"),
        ('[steps]\njudge = "deterministic-a"\nextra = 1\n', 0o600, "only a [steps] table"),
        ('[other]\njudge = "deterministic-a"\n', 0o600, "only a [steps] table"),
        ('[steps]\njudge = "deterministic-a"\n', 0o664, "writable by group or others"),
    ],
)
def test_a_bad_steps_toml_fails_every_verb_that_reads_it(tmp_path: Path, site: Path, home: Path, content: str, mode: int, message: str) -> None:
    _file(home).parent.mkdir(parents=True)
    _file(home).write_text(content, encoding="utf-8")
    _file(home).chmod(mode)

    for argv in (("steps",), ("status", "--json"), ("steps", "set", "answer", "deterministic-b")):
        refused = _gigai(tmp_path, site, home, GIG, *argv, code=1)
        assert message in refused.stderr and "Traceback" not in refused.stderr
    assert _file(home).read_text(encoding="utf-8") == content


def test_a_symlinked_steps_toml_is_refused(tmp_path: Path, site: Path, home: Path) -> None:
    target = tmp_path / "elsewhere.toml"
    target.write_text("[steps]\n", encoding="utf-8")
    target.chmod(0o600)
    _file(home).parent.mkdir(parents=True)
    _file(home).symlink_to(target)

    refused = _gigai(tmp_path, site, home, GIG, "steps", "set", "answer", "deterministic-b", code=1)
    assert "symlink" in refused.stderr
    assert target.read_text(encoding="utf-8") == "[steps]\n"


def test_concurrent_steps_set_leave_one_whole_steps_toml(tmp_path: Path, site: Path, home: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path), "GIGAI_HOME": str(home), "PYTHONPATH": str(site)}
    sets = [("answer", "deterministic-a"), ("answer", "deterministic-b"), ("judge", "deterministic-a"), ("judge", "offline-default")] * 2
    children = [
        subprocess.Popen([sys.executable, "-c", CLI, GIG, "steps", "set", step, target], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for step, target in sets
    ]
    assert [child.wait(timeout=120) for child in children] == [0] * len(children)

    overrides = tomllib.loads(_file(home).read_text(encoding="utf-8"))["steps"]
    assert set(overrides) <= {"answer", "judge"} and overrides
    assert all(target in {"deterministic-a", "deterministic-b", "offline-default"} for target in overrides.values())
    assert stat.S_IMODE(_file(home).stat().st_mode) == 0o600
    assert sorted(path.name for path in _file(home).parent.iterdir()) == ["steps.toml"]


def test_gig_check_warns_about_an_override_of_a_step_the_manifest_lacks(tmp_path: Path, site: Path, home: Path) -> None:
    _file(home).parent.mkdir(parents=True)
    _file(home).write_text('[steps]\nretired = "deterministic-a"\n', encoding="utf-8")
    _file(home).chmod(0o600)

    checked = _run(tmp_path, site, "gig", "check", GIG, env_extra={"GIGAI_HOME": str(home)})
    assert "WARN  steps  steps.toml overrides step 'retired'" in checked.stdout, checked.stdout

    assert "override removed" in _gigai(tmp_path, site, home, GIG, "steps", "set", "retired", "-").stdout
    assert "steps.toml" not in _run(tmp_path, site, "gig", "check", GIG, env_extra={"GIGAI_HOME": str(home)}).stdout
