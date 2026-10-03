"""Capability probes for the CLI adapters: ask the installed CLI, never compare versions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import tempfile

from .port import ModelInvocationError
from .process import allowed_environment

_PROBE_TIMEOUT_SECONDS = 20.0
_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}

# 0110-8-07: the two features every supported codex must list (0110-004), then
# every other feature that gives the model a tool or runs something for it.
# ``--disable`` with a name the installed codex does not know is an error
# (EXECUTED on codex-cli 0.159.3: "Unknown feature flag"), so only the names
# ``codex features list`` prints are passed. A name the user's config.toml turns
# on (``[features] code_mode = true``) is turned off again by the command line.
_CODEX_REQUIRED_FEATURES = ("shell_tool", "memories")
CODEX_TOOL_FEATURES = (
    *_CODEX_REQUIRED_FEATURES,
    # running commands or code
    "unified_exec", "code_mode", "code_mode_only", "code_mode_host", "js_repl", "js_repl_tools_only",
    "sleep_tool", "request_permissions_tool",
    # web search (the mode itself is ``web_search="disabled"``; the deprecated ``web_search_request`` /
    # ``web_search_cached`` switches are left alone: naming them only prints a warning, and the mode wins)
    "standalone_web_search", "search_tool", "tool_search",
    # apps (connectors), plugins and what they bring (their MCP servers, hooks)
    "apps", "enable_mcp_apps", "plugins", "remote_plugin", "plugin_sharing", "recommended_plugins",
    "tool_suggest", "tool_call_mcp_elicitation", "skill_mcp_dependency_install", "hooks", "plugin_hooks",
    # browser and computer control
    "browser_use", "browser_use_external", "browser_use_full_cdp_access", "computer_use", "in_app_browser",
    # sub-agents and messaging
    "multi_agent", "multi_agent_v2", "agent_message_board", "send_message_to_user_async",
    # other model-callable tools
    "goals", "view_image", "image_generation", "artifact", "chronicle", "realtime_conversation",
    "worktrees", "workspace_dependencies", "in_app_local_automation", "external_agent_memory_import",
)
_MCP_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]+")
# A model's catalog entry can force tools on whatever the feature flags say
# (EXECUTED, codex-cli 0.159.3: ``tool_mode = "code_mode_only"`` keeps the
# ``exec`` tool and ``multi_agent_version = "v2"`` keeps ``spawn_agent`` and the
# other sub-agent tools with every feature above disabled). The call therefore
# runs on a copy of the catalog with these fields removed or emptied.
_CATALOG_DROPPED_FIELDS = ("tool_mode", "multi_agent_version")
_CATALOG_EMPTIED_FIELDS = {"experimental_supported_tools": [], "apply_patch_tool_type": None, "supports_search_tool": False}


@dataclass(frozen=True)
class CodexLockdown:
    """What one ``codex exec`` call must switch off: listed tool features and enabled MCP servers."""

    features: tuple[str, ...] = _CODEX_REQUIRED_FEATURES
    mcp_servers: tuple[str, ...] = ()
    #: The model catalog to run on (JSON text, tool fields removed); ``None`` only in tests of the argv.
    model_catalog: str | None = None


def _run_probe(argv: tuple[str, ...]) -> str:
    """Run one no-model probe command; fail closed on any failure."""

    with tempfile.TemporaryDirectory(prefix="gigai-probe-") as directory:
        try:
            done = subprocess.run(
                [*argv],
                cwd=Path(directory),
                env=allowed_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelInvocationError(f"could not check the capabilities of `{' '.join(argv)}`: {exc}") from exc
    if done.returncode != 0:
        raise ModelInvocationError(
            f"could not check the capabilities of `{' '.join(argv)}`: exit code {done.returncode}"
        )
    return done.stdout


def require_codex_capabilities(executable: str) -> tuple[str, ...]:
    """``codex features list`` must list ``shell_tool`` and ``memories`` (so ``--disable`` can turn them off).

    Returns every tool feature this codex lists (``CODEX_TOOL_FEATURES`` order): the ones to ``--disable``.
    """

    key = ("codex", executable)
    if key in _CACHE:
        return _CACHE[key]
    output = _run_probe((executable, "features", "list"))
    listed = {line.split()[0] for line in output.splitlines() if line.strip()}
    missing = [name for name in _CODEX_REQUIRED_FEATURES if name not in listed]
    if missing:
        raise ModelInvocationError(
            f"this codex lacks the {' and '.join(missing)} feature Scout needs to lock the model down; "
            "upgrade codex"
        )
    _CACHE[key] = tuple(name for name in CODEX_TOOL_FEATURES if name in listed)
    return _CACHE[key]


def codex_feature_flags(features: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part for name in features for part in ("--disable", name))


def codex_mcp_flags(servers: tuple[str, ...]) -> tuple[str, ...]:
    """One ``-c mcp_servers.<name>.enabled=false`` per server: codex has no single key for all of them."""

    return tuple(part for name in servers for part in ("-c", f"mcp_servers.{name}.enabled=false"))


def _codex_enabled_mcp_servers(executable: str, flags: tuple[str, ...]) -> tuple[str, ...]:
    argv = (executable, "mcp", "list", "--json", *flags)
    try:
        listed = json.loads(_run_probe(argv))
        if type(listed) is not list:
            raise ValueError("not a list")
        names = tuple(str(entry["name"]) for entry in listed if entry.get("enabled") is not False)
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        raise ModelInvocationError(
            "could not read `codex mcp list --json` to turn the configured MCP servers off; upgrade codex"
        ) from exc
    return names


def codex_plain_model_catalog(executable: str) -> str:
    """``codex debug models`` (the catalog this call would use) without the fields that force tools on."""

    try:
        catalog = json.loads(_run_probe((executable, "debug", "models")))
        models = catalog["models"]
        if type(models) is not list or not all(type(model) is dict for model in models):
            raise ValueError("models is not a list of objects")
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelInvocationError(
            "could not read `codex debug models` to turn the model's built-in tools off; upgrade codex"
        ) from exc
    for model in models:
        for name in _CATALOG_DROPPED_FIELDS:
            model.pop(name, None)
        model.update(_CATALOG_EMPTIED_FIELDS)
    return json.dumps(catalog)


def codex_lockdown(executable: str) -> CodexLockdown:
    """What this call must switch off. The MCP list and the model catalog are read on EVERY call (never cached).

    The user's ``config.toml`` stays loaded (a custom model provider and the
    default model live there), so its MCP servers are switched off by name on the
    command line, which outranks the file. ``codex mcp list --json`` runs with the
    feature flags first (plugins off drops plugin-provided servers; overriding a
    server that is no longer there is a config error), then once more with the
    overrides to confirm nothing is left enabled. Fails closed.
    """

    feature_flags = codex_feature_flags(require_codex_capabilities(executable))
    servers = _codex_enabled_mcp_servers(executable, feature_flags)
    unsafe = [name for name in servers if not _MCP_SERVER_NAME.fullmatch(name)]
    if unsafe:
        raise ModelInvocationError(
            f"cannot turn off the codex MCP server(s) {', '.join(sorted(unsafe))}: unsupported name; "
            "remove or rename them in the codex config"
        )
    if servers:
        left = _codex_enabled_mcp_servers(executable, (*feature_flags, *codex_mcp_flags(servers)))
        if left:
            raise ModelInvocationError(
                f"could not turn off the codex MCP server(s) {', '.join(sorted(left))}; Scout will not call "
                "the model with tools on"
            )
    return CodexLockdown(
        features=require_codex_capabilities(executable),
        mcp_servers=servers,
        model_catalog=codex_plain_model_catalog(executable),
    )


def require_claude_capabilities(executable: str) -> None:
    """``claude --help`` must list the flags that turn settings, MCP and tools off."""

    key = ("claude", executable)
    if key in _CACHE:
        return
    output = _run_probe((executable, "--help"))
    missing = [flag for flag in ("--setting-sources", "--strict-mcp-config", "--tools") if flag not in output]
    if missing:
        raise ModelInvocationError(
            f"this claude lacks the {', '.join(missing)} flag(s) Scout needs to lock the model down; "
            "upgrade Claude Code"
        )
    _CACHE[key] = ()


def reset_probe_cache() -> None:
    _CACHE.clear()


__all__ = [
    "CODEX_TOOL_FEATURES",
    "CodexLockdown",
    "codex_feature_flags",
    "codex_lockdown",
    "codex_mcp_flags",
    "codex_plain_model_catalog",
    "require_claude_capabilities",
    "require_codex_capabilities",
    "reset_probe_cache",
]
