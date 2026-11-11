"""``gigai gig install | update | uninstall``: the uv argv for a ``uv tool`` env (rebuilt from its receipt, install and
update first with core pinned, then as the receipt has it) and a plain venv (core pinned with ``-c``), refusals (pipx,
system Python, gigai itself, a gig named like a core command or another installed gig, no uv, a gig needing another
gigai), the rollback when core moves anyway, and the translation of uv's resolver failure. Everything runs with
``--dry-run`` or a stand-in for ``subprocess.run``: no uv, no index, no network; the scratch-env runs against real uv
are recorded in the packet report."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.gigs import install
from gigai.gigs.install import install_command, option_args, read_spec, requirement_string, resolver_message, uninstall_command, update_command
from tests.behaviors.gigs.gig_wheel import FIXTURES, build_sdist, build_wheel

CORE = metadata.version("gigai")
HELLO = FIXTURES / "hello"

RECEIPT = """\
[tool]
requirements = [
    { name = "gigai" },
    { name = "gigai-gig-notes", specifier = ">=1.2" },
    { name = "gigai-test-gig-hello", path = "/wheels/gigai_test_gig_hello-0.0.9-py3-none-any.whl" },
]
constraints = [{ name = "httpx", specifier = "<1" }]
python = "3.12"
entrypoints = [
    { name = "gigai", install-path = "/home/u/.local/bin/gigai", from = "gigai" },
]

[tool.options]
no-index = true
find-links = ["file:///wheels"]
index = [{ url = "https://pypi.example/simple", explicit = false, default = true, format = "simple", authenticate = "auto" }]
"""

TOOL_RESOLVER_FAILURE = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because only gigai-test-gig-needy==0.1.0 is available and
      gigai-test-gig-needy==0.1.0 depends on gigai>=0.3, we can conclude that
      all versions of gigai-test-gig-needy depend on gigai>=0.3.
      And because only gigai<=0.2.0a9 is available and you require
      gigai-test-gig-needy, we can conclude that your requirements are
      unsatisfiable.
"""

VENV_RESOLVER_FAILURE = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because gigai-test-gig-newer==0.1.0 depends on gigai>=0.2.0a5 and
      gigai==0.2.0a2, we can conclude that gigai-test-gig-newer==0.1.0 cannot
      be used.
"""


@pytest.fixture
def uv(monkeypatch):
    """``uv`` on PATH and a ``subprocess.run`` that records argv (and the ``-c`` file's text) instead of running."""

    calls: list[dict] = []

    def run(argv, **kwargs):
        assert kwargs.get("shell") is False
        # temporary file paths shrink to the names --dry-run prints, so argv compares against the same lists
        call = {"argv": [Path(arg).name if arg.endswith(".txt") else arg for arg in argv], "env": kwargs.get("env")}
        if "-c" in argv and argv[0] == "/bin/uv":
            call["constraints"] = Path(argv[argv.index("-c") + 1]).read_text(encoding="utf-8")
        calls.append(call)
        return subprocess.CompletedProcess(argv, calls_rc.pop(0) if calls_rc else 0, stderr=stderr.pop(0) if stderr else "")

    calls_rc: list[int] = []
    stderr: list[str] = []
    # install.py imports subprocess lazily (gigai.cli import time), so the stand-ins go on the modules themselves
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(subprocess, "run", run)
    run.calls, run.rc, run.stderr = calls, calls_rc, stderr  # type: ignore[attr-defined]
    return run


def _core_moves(monkeypatch, uv) -> None:
    """Core reads as 9.9.9 after the first unpinned ``uv tool install``, until a later run pins it back to
    ``gigai==CORE``: core moved by the rebuild after the pinned gig step (the pinned step itself cannot move it)."""

    def version() -> str:
        tool = [call["argv"] for call in uv.calls if call["argv"][1:3] == ["tool", "install"]]
        plain = next((at for at, argv in enumerate(tool) if PIN not in argv), None)
        if plain is not None and not any(PIN in argv for argv in tool[plain + 1 :]):
            return "9.9.9"
        return CORE

    monkeypatch.setattr(install.gig_manifest, "installed_core_version", version)


def _tool_env(tmp_path: Path, monkeypatch, receipt: str = RECEIPT) -> Path:
    prefix = tmp_path / "tools" / "gigai"
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(prefix / "bin" / "python"))
    return prefix


