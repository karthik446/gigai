"""0110.7 D-P1: the Scout ATS score on synthetic PDFs built here with Typst.

``good`` is Scout's own render (``render_markdown_pdf``); the bad layouts are Typst documents written in this file
(two-column, table, image-only, fancy characters).  A bad layout must score <= 80 and name the rule it failed; a
keyword-thin resume scores lower and lists its missing must-haves.  Nothing leaves the machine; synthetic data only.
"""

from __future__ import annotations

import io
import time
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

import pytest
import typst
from pypdf import PdfReader

from gigai.scout.ats_score import ATS_WORDING, AtsScore, score
from gigai.scout.posting_keywords import PostingKeywords
from gigai.scout.resume_display import ContactItem, PdfHeader
from gigai.scout.resume_pdf import pdf_file_name, render_markdown_pdf

STAMP = datetime(2026, 10, 2, tzinfo=timezone.utc)
HEADER = PdfHeader("Jordan Example", "Senior Platform Engineer", (ContactItem("jordan@example.invalid", "mailto:jordan@example.invalid"), ContactItem("555-0100", None)))
FILE_NAME = pdf_file_name("Jordan Example", "Example Corp", date(2026, 10, 2))
KEYWORDS = PostingKeywords(
    must=("Kubernetes", "Terraform", "AWS", "CI/CD", "SOC 2"),
    nice=("Go", "Python", "Prometheus", "Datadog", "ArgoCD", "Helm"),
    title=("platform", "engineer", "senior"),
)
RESUME = """# Jordan Example

## Summary
Platform engineer with 8 years building efficient, offline-first delivery workflows on AWS and GCP.
Led reliability work certified against SOC 2 and ISO 27001 controls.

## Experience
### Northwind Logistics
Senior Platform Engineer | Jun 2022 - Present
- Cut CI time 60% by configuring Bazel remote caching and GitHub Actions runners on Kubernetes (EKS).
- Built Terraform modules for 40+ services; drove the migration from Jenkins to GitHub Actions.
- Defined SLOs with Prometheus and Grafana; on-call for a fleet of 300 nodes.

### Contoso Health
Site Reliability Engineer | Mar 2019 - May 2022
- Ran PostgreSQL and Kafka clusters; automated failover with Python and Go.
- Shipped a HIPAA audit-log pipeline (Fluent Bit, S3, Athena) with zero-downtime rollouts.

### Fabrikam Cafe
Software Engineer | Aug 2016 - Feb 2019
- Wrote Django services and Celery workers; introduced Docker for local development.

## Skills
Python · Go · Terraform · Kubernetes · AWS · GCP · Docker · Prometheus · Grafana · PostgreSQL · Kafka · GitHub Actions · Bazel

## Education
### State University
B.S. Computer Science | 2012 - 2016
"""
THIN = (
    RESUME.replace("Terraform modules", "Infrastructure modules").replace(" · Terraform", "").replace(" · AWS", "")
    .replace("on AWS and GCP", "on GCP").replace("SOC 2 and ISO 27001", "internal").replace("(EKS)", "")
)
FONTS = str(resources.files("gigai.scout").joinpath("data", "resume"))
PAGE = '#set page(paper: "us-letter", margin: 40pt)\n#set text(font: "Inter", size: 9pt)\n'
EXPERIENCE = [
    ("Northwind Logistics", "Senior Platform Engineer", "Jun 2022 - Present", "Built Terraform modules and ran Kubernetes on AWS with Prometheus."),
    ("Contoso Health", "Site Reliability Engineer", "Mar 2019 - May 2022", "Ran PostgreSQL and Kafka with Python and Go."),
]


def _typst(source: str, root: Path | None = None) -> bytes:
    return typst.compile(source.encode(), format="pdf", font_paths=[FONTS], ignore_system_fonts=True, **({"root": str(root)} if root else {}))


def _good(markdown: str = RESUME) -> bytes:
    return render_markdown_pdf(markdown, HEADER, timestamp=STAMP, company="Example Corp").pdf


