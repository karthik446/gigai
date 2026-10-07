"""0.1.11.3 packet 7: a stored job resume's PDF stays on the page limit it was fitted to, on the END outcome.

The operator's case, on synthetic data: a job resume the fit measured at 2 pages (the selector's spacing, 0.9) was
rendered at the saved spacing (1.0, auto fit off) and ran to a 3rd page that held only the last row of Skills chips,
through the CLI and, with the Generate PDF form's header, through the UI's route.

Pinned on the page count of the PDF that comes back (pypdf reads it):

- the fixture IS the failing size: rendered at 1.0 with no page limit it is 3 pages, the 3rd holding only skills;
- ``gigai scout resume pdf --job-url URL`` writes 2 pages (the spacing tightened, never below 0.8) and no note;
- ``POST /api/tailored-resumes/pdf`` over HTTP on the real server (in this process), with a header of FOUR lines (the name, the
  title, a contact line that wraps to two), answers 2 pages and no ``X-GigAI-Fit-Note``;
- a spacing the user names below the floor is used as named, and a resume that fits keeps its saved spacing;
- a resume one more line long, which no spacing down to 0.8 fits, fits with the Skills laid out compactly; a
  longer one, compact at the tightest spacing (0.7); a longer one still gets the sentence below;
- every size the fit accepts (2 pages at 0.9) is 2 pages with the four-line header at a saved spacing of 1.0 and of 1.4;
- a resume that cannot fit is still rendered, with ONE plain sentence (the CLI's ``note``, the route's
  ``X-GigAI-Fit-Note``): page counts and what to do, no internal name, nothing of the resume.

Synthetic only: an invented person on reserved domains. Typst is a dependency of the package (no system binary),
so these run wherever the suite does.
"""

from __future__ import annotations

import io
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.scout.master_selection import FIT_SCALE
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_display import SPACING_MIN, DisplaySettings, form_header, save_display
from gigai.scout.resume_pdf import FIT_FLOOR, _body, _render, measure_markdown, over_limit_note, stored_resume_pdf
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import LENGTH_RULE, list_tailored_resumes

from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
TITLE = "Staff Software Engineer, Data Platform"
#: The Generate PDF form, long enough that the contact line wraps: with the saved title the header is four lines.
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "linkedin": "linkedin.example.invalid/in/zora-quillfeather-staff-engineer", "link": "https://zora-quillfeather.example.invalid/portfolio/work",
}
#: 0.1.11.3 item 15: the header is the name, the saved title, and ONE contact line (location, links, email, phone),
#: here too long for one line even set smaller, so it wraps once: four lines.
HEADER_TEXT = (
    "ZORA QUILLFEATHER" + TITLE + "Nowhere Springs, Colorado | linkedin.example.invalid/in/zora-quillfeather-staff-engineer | "
    "zora-quillfeather.example.invalid/portfolio/work | zora.quillfeather@example.invalid | +1 (555) 010-0142"
)


def _is_the_four_line_header(page: str) -> bool:
    """The page starts with the four header lines (where the contact line wraps is the layout's business)."""

    lines = [line.strip() for line in page.splitlines()]
    return "".join("".join(lines[:4]).split()) == "".join(HEADER_TEXT.split()) and lines[4] == "SUMMARY"
SKILLS = (
    "Python, Go, TypeScript, Kubernetes, PostgreSQL, Terraform, AWS, GCP, Kafka, Redis, Airflow, dbt, Snowflake, Spark, Docker, Helm, ArgoCD, "
    "Prometheus, Grafana, OpenTelemetry, gRPC, GraphQL, React, Node.js, FastAPI, PyTorch, LangChain, RAG, Vector search, CI/CD"
)
LAST_SKILLS = ("Prometheus", "CI/CD")


def resume(long_lines: int = 0, short_lines: int = 3, roles: int = 5) -> str:
    """Resume markdown of ``roles`` five-line roles (extra lines in the fifth), then Education and Skills (last)."""

    out = ["## Summary", "", "Engineer with twelve years building data platforms and inference services for regulated health products.", "", "## Experience", ""]
    for role in range(roles):
        out += [f"### Company {role} Systems", f"Staff Engineer | {2024 - 2 * role} - {2026 - 2 * role}", ""]
        out += [
            f"- Role {role} line {line}: built the scheduling and billing pipeline that moved {line + 3}0 million records a day across four regions with no data loss."
            for line in range(5)
        ]
        if role == 4:
            out += [
                f"- Extra line {line}: built the scheduling and billing pipeline that moved {line + 3}0 million records a day across four regions with no data loss."
                for line in range(long_lines)
            ]
            out += [f"- Short line {line} about on-call." for line in range(short_lines)]
        out += [""]
    out += ["## Education", "", "### Example State University", "BS Computer Science | 2010 - 2014", "", "## Skills", "", f"- {SKILLS}", ""]
    return "\n".join(out)


