"""0.1.11.5 (a): a job's saved spacing stays when its resume is picked again, and a pick never writes the master.

On the pick fixture of ``test_pick_header_room.py`` (a job assessed against an invented master, its resume picked by
the assessment): the person leaves the job page's slider at 0.8; ``gigai scout resume pick --refresh`` (the page's
"Pick it now" / re-pick) stores a new resume for the job, and the job's spacing is still 0.8: the PDF is rendered at
it. Saving the spacing writes one small file beside the job's resume: the resume and the master's files are byte for
byte what they were.
"""

from __future__ import annotations

from pathlib import Path

from gigai.scout.resume_pdf import job_layout_path, job_spacing, save_job_spacing, stored_resume_pdf
from gigai.scout.tailored_resume import read_tailored_resume, tailored_resume_path

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, _assess, _ok, _pipeline_off, fx  # noqa: F401 - the fixtures
from tests.support.posting_fixtures import PostingsFixture


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_re_pick_keeps_the_jobs_saved_spacing_and_saving_it_never_writes_the_master(fx: PostingsFixture) -> None:  # noqa: F811
    _assess(fx)
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    picked = read_tailored_resume(path)
    assert picked is not None and job_spacing(path) is None and picked.edited is None and picked.selection is not None

    before = _files(fx.home_root)
    assert save_job_spacing(fx.home_root, fx.target, fx.default_profile_id, _JOB, 0.8) == 0.8
    assert read_tailored_resume(path) == picked, "the job's stored resume was written"
    after = _files(fx.home_root)
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    assert changed == {str(job_layout_path(path).relative_to(fx.home_root))}, f"saving a job's spacing wrote more than its own small file: {sorted(changed)}"
    assert any("master" in name for name in before), "the home holds a master"

    # Picked again: a new resume is stored for the job, and the job's spacing is the one that was saved.
    again = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert again["conflicts"] == []
    repicked = read_tailored_resume(path)
    assert repicked is not None and job_spacing(path) == 0.8
    rendered, _name = stored_resume_pdf(repicked, home_root=fx.home_root, count_pages=True)
    assert rendered.spacing_scale == 0.8
    # ... and the master is what it was before the spacing was saved.
    assert {name: data for name, data in _files(fx.home_root).items() if "master" in name} == {name: data for name, data in before.items() if "master" in name}
