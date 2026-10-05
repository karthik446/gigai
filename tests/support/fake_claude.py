"""uat-bug-035: a fake ``claude`` executable for the ``claude_cli`` model target.

``write_fake_claude(directory)`` writes an executable named ``claude`` into
``directory``; put that directory first on ``PATH`` and the real
``ClaudeCLIAdapter`` (found through ``shutil.which("claude")``, exactly as a
real install is found) runs it instead of Claude Code. No live model call.

The script answers the way ``claude -p --output-format json`` does: one JSON
object on stdout whose ``result`` is the model's text. The text is what the
fake Ollama model (``bindings._test_model_handler``) answers for the same
prompt, so an extraction, a rank batch, an assessment and a tailoring all get
the replies every other hermetic journey gets. ``--version`` answers like
the real CLI, so ``gigai setup``'s detection sees an installed ``claude``.

Each call appends ``{"argv": [...], "kind": ...}`` to ``record`` (one JSON
line), so a test can pin the argv each caller used (the assess path's plan
mode, the ranker's lean mode).
"""

from __future__ import annotations

import json
from pathlib import Path
import stat
import sys

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What ``modelUsage`` names as the model that ran (lean mode reads it).
FAKE_CLAUDE_MODEL = "claude-fake-5-5"


#: What ``claude --help`` lists: the flags the adapter's capability probe requires.
FAKE_CLAUDE_HELP = "Usage: claude [options]\n  --setting-sources <sources>\n  --strict-mcp-config\n  --tools <tools...>"


def prompt_kind(prompt: str) -> str:
    from gigai.scout.find_jobs import bindings

    if bindings.TEST_MODEL_EXTRACT_MARKER in prompt:
        return "extract"
    if bindings.TEST_MODEL_RANK_MARKER in prompt:
        return "rank"
    if bindings.TEST_MODEL_TAILOR_MARKER in prompt or bindings.TEST_MODEL_JUDGE_MARKER in prompt:
        return "tailor"
    return "assess"


def answer(prompt: str) -> str:
    """The fake Ollama model's answer text for ``prompt``."""

    from gigai.scout.find_jobs import bindings

    request = httpx.Request(
        "POST",
        "http://127.0.0.1:11434/api/chat",
        json={"model": bindings.TEST_MODEL_NAME, "messages": [{"role": "user", "content": prompt}]},
    )
    response = bindings._test_model_handler(request)
    if response.status_code != 200:
        raise RuntimeError(f"fake model answered HTTP {response.status_code}")
    return str(response.json()["message"]["content"])


def _asked_model(argv: list[str]) -> str | None:
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def main(record: str, refuse: tuple[str, ...] = (), answer_text: str | None = None, echo_model: bool = False) -> int:
    """``refuse``: models the CLI refuses (exit 1, like a plan without the model or a spent limit).

    ``answer_text`` replaces the fake model's answer; ``echo_model`` names the asked model in ``modelUsage`` (the CLI's
    default, ``FAKE_CLAUDE_MODEL``, when no ``--model`` was given).
    """

    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("2.1.0 (Claude Code)")
        return 0
    if argv == ["--help"]:
        # the adapter's capability probe: answered, never recorded as a model call
        print(FAKE_CLAUDE_HELP)
        return 0
    prompt = sys.stdin.read()
    with open(record, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "kind": prompt_kind(prompt)}) + "\n")
    asked = _asked_model(argv)
    if asked is not None and asked in refuse:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": f"There's an issue with the selected model ({asked})."}))
        print(f"model {asked} is not available", file=sys.stderr)
        return 1
    ran = asked if echo_model and asked is not None else FAKE_CLAUDE_MODEL
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": answer_text if answer_text is not None else answer(prompt),
                "usage": {"input_tokens": 120, "output_tokens": 30},
                "modelUsage": {ran: {"inputTokens": 120, "outputTokens": 30}},
                "total_cost_usd": 0.0,
            }
        )
    )
    return 0


def write_fake_claude(
    directory: Path, *, record: Path | None = None, refuse: tuple[str, ...] = (), answer_text: str | None = None, echo_model: bool = False
) -> Path:
    """Write ``directory/claude``; returns the record file its calls append to."""

    directory.mkdir(parents=True, exist_ok=True)
    record = record if record is not None else directory / "claude-calls.jsonl"
    executable = directory / "claude"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tests.support.fake_claude import main\n"
        f"sys.exit(main({str(record)!r}, {tuple(refuse)!r}, {answer_text!r}, {echo_model!r}))\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return record


def calls(record: Path) -> list[dict]:
    """Every call the fake answered, in order (``[]`` before the first)."""

    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]


__all__ = ["FAKE_CLAUDE_MODEL", "answer", "calls", "prompt_kind", "write_fake_claude"]
