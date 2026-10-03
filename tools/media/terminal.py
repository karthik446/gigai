"""Terminal screenshots of the agent loop: a scripted transcript around REAL `gigai` commands.

What is real: every command line shown is run, as shown, against the demo home (`GIGAI_HOME`
points at it, so no path is typed), and the text under it is that command's own output, uncut.
What is scripted: the `you` and `agent` lines (persona.py). No model is called, so the build is
the same every time; when a command or its output changes, the frames change with it, and a
command that fails stops the build.

It runs AFTER the UI screenshots: `gigai scout new --yes` assesses the new postings and moves the
"new since" mark, which the Jobs screenshots need untouched.

Rendering: by default each frame is an HTML page shot with the same headless Chromium as the UI
screenshots (the "rendered text frame"): the same pixels on macOS and Linux, exact text for the
privacy gate, nothing more to install. `MEDIA_TERMINAL=vhs` renders the same transcript through
VHS's `Screenshot` instead (needs `vhs`, `ttyd` and `ffmpeg` on PATH).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from . import persona
from .demo_home import SEAM_ENV, DemoHome

WIDTH = 1280
COLUMNS = 150
COMMAND_TIMEOUT_SECONDS = 120


class TerminalError(RuntimeError):
    pass


@dataclass
class Frame:
    name: str
    alt: str
    lines: list[tuple[str, str]] = field(default_factory=list)  # (kind, text); kind: you | agent | command | output, plus " more" on a continuation line

    def text(self) -> str:
        return "\n".join(text for _kind, text in self.lines)


class _Session:
    """Runs `gigai ...` for real in the demo home's work folder and records what it printed."""

    def __init__(self, demo: DemoHome) -> None:
        self.work = Path(demo.root) / "work"
        self.work.mkdir(exist_ok=True)
        bin_dir = str(Path(sys.executable).parent)
        self.env = {
            **os.environ, **SEAM_ENV, "GIGAI_HOME": demo.home, "COLUMNS": str(COLUMNS), "NO_COLOR": "1",
            "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
        }

    def run(self, frame: Frame, *args: str) -> str:
        frame.lines.append(("command", "gigai " + shlex.join(args)))
        done = subprocess.run(
            ["gigai", *args], cwd=self.work, env=self.env, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
        )
        if done.returncode != 0:
            raise TerminalError(f"`gigai {' '.join(args[:3])}` failed ({done.returncode}): {(done.stderr or done.stdout)[-600:]}")
        output = done.stdout.rstrip("\n")
        frame.lines.extend(("output", line) for line in output.splitlines())
        return output


_SINCE = re.compile(r"--since (\S+)")


