"""Bounded Claude Code model-port adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from .capabilities import require_capabilities
from .port import InvocationRequest, InvocationResult, ModelInvocationError, NormalizedUsage
from .process import run_json_process


class ClaudeCLIAdapter:
    """Invoke Claude Code in print/JSON/plan mode without session persistence."""

    executable_name = "claude"
    adapter_name = "claude_cli"

    def __init__(self, *, executable: str | None = None, timeout_seconds: float = 120.0) -> None:
        self._executable = executable or shutil.which(self.executable_name)
        self._timeout_seconds = timeout_seconds
        if self._executable is None:
            raise ModelInvocationError("claude executable is not available on PATH")

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        with TemporaryDirectory(prefix="gigai-claude-") as directory:
            argv = [
                self._executable,
                "-p",
                "--output-format",
                "json",
                "--no-session-persistence",
                "--permission-mode",
                "plan",
                "--tools",
                "",
            ]
            if request.model != "default":
                argv.extend(("--model", request.model))
            output = run_json_process(
                tuple(argv),
                prompt=request.prompt,
                cwd=Path(directory),
                timeout_seconds=self._timeout_seconds,
            )
        text, model, usage = _parse_claude_json(output.stdout, request.model)
        return InvocationResult(
            status="success",
            output_text=text,
            resolved_model=model,
            raw_usage=usage,
            normalized_usage=_normalize_usage(usage),
            cost_status="provider_reported" if usage else "unavailable",
        )


def _parse_claude_json(stdout: str, requested_model: str) -> tuple[str, str, Mapping[str, object]]:
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


__all__ = ["ClaudeCLIAdapter"]
