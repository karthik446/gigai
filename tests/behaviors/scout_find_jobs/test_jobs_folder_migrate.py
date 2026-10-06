"""0.1.11.4 J2: ``gigai scout jobs-folder migrate`` imports the flat resumes folder into the jobs layout, once.

The synthetic home of ``test_assessment_v9_flow`` (one profile, one posting, a scripted model) gives one REAL stored
job resume; further jobs are copies of that stored record with another title / posting address.  The home is a
temporary one, so the old folder is ``<home>/resumes`` and the jobs folder ``<home>/jobs``: nothing here can reach
the real Documents folder.  The flat files are written by the resumes folder's own ``save_markdown`` (what GigAI did
before 0.1.11.4), under names that DISAGREE with the stored job, so a pass proves the names come from the record.

On the END outcome, through the real CLI:

- a dry run lists every planned copy and writes nothing (the home and both folders byte-identical);
- a run copies each file to ``<company>/<role>/resume.md`` named from the stored job record, never the file name;
- two roles at one company are two folders; a second posting with the same title gets the short-id suffix;
- a flat file the user edited is copied as the user's own (GigAI never replaces it), ``resume-2.md`` when
  ``resume.md`` is taken; two files of one job fold into one folder by the job key;
- a file with no stored job is listed, not moved; a file with contact details is skipped and listed;
- ``master.md`` and PDFs are not migrated; the old folder is byte-identical after a run and gets no note file;
- a second run is a no-op (``already migrated: N``), and ``status`` / ``jobs-folder`` offer the import until then.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import jobs_folder, jobs_folder_migrate, resumes_folder
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import tailored_resume_path

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _JOB, PYTHON_LINE, _assess, _resume_path, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

DAY = date(2026, 10, 6)
_ROLE_DIR = "harborlight/staff-ai-engineer"
_EMAIL = "zora.quillfeather@zq.example.invalid"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


@pytest.fixture
def old(fx: PostingsFixture) -> PostingsFixture:
    """A home as 0.1.11.3 left it: one stored job resume, a master.md, and no jobs folder yet."""

    _assess(fx, _v9_answer(fx))
    assert _resume_path(fx).is_file()
    _remove_tree(fx.home_root / "jobs")
    (fx.home_root / "scout" / "jobs-folder-index.json").unlink()
    assert (fx.home_root / "resumes" / "master.md").is_file()
    return fx


def _remove_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    root.rmdir()


def _cli(fx: PostingsFixture, *args: str):
    return CliRunner().invoke(scout_group, ["jobs-folder", *args, "--home", str(fx.home_root)])


def _json(fx: PostingsFixture, *args: str) -> dict:
    result = _cli(fx, *args, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _text(fx: PostingsFixture, *args: str) -> str:
    result = _cli(fx, *args)
    assert result.exit_code == 0, result.output
    return result.output


def _key(fx: PostingsFixture, stored: Path | None = None) -> str:
    return resumes_folder.job_key(fx.home_root, stored or _resume_path(fx))


def _markdown(fx: PostingsFixture) -> str:
    return json.loads(_resume_path(fx).read_text(encoding="utf-8"))["markdown"]


def _another_job(fx: PostingsFixture, number: int, *, title: str, company: str = "harborlight") -> str:
    """A second stored job resume (the first one's record with another posting address, title and company); its job key."""

    record = json.loads(_resume_path(fx).read_text(encoding="utf-8"))
    identity = f"https://jobs.lever.co/{company}/{company}-{number:05d}"
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, identity)
    record["job"].update({"job_identity": identity, "normalized_url": identity, "source_url": identity, "title": title, "company": company})
    record["stored_path"], record["markdown_path"] = str(path), str(path.with_suffix(".md"))
    path.write_text(json.dumps(record), encoding="utf-8")
    return _key(fx, path)


def _flat(fx: PostingsFixture, key: str, *, company: str, role: str, markdown: str | None = None) -> Path:
    """A flat file as GigAI wrote it before 0.1.11.4, named ``<company>-<role>-<date>.md`` from the words GIVEN here."""

    saved = resumes_folder.save_markdown(fx.home_root, key=key, company=company, role=role, day=DAY, markdown=markdown or _markdown(fx))
    return saved.path


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every file's bytes and every folder under ``root``."""

    return {path.relative_to(root).as_posix(): (None if path.is_dir() else path.read_bytes()) for path in sorted(root.rglob("*"))}


def _jobs_tree(fx: PostingsFixture) -> list[str]:
    root = fx.home_root / "jobs"
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) if root.is_dir() else []