#: The failing size: 2 pages where the fit measures (spacing 0.9), a 3rd page of skills at the saved spacing 1.0.
FAILING = resume()
#: Longer: 3 pages at every spacing down to the floor with the chips as they are, 2 with them compact.
#: (0.1.11.5: below 1.0 the spacing also tightens the body's lines, so each of these three is longer than it was.)
NEEDS_COMPACT = resume(long_lines=6, short_lines=2)
#: Longer: 2 pages only with the chips compact AND the tightest spacing.
NEEDS_TIGHTEST = resume(long_lines=9, short_lines=3)
#: Longer still: 3 pages whatever is done.
TOO_LONG = resume(long_lines=12, short_lines=3)
#: The master: nine roles, 4 pages. ``FAILING`` is its first five roles, line for line.
LONG = resume(roles=9)


def _pages(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def _invoke(fx: PipelineFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    """The synthetic gig with the failing-size resume stored for ``JOB``, and the operator's layout: spacing 1.0, auto fit off."""

    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=FAILING)
    master = tmp_path / "master.md"
    master.write_text(LONG, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=master, gig_id=fx.gig.resolved.gig_id).status == "created"
    picked = tmp_path / "picked.md"
    picked.write_text(FAILING, encoding="utf-8")
    _invoke(fx, "resume", "store", "--in", str(picked), "--job-url", JOB, "--as", "agent")
    save_display(fx.home_root, DisplaySettings(titles={fx.profile_id: TITLE}, spacing_scale=1.0, auto_fit=False))
    return fx


def _store_over_the_limit(fx: PipelineFixture) -> None:
    """The whole master for ``JOB``, cut to two pages by ``--fit`` and then put back by the user: over the limit, by choice."""

    _invoke(fx, "resume", "store", "--in", str(fx.home_root.parent / "master.md"), "--job-url", JOB, "--as", "agent", "--fit")
    _invoke(fx, "resume", "length", "--job-url", JOB, "--restore")


def _stored(fx: PipelineFixture):
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)[0]


def test_the_fixture_is_the_failing_size(fx: PipelineFixture) -> None:
    """What each side measured: the fit says 2 pages, the render at the saved spacing (no page limit) makes 3."""

    assert measure_markdown(FAILING, spacing_scale=FIT_SCALE)[0] == LENGTH_RULE.max_pages == 2
    header = form_header(FORM, TITLE)
    unfitted = _render(_body(_stored(fx).result), header, company="Acme", timestamp=STAMP, spacing_scale=1.0, auto_fit=False, count_pages=True)
    pages = _pages(unfitted.pdf)
    assert unfitted.pages == len(pages) == 3
    assert _is_the_four_line_header(pages[0]), "the header is not four lines"
    # ... and the 3rd page holds nothing but the end of the Skills chips.
    assert pages[2].strip().startswith(LAST_SKILLS[0]) and pages[2].strip().endswith(LAST_SKILLS[1]) and "Role" not in pages[2] and len(pages[2].splitlines()) <= 3


def test_cli_pdf_of_the_stored_job_resume_stays_on_two_pages(fx: PipelineFixture, tmp_path: Path) -> None:
    out = tmp_path / "out" / "cli.pdf"
    payload = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--out", str(out))
    pages = _pages(out.read_bytes())
    assert len(pages) == payload["pages"] == 2, f"the CLI's PDF is {len(pages)} pages at spacing {payload['spacing_scale']}"
    assert FIT_FLOOR <= payload["spacing_scale"] < 1.0 and payload["note"] is None
    assert LAST_SKILLS[1] in pages[1] and "Role 0 line 0" in pages[0], "the resume is not whole"

    # A spacing the user names is still theirs: one that fits is used as named, also below the floor.
    for named in (0.7, 0.85):
        again = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--spacing", str(named), "--out", str(out))
        assert again["spacing_scale"] == named and again["pages"] == 2 and again["note"] is None


def test_api_pdf_with_a_four_line_header_stays_on_two_pages(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=60)
    try:
        key = {"profile_id": fx.profile_id, "job_identity": JOB}
        response = client.post("/api/tailored-resumes/pdf", json={**key, "header": FORM})
        assert response.status_code == 200, response.text
        pages = _pages(response.content)
        assert _is_the_four_line_header(pages[0]), "the header is not four lines"
        assert len(pages) == 2, f"Generate PDF made {len(pages)} pages; the last holds: {pages[-1][:80]!r}"
        assert "x-gigai-fit-note" not in response.headers
        assert LAST_SKILLS[1] in pages[1]
        # The headerless PDF (an agent's) is on the same pages.
        headerless = client.post("/api/tailored-resumes/pdf", json=key)
        assert headerless.status_code == 200 and len(_pages(headerless.content)) == 2 and "x-gigai-fit-note" not in headerless.headers

        # A resume that cannot fit: the PDF still comes back, with one plain sentence.
        _store_over_the_limit(fx)
        over = client.post("/api/tailored-resumes/pdf", json={**key, "header": FORM})
        assert over.status_code == 200 and over.content.startswith(b"%PDF")
        count = len(_pages(over.content))
        assert count > 2 and over.headers["x-gigai-fit-note"] == over_limit_note(count, 2)
    finally:
        client.close()
        server.shutdown()
        server.server_close()


