"""0.1.11.12 (dossier-B, gigai-hn4): the job's stored view carries the fact guard's flags, computed on the read.

The guard (``tailor_fact_guard.unverified_facts``) reads the stored resume's markdown against the master and the
stored assessment's posting text; the answer is ``fact_checks`` on the view ``gigai scout resume pick`` prints and
``GET /api/jobs/suggestions`` serves when a job is opened (``job_actions.OPEN_KEYS``), and ``jobResumeModel.js``
puts it on the page's record. Nothing is stored: a resume with a flag saves as it is, and the file does not change.

The synthetic home of ``test_assessment_v9_flow`` (one profile, a small invented master, one posting, a scripted
model; the pipeline is off). The UI rule runs under node (LOUD skip without it, as ``tests/ui/test_job_resume_model.py``).
"""

from __future__ import annotations

from dataclasses import replace
import json
import shutil
import subprocess
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import job_actions, tailor_master
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import apply_line_edit, read_tailored_resume, save_tailor_response

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _URL, _assess, _resume_path, _v9_answer, fx  # noqa: F401 - `fx` is the fixture
from tests.support.posting_fixtures import PostingsFixture

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
INVENTED = "Own the Python inference services behind 40 product teams, now in Rust, cutting latency by 73%."


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _cli(fx: PostingsFixture, *args: str, as_json: bool = True) -> dict | str:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), *(["--json"] if as_json else [])])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1]) if as_json else result.output


def _edit_first_bullet(fx: PostingsFixture, text: str) -> None:
    stored = read_tailored_resume(_resume_path(fx))
    assert stored is not None
    line = next(line for section in stored.result.sections for line in section.body_lines())
    save_tailor_response(apply_line_edit(stored, line.id, text), home_root=fx.home_root)


def test_the_view_flags_what_neither_the_master_nor_the_posting_states_and_stores_nothing(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess(fx, _v9_answer(fx))
    # A clean pick (master lines, word for word): nothing to flag, and the key is there.
    assert _cli(fx, "resume", "pick", "--job-url", _URL)["fact_checks"] == []
    # The user's own words on one line: a tool and a number neither source states. The resume saves as it is.
    _edit_first_bullet(fx, INVENTED)
    before = _resume_path(fx).read_bytes()
    view = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert view["fact_checks"] == ["Rust", "73%"] and view["fact_checks_posting"] is True
    assert isinstance(view["resume"], dict) and INVENTED in view["resume"]["markdown"]
    assert _resume_path(fx).read_bytes() == before  # the read computed the flags; it wrote nothing
    # What the page loads when a job is opened carries the same list (the open read, `OPEN_KEYS`).
    opened = job_actions.list_suggestions(fx.home_root, fx.target, _URL, with_view=True)
    assert opened["fact_checks"] == ["Rust", "73%"] and opened["fact_checks_posting"] is True
    # The text output says they are checks to read, not errors, and names each one.
    text = _cli(fx, "resume", "pick", "--job-url", _URL, as_json=False)
    assert "Check (not an error): Rust, 73% -- not in your resume or the posting." in text
    # No master at all: nothing to check against, and the view says so (null, not an empty list); the posting was there.
    monkeypatch.setattr(tailor_master, "stored_master", lambda *_args, **_kwargs: None)
    gone = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert gone["master_stored"] is False and gone["fact_checks"] is None and gone["fact_checks_posting"] is True


def test_a_pasted_job_without_posting_text_is_checked_against_the_master_alone_and_says_so(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix round 1, F4 (b): the pasted posting is never kept (`AssessResponse.posting_text` is None), so the check is
    # master-only and the view says so (`fact_checks_posting: false`); the CLI line does not claim the posting was read.
    from gigai.scout import job_brief

    _assess(fx, _v9_answer(fx))
    _edit_first_bullet(fx, "Own the Python inference services behind 40 product teams on Helm.")
    view = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert view["fact_checks"] == [] and view["fact_checks_posting"] is True  # Helm: the posting's word
    real = job_brief.stored_job

    def without_posting(*args, **kwargs):
        job = real(*args, **kwargs)
        return replace(job, assessment=replace(job.assessment, posting_text=None))

    monkeypatch.setattr(job_brief, "stored_job", without_posting)
    view = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert view["fact_checks"] == ["Helm"] and view["fact_checks_posting"] is False
    text = _cli(fx, "resume", "pick", "--job-url", _URL, as_json=False)
    assert "Check (not an error): Helm -- not in your resume (the pasted posting is not kept, so it was not checked against it)." in text
    assert "or the posting" not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="LOUD skip: node is not installed; the UI rule did not run")
def test_the_page_record_carries_the_flags_as_items_to_read() -> None:
    script = """
const m = await import(__URL__);
const pick = { job_identity: "j", profile_id: "p", stale: [], picked: null, conflicts: [], fact_checks: ["Rust", "73%", 7, null], fact_checks_posting: true };
const out = {
  served: m.factCheckItems(m.suggestionsAnswer(null, pick).record),
  none: m.factCheckItems(m.suggestionsAnswer(null, { ...pick, fact_checks: null }).record),
  absent: m.factCheckItems(m.suggestionsAnswer(null, { ...pick, fact_checks: undefined }).record),
  empty: m.factCheckItems(m.suggestionsAnswer({ job_identity: "j", suggestions: [] }, null).record),
  pasted: m.factCheckItems(m.suggestionsAnswer(null, { ...pick, fact_checks: ["Helm"], fact_checks_posting: false }).record),
  posting: [m.suggestionsAnswer(null, pick).record.fact_checks_posting, m.suggestionsAnswer(null, { ...pick, fact_checks_posting: undefined }).record.fact_checks_posting],
  heading: m.FACT_CHECKS_HEADING,
};
process.stdout.write(JSON.stringify(out));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script.replace("__URL__", json.dumps((UI_SRC / "jobResumeModel.js").as_uri()))], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["served"] == [{"word": "Rust", "text": "Rust — not in your resume or the posting"}, {"word": "73%", "text": "73% — not in your resume or the posting"}]
    assert out["none"] == [] and out["absent"] == [] and out["empty"] == []
    assert out["pasted"] == [{"word": "Helm", "text": "Helm — not in your resume (the pasted posting is not kept, so it was not checked against it)"}]
    assert out["posting"] == [True, False]
    assert out["heading"] == "Checks for you to read, not errors."
