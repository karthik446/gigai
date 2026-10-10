"""0.1.11.10 Part A: importing a course folder (``gigai.scout.learning_import``).

1. A synthetic course is accepted: its files are copied byte for byte under ``courses/<id>/site``, the record is
   ``done`` with ``source: imported``, the lessons (``concept-*.html`` at the top) and the bytes are counted, and
   ``imported_from`` is the folder's own name, never a path.
2. The folder is checked whole and NOTHING is stored when a check fails: a link (a file, a folder, the folder
   itself), a file kind that is not allowed, no ``index.html``, no lesson page, a dotfile, a name with ``..`` or a
   space or a non-ASCII letter, too many files, too many bytes, an HTML page of 3 MB.
3. The same role imported again is a NEW pathway; ``replace_id`` replaces that one pathway's course (same id, same
   ``requested_at``, one revision on) and an unknown id is ``pathway_not_found``.
4. The pages with an inline script block are counted (the server does not run those), and not changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import learning_import, learning_store
from gigai.scout.learning_store import LearningError

from tests.behaviors.scout_learning.test_learning_store import APP_JS, home_target, make_course, page  # noqa: F401 - home_target is the fixture


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _stored_nothing(home: Path, target: Path) -> bool:
    courses = learning_store.courses_dir(home, target)
    return learning_store.list_pathways(home, target) == [] and (not courses.exists() or list(courses.iterdir()) == [])


def test_a_synthetic_course_is_imported_whole_and_the_record_is_done(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    source = make_course(tmp_path / "mlops-course", lessons=3)
    before = _tree(source)
    cost = {"cli_model_calls": 11, "worker_minutes": 135, "web_fetches": 430, "web_searches": 16, "note": "synthetic"}

    result = learning_import.import_course(home, target, source, "MLOps engineer", cost)
    record = result.pathway

    site = learning_store.course_site_dir(home, target, record.id)
    assert _tree(site) == before, "every file is copied byte for byte, and nothing else"
    assert _tree(source) == before, "the folder that was imported is not changed"
    assert record.status == "done" and record.source == "imported" and record.revision == 1 and record.error is None
    assert record.role_text == "MLOps engineer" and record.cost == {**cost, "tokens": None}
    assert record.course is not None
    assert record.course.to_json() == {"entry": "index.html", "lessons": 3, "size_bytes": sum(map(len, before.values())), "generated_at": None}
    assert record.imported_from == "mlops-course"
    assert result.files == len(before) and result.replaced is False
    assert learning_store.get_pathway(home, target, record.id) == record
    stored = (learning_store.records_dir(home, target) / f"{record.id}.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in stored and str(home) not in stored, "no path of this computer is stored"
    assert json.loads(stored)["imported_from"] == "mlops-course"
    assert sorted(path.name for path in learning_store.courses_dir(home, target).iterdir()) == [record.id], "no temporary folder is left"
    assert result.to_json()["pathway"] == record.to_json() and result.to_json()["schema_version"] == "scout-learning-import:1"


def test_a_folder_name_that_is_not_plain_is_not_kept(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    assert learning_import.import_course(home, target, make_course(tmp_path / "my course (final)"), "MLOps engineer").pathway.imported_from is None
    assert learning_import.import_course(home, target, make_course(tmp_path / "jane.roe@example.com"), "MLOps engineer").pathway.imported_from is None


def _with_symlinked_file(root: Path) -> None:
    (root / "assets" / "more.js").symlink_to(root / "assets" / "app.js")


def _with_symlinked_folder(root: Path) -> None:
    (root / "shots").symlink_to(root / "assets", target_is_directory=True)


def _with_link_out(root: Path) -> None:
    outside = root.parent / "outside.txt"
    outside.write_text("not part of the course", encoding="utf-8")
    (root / "notes.txt").symlink_to(outside)


def _write(relative: str, content: str = "x"):
    def change(root: Path) -> None:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return change


def _remove(relative: str):
    return lambda root: (root / relative).unlink()


def _no_lessons(root: Path) -> None:
    for path in root.glob("concept-*.html"):
        path.unlink()


@pytest.mark.parametrize(
    ("change", "code", "named"),
    [
        (_with_symlinked_file, "course_symlink", "assets/more.js"),
        (_with_symlinked_folder, "course_symlink", "shots"),
        (_with_link_out, "course_symlink", "notes.txt"),
        (_write("assets/tool.exe"), "course_file_kind_refused", "assets/tool.exe"),
        (_write("assets/run.sh"), "course_file_kind_refused", "assets/run.sh"),
        (_write("assets/page.php"), "course_file_kind_refused", "assets/page.php"),
        (_write("README"), "course_file_kind_refused", "README"),
        (_write("assets/photo.jpeg"), "course_file_kind_refused", "assets/photo.jpeg"),
        (_remove("index.html"), "course_index_missing", "index.html"),
        (_no_lessons, "course_no_lessons", "concept-"),
        (_write(".env"), "course_name_refused", ".env"),
        (_write("assets/.hidden.js"), "course_name_refused", "assets/.hidden.js"),
        (_write(".git/config.txt"), "course_name_refused", ".git"),
        (_write("assets/a..b.js"), "course_name_refused", "a..b.js"),
        (_write("assets/my file.js"), "course_name_refused", "my file.js"),
        (_write("assets/café.js"), "course_name_refused", "caf"),
        (_write("assets/-rf.js"), "course_name_refused", "-rf.js"),
        (_write("big.html", "x" * (3 * 1024 * 1024)), "course_html_too_large", "big.html"),
    ],
)
def test_a_course_that_fails_a_check_is_refused_by_name_and_nothing_is_stored(
    home_target: tuple[Path, Path], tmp_path: Path, change, code: str, named: str,  # noqa: F811
) -> None:
    home, target = home_target
    source = make_course(tmp_path / "course")
    change(source)
    with pytest.raises(LearningError) as refused:
        learning_import.import_course(home, target, source, "MLOps engineer")
    assert refused.value.code == code, str(refused.value)
    assert named in str(refused.value), str(refused.value)
    assert str(tmp_path) not in str(refused.value), "the refusal names the file inside the course, never a path of this computer"
    assert _stored_nothing(home, target)


def test_the_folder_itself_must_be_a_real_folder(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    real = make_course(tmp_path / "course")
    link = tmp_path / "link-to-course"
    link.symlink_to(real, target_is_directory=True)
    for source, code in ((link, "course_symlink"), (tmp_path / "missing", "course_not_a_folder"), (real / "index.html", "course_not_a_folder")):
        with pytest.raises(LearningError) as refused:
            learning_import.import_course(home, target, source, "MLOps engineer")
        assert refused.value.code == code
    assert _stored_nothing(home, target)


def test_the_size_and_count_caps_hold(home_target: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = home_target
    assert (learning_import.MAX_TOTAL_BYTES, learning_import.MAX_FILES, learning_import.MAX_HTML_BYTES) == (64 * 1024 * 1024, 2000, 3 * 1024 * 1024)
    source = make_course(tmp_path / "course")
    size = sum(len(content) for content in _tree(source).values())
    count = len(_tree(source))

    monkeypatch.setattr(learning_import, "MAX_TOTAL_BYTES", size - 1)
    with pytest.raises(LearningError) as refused:
        learning_import.import_course(home, target, source, "MLOps engineer")
    assert refused.value.code == "course_too_large"
    monkeypatch.setattr(learning_import, "MAX_TOTAL_BYTES", size)

    monkeypatch.setattr(learning_import, "MAX_FILES", count - 1)
    with pytest.raises(LearningError) as refused:
        learning_import.import_course(home, target, source, "MLOps engineer")
    assert refused.value.code == "course_too_many_files"
    assert _stored_nothing(home, target)

    monkeypatch.setattr(learning_import, "MAX_FILES", count)
    assert learning_import.import_course(home, target, source, "MLOps engineer").pathway.status == "done"  # exactly at both caps
    # A page just under 3 MB is accepted.
    (source / "wide.html").write_text("x" * (3 * 1024 * 1024 - 1), encoding="utf-8")
    monkeypatch.undo()
    assert learning_import.import_course(home, target, source, "MLOps engineer").pathway.status == "done"


def test_a_refused_role_or_cost_stores_nothing(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    source = make_course(tmp_path / "course")
    with pytest.raises(LearningError) as refused:
        learning_import.import_course(home, target, source, "MLOps, call +1 415 555 0100")
    assert refused.value.code == "personal_info_refused"
    with pytest.raises(LearningError) as refused:
        learning_import.import_course(home, target, source, "MLOps engineer", {"wat": 5})
    assert refused.value.code == "invalid_value"
    assert _stored_nothing(home, target)


def test_the_same_role_again_is_a_new_pathway_and_replace_replaces_one(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    first = learning_import.import_course(home, target, make_course(tmp_path / "v1", lessons=2), "MLOps engineer").pathway
    second = learning_import.import_course(home, target, make_course(tmp_path / "v1b", lessons=2), "MLOps engineer").pathway
    assert first.id != second.id, "an import never writes over another pathway silently"
    assert {item.id for item in learning_store.list_pathways(home, target)} == {first.id, second.id}
    first_site = _tree(learning_store.course_site_dir(home, target, first.id))

    newer = make_course(tmp_path / "v2", lessons=4, extra={"assets/extra.js": APP_JS})
    (newer / "practice-1-1-first.html").unlink()
    replaced = learning_import.import_course(home, target, newer, "MLOps engineer (v2)", {"web_fetches": 9}, replace_id=second.id)
    after = replaced.pathway
    assert replaced.replaced is True and after.id == second.id and after.requested_at == second.requested_at and after.revision == second.revision + 1
    assert after.role_text == "MLOps engineer (v2)" and after.cost["web_fetches"] == 9 and after.imported_from == "v2"
    assert after.course is not None and after.course.lessons == 4
    assert _tree(learning_store.course_site_dir(home, target, second.id)) == _tree(newer), "the old course's files are gone, the new one's are there"
    assert _tree(learning_store.course_site_dir(home, target, first.id)) == first_site, "the other pathway is untouched"
    assert sorted(path.name for path in learning_store.courses_dir(home, target).iterdir()) == sorted([first.id, second.id])

    # A request that is still queued can get its course by an import; an unknown id cannot.
    queued = learning_store.create_request(home, target, "Product manager")
    filled = learning_import.import_course(home, target, make_course(tmp_path / "pm"), "Product manager", replace_id=queued.id).pathway
    assert filled.id == queued.id and filled.status == "done" and filled.source == "imported" and filled.revision == 2
    for unknown in ("lp-00000000", "nope", "../x"):
        with pytest.raises(LearningError) as refused:
            learning_import.import_course(home, target, make_course(tmp_path / f"u{len(unknown)}"), "MLOps engineer", replace_id=unknown)
        assert refused.value.code == "pathway_not_found"
    # A replacement that fails its checks leaves the stored course as it was.
    broken = make_course(tmp_path / "broken")
    (broken / "assets" / "tool.exe").write_text("x", encoding="utf-8")
    with pytest.raises(LearningError):
        learning_import.import_course(home, target, broken, "MLOps engineer", replace_id=second.id)
    assert _tree(learning_store.course_site_dir(home, target, second.id)) == _tree(newer)
    assert learning_store.get_pathway(home, target, second.id) == after


def test_pages_with_an_inline_script_block_are_counted_and_left_as_they_are(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    plain = learning_import.import_course(home, target, make_course(tmp_path / "plain"), "MLOps engineer")
    assert plain.inline_script_pages == 0, "a <script src> is a file, not an inline block"
    inline = '<script>document.title = "inline";</script>'
    source = make_course(
        tmp_path / "inline", lessons=2,
        extra={
            "concept-1-1-first.html": page("Lesson 1", inline),
            "practice-1-1-first.html": page("Practice 1", "<SCRIPT type=\"module\">\n  window.x = 1;\n</SCRIPT>"),
            "concept-1-2-first.html": page("Lesson 2", "<script></script><script src=\"assets/app.js\"></script>"),
        },
    )
    result = learning_import.import_course(home, target, source, "MLOps engineer")
    assert result.inline_script_pages == 2
    site = learning_store.course_site_dir(home, target, result.pathway.id)
    assert inline.encode() in (site / "concept-1-1-first.html").read_bytes(), "the page is stored as it was given"
