"""Bounded Claude Code model-port adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from .capabilities import require_capabilities
from .port import InvocationRequest, InvocationResult, ModelInvocationError, NormalizedUsage
from .process import run_json_process


# Lean mode (SCOPE-ADD-3, used by the model ranker): Claude Code's default
# system prompt plus the user's settings, MCP servers and skills add ~31.8k
# input tokens to every call, and ``--permission-mode plan`` silently ignores
# ``--model`` (ranking spike, EXECUTED: ``--model haiku`` under plan mode ran
# Sonnet). Lean mode replaces the system prompt with this line, loads no
# settings/MCP/slash commands, drops plan mode (no tools are enabled either
# way: ``--tools ""`` stays), passes ``--effort`` and honours ``--model``.
LEAN_SYSTEM_PROMPT = (
    "You are a strict JSON function. Follow the user's instructions exactly and answer with JSON only."
)
_LEAN_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_LEAN_DEFAULT_EFFORT = "low"


class ClaudeCLIAdapter:
    """Invoke Claude Code in print/JSON/plan mode without session persistence.

    ``lean=True`` is the ranker's mode (see ``LEAN_SYSTEM_PROMPT``). The
    default (assess) mode's argv is unchanged.
    """

    executable_name = "claude"
    adapter_name = "claude_cli"

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: float = 120.0,
        lean: bool = False,
    ) -> None:
        self._executable = executable or shutil.which(self.executable_name)
        self._timeout_seconds = timeout_seconds
        self._lean = lean
        if self._executable is None:
            raise ModelInvocationError("claude executable is not available on PATH")

    @property
    def lean(self) -> bool:
        return self._lean

    def lean_copy(self) -> "ClaudeCLIAdapter":
        """The same executable and timeout, in lean mode."""

        return ClaudeCLIAdapter(executable=self._executable, timeout_seconds=self._timeout_seconds, lean=True)

    def argv(self, request: InvocationRequest) -> tuple[str, ...]:
        """The exact child argv for ``request`` (no shell; the prompt goes on stdin)."""

        assert self._executable is not None
        argv = [self._executable, "-p", "--output-format", "json", "--no-session-persistence"]
        if self._lean:
            effort = request.reasoning_effort if request.reasoning_effort in _LEAN_EFFORTS else _LEAN_DEFAULT_EFFORT
            argv.extend((
                "--tools",
                "",
                "--system-prompt",
                LEAN_SYSTEM_PROMPT,
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--disable-slash-commands",
                "--effort",
                effort,
            ))
        else:
            argv.extend(("--permission-mode", "plan", "--tools", ""))
        if request.model != "default":
            argv.extend(("--model", request.model))
        return tuple(argv)

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        with TemporaryDirectory(prefix="gigai-claude-") as directory:
            output = run_json_process(
                self.argv(request),
                prompt=request.prompt,
                cwd=Path(directory),
                timeout_seconds=self._timeout_seconds,
                extra_environment_names=(
                    # Claude's macOS login lookup requires USER even with HOME
                    # preserved. Keep this adapter-specific, not full inheritance.
                    "USER",
                    *(("CLAUDE_CODE_OAUTH_TOKEN",) if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else ()),
                ),
            )
        text, model, usage = _parse_claude_json(output.stdout, request.model, model_usage_fallback=self._lean)
        return InvocationResult(
            status="success",
            output_text=text,
            resolved_model=model,
            raw_usage=usage,
            normalized_usage=_normalize_usage(usage),
            cost_status="provider_reported" if usage else "unavailable",
        )


def _parse_claude_json(
    stdout: str, requested_model: str, *, model_usage_fallback: bool = False
) -> tuple[str, str, Mapping[str, object]]:
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ModelInvocationError("Claude returned malformed JSON") from exc
    if type(payload) is not dict:
        raise ModelInvocationError("Claude returned a non-object JSON result")
    if payload.get("is_error") is True or payload.get("subtype") not in {None, "success"}:
        raise ModelInvocationError("Claude returned a non-success result")
    text = payload.get("result")
    if not isinstance(text, str) or not text:
        raise ModelInvocationError("Claude JSON did not contain final assistant text")
    model = payload.get("model") if isinstance(payload.get("model"), str) else requested_model
    model_usage = payload.get("modelUsage")
    if model_usage_fallback and not isinstance(payload.get("model"), str) and isinstance(model_usage, dict) and model_usage:
        # Lean mode only: ``claude -p --output-format json`` names the model
        # that actually ran as the ``modelUsage`` key, not a ``model`` field.
        model = ",".join(str(name) for name in model_usage)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return text, model, usage


def _normalize_usage(usage: Mapping[str, object]) -> NormalizedUsage:
    def integer(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if type(value) is int and value >= 0:
                return value
        return None

    input_tokens = integer("input_tokens", "input_tokens_count")
    output_tokens = integer("output_tokens", "output_tokens_count")
    total_tokens = integer("total_tokens")
    return NormalizedUsage(input_tokens, output_tokens, total_tokens)


__all__ = ["ClaudeCLIAdapter", "LEAN_SYSTEM_PROMPT"]