def test_a_dry_run_lists_every_planned_copy_and_writes_nothing(old: PostingsFixture) -> None:
    fx = old
    flat = _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    growth = _flat(fx, _another_job(fx, 2, title="Senior Engineer (Growth)"), company="Elsewhere", role="Painter", markdown="## Summary\n\n- Growth work.\n")
    assert (flat.name, growth.name) == ("wrongco-janitor-2026-10-06.md", "elsewhere-painter-2026-10-06.md")
    _json(fx)  # one read first: a command's own scratch files are not the dry run's
    before = _snapshot(fx.home_root)

    report = _json(fx, "migrate", "--dry-run")
    plain = _text(fx, "migrate", "--dry-run")

    assert _snapshot(fx.home_root) == before, "a dry run writes nothing: no folder, no index, no record"
    assert not (fx.home_root / "jobs").exists()
    assert report["dry_run"] is True
    assert report["counts"] == {"planned": 2, "migrated": 0, "already": 0, "skipped_orphan": 0, "skipped_contact": 0, "skipped_other": 0}
    assert [(move["source"], move["to"]) for move in report["moves"]] == [
        ("wrongco-janitor-2026-10-06.md", f"{_ROLE_DIR}/resume.md"),
        ("elsewhere-painter-2026-10-06.md", "harborlight/senior-engineer-growth/resume.md"),
    ]
    assert plain.splitlines()[0] == "Dry run: nothing was written."
    assert f"  wrongco-janitor-2026-10-06.md -> {_ROLE_DIR}/resume.md" in plain
    assert "  elsewhere-painter-2026-10-06.md -> harborlight/senior-engineer-growth/resume.md" in plain
    assert "Copy them: gigai scout jobs-folder migrate" in plain


def test_a_run_copies_each_file_under_the_stored_jobs_company_and_role_and_leaves_the_old_folder_as_it_was(old: PostingsFixture) -> None:
    fx = old
    flat = _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    folder = fx.home_root / "resumes"
    (folder / "wrongco-janitor-2026-10-06.pdf").write_bytes(b"%PDF-1.7 synthetic")
    resumes_folder.save_pdf(fx.home_root, name="harborlight-staff-ai-engineer-2026-10-06.pdf", pdf=b"%PDF-1.7 headerless", text="Platform work.", key=_key(fx))
    before = _snapshot(folder)

    plain = _text(fx, "migrate")

    assert _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume.md"], "the record's company and role, not the file name's; no master, no PDF"
    copied = fx.home_root / "jobs" / _ROLE_DIR / "resume.md"
    assert copied.read_bytes() == flat.read_bytes() and PYTHON_LINE in copied.read_text(encoding="utf-8")
    assert _snapshot(folder) == before, "the old folder is byte-identical: a copy, not a move, and no note file"
    record = json.loads((copied.parent / ".gigai-job.json").read_text(encoding="utf-8"))
    assert (record["company"], record["role"], record["job_url"], record["job_key"]) == ("Harborlight", "Staff AI Engineer", _JOB, _key(fx))
    assert f"Copied 1 resume from {folder} into {fx.home_root / 'jobs'}:" in plain
    assert f"  wrongco-janitor-2026-10-06.md -> {_ROLE_DIR}/resume.md" in plain
    assert "is a legacy place for job resumes now" in plain and "master.md and the PDFs stay there" in plain
    # It is the job's own folder from now on: the read model finds it, and a later pick rewrites the file GigAI copied.
    assert jobs_folder.job_folder(fx.home_root, _key(fx)).resume == "resume.md"  # type: ignore[union-attr]
    ref = jobs_folder.JobRef(_key(fx), _JOB, "Harborlight", "Staff AI Engineer")
    jobs_folder.save_resume(fx.home_root, job=ref, markdown="## Summary\n\n- A later pick.\n", day=DAY)
    assert _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume.md"]
    assert copied.read_text(encoding="utf-8") == "## Summary\n\n- A later pick.\n"