def _venv(tmp_path: Path, monkeypatch) -> Path:
    prefix = tmp_path / "venv"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(prefix / "bin" / "python"))
    return prefix


def _invoke(command, *args: str):
    return CliRunner().invoke(command, list(args), prog_name=f"gigai gig {command.name}")


def _argv(result, step: int = 0) -> list[str]:
    """The ``step``-th uv command printed (``--dry-run`` prints the pinned gig step, then the rebuild)."""

    return shlex.split([line for line in result.stdout.splitlines() if line.startswith("uv ")][step])


PIN = f"gigai=={CORE}"
CARRIED = ["--python", "3.12", "--constraints", "constraints.txt", "--no-index", "--find-links", "file:///wheels", "--default-index", "https://pypi.example/simple"]
NOTES = ["--with", "gigai-gig-notes>=1.2"]
OLD_HELLO = ["--with", "gigai-test-gig-hello @ file:///wheels/gigai_test_gig_hello-0.0.9-py3-none-any.whl"]


def test_tool_install_pins_core_then_rebuilds_the_full_receipt_list_without_pin_or_reinstall(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    wheel = build_wheel(HELLO, tmp_path / "dist")
    result = _invoke(install_command, str(wheel), "--dry-run")
    assert result.exit_code == 0, result.output
    # the old hello entry is replaced by SPEC; the other gig keeps its specifier; core is pinned, then the receipt's own
    assert _argv(result, 0) == ["uv", "tool", "install", PIN, *NOTES, "--with", str(wheel), *CARRIED]
    assert _argv(result, 1) == ["uv", "tool", "install", "gigai", *NOTES, "--with", str(wheel), *CARRIED]
    assert result.stdout.splitlines().count("  constraints.txt: httpx<1") == 2
    assert not any("--reinstall" in _argv(result, step) for step in (0, 1))
    assert not any(arg.startswith("gigai==") for arg in _argv(result, 1))
    assert uv.calls == []


def test_install_and_update_run_the_pinned_step_then_the_rebuild(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: [])
    assert _invoke(install_command, "gigai-gig-other").exit_code == 0
    assert _invoke(update_command, "gigai-gig-notes").exit_code == 0
    assert [call["argv"][1:] for call in uv.calls] == [
        ["tool", "install", PIN, *NOTES, *OLD_HELLO, "--with", "gigai-gig-other", *CARRIED],
        ["tool", "install", "gigai", *NOTES, *OLD_HELLO, "--with", "gigai-gig-other", *CARRIED],
        ["tool", "install", PIN, *NOTES, *OLD_HELLO, *CARRIED, *REFRESH_NOTES],
        ["tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED],
    ]


def test_tool_commands_run_in_the_tool_dir_and_bin_dir_of_this_env(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "otherbin"))
    result = _invoke(uninstall_command, "gigai-gig-notes")
    assert result.exit_code == 0, result.output
    (call,) = uv.calls
    assert call["env"]["UV_TOOL_DIR"] == str(tmp_path / "tools")
    assert call["env"]["UV_TOOL_BIN_DIR"] == "/home/u/.local/bin"  # where the receipt says bin/gigai went
    assert call["env"]["PATH"] == install.os.environ["PATH"]


def test_tool_install_without_receipt_python_passes_no_python(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch, '[tool]\nrequirements = [{ name = "gigai", specifier = ">=0.2" }]\n')
    result = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    assert result.exit_code == 0, result.output
    assert _argv(result, 0) == ["uv", "tool", "install", PIN, "--with", "gigai-gig-notes"]
    assert _argv(result, 1) == ["uv", "tool", "install", "gigai>=0.2", "--with", "gigai-gig-notes"]


def test_a_core_from_a_source_tree_is_not_pinned(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch, '[tool]\nrequirements = [{ name = "gigai", directory = "/src/gigai", editable = true }]\n')
    result = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [shlex.join(["uv", "tool", "install", "--editable", "/src/gigai", "--with", "gigai-gig-notes"])]


REFRESH_NOTES = ["--reinstall-package", "gigai-gig-notes", "--upgrade-package", "gigai-gig-notes"]


def test_tool_update_refreshes_only_the_gig_and_keeps_the_list(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    result = _invoke(update_command, "gigai-gig-notes", "--dry-run")
    assert result.exit_code == 0, result.output
    # --reinstall-package alone keeps the installed version; --upgrade-package lets the gig (only) take its newest
    # release that works with the pinned core; the rebuild after it needs neither
    assert _argv(result, 0) == ["uv", "tool", "install", PIN, *NOTES, *OLD_HELLO, *CARRIED, *REFRESH_NOTES]
    assert _argv(result, 1) == ["uv", "tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED]


def test_venv_update_refreshes_only_the_gig_with_core_pinned(tmp_path, monkeypatch, uv):
    python = str(_venv(tmp_path, monkeypatch) / "bin" / "python")
    result = _invoke(update_command, "click", "--dry-run")
    assert result.exit_code == 0, result.output
    refresh = ["--reinstall-package", "click", "--upgrade-package", "click"]
    assert _argv(result) == ["uv", "pip", "install", *refresh, "--python", python, "-c", "constraints.txt", "click"]


def test_tool_uninstall_drops_the_gig_from_the_list(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    result = _invoke(uninstall_command, "gigai_test_gig_hello", "--dry-run")
    assert result.exit_code == 0, result.output
    assert _argv(result) == ["uv", "tool", "install", "gigai", *NOTES, *CARRIED]


def test_tool_update_of_a_gig_not_in_the_receipt_fails(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    result = _invoke(update_command, "gigai-gig-other", "--dry-run")
    assert result.exit_code == 1
    assert "not in this uv tool env's receipt" in result.output


def test_core_and_its_shipped_gigs_are_not_gigs_to_install_update_or_uninstall(tmp_path, monkeypatch, uv):
    for make_env in (_tool_env, _venv):
        make_env(tmp_path / make_env.__name__, monkeypatch)
        for command, target in ((uninstall_command, "gigai"), (update_command, "scout")):
            result = _invoke(command, target, "--dry-run")
            assert result.exit_code == 2
            assert "part of gigai itself" in result.stderr
        for target in ("gigai", "GigAI[mcp]>=0.2"):
            result = _invoke(install_command, target)
            assert result.exit_code == 2
            assert "is gigai itself, not a gig" in result.stderr
    assert uv.calls == []


def test_receipt_entries_become_the_requirement_uv_records_the_same_way():
    assert requirement_string({"name": "x", "path": "/w/x-1-py3-none-any.whl"}) == "x @ file:///w/x-1-py3-none-any.whl"
    assert requirement_string({"name": "x", "directory": "/src/x"}) == "x @ file:///src/x"
    assert requirement_string({"name": "x", "git": "file:///r/x?rev=main&subdirectory=gig#0123abc"}) == "x @ git+file:///r/x@main#subdirectory=gig"
    assert requirement_string({"name": "x", "url": "https://h/x-1.tar.gz"}) == "x @ https://h/x-1.tar.gz"
    assert requirement_string({"name": "x", "extras": ["a", "b"], "specifier": ">=1", "marker": "sys_platform == 'darwin'"}) == (
        "x[a,b]>=1 ; sys_platform == 'darwin'"
    )
    assert install._with_args([{"name": "x", "directory": "/src/x", "editable": True}]) == ["--with-editable", "/src/x"]


def test_unknown_receipt_entry_or_option_is_never_silently_dropped(tmp_path, monkeypatch, uv):
    assert option_args({"find-links": ["file:///w"], "torch-backend": "cpu"}) == (["--find-links", "file:///w"], ["torch-backend"])
    _tool_env(tmp_path, monkeypatch, '[tool]\nrequirements = [{ name = "gigai" }]\n[tool.options]\ntorch-backend = "cpu"\n')
    noted = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    assert noted.exit_code == 0
    assert "note: receipt option 'torch-backend' is not carried over" in noted.stderr
    _tool_env(tmp_path / "b", monkeypatch, '[tool]\nrequirements = [{ name = "gigai" }, { name = "y", group = "g" }]\n')
    refused = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    assert refused.exit_code == 2
    assert "cannot rebuild receipt entry" in refused.stderr


def test_venv_install_pins_installed_core_with_a_constraints_file(tmp_path, monkeypatch, uv):
    prefix = _venv(tmp_path, monkeypatch)
    wheel = build_wheel(HELLO, tmp_path / "dist")
    dry = _invoke(install_command, str(wheel), "--dry-run")
    assert dry.exit_code == 0, dry.output
    assert _argv(dry) == ["uv", "pip", "install", "--python", str(prefix / "bin" / "python"), "-c", "constraints.txt", str(wheel)]
    assert f"  constraints.txt: gigai=={CORE}" in dry.stdout.splitlines()

    monkeypatch.setattr(install, "_installed_gigs", lambda dist: [])
    real = _invoke(install_command, str(wheel))
    assert real.exit_code == 0, real.output
    (call,) = uv.calls
    assert call["argv"][:5] == ["/bin/uv", "pip", "install", "--python", str(prefix / "bin" / "python")]
    assert call["constraints"] == f"gigai=={CORE}\n"
    assert call["argv"][5:] == ["-c", "constraints.txt", str(wheel)]
    assert _argv(real)[6].endswith("/constraints.txt")  # the run prints the real temporary path it passes


def test_venv_uninstall_runs_uv_pip_uninstall(tmp_path, monkeypatch, uv):
    prefix = _venv(tmp_path, monkeypatch)
    result = _invoke(uninstall_command, "click", "--dry-run")
    assert result.exit_code == 0, result.output
    assert _argv(result) == ["uv", "pip", "uninstall", "--python", str(prefix / "bin" / "python"), "click"]
    missing = _invoke(uninstall_command, "gigai-gig-not-installed", "--dry-run")
    assert missing.exit_code == 1
    assert "is not installed" in missing.output


def test_pipx_and_system_python_are_refused_with_the_supported_ways(tmp_path, monkeypatch, uv):
    prefix = _venv(tmp_path, monkeypatch)
    (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")
    pipx = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "system"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "system"))
    system = _invoke(install_command, "gigai-gig-notes", "--dry-run")
    for result, where in ((pipx, "pipx env"), (system, "not a venv")):
        assert result.exit_code == 2
        assert where in result.stderr
        assert "`uv tool install gigai`, or into a venv" in result.stderr
    assert uv.calls == []


def test_missing_uv_prints_the_command_and_exits_2(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = _invoke(uninstall_command, "gigai-gig-notes")
    assert result.exit_code == 2
    assert _argv(result) == ["uv", "tool", "install", "gigai", *OLD_HELLO, *CARRIED]
    assert "uv is not on PATH" in result.stderr
    assert uv.calls == []


@pytest.mark.parametrize("taken", ["doctor", "gig", "scout"])
def test_a_gig_named_like_a_core_command_is_refused_before_installing(tmp_path, monkeypatch, uv, taken):
    _tool_env(tmp_path, monkeypatch)
    wheel = build_wheel(HELLO, tmp_path / "dist", entry_name=taken)
    result = _invoke(install_command, str(wheel))
    assert result.exit_code == 2
    assert f"declares gig '{taken}', already a gigai command; not installed" in result.stderr
    assert uv.calls == []


def test_a_gig_named_like_an_installed_gig_of_another_dist_is_refused(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    hello = SimpleNamespace(name="hello", dist=SimpleNamespace(name="gigai-test-gig-hello"))
    monkeypatch.setattr(install.gig_manifest, "entry_points", lambda: (hello,))
    twin = build_wheel(HELLO, tmp_path / "dist", name="gigai-test-gig-hello-twin")
    result = _invoke(install_command, str(twin))
    assert result.exit_code == 2
    assert "declares gig 'hello', already a gig from gigai-test-gig-hello; not installed" in result.stderr
    assert uv.calls == []
    # the dist that owns the name may be installed again (a new build, a reinstall)
    again = _invoke(install_command, str(build_wheel(HELLO, tmp_path / "dist")), "--dry-run")
    assert again.exit_code == 0, again.output


def test_an_index_gig_whose_name_collides_is_rolled_back(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: ["doctor"])
    result = _invoke(install_command, "gigai-gig-doctor")
    assert result.exit_code == 2
    assert "declares gig 'doctor'" in result.stderr
    installed, rolled_back = (call["argv"][1:] for call in uv.calls)  # the receipt's rebuild also drops the pin
    assert installed[2] == PIN and installed[-len(CARRIED) - 2 : -len(CARRIED)] == ["--with", "gigai-gig-doctor"]
    assert rolled_back == ["tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED]


def test_venv_rollback_uninstalls_only_what_this_command_added(tmp_path, monkeypatch, uv):
    python = str(_venv(tmp_path, monkeypatch) / "bin" / "python")
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: ["doctor"])
    added = _invoke(install_command, "gigai-gig-doctor")
    assert added.exit_code == 2
    assert "declares gig 'doctor', already a gigai command; not installed" in added.stderr
    assert uv.calls[1]["argv"][1:] == ["pip", "uninstall", "--python", python, "gigai-gig-doctor"]
    uv.calls.clear()
    present = _invoke(install_command, "click")  # installed before the command: left in place, never uninstalled
    assert present.exit_code == 2
    assert "it was installed before this command, so it is left in place" in present.stderr
    (call,) = uv.calls
    assert call["argv"][1:3] == ["pip", "install"]


NEEDS_NEWER_NOTES = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because only gigai-gig-notes==2.0 is available and gigai-gig-notes==2.0
      depends on gigai>=0.3, we can conclude that all versions of
      gigai-gig-notes depend on gigai>=0.3.
      And because you require gigai==0.2.0a2 and gigai-gig-notes>=1.2, we can
      conclude that your requirements are unsatisfiable.
"""


@pytest.mark.parametrize(("command", "target", "gig", "stderr"), [
    (install_command, "gigai-test-gig-needy", "gigai-test-gig-needy", TOOL_RESOLVER_FAILURE),
    (update_command, "gigai-gig-notes", "gigai-gig-notes", NEEDS_NEWER_NOTES),  # the only release left needs newer
])
def test_a_gig_needing_a_newer_gigai_fails_in_the_pinned_step_and_changes_nothing(tmp_path, monkeypatch, uv, command, target, gig, stderr):
    prefix = _tool_env(tmp_path, monkeypatch)
    uv.rc.append(1)
    uv.stderr.append(stderr)
    result = _invoke(command, target)
    assert result.exit_code == 2
    needs = ">=0.3"
    message = f"gigai gig: gig {gig} needs gigai {needs}, you have {CORE}; run `uv tool upgrade gigai` first"
    assert message in result.stderr and result.stderr.count("gigai gig:") == 1  # said once
    (call,) = uv.calls  # the pinned step only: uv resolves before it changes anything, so no rebuild, no rollback
    assert call["argv"][3] == PIN
    assert (prefix / "uv-receipt.toml").read_text(encoding="utf-8") == RECEIPT


def test_a_gig_that_moves_core_after_the_pinned_step_is_rolled_back_and_refused(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    _core_moves(monkeypatch, uv)
    monkeypatch.setattr(metadata, "requires", lambda dist: ["click>=8", "gigai>=9 ; python_version >= '3.12'"])
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: ["needy"])
    result = _invoke(install_command, "gigai-test-gig-needy")
    assert result.exit_code == 2
    assert f"gig gigai-test-gig-needy needs gigai >=9, you have {CORE}; run `uv tool upgrade gigai` first" in result.stderr
    installed, rebuilt, pinned, restored = (call["argv"][1:] for call in uv.calls)  # no gig check: nothing was kept
    assert installed == ["tool", "install", PIN, *NOTES, *OLD_HELLO, "--with", "gigai-test-gig-needy", *CARRIED]
    assert rebuilt == ["tool", "install", "gigai", *NOTES, *OLD_HELLO, "--with", "gigai-test-gig-needy", *CARRIED]
    assert pinned == ["tool", "install", PIN, *NOTES, *OLD_HELLO, *CARRIED]
    assert restored == ["tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED]


def test_an_update_that_moves_core_is_rolled_back_and_refused(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    _core_moves(monkeypatch, uv)
    monkeypatch.setattr(metadata, "requires", lambda dist: ["gigai-gig-base>=1"])  # core moved through a dependency
    result = _invoke(update_command, "gigai-gig-notes")
    assert result.exit_code == 2
    assert f"gig gigai-gig-notes moved gigai to 9.9.9, you have {CORE}; run `uv tool upgrade gigai` first" in result.stderr
    updated, rebuilt, pinned, restored = (call["argv"][1:] for call in uv.calls)
    assert updated == ["tool", "install", PIN, *NOTES, *OLD_HELLO, *CARRIED, *REFRESH_NOTES]
    assert rebuilt == ["tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED]
    assert pinned == ["tool", "install", PIN, *NOTES, *OLD_HELLO, *CARRIED]
    assert restored == ["tool", "install", "gigai", *NOTES, *OLD_HELLO, *CARRIED]


def test_a_failed_rollback_says_once_how_to_put_core_back(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    _core_moves(monkeypatch, uv)
    uv.rc.extend([0, 0, 1])
    uv.stderr.extend(["", "", TOOL_RESOLVER_FAILURE])
    result = _invoke(install_command, "gigai-test-gig-needy")
    assert result.exit_code == 2
    assert f"putting gigai {CORE} and the gigs back failed (see above): run `uv tool install gigai=={CORE}`" in result.stderr
    assert result.stderr.count("gigai gig:") == 1


def test_a_failed_rebuild_after_the_pinned_step_says_the_pin_is_still_there(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: [])
    uv.rc.extend([0, 1])
    result = _invoke(update_command, "gigai-gig-notes")
    assert result.exit_code == 2
    assert f"the gig is installed, but this env's receipt still pins {PIN}" in result.stderr
    assert result.stderr.count("gigai gig:") == 1


def test_install_prints_the_gig_check_verdict(tmp_path, monkeypatch, uv):
    _tool_env(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "_installed_gigs", lambda dist: ["hello"])
    uv.rc.extend([0, 0, 1])
    result = _invoke(install_command, str(build_wheel(HELLO, tmp_path / "dist")))
    assert result.exit_code == 0, result.output
    check = uv.calls[2]["argv"]
    assert check[0] == sys.executable and check[-3:] == ["gig", "check", "hello"]
    assert "gig hello: gig check has FAIL items" in result.stdout


def test_resolver_failure_names_the_gig_and_both_versions(tmp_path, monkeypatch, uv):
    _venv(tmp_path, monkeypatch)
    monkeypatch.setattr(install.gig_manifest, "installed_core_version", lambda: "0.2.0a2")
    uv.rc.append(1)
    uv.stderr.append(VENV_RESOLVER_FAILURE)
    result = _invoke(install_command, "gigai-test-gig-newer")
    assert result.exit_code == 2
    assert "gigai gig: gig gigai-test-gig-newer needs gigai >=0.2.0a5, you have 0.2.0a2; upgrade gigai in this venv first" in result.stderr
    assert result.stderr.count("gigai gig:") == 1
    uv.rc.append(1)
    uv.stderr.append("error: Failed to fetch")
    failed = _invoke(install_command, "gigai-test-gig-newer")
    assert failed.exit_code == 1 and "gigai gig: uv failed" in failed.stderr  # any other uv failure keeps uv's code
    assert resolver_message("Because only gigai<=0.2.0 is available and toygig3==0.1.0 depends on gigai>=0.3,<1, we", "0.2.0", "uv tool") == (
        "gig toygig3 needs gigai >=0.3,<1, you have 0.2.0; run `uv tool upgrade gigai` first"
    )
    assert resolver_message("error: Failed to fetch", "0.2.0", "venv") is None


def test_spec_names_come_from_the_dist_itself(tmp_path):
    wheel = read_spec(str(build_wheel(HELLO, tmp_path / "w")))
    sdist = read_spec(str(build_sdist(HELLO, tmp_path / "s")))
    project = read_spec(str(HELLO))
    for spec in (wheel, sdist, project):
        assert (spec.dist, spec.gigs) == ("gigai-test-gig-hello", ("hello",))
        assert Path(spec.requirement).is_absolute()
    assert read_spec("gigai-gig-notes>=1") == install.Spec("gigai-gig-notes>=1", "gigai-gig-notes", None)


def test_every_verb_has_help_and_dry_run():
    for command in (install_command, update_command, uninstall_command):
        result = _invoke(command, "--help")
        assert result.exit_code == 0
        assert command.help and "--dry-run" in result.output