def _two_column() -> bytes:
    side = "== Contact\njordan\\@example.invalid\n== Skills\n" + "\n".join(f"- {t}" for t in ("Python", "Go", "Terraform", "Kubernetes", "AWS"))
    main = "= Jordan Example\n== Summary\nPlatform engineer with 8 years building delivery workflows.\n== Experience\n" + "\n".join(
        f"*{c}*\\\n{r} #h(1fr) {d}\\\n- {b}\n" for c, r, d, b in EXPERIENCE)
    return _typst(PAGE + f"#grid(columns: (32%, 1fr), gutter: 18pt, [\n{side}\n], [\n{main}\n])\n")


def _table() -> bytes:
    rows = "\n".join(f"[{c}], [{r}], [{d}], [{b}]," for c, r, d, b in EXPERIENCE)
    return _typst(PAGE + "= Jordan Example\njordan\\@example.invalid | 555-0100\n\n== Summary\nPlatform engineer with 8 years building delivery workflows.\n"
                  "== Experience\n#table(columns: (16%, 18%, 13%, 1fr), [*Company*], [*Role*], [*Dates*], [*Highlights*],\n" + rows + ")\n== Skills\n#table(columns: 3, [Python], [Go], [Terraform])\n")


def _fancy() -> bytes:
    body = " ".join(f"{b}" for *_rest, b in EXPERIENCE) * 3
    return _typst(PAGE + "#text(size: 18pt, tracking: 6pt)[J O R D A N  E X A M P L E]\n\n✉ jordan\\@example.invalid ☎ 555-0100\n\n"
                  f"== About Me\n{body}\n== Where I've Been\n{body}\n== Toolbox\nPython ★ Go ★ Terraform\n== Schooling\nState University\n")


def _image_only(tmp_path: Path) -> bytes:
    (tmp_path / "page.png").write_bytes(typst.compile((PAGE + "= Jordan Example\n== Experience\n" + EXPERIENCE[0][3]).encode(), format="png", font_paths=[FONTS], ignore_system_fonts=True, ppi=80))
    return _typst('#set page(paper: "us-letter", margin: 0pt)\n#image("page.png", width: 100%)\n', root=tmp_path)


def _score(pdf: bytes, markdown: str = RESUME, keywords: PostingKeywords = KEYWORDS, file_name: str | None = FILE_NAME) -> AtsScore:
    return score(pdf, markdown, keywords, file_name=file_name)


def _failed(result: AtsScore) -> list[str]:
    return result.breakdown["format"]["failed"]  # type: ignore[index,return-value]


def test_scouts_own_render_scores_high_and_parses_cleanly() -> None:
    result = _score(_good())
    assert result.score >= 90, result.line
    assert _failed(result) == [] and result.line.startswith(f"Scout ATS {result.score}: parses cleanly · ")
    assert "key skills" in result.line and "missing: " in result.line
    assert result.fidelity >= 36 and result.format == 20
    assert result.breakdown["wording"] == ATS_WORDING and "’s own local check" in ATS_WORDING and ATS_WORDING.endswith("score.")


@pytest.mark.parametrize(
    ("name", "rule"),
    [("two_column", "single column"), ("table", "no tables"), ("fancy", "plain characters")],
)
def test_bad_layouts_score_at_most_80_and_name_the_failed_rule(name: str, rule: str) -> None:
    result = _score({"two_column": _two_column, "table": _table, "fancy": _fancy}[name]())
    assert result.score <= 80, (name, result.line)
    assert rule in _failed(result), (name, _failed(result))
    assert rule in result.line, result.line
    assert result.score < _score(_good()).score


def test_image_only_pdf_has_no_text_layer(tmp_path: Path) -> None:
    result = _score(_image_only(tmp_path))
    assert result.score <= 10 and "text layer" in _failed(result) and "no images" in _failed(result)
    assert result.line.startswith(f"Scout ATS {result.score}: does not parse: no text layer"), result.line
    assert result.coverage == 0.0


