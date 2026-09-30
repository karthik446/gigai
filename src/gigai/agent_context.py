"""``gigai agent-context``: the CLI manual, merged from two sources.

Arguments and options come from the live Click command tree (never copied by
hand). The prose an agent needs (summary, examples, effect, external, output,
notes, hidden reason) comes from ``data/cli/commands.yaml``. That file is
packaged data written in the JSON-compatible subset of YAML, parsed with the
standard-library ``json`` module because GigAI has no YAML dependency.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import click

SCHEMA_VERSION = 1
EFFECTS = ("read", "write")
EXTERNALS = ("none", "model", "network")

MANUAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schemaVersion", "commandCount", "commands"],
    "additionalProperties": False,
    "properties": {
        "schemaVersion": {"const": SCHEMA_VERSION},
        "commandCount": {"type": "integer", "minimum": 1},
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "command", "path", "aliases", "summary", "usage", "options",
                    "arguments", "examples", "notes", "effect", "external",
                    "output", "hidden",
                ],
                "additionalProperties": False,
                "properties": {
                    "command": {"type": "string", "minLength": 1},
                    "path": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string", "minLength": 1},
                    "usage": {"type": "string", "minLength": 1},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type", "required", "default", "help"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "required": {"type": "boolean"},
                                "default": {"type": ["string", "number", "boolean", "null"]},
                                "help": {"type": "string"},
                            },
                        },
                    },
                    "arguments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type", "required"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "required": {"type": "boolean"},
                            },
                        },
                    },
                    "examples": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "notes": {"type": "string"},
                    "effect": {"enum": list(EFFECTS)},
                    "external": {"enum": list(EXTERNALS)},
                    "output": {"type": "string", "minLength": 1},
                    "hidden": {"type": "boolean"},
                },
            },
        },
    },
}


def load_prose() -> dict[str, Any]:
    """Read the packaged declarative manifest."""

    text = resources.files("gigai").joinpath("data/cli/commands.yaml").read_text(encoding="utf-8")
    return json.loads(text)


def iter_commands(root: click.Group):
    """Yield ``(path, command, hidden)`` for every registered leaf command."""

    def walk(group: click.Group, path: tuple[str, ...], hidden: bool):
        for name in sorted(group.commands):
            command = group.commands[name]
            child = path + (name,)
            child_hidden = hidden or command.hidden
            if isinstance(command, click.Group):
                yield from walk(command, child, child_hidden)
            else:
                yield child, command, child_hidden

    yield from walk(root, (), False)


def _json_default(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _type_name(param: click.Parameter) -> str:
    if isinstance(param.type, click.Choice):
        return "choice(" + "|".join(str(choice) for choice in param.type.choices) + ")"
    return param.type.name


def _option_entry(option: click.Option) -> dict[str, Any]:
    long_names = [name for name in option.opts if name.startswith("--")]
    name = "/".join(long_names + list(option.secondary_opts)) or option.opts[0]
    default = None if option.multiple else _json_default(option.default)
    return {
        "name": name,
        "type": "flag" if option.is_flag else _type_name(option) + (" (repeatable)" if option.multiple else ""),
        "required": bool(option.required),
        "default": default,
        "help": option.help or "",
    }


def build_manual(root: click.Group) -> dict[str, Any]:
    prose = load_prose()["commands"]
    registered = list(iter_commands(root))
    by_object: dict[int, list[str]] = {}
    for path, command, _ in registered:
        by_object.setdefault(id(command), []).append(" ".join(path))
    entries = []
    for path, command, hidden in registered:
        key = " ".join(path)
        info = prose[key]
        source = prose[info["aliasOf"]] if "aliasOf" in info else info
        context = click.Context(command, info_name=path[-1])
        options = [
            _option_entry(param)
            for param in command.params
            if isinstance(param, click.Option) and "--help" not in param.opts
        ]
        arguments = [
            {"name": param.name or "", "type": _type_name(param), "required": bool(param.required)}
            for param in command.params
            if isinstance(param, click.Argument)
        ]
        notes = source.get("notes", "")
        if hidden and info.get("hiddenReason"):
            notes = (notes + " " if notes else "") + "Hidden: " + info["hiddenReason"]
        entries.append(
            {
                "command": key,
                "path": list(path),
                "aliases": [name for name in by_object[id(command)] if name != key],
                "summary": info["summary"],
                "usage": " ".join(["gigai", *path, *command.collect_usage_pieces(context)]),
                "options": options,
                "arguments": arguments,
                "examples": list(info["examples"]),
                "notes": notes,
                "effect": info["effect"],
                "external": info["external"],
                "output": info["output"],
                "hidden": hidden,
            }
        )
    return {"schemaVersion": SCHEMA_VERSION, "commandCount": len(entries), "commands": entries}


def summary_line(manual: dict[str, Any]) -> str:
    return (
        f"{manual['commandCount']} commands (schema v{manual['schemaVersion']}). "
        "Run `gigai agent-context --json` for the full machine-readable command schema."
    )


def render_markdown(manual: dict[str, Any]) -> str:
    """The generated human page ``docs/cli.md``."""

    lines = [
        "# GigAI command reference",
        "",
        "Generated by `make cli-manual` from `gigai agent-context`; do not edit by hand.",
        f"{manual['commandCount']} commands (schema v{manual['schemaVersion']}). "
        "Agents: run `gigai agent-context --json` for the machine-readable form.",
        "",
        "- **effect**: `read` never changes state; `write` may change GigAI state or files (for some commands only with certain options).",
        "- **external**: the strongest thing the command can reach. `none` is offline, `model` may call a model provider (cost, and text leaves this machine), `network` may make other network requests.",
        "",
    ]
    for entry in manual["commands"]:
        heading = f"## `gigai {entry['command']}`" + (" (hidden)" if entry["hidden"] else "")
        lines += [heading, "", entry["summary"], "", f"`{entry['usage']}`", ""]
        lines.append(f"effect: `{entry['effect']}` · external: `{entry['external']}` · output: {entry['output']}")
        lines.append("")
        if entry["aliases"]:
            lines += ["Also registered as: " + ", ".join(f"`gigai {name}`" for name in entry["aliases"]), ""]
        lines += ["Example:", "", "```sh", *entry["examples"], "```", ""]
        if entry["notes"]:
            lines += [entry["notes"], ""]
    return "\n".join(lines).rstrip() + "\n"


@click.command("agent-context")
@click.option("--json", "as_json", is_flag=True, help="Emit the full machine-readable command schema.")
@click.pass_context
def agent_context_command(context: click.Context, as_json: bool) -> None:
    """Print the command manual for agents: what every command does and costs."""

    manual = build_manual(context.find_root().command)  # type: ignore[arg-type]
    if as_json:
        click.echo(json.dumps(manual, indent=2, ensure_ascii=False))
    else:
        click.echo(summary_line(manual))


def main() -> None:
    """``python -m gigai.agent_context --markdown``: print docs/cli.md."""

    import sys

    from gigai.cli import cli

    manual = build_manual(cli)
    sys.stdout.write(render_markdown(manual) if "--markdown" in sys.argv[1:] else json.dumps(manual, indent=2) + "\n")


if __name__ == "__main__":
    main()
