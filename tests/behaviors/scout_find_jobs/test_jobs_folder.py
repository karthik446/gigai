"""0.1.11.4 J1: the jobs folder -- one folder per application (``gigai.scout.jobs_folder``).

The module's own rules, on synthetic data in temporary folders (``Path.home`` is a temporary
one, so nothing here can reach the real Documents folder):

* where: ``~/Documents/GigAI/jobs`` for the default home only, a setting for any other place
  (the resumes folder's rule), shown and set by ``gigai scout jobs-folder``;
* names: ``<company>/<role>/resume.md``, lowercase ASCII with hyphens, capped, never a date; two
  roles at one company are two folders; a collision appends the job's short id; the same job
  always maps to the same folder (the index, and the folder's own ``.gigai-job.json``);
* the user's file: an edited ``resume.md`` is never replaced (the new one goes beside it);
* never contact data, never a PDF, and ``interview/`` / ``cover-letter.md`` are never created.
"""

from __future__ import annotations

import ast
from datetime import date
import json
from pathlib import Path
import re
import stat

from click.testing import CliRunner
import pytest

from gigai.scout import jobs_folder
from gigai.scout.jobs_folder import JobRef, JobsFolderError
from gigai.scout.scout_cli import scout_group

DAY = date(2026, 10, 6)
LATER = date(2026, 10, 9)
MARKDOWN = "## Summary\n\n- Platform engineer with nine years of Python. <!-- R3 -->\n"
READABLE = "## Summary\n\n- Platform engineer with nine years of Python.\n"
NEWER = "## Summary\n\n- Platform engineer with nine years of Python and Go. <!-- R3 -->\n"
NEWER_READABLE = "## Summary\n\n- Platform engineer with nine years of Python and Go.\n"

FULLSTACK = JobRef("project_x/resumes/profile_y/aaa", "https://boards.example.invalid/thrive/jobs/1", "Thrive Market", "Staff Software Engineer, Fullstack")
GROWTH = JobRef("project_x/resumes/profile_y/bbb", "https://boards.example.invalid/thrive/jobs/2", "Thrive Market", "Senior Engineer (Growth)")
#: Another posting of the same company with the same title as FULLSTACK.
TWIN = JobRef("project_x/resumes/profile_y/ccc", "https://boards.example.invalid/thrive/jobs/3", "Thrive Market", "Staff Software Engineer, Fullstack")


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def home_root(tmp_path: Path, user_home: Path) -> Path:
    root = tmp_path / "gigai-home"
    root.mkdir()
    return root


def _save(home_root: Path, job: JobRef = FULLSTACK, *, markdown: str = MARKDOWN, day: date = DAY):
    return jobs_folder.save_resume(home_root, job=job, markdown=markdown, day=day)


def _tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


# --- where, and the setting -------------------------------------------------------------------


def test_only_the_default_home_uses_documents(user_home: Path, tmp_path: Path) -> None:
    assert jobs_folder.default_folder(user_home / ".gigai") == user_home / "Documents" / "GigAI" / "jobs"
    scratch = tmp_path / "scratch-home"
    assert jobs_folder.default_folder(scratch) == scratch / "jobs"
    folder = jobs_folder.jobs_folder(user_home / ".gigai")
    assert (folder.source, folder.shown) == ("default", "~/Documents/GigAI/jobs")
    assert folder.to_json()["exists"] is False, "reading where the folder is never creates it"
    assert not (user_home / "Documents").exists()