def test_keyword_thin_resume_scores_lower_and_lists_missing_must_haves() -> None:
    good, thin = _score(_good()), _score(_good(THIN), THIN)
    assert thin.score < good.score and thin.coverage < good.coverage
    assert thin.breakdown["format"] == good.breakdown["format"]  # same layout: only the keywords moved
    for must in ("Terraform", "AWS", "SOC 2"):
        assert must in thin.breakdown["coverage"]["missing_must"], thin.breakdown["coverage"]  # type: ignore[index]
        assert must in thin.line, thin.line
    assert good.breakdown["coverage"]["missing_must"] == []  # type: ignore[index]


def test_keywords_match_the_extracted_text_with_aliases() -> None:
    kw = PostingKeywords(must=("Kubernetes", "Go"), nice=("CI/CD",))
    result = _score(_good(), keywords=kw)
    assert result.breakdown["coverage"]["key_skills"] == "3/3", result.breakdown["coverage"]  # type: ignore[index]
    assert "missing" not in result.line
    assert _score(_good(), keywords=PostingKeywords(must=("Rust",))).breakdown["coverage"]["missing_must"] == ["Rust"]  # type: ignore[index]


def test_wrong_file_name_fails_only_the_file_name_rule() -> None:
    assert _failed(_score(_good(), file_name="Resume FINAL (2).pdf")) == ["file name"]
    assert _failed(_score(_good(), file_name=None)) == []


def test_skill_chips_extract_with_a_visible_separator() -> None:
    """The template prints a middle dot between chips, so a reader gets ``Python · Go · Terraform`` not ``Python Go Terraform``."""
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(_good())).pages)
    skills = text.split("SKILLS")[1].split("EDUCATION")[0]
    assert "Python · Go · Terraform · Kubernetes · AWS" in " ".join(skills.split()), skills


def test_a_damaged_pdf_is_scored_unreadable_not_raised() -> None:
    result = _score(b"%PDF-1.7 not really")
    assert result.score == 0 and "unreadable" in result.line and "text layer" in _failed(result)


def test_one_page_score_is_fast() -> None:
    pdf = _good()
    _score(pdf)  # warm imports
    # In-process CPU time, not wall clock, so a loaded CI runner cannot flip it (0.1.10.8: a shared ubuntu shard read
    # 113 ms against the old 100 ms wall-clock ceiling). The intent is "cheap, never pathological": the ceiling is generous.
    start = time.process_time()
    runs = 10
    for _ in range(runs):
        _score(pdf)
    per_run = (time.process_time() - start) / runs
    assert per_run < 0.400, f"{per_run * 1000:.1f} ms of CPU per one-page score"


def test_scoring_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    pdf = _good()
    assert _score(pdf).score >= 90


def test_a_tailored_resume_is_a_valid_source() -> None:
    import json

    from gigai.scout.resume_pdf import render_pdf
    from gigai.scout.tailored_resume import TailoredResume

    fixture = Path(__file__).parent / "fixtures" / "resume_pdf_result.json"
    result = TailoredResume.from_json(json.loads(fixture.read_text()))
    pdf = render_pdf(result, PdfHeader("Riley Example", "Clinical Applications Manager", (ContactItem("riley@example.invalid", None),)), company="Example Corp", timestamp=STAMP)
    scored = score(pdf if isinstance(pdf, bytes) else pdf.pdf, result, PostingKeywords())
    assert scored.score >= 85 and _failed(scored) == [], scored.line
    assert "key skills" not in scored.line  # no posting keywords: nothing to count


# --- 0.1.10.7 ATS2: Scout's own render reads the same whatever its headings hold; a lost point is always named ---

ONE_WORD = RESUME.replace("Northwind Logistics", "Northwind").replace("Contoso Health", "Contoso").replace("Fabrikam Cafe", "Fabrikam").replace("State University", "Statefield")


def _headerless(markdown: str) -> bytes:
    return render_markdown_pdf(markdown, None, timestamp=STAMP, company="Example Corp").pdf


