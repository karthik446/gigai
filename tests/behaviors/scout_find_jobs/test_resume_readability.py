"""0.3.5 (gigai-lc3): the resume PDF reads at every spacing, and the page estimate stays an upper bound on it.

Read from the PDF itself (pypdf text positions, fonts and fill colours): every bullet is its own unit (a constant
4pt gap between bullets at every spacing, always more than the leading inside one), the body is Inter Regular in
near-black with a line box of at least 12.5pt, the role line is ``Title | dates`` on one line and the Summary prints
straight under the header with no title.  Synthetic resumes only (``tests/support/resume_spacing_fixture``)."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from gigai.scout.resume_display import SPACING_MAX, SPACING_MIN, ContactItem, PdfHeader
from gigai.scout.resume_pdf import measure_markdown, render_markdown_pdf
from tests.support.resume_spacing_fixture import resume

STAMP = datetime(2026, 11, 22, tzinfo=timezone.utc)
HEADER = PdfHeader("Riley Example", "Staff Software Engineer", (ContactItem("Denver, Colorado", None), ContactItem("riley@example.com", None), ContactItem("github.com/example", "https://github.com/example")))
TOL = 0.1
#: ``resume.typ``: ``bullet-gap``, ``lh-tight``, ``lh-full``, ``ink`` (#1C1C1E as Typst writes it, 8 significant digits).
BULLET_GAP = 4.0
LH = {0.7: 12.5, 0.85: 13.4, 1.0: 14.3, 1.4: 14.3}
INK = b"0.10980392 0.10980392 0.11764706"
#: A four-to-five-line point; ``{n}`` keeps every bullet its own text.
LONG = (
    "- Owned migration {n} of the order ledger from a nightly batch to an event-driven pipeline on Kafka and Postgres: wrote the design and "
    "the rollout plan, paired with the partner teams on their consumers, moved {m} million records a day without a lost one, cut the "
    "reconciliation from hours to minutes while the p95 latency of the read path fell by half, and left the runbook that the team now "
    "uses as its template for the next three systems it moves the same way."
)


def _long(counts: tuple[int, ...]) -> str:
    """A resume of long points: one employer per count, each with that many four-to-five-line bullets."""
    out = ["## Summary", "", "Engineering leader building scalable, resilient distributed applications; leads teams of four to thirty and owns the architecture.", "", "## Experience", ""]
    number = 0
    for i, count in enumerate(counts):
        out += [f"### Employer {i + 1}", f"Senior Engineer {i + 1} | Jan {2010 + 3 * i} - Dec {2012 + 3 * i}", ""]
        for _ in range(count):
            out.append(LONG.format(n=number, m=number + 12))
            number += 1
        out.append("")
    out += ["## Skills", "", "- Python, Go, TypeScript, AWS, GCP, Kubernetes, Postgres, Kafka, Redis, Terraform, Datadog, GitHub Actions, FastAPI, React", "", "## Education", "", "### State University", "B.S. Computer Science | 2006 - 2010", ""]
    return "\n".join(out)


def _pdf(markdown: str, scale: float) -> bytes:
    return render_markdown_pdf(markdown, HEADER, timestamp=STAMP, spacing_scale=scale, auto_fit=False).pdf


def _lines(data: bytes) -> list[list[tuple[float, float, str, str]]]:
    """Per page: text lines top-to-bottom as (y, x, text, font of the first run); one line per baseline."""
    pages = []
    for page in PdfReader(io.BytesIO(data)).pages:
        runs: list[tuple[float, float, str, str]] = []

        def visit(text: str, cm: list[float], tm: list[float], font: object, size: float) -> None:
            if text.strip():
                runs.append((round(tm[5] * cm[3] + cm[5], 2), round(tm[4] * cm[0] + cm[4], 2), text, str(font["/BaseFont"]) if font else ""))  # type: ignore[index]

        page.extract_text(visitor_text=visit)
        by_y: dict[float, list[tuple[float, float, str, str]]] = {}
        for run in runs:
            by_y.setdefault(run[0], []).append(run)
        pages.append([(y, min(r[1] for r in rs), "".join(r[2] for r in sorted(rs, key=lambda r: r[1])), sorted(rs, key=lambda r: r[1])[0][3]) for y, rs in sorted(by_y.items(), reverse=True)])
    return pages

def _bullet_groups(page: list[tuple[float, float, str, str]]) -> list[list[float]]:
    """Baselines of each bullet: a bullet glyph starts a group, the wrapped lines (indented, close below) belong to it;
    any other line (a heading, a title) ends the run with an empty group, so only bullets in a row are neighbours."""
    groups: list[list[float]] = []
    for y, x, text, _font in page:
        if text.startswith("•"):
            groups.append([y])
        elif groups and groups[-1] and x > 70 and groups[-1][-1] - y < 20:
            groups[-1].append(y)
        elif groups and groups[-1]:
            groups.append([])
    return groups


@pytest.mark.parametrize("scale", sorted(LH))
def test_every_bullet_is_its_own_unit_at_every_spacing(scale: float) -> None:
    """The gap between two bullets is 4pt at every spacing (not the gap unit, which at 0.7 was under 1pt), and the
    body's line box is never under 12.5pt: the leading inside a bullet and the gap between bullets both stay readable."""

    within: set[float] = set()
    between: list[float] = []
    for page in _lines(_pdf(resume(12), scale)):
        groups = _bullet_groups(page)
        for group in groups:
            within |= {round(a - b, 1) for a, b in zip(group, group[1:])}
        between += [round(a[-1] - b[0], 1) for a, b in zip(groups, groups[1:]) if a and b]
    assert len(within) == 1, f"the leading inside bullets varies: {within}"
    leading = within.pop()
    assert abs(leading - LH[scale]) <= TOL, f"line box {leading} at {scale}, expected {LH[scale]}"
    assert between, "no two bullets in a row"
    gaps = {round(b - leading, 1) for b in between}
    assert gaps == {BULLET_GAP}, f"bullet gaps {gaps} at {scale}: expected the constant {BULLET_GAP}pt over the leading {leading}"


