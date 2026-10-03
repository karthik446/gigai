"""0.1.10.7 I: every ``gigai ...`` command in a fenced block of the docs, the README and the changelog exists.

Like ``test_agent_skill.py`` for the skill: the command path resolves in the real Click tree, ``--help`` works for
it, and every ``--flag`` on the line is one of its options. A renamed or removed command fails here, not in a
reader's terminal.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gigai.cli import cli

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"
#: Hand-written pages (the generated CLI reference is checked by test_docs_gen and test_agent_context).
PAGES = (
    "README.md",
    "CHANGELOG.md",
    "gigai-docs/src/content/docs/scout/agents.md",
    "gigai-docs/src/content/docs/scout/numbers.md",
    "gigai-docs/src/content/docs/scout/quickstart.md",
    "gigai-docs/src/content/docs/scout/resume.md",
    "gigai-docs/src/content/docs/scout/privacy.md",
    "gigai-docs/src/content/docs/agents.md",
    # 0.1.10.8: the pages an agent follows before anything is installed, and the two pages beside them.
    "gigai-docs/src/content/docs/scout/agents/start.md",
    "gigai-docs/src/content/docs/scout/first-10-minutes.md",
    "gigai-docs/src/content/docs/scout/tokens.md",
)
#: The public llms.txt (plain text, no fenced blocks): its commands are the inline code spans that start with ``gigai``.
LLMS_TXT = "gigai-docs/src/llms.template.txt"
#: The inline commands an agent is told to run on the start page's prose and in the README's starter prompt.
INLINE_PAGES = (LLMS_TXT, "gigai-docs/src/content/docs/scout/agents/start.md", "README.md")
FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)
SPAN = re.compile(r"`(gigai [^`\n]+)`")
FLAG = re.compile(r"^--[a-z][a-z-]*$")


def _commands(text: str) -> list[str]:
    """The ``gigai`` command lines of every fenced block, a backslash-continued command joined into one."""

    found: list[str] = []
    for block in FENCE.findall(text):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            line = line.strip()
            if line.startswith("gigai "):
                found.append(line)
    return found


def _resolve(line: str) -> tuple[list[str], click.Command, list[str]]:
    words = shlex.split(line, comments=True)[1:]
    for stop in (">", "|", "&&", ";"):
        if stop in words:
            words = words[: words.index(stop)]
    command: click.Command = cli
    path: list[str] = []
    while isinstance(command, click.Group) and words and words[0] in command.commands:
        command = command.commands[words[0]]
        path.append(words.pop(0))
    return path, command, words


def _inline_commands(text: str) -> list[str]:
    """The ``gigai ...`` code spans outside fenced blocks, each once."""

    return list(dict.fromkeys(SPAN.findall(FENCE.sub("", text))))


def _cases() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for rel in PAGES:
        path = ROOT / rel
        if not path.is_file():
            continue  # gigai-docs is left out of the offline container build context
        cases.extend((rel, line) for line in _commands(path.read_text(encoding="utf-8")))
    for rel in INLINE_PAGES:
        path = ROOT / rel
        if path.is_file():
            cases.extend((f"{rel} (inline)", line) for line in _inline_commands(path.read_text(encoding="utf-8")))
    return cases


def test_the_pages_hold_fenced_commands_to_check() -> None:
    if not DOCS.is_dir():
        pytest.skip("gigai-docs is excluded from the offline container build context")
    by_page: dict[str, int] = {}
    for rel, _line in _cases():
        by_page[rel] = by_page.get(rel, 0) + 1
    assert by_page.get("README.md", 0) >= 4, by_page
    assert by_page.get("gigai-docs/src/content/docs/scout/agents.md", 0) >= 30, by_page
    # 0.1.10.8: the start page and the public llms.txt teach the whole setup, so both hold its commands.
    assert by_page.get("gigai-docs/src/content/docs/scout/agents/start.md", 0) >= 12, by_page
    assert by_page.get(f"{LLMS_TXT} (inline)", 0) >= 12, by_page


@pytest.mark.parametrize(("rel", "line"), _cases(), ids=lambda value: value if isinstance(value, str) and len(value) < 60 else None)
def test_a_fenced_gigai_command_resolves_against_the_real_cli(rel: str, line: str) -> None:
    path, command, rest = _resolve(line)
    if not path:  # an option of gigai itself: `gigai --version`
        assert rest and all(FLAG.match(word) for word in rest), f"{rel}: {line!r} names no gigai command"
    else:
        assert not isinstance(command, click.Group), f"{rel}: {line!r} stops at the group {' '.join(path)!r}"
    result = CliRunner().invoke(cli, [*path, "--help"])
    assert result.exit_code == 0, f"{rel}: gigai {' '.join(path)} --help failed: {result.output}"
    known = {opt for param in command.params if isinstance(param, click.Option) for opt in (*param.opts, *param.secondary_opts)}
    for word in rest:
        if FLAG.match(word.split("=", 1)[0]):
            assert word.split("=", 1)[0] in known, f"{rel}: {line!r}: {word} is not an option of gigai {' '.join(path)}"
    # A line with no placeholder parses in full: required arguments present, values of the right type.
    # (An ALL-CAPS word is a placeholder too: `gigai scout resume check PATH`.)
    if path and not re.search(r"<[^>]+>|\$|\.\.\.|\b[A-Z]{3,}\b", line):
        try:
            command.make_context(command.name or "", list(rest), resilient_parsing=False)
        except click.BadParameter as exc:
            # The example names a file of the reader's (./resume.md): that it is not here is not drift.
            assert "does not exist" in exc.format_message(), f"{rel}: {line!r}: {exc.format_message()}"
