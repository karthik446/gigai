"""SCOPE-ADD-3 C1 (operator decision A): the Codex CLI adapter's opt-in reasoning effort.

Only the ranker's copy (``effort_copy``, ``honours_reasoning_effort=True``)
passes ``-c model_reasoning_effort=<effort>``. The default instance -- the
assess and quick-assess path -- keeps its argv byte for byte, whatever the
request's effort says.
"""

from __future__ import annotations

from pathlib import Path
import json
import stat
import sys
from types import SimpleNamespace
from typing import Any, cast

from gigai.adapters.codex_cli import CodexCLIAdapter
from gigai.adapters.port import InvocationRequest
from gigai.scout.find_jobs import model_rank

_EXE = "/opt/fake/codex"
_DIR = "/tmp/gigai-codex-x"


def _request(model: str = "default", effort: str | None = None) -> InvocationRequest:
    return InvocationRequest(
        target_name="codex-default",
        endpoint_name="codex",
        model=model,
        role="reviewer",
        prompt="Rank these.",
        target_capabilities=frozenset({"text"}),
        reasoning_effort=effort,
    )


# The argv the assess path ran before C1, spelled out: any change is deliberate.
_ASSESS_ARGV = (
    _EXE, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
    "--disable", "shell_tool", "--disable", "memories",  # 0110-004: no shell, no memories
    "--skip-git-repo-check", "--cd", _DIR, "-",
)


def test_default_assess_argv_is_unchanged_whatever_the_effort() -> None:
    adapter = CodexCLIAdapter(executable=_EXE)

    assert adapter.honours_reasoning_effort is False
    for effort in (None, "low", "high"):
        assert adapter.argv(_request(effort=effort), _DIR) == _ASSESS_ARGV
    assert adapter.argv(_request(model="gpt-x", effort="low"), _DIR) == (
        *_ASSESS_ARGV[:-1], "--model", "gpt-x", "-",
    )


def test_effort_copy_passes_the_requested_effort_as_a_config_override() -> None:
    ranker = CodexCLIAdapter(executable=_EXE, timeout_seconds=9.0).effort_copy()

    assert ranker.honours_reasoning_effort is True
    assert ranker.argv(_request(effort="low"), _DIR) == (
        *_ASSESS_ARGV[:-1], "-c", "model_reasoning_effort=low", "-",
    )
    # no effort asked, or one codex does not take: nothing is added
    assert ranker.argv(_request(effort=None), _DIR) == _ASSESS_ARGV
    assert ranker.argv(_request(effort="max"), _DIR) == _ASSESS_ARGV


def test_invoke_runs_exactly_the_argv(tmp_path: Path) -> None:
    record = tmp_path / "argv.json"
    executable = tmp_path / "codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ok'}}))\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    CodexCLIAdapter(executable=str(executable)).invoke(_request(effort="low"))
    assess = json.loads(record.read_text(encoding="utf-8"))
    CodexCLIAdapter(executable=str(executable)).effort_copy().invoke(_request(effort="low"))
    rank = json.loads(record.read_text(encoding="utf-8"))

    assert "-c" not in assess and "model_reasoning_effort=low" not in assess
    assert rank[rank.index("-c") + 1] == "model_reasoning_effort=low"
    assert [item for item in rank if item not in {"-c", "model_reasoning_effort=low"}][:-2] == assess[:-2]


def test_the_ranker_uses_its_own_effort_copy_and_leaves_the_assess_port_alone(monkeypatch) -> None:
    assess_port = CodexCLIAdapter(executable=_EXE)
    binding = SimpleNamespace(port=assess_port, current=SimpleNamespace(target=SimpleNamespace(model="default")))
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", lambda *_a, **_k: binding)
    monkeypatch.setattr(
        "gigai.scout.proposal_execution._resolve_configured_target_name_for_adapter", lambda *_a, **_k: "codex-default"
    )
    monkeypatch.setattr("gigai.scout.find_jobs.bindings._patch_test_model_transport", lambda *_a, **_k: None)

    resolved = model_rank._resolve(cast(Any, object()), "codex_cli", home_root=Path("/nonexistent"))

    assert resolved.port is not assess_port and getattr(resolved.port, "honours_reasoning_effort") is True
    assert binding.port is assess_port and assess_port.honours_reasoning_effort is False
    assert model_rank._effort_applied(resolved, "codex_cli") == "low"
