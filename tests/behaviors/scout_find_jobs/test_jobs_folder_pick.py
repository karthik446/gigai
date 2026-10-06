"""0.1.11.4 J1: a picked resume lands in the job's own folder of the jobs folder. On the END outcome.

The synthetic home of ``test_assessment_v9_flow`` (one profile, a small invented master, one posting, a scripted
model), the real CLI, the background pipeline OFF. The home is a temporary one, so the jobs folder is ``<home>/jobs``
and the resumes folder ``<home>/resumes``: nothing here can reach the real Documents folder.

- an assessment's pick writes ``<jobs>/<company>/<role>/resume.md`` (clean markdown) and ``resume pick`` names the
  file and the folder; the flat resumes folder gets no job file any more (``master.md`` stays where it is);
- a re-pick rewrites the same folder; a resume the user handed back is kept by a re-pick (the new selection waits as
  proposed) and ONLY ``--use-proposed`` replaces ``resume.md``;
- a ``resume.md`` the user changed in the folder itself is never replaced: the new pick goes beside it;
- a PDF with the header file's name and contact details goes only to ``--out``: no PDF and no header value ever lands
  in the jobs folder, and ``interview/`` / ``cover-letter.md`` are never created.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import jobs_folder
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _URL, PYTHON_LINE, _assess, _record, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

_JOB_DIR = "harborlight/staff-ai-engineer"
_NAME, _EMAIL, _PHONE = "Zora Quillfeather", "zora.quillfeather@zq.example.invalid", "+1 (555) 014-2999"
_HEADER = {"name": _NAME, "email": _EMAIL, "phone": _PHONE, "location": "Quillshire, ZZ", "linkedin": "zq-invalid"}
_HEADER_MARKERS = (_NAME, _NAME.upper(), "Quillfeather", _EMAIL, _PHONE, "014-2999", "Quillshire", "zq-invalid")


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _ok(fx: PostingsFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _jobs(fx: PostingsFixture) -> Path:
    return fx.home_root / "jobs"


def _job_dir(fx: PostingsFixture) -> Path:
    return _jobs(fx) / _JOB_DIR


def _tree(fx: PostingsFixture) -> list[str]:
    return sorted(path.relative_to(_jobs(fx)).as_posix() for path in _jobs(fx).rglob("*") if path.is_file())


def _flat_job_files(fx: PostingsFixture) -> list[str]:
    """What the flat resumes folder holds besides the master's own files."""

    folder = fx.home_root / "resumes"
    return sorted(path.name for path in folder.iterdir() if not path.name.startswith("master")) if folder.is_dir() else []


def _pick(fx: PostingsFixture, *args: str) -> dict:
    return _ok(fx, "resume", "pick", "--job-url", _URL, *args)


def test_a_pick_writes_resume_md_into_the_jobs_own_folder_and_names_it(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))

    resume = _job_dir(fx) / "resume.md"
    assert _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume.md"], "one folder for the job: no PDF, no cover letter, no interview/"
    text = resume.read_text(encoding="utf-8")
    assert PYTHON_LINE in text and "<!--" not in text, "clean markdown, as a reader sees it"
    assert _flat_job_files(fx) == [], "the flat resumes folder is not written by a pick any more"
    view = _pick(fx)
    shown = view["resume"]["job_folder"]
    assert Path(shown).expanduser() == _job_dir(fx) and view["resume"]["folder_path"] == f"{shown}/resume.md"
    plain = CliRunner().invoke(scout_group, ["resume", "pick", "--job-url", _URL, "--home", str(fx.home_root), "--target", str(fx.target)])
    assert f"  In your jobs folder: {shown}/resume.md" in plain.output
    status = _ok(fx, "status")
    assert status["jobs_folder"]["path"] == str(_jobs(fx)) and status["resumes_folder"]["path"] == str(fx.home_root / "resumes")
    # The brief: the private part names the jobs folder only; the posting part the job's folder and file (the posting's words).
    yours = _ok(fx, "resume", "brief", "--job-url", _URL)
    assert yours["resume"]["folder"] == status["jobs_folder"]["shown"] and "harborlight" not in json.dumps(yours["resume"]["folder"])
    assert yours["commands"]["jobs_folder"] == "gigai scout jobs-folder --json"
    posting = _ok(fx, "resume", "brief", "--job-url", _URL, "--posting")
    assert (posting["job_folder"], posting["resume_file"]) == (_JOB_DIR, f"{_JOB_DIR}/resume.md")

    # A re-pick rewrites the same folder (the pick made in code is its own text).
    record = json.loads((_job_dir(fx) / ".gigai-job.json").read_text(encoding="utf-8"))
    again = _pick(fx, "--refresh")
    assert _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume.md"]
    assert resume.read_text(encoding="utf-8") == jobs_folder.readable_markdown(again["resume"]["markdown"])
    assert json.loads((_job_dir(fx) / ".gigai-job.json").read_text(encoding="utf-8")) == record


