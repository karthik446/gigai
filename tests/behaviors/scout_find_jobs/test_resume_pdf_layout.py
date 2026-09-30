"""0110-012: the rendered PDF's vertical rhythm, read from text positions (pypdf visitor)."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from gigai.scout.resume_display import ContactItem, PdfHeader
from gigai.scout.resume_pdf import render_pdf
from gigai.scout.tailored_resume import TailoredLine, TailoredResume, TailoredSection

FIXTURES = Path(__file__).parent / "fixtures"
STAMP = datetime(2026, 9, 29, tzinfo=timezone.utc)
HEADER = PdfHeader("Riley Example", "Clinical Applications Manager", (ContactItem("Columbus, Ohio", None),))
TOL = 0.05


def _result() -> TailoredResume:
    return TailoredResume.from_json(json.loads((FIXTURES / "resume_pdf_result.json").read_text()))


def _pdf(result: TailoredResume, header: PdfHeader = HEADER) -> bytes:
    return render_pdf(result, header, company="Northwind", timestamp=STAMP)


def _lines(data: bytes) -> list[list[tuple[float, float, str, float]]]:
    """Per page: text lines top-to-bottom as (y, x, text, font size); one line per baseline."""
    pages = []
    for page in PdfReader(io.BytesIO(data)).pages:
        runs: list[tuple[float, float, str, float]] = []

        def visit(text: str, cm: list[float], tm: list[float], font: object, size: float) -> None:
            if text.strip():
                runs.append((round(tm[5] * cm[3] + cm[5], 2), round(tm[4] * cm[0] + cm[4], 2), text, round(size * cm[3], 2)))

        page.extract_text(visitor_text=visit)
        by_y: dict[float, list[tuple[float, float, str, float]]] = {}
        for run in runs:
            by_y.setdefault(run[0], []).append(run)
        pages.append([(y, min(r[1] for r in rs), "".join(r[2] for r in sorted(rs, key=lambda r: r[1])), max(r[3] for r in rs)) for y, rs in sorted(by_y.items(), reverse=True)])
    return pages


def _skills_wrapped() -> TailoredResume:
    """Skills as the source hard-wrapped them (five physical lines), a paragraph summary, a wrapped bullet."""
    result = _result()
    wrapped = ("LLM/GenAI (RAG, evals, agent orchestration) ·", "pgvector/Embeddings · Ruby/Rails/Sinatra ·", "REST API/GraphQL/gRPC · MySQL/Postgres ·", "Docker/Kubernetes/Pulumi · Datadog/OpenTelemetry ·", "Event-driven systems · Kafka/SQS")
    skills = TailoredSection("skills", lines=tuple(TailoredLine("copy", t, ()) for t in wrapped))
    summary = TailoredSection("summary", lines=(TailoredLine("copy", "Staff engineer building distributed systems.\nLeads teams of 4 to 30.", ()),))
    others = tuple(s for s in result.sections if s.heading not in ("skills", "summary"))
    return replace(result, sections=(summary,) + others + (skills,))


def _bullet_groups(page: list[tuple[float, float, str, float]]) -> list[list[float]]:
    """Baselines of each bullet (a bullet glyph line starts a group; wrapped lines share its indent)."""
    groups: list[list[float]] = []
    for y, x, text, size in page:
        if text.startswith("•"):
            groups.append([y])
        elif groups and x > 60 + 10 and size < 10.5 and groups[-1][-1] - y < 20:
            groups[-1].append(y)
    return groups


def test_bullet_rhythm_leading_within_gap_between_and_no_overlap() -> None:
    pages = _lines(_pdf(_result()))
    within: set[float] = set()
    between: list[float] = []
    for page in pages:
        groups = _bullet_groups(page)
        for g in groups:
            within |= {round(a - b, 1) for a, b in zip(g, g[1:])}
        between += [round(a[-1] - b[0], 1) for a, b in zip(groups, groups[1:])]
        ys = [y for y, *_ in page]
        assert all(a - b > 8 for a, b in zip(ys, ys[1:])), f"two text lines overlap: {ys}"
    assert len(within) == 1, f"line-to-line gap inside bullets varies: {within}"
    leading = within.pop()
    assert between and min(between) > leading + TOL, f"bullet gap {min(between)} <= line gap {leading}"


def test_skills_hard_wrapped_source_is_one_paragraph_and_summary_is_not_a_bullet() -> None:
    data = _pdf(_skills_wrapped())
    lines = [t for page in _lines(data) for _, _, t, _ in page]
    text = "\n".join(lines)
    assert "•" not in text.split("SKILLS")[1] and "•" not in text.split("SUMMARY")[1].split("EXPERIENCE")[0]
    skills = [y for page in _lines(data) for y, _, t, _ in page if "Kafka" in t or "Sinatra" in t]
    body = text.split("SKILLS")[1].strip().split("\n")
    assert " ".join(body).count("·") >= 8 and all(not l.startswith("•") for l in body)
    # one paragraph: every skills line sits one leading apart (no block gap between them)
    page = next(p for p in _lines(data) if any("Sinatra" in t for _, _, t, _ in p))
    ys = [y for y, _, t, _ in page[[t for _, _, t, _ in page].index("SKILLS") + 1 :]]
    assert len({round(a - b, 1) for a, b in zip(ys, ys[1:])}) <= 1 and skills


def test_no_heading_is_the_last_line_on_a_page() -> None:
    headings = {"SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS"}
    for result in (_result(), _skills_wrapped()):
        for page in _lines(_pdf(result)):
            last = page[-1][2].strip()
            assert last not in headings, f"page ends on heading {last!r}"


def test_render_is_deterministic() -> None:
    assert _pdf(_skills_wrapped()) == _pdf(_skills_wrapped())


def test_golden_png_renders() -> None:
    """The committed golden is for visual review; regenerate with typst format=png when the template changes."""
    assert (FIXTURES / "resume_pdf_layout_golden.png").stat().st_size > 10_000
