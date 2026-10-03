"""0110-8-07: fake ``codex`` and ``claude`` executables that report the TOOL SURFACE a call would have.

``write_fakes(directory)`` writes both executables; put the directory first on
``PATH`` and the real adapters run them. No model call. Each fake does what the
real CLI documents: it loads the user's config from the ``HOME`` /
``CODEX_HOME`` it is given, applies the command line on top (the command line
outranks the file), and appends one JSON line per model call to the record:
its argv, the environment names it got, the config text it loaded and the
resulting ``surface`` (web search mode, enabled MCP servers, the tools the model
would be offered).

The codex fake mirrors codex-cli 0.159.3 (facts EXECUTED for the ticket against
a local stand-in model endpoint):

* ``codex features list`` prints ``codex_features_0_159_3.txt`` (the real
  list with an empty config);
* ``--disable <unknown>`` is an error; ``--disable X`` is ``-c features.X=false``;
* ``web_search`` defaults to ``"cached"`` (ON); only ``"disabled"`` removes the tool;
* a ``-c mcp_servers={}`` override does not clear the servers (tables merge);
  ``-c mcp_servers.<name>.enabled=false`` turns one off; the same override for
  a server that does not exist is a config error;
* a plugin's MCP server is there only while the ``plugins`` feature is on;
* a model's catalog entry forces tools on whatever the features say:
  ``tool_mode = "code_mode_only"`` keeps ``exec``, ``multi_agent_version``
  keeps the sub-agent tools, ``apply_patch_tool_type`` keeps ``apply_patch``;
  ``codex debug models`` prints the catalog and ``-c model_catalog_json=<file>``
  replaces it;
* ``request_user_input`` is always offered (nobody can answer it in ``codex exec``).

Replies are the fake Ollama model's (``fake_claude.answer``), so the real call
paths get an answer they can read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_TEXT = (Path(__file__).parent / "codex_features_0_159_3.txt").read_text(encoding="utf-8")

#: feature -> the tool(s) the model is offered while it is on.
CODEX_FEATURE_TOOLS = {
    "shell_tool": "shell",
    "memories": "memories",
    "multi_agent": "multi_agent_v1",
    "multi_agent_v2": "collaboration",
    "goals": "goals",
    "view_image": "view_image",
    "image_generation": "image_generation",
    "code_mode": "exec",
    "js_repl": "js_repl",
    "request_permissions_tool": "request_permissions",
    "standalone_web_search": "web_search",
    "apps": "apps",
    "browser_use": "browser",
    "computer_use": "computer",
    "hooks": "hooks",
}
#: ``codex debug models``: the default model is the first one, a "code mode only" model with sub-agents.
CODEX_CATALOG = {
    "models": [
        {
            "slug": "fake-code-model", "display_name": "Fake Code Model", "tool_mode": "code_mode_only",
            "multi_agent_version": "v2", "apply_patch_tool_type": "freeform", "supports_search_tool": True,
            "experimental_supported_tools": ["clock"], "shell_type": "unified_exec", "base_instructions": "You are Codex.",
        },
        {"slug": "fake-plain-model", "display_name": "Fake Plain Model", "apply_patch_tool_type": "freeform",
         "shell_type": "unified_exec", "base_instructions": "You are Codex."},
    ]
}
CLAUDE_HELP = "Usage: claude [options]\n  --setting-sources <sources>\n  --strict-mcp-config\n  --tools <tools...>"


def _feature_defaults() -> dict[str, bool]:
    return {line.split()[0]: line.split()[-1] == "true" for line in FEATURES_TEXT.splitlines() if line.strip()}


def _merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def _override(key_value: str) -> dict:
    key, _, raw = key_value.partition("=")
    try:
        value: object = tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw
    nested: object = value
    for part in reversed(key.split(".")):
        nested = {part: nested}
    assert isinstance(nested, dict)
    return nested


class _Refused(Exception):
    pass


def codex_state(argv: list[str], environ: dict[str, str]) -> dict:
    """The config codex would run ``argv`` with: user config, then the command line on top."""

    home = Path(environ.get("CODEX_HOME") or Path(environ["HOME"]) / ".codex")
    path = home / "config.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    loaded = "--ignore-user-config" not in argv
    config: dict = tomllib.loads(text) if loaded else {}
    defaults = _feature_defaults()
    cli: dict = {}
    for index, value in enumerate(argv):
        following = argv[index + 1] if index + 1 < len(argv) else ""
        if value in ("--disable", "--enable"):
            if following not in defaults:
                raise _Refused(f"Error: Unknown feature flag: {following}")
            cli = _merge(cli, {"features": {following: value == "--enable"}})
        elif value in ("-c", "--config"):
            cli = _merge(cli, _override(following))
    merged = _merge(config, cli)
    features = {**defaults, **{k: bool(v) for k, v in merged.get("features", {}).items() if k in defaults}}
    servers = dict(merged.get("mcp_servers", {}))
    if features["plugins"]:
        for plugin, entry in merged.get("plugins", {}).items():
            if isinstance(entry, dict) and entry.get("enabled", True):
                name = plugin.split("@")[0] + "_plugin"
                servers[name] = _merge({"command": "plugin-server"}, servers.get(name, {}))
    for name, entry in servers.items():
        if not isinstance(entry, dict) or not (entry.get("command") or entry.get("url")):
            raise _Refused("Error: failed to load bootstrap configuration")
    stubborn = set(config.get("fake", {}).get("stubborn_mcp_servers", []))
    enabled = sorted(name for name, entry in servers.items() if entry.get("enabled", True) or name in stubborn)
    web_search = merged.get("web_search", "cached")
    tools = {"request_user_input"}
    if web_search != "disabled":
        tools.add("web_search")
    tools.update(tool for feature, tool in CODEX_FEATURE_TOOLS.items() if features.get(feature))
    if enabled:
        tools.update({"list_mcp_resources", "read_mcp_resource", *(f"mcp__{name}" for name in enabled)})
    catalog, catalog_path = CODEX_CATALOG, merged.get("model_catalog_json")
    if catalog_path is not None:
        try:
            catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _Refused(f"Error: failed to parse model_catalog_json path `{catalog_path}`") from None
    model = argv[argv.index("--model") + 1] if "--model" in argv else merged.get("model", CODEX_CATALOG["models"][0]["slug"])
    entry = next((item for item in catalog["models"] if item.get("slug") == model), {})
    if entry.get("tool_mode") == "code_mode_only":
        tools.add("exec")
    if entry.get("multi_agent_version"):
        tools.add("collaboration")
    if entry.get("apply_patch_tool_type"):
        tools.add("apply_patch")
    tools.update(entry.get("experimental_supported_tools") or [])
    sandbox = argv[argv.index("--sandbox") + 1] if "--sandbox" in argv else merged.get("sandbox_mode", "read-only")
    return {
        "config_path": str(path),
        "config_text": text,
        "surface": {
            "config_loaded": loaded,
            "model_provider": merged.get("model_provider", "openai"),
            "model": model,
            "model_catalog_json": catalog_path,
            "web_search": web_search,
            "mcp_servers": {name: bool(name in enabled) for name in sorted(servers)},
            "mcp_enabled": enabled,
            "features_on": sorted(name for name in CODEX_FEATURE_TOOLS if features.get(name)),
            "sandbox": sandbox,
            "tools": sorted(tools),
        },
    }


def _answer(prompt: str) -> tuple[str, str]:
    sys.path.insert(0, str(REPO_ROOT))
    from tests.support.fake_claude import answer, prompt_kind

    try:
        return prompt_kind(prompt), answer(prompt)
    except Exception:  # noqa: BLE001 - a fake: any prompt the fixture model cannot answer gets "{}"
        return prompt_kind(prompt), "{}"


def _record(record: str, entry: dict) -> None:
    with open(record, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def codex_main(record: str) -> int:
    argv = sys.argv[1:]
    if argv == ["features", "list"]:
        sys.stdout.write(FEATURES_TEXT)
        return 0
    if argv == ["debug", "models"]:
        print(json.dumps(CODEX_CATALOG))
        return 0
    try:
        state = codex_state(argv, dict(os.environ))
    except _Refused as refused:
        print(str(refused), file=sys.stderr)
        _record(record, {"cli": "codex", "kind": "refused", "argv": argv, "error": str(refused)})
        return 1
    if argv[:3] == ["mcp", "list", "--json"]:
        _record(record, {"cli": "codex", "kind": "mcp_list", "argv": argv})
        print(json.dumps([{"name": name, "enabled": on} for name, on in state["surface"]["mcp_servers"].items()]))
        return 0
    if argv[:1] != ["exec"]:
        print(f"error: unexpected argument '{argv[0] if argv else ''}' found", file=sys.stderr)
        return 2
    kind, text = _answer(sys.stdin.read())
    _record(record, {"cli": "codex", "kind": kind, "argv": argv, "env": sorted(os.environ), "cwd": os.getcwd(), **state})
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 30}}))
    return 0


def claude_state(argv: list[str], environ: dict[str, str]) -> dict:
    """What ``claude -p`` would load for ``argv``: settings sources, MCP servers, built-in tools."""

    home = Path(environ["HOME"])
    settings_path, state_path = home / ".claude" / "settings.json", home / ".claude.json"
    settings_text = settings_path.read_text(encoding="utf-8") if settings_path.is_file() else ""
    state_text = state_path.read_text(encoding="utf-8") if state_path.is_file() else ""

    def value(flag: str, default: str | None) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else default

    sources = [item for item in (value("--setting-sources", "user,project,local") or "").split(",") if item]
    settings = json.loads(settings_text) if settings_text and "user" in sources else {}
    strict = "--strict-mcp-config" in argv
    mcp = [] if strict else sorted({*json.loads(state_text or "{}").get("mcpServers", {}), *settings.get("mcpServers", {})})
    if "--mcp-config" in argv:
        mcp.append("<--mcp-config>")
    tools_flag = value("--tools", "default")
    tools = [] if tools_flag == "" else [tools_flag]
    tools.extend(f"mcp__{name}" for name in mcp)
    return {
        "config_path": str(settings_path),
        "config_text": settings_text + state_text,
        "surface": {
            "setting_sources": sources,
            "hooks": sorted(settings.get("hooks", {})),
            "plugins": sorted(settings.get("enabledPlugins", {})),
            "mcp_enabled": mcp,
            "tools": tools,
        },
    }


def claude_main(record: str) -> int:
    argv = sys.argv[1:]
    if argv == ["--help"]:
        print(CLAUDE_HELP)
        return 0
    kind, text = _answer(sys.stdin.read())
    _record(record, {"cli": "claude", "kind": kind, "argv": argv, "env": sorted(os.environ), "cwd": os.getcwd(), **claude_state(argv, dict(os.environ))})
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": text,
        "usage": {"input_tokens": 120, "output_tokens": 30}, "modelUsage": {"claude-fake-5-5": {}}, "total_cost_usd": 0.0,
    }))
    return 0


def write_fakes(directory: Path, *, record: Path | None = None) -> Path:
    """Write ``directory/codex`` and ``directory/claude``; returns the record file their calls append to."""

    directory.mkdir(parents=True, exist_ok=True)
    record = record if record is not None else directory / "tool-surface-calls.jsonl"
    for name in ("codex", "claude"):
        executable = directory / name
        executable.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            f"from tests.support.fake_tool_surface_cli import {name}_main\n"
            f"sys.exit({name}_main({str(record)!r}))\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return record


def calls(record: Path, *, cli: str | None = None, model_only: bool = True) -> list[dict]:
    """The recorded calls in order; by default only model calls (no ``mcp list`` probes or refusals)."""

    if not record.exists():
        return []
    entries = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]
    return [
        entry for entry in entries
        if (cli is None or entry["cli"] == cli) and (not model_only or entry["kind"] not in ("mcp_list", "refused"))
    ]


__all__ = ["CODEX_CATALOG", "CODEX_FEATURE_TOOLS", "calls", "claude_state", "codex_state", "write_fakes"]