def record(demo: DemoHome) -> list[Frame]:
    """Run the agent loop once and return its frames."""

    session = _Session(demo)
    answer, story = persona.TERMINAL_ANSWER, persona.TERMINAL_STORY

    new = Frame("terminal-new", "An agent runs `gigai scout new`: what is new, and the cost, before anything is assessed.")
    new.lines.append(("you", "What's new on Scout?"))
    asked = session.run(new, "scout", "new")
    since = _SINCE.search(asked)
    if since is None:
        raise TerminalError("`gigai scout new` did not ask before assessing (no --since in its output)")
    new.lines.append(("agent", f"{persona.posting_count(wave=2)} new postings. Assessing them is about 3 model calls on your local model. Go ahead?"))

    assessed = Frame("terminal-assessed", "After your yes, the agent assesses the new postings and shows them ranked.")
    assessed.lines.append(("you", "Yes."))
    session.run(assessed, "scout", "new", "--yes", "--since", since.group(1))

    yours = Frame("terminal-yours", "`gigai scout new --yours`: what matches, from your own resume and answers, as a separate call.")
    yours.lines.append(("you", "What matches for those?"))
    session.run(yours, "scout", "new", "--yours", "--since", since.group(1))

    answered = Frame("terminal-answer", "The agent asks a question, saves your answer, and offers to make it a story.")
    answered.lines += [("agent", answer["question"]), ("you", answer["answer"])]
    session.run(
        answered, "scout", "answers", "save", answer["question_id"], "--question", answer["question"],
        "--answer-text", answer["answer"], "--actor", "agent",
    )
    answered.lines.append(("agent", "Saved. That sounds like an experience worth keeping. Want me to make this a story?"))

    told = Frame("terminal-story", "The agent writes the story from your own words and saves it on your OK.")
    told.lines += [
        ("you", "Yes. " + story["raw_text"]),
        ("agent", "Here it is, in your words:"),
        *(("agent", f"  {part.capitalize()}: {story[part]}") for part in ("situation", "task", "action", "result")),
        ("agent", "Save it?"),
        ("you", "Save it."),
    ]
    session.run(
        told, "scout", "story", "save", "--title", story["title"], "--raw-text", story["raw_text"],
        "--situation", story["situation"], "--task", story["task"], "--action", story["action"], "--result", story["result"],
        "--tag", story["tag"], "--answers", story["answers"], "--actor", "agent",
    )

    pdf = Frame("terminal-pdf", "The agent renders the tailored resume as a headerless PDF and hands you the link to finish it in Scout.")
    pdf.lines.append(("you", f"Make the PDF for the {persona.COMPANIES[0].name} job."))
    # A relative --out: the default prints the PDF's full path, which names the folder it ran in.
    session.run(pdf, "scout", "resume", "pdf", "--tailored", "--job-url", demo.hero_job, "--out", "resume.pdf")
    pdf.lines.append(("agent", "Done. The PDF has no name or contact details: open that link to add yours in the browser and download it."))

    return [new, assessed, yours, answered, told, pdf]


_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; background: #11131a; }
.window { width: %(width)dpx; padding: 0 0 22px; background: #161923; color: #d7dbe6;
  font: 13px/1.5 ui-monospace, "SF Mono", Menlo, "DejaVu Sans Mono", "Liberation Mono", monospace; }
