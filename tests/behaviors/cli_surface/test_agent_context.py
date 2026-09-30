"""0110-008: the shipped CLI manual cannot drift from the real Click tree."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import click
import jsonschema
import pytest
from click.testing import CliRunner

from gigai.agent_context import (
    EFFECTS,
    EXTERNALS,
    MANUAL_SCHEMA,
    build_manual,
    iter_commands,
    load_prose,
    render_markdown,
)
from gigai.cli import cli

REPO = Path(__file__).resolve().parents[3]


def _registered() -> dict[str, tuple[click.Command, bool]]:
    return {" ".join(path): (command, hidden) for path, command, hidden in iter_commands(cli)}


def test_every_command_has_prose_and_every_prose_has_a_command() -> None:
    prose = load_prose()["commands"]
    registered = _registered()
    assert sorted(set(registered) - set(prose)) == []
    assert sorted(set(prose) - set(registered)) == []
    for key, entry in prose.items():
        assert entry["summary"].strip(), key
        assert len(entry["examples"]) >= 1, key
        assert entry["effect"] in EFFECTS, key
        assert entry["external"] in EXTERNALS, key
        assert entry["output"].strip(), key
        if "aliasOf" in entry:
            assert entry["aliasOf"] in registered, key
            assert registered[entry["aliasOf"]][0] is registered[key][0], key


def test_hidden_commands_are_marked_with_a_reason() -> None:
    prose = load_prose()["commands"]
    for key, (_command, hidden) in _registered().items():
        assert bool(prose[key].get("hidden", False)) is hidden, key
        if hidden:
            assert prose[key].get("hiddenReason", "").strip(), key
    assert prose["scout prep"]["hidden"] is True


def _parse_example(example: str) -> None:
    words = shlex.split(example)
    assert words[0] == "gigai", example
    args = words[1:]
    context = click.Context(cli, info_name="gigai")
    command: click.Command = cli
    while isinstance(command, click.Group):
        assert args, example
        command = command.commands[args[0]]
        name, args = args[0], args[1:]
    command.make_context(name, args, parent=context)


def test_every_example_parses_against_the_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    # Examples name files that do not exist here; parsing must not touch disk.
    monkeypatch.setattr(click.Path, "convert", lambda self, value, param, ctx: value)
    for key, entry in load_prose()["commands"].items():
        for example in entry["examples"]:
            assert example.startswith(f"gigai {key} ") or example == f"gigai {key}" or "aliasOf" in entry, (key, example)
            try:
                _parse_example(example)
            except click.ClickException as exc:
                pytest.fail(f"{key}: example {example!r} does not parse: {exc.format_message()}")


def test_manual_json_validates_and_takes_options_from_click() -> None:
    manual = build_manual(cli)
    jsonschema.Draft202012Validator(MANUAL_SCHEMA).validate(manual)
    assert manual["commandCount"] == len(manual["commands"]) == len(_registered())
    by_command = {entry["command"]: entry for entry in manual["commands"]}
    options = {option["name"]: option for option in by_command["scout run"]["options"]}
    assert "--port" in options and options["--port"]["type"] == "integer"
    assert by_command["scout assess"]["external"] == "model"
    assert by_command["comparison start"]["aliases"] == ["comparison run"]
    assert by_command["scout prep"]["hidden"] is True


def test_agent_context_command_and_first_help_line() -> None:
    runner = CliRunner()
    text = runner.invoke(cli, ["agent-context"])
    assert text.exit_code == 0, text.output
    assert text.output.strip().startswith(f"{len(_registered())} commands (schema v1).")
    as_json = runner.invoke(cli, ["agent-context", "--json"])
    assert as_json.exit_code == 0, as_json.output
    jsonschema.Draft202012Validator(MANUAL_SCHEMA).validate(json.loads(as_json.output))
    help_text = runner.invoke(cli, ["--help"]).output
    first_line = help_text.split("Options:")[0].split("\n\n")[1].replace("\n", " ")
    assert "gigai agent-context" in first_line[:80]


def test_markdown_manual_renders_every_command() -> None:
    # The site's CLI pages (tools/docs_gen.py, gated by tests/behaviors/ci_tooling/test_docs_gen.py)
    # are built from this renderer; docs/cli.md and `make cli-manual` were replaced by them.
    manual = build_manual(cli)
    text = render_markdown(manual)
    for entry in manual["commands"]:
        assert f"## `gigai {entry['command']}`" in text, entry["command"]
    assert not (REPO / "docs" / "cli.md").exists()