def test_body_is_regular_near_black_with_the_reference_look() -> None:
    """Inter Regular (not Light) in #1C1C1E; the Summary paragraph straight under the contact line with no SUMMARY
    title; the role line ``Title | dates`` on one line; the employer in SemiBold; a degree's years still at the right."""

    data = _pdf(resume(8), 0.85)
    pages = _lines(data)
    first = pages[0]
    texts = [t for _, _, t, _ in first]
    assert "SUMMARY" not in texts and not any(t.startswith("SUMMARY") for page in pages for _, _, t, _ in page)
    contact = next(i for i, t in enumerate(texts) if t.startswith("Denver, Colorado"))
    assert texts[contact + 1].startswith("Staff engineer with fourteen years"), texts[: contact + 2]
    bullet_fonts = {font for _, _, t, font in first if t.startswith("•")}
    assert bullet_fonts and all(font.endswith("Inter-Regular") for font in bullet_fonts), bullet_fonts
    employer = next((font for _, _, t, font in first if t == "LARKSPUR TABLE SYSTEMS"), "")
    assert employer.endswith("Inter-SemiBold"), employer
    stream = b"".join(page.get_contents().get_data() for page in PdfReader(io.BytesIO(data)).pages)
    assert INK in stream and b"0.19607843 0.2 0.21176471" not in stream, "the body is not #1C1C1E (or 0.3.4's #323336 is still in use)"
    degree = next(((x, t) for page in pages for _, x, t, _ in page if t.startswith("EXAMPLE STATE UNIVERSITY")), None)
    assert degree is not None and degree[1].endswith("2010 - 2012"), degree
    runs: list[tuple[float, str]] = []
    for page in PdfReader(io.BytesIO(data)).pages:
        page.extract_text(visitor_text=lambda text, cm, tm, font, size: runs.append((round(tm[4] * cm[0] + cm[4], 1), text)) if text.strip() else None)
    assert any(x > 400 and t.strip() == "2010 - 2012" for x, t in runs), "a degree's years left the right margin"


def _scales() -> list[float]:
    return [round(SPACING_MIN + 0.05 * i, 2) for i in range(int(round((SPACING_MAX - SPACING_MIN) / 0.05)) + 1)]


def test_estimate_is_an_upper_bound_on_the_printed_pages_at_every_spacing() -> None:
    """The page ESTIMATE (``_estimate``: the full 14.3pt line box, every gap the PDF's own) never counts fewer pages
    than the PDF prints, for short and long points, one to four pages, at every spacing on the fit grid: what a pick
    or the length rule budgets, the PDF keeps."""

    resumes = {f"fixture-{n}": resume(n) for n in (4, 12, 20, 28, 36)}
    resumes["master-20"] = resume(20, master=True)
    resumes["long-12-4-3-2"] = _long((12, 4, 3, 2))
    resumes["long-20-8"] = _long((20, 8))
    over: list[str] = []
    for name, markdown in resumes.items():
        for scale in _scales():
            printed = measure_markdown(markdown, spacing_scale=scale, printed=True)
            estimate = measure_markdown(markdown, spacing_scale=scale)
            if printed[0] > estimate[0] or (printed[0] == estimate[0] and printed[1] > estimate[1] + 1e-9):
                over.append(f"{name} at {scale}: printed {printed}, estimate {estimate}")
    assert not over, "\n".join(over)
    for name in ("fixture-20", "long-12-4-3-2"):
        for scale in (0.7, 0.85, 1.0, 1.4):
            pages = render_markdown_pdf(resumes[name], HEADER, timestamp=STAMP, spacing_scale=scale, auto_fit=False).pages
            assert pages is not None and pages <= measure_markdown(resumes[name], spacing_scale=scale)[0], f"{name} at {scale}: the PDF with a header has {pages} pages"