def test_the_folder_is_a_setting_by_the_resumes_folders_rule(home_root: Path, user_home: Path, tmp_path: Path) -> None:
    chosen = jobs_folder.set_jobs_folder(home_root, "~/Applications")
    assert chosen.path == user_home / "Applications" and chosen.path.is_dir() and chosen.source == "setting"
    setting = home_root / "scout" / "jobs-folder.json"
    assert stat.S_IMODE(setting.stat().st_mode) == 0o600 and stat.S_IMODE(setting.parent.stat().st_mode) == 0o700
    assert json.loads(setting.read_text(encoding="utf-8"))["schema_version"] == "scout-jobs-folder:1"
    assert jobs_folder.jobs_folder(home_root).to_json() == {
        "schema_version": "scout-jobs-folder-response:1", "path": str(user_home / "Applications"), "shown": "~/Applications",
        "source": "setting", "default": jobs_folder.jobs_folder(home_root).to_json()["default"], "exists": True,
    }
    # The resumes folder's own setting is another file: one never moves the other.
    assert not (home_root / "scout" / "resumes-folder.json").exists()

    for bad, code in (("relative/jobs", "invalid_value"), (str(home_root / "scout" / "inside"), "invalid_value"), (7, "wrong_type"), ("a\nb", "invalid_value")):
        with pytest.raises(JobsFolderError) as refused:
            jobs_folder.set_jobs_folder(home_root, bad)
        assert refused.value.code == code
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(JobsFolderError) as refused:
        jobs_folder.set_jobs_folder(home_root, str(a_file))
    assert refused.value.code == "invalid_value"
    assert jobs_folder.jobs_folder(home_root).path == user_home / "Applications", "a refused value changes nothing"

    back = jobs_folder.set_jobs_folder(home_root, "")
    assert back.source == "default" and back.path == home_root / "jobs" and not setting.exists()


def test_the_command_shows_and_sets_the_folder(home_root: Path, user_home: Path, tmp_path: Path) -> None:
    def run(*args: str):
        return CliRunner().invoke(scout_group, ["jobs-folder", *args, "--home", str(home_root)])

    shown = run("--json")
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == {
        "ok": True, "schema_version": "scout-jobs-folder-response:1", "path": str(home_root / "jobs"), "shown": json.loads(shown.output)["shown"],
        "source": "default", "default": json.loads(shown.output)["default"], "exists": False,
    }
    assert not (home_root / "jobs").exists() and not (home_root / "scout").exists(), "showing the folder writes nothing"
    plain = run()
    assert plain.exit_code == 0 and plain.output.strip() == f"Jobs folder: {json.loads(shown.output)['shown']} (the default)"

    chosen = tmp_path / "my jobs"
    changed = run("--set", str(chosen), "--json")
    assert changed.exit_code == 0, changed.output
    assert json.loads(changed.output)["source"] == "setting" and json.loads(changed.output)["path"] == str(chosen) and chosen.is_dir()
    assert run().output.strip() == f"Jobs folder: {json.loads(changed.output)['shown']}"
    # The next resume goes to the chosen folder.
    assert _save(home_root).path == chosen / "thrive-market" / "staff-software-engineer-fullstack" / "resume.md"

    both = run("--set", str(chosen), "--reset", "--json")
    assert both.exit_code == 1 and json.loads(both.output)["error"]["code"] == "invalid_value"
    relative = run("--set", "relative/place", "--json")
    assert relative.exit_code == 1 and json.loads(relative.output)["error"]["code"] == "invalid_value"
    reset = run("--reset", "--json")
    assert reset.exit_code == 0 and json.loads(reset.output)["source"] == "default"


# --- names ------------------------------------------------------------------------------------


