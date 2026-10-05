"""0.1.10.7 G: the agent instructions cannot drift from the CLI, carry no contact data, and
the permissions command only prints."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gigai.agent_skill import PERMISSIONS_SNIPPET, render, source_text
from gigai.cli import cli
from gigai.scout.ats_score import ATS_WORDING
from gigai.scout.pipeline.steps import LABEL_NAME, LABEL_WORDING

SPAN = re.compile(r"`(gigai [^`]+)`")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def _spans() -> list[str]:
    return SPAN.findall(source_text())


def _resolve(words: list[str]) -> list[tuple[click.Command, list[str]]]:
    """Walk the subcommand words ('a|b' alternates expand); return (command, remaining words)."""
    found: list[tuple[click.Command, list[str]]] = []

    def walk(command: click.Command, rest: list[str]) -> None:
        if isinstance(command, click.Group) and rest and re.fullmatch(r"[a-z][a-z-]*(\|[a-z][a-z-]*)*", rest[0]):
            for name in rest[0].split("|"):
                assert name in command.commands, f"no command {name!r}"
                walk(command.commands[name], rest[1:])
            return
        found.append((command, rest))

    walk(cli, words)
    return found


def test_the_skill_cites_commands_that_exist() -> None:
    spans = _spans()
    assert len(spans) >= 15
    for span in spans:
        words = span.split()
        assert words[0] == "gigai"
        for command, rest in _resolve(words[1:]):
            result = CliRunner().invoke(cli, [*_path(command), "--help"])
            assert result.exit_code == 0, span
            known = {opt for param in command.params if isinstance(param, click.Option) for opt in param.opts + param.secondary_opts}
            for flag in FLAG.findall(" ".join(rest)):
                assert flag in known, f"{span}: {flag} is not a flag of {_path(command)}"


def _path(command: click.Command) -> list[str]:
    def walk(group: click.Group, prefix: list[str]):
        for name, child in group.commands.items():
            if child is command:
                return [*prefix, name]
            if isinstance(child, click.Group):
                found = walk(child, [*prefix, name])
                if found:
                    return found
        return None

    found = walk(cli, [])  # type: ignore[arg-type]
    assert found
    return found


def test_the_spans_without_pseudo_syntax_parse_in_full() -> None:
    for span in _spans():
        if re.search(r"[|\[\]()]|\.\.\.|\bTEXT\b|\bPATH\b|\bN\b|\bS\b", span) or " / " in span:
            continue
        words = span.split()[1:]
        (command, rest), = _resolve(words)
        command.make_context(command.name or "", rest)


def test_wording_comes_from_the_existing_constants() -> None:
    text = source_text()
    assert ATS_WORDING.replace("’", "'") in text
    assert LABEL_WORDING in text
    assert LABEL_NAME in text


def test_no_contact_shapes_and_no_forbidden_phrases() -> None:
    for text in (source_text(), render("skill"), render("agents-md")):
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        assert not re.search(r"\+?\d[\d ()-]{8,}\d", text)
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)\S+", text)
        lowered = text.lower()
        assert "ready to apply" not in lowered
        assert not re.search(r"gigai (scout )?today\b", lowered)
        assert "`today`" not in lowered
    assert len(source_text().splitlines()) <= 150


def test_both_formats_render_and_output_is_stable() -> None:
    skill, agents = render("skill"), render("agents-md")
    assert skill.startswith("---\nname: gigai-scout\ndescription: ")
    assert skill.split("---\n")[1].count("\n") == 2
    assert agents.startswith("## Working with GigAI Scout") and "### The daily loop" in agents
    assert "\n# " not in agents
    result = CliRunner().invoke(cli, ["agent-skill"])
    assert result.exit_code == 0 and result.output == skill
    assert CliRunner().invoke(cli, ["agent-skill", "--format", "agents-md"]).output == agents
    # Golden digests: a deliberate edit of the instructions updates these two lines.
    assert hashlib.sha256(skill.encode()).hexdigest() == GOLDEN_SKILL
    assert hashlib.sha256(agents.encode()).hexdigest() == GOLDEN_AGENTS


def test_out_writes_only_the_named_file_and_never_replaces_silently(tmp_path: Path) -> None:
    target = tmp_path / "SKILL.md"
    ok = CliRunner().invoke(cli, ["agent-skill", "--out", str(target)])
    assert ok.exit_code == 0 and target.read_text() == render("skill")
    assert [p.name for p in tmp_path.iterdir()] == ["SKILL.md"]
    refused = CliRunner().invoke(cli, ["agent-skill", "--out", str(target), "--format", "agents-md"])
    assert refused.exit_code != 0 and target.read_text() == render("skill")
    assert CliRunner().invoke(cli, ["agent-skill", "--out", str(target), "--format", "agents-md", "--force"]).exit_code == 0


def test_out_creates_missing_parent_folders(tmp_path: Path) -> None:
    target = tmp_path / "fresh" / ".claude" / "skills" / "gigai-scout" / "SKILL.md"
    result = CliRunner().invoke(cli, ["agent-skill", "--format", "skill", "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert target.read_text() == render("skill")


def test_the_skill_gates_every_resume_file_on_the_check() -> None:
    text = source_text()
    assert "gigai scout resume check PATH" in text and "gigai scout resume clean PATH --out resume-clean.md" in text
    assert "Never ask the user to paste a resume" in text
    assert len(text.splitlines()) <= 100
    description = render("skill").split("---\n")[1]
    assert "what's new on Scout" in description


def test_agent_permissions_prints_valid_json_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    result = CliRunner().invoke(cli, ["agent-permissions"])
    assert result.exit_code == 0
    start = result.output.index("{")
    end = result.output.rindex("}") + 1
    assert json.loads(result.output[start:end]) == PERMISSIONS_SNIPPET
    permissions = PERMISSIONS_SNIPPET["permissions"]
    assert permissions["allow"] == ["Bash(gigai *)", "Bash(curl http://127.0.0.1:8765/*)", "Bash(curl http://localhost:8765/*)"]
    assert permissions["deny"] == ["Read(~/.gigai/**)", "Bash(cat ~/.gigai/**)"]
    assert "curl http://127.0.0.1:8765/" in result.output and "prefer the `gigai` CLI" in result.output
    assert "not a security boundary" in result.output
    assert list(home.rglob("*")) == [] and list(work.rglob("*")) == []


def test_agent_permissions_takes_the_port_scout_runs_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.10.11 NF: a user who started Scout with ``--port`` got a snippet for 8765 only and had to change each line by hand."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = CliRunner().invoke(cli, ["agent-permissions", "--port", "18766"])
    assert result.exit_code == 0, result.output
    snippet = json.loads(result.output[result.output.index("{"): result.output.rindex("}") + 1])
    assert snippet["permissions"]["allow"] == ["Bash(gigai *)", "Bash(curl http://127.0.0.1:18766/*)", "Bash(curl http://localhost:18766/*)"]
    assert snippet["permissions"]["deny"] == PERMISSIONS_SNIPPET["permissions"]["deny"]
    assert "curl http://127.0.0.1:18766/" in result.output and "8765" not in result.output
    assert list(home.rglob("*")) == []
    # The default is the snippet as it always was, and a port that is none is refused.
    assert CliRunner().invoke(cli, ["agent-permissions", "--port", "8765"]).output == CliRunner().invoke(cli, ["agent-permissions"]).output
    assert CliRunner().invoke(cli, ["agent-permissions", "--port", "0"]).exit_code == 2


GOLDEN_SKILL = "77228cbef60aa8f5c5730a7bae99b499118676f9a5dcfa92cf31b91c5e4b5bc9"
GOLDEN_AGENTS = "98b88eccc2ed5b312363090c054119cc01199c75a17bb92bcf2fbf185d681847"
