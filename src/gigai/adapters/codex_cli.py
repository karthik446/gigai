"""Bounded Codex CLI model-port adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from .capabilities import require_capabilities
from .cli_probe import CodexLockdown, codex_feature_flags, codex_lockdown, codex_mcp_flags
from .port import InvocationRequest, InvocationResult, ModelInvocationError, NormalizedUsage
from .process import run_json_process


# SCOPE-ADD-3 C1 (operator decision A): the reasoning efforts codex's
# ``model_reasoning_effort`` config key takes. Anything else is not passed.
_CODEX_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})

# 0110-004 hardening: a read-only sandbox still lets the model read local files
# (prep spike, EXECUTED: it ran ``rg`` over ``~/.codex/memories``). Every Scout
# call therefore turns the shell tool and memories off. ``--ignore-user-config``
# is deliberately NOT used: it skips ``$CODEX_HOME/config.toml``, where a user's
# custom model provider and default model live, so it can break "default" model
# selection. It would not be enough either: web search, sub-agents, goals and
# the image tool are on by default with no config at all.
#
# 0110-8-07: the prompt carries untrusted posting text next to the user's resume,
# so the call runs with EVERY tool off, whatever ``config.toml`` says (command
# line overrides outrank it):
#   * ``--disable <feature>`` for every tool feature the installed codex lists
#     (``cli_probe.CODEX_TOOL_FEATURES``: shell, code mode, apps, plugins, hooks,
#     browser/computer use, sub-agents, goals, images, ...);
#   * ``-c web_search="disabled"`` (the default is "cached": web search is ON);
#   * ``-c mcp_servers.<name>.enabled=false`` for every configured MCP server;
#   * ``-c model_catalog_json="<call dir>/gigai-model-catalog.json"``: codex's own
#     model catalog without the fields that force tools on for a model whatever
#     the feature flags say (code mode's ``exec``, the sub-agent tools).
# Verified on codex-cli 0.159.3 against a local stand-in model endpoint, for
# every model in the catalog: the request's tool definitions go from web_search +
# MCP + apps + sub-agent + goal + image tools to ``request_user_input`` alone
# (nobody can answer it in ``codex exec``).
_WEB_SEARCH_OFF = ("-c", 'web_search="disabled"')
MODEL_CATALOG_FILE = "gigai-model-catalog.json"
# What ``codex exec --json`` reports when the model used a tool. Any of these in
# the event stream discards the answer (the lockdown above should make it impossible).
_TOOL_ITEM_TYPES = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"})
_UNKNOWN_FLAG_MARKERS = ("unexpected argument", "unrecognized", "unknown option", "unknown flag")


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

    def argv(self, request: InvocationRequest, directory: str, lockdown: CodexLockdown) -> tuple[str, ...]:
        """The exact child argv for ``request`` run in ``directory`` (the prompt goes on stdin).

        ``lockdown`` is what :func:`cli_probe.codex_lockdown` read from the
        installed codex for this call; every tool-off flag is rendered here.
        """

        assert self._executable is not None
        argv = [
            self._executable,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            *codex_feature_flags(lockdown.features),
            *_WEB_SEARCH_OFF,
            *codex_mcp_flags(lockdown.mcp_servers),
            *_model_catalog_flags(lockdown, directory),
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
        assert self._executable is not None
        lockdown = codex_lockdown(self._executable)
        with tempfile.TemporaryDirectory(prefix="gigai-codex-") as directory:
            if lockdown.model_catalog is not None:
                (Path(directory) / MODEL_CATALOG_FILE).write_text(lockdown.model_catalog, encoding="utf-8")
            try:
                output = run_json_process(
                    self.argv(request, directory, lockdown),
                    prompt=request.prompt,
                    cwd=Path(directory),
                    timeout_seconds=self._timeout_seconds,
                )
            except ModelInvocationError as exc:
                if any(marker in str(exc).lower() for marker in _UNKNOWN_FLAG_MARKERS):
                    raise ModelInvocationError(
                        f"this codex does not support the lockdown flags Scout requires; "
                        f"upgrade codex ({exc})"
                    ) from exc
                raise
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
        # the model Codex says answered: on the event, or in a session header's ``msg`` (when it reports one at all;
        # otherwise the request's ``default`` stays, and the assessment says the model is not reported)
        reported = event.get("model") if isinstance(event.get("model"), str) else None
        if reported is None and type(event.get("msg")) is dict and isinstance(event["msg"].get("model"), str):
            reported = event["msg"]["model"]
        if reported:
            resolved_model = reported
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        item = event.get("item")
        if type(item) is dict and _is_tool_item(item.get("type")):
            raise ModelInvocationError(
                f"Codex used a tool ({item.get('type')}) although Scout turns every tool off; the answer was discarded"
            )
        if type(item) is dict and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
        elif event.get("type") == "result" and isinstance(event.get("result"), str):
            messages.append(event["result"])
    text = "\n".join(part for part in messages if part)
    if not text:
        raise ModelInvocationError("Codex JSONL did not contain a final assistant message")
    return text, resolved_model, usage


def _model_catalog_flags(lockdown: CodexLockdown, directory: str) -> tuple[str, ...]:
    if lockdown.model_catalog is None:
        return ()
    # json.dumps: a quoted TOML basic string (the value of ``-c`` is parsed as TOML)
    return ("-c", f"model_catalog_json={json.dumps(str(Path(directory) / MODEL_CATALOG_FILE))}")


def _is_tool_item(kind: object) -> bool:
    return isinstance(kind, str) and (kind in _TOOL_ITEM_TYPES or kind.endswith("tool_call"))


def _normalize_usage(usage: Mapping[str, object]) -> NormalizedUsage:
    def integer(name: str) -> int | None:
        value = usage.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = integer("input_tokens")
    output_tokens = integer("output_tokens")
    total_tokens = integer("total_tokens")
    return NormalizedUsage(input_tokens, output_tokens, total_tokens)


__all__ = ["CodexCLIAdapter"]