def test_a_resume_is_resume_md_in_the_jobs_own_folder_and_reads_as_a_resume(home_root: Path) -> None:
    saved = _save(home_root)

    root = home_root / "jobs"
    assert saved.path == root / "thrive-market" / "staff-software-engineer-fullstack" / "resume.md" and saved.written is True
    assert saved.path.read_text(encoding="utf-8") == READABLE, "clean markdown: no per-line source comments"
    assert _tree(root) == [
        "thrive-market", "thrive-market/staff-software-engineer-fullstack",
        "thrive-market/staff-software-engineer-fullstack/.gigai-job.json", "thrive-market/staff-software-engineer-fullstack/resume.md",
    ], "nothing else: no PDF, no cover-letter.md, and interview/ is never created empty"
    record = json.loads((saved.folder / ".gigai-job.json").read_text(encoding="utf-8"))
    assert record == {
        "schema_version": "scout-jobs-folder-job:1", "job_key": FULLSTACK.key, "job_url": FULLSTACK.job_identity,
        "identity_sha256": record["identity_sha256"], "company": "Thrive Market", "role": "Staff Software Engineer, Fullstack", "created": "2026-10-06",
    }
    assert record["identity_sha256"].startswith("sha256:")
    assert not re.search(r"\d{4}-\d{2}-\d{2}", saved.path.relative_to(root).as_posix()), "the date lives in the record, never in a name"
    folder = jobs_folder.job_folder(home_root, FULLSTACK.key)
    assert folder is not None and folder.path == saved.folder and folder.relative == "thrive-market/staff-software-engineer-fullstack"
    assert folder.resume == "resume.md" and folder.to_json()["files"] == {"resume": "resume.md"}
    assert jobs_folder.job_folder(home_root, GROWTH.key) is None


def test_two_roles_at_one_company_get_two_folders(home_root: Path) -> None:
    first, second = _save(home_root, FULLSTACK), _save(home_root, GROWTH)

    company = home_root / "jobs" / "thrive-market"
    assert first.folder == company / "staff-software-engineer-fullstack" and second.folder == company / "senior-engineer-growth"
    assert sorted(path.name for path in company.iterdir()) == ["senior-engineer-growth", "staff-software-engineer-fullstack"]
    assert first.path.is_file() and second.path.is_file()


def test_a_collision_gets_the_short_id_and_the_same_job_keeps_its_folder(home_root: Path) -> None:
    first = _save(home_root, FULLSTACK)
    twin = _save(home_root, TWIN)

    suffix = jobs_folder.short_id(TWIN.key)
    assert re.fullmatch(r"[0-9a-f]{8}", suffix)
    assert first.folder.name == "staff-software-engineer-fullstack", "the first posting keeps the plain name"
    assert twin.folder == first.folder.parent / f"staff-software-engineer-fullstack-{suffix}"
    assert json.loads((twin.folder / ".gigai-job.json").read_text(encoding="utf-8"))["job_key"] == TWIN.key

    # A re-pick of either job rewrites its own folder: decided once.
    again, twin_again = _save(home_root, FULLSTACK, markdown=NEWER, day=LATER), _save(home_root, TWIN, markdown=NEWER, day=LATER)
    assert (again.path, twin_again.path) == (first.path, twin.path) and again.written and twin_again.written
    assert first.path.read_text(encoding="utf-8") == NEWER_READABLE == twin.path.read_text(encoding="utf-8")
    assert sorted(path.name for path in first.folder.parent.iterdir()) == sorted([first.folder.name, twin.folder.name])
    assert json.loads((first.folder / ".gigai-job.json").read_text(encoding="utf-8"))["created"] == "2026-10-06", "the day it was made, not the day of a re-pick"

    # The index lost (or the first job gone from it): each folder's own record still says whose it is.
    (home_root / "scout" / "jobs-folder-index.json").unlink()
    assert _save(home_root, TWIN, markdown=NEWER).path == twin.path and _save(home_root, FULLSTACK, markdown=NEWER).path == first.path
    assert sorted(path.name for path in first.folder.parent.iterdir()) == sorted([first.folder.name, twin.folder.name])


def test_a_folder_that_is_not_this_jobs_is_never_used(home_root: Path) -> None:
    mine = home_root / "jobs" / "thrive-market" / "staff-software-engineer-fullstack"
    mine.mkdir(parents=True)
    (mine / "resume.md").write_text("my own notes\n", encoding="utf-8")

    saved = _save(home_root)

    assert saved.folder.name == f"staff-software-engineer-fullstack-{jobs_folder.short_id(FULLSTACK.key)}"
    assert (mine / "resume.md").read_text(encoding="utf-8") == "my own notes\n" and sorted(path.name for path in mine.iterdir()) == ["resume.md"]


