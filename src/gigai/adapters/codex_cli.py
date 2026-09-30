"""Bounded Codex CLI model-port adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from .capabilities import require_capabilities
from .port import InvocationRequest, InvocationResult, ModelInvocationError, NormalizedUsage
from .process import run_json_process


# SCOPE-ADD-3 C1 (operator decision A): the reasoning efforts codex's
# ``model_reasoning_effort`` config key takes. Anything else is not passed.
_CODEX_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})


class CodexCLIAdapter:
    """Invoke Codex through its explicit, read-only, ephemeral exec surface.

    ``honours_reasoning_effort`` (SCOPE-ADD-3 C1, opt-in): only a copy made by
    :meth:`effort_copy` passes ``request.reasoning_effort`` on as ``-c
    model_reasoning_effort=<effort>``. The ranker makes that copy; every other
    caller (assess, quick assess) keeps the default instance, whose argv is
    unchanged byte for byte whatever the request's effort says.
    """

    executable_name = "codex"
    adapter_name = "codex_cli"

    def __init__(
        self, *, executable: str | None = None, timeout_seconds: float = 120.0, honours_reasoning_effort: bool = False
    ) -> None:
        self._executable = executable or shutil.which(self.executable_name)
        self._timeout_seconds = timeout_seconds
        self.honours_reasoning_effort = honours_reasoning_effort
        if self._executable is None:
            raise ModelInvocationError("codex executable is not available on PATH")

    def effort_copy(self) -> "CodexCLIAdapter":
        """The same executable and timeout, passing the request's reasoning effort on."""

        return CodexCLIAdapter(
            executable=self._executable, timeout_seconds=self._timeout_seconds, honours_reasoning_effort=True
        )

    def argv(self, request: InvocationRequest, directory: str) -> tuple[str, ...]:
        """The exact child argv for ``request`` run in ``directory`` (the prompt goes on stdin)."""

        assert self._executable is not None
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
        if self.honours_reasoning_effort and request.reasoning_effort in _CODEX_EFFORTS:
            argv.extend(("-c", f"model_reasoning_effort={request.reasoning_effort}"))
        argv.append("-")
        return tuple(argv)

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        with tempfile.TemporaryDirectory(prefix="gigai-codex-") as directory:
            output = run_json_process(
                self.argv(request, directory),
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
