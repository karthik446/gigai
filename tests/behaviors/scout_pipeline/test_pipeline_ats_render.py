"""0.1.10.7 ATS2 on the END outcome: the Scout ATS record of Scout's OWN render, through the pipeline step.

A realistic synthetic resume (Summary, two roles with bullets, Skills chips, Education) goes through
``gigai scout pipeline process``; what is asserted is the stored ATS record a user reads on the job page.
Scout's own PDF must score high and name no failed rule, with or without a header, whatever letters its
headings happen to hold: a heading font whose subset has no space glyph (one-word company names, no name
header) once read as ``S U M M A RY`` and reported "parse issues" on a clean page.  Synthetic data only.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from click.testing import CliRunner
from pypdf import PdfReader
import pytest

from gigai.scout import ats_score
from gigai.scout.pipeline import steps
from gigai.scout.posting_keywords import extract_keywords, skills_from_markdown
from gigai.scout.resume_pdf import stored_resume_pdf
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import list_tailored_resumes

from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

POSTING = (
    "Acme is hiring a Staff Platform Engineer to own its delivery platform. "
    "Requirements: 5+ years of Python in production; Kubernetes; Terraform; AWS. "
    "Nice to have: Prometheus, Go, PostgreSQL. Remote within the United States."
)
#: The resume's lines as the tailoring cites them: every non-blank line, numbered from 1.
_LINES = (
    "# Zora Quillfeather",
    "zora.quillfeather@zq.example.invalid | +1 (555) 014-2999 | Denver, CO",
    "## Summary",
    "Platform engineer with 8 years building efficient, offline-first delivery workflows on AWS and GCP.",
    "## Experience",
    "### {first}",
    "Senior Platform Engineer | Jun 2022 - Present",
    "- Cut CI time 60% by configuring Bazel remote caching and GitHub Actions runners on Kubernetes (EKS).",
    "- Built Terraform modules for 40+ services; drove the migration from Jenkins to GitHub Actions.",
    "- Defined SLOs with Prometheus and Grafana; on-call for a fleet of 300 nodes.",
    "### {second}",
    "Site Reliability Engineer | Mar 2019 - May 2022",
    "- Ran PostgreSQL and Kafka clusters; automated failover with Python and Go.",
    "- Shipped an audit-log pipeline (Fluent Bit, S3, Athena) with zero-downtime rollouts.",
    "## Skills",
    "Python · Go · Terraform · Kubernetes · AWS · GCP · Docker · Prometheus · Grafana · PostgreSQL · Kafka",
    "## Education",
    "### {school}",
    "B.S. Computer Science | 2012 - 2016",
)
#: ``one_word``: no heading holds a space, so the heading font's subset has no space glyph (the defect's trigger).
NAMES = {
    "spaced": {"first": "Northwind Logistics", "second": "Contoso Health", "school": "State University"},
    "one_word": {"first": "Northwind", "second": "Contoso", "school": "Statefield"},
}
_REASON = {"kind": "summary", "requirement": None, "posting_phrase": "delivery platform"}
TAILORED: dict[str, object] = {
    "sections": [
        {
            "heading": "summary",
            "lines": [{
                "text": "Platform engineer with 8 years building efficient delivery workflows on AWS and GCP.",
                "refs": [{"kind": "resume", "line": 4}], "reason": _REASON,
            }],
        },
        {
            "heading": "experience",
            "entries": [
                {"heading_ref": [{"copy": 6}, {"copy": 7}], "bullets": [{"copy": 8}, {"copy": 9}, {"copy": 10}]},
                {"heading_ref": [{"copy": 11}, {"copy": 12}], "bullets": [{"copy": 13}, {"copy": 14}]},
            ],
        },
        {"heading": "skills", "lines": [{"copy": 16}]},
        {"heading": "education", "entries": [{"heading_ref": [{"copy": 18}, {"copy": 19}], "bullets": []}]},
    ],
}
FORM = {"name": "Jordan Example", "email": "jordan@example.invalid"}


def _resume(names: str) -> str:
    return "\n".join(_LINES).format(**NAMES[names]) + "\n"


def _processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: str, tailored: dict[str, object] | None = TAILORED) -> PipelineFixture:
    monkeypatch.setenv("GIGAI_SCOUT_PIPELINE", "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=_resume(names), posting=POSTING)
    if tailored is not None:
        fx.model.tailored = tailored
    result = CliRunner().invoke(scout_group, fx.cli("process", JOB))
    assert result.exit_code == 0, result.output
    assert {step["name"]: step["outcome"] for step in json.loads(result.output)["drain"]["steps"]}["ats"] == "done", result.output
    return fx


def _ats(fx: PipelineFixture) -> dict[str, object]:
    record = steps.read_ats(fx.home_root, fx.target, fx.profile_id, JOB)
    assert record is not None
    return record["result"]  # type: ignore[return-value]


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("names", ["spaced", "one_word"])
def test_the_pipeline_scores_scouts_own_realistic_render_at_least_90_with_no_failed_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: str,
) -> None:
    result = _ats(_processed(tmp_path, monkeypatch, names))

    assert result["score"] >= 90, result["line"]  # type: ignore[operator]
    assert result["breakdown"]["format"]["failed"] == [], result["line"]  # type: ignore[index]
    assert result["line"].startswith(f"Scout ATS {result['score']}: parses cleanly · "), result["line"]  # type: ignore[union-attr]
    fidelity = result["breakdown"]["fidelity"]  # type: ignore[index]
    assert (fidelity["sections"], fidelity["sections_missing"], fidelity["dates"]) == ("4/4", [], "3/3"), fidelity
    assert result["fidelity"] == 40.0 and result["format"] == 20.0, result


@pytest.mark.parametrize("names", ["spaced", "one_word"])
def test_a_headerless_pdf_scores_like_a_headered_one_and_chips_are_plain_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: str,
) -> None:
    fx = _processed(tmp_path, monkeypatch, names)
    (tailored,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)
    keywords = extract_keywords(POSTING, title="Staff AI Engineer", skills=skills_from_markdown(tailored.markdown))

    scored = {}
    for kind, form in (("headerless", None), ("headered", FORM)):
        rendered, file_name = stored_resume_pdf(tailored, home_root=fx.home_root, form=form)
        scored[kind] = ats_score.score(rendered.pdf, tailored.result, keywords, file_name=file_name)
        text = _text(rendered.pdf)
        # The template's chip separator (U+00B7) is in the text a reader gets and is a plain character.
        assert "Python · Go · Terraform" in " ".join(text.split()), text
        assert ("Jordan Example".upper() in text) is (form is not None)
        assert scored[kind].breakdown["format"]["failed"] == [], (kind, scored[kind].line)  # type: ignore[index]

    assert scored["headerless"].score == scored["headered"].score >= 90, {k: v.line for k, v in scored.items()}
    assert scored["headerless"].fidelity == scored["headered"].fidelity == 40.0
    assert scored["headerless"].to_json() == _ats(fx)  # the step scored this very render


def test_a_short_tailoring_with_no_role_line_loses_no_fidelity_and_never_reports_an_unnamed_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixtures' own short tailoring: one entry whose heading has no "Title | dates" line, and no header."""

    monkeypatch.setenv("GIGAI_SCOUT_PIPELINE", "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch)
    assert CliRunner().invoke(scout_group, fx.cli("process", JOB)).exit_code == 0
    result = _ats(fx)

    assert result["fidelity"] == 40.0 and result["breakdown"]["format"]["failed"] == [], result  # type: ignore[index]
    assert ": parses cleanly · " in result["line"] and "parse issues" not in result["line"], result["line"]  # type: ignore[operator]
