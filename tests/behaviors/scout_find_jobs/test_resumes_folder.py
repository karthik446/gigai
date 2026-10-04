"""0110-10-05 A: the resumes folder's own rules (``gigai.scout.resumes_folder``), on temporary folders only.

* where it is: ``~/Documents/GigAI/resumes`` only for the default home ``~/.gigai``; any other
  home keeps its files inside itself, so a scratch or test home never writes into Documents;
* the setting: an absolute or ``~`` path, created when chosen; a relative path, a file, or a
  folder inside the GigAI home is refused; reset goes back to the default;
* never contact data: a text with a contact shape is refused, nothing is written;
* never the user's file: a file the user changed, or one GigAI did not write, is left alone and
  the new file gets the next free name; GigAI's own untouched file is replaced;
* one markdown per job (a newer date replaces the older file GigAI wrote), PDFs are kept;
* the index holds file names, digests and keys: no text.

``Path.home`` is patched to a temporary folder wherever the default is computed, so no test can
resolve the real Documents folder.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from gigai.scout import resumes_folder
from gigai.scout.resumes_folder import ResumesFolderError

DAY = date(2026, 10, 4)
MARKDOWN = "## Summary\n\n- Platform engineer with nine years of Python. <!-- R3 -->\n"
READABLE = "## Summary\n\n- Platform engineer with nine years of Python.\n"
KEY = "project_x/resumes/profile_y/abc"


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


def _md(home_root: Path, *, key: str = KEY, day: date = DAY, markdown: str = MARKDOWN, company: str = "Acme Corp", role: str = "Staff Engineer"):
    return resumes_folder.save_markdown(home_root, key=key, company=company, role=role, day=day, markdown=markdown)


def test_only_the_default_home_uses_documents(user_home: Path, tmp_path: Path) -> None:
    assert resumes_folder.default_folder(user_home / ".gigai") == user_home / "Documents" / "GigAI" / "resumes"
    scratch = tmp_path / "scratch-home"
    assert resumes_folder.default_folder(scratch) == scratch / "resumes"
    folder = resumes_folder.resumes_folder(user_home / ".gigai")
    assert (folder.source, folder.shown) == ("default", "~/Documents/GigAI/resumes")
    assert folder.to_json()["exists"] is False, "reading where the folder is never creates it"
    assert not (user_home / "Documents").exists()


def test_the_folder_is_a_setting(home_root: Path, user_home: Path, tmp_path: Path) -> None:
    chosen = resumes_folder.set_resumes_folder(home_root, "~/Resumes")
    assert chosen.path == user_home / "Resumes" and chosen.path.is_dir() and chosen.source == "setting"
    assert resumes_folder.resumes_folder(home_root).path == user_home / "Resumes"
    setting = home_root / "scout" / "resumes-folder.json"
    assert setting.stat().st_mode & 0o777 == 0o600
    assert _md(home_root).path.parent == user_home / "Resumes"

    for bad in ("relative/folder", "line\nbreak", str(home_root / "scout" / "inside"), str(home_root)):
        with pytest.raises(ResumesFolderError) as caught:
            resumes_folder.set_resumes_folder(home_root, bad)
        assert caught.value.code == "invalid_value", bad
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ResumesFolderError):
        resumes_folder.set_resumes_folder(home_root, str(a_file))
    with pytest.raises(ResumesFolderError) as caught:
        resumes_folder.set_resumes_folder(home_root, 7)
    assert caught.value.code == "wrong_type"
    assert resumes_folder.resumes_folder(home_root).path == user_home / "Resumes", "a refused value changes nothing"

    back = resumes_folder.set_resumes_folder(home_root, "")
    assert (back.path, back.source) == (home_root / "resumes", "default") and not setting.exists()
    # The default of a home that is not ~/.gigai is inside that home, and may be chosen by name.
    assert resumes_folder.set_resumes_folder(home_root, str(home_root / "resumes")).path == home_root / "resumes"


def test_the_markdown_is_named_for_the_job_and_reads_as_a_resume(home_root: Path) -> None:
    saved = _md(home_root)
    assert saved.name == "acme-corp-staff-engineer-2026-10-04.md" and saved.written
    assert saved.path.read_text(encoding="utf-8") == READABLE, "the source comments are not in the visible file"
    assert resumes_folder.file_name("", "", DAY, ".md") == "resume-2026-10-04.md"
    assert resumes_folder.file_name("Ünïcode & Söns", "Señor  Engineer / Platform", DAY) == "unicode-sons-senor-engineer-platform-2026-10-04.pdf"
    again = _md(home_root)
    assert again.path == saved.path and again.written is False, "the same bytes are not written twice"
    assert resumes_folder.job_files(home_root, KEY) == {"markdown": saved.name, "pdf": None}


@pytest.mark.parametrize("text", ["- Reach me at zora@zq.example.invalid.", "- Call +1 (555) 014-2999 any time.", "- See linkedin.com/in/zora-q for more."])
def test_contact_data_is_never_written(home_root: Path, text: str) -> None:
    with pytest.raises(ResumesFolderError) as caught:
        _md(home_root, markdown=f"## Summary\n\n{text}\n")
    assert caught.value.code == "contact_data_found" and "zora" not in str(caught.value) and "555" not in str(caught.value)
    with pytest.raises(ResumesFolderError):
        resumes_folder.save_pdf(home_root, name="acme-staff-2026-10-04.pdf", pdf=b"%PDF-1.7 fake", text=text)
    assert not (home_root / "resumes").exists() or list((home_root / "resumes").iterdir()) == []
    assert resumes_folder.try_save_markdown(home_root, key=KEY, company="Acme", role="Staff", day=DAY, markdown=text) is None


def test_a_file_the_user_changed_is_never_replaced(home_root: Path) -> None:
    first = _md(home_root)
    first.path.write_text(READABLE + "- My own extra line.\n", encoding="utf-8")  # the user edits it in place

    second = _md(home_root, markdown=MARKDOWN + "- Ran the release calendar.\n")

    assert second.name == "acme-corp-staff-engineer-2026-10-04-2.md"
    assert "My own extra line." in first.path.read_text(encoding="utf-8"), "the user's edit is still there"
    assert "Ran the release calendar." in second.path.read_text(encoding="utf-8")
    # A file GigAI never wrote is not replaced either.
    folder = first.path.parent
    (folder / "globex-sre-2026-10-04.md").write_text("mine\n", encoding="utf-8")
    other = _md(home_root, key="project_x/resumes/profile_y/other", company="Globex", role="SRE")
    assert other.name == "globex-sre-2026-10-04-2.md" and (folder / "globex-sre-2026-10-04.md").read_text(encoding="utf-8") == "mine\n"


def test_two_jobs_with_the_same_name_get_two_files(home_root: Path) -> None:
    one = _md(home_root, key="p/resumes/a/1")
    two = _md(home_root, key="p/resumes/a/2", markdown=MARKDOWN + "- Another job's line.\n")
    assert (one.name, two.name) == ("acme-corp-staff-engineer-2026-10-04.md", "acme-corp-staff-engineer-2026-10-04-2.md")
    assert _md(home_root, key="p/resumes/a/2", markdown=MARKDOWN + "- Changed.\n").path == two.path, "each job keeps its own file"
    assert one.path.read_text(encoding="utf-8") == READABLE


def test_a_job_has_one_markdown_and_keeps_its_pdfs(home_root: Path) -> None:
    old = _md(home_root)
    new = _md(home_root, day=date(2026, 10, 6), markdown=MARKDOWN + "- Tailored again.\n")
    assert new.name == "acme-corp-staff-engineer-2026-10-06.md" and not old.path.exists(), "the older markdown GigAI wrote is replaced"

    first = resumes_folder.save_pdf(home_root, name="acme-corp-staff-engineer-2026-10-04.pdf", pdf=b"%PDF-1.7 one", text=READABLE, key=KEY)
    later = resumes_folder.save_pdf(home_root, name="acme-corp-staff-engineer-2026-10-06.pdf", pdf=b"%PDF-1.7 two", text=READABLE, key=KEY)
    assert first.path.exists() and later.path.exists(), "an earlier PDF is kept"
    same_day = resumes_folder.save_pdf(home_root, name="acme-corp-staff-engineer-2026-10-06.pdf", pdf=b"%PDF-1.7 three", text=READABLE, key=KEY)
    assert same_day.path == later.path and later.path.read_bytes() == b"%PDF-1.7 three", "GigAI's own untouched file is written again"
    assert resumes_folder.job_files(home_root, KEY) == {"markdown": new.name, "pdf": later.name}


def test_the_index_holds_names_digests_and_keys_only(home_root: Path) -> None:
    _md(home_root)
    path = home_root / "scout" / "resumes-folder-index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(index) == {"schema_version", "folder", "files"} and index["folder"] == str(home_root / "resumes")
    (entry,) = index["files"].values()
    assert set(entry) == {"sha256", "key"} and entry["key"] == KEY and entry["sha256"].startswith("sha256:")
    assert "Platform engineer" not in path.read_text(encoding="utf-8")
