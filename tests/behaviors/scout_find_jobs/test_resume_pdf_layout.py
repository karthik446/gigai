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


def test_skills_hard_wrapped_source_is_tags_and_summary_is_not_a_bullet() -> None:
    data = _pdf(_skills_wrapped())
    text = "\n".join(t for page in _lines(data) for _, _, t, _ in page)
    assert "•" not in text.split("SKILLS")[1] and "•" not in text.split("SUMMARY")[1].split("EXPERIENCE")[0]
    tags = _skill_tags(data)
    assert "Kafka/SQS" in tags and "Ruby/Rails/Sinatra" in tags and "LLM/GenAI (RAG, evals, agent orchestration)" in tags
    assert "·" not in "".join(tags)


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


def _texts(data: bytes) -> list[str]:
    return [t.strip() for page in _lines(data) for _, _, t, _ in page]


def _skill_runs(data: bytes) -> list[str]:
    """Every text run (one per chip) after the SKILLS heading, in reading order."""
    runs: list[str] = []
    for page in PdfReader(io.BytesIO(data)).pages:
        found: list[str] = []
        page.extract_text(visitor_text=lambda text, cm, tm, font, size: found.append(text) if text.strip() else None)
        runs += found
    return [r.strip() for r in runs[[r.strip() for r in runs].index("SKILLS") + 1 :]]


def _skill_tags(data: bytes) -> list[str]:
    return _skill_runs(data)


def test_no_duplicated_text_lines() -> None:
    """0110-015: a hard-wrapped paragraph whose copy lines each cite their continuations prints once."""
    lines = _texts(_pdf(_result()))
    repeated = sorted({l for l in lines if len(l) > 12 and lines.count(l) > 1})
    assert not repeated, f"text lines printed more than once: {repeated}"
    body = " ".join(lines)
    for item in ("Active Directory/Citrix", "Visio/process mapping", "SQL (Oracle, SQL Server)"):
        assert body.count(item) == 1, f"{item!r} printed {body.count(item)} times"


def test_skills_rendered_as_n_unique_tags() -> None:
    tags = _skill_tags(_pdf(_result()))
    assert len(tags) == 11 and len({t.casefold() for t in tags}) == 11, tags
    assert tags[0] == "SQL (Oracle, SQL Server)" and tags[-1] == "Visio/process mapping"  # reading order kept


def _filled(count: int, dropped: int = 0, long_entry: bool = False) -> TailoredResume:
    """The fixture with ``count`` extra one-line summary paragraphs and the last ``dropped`` experience bullets
    removed, to slide the page break through the entries and to size the overflow."""
    result = _result()
    filler = tuple(TailoredLine("copy", f"Filler paragraph {i} about scheduling and billing systems.", ()) for i in range(count))
    sections = []
    for s in result.sections:
        if s.heading == "summary":
            s = replace(s, lines=s.lines + filler)
        elif s.heading == "experience" and long_entry:  # one long role (10 bullets) so a page break can fall inside it
            first = s.entries[0]
            s = replace(s, entries=(replace(first, bullets=first.bullets * 2),) + s.entries[1:])
        elif s.heading == "experience" and dropped:
            entries = list(s.entries)
            left = dropped
            for index in range(len(entries) - 1, -1, -1):
                cut = min(left, max(len(entries[index].bullets) - 1, 0))
                if cut:
                    entries[index] = replace(entries[index], bullets=entries[index].bullets[:-cut])
                    left -= cut
            s = replace(s, entries=tuple(entries))
        sections.append(s)
    return replace(result, sections=tuple(sections))


def _kinds(page: list[tuple[float, float, str, float]]) -> list[str]:
    return ["bullet" if t.startswith("•") else "wrap" if x > 75 and size < 10.5 else "other" for _, x, t, size in page]


def test_no_entry_split_leaves_one_bullet_alone() -> None:
    splits = 0
    for count in range(0, 40):
        pages = _lines(_pdf(_filled(count, long_entry=True)))
        for before, after in zip(pages, pages[1:]):
            kinds_after = _kinds(after)
            if not kinds_after or kinds_after[0] not in ("bullet", "wrap"):
                continue  # the page break falls between entries
            splits += 1
            head = 0
            for kind in kinds_after:
                if kind == "other":
                    break
                head += kind == "bullet"
            tail = 0
            for kind in reversed(_kinds(before)):
                if kind == "other":
                    break
                tail += kind == "bullet"
            assert head >= 2 and tail >= 2, f"count={count}: an entry splits {tail} | {head} bullets across the page break"
    assert splits, "no fixture size split an entry across pages"


def test_one_page_when_it_overflows_by_a_small_amount() -> None:
    """Rule: a small overflow is tightened onto one page; a real second page carries real content."""
    saw_one = saw_two = False
    for dropped in range(0, 12):
        pages = _lines(_pdf(_filled(0, dropped)))
        saw_one |= len(pages) == 1
        if len(pages) > 1:
            saw_two = True
            assert len(pages[1]) >= 6, f"dropped={dropped}: page 2 holds only {len(pages[1])} lines (a small overflow should fit page 1)"
    assert saw_one and saw_two
