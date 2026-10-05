"""Desktop screenshots of the real Scout UI on the demo home: 1280x800, light and dark.

One function per shot; each waits on a `data-testid` (or a `data-role` where the UI has no
test id for that element yet), never on visible text. Beside every PNG the page's own text is
written to `<name>.txt` (the body's text plus every form field's value): the privacy gate reads
that exact text as well as the pixels.

A console error, a page error or a failed request fails the build: a screenshot of a broken
page is worse than no screenshot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import persona
from .demo_home import DemoHome

VIEWPORT = {"width": 1280, "height": 800}
SCHEMES = ("light", "dark")
WAIT_MS = 20_000


class ShotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Shot:
    name: str
    alt: str  # the docs' alt text and the manifest's description
    take: Callable[[object, DemoHome], None]


def _tid(name: str) -> str:
    return f'[data-testid="{name}"]'


def _settle(page) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")


STICKY_BAR = 50  # the top bar stays on screen; anything scrolled above this line is behind it


def _top_of(page, selector: str, *, closest: str | None = None) -> None:
    """Scroll so `selector` (or its nearest `closest` ancestor) starts just under the top bar."""

    page.evaluate(
        "([selector, closest, gap]) => { let el = document.querySelector(selector);"
        " if (closest) { el = el.closest(closest) || el; }"
        " window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - gap); }",
        [selector, closest, STICKY_BAR + 8],
    )


def _open_jobs(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/jobs")
    page.wait_for_selector(_tid("job-row"), timeout=WAIT_MS)
    _settle(page)


def _jobs(page, demo: DemoHome) -> None:
    _open_jobs(page, demo)
    # From the search box down: the filter chips, the profile chips and the first rows.
    _top_of(page, "#jobs-filter-search", closest=".panel")


def _jobs_new(page, demo: DemoHome) -> None:
    _open_jobs(page, demo)
    page.click(_tid("time-chip-new"))
    page.wait_for_function(
        f"document.querySelectorAll('{_tid('job-row')}').length === {persona.posting_count(wave=2)}", timeout=WAIT_MS
    )
    _settle(page)


def _approval_dialog(page, demo: DemoHome) -> None:
    _jobs_new(page, demo)
    page.click(_tid("assess-these"))
    page.wait_for_selector(_tid("approval-dialog"), timeout=WAIT_MS)


def _open_job(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/jobs/" + quote(demo.hero_job, safe=""))
    page.wait_for_selector(f'{_tid("step-timeline")}[data-state="done"]', timeout=WAIT_MS)
    page.wait_for_selector(f'{_tid("step-timeline")} {_tid("scout-label-chip")}', timeout=WAIT_MS)
    _settle(page)


def _job_page(page, demo: DemoHome) -> None:
    _open_job(page, demo)


def _job_pipeline(page, demo: DemoHome) -> None:
    _open_job(page, demo)
    _top_of(page, _tid("step-timeline"))


def _open_tailored(page, demo: DemoHome) -> None:
    """The job page with its Tailored resume panel read: the stored resume, its file in the resumes folder, who picked."""

    _open_job(page, demo)
    page.wait_for_selector(f'#job-resume[data-state="stored"] {_tid("resumes-folder-file")}', timeout=WAIT_MS)  # 0.1.11 N6: the panel's id
    page.wait_for_selector(_tid("picked-left-out"), timeout=WAIT_MS)
    _settle(page)


def _job_resume(page, demo: DemoHome) -> None:
    _open_tailored(page, demo)
    _top_of(page, "#job-resume")


def _picked_or_left_out(page, demo: DemoHome, which: str) -> None:
    _open_tailored(page, demo)
    page.click(f'{_tid("picked-left-out")} [data-action="show-{which}"]')
    # The list is there once the master is read: every line with its reason.
    page.wait_for_selector(f'{_tid("picked-left-out")} [data-role="{which}"] [data-item-id]', timeout=WAIT_MS)
    _settle(page)
    _top_of(page, _tid("picked-left-out"))


def _job_picked(page, demo: DemoHome) -> None:
    _picked_or_left_out(page, demo, "picked")


def _job_left_out(page, demo: DemoHome) -> None:
    _picked_or_left_out(page, demo, "left-out")


def _master(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/master")
    page.wait_for_selector('[data-role="master-page"]:not([data-master-revision=""]) [data-role="master-file"][data-file-state="current"]', timeout=WAIT_MS)
    page.wait_for_selector('[data-role="master-selections"] li[data-profile-id]', timeout=WAIT_MS)
    page.wait_for_selector('[data-master-section] li[data-line-id]', timeout=WAIT_MS)
    _settle(page)


def _master_lines(page, demo: DemoHome) -> None:
    _master(page, demo)
    # From the first section down: every role with its lines and each line's strength.
    _top_of(page, "[data-master-section]")


def _open_answers(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/answers")
    page.wait_for_selector('[data-role="answers"]', timeout=WAIT_MS)
    page.wait_for_selector('[data-role="stories"] [data-role="story-raw"]', timeout=WAIT_MS)
    _settle(page)


def _answers(page, demo: DemoHome) -> None:
    _open_answers(page, demo)


def _stories(page, demo: DemoHome) -> None:
    _open_answers(page, demo)
    _top_of(page, '[data-role="stories"]')


def _pdf(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe=""))
    page.wait_for_selector('[data-role="generate-pdf-form"]', timeout=WAIT_MS)
    # The header is typed in the browser, by the user: the persona's obviously fictional details.
    for key, value in persona.PDF_HEADER.items():
        page.locator(f"#generate-pdf-{key}").fill(value)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    _settle(page)


def _background(page, demo: DemoHome) -> None:
    page.goto(demo.url + "/#/settings")
    page.wait_for_selector(f'{_tid("approvals-list")} [data-role="approval"]', timeout=WAIT_MS)
    _settle(page)
    _top_of(page, _tid("background-panel"))


SHOTS: tuple[Shot, ...] = (
    Shot("jobs", "The Jobs page: every stored posting your profiles match, with filter chips, profile tags and Scout's chips.", _jobs),
    Shot("jobs-new", "The Jobs page filtered to what is new since the last check.", _jobs_new),
    Shot("approval-dialog", "The approval dialog: Scout says how many postings and what it costs before it assesses anything.", _approval_dialog),
    Shot("job-page", "A job page: the posting, its verdict and the requirements against your resume and answers.", _job_page),
    Shot("job-pipeline", "The same job page further down: the background pipeline's steps, the Scout label and the Scout ATS score, then the tailored resume.", _job_pipeline),
    Shot("job-resume", "The tailored resume on the job page, with where its file is in your resumes folder.", _job_resume),
    Shot("job-picked", "Picked: the lines of your master resume this job's resume shows, each with why.", _job_picked),
    Shot("job-left-out", "Left out: the other lines of your master resume, each with why, and Add to show one on this resume.", _job_left_out),
    Shot("master", "The Master resume page: its revision, the file you can edit in your resumes folder, and each profile's selection of it.", _master),
    Shot("master-lines", "The same page further down: every role and line of the master, each marked backed, stating a number, or stated.", _master_lines),
    Shot("answers", "Answers: what you told Scout or your agent, kept once and reused.", _answers),
    Shot("stories", "Stories: your experiences in your own words, with the questions each one answers.", _stories),
    Shot("pdf", "Generate PDF: you add your own name and contact details in the browser; GigAI stores none.", _pdf),
    Shot("background", "The Background pipeline panel, with jobs waiting for your approval.", _background),
)


def page_text(page) -> str:
    """The text a person can read on the page: the body's text and every field's value."""

    return page.evaluate(
        "() => [document.body.innerText, ...Array.from(document.querySelectorAll('input, textarea, select'))"
        ".map((el) => el.tagName === 'SELECT' ? (el.selectedOptions[0] ? el.selectedOptions[0].text : '') : el.value)]"
        ".filter(Boolean).join('\\n')"
    )


def take_all(demo: DemoHome, out: Path, *, log=print) -> list[dict[str, str]]:
    """Take every shot in both schemes. Returns one manifest row per PNG."""

    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    made: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for scheme in SCHEMES:
                context = browser.new_context(viewport=VIEWPORT, color_scheme=scheme, reduced_motion="reduce", device_scale_factor=1)
                for shot in SHOTS:
                    name = f"{shot.name}-{scheme}"
                    # A new page per shot: no chip, scroll position or open dialog is carried over.
                    page = context.new_page()
                    page.on("console", lambda message, name=name: problems.append(f"{name}: console {message.type}: {message.text}") if message.type == "error" else None)
                    page.on("pageerror", lambda error, name=name: problems.append(f"{name}: page error: {error}"))
                    page.on("response", lambda response, name=name: problems.append(f"{name}: {response.status} {response.request.method} {response.url.replace(demo.url, '')}") if response.status >= 400 else None)
                    shot.take(page, demo)
                    page.screenshot(path=str(out / f"{name}.png"))
                    (out / f"{name}.txt").write_text(page_text(page), encoding="utf-8")
                    page.close()
                    made.append({"file": f"{name}.png", "kind": "ui", "scheme": scheme, "alt": shot.alt})
                    log(f"  {name}.png")
                context.close()
        finally:
            browser.close()
    if problems:
        raise ShotError("the UI reported problems while the screenshots were taken:\n" + "\n".join(problems))
    return made