def test_two_roles_at_one_company_are_two_folders_and_a_second_posting_of_one_title_gets_the_short_id(old: PostingsFixture) -> None:
    fx = old
    growth = _another_job(fx, 2, title="Senior Engineer (Growth)")
    twin = _another_job(fx, 3, title="Staff AI Engineer")
    _flat(fx, _key(fx), company="A", role="One")
    _flat(fx, growth, company="A", role="Two", markdown="## Summary\n\n- Growth work.\n")
    _flat(fx, twin, company="A", role="Three", markdown="## Summary\n\n- Twin posting.\n")

    dry = _json(fx, "migrate", "--dry-run")
    dry_plain = _text(fx, "migrate", "--dry-run")
    report = _json(fx, "migrate")

    first, second = sorted([_key(fx), twin])  # the plain name goes to the first job by key
    suffixed = f"{_ROLE_DIR}-{jobs_folder.short_id(second)}"
    assert sorted(_jobs_tree(fx)) == sorted([
        f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume.md",
        "harborlight/senior-engineer-growth/.gigai-job.json", "harborlight/senior-engineer-growth/resume.md",
        f"{suffixed}/.gigai-job.json", f"{suffixed}/resume.md",
    ])
    assert json.loads((fx.home_root / "jobs" / _ROLE_DIR / ".gigai-job.json").read_text(encoding="utf-8"))["job_key"] == first
    assert json.loads((fx.home_root / "jobs" / suffixed / ".gigai-job.json").read_text(encoding="utf-8"))["job_key"] == second
    assert [(move["to"], move["folder_suffix"]) for move in report["moves"] if move["folder_suffix"]] == [(f"{suffixed}/resume.md", True)]
    assert [move["to"] for move in dry["moves"]] == [move["to"] for move in report["moves"]], "the dry run lists exactly what the run does"
    assert report["counts"]["migrated"] == 3 and dry["counts"]["planned"] == 3
    assert f"-> {suffixed}/resume.md (another job has the plain folder name, so this one ends in the job's short id)" in dry_plain


def test_a_flat_file_the_user_edited_is_copied_as_theirs_and_never_replaced(old: PostingsFixture) -> None:
    fx = old
    flat = _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    flat.write_text(flat.read_text(encoding="utf-8") + "- My own line about tide tables.\n", encoding="utf-8")

    report = _json(fx, "migrate")

    copied = fx.home_root / "jobs" / _ROLE_DIR / "resume.md"
    assert [(move["to"], move["edited"]) for move in report["moves"]] == [(f"{_ROLE_DIR}/resume.md", True)]
    assert copied.read_bytes() == flat.read_bytes(), "the user's bytes, kept as resume.md while that name is free"
    # It is the user's file: GigAI's next resume for the job goes beside it.
    ref = jobs_folder.JobRef(_key(fx), _JOB, "Harborlight", "Staff AI Engineer")
    jobs_folder.save_resume(fx.home_root, job=ref, markdown="## Summary\n\n- A later pick.\n", day=DAY)
    assert "tide tables" in copied.read_text(encoding="utf-8")
    assert _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume-2.md", f"{_ROLE_DIR}/resume.md"]


def test_two_files_of_one_job_fold_into_its_folder_and_the_edited_one_never_overwrites(old: PostingsFixture) -> None:
    fx = old
    first = _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    # A `-2` of the same job: written under another key, then given the first job's key in the index (the old layout's duplicate).
    second = _flat(fx, "placeholder/key", company="Wrongco", role="Janitor", markdown="## Summary\n\n- A second export.\n")
    assert second.name == "wrongco-janitor-2026-10-06-2.md"
    index_path = fx.home_root / "scout" / "resumes-folder-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["files"][second.name]["key"] = _key(fx)
    index_path.write_text(json.dumps(index), encoding="utf-8")
    second.write_text("## Summary\n\n- A second export, edited by me.\n", encoding="utf-8")

    report = _json(fx, "migrate")

    job_dir = fx.home_root / "jobs" / _ROLE_DIR
    assert _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume-2.md", f"{_ROLE_DIR}/resume.md"], "one folder: by the job key"
    assert (job_dir / "resume.md").read_bytes() == first.read_bytes() and (job_dir / "resume-2.md").read_bytes() == second.read_bytes()
    assert [(move["source"], move["file"], move["file_suffix"], move["edited"]) for move in report["moves"]] == [
        (first.name, "resume.md", False, False), (second.name, "resume-2.md", True, True),
    ]


def test_a_file_with_no_stored_job_is_listed_and_not_moved(old: PostingsFixture) -> None:
    fx = old
    gone = _another_job(fx, 9, title="Removed Role")
    _flat(fx, gone, company="Gone", role="Role", markdown="## Summary\n\n- Orphan.\n")
    (fx.home_root / "scout" / f"{gone}.json").unlink()
    (fx.home_root / "resumes" / "my-notes.md").write_text("notes of my own\n", encoding="utf-8")
    _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    _flat(fx, _another_job(fx, 4, title="Binary Role"), company="Not", role="Text", markdown="## Summary\n\n- Soon not text.\n").write_bytes(b"\xff\xfe\x00")

    report = _json(fx, "migrate")
    plain = _text(fx, "migrate")

    assert report["orphans"] == [{"source": "gone-role-2026-10-06.md", "reason": "no_stored_job"}, {"source": "my-notes.md", "reason": "not_in_index"}]
    assert report["other"] == [{"source": "not-text-2026-10-06.md", "reason": "not_text"}]
    assert report["counts"]["skipped_orphan"] == 2 and report["counts"]["skipped_other"] == 1 and report["counts"]["migrated"] == 1
    assert _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume.md"]
    assert "Not moved, no stored job (2): gone-role-2026-10-06.md, my-notes.md" in plain, "listed again on every run"


