""":command:`gigai agent-skill` and :command:`gigai agent-permissions`: the agent side of Scout.

The instructions are ONE packaged source file, rendered either as a Claude Code
skill (``SKILL.md`` with frontmatter) or as an ``AGENTS.md`` section. Both
commands print by default; ``--out`` writes only the file the user named, and
nothing here ever touches an agent's own settings.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import click

SKILL_NAME = "gigai-scout"
SKILL_DESCRIPTION = (
    "Run the daily GigAI Scout job-search loop with the `gigai` CLI: what is new on Scout, "
    "assess postings, open questions, answers and stories, tailored resume PDF. Use when the user "
    "asks \"what's new on Scout\", about job postings or GigAI."
)
FORMATS = ("skill", "agents-md")

#: What a user can paste into their Claude Code settings.json. GigAI prints it, never applies it.
PERMISSIONS_SNIPPET: dict[str, dict[str, list[str]]] = {
    "permissions": {
        "allow": [
            "Bash(gigai *)",
            "Bash(curl http://127.0.0.1:8765/*)",
            "Bash(curl http://localhost:8765/*)",
        ],
        "deny": [
            "Read(~/.gigai/**)",
            "Bash(cat ~/.gigai/**)",
        ],
    }
}
PERMISSIONS_EXPLANATION = (
    "This stops your agent from reading GigAI's files by accident and lets it use the GigAI "
    "command line and Scout's local API on this computer. It is a guard against accidents, "
    "not a security boundary: an agent that can run commands on your computer can read "
    "GigAI's files directly. The curl rules cover only `curl http://127.0.0.1:8765/…` written "
    "exactly that way; the agent should prefer the `gigai` CLI. Keep your agent's permission prompts on."
)


def source_text() -> str:
    return resources.files("gigai").joinpath("data/agent/scout-agent-instructions.md").read_text(encoding="utf-8")


def render(fmt: str) -> str:
    source = source_text().rstrip("\n")
    if fmt == "skill":
        return f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESCRIPTION}\n---\n\n{source}\n"
    # AGENTS.md section: one level down, so it nests under the user's own headings.
    lines = [("#" + line) if line.startswith("#") else line for line in source.split("\n")]
    return "\n".join(lines) + "\n"


@click.command("agent-skill")
@click.option("--format", "fmt", type=click.Choice(FORMATS), default="skill", show_default=True,
              help="skill: a Claude Code SKILL.md. agents-md: a section for an AGENTS.md.")
@click.option("--out", "out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write to this FILE instead of printing. Refuses to replace an existing file unless --force.")
@click.option("--force", is_flag=True, help="With --out: replace the file if it exists.")
def agent_skill_command(fmt: str, out: Path | None, force: bool) -> None:
    """Print the instructions that teach your AI agent the daily Scout loop."""

    text = render(fmt)
    if out is None:
        click.echo(text, nl=False)
        return
    if out.exists() and not force:
        raise click.ClickException(f"{out} already exists; pass --force to replace it.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    click.echo(f"Wrote {out}")


@click.command("agent-permissions")
@click.option("--agent", type=click.Choice(["claude-code"]), default="claude-code", show_default=True)
def agent_permissions_command(agent: str) -> None:
    """Print a recommended Claude Code permissions snippet for you to apply. Writes nothing."""

    click.echo("Add this to your Claude Code settings.json (merge with what is there):\n")
    click.echo(json.dumps(PERMISSIONS_SNIPPET, indent=2))
    click.echo("\n" + PERMISSIONS_EXPLANATION)
