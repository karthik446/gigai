"""0.3.6 (gigai-3jj.2): a long two-page resume with a full header is two pages again at the default fit.

0.3.5's Standard Resume look (gigai-lc3) set the body taller than 0.3.4 (Regular 400, a constant 4pt bullet gap, a
12.5pt line floor, the gap unit no longer tightened below 1.0): some +200pt at 0.7, so a typical 22-point resume with
an 8-item header (its contact line wraps to two lines) went from 2 pages on 0.3.4 to 3 pages on 0.3.5, and auto fit,
which kept the LOOSEST spacing on the fewest pages any spacing reaches, then printed it at 1.40x on 3 pages.

Pinned here, on synthetic text only (no person's data; invented names on reserved domains):
- the resume below, sized like the audit's 22-point resumes, is 2 pages at the default fit (auto fit, a 2-page limit)
  and at the tightest spacing, with the 8-item header;
- the body at the tightest spacing is 9.5pt type on 0.3.4's 11.5pt line pitch;
- auto fit with a limit no spacing reaches takes the TIGHTEST spacing, and its note names about how many lines to
  leave out (``fit_scale``'s ``max_pages``; ``over_limit_note``);
- ``fit_scale`` itself, on a fake measure: no limit, a reachable limit (also with fewer pages than the limit), an
  unreachable one.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from datetime import datetime, timezone

from pypdf import PdfReader

from gigai.scout.resume_display import SPACING_DEFAULT, SPACING_MIN, form_header
from gigai.scout.resume_pdf import _GRID, _render, fit_scale, over_limit_note, parse_resume_markdown

STAMP = datetime(2026, 11, 24, tzinfo=timezone.utc)

#: The full Generate PDF form: 8 items (the name and seven contact items), invented, on reserved domains.  The contact
#: line (some 160 characters, as long as the audit's 8-item header) is too long for one line, so it wraps to two.
FORM = {
    "name": "Ottoline Brackenridge",
    "email": "otto.b@example.invalid",
    "phone": "+1 (555) 010-0177",
    "location": "Ridgeway Falls, CO",
    "github": "obrack",
    "linkedin": "obrackenridge",
    "website": "obrack.example.invalid",
    "work_authorization": "US citizen, no sponsorship needed",
}

_WORDS = (
    "designed built led migrated the event pipeline for payments and ledger services across three regions with "
    "zero downtime cutting p99 latency from 900 ms to 180 ms and saving 40 percent of compute spend by moving "
    "batch scoring onto Kubernetes autoscaling with Kafka backpressure Terraform modules and a shared observability "
    "stack in OpenTelemetry Prometheus and Grafana mentoring six engineers through design reviews on-call runbooks "
    "and incident retrospectives while partnering with product security and finance on SOC 2 controls"
).split()


def _text(seed: int, length: int) -> str:
    """Engineering prose of about ``length`` characters (whole words), different for every ``seed``."""
    words: list[str] = []
    i = seed * 7
    while len(" ".join(words)) < length:
        words.append(_WORDS[i % len(_WORDS)])
        i += 3
    return " ".join(words).capitalize() + "."


#: The lead role's bullets (characters), as on the audit's 22-point resumes: most wrap to two lines, some to three.
LEAD = (278, 215, 278, 295, 256, 283, 198, 301, 301, 302, 122, 298, 300, 301, 301)
#: One Skills line of some 990 characters, as on those resumes (it prints on about four lines).
SKILLS = ", ".join((
    "Python", "Go", "TypeScript", "Java", "Rust", "SQL", "Kubernetes", "Docker", "Helm", "Terraform", "Pulumi", "AWS", "GCP", "Azure",
    "Kafka", "Flink", "Spark", "Airflow", "dbt", "Snowflake", "BigQuery", "PostgreSQL", "MySQL", "Redis", "DynamoDB", "Cassandra",
    "Elasticsearch", "ClickHouse", "gRPC", "GraphQL", "REST API design", "FastAPI", "Django", "Node.js", "React", "Next.js",
    "OpenTelemetry", "Prometheus", "Grafana", "Datadog", "PagerDuty", "ArgoCD", "GitHub Actions", "CI/CD", "Bazel", "Linux",
    "Distributed systems", "Event-driven architecture", "Stream processing", "Data modeling", "Capacity planning", "SRE practices",
    "Incident management", "SLOs and error budgets", "Zero-downtime migrations", "Multi-region failover", "Cost optimization",
    "PyTorch", "Hugging Face", "LLM inference", "RAG", "Vector search", "Embeddings", "Evaluation harnesses", "Prompt engineering",
    "Model serving", "Feature stores", "MLOps", "SOC 2", "PCI DSS", "IAM", "OAuth 2.0", "Threat modeling", "Technical strategy",
    "System design reviews", "Mentoring", "Hiring", "Cross-team roadmaps",
))


def resume_markdown(extra: int = 0) -> str:
    """A 22-point resume (the Summary, 20 bullets in four roles and two projects, Skills, two degrees); ``extra``
    more bullets of 300 characters in the lead role make it longer."""
    out = ["## Summary", "", f"- {_text(0, 300)}", "", "## Experience", ""]
    out += ["### Northwind Ledger Systems", "Staff Software Engineer | Jun 2021 - Present", ""]
    out += [f"- {_text(10 + i, n)}" for i, n in enumerate(LEAD)]
    out += [f"- {_text(40 + i, 300)}" for i in range(extra)]
    out += ["", "### Bluefin Analytics", "Senior Software Engineer | Mar 2018 - May 2021", "", f"- {_text(60, 230)}", f"- {_text(61, 179)}", ""]
    out += ["### Quarry Labs", "Software Engineer | Jan 2016 - Feb 2018", "", f"- {_text(62, 107)}", ""]
    out += ["### Earlier experience", "Software Engineer, Tidewater Data | 2013 - 2015", "Software Engineer, Lanternfish Co | 2011 - 2013", ""]
    out += ["## Projects", "", "### Open ledger toolkit", _text(70, 140), "", f"- {_text(71, 284)}", ""]
    out += ["### Inference cost tracker", _text(72, 120), "", f"- {_text(73, 301)}", ""]
    out += ["## Skills", "", f"- {SKILLS}", ""]
    out += ["## Education", "", "### Example State University", "MS Computer Science | 2009 - 2011", "", "### Example City College", "BS Mathematics | 2005 - 2009", ""]
    return "\n".join(out)


def _render_md(markdown: str, *, auto_fit: bool, spacing: float = SPACING_DEFAULT, max_pages: int | None = 2):
    sections = parse_resume_markdown(markdown)[1]
    return _render(sections, form_header(FORM), company="Acme", timestamp=STAMP, spacing_scale=spacing, auto_fit=auto_fit, count_pages=True, max_pages=max_pages)


def _page_texts(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def _body_pitch(pdf: bytes) -> float:
    """The most common baseline-to-baseline distance between two 9.5pt lines on the first two pages (pt)."""
    deltas: Counter[float] = Counter()
    for page in PdfReader(io.BytesIO(pdf)).pages[:2]:
        ys: set[float] = set()

        def visit(text: str, cm: list[float], tm: list[float], _font: object, size: float) -> None:
            if text.strip() and round(size * ((tm[2] * cm[2] + tm[3] * cm[3]) ** 2 + (tm[2] * cm[0] + tm[3] * cm[1]) ** 2) ** 0.5, 1) == 9.5:
                ys.add(round(tm[4] * cm[1] + tm[5] * cm[3] + cm[5], 1))

        page.extract_text(visitor_text=visit)
        body = sorted(ys, reverse=True)
        deltas.update(round(a - b, 1) for a, b in zip(body, body[1:]) if 0 < a - b < 20)
    return deltas.most_common(1)[0][0]


def test_the_fixture_is_a_22_point_resume_whose_eight_item_header_wraps_to_two_contact_lines() -> None:
    markdown = resume_markdown()
    assert sum(1 for line in markdown.splitlines() if line.startswith("- ")) == 22  # the Summary, 20 bullets, Skills
    assert len(LEAD) + 5 == 20
    first = [line.strip() for line in _page_texts(_render_md(markdown, auto_fit=False, spacing=SPACING_MIN, max_pages=None).pdf)[0].splitlines()]
    assert first[0] == FORM["name"].upper()
    # The contact line wraps once: its seven items fill the second and third lines, and the Summary starts on the fourth.
    contact = " ".join(first[1:3])
    assert contact.count(" | ") == 6 and all(FORM[key] in contact for key in ("email", "phone", "location", "work_authorization")), first[:4]
    assert first[3].startswith(_text(0, 300)[:20]), first[:4]


def test_a_long_two_page_resume_with_an_eight_item_header_is_two_pages_at_the_default_fit() -> None:
    """0.3.4: 2 pages; 0.3.5: 3 pages at every spacing, and auto fit chose 1.40x."""
    markdown = resume_markdown()
    rendered = _render_md(markdown, auto_fit=True)
    assert (rendered.pages, len(_page_texts(rendered.pdf)), rendered.note) == (2, 2, None), f"{rendered.pages} pages at {rendered.spacing_scale}"
    tightest = _render_md(markdown, auto_fit=False, spacing=SPACING_MIN, max_pages=None)
    assert tightest.pages == 2, f"{tightest.pages} pages at the tightest spacing"


def test_the_body_at_the_tightest_spacing_is_9_5pt_on_the_0_3_4_line_pitch() -> None:
    """The type never shrinks and the lines are never tighter or looser than 0.3.4's at 0.7: 11.5pt (0.3.5: 12.5pt)."""
    pdf = _render_md(resume_markdown(), auto_fit=False, spacing=SPACING_MIN, max_pages=None).pdf
    assert _body_pitch(pdf) == 11.5