def test_a_resume_that_cannot_fit_says_so_in_plain_words(fx: PipelineFixture, tmp_path: Path) -> None:
    _store_over_the_limit(fx)
    out = tmp_path / "long.pdf"
    payload = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--out", str(out))
    count = len(_pages(out.read_bytes()))
    assert count == payload["pages"] == 4 and payload["spacing_scale"] == 1.0, "a resume no spacing fits renders as saved"
    note = payload["note"]
    assert note == (
        "This resume takes 4 pages: it does not fit on 2 pages even with the tightest spacing. "
        "To get 2 pages, remove a point or two on the job's page, then generate the PDF again. Or keep it at 4 pages."
    )
    # 0.1.11.5: "Shorten automatically" is retired; the sentence names what replaced it (Remove a point on the job's page).
    assert "--shorten" not in note and "Shorten automatically" not in note and "remove a few lines" not in note
    assert note.isascii() and "\n" not in note
    for internal in ("max_pages", "spacing_scale", "auto_fit", "over_page_limit", "LengthFit", "scout.pick", "Typst", "_"):
        assert internal not in note, internal
    # The text output carries the same sentence.
    result = CliRunner().invoke(scout_group, ["resume", "pdf", "--job-url", JOB, "--out", str(out), "--home", str(fx.home_root), "--target", str(fx.target)])
    assert result.exit_code == 0 and note in result.output


def test_skills_go_compact_before_a_page_holds_only_chips_and_a_fitting_resume_is_untouched(fx: PipelineFixture) -> None:
    stored = _stored(fx)
    header = form_header(FORM, TITLE)

    def render(markdown: str, scale: float, max_pages: int | None):
        from gigai.scout.resume_pdf import parse_resume_markdown

        return _render(parse_resume_markdown(markdown)[1], header, company="Acme", timestamp=STAMP, spacing_scale=scale, auto_fit=False, count_pages=True, max_pages=max_pages)

    # No spacing down to the floor fits this one with the chips as they are ...
    assert [render(NEEDS_COMPACT, scale, None).pages for scale in (1.0, 0.9, FIT_FLOOR)] == [3, 3, 3]
    fitted = render(NEEDS_COMPACT, 1.0, 2)
    pages = _pages(fitted.pdf)
    # ... so the Skills are laid out compactly: 2 pages, every skill still printed, in order, and no note.
    assert fitted.pages == len(pages) == 2 and fitted.note is None and fitted.spacing_scale == 0.85
    printed = pages[1][pages[1].index("SKILLS"):].replace("\n", " ")
    assert [part.strip() for part in printed.removeprefix("SKILLS").split("·")] == [skill.strip() for skill in SKILLS.split(",")]
    # A longer one takes the tightest spacing too; a longer one still is rendered as saved, with the sentence.
    tightest = render(NEEDS_TIGHTEST, 1.0, 2)
    assert (tightest.pages, tightest.spacing_scale, tightest.note) == (2, SPACING_MIN, None) and len(_pages(tightest.pdf)) == 2
    over = render(TOO_LONG, 1.0, 2)
    assert (over.pages, over.spacing_scale, over.note) == (3, 1.0, over_limit_note(3, 2)) and over.pdf == render(TOO_LONG, 1.0, None).pdf

    # A resume that fits at the saved spacing is rendered exactly as it was (the same bytes as with no limit).
    small = replace(stored, result=replace(stored.result, sections=tuple(
        replace(section, entries=section.entries[:2]) if section.heading == "experience" else section for section in stored.result.sections
    )))
    for scale in (1.0, 1.25, 0.75):
        save_display(fx.home_root, DisplaySettings(spacing_scale=scale, auto_fit=False))
        kept = stored_resume_pdf(small, home_root=fx.home_root, count_pages=True)[0]
        plain = _render(_body(small.result), None, company="Acme", timestamp=datetime.fromisoformat(small.updated_at.replace("Z", "+00:00")), spacing_scale=scale, auto_fit=False)
        assert kept.spacing_scale == scale and kept.note is None and kept.pdf == plain.pdf


def test_every_size_the_fit_accepts_is_on_its_pages_with_the_four_line_header_at_any_saved_spacing() -> None:
    """The fit measures at 0.9 with a two-line header's block; the render absorbs a looser saved spacing and the taller header."""

    from gigai.scout.resume_pdf import parse_resume_markdown

    header = form_header(FORM, TITLE)
    accepted = 0
    for long_lines in range(3):
        for short_lines in range(5):
            markdown = resume(long_lines, short_lines)
            if measure_markdown(markdown, spacing_scale=FIT_SCALE)[0] > 2:
                continue
            accepted += 1
            for saved in (1.0, 1.4):
                rendered = _render(parse_resume_markdown(markdown)[1], header, company="Acme", timestamp=STAMP, spacing_scale=saved, auto_fit=False, max_pages=2)
                assert (len(_pages(rendered.pdf)), rendered.note) == (2, None), f"{long_lines} long, {short_lines} short lines, saved spacing {saved}: {rendered.pages} pages at {rendered.spacing_scale}"
                assert rendered.spacing_scale >= FIT_FLOOR, "a size the fit accepts needed the last resort"
    assert accepted >= 5, "the sizes no longer reach the page limit"