@pytest.mark.parametrize("markdown", [RESUME, ONE_WORD], ids=["spaced_headings", "one_word_headings"])
def test_a_headerless_render_scores_like_a_headered_one(markdown: str) -> None:
    """``one_word_headings`` with no header: the heading font's subset has no space glyph, which once made the
    reader split every tracked heading into letters (``S U M M A RY``) and report parse issues on a clean page."""
    headered, headerless = _score(_good(markdown), markdown), _score(_headerless(markdown), markdown)
    assert _failed(headerless) == _failed(headered) == [], (headerless.line, headered.line)
    assert headerless.score == headered.score >= 90 and headerless.fidelity == headered.fidelity == 40.0, (headerless.line, headered.line)
    assert headerless.breakdown["fidelity"]["lost"] == [] and ": parses cleanly · " in headerless.line  # type: ignore[index]
    text = "\n".join(page.extract_text(space_width=250.0) for page in PdfReader(io.BytesIO(_headerless(markdown))).pages)
    assert {"SUMMARY", "EXPERIENCE", "SKILLS", "EDUCATION"} <= set(text.splitlines()), text


def test_a_heading_font_with_no_space_glyph_is_the_case_under_test() -> None:
    """Guards the regression test itself: the one-word headerless PDF really has a font without a space."""
    fonts = PdfReader(io.BytesIO(_headerless(ONE_WORD))).pages[0]["/Resources"]["/Font"]
    spaces = {str(font.get_object()["/BaseFont"]).split("+")[1]: b"<0020>" in font.get_object()["/ToUnicode"].get_data() for font in fonts.values()}
    assert spaces["Inter-SemiBold"] is False and spaces["Inter-Light"] is True, spaces


def test_chip_separators_are_plain_characters() -> None:
    result = _score(_headerless(RESUME))
    assert "plain characters" not in _failed(result) and " · " in "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(_headerless(RESUME))).pages)


def test_a_source_with_no_role_line_and_no_email_loses_nothing_for_them() -> None:
    markdown = "## Summary\nPlatform engineer with 8 years building delivery workflows on AWS.\n\n## Experience\n### Northwind\n- Built Terraform modules for 40 services over 2019 - 2023.\n\n## Skills\nPython · Go · Terraform\n"
    result = _score(_headerless(markdown), markdown, PostingKeywords())
    detail = result.breakdown["fidelity"]
    assert result.fidelity == 40.0 and detail["lost"] == [], detail  # type: ignore[index]
    assert (detail["email"], detail["role_lines_intact_stream"], detail["role_lines_intact_rows"]) == (True, 1.0, 1.0)  # type: ignore[index]


def test_a_fidelity_loss_is_always_named_in_the_line() -> None:
    pdf = _headerless(RESUME)
    more = RESUME + "\n## Projects\n### Tailwind Traders\nStaff Engineer | Jan 2010 - Dec 2011\n" + "".join(f"- Unseen{i} delivery{i} outcome{i} migration{i}.\n" for i in range(40))
    lost = _score(pdf, more)
    assert _failed(lost) == [] and lost.fidelity < 36, lost.line
    for reason in ("projects heading", "text lost", "dates lost", "role lines split"):
        assert reason in lost.breakdown["fidelity"]["lost"] and reason in lost.line, (reason, lost.line)  # type: ignore[index,operator]
    with_email = _score(pdf, RESUME.replace("# Jordan Example\n", "# Jordan Example\njordan@example.invalid\n"))
    assert with_email.breakdown["fidelity"]["lost"] == ["email lost"] and with_email.fidelity < 40.0  # type: ignore[index]
    assert "parse issues:  " not in lost.line and not lost.line.split(" · ")[0].endswith("parse issues: ")


def test_a_short_resume_has_a_text_layer_and_an_image_of_it_does_not(tmp_path: Path) -> None:
    """Under 200 characters of text and no date range: the page is short, not unreadable."""
    markdown = "## Summary\nPlatform engineer. Python services, Postgres migrations.\n\n## Experience\n### Northwind\n- Built Python services.\n\n## Skills\nPython · Go\n"
    pdf = _headerless(markdown)
    assert len("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).strip()) < 200
    result = _score(pdf, markdown, PostingKeywords())
    assert _failed(result) == [] and result.fidelity == 40.0 and result.score == 100, result.line
    assert result.breakdown["fidelity"]["dates"] == "0/0" and result.line == "Scout ATS 100: parses cleanly"  # type: ignore[index]
    image = _score(_image_only(tmp_path), markdown, PostingKeywords())
    assert "text layer" in _failed(image) and image.score <= 10, image.line
