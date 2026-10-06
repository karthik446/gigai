"""0110-10-05: a job's resume files have one visible folder, and an edited resume can be stored back for ONE job.

On the END outcome, with the synthetic gig and scripted model of
``tests/support/pipeline_fixtures`` (every model call is counted) and the real
CLI. The home is a temporary one, so the resumes folder is ``<home>/resumes``
(only the default home ``~/.gigai`` uses ``~/Documents/GigAI/resumes``): nothing
here can reach the real Documents folder.

A. The folder: a tailoring puts the job's markdown there as
   ``<company>-<role>-<YYYY-MM-DD>.md`` with no contact data;
   ``gigai scout resume pdf`` without ``--out`` writes its headerless PDF there
   and never into the current directory; markdown whose printed text holds a
   contact detail is refused there; ``gigai scout status`` shows the folder.
B. ``gigai scout resume tailor --in FILE --job-url URL``: the edited markdown is
   that job's tailored resume, marked edited with who wrote it; a number or a
   posting skill neither the resume nor an answer states is refused and nothing
   is stored; the Scout ATS score and the Scout label are made again FROM the
   edited resume with no model tailoring; background tailoring never replaces it.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout import story_bank
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.pipeline import steps, triggers
from gigai.scout.pipeline.runner import PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorResponse, list_tailored_resumes

from tests.support.answers_stories_fixtures import config
from tests.support.pipeline_fixtures import EMAIL, JOB, MARKERS, PipelineFixture, assessment, build_pipeline_fixture, resolved_job

_KEPT = "tailor_kept_user_edits"
_DONE = dict.fromkeys(STEPS, "done")
_TERRAFORM_BULLET = "- Wrote Terraform modules for the clusters for three years."
_TERRAFORM_ANSWER = "Yes, three years of Terraform modules for our clusters."


#: The job's base assessment: the posting asks for Terraform and the resume does not show it.
_BASE = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "Terraform", "class": "askable", "status": "unmet", "resume_evidence": []},
        ],
        "suggestions": [], "questions": [], "not_a_match_reason": None,
    }
)


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, base=False)
    fx.model.assessed = _BASE
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=JOB), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(JOB),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()
    return fx


def _invoke(fx: PipelineFixture, *args: str):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])


def _cli(fx: PipelineFixture, *args: str) -> dict[str, object]:
    result = _invoke(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _refused(fx: PipelineFixture, *args: str) -> dict[str, str]:
    result = _invoke(fx, *args)
    assert result.exit_code == 1, result.output
    return json.loads(result.output)["error"]


def _pipeline_tailors(fx: PipelineFixture) -> TailorResponse:
    """The background pipeline tailors the job: its own resume, its ATS record and its label."""

    triggers.process_now(fx.home_root, fx.target, fx.profile_id, JOB)
    PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain()
    assert _states(fx) == _DONE
    return _stored(fx)


def _states(fx: PipelineFixture) -> dict[str, str]:
    store = PipelineStore(fx.db)
    try:
        return {step.name: step.state for step in store.steps(profile_id=fx.profile_id, job=JOB)}
    finally:
        store.close()


def _stored(fx: PipelineFixture) -> TailorResponse:
    (item,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)
    return item


def _folder(fx: PipelineFixture) -> Path:
    return fx.home_root / "resumes"


def _folder_files(fx: PipelineFixture, suffix: str) -> list[Path]:
    return sorted(path for path in _folder(fx).iterdir() if path.suffix == suffix) if _folder(fx).is_dir() else []


def _edited(markdown: str) -> str:
    """The folder's markdown as an agent edits it: one more Experience bullet, the Other section dropped."""

    lines = markdown.splitlines()
    other = lines.index("## Other")
    skills = lines.index("## Skills")
    return "\n".join([*lines[:skills], _TERRAFORM_BULLET, "", *lines[skills:other]]) + "\n"


def _save_terraform_answer(fx: PipelineFixture) -> None:
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id="tooling:terraform", answer=_TERRAFORM_ANSWER,
        question="Have you used Terraform in production?",
    )