def test_slugs_are_lowercase_ascii_capped_and_never_empty(home_root: Path) -> None:
    assert jobs_folder.company_slug("Thrive Market") == "thrive-market"
    assert jobs_folder.company_slug("Hims & Hers") == "hims-hers" and jobs_folder.company_slug("Société Générale") == "societe-generale"
    assert jobs_folder.role_slug("Sr. Engineer — Growth / Platform (Remote, US)") == "sr-engineer-growth-platform-remote-us"
    long = jobs_folder.role_slug("Staff Engineer " * 20)
    assert len(long) <= 60 and not long.endswith("-") and len(jobs_folder.company_slug("x" * 200)) == 40
    assert (jobs_folder.company_slug("日本語"), jobs_folder.role_slug(""), jobs_folder.company_slug("../..")) == ("company", "role", "company")

    saved = _save(home_root, JobRef("k/1", "https://boards.example.invalid/j/9", "", "///"))
    assert saved.path == home_root / "jobs" / "company" / "role" / "resume.md"
    escaping = _save(home_root, JobRef("k/2", "https://boards.example.invalid/j/10", "../../outside", "..\\up"))
    assert escaping.path == home_root / "jobs" / "outside" / "up" / "resume.md", "a name never leaves the jobs folder"


# --- the user's file --------------------------------------------------------------------------


def test_an_edited_resume_md_is_never_replaced(home_root: Path) -> None:
    first = _save(home_root)
    first.path.write_text(READABLE + "- My own line.\n", encoding="utf-8")

    second = _save(home_root, markdown=NEWER, day=LATER)

    assert first.path.read_text(encoding="utf-8") == READABLE + "- My own line.\n", "the user's file is left alone"
    assert second.path == first.folder / "resume-2.md" and second.path.read_text(encoding="utf-8") == NEWER_READABLE
    assert jobs_folder.job_folder(home_root, FULLSTACK.key).resume == "resume-2.md"  # type: ignore[union-attr]
    # GigAI's own file beside it is the one a later resume replaces; the user's stays.
    third = _save(home_root, markdown=MARKDOWN)
    assert third.path == second.path and third.path.read_text(encoding="utf-8") == READABLE
    assert first.path.read_text(encoding="utf-8") == READABLE + "- My own line.\n"
    assert sorted(path.name for path in first.folder.iterdir()) == [".gigai-job.json", "resume-2.md", "resume.md"]

    # Once resume.md is gone (the user removed it), the resume is resume.md again and GigAI's beside file goes.
    first.path.unlink()
    fourth = _save(home_root, markdown=NEWER)
    assert fourth.path == first.path and sorted(path.name for path in first.folder.iterdir()) == [".gigai-job.json", "resume.md"]


def test_a_file_that_already_holds_the_resume_is_kept_as_it_is(home_root: Path) -> None:
    first = _save(home_root)
    # The user edits resume.md and hands that same text back: the stored resume and the file are one.
    first.path.write_text(NEWER_READABLE, encoding="utf-8")

    same = _save(home_root, markdown=NEWER)

    assert same.path == first.path and same.written is False
    assert sorted(path.name for path in first.folder.iterdir()) == [".gigai-job.json", "resume.md"], "no resume-2.md of the same text"
    assert _save(home_root, markdown=MARKDOWN).path == first.path, "and it is GigAI's own again: the next resume replaces it"


def test_the_file_the_user_just_handed_back_is_replaced_not_doubled(home_root: Path) -> None:
    from gigai.canonical import digest_imported_bytes

    first = _save(home_root)
    handed = READABLE.replace("nine years", "nine  years") + "\n"  # the user's own spacing: not what the store renders
    first.path.write_text(handed, encoding="utf-8")

    stored = jobs_folder.save_resume(home_root, job=FULLSTACK, markdown=NEWER, day=LATER, imported=digest_imported_bytes(handed.encode("utf-8")))

    assert stored.path == first.path and stored.path.read_text(encoding="utf-8") == NEWER_READABLE
    assert sorted(path.name for path in first.folder.iterdir()) == [".gigai-job.json", "resume.md"]
    # Any OTHER change of the file is still the user's: only the handed-back bytes may be replaced.
    first.path.write_text(handed + "- changed after the hand-back\n", encoding="utf-8")
    beside = jobs_folder.save_resume(home_root, job=FULLSTACK, markdown=MARKDOWN, day=LATER, imported=digest_imported_bytes(handed.encode("utf-8")))
    assert beside.name == "resume-2.md" and first.path.read_text(encoding="utf-8").endswith("- changed after the hand-back\n")