def test_auto_fit_with_an_unreachable_limit_takes_the_tightest_spacing_and_names_the_lines_to_leave_out() -> None:
    """0.3.5: the loosest spacing on the fewest pages (1.40x, 4 pages) and "remove a point or two"."""
    extra = 12
    rendered = _render_md(resume_markdown(extra), auto_fit=True)
    assert rendered.pages == 3 and rendered.spacing_scale == SPACING_MIN, f"{rendered.pages} pages at {rendered.spacing_scale}"
    assert len(_page_texts(rendered.pdf)) == 3
    said = re.search(r"leave out about (\d+) lines? ", rendered.note or "")
    assert said, rendered.note
    lines = int(said.group(1))
    assert rendered.note == over_limit_note(3, 2, lines=lines)
    # An honest "about": leaving out that many of the added 300-character bullets puts it on 2 pages at the tightest
    # spacing, and leaving out half as many does not.
    assert 2 <= lines <= extra, lines
    fits = _render_md(resume_markdown(extra - lines), auto_fit=False, spacing=SPACING_MIN, max_pages=None)
    assert fits.pages == 2, f"leaving out {lines} lines still prints {fits.pages} pages"
    half = _render_md(resume_markdown(extra - lines // 2), auto_fit=False, spacing=SPACING_MIN, max_pages=None)
    assert half.pages == 3, f"leaving out {lines // 2} lines already prints 2 pages: the note asks for too many"


def _fake(pages_at: dict[float, int]):
    """A measure of ``(pages, fill)`` by scale, counting its calls; pages only grow with the scale."""
    calls: list[float] = []

    def measure(scale: float) -> tuple[int, float]:
        calls.append(scale)
        return pages_at[scale], 0.5

    return measure, calls


def _steps(*bounds: tuple[float, int]) -> dict[float, int]:
    """Pages for every grid scale: ``(up_to_scale, pages)`` pairs, tightest first."""
    return {scale: next(pages for top, pages in bounds if scale <= top) for scale in _GRID}


def test_fit_scale_aims_at_the_page_limit() -> None:
    two_up_to_1 = _steps((1.0, 2), (9.9, 3))
    one_up_to_0_8 = _steps((0.8, 1), (9.9, 2))
    never_two = _steps((0.9, 3), (9.9, 4))
    assert _GRID[0] == 1.4 and _GRID[-1] == SPACING_MIN == 0.7  # loosest first

    # No limit: as before, the loosest scale that ends on the fewest pages any scale reaches.
    for pages_at, want in ((two_up_to_1, 1.0), (one_up_to_0_8, 0.8), (never_two, 0.9)):
        measure, calls = _fake(pages_at)
        assert fit_scale(measure) == want and len(calls) <= 6
    # A reachable limit: the loosest scale on the limit's pages ...
    measure, calls = _fake(two_up_to_1)
    assert fit_scale(measure, max_pages=2) == 1.0 and len(calls) <= 6
    # ... and a resume on fewer pages than the limit stays on them (it is not spread onto the limit's last page).
    measure, _ = _fake(one_up_to_0_8)
    assert fit_scale(measure, max_pages=2) == 0.8
    # An unreachable limit: the TIGHTEST scale (0.3.5: the loosest on the fewest pages, 0.9 here), one measurement.
    measure, calls = _fake(never_two)
    assert fit_scale(measure, max_pages=2) == SPACING_MIN and calls == [SPACING_MIN]
