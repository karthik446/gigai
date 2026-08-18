"""Bounded Codex CLI model-port adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .capabilities import require_capabilities
from .port import InvocationRequest, InvocationResult, ModelInvocationError, NormalizedUsage
from .process import run_json_process


class CodexCLIAdapter:
    """Invoke Codex through its explicit, read-only, ephemeral exec surface."""

    executable_name = "codex"
    adapter_name = "codex_cli"

    def __init__(self, *, executable: str | None = None, timeout_seconds: float = 120.0) -> None:
        self._executable = executable or shutil.which(self.executable_name)
        self._timeout_seconds = timeout_seconds
        if self._executable is None:
            raise ModelInvocationError("codex executable is not available on PATH")

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        with tempfile.TemporaryDirectory(prefix="gigai-codex-") as directory:
            argv = [
                self._executable,
                "exec",
                "--json",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--cd",
                directory,
            ]
            if request.model != "default":
                argv.extend(("--model", request.model))
            argv.append("-")
            output = run_json_process(
                tuple(argv),
                prompt=request.prompt,
                cwd=Path(directory),
                timeout_seconds=self._timeout_seconds,
            )
        text, model, usage = _parse_codex_jsonl(output.stdout, request.model)
        return InvocationResult(
            status="success",
            output_text=text,
            resolved_model=model,
            raw_usage=usage,
            normalized_usage=_normalize_usage(usage),
            cost_status="unavailable",
        )


def _parse_codex_jsonl(stdout: str, requested_model: str) -> tuple[str, str, Mapping[str, object]]:
    messages: list[str] = []
    resolved_model = requested_model
    usage: Mapping[str, object] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelInvocationError("Codex returned malformed JSONL") from exc
        if type(event) is not dict:
            raise ModelInvocationError("Codex returned a non-object JSON event")
        if isinstance(event.get("model"), str) and event["model"]:
            resolved_model = event["model"]
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        item = event.get("item")
        if type(item) is dict and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
        elif event.get("type") == "result" and isinstance(event.get("result"), str):
            messages.append(event["result"])
    text = "\n".join(part for part in messages if part)
    if not text:
        raise ModelInvocationError("Codex JSONL did not contain a final assistant message")
    return text, resolved_model, usage


def _normalize_usage(usage: Mapping[str, object]) -> NormalizedUsage:
    def integer(name: str) -> int | None:
        value = usage.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = integer("input_tokens")
    output_tokens = integer("output_tokens")
    total_tokens = integer("total_tokens")
    return NormalizedUsage(input_tokens, output_tokens, total_tokens)


__all__ = ["CodexCLIAdapter"]