.bar { display: flex; gap: 8px; align-items: center; padding: 12px 16px; background: #1f2330; color: #8b93a7; font-size: 12px; }
.dot { width: 11px; height: 11px; border-radius: 50%%; background: #3a4052; }
.title { margin-left: 10px; }
.body { padding: 14px 22px 0; }
.line { white-space: pre; min-height: 1.5em; }
.you { color: #ffffff; font-weight: 600; margin-top: 10px; }
.you::before { content: "you    > "; color: #7aa2f7; }
.agent { color: #c3e88d; margin-top: 2px; }
.agent::before { content: "agent  > "; color: #7fb069; }
.more { padding-left: 9ch; }
.you.more, .agent.more { margin-top: 0; }
.you.more::before, .agent.more::before { content: none; }
.command { color: #f2cc8f; margin-top: 8px; }
.command::before { content: "  $ "; color: #8b93a7; }
.command.more { margin-top: 0; padding-left: 6ch; }
.command.more::before { content: none; }
.output { color: #aab1c5; padding-left: 4ch; white-space: pre-wrap; overflow-wrap: anywhere; }
"""


def frame_html(frame: Frame) -> str:
    rows = [f'<div class="line {kind}">{html.escape(text)}</div>' for kind, text in frame.lines]
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>" + _CSS % {"width": WIDTH} + "</style></head><body>"
        "<div class='window'><div class='bar'><span class='dot'></span><span class='dot'></span><span class='dot'></span>"
        "<span class='title'>agent session: scripted lines, real gigai commands and output</span></div>"
        "<div class='body'>" + "".join(rows) + "</div></div></body></html>"
    )


def _wrap(frames: list[Frame]) -> None:
    """Fold the scripted lines (never a command's output) so nothing runs past the window."""

    import textwrap

    for frame in frames:
        folded: list[tuple[str, str]] = []
        previous = ""
        for kind, text in frame.lines:
            if kind == "agent" and previous == "agent":
                kind = "agent more"  # one speaker label per turn
            previous = kind.split()[0]
            if previous in ("you", "agent") and len(text) > COLUMNS - 12:
                parts = textwrap.wrap(text, COLUMNS - 12)
                folded.extend((kind if not index else f"{previous} more", part) for index, part in enumerate(parts))
            elif kind == "command" and len(text) > COLUMNS - 6:
                parts = textwrap.wrap(text, COLUMNS - 10, break_long_words=False, break_on_hyphens=False)
                folded.extend(("command" if not index else "command more", part + (" \\" if index < len(parts) - 1 else "")) for index, part in enumerate(parts))
            else:
                folded.append((kind, text))
        frame.lines = folded


def render_html(frames: list[Frame], out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_context(viewport={"width": WIDTH, "height": 400}, device_scale_factor=1).new_page()
            for frame in frames:
                page.set_content(frame_html(frame))
                page.locator(".window").screenshot(path=str(out / f"{frame.name}.png"))
        finally:
            browser.close()


def render_vhs(frames: list[Frame], out: Path) -> None:
    """The same transcript through VHS: each frame is printed in a real terminal and captured with `Screenshot`."""

    for tool in ("vhs", "ttyd", "ffmpeg"):
        if shutil.which(tool) is None:
            raise TerminalError(f"MEDIA_TERMINAL=vhs needs `{tool}` on PATH (brew install vhs ttyd ffmpeg)")
    colors = {"you": "1;37", "agent": "32", "command": "33", "output": "0"}
    prefixes = {"you": "you    > ", "agent": "agent  > ", "command": "  $ ", "output": "    ", "more": "         "}

    def ansi_line(kind: str, text: str) -> str:
        base = kind.split()[0]
        return f"\033[{colors[base]}m{prefixes['more' if kind.endswith(' more') else base]}{text}\033[0m"

    work = out / ".vhs"
    work.mkdir(exist_ok=True)
    for frame in frames:
        ansi = "\n".join(ansi_line(kind, text) for kind, text in frame.lines)
        (work / f"{frame.name}.ansi").write_text(ansi + "\n", encoding="utf-8")
        height = 60 + 24 * (len(frame.lines) + 2)
        tape = "\n".join([
            f"Output {frame.name}.gif", 'Set Shell "bash"', "Set FontSize 14", f"Set Width {WIDTH + 180}", f"Set Height {height}",
            "Set Padding 20", 'Set Theme "Catppuccin Mocha"', "Hide", f'Type "clear && cat {frame.name}.ansi"', "Enter", "Sleep 800ms",
            "Show", f"Screenshot {frame.name}.png", "Sleep 200ms", "",
        ])
        (work / f"{frame.name}.tape").write_text(tape, encoding="utf-8")
        # PS1 is set here, not typed, so the prompt shows no user or host name.
        done = subprocess.run(
            ["vhs", f"{frame.name}.tape"], cwd=work, env={**os.environ, "PS1": "$ "}, capture_output=True, text=True, timeout=180,
        )
        if done.returncode != 0 or not (work / f"{frame.name}.png").is_file():
            raise TerminalError(f"vhs failed on {frame.name}: {(done.stderr or done.stdout)[-600:]}")
        shutil.move(str(work / f"{frame.name}.png"), out / f"{frame.name}.png")
    shutil.rmtree(work)


def take_all(demo: DemoHome, out: Path, *, renderer: str | None = None, log=print) -> list[dict[str, str]]:
    renderer = renderer or os.environ.get("MEDIA_TERMINAL", "html")
    if renderer not in ("html", "vhs"):
        raise TerminalError(f"MEDIA_TERMINAL must be html or vhs, not {renderer}")
    out.mkdir(parents=True, exist_ok=True)
    frames = record(demo)
    _wrap(frames)
    for frame in frames:
        (out / f"{frame.name}.txt").write_text(frame.text(), encoding="utf-8")
    (render_vhs if renderer == "vhs" else render_html)(frames, out)
    for frame in frames:
        log(f"  {frame.name}.png")
    return [{"file": f"{frame.name}.png", "kind": "terminal", "scheme": "dark", "alt": frame.alt} for frame in frames]
