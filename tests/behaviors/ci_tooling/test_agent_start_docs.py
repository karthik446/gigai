"""0.1.10.8 docs wave: the agent start page, the public llms.txt, the README starter prompt and the token page.

What these pin is the END outcome a reader gets: an agent that follows the page runs the contact-details check
BEFORE any step that reads or imports the resume, tells the user the network notice before the first Update sources,
and asks before model calls; the prompt a user pastes points at a real page of this release; every number on the
token page says where it came from and when.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"
START = DOCS / "scout" / "agents" / "start.md"
LLMS = ROOT / "gigai-docs" / "src" / "llms.template.txt"
PII_FIRST = "**Remove your name, email, phone, address and links from your resume before you give it to an agent.**"

pytestmark = pytest.mark.skipif(not DOCS.is_dir(), reason="gigai-docs is excluded from the offline container build context")


def _flat(text: str) -> str:
    return " ".join(re.sub(r"^\s*>\s?", "", text, flags=re.M).split())


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---", 2)[2]


def test_the_start_page_opens_with_the_bold_resume_warning() -> None:
    first = next(line for line in _body(START).splitlines() if line.strip())
    assert first == PII_FIRST
    # The same sentence is where a person meets it: the first-10-minutes page, the README and llms.txt.
    assert PII_FIRST in _body(DOCS / "scout" / "first-10-minutes.md")
    assert PII_FIRST in (ROOT / "README.md").read_text(encoding="utf-8")
    llms = LLMS.read_text(encoding="utf-8")
    assert PII_FIRST in llms and llms.index(PII_FIRST) < llms.index("1. ")


@pytest.mark.parametrize("path", [START, LLMS], ids=["start.md", "llms.txt"])
def test_the_resume_check_comes_before_any_step_that_reads_or_imports_the_resume(path: Path) -> None:
    """The PII gate: `resume check` is named before the first read, the first `resume add`, and `resume clean`."""

    text = path.read_text(encoding="utf-8")
    check = text.index("gigai scout resume check")
    # Every command that takes the resume in comes after the check ...
    for later in ("gigai scout resume add", "gigai scout resume clean"):
        assert later in text, later
        assert check < text.index(later), f"{path.name}: {later} is named before the check"
    assert text.count("gigai scout resume add") >= 1
    # ... and so does every sentence that lets the agent read the file.
    flat = _flat(text)
    reads = [m.start() for m in re.finditer(r"(?i)\b(?:may you read|read only a file|read the clean file|you read it)\b", flat)]
    assert reads, f"{path.name}: no sentence says when the agent may read the file"
    assert all(flat.index("gigai scout resume check") < at for at in reads)
    # The gate itself: ask for a PATH (not a paste), stop on findings, offer the cleaned copy, re-check it.
    assert re.search(r"(?i)ask (?:the user )?for the (?:\*\*)?(?:file )?path", flat), path.name
    assert re.search(r"(?i)exit code 2: (?:\*\*)?stop", flat), path.name
    assert "--out resume-clean.md" in flat
    assert re.search(r"(?i)check (?:that copy|the cleaned file again)", flat) or "gigai scout resume check resume-clean.md" in flat
    # Honest limits, said plainly.
    assert re.search(r"(?i)pattern", flat) and re.search(r"(?i)look at the cleaned file once", flat)
    assert re.search(r"(?i)pastes a resume with contact details straight into the chat", flat)
    assert "outside GigAI's control" in flat


def test_the_start_page_asks_before_the_network_and_before_model_calls() -> None:
    text = _flat(_body(START))
    # Order of the page: install, start without a browser, the gate, the profile, Update sources, scout new, the skill.
    steps = [
        "uv tool install gigai",
        "gigai scout run --no-browser",
        "gigai scout resume check",
        "curl -s -X PUT http://127.0.0.1:8765/api/setup",
        "gigai scout sources update",
        "gigai scout new --json",
        "gigai scout new --yes --json",
        "gigai agent-skill --format skill --out ~/.claude/skills/gigai-scout/SKILL.md",
        "gigai agent-permissions",
        "uv tool upgrade gigai",
    ]
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions), list(zip(steps, positions))
    assert "wait for a yes" in text and "Only on a yes" in text
    # Prerequisites the plan names: OS detection, Homebrew incl. Intel, Linux, Windows via WSL, no sudo, PATH.
    for fact in ("uname -s", "Apple Silicon or Intel", "brew install uv", "curl -LsSf https://astral.sh/uv/install.sh | sh",
                 "use WSL", "uv tool update-shell", "~/.local/bin"):
        assert fact in text.replace("\\|", "|"), fact
    assert re.search(r"(?i)ask the user before any command that needs `sudo`", text)
    # Never the unsafe Codex modes, and no Codex skill path (not verified).
    for banned in ("--yolo", "dangerously-bypass", "~/.codex/skills", "~/.agents/skills"):
        assert banned not in text, banned


def test_the_readme_starter_prompt_points_at_this_release_and_carries_the_fallbacks() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prompt = next(block for block in re.findall(r"```text\n(.*?)\n```", readme, flags=re.S) if block.startswith("Set up GigAI for me."))
    assert "\n" not in prompt, "one paragraph, so it pastes as one message"
    # /latest/ pages are redirect stubs, so the prompt names the release's own pages. The release is the newest
    # CHANGELOG entry: a release that forgets to move the prompt fails here.
    newest = re.search(r"^### (\d+(?:\.\d+)+)$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), flags=re.M)
    assert newest is not None
    version = newest.group(1)
    assert f"https://karthik446.github.io/gigai/{version}/scout/agents/start/" in prompt
    assert f"`curl -fsSL https://karthik446.github.io/gigai/{version}/llms.txt`" in prompt, "the fallback when the agent cannot open the page"
    assert "/latest/" not in prompt
    for must in (
        "`brew install uv`",
        "`curl -LsSf https://astral.sh/uv/install.sh | sh`",
        "on Windows use WSL",
        "do not use sudo without asking me",
        "`uv tool update-shell`",
        "`gigai scout run --no-browser`",
        "run GigAI's contact-details check on the file and stop if it finds any",
        "Ask me before any step that makes thousands of network requests (Update sources) or spends model calls (assessing)",
    ):
        assert must in prompt, must
    assert "--refresh --force" not in readme
    # The pages the prompt names exist in this tree, and the site builds llms.txt from the template.
    assert START.is_file() and LLMS.is_file()
    endpoint = (ROOT / "gigai-docs" / "src" / "pages" / "llms.txt.js").read_text(encoding="utf-8")
    assert "llms.template.txt?raw" in endpoint and "{BASE}" in endpoint


def test_llms_txt_follows_the_llmstxt_org_shape() -> None:
    """H1, a blockquote summary, text with no further headings, then H2 sections that hold only link lists."""

    lines = LLMS.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# GigAI"
    assert next(line for line in lines[1:] if line.strip()).startswith("> ")
    headings = [(n, line) for n, line in enumerate(lines) if line.startswith("#")]
    assert [line for _n, line in headings] == ["# GigAI", "## Setup", "## Optional"]
    first_h2 = headings[1][0]
    for line in lines[first_h2:]:
        if line.strip() and not line.startswith("## "):
            assert re.match(r"^- \[[^\]]+\]\(\{BASE\}[a-z0-9/-]+/\)(: .+)?$", line), line
    # Every link is a page of the docs tree.
    for rel in re.findall(r"\(\{BASE\}([a-z0-9/-]+)/\)", "\n".join(lines)):
        if rel == "scout/reference/api":
            continue  # generated by the OpenAPI plugin at build time
        assert (DOCS / f"{rel}.md").is_file() or (DOCS / rel / "index.md").is_file(), rel


def test_the_new_pages_are_in_the_sidebar() -> None:
    config = (ROOT / "gigai-docs" / "astro.config.mjs").read_text(encoding="utf-8")
    for slug in ("scout/agents/start", "scout/first-10-minutes", "scout/tokens"):
        assert f"'{slug}'" in config, slug
        assert (DOCS / f"{slug}.md").is_file()


def test_every_number_on_the_token_page_carries_its_dated_source() -> None:
    page = _body(DOCS / "scout" / "tokens.md")
    sections = {title: body for title, body in re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", page, flags=re.S | re.M)}
    assert list(sections) == ["What an agent costs", "Tokens per step", "A real run: the first catch-up and a normal day", "Codex or Claude Code", "See your own numbers"]
    # Each section that holds measured numbers names its source, the date and the versions.
    steps = _flat(sections["Tokens per step"])
    assert "Source: GigAI 0.1.10.8 development build, 2026-10-03, codex-cli 0.159.3 and Claude Code 2.1.288" in steps
    assert "six calls per CLI" in steps and "read with `gigai scout metrics --json`" in steps
    assert "made-up resume and two short made-up postings" in steps
    real = _flat(sections["A real run: the first catch-up and a normal day"])
    assert "GigAI 0.1.10.7, Codex CLI on a ChatGPT subscription" in real and "read on 2026-10-03 with `gigai scout metrics --json`" in real
    assert "examples, not a promise for your plan" in real and "Worked out from the numbers above" in real
    assert "has not been measured yet" in real
    # The table rows are the plan's steps, and the local step is zero tokens.
    rows = [row.split("|")[1].strip() for row in sections["Tokens per step"].splitlines() if row.startswith("| ") and "---" not in row][1:]
    assert rows == ["Rank", "First assessment", "Tag titles", "Tailor", "Re-assess after tailoring", "Scout ATS score"]
    assert steps.count("0 tokens: computed on your computer, no model") == 2
    # Each cell's total is its input plus its output (the page's own arithmetic).
    cells = re.findall(r"([\d,]+) tokens \(([\d,]+) in[^;)]*; ([\d,]+) out\)", page)
    assert len(cells) >= 12
    for total, tokens_in, tokens_out in cells:
        number = lambda text: int(text.replace(",", ""))  # noqa: E731
        assert number(total) == number(tokens_in) + number(tokens_out), (total, tokens_in, tokens_out)
    # Tokens, not prices: the only money on the page is what the Claude Code CLI itself reports, said as such.
    assert "$" not in page
    money = [sentence for sentence in re.split(r"(?<=[.:])\s+", _flat(page)) if "dollar" in sentence.lower()]
    assert len(money) == 1 and "The Claude Code CLI also reports an API price for each call" in money[0]
    # The caps are the code's.
    from gigai.scout.pipeline import settings

    assert settings.DEFAULT_MAX_MODEL_CALLS_PER_DAY == 40 and settings.DEFAULT_RANK_MAX_CALLS_PER_DAY == 100
    assert settings.DEFAULT_RANK_WARN_CALLS_PER_DAY == 60
    cost = _flat(sections["What an agent costs"])
    assert "at most **40** model calls a day" in cost and "at most **100** calls a day, with a warning past 60" in cost
    assert "**It asks before it assesses.**" in cost and "**The first run is the expensive one.**" in cost
    from gigai.scout.find_jobs import model_rank

    assert model_rank.DEFAULT_BATCH_SIZE == 50 and "**One rank call covers 50 postings**" in steps


def test_the_agents_page_says_how_each_agent_picks_scout_up() -> None:
    page = _flat(_body(DOCS / "scout" / "agents.md"))
    section = page.split("## Use it from your agent", 1)[1].split("## The security model", 1)[0]
    for heading in ("### Claude Code", "### Codex", "### Any other agent", "### What it costs"):
        assert heading in section, heading
    assert "gigai agent-skill --format skill --out ~/.claude/skills/gigai-scout/SKILL.md" in section
    assert "Start a new Claude Code session" in section
    assert "`~/.claude/settings.json` (all projects) or `.claude/settings.local.json` (this project only)" in section
    assert "`~/.codex/AGENTS.md` for every project, or the `AGENTS.md` at the root of one repository" in section
    assert "`workspace-write`" in section and "`on-request`" in section and "network access off" in section
    assert "codex --search" in section and "curl -fsSL" in section
    assert "GET http://127.0.0.1:8765/llms.txt" in section and "GET http://127.0.0.1:8765/api/openapi.json" in section
    assert "| Claude Code | The skill file" in section and "| Codex | An `AGENTS.md` section" in section and "| Any other agent |" in section
    # Not verified, so not presented: a Codex skill folder. Never recommended: the unsafe Codex modes.
    for banned in ("~/.codex/skills", "~/.agents/skills", "--yolo"):
        assert banned not in page, banned
    assert "Do not turn the sandbox off for GigAI" in section
    # U3's behaviour (0110-8-04, -08, -14): the order, the two questions, the counts, the progress lines.
    daily = page.split("### 1. What is new", 1)[1].split("### 2. Public and private", 1)[0]
    assert "best score first" not in page and "% of requirements met" not in page
    assert "Postings with a current assessment come first" in daily
    assert "`--yes` never touches it" in daily and "gigai scout new --reassess-stale --json" in daily
    assert "`counts.to_assess`" in daily and "`counts.only_stale`" in daily
    assert "`assessed 120 of 333 · ~18 min left`" in daily and "stderr" in daily