# --- A. the visible folder -------------------------------------------------------------------


def test_a_tailoring_puts_the_jobs_markdown_in_the_resumes_folder_without_contact_data(fx: PipelineFixture) -> None:
    stored = _pipeline_tailors(fx)

    assert _cli(fx, "status")["resumes_folder"]["path"] == str(_folder(fx)), "a temporary home never uses ~/Documents"
    day = datetime.fromisoformat(stored.updated_at.replace("Z", "+00:00")).astimezone().date().isoformat()
    (markdown,) = _folder_files(fx, ".md")
    assert markdown.name == f"acme-staff-ai-engineer-{day}.md", "<company>-<role>-<YYYY-MM-DD>.md"
    text = markdown.read_text(encoding="utf-8")
    assert "## Experience" in text and "### Acme Corp — Senior Engineer (2019–2023)" in text
    assert "<!--" not in text, "the visible file reads as a resume: no source comments"
    assert not any(marker in text for marker in MARKERS), "the folder never holds a name or contact details"
    # The index that says which files are GigAI's own holds names, digests and keys: no resume text.
    index = (fx.home_root / "scout" / "resumes-folder-index.json").read_text(encoding="utf-8")
    assert "Python" not in index and markdown.name in index


def test_resume_pdf_without_out_writes_the_headerless_pdf_into_the_folder_not_the_current_directory(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pipeline_tailors(fx)
    elsewhere = tmp_path / "some-worktree"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    payload = _cli(fx, "resume", "pdf", "--tailored", "--job-url", JOB)

    (pdf,) = _folder_files(fx, ".pdf")
    assert pdf.name.startswith("acme-staff-ai-engineer-") and pdf.read_bytes().startswith(b"%PDF")
    assert list(elsewhere.iterdir()) == [], "nothing lands in the directory the command was run from"
    assert payload["in_resumes_folder"] is True and payload["header"] is False
    assert Path(str(payload["out_path"])).expanduser() == pdf, "the path is printed"
    for marker in MARKERS:
        assert marker.encode() not in pdf.read_bytes()


def test_markdown_with_contact_details_is_never_rendered_into_the_folder(fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "mine.md"
    source.write_text(f"## Summary\n\n- Platform engineer. Reach me at {EMAIL}.\n", encoding="utf-8")

    error = _refused(fx, "resume", "pdf", "--in", str(source))

    assert error["code"] == "contact_data_found" and "--out" in error["message"] and EMAIL not in error["message"]
    assert _folder_files(fx, ".pdf") == []
    # Where the user says, it is theirs to keep.
    chosen = tmp_path / "out" / "mine.pdf"
    payload = _cli(fx, "resume", "pdf", "--in", str(source), "--out", str(chosen))
    assert chosen.read_bytes().startswith(b"%PDF") and payload["in_resumes_folder"] is False
    assert _folder_files(fx, ".pdf") == []
    # A name and contact line ABOVE the first section is not printed, so it does not keep the PDF out of the folder.
    headed = tmp_path / "headed.md"
    headed.write_text(f"# Zora Quillfeather\n{EMAIL}\n\n## Summary\n\n- Platform engineer.\n", encoding="utf-8")
    _cli(fx, "resume", "pdf", "--in", str(headed))
    (pdf,) = _folder_files(fx, ".pdf")
    assert pdf.name.startswith("resume-")


def test_scout_status_shows_the_folder_and_it_can_be_changed(fx: PipelineFixture, tmp_path: Path) -> None:
    status = _cli(fx, "status")
    assert status["resumes_folder"]["path"] == str(_folder(fx)) and status["resumes_folder"]["source"] == "default"
    plain = CliRunner().invoke(scout_group, ["status", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert f"Resumes folder: {status['resumes_folder']['shown']}" in plain.output

    chosen = tmp_path / "my resumes"
    result = CliRunner().invoke(scout_group, ["resume", "folder", "--set", str(chosen), "--home", str(fx.home_root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["source"] == "setting" and chosen.is_dir()
    assert _cli(fx, "status")["resumes_folder"]["path"] == str(chosen)
    # The next tailoring goes to the chosen folder.
    _pipeline_tailors(fx)
    assert [path.suffix for path in chosen.iterdir()] == [".md"] and _folder_files(fx, ".md") == []


# --- B. an edited resume, stored back for one job ----------------------------------------------


def test_an_edited_markdown_becomes_that_jobs_tailored_resume_and_the_checks_run_on_it(fx: PipelineFixture, tmp_path: Path) -> None:
    before = _pipeline_tailors(fx)
    (visible,) = _folder_files(fx, ".md")
    edited = tmp_path / "edited.md"
    edited.write_text(_edited(visible.read_text(encoding="utf-8")), encoding="utf-8")
    stored_bytes = Path(before.stored_path).read_bytes()
    tailorings, assessments = len(fx.model.tailor_prompts), len(fx.model.assess_prompts)

    # Terraform is a skill the posting names; neither the resume nor an answer states it (or "three"): refused, nothing stored.
    error = _refused(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB, "--as", "agent")
    assert error["code"] == "edited_resume_unsupported", error
    assert 'the posting skill "terraform"' in error["message"] and 'the number "three"' in error["message"]
    assert "Wrote Terraform modules" not in error["message"], "a refusal names the line number, never the line's text"
    assert Path(before.stored_path).read_bytes() == stored_bytes

    # The honest fix: the answer that states it. Then the same file is stored.
    _save_terraform_answer(fx)
    payload = _cli(
        fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB, "--as", "agent", "--source", "trimmed and added Terraform, at the user's request",
    )

    after = _stored(fx)
    assert payload["changed"] is True and payload["edited"] == after.to_json()["edited"]
    assert (payload["edited"]["written_by"], payload["edited"]["source"]) == ("agent", "trimmed and added Terraform, at the user's request")
    assert after.job.job_identity == JOB and after.created_at == before.created_at and after.updated_at != before.updated_at
    assert [section.heading for section in after.result.sections] == ["summary", "experience", "skills"], "the Other section was dropped"
    (entry,) = after.result.sections[1].entries
    assert [line.kind for line in entry.heading] == ["copy"], "an entry heading stays a copy of the resume line"
    kept, added = entry.bullets
    assert (kept.kind, [ref.label() for ref in kept.refs]) == ("rewritten", ["R5"]), "an unchanged line keeps its sources"
    assert (added.kind, added.origin, added.refs, added.edited_from) == ("custom", "user", (), None)
    assert added.text == _TERRAFORM_BULLET.removeprefix("- ")
    assert "<!-- edited -->" in after.markdown
    # No model tailored; the one model call is the assessment against the edited resume.
    assert len(fx.model.tailor_prompts) == tailorings and len(fx.model.assess_prompts) == assessments + 1
    assert "Wrote Terraform modules" in fx.model.assess_prompts[-1]

    # The checks were made again FROM the edited resume.
    assert payload["recheck"]["result"] == "enqueued" and _states(fx) == _DONE
    outputs = steps.job_outputs(fx.home_root, fx.target, fx.profile_id, JOB)
    assert outputs["tailored_resume"]["outcome"] == _KEPT
    ats = steps.read_ats(fx.home_root, fx.target, fx.profile_id, JOB)
    assert ats["tailored_sha256"] == digest_imported_bytes(after.markdown.encode("utf-8"))
    assert outputs["label"]["updated_at"] >= after.updated_at
    assert payload["status"]["outputs"]["tailored_resume"]["outcome"] == _KEPT

    # The folder holds the job's ONE markdown: the edited one.
    (visible,) = _folder_files(fx, ".md")
    assert _TERRAFORM_BULLET in visible.read_text(encoding="utf-8") and "## Other" not in visible.read_text(encoding="utf-8")
    assert Path(str(payload["folder_path"])).expanduser() == visible

    # Background tailoring never replaces it: forced through the pipeline again, no model tailors and the file is the same.
    edited_bytes = Path(after.stored_path).read_bytes()
    triggers.process_now(fx.home_root, fx.target, fx.profile_id, JOB, force=True)
    PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain()
    assert Path(after.stored_path).read_bytes() == edited_bytes and len(fx.model.tailor_prompts) == tailorings

    # Attaching the same file again changes nothing and calls no model.
    assessments = len(fx.model.assess_prompts)
    again = _cli(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB, "--as", "agent")
    assert again["changed"] is False and again["recheck"]["result"] == "unchanged"
    assert Path(after.stored_path).read_bytes() == edited_bytes and len(fx.model.assess_prompts) == assessments


@pytest.mark.parametrize(
    ("line", "code", "says"),
    [
        ("- Cut cloud spend by 55% across 12 services.", "edited_resume_unsupported", 'line {number}: the number "55"; line {number}: the number "12"'),
        (f"- Reach me at {EMAIL} for details.", "personal_info_refused", "line {number}"),
        ("- Zora Quillfeather", "personal_info_refused", "line {number}"),
        ("- Rust, Python", "edited_resume_unsupported", 'line {number}: the skill "Rust"'),
    ],
)
def test_an_edited_line_that_cannot_be_traced_or_holds_personal_info_is_refused(
    fx: PipelineFixture, tmp_path: Path, line: str, code: str, says: str
) -> None:
    before = _pipeline_tailors(fx)
    stored_bytes = Path(before.stored_path).read_bytes()
    (visible,) = _folder_files(fx, ".md")
    lines = visible.read_text(encoding="utf-8").splitlines()
    # A Skills item goes in the Skills section, anything else under the role.
    lines.insert(lines.index("## Other") - 1 if line.startswith("- Rust") else lines.index("## Skills") - 1, line)
    edited = tmp_path / "edited.md"
    edited.write_text("\n".join(lines) + "\n", encoding="utf-8")

    error = _refused(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB)

    assert error["code"] == code and says.format(number=lines.index(line) + 1) in error["message"], error
    assert EMAIL not in error["message"] and "Quillfeather" not in error["message"]
    assert Path(before.stored_path).read_bytes() == stored_bytes and "edited" not in _stored(fx).to_json()


def test_an_entry_heading_must_be_a_resume_line(fx: PipelineFixture, tmp_path: Path) -> None:
    before = _pipeline_tailors(fx)
    (visible,) = _folder_files(fx, ".md")
    edited = tmp_path / "edited.md"
    edited.write_text(visible.read_text(encoding="utf-8").replace("Senior Engineer (2019–2023)", "Principal Engineer (2015–2023)"), encoding="utf-8")

    error = _refused(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB)

    assert error["code"] == "resume_markdown_invalid" and "an entry heading" in error["message"]
    assert _stored(fx).updated_at == before.updated_at


def test_a_job_with_no_tailoring_yet_takes_an_edited_resume_written_by_the_operator(fx: PipelineFixture, tmp_path: Path) -> None:
    """No pipeline run before it: the attach is the job's first tailored resume, made of resume lines and one own line."""

    edited = tmp_path / "mine.md"
    edited.write_text(
        "# Zora Quillfeather\n"
        f"{EMAIL}\n\n"
        "## Experience\n\n"
        "### Acme Corp — Senior Engineer (2019–2023)\n\n"
        "- Built Python services for six years; cut p99 latency by 40%.\n"
        "- Ran the Python services on Kubernetes.\n\n"
        "## Skills\n\n"
        "- Python, Kubernetes, PostgreSQL\n",
        encoding="utf-8",
    )

    payload = _cli(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB)

    stored = _stored(fx)
    assert stored.to_json()["edited"]["written_by"] == "operator" and payload["recheck"]["result"] == "enqueued"
    (entry,) = stored.result.sections[0].entries
    assert [line.kind for line in entry.bullets] == ["copy", "custom"]
    assert [line.kind for line in stored.result.sections[1].lines] == ["copy"]
    assert not any(marker in stored.markdown for marker in MARKERS), "lines above the first section are never stored"
    assert len(fx.model.tailor_prompts) == 0 and _states(fx) == _DONE
    assert steps.job_outputs(fx.home_root, fx.target, fx.profile_id, JOB)["tailored_resume"]["outcome"] == _KEPT
