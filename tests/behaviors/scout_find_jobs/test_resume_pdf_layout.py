"""0110-012: the rendered PDF's vertical rhythm, read from text positions (pypdf visitor)."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
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


def _pdf(result: TailoredResume, header: PdfHeader = HEADER, **layout: object) -> bytes:
    return render_pdf(result, header, company="Northwind", timestamp=STAMP, **layout)


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
    return [r.strip() for r in runs[[r.strip() for r in runs].index("SKILLS") + 1 :] if r.strip() != "·"]  # "·": the separator printed between chips


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


# --- 0110-017: one spacing unit, the slider, auto fit ----------------------------------------------------

UNIT = 1.71
MARGIN_Y = 50.0
PAGE_H = 792.0
_K = 0.96875 - 0.2412109375  # Inter's ascender - descender (em): the template's line box model


def _box(size: float, lh: float) -> tuple[float, float]:
    """A line box ``lh`` tall around text of ``size``: (above the baseline, below it)."""
    return (lh + _K * size) / 2, (lh - _K * size) / 2


NAME, BODY, SECTION, ORG = _box(16.6, 16.6), _box(9.5, 14.3), _box(7.1, 7.1), _box(10.7, 14.3)


def _index(page: list[tuple[float, float, str, float]], start: int, test: object) -> int:
    return next(i for i in range(start, len(page)) if test(page[i]))  # type: ignore[operator]


def test_every_vertical_gap_is_a_multiple_of_the_spacing_unit() -> None:
    """Baseline to baseline = the upper line's box below its baseline + k units + the lower line's box above."""
    for scale in (0.7, 0.85, 1.0, 1.4):
        page = _lines(_pdf(_result(), spacing_scale=scale, auto_fit=False))[0]
        texts = [t.strip() for _, _, t, _ in page]
        summary, experience = texts.index("SUMMARY"), texts.index("EXPERIENCE")
        org = experience + 1
        first_bullet = _index(page, org, lambda line: line[2].startswith("•"))
        second_bullet = _index(page, first_bullet + 1, lambda line: line[2].startswith("•"))
        next_org = _index(page, second_bullet, lambda line: abs(line[3]) > 10.5)  # the organisation size
        # 0.1.11.5: below 1.0 the BODY's line box tightens in a straight line from 14.3pt down to 11.5pt at 0.7 (an
        # organisation line keeps at least 13pt); the header's lines keep 14.3pt, and at 1.0 and above nothing moved.
        lh = 14.3 if scale >= 1.0 else max(11.5, 14.3 - (14.3 - 11.5) * (1.0 - scale) / 0.3)
        body, entry = _box(9.5, lh), _box(10.7, max(lh, 13.0))
        assert (scale < 1.0) or (body, entry) == (BODY, ORG)
        pairs = (  # (upper line, lower line, upper box, lower box, units)
            (0, 1, NAME, BODY, 6),  # after the name
            (1, 2, BODY, BODY, 2),  # after the title
            (2, summary, BODY, SECTION, 15),  # between sections (the header is the first)
            (summary, summary + 1, SECTION, body, 8),  # after a section title
            (experience - 1, experience, body, SECTION, 15),
            (experience, org, SECTION, entry, 8),
            (org, org + 1, entry, body, 0),  # the role line sits directly under the organisation
            (org + 1, first_bullet, body, body, 2),  # after the role line
            (second_bullet - 1, second_bullet, body, body, 1),  # between bullets
            (next_org - 1, next_org, body, entry, 7),  # between entries
        )
        for upper, lower, a, b, units in pairs:
            gap = page[upper][0] - page[lower][0]
            expected = a[1] + units * UNIT * scale + b[0]
            assert abs(gap - expected) <= 0.1, f"scale {scale}: {texts[upper][:20]!r} -> {texts[lower][:20]!r} is {gap:.2f}pt, expected {units}s = {expected:.2f}pt"


def test_slider_extremes_render_one_or_two_pages_without_overlap() -> None:
    for scale in (0.7, 1.4):
        data = _pdf(_result(), spacing_scale=scale, auto_fit=False)
        pages = _lines(data)
        assert data.startswith(b"%PDF") and 1 <= len(pages) <= 2
        for page in pages:
            ys = [y for y, *_ in page]
            assert all(a - b > 8 for a, b in zip(ys, ys[1:])), f"scale {scale}: two text lines overlap: {ys}"
        lines = _texts(data)
        assert not sorted({l for l in lines if len(l) > 12 and lines.count(l) > 1}), f"scale {scale}: text printed twice"
    assert _pdf(_result(), spacing_scale=0.7, auto_fit=False) != _pdf(_result(), spacing_scale=1.4, auto_fit=False)


def _sized(entries: int, filler: int) -> TailoredResume:
    """The fixture cut to its first ``entries`` roles, with ``filler`` synthetic summary lines (one paragraph)."""
    result = _result()
    lines = tuple(TailoredLine("copy", f"Filler paragraph {i} about scheduling and billing systems.", ()) for i in range(filler))
    sections = []
    for s in result.sections:
        if s.heading == "experience":
            s = replace(s, entries=s.entries[:entries])
        elif s.heading == "summary":
            s = replace(s, lines=s.lines + lines)
        sections.append(s)
    return replace(result, sections=tuple(sections))


def _last_fill(data: bytes) -> tuple[int, float]:
    """(pages, how far down the last page the last baseline sits, 0..1 of the text area)."""
    pages = _lines(data)
    return len(pages), (PAGE_H - MARGIN_Y - pages[-1][-1][0]) / (PAGE_H - 2 * MARGIN_Y)


def test_auto_fit_spreads_a_long_two_page_resume_to_fill_page_two() -> None:
    """(a) ~1.65 pages at 1.0x: auto fit fills page 2 to >= 80% (or everything lands on one page)."""
    result = _sized(3, 21)
    pages, fill = _last_fill(_pdf(result, spacing_scale=1.0, auto_fit=False))
    assert pages == 2 and 0.55 <= fill <= 0.75, f"fixture drifted: {pages} pages, page 2 {fill:.2f} full at 1.0x"
    pages, fill = _last_fill(_pdf(result))
    assert pages == 1 or fill >= 0.8, f"auto fit left page 2 only {fill:.2f} full"


def test_auto_fit_pulls_a_just_over_one_page_resume_onto_one_page() -> None:
    """(b) ~1.06 pages at 1.0x: auto fit tightens the spacing (never below 0.7x, never smaller type) to one page."""
    result = _sized(1, 8)
    assert len(_lines(_pdf(result, spacing_scale=1.0, auto_fit=False))) == 2
    data = _pdf(result)
    assert len(_lines(data)) == 1
    assert {round(size, 1) for page in _lines(data) for *_, size in page} == {round(size, 1) for page in _lines(_pdf(result, auto_fit=False)) for *_, size in page}


def test_auto_fit_range_limit_on_a_one_and_a_half_page_resume() -> None:
    """(c) ~1.5 pages at 1.0x: the loosest spacing (1.4x) is chosen and page 2 fills more than at 1.0x, but the
    range (gaps are ~20% of a page) cannot reach 80% -- the documented limit, never smaller type."""
    result = _sized(2, 22)
    at_one = _last_fill(_pdf(result, spacing_scale=1.0, auto_fit=False))
    auto = _pdf(result)
    assert auto == _pdf(result, spacing_scale=1.4, auto_fit=False)
    pages, fill = _last_fill(auto)
    assert at_one[0] == pages == 2 and fill > at_one[1] + 0.1, (at_one, fill)


def test_auto_fit_is_deterministic_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import typst

    calls: list[str] = []
    for name in ("query", "compile"):
        real = getattr(typst, name)
        monkeypatch.setattr(typst, name, lambda *a, _real=real, _name=name, **k: (calls.append(_name), _real(*a, **k))[1])
    for result in (_result(), _sized(1, 8), _sized(2, 22), _sized(3, 21)):
        calls.clear()
        first = _pdf(result)
        assert len(calls) <= 8 and calls.count("compile") == 1, calls
        assert _pdf(result) == first
    calls.clear()
    _pdf(_result(), spacing_scale=1.2, auto_fit=False)
    assert calls == ["compile"]  # auto fit off: the slider's scale as given, one compile