def test_a_resume_the_user_handed_back_is_replaced_only_by_use_proposed(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    resume = _job_dir(fx) / "resume.md"
    picked = resume.read_text(encoding="utf-8")
    # The user edits the job's file and hands it back: one bullet less.
    lines = picked.splitlines()
    dropped = next(line for line in reversed(lines) if line.startswith("- ") and PYTHON_LINE not in line)
    mine = "\n".join(line for line in lines if line != dropped) + "\n"
    resume.write_text(mine, encoding="utf-8")
    stored = _ok(fx, "resume", "store", "--in", str(resume), "--job-url", _URL, "--as", "operator")
    assert stored["changed"] is True and Path(stored["folder_path"]).expanduser() == resume and Path(stored["job_folder"]).expanduser() == _job_dir(fx)
    mine = jobs_folder.readable_markdown(stored["markdown"])  # the file now reads as the stored resume does
    assert dropped not in mine and resume.read_text(encoding="utf-8") == mine
    assert _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume.md"], "the file handed back is the job's resume: no second copy beside it"

    # A re-pick never replaces it: the new selection waits as proposed.
    view = _pick(fx, "--refresh")
    assert view["proposed"] is not None and view["resume"]["replaceable"] is False
    assert resume.read_text(encoding="utf-8") == mine and _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume.md"]

    # The one explicit step that does.
    taken = _pick(fx, "--use-proposed")
    assert taken["proposed"] is None and taken["resume"]["edited"] is None
    assert resume.read_text(encoding="utf-8") == jobs_folder.readable_markdown(taken["resume"]["markdown"]) != mine
    assert _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume.md"] and _flat_job_files(fx) == []


def test_a_resume_md_changed_in_the_folder_is_never_replaced_by_a_pick(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))
    resume = _job_dir(fx) / "resume.md"
    picked = resume.read_text(encoding="utf-8")
    resume.write_text(picked + "- A line of my own, not handed back yet.\n", encoding="utf-8")

    view = _pick(fx, "--refresh")

    assert resume.read_text(encoding="utf-8") == picked + "- A line of my own, not handed back yet.\n", "the user's file is left alone"
    beside = _job_dir(fx) / "resume-2.md"
    assert beside.read_text(encoding="utf-8") == jobs_folder.readable_markdown(view["resume"]["markdown"])
    assert Path(view["resume"]["folder_path"]).expanduser() == beside
    assert _tree(fx) == [f"{_JOB_DIR}/.gigai-job.json", f"{_JOB_DIR}/resume-2.md", f"{_JOB_DIR}/resume.md"]


def test_a_headered_pdf_never_lands_in_the_jobs_folder(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    before = {name: (_jobs(fx) / name).read_bytes() for name in _tree(fx)}
    header = tmp_path / "my-header.json"
    header.write_text(json.dumps(_HEADER), encoding="utf-8")
    os.chmod(header, 0o600)
    out = tmp_path / "out" / "apply.pdf"

    made = _ok(fx, "resume", "pdf", "--job-url", _URL, "--out", str(out), "--header", str(header))

    assert made["header"] is True and made["in_resumes_folder"] is False and out.read_bytes().startswith(b"%PDF")
    assert {name: (_jobs(fx) / name).read_bytes() for name in _tree(fx)} == before, "the PDF goes where the user saves it; the jobs folder is unchanged"
    assert not [name for name in _tree(fx) if name.endswith(".pdf")] and not (_job_dir(fx) / "interview").exists()
    # A PDF without a header and without --out still goes to the RESUMES folder, never the jobs folder.
    plain = _ok(fx, "resume", "pdf", "--job-url", _URL, "--no-header")
    assert plain["in_resumes_folder"] is True and Path(plain["out_path"]).expanduser().parent == fx.home_root / "resumes"
    assert {name: (_jobs(fx) / name).read_bytes() for name in _tree(fx)} == before
    # No header value in the jobs folder, in either folder's index, or in the job's record.
    holders = [
        str(path) for path in [*_jobs(fx).rglob("*"), *(fx.home_root / "scout").glob("*folder*.json")]
        if path.is_file() and any(marker.encode() in path.read_bytes() for marker in _HEADER_MARKERS)
    ]
    assert holders == [], holders
    assert _record(fx).selection is not None
