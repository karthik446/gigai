from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import json
import stat

import pytest

from gigai.adapters import claude_cli, codex_cli, cli_workspace
from gigai.adapters.cli_probe import CodexLockdown
from tests.behaviors.cli_surface.test_g30_cli_adapters import _request


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_repeated_concurrent_workspace(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "home"))
    module = claude_cli if kind == "claude" else codex_cli
    catalog = json.dumps({"models": [{"slug": "synthetic"}]})
    monkeypatch.setattr(claude_cli, "require_claude_capabilities", lambda _: None)
    lockdown = CodexLockdown(model_catalog=catalog)
    monkeypatch.setattr(codex_cli, "codex_lockdown", lambda _: lockdown)
    adapter = (claude_cli.ClaudeCLIAdapter if kind == "claude" else codex_cli.CodexCLIAdapter)(executable="fake")
    seen = []

    def run(argv, *, prompt, cwd, **kwargs):
        assert prompt == _request().prompt
        assert cwd.is_dir() and not cwd.is_symlink()
        assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
        if kind == "codex":
            assert (cwd / codex_cli.MODEL_CATALOG_FILE).read_text() == catalog
            assert argv == adapter.argv(_request(), str(cwd), lockdown)
        else:
            assert list(cwd.iterdir()) == []
            assert argv == adapter.argv(_request())
        seen.append(cwd)
        return SimpleNamespace(stdout=json.dumps({"type": "result", "result": "ok"}))

    monkeypatch.setattr(module, "run_json_process", run)
    adapter.invoke(_request())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: adapter.invoke(_request()), range(12)))
    assert set(seen) == {tmp_path / "home" / "run" / f"cli-{kind}"}
    assert {p.name for p in seen[0].iterdir()} == ({codex_cli.MODEL_CATALOG_FILE} if kind == "codex" else set())
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "other-home"))
    adapter.invoke(_request())
    assert seen[-1] == tmp_path / "other-home" / "run" / f"cli-{kind}"


@pytest.mark.parametrize("kind", ["claude", "codex"])
@pytest.mark.parametrize("obstacle", ["file", "symlink", "run_symlink", "unresolved", "permission"])
def test_workspace_falls_back(tmp_path, monkeypatch, obstacle, kind):
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    outside.mkdir()
    if obstacle == "run_symlink":
        run.symlink_to(outside, target_is_directory=True)
    elif obstacle == "permission":
        original = Path.mkdir

        def denied(path, *args, **kwargs):
            if path == run:
                raise PermissionError("read-only home")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", denied)
    elif obstacle == "unresolved":
        def fail():
            raise RuntimeError("no home")
        monkeypatch.setattr("gigai.setup.default_home_root", fail)
    else:
        run.mkdir()
        path = run / f"cli-{kind}"
        if obstacle == "file":
            path.write_text("unchanged")
        else:
            path.symlink_to(outside, target_is_directory=True)
    with cli_workspace.cli_workspace(kind) as directory:
        temporary = Path(directory)
        assert temporary.is_dir()
        assert temporary != run / f"cli-{kind}"
        assert list(temporary.iterdir()) == []
    assert not temporary.exists()
    assert list(outside.iterdir()) == []