def test_writing_twice_writes_once(home_root: Path) -> None:
    first = _save(home_root)
    index = home_root / "scout" / "jobs-folder-index.json"
    before = (first.path.stat().st_mtime_ns, index.stat().st_mtime_ns)
    assert _save(home_root, day=LATER).written is False
    assert (first.path.stat().st_mtime_ns, index.stat().st_mtime_ns) == before


# --- never contact data, never text in GigAI's own records ---------------------------------------


@pytest.mark.parametrize("text", ["- Reach me at zora@zq.example.invalid.", "- Call +1 (555) 014-2999 any time.", "- See linkedin.com/in/zora-q for more."])
def test_contact_data_is_never_written(home_root: Path, text: str) -> None:
    with pytest.raises(JobsFolderError) as refused:
        _save(home_root, markdown=f"## Summary\n\n{text}\n")
    assert refused.value.code == "contact_data_found" and "zora" not in str(refused.value) and "555" not in str(refused.value)
    assert not (home_root / "jobs").exists() and not (home_root / "scout").exists(), "nothing is created for a refused resume"
    assert jobs_folder.try_save_resume(home_root, job=FULLSTACK, markdown=f"## Summary\n\n{text}\n", day=DAY) is None


def test_the_index_holds_folders_names_and_digests_only(home_root: Path) -> None:
    _save(home_root)
    path = home_root / "scout" / "jobs-folder-index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert index["schema_version"] == "scout-jobs-folder-index:1" and index["folder"] == str(home_root / "jobs")
    (entry,) = index["jobs"].values()
    assert entry == {"dir": "thrive-market/staff-software-engineer-fullstack", "files": {"resume.md": entry["files"]["resume.md"]}}
    assert entry["files"]["resume.md"].startswith("sha256:") and "Python" not in path.read_text(encoding="utf-8")
    # An index that names a place outside the folder is not believed.
    index["jobs"][FULLSTACK.key]["dir"] = "../elsewhere"
    path.write_text(json.dumps(index), encoding="utf-8")
    assert jobs_folder.job_folder(home_root, FULLSTACK.key) is None
    again = _save(home_root, markdown=NEWER)
    assert again.folder == home_root / "jobs" / "thrive-market" / "staff-software-engineer-fullstack", "the folder's own record still names the job"
    # ... and with no digest left to vouch for resume.md, it is treated as the user's: the new resume goes beside it.
    assert again.name == "resume-2.md" and (again.folder / "resume.md").read_text(encoding="utf-8") == READABLE
    assert not (home_root / "elsewhere").exists()


def test_the_jobs_folder_modules_import_no_pdf_and_no_header_reader() -> None:
    """Static: what writes into the jobs folder cannot reach the PDF header file or a renderer, so no header value and no PDF can land there."""

    from gigai.scout.find_jobs.api import jobs_folder as routes

    for module in (jobs_folder, routes):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in ([node.module or ""] if isinstance(node, ast.ImportFrom) else []) + [alias.name for alias in node.names]
        }
        reached = sorted(name for name in imported if "pdf" in name.lower() or "header" in name.lower() or "typst" in name.lower())
        assert reached == [], f"{module.__name__} imports {reached}"
    # The one writer takes markdown and refuses a contact shape before anything is created; it has no bytes/PDF entry point.
    writers = sorted(name for name in vars(jobs_folder) if name.startswith(("save_", "try_save_")))
    assert writers == ["save_resume", "try_save_resume"]