def test_a_file_with_contact_details_is_skipped_and_listed(old: PostingsFixture) -> None:
    fx = old
    flat = _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    flat.write_text(f"Zora Quillfeather | {_EMAIL}\n\n" + flat.read_text(encoding="utf-8"), encoding="utf-8")

    report = _json(fx, "migrate")
    plain = _text(fx, "migrate")

    assert report["counts"] == {"planned": 0, "migrated": 0, "already": 0, "skipped_orphan": 0, "skipped_contact": 1, "skipped_other": 0}
    assert report["contact"] == [{"source": "wrongco-janitor-2026-10-06.md", "found": ["email"]}]
    assert _jobs_tree(fx) == [] and not (fx.home_root / "jobs").exists(), "nothing with contact details reaches the jobs folder"
    assert "Not moved, contact details in the file (1): wrongco-janitor-2026-10-06.md" in plain
    assert _EMAIL not in plain and _EMAIL not in json.dumps(report), "the report names the file, never its text"
    record = (fx.home_root / "scout" / "jobs-folder-migration.json").read_text(encoding="utf-8")
    assert _EMAIL not in record and "Quillfeather" not in record
    assert "can be moved" not in _text(fx), "a file that will never be moved is not offered again"


def test_a_second_run_is_a_no_op_and_the_import_is_offered_only_until_it_ran(old: PostingsFixture) -> None:
    fx = old
    _flat(fx, _key(fx), company="Wrongco", role="Janitor")
    _flat(fx, _another_job(fx, 2, title="Senior Engineer (Growth)"), company="Elsewhere", role="Painter", markdown="## Summary\n\n- Growth work.\n")
    offer = "2 resumes in your old resumes folder can be moved: gigai scout jobs-folder migrate --dry-run"
    status = CliRunner().invoke(scout_group, ["status", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert offer in status.output.splitlines() and offer in _text(fx).splitlines()
    assert _json(fx)["legacy_pending"] == 2

    assert _json(fx, "migrate")["counts"]["migrated"] == 2
    after = _snapshot(fx.home_root)
    again = _json(fx, "migrate")
    plain = _text(fx, "migrate")

    assert again["counts"] == {"planned": 0, "migrated": 0, "already": 2, "skipped_orphan": 0, "skipped_contact": 0, "skipped_other": 0}
    assert again["moves"] == [] and "already migrated: 2" in plain.splitlines()
    assert _snapshot(fx.home_root) == after, "a second run writes nothing"
    status = CliRunner().invoke(scout_group, ["status", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert "can be moved" not in status.output and "can be moved" not in _text(fx) and _json(fx)["legacy_pending"] == 0
    # Once means once: a copy the user removed from the jobs folder is not brought back.
    (fx.home_root / "jobs" / _ROLE_DIR / "resume.md").unlink()
    assert _json(fx, "migrate")["counts"]["already"] == 2 and not (fx.home_root / "jobs" / _ROLE_DIR / "resume.md").exists()
    mode = (fx.home_root / "scout" / "jobs-folder-migration.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_a_job_whose_folder_already_holds_its_resume_is_not_given_the_flat_copy(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))  # 0.1.11.4: the pick wrote jobs/<company>/<role>/resume.md
    _flat(fx, _key(fx), company="Wrongco", role="Janitor", markdown="## Summary\n\n- An older export.\n")
    resume = fx.home_root / "jobs" / _ROLE_DIR / "resume.md"
    before = resume.read_bytes()

    report = _json(fx, "migrate")

    assert report["counts"]["migrated"] == 0 and report["already"] == [
        {"source": "wrongco-janitor-2026-10-06.md", "to": f"{_ROLE_DIR}/resume.md", "reason": "job_folder_has_resume"},
    ]
    assert resume.read_bytes() == before and _jobs_tree(fx) == [f"{_ROLE_DIR}/.gigai-job.json", f"{_ROLE_DIR}/resume.md"]


def test_dry_run_and_set_are_refused_where_they_do_not_belong(old: PostingsFixture) -> None:
    fx = old
    alone = _cli(fx, "--dry-run", "--json")
    assert alone.exit_code == 1 and json.loads(alone.output)["error"]["code"] == "invalid_value"
    both = _cli(fx, "migrate", "--set", str(fx.home_root.parent / "elsewhere"), "--json")
    assert both.exit_code == 1 and json.loads(both.output)["error"]["code"] == "invalid_value"
    assert not (fx.home_root.parent / "elsewhere").exists()
    assert jobs_folder_migrate.pending_count(fx.home_root) == 0
