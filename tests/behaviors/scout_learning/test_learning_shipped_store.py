"""0.1.11.10 shipped lessons, slice 3: the reader of the shipped courses, their page cache, the list rows and the CLI.

1. THE LIST. With no shipped folder the list is empty and nothing breaks. A valid folder is one course (role key,
   role, lesson count); a folder that is not a course (a wrong schema, a role key that is not the folder's name, a
   name that is a pathway id, a link, no catalog) is left out. A role key is never a pathway id.
2. THE CACHE. The pages are rendered on first use into ``<home>/cache/scout/learning-shipped/<version>/<role-key>/``
   with the assets at ``/learning/_assets``; a second use renders nothing; a new GigAI version, changed data or a
   changed renderer renders again and the folder is replaced whole (a page the course no longer has is gone); a
   ``pathway.html`` found there is carried over, and another version's folder holding one is not removed; a course
   that does not render raises, leaves the folder as it was and no temporary folder behind.
3. A PAGE is one plain ``.html`` name of the cache folder: never ``pathway.html``, a path, a dotfile or a link.
4. THE ASSETS: exactly the files of the package's assets folder, by name; nothing else, no link.
5. THE LOCAL PAGE: ``not_built`` until ``<home>/scout/<project>/learning/shipped/<role-key>/pathway.html`` is there.
6. THE ROWS: ``GET /api/learning/pathways``' body lists the shipped courses first with ``source: "shipped"``; the
   rows of the user's own courses are as they were.
7. THE CLI: ``list`` shows the shipped rows (marked ``shipped``), ``show`` and ``open`` take a role key, ``cancel``
   and ``resume`` refuse one.
8. Every course that really ships in the package lists and renders (none ships yet: the guard for when one does).

Everything is SYNTHETIC: the shipped course is the widget course of ``test_learning_render_shipped.py``, written
into ``tmp_path``; the package's shipped folder is replaced by it (``package_shipped_dir``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import learning_render, learning_shipped_store as store, learning_store, run_supervisor
from gigai.scout.find_jobs.api.learning import pathways_response, shipped_response, shipped_summary
from gigai.scout.learning_store import LearningError

from tests.behaviors.scout_learning.test_learning_render_shipped import shipped_course, shipped_paths, write_data
from tests.behaviors.scout_learning.test_learning_store import home_target, make_course  # noqa: F401 - home_target is the fixture

KEY = "synthetic-widget-engineer"
ROLE = "Synthetic widget engineer"
PAGES = {
    "index.html", "concept-lesson-alpha.html", "concept-lesson-beta.html", "concept-lesson-gamma.html", "concept-lesson-delta.html",
    "practice-lesson-alpha.html", "practice-lesson-gamma.html",
}
ROW_KEYS = {"id", "role_text", "requested_at", "status", "cost", "course", "url", "source", "imported_from", "error", "error_code", "progress"}


def make_shipped(root: Path, *, key: str = KEY, role: str = ROLE, course: dict | None = None, paths: dict[str, dict] | None = None) -> Path:
    """One synthetic shipped course in ``root/<key>``: ``course.json``, ``catalog.json`` and ``paths/``."""

    data = dict(course if course is not None else shipped_course())
    data.update({"role_key": key, "role": role})
    for module in data["modules"]:
        for lesson in module["concepts"]:
            lesson.pop("source", None)  # the export takes it out; the shipped schema does not hold it
    folder = write_data(root / key, data, shipped_paths() if paths is None else paths)
    lessons = [
        {"id": lesson["id"], "title": lesson["title"], "module": module["id"], "concept_keys": lesson["concept_keys"], "technologies": lesson["technologies"],
         "has_practice": (folder / "paths" / f"{lesson['id']}.json").is_file(), "subtopics": [{"id": sub["id"], "title": sub["title"]} for sub in lesson["subtopics"]]}
        for module in data["modules"] for lesson in module["concepts"]
    ]
    catalog = {
        "schema_version": "scout-learning-shipped-catalog:1", "role_key": key, "role": role,
        "titles": {"phrases": ["widget engineer"], "deny": ["intern"]},
        "vocabulary": [{"id": "widgetflow", "display": "Widgetflow", "category": "tool", "phrases": ["widgetflow"], "technical": True}],
        "expectations": {"responsibilities": ["ship-widgets", "review-widgets"], "scope_at_staff": ["owns-an-area"]},
        "technologies": {"Widget tooling": ["widgetflow", "gearbox"]},
        "modules": [{"id": module["id"], "title": module["title"]} for module in data["modules"]],
        "lessons": lessons,
    }
    (folder / "catalog.json").write_text(json.dumps(catalog, sort_keys=True), encoding="utf-8")
    return folder


@pytest.fixture
def shipped_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty folder that stands for the package's shipped folder."""

    root = tmp_path / "shipped-package"
    root.mkdir()
    monkeypatch.setattr(store, "package_shipped_dir", lambda: root)
    return root


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every folder ``render_shipped`` was asked to write, in order (the real render still runs)."""

    made: list[Path] = []
    real = learning_render.render_shipped

    def counted(data_dir: Path, out_dir: Path, asset_base: str) -> learning_render.RenderResult:
        made.append(Path(out_dir))
        return real(data_dir, out_dir, asset_base)

    monkeypatch.setattr(learning_render, "render_shipped", counted)
    return made


def _names(folder: Path) -> set[str]:
    return {entry.name for entry in folder.iterdir()}


# ---------------------------------------------------------------------------
# 1. The list
# ---------------------------------------------------------------------------


def test_with_no_shipped_folder_the_list_is_empty_and_nothing_breaks(home_target: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = home_target
    monkeypatch.setattr(store, "package_shipped_dir", lambda: tmp_path / "there-is-no-such-folder")
    assert store.list_courses() == [] and store.get_course(KEY) is None
    assert store.resolve_page(home, KEY, "index.html") is None
    assert pathways_response(home, target) == {"schema_version": "scout-learning-pathways-response:1", "pathways": [], "total": 0}


def test_the_package_folder_is_the_packages_and_the_seam_is_read_only_with_both_test_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import bindings

    packaged = store.package_shipped_dir()
    assert packaged.parts[-5:] == ("gigai", "scout", "data", "learning", "shipped")
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_SHIPPED_DIR", str(tmp_path))
    monkeypatch.setattr(bindings, "_test_model_enabled", lambda: True)
    monkeypatch.setattr(bindings, "_test_http_enabled", lambda: False)
    assert store.shipped_root() == packaged, "one seam alone names no other folder"
    monkeypatch.setattr(bindings, "_test_http_enabled", lambda: True)
    assert store.shipped_root() == tmp_path


def test_a_valid_folder_is_one_course_and_a_folder_that_is_not_a_course_is_left_out(shipped_root: Path, tmp_path: Path) -> None:
    make_shipped(shipped_root)
    make_shipped(shipped_root, key="another-role", role="Another role")
    listed = store.list_courses()
    assert [(course.role_key, course.role, course.lessons) for course in listed] == [("another-role", "Another role", 4), (KEY, ROLE, 4)], "by role"
    assert listed[1].data_dir == shipped_root / KEY and store.get_course(KEY) == listed[1]

    wrong_schema = make_shipped(shipped_root, key="wrong-schema")
    (wrong_schema / "course.json").write_text(json.dumps({**shipped_course(), "role_key": "wrong-schema", "schema_version": "scout-learning-course:1"}), encoding="utf-8")
    other_name = make_shipped(shipped_root, key="other-name")
    other_name.rename(shipped_root / "renamed-folder")
    no_catalog = make_shipped(shipped_root, key="no-catalog")
    (no_catalog / "catalog.json").unlink()
    no_role = make_shipped(shipped_root, key="no-role", role=" ")
    make_shipped(shipped_root, key="lp-0a1b2c3d")  # a pathway id is never a role key
    make_shipped(shipped_root, key="Not_A_Key")
    (shipped_root / "linked-course").symlink_to(make_shipped(tmp_path / "elsewhere", key="linked-course"), target_is_directory=True)
    (shipped_root / "README.md").write_text("not a course\n", encoding="utf-8")

    assert [course.role_key for course in store.list_courses()] == ["another-role", KEY]
    for name in ("wrong-schema", "renamed-folder", "other-name", "no-catalog", "lp-0a1b2c3d", "Not_A_Key", "linked-course", "../another-role", "", "README.md", None, 7):
        assert store.get_course(name) is None, name
    assert no_role.is_dir() and store.get_course("no-role") is None


def test_a_course_is_read_again_when_its_file_changed(shipped_root: Path) -> None:
    folder = make_shipped(shipped_root)
    assert store.get_course(KEY).lessons == 4
    course = shipped_course()
    course["modules"].pop()
    make_shipped(shipped_root.parent / "smaller", course=course)
    os.replace(shipped_root.parent / "smaller" / KEY / "course.json", folder / "course.json")
    assert store.get_course(KEY).lessons == 3


def test_a_role_key_is_lower_case_words_joined_by_dashes_and_never_a_pathway_id() -> None:
    for good in ("mlops-engineer", "a", "ai-forward-deployed-engineer", "x" * 64, "lp-0a1b2c3", "lp-0a1b2c3d4"):
        assert store.is_role_key(good), good
    for bad in ("", "MLOps", "a_b", "-a", "a-", "a--b", "a/b", "..", "x" * 65, "lp-0a1b2c3d", "a b", "a.b", None, 3):
        assert not store.is_role_key(bad), bad
    with pytest.raises(LearningError):
        store.cache_dir(Path("/nowhere"), "../escape")


# ---------------------------------------------------------------------------
# 2. The cache
# ---------------------------------------------------------------------------


def test_the_pages_are_rendered_on_first_use_into_the_cache_of_this_version_and_only_once(home_target: tuple[Path, Path], shipped_root: Path, renders: list[Path]) -> None:  # noqa: F811
    home, _target = home_target
    make_shipped(shipped_root)
    course = store.get_course(KEY)
    assert not store.is_rendered(home, course) and not store.cache_root(home).exists(), "listing renders nothing"

    site = store.ensure_rendered(home, course)
    assert site == home / "cache" / "scout" / "learning-shipped" / store.gigai_version() / KEY
    assert _names(site) == PAGES | {store.STAMP_FILENAME} and _names(site.parent) == {KEY}, "no temporary folder is left"
    index = (site / "index.html").read_text(encoding="utf-8")
    assert 'href="/learning/_assets/course.css"' in index and 'src="/learning/_assets/theme-init.js"' in index and "<style" not in index
    assert 'href="pathway.html"' in index and ROLE in index
    assert not (site / "assets").exists(), "no asset is copied into a shipped course"
    stamp = json.loads((site / store.STAMP_FILENAME).read_text(encoding="utf-8"))
    assert stamp["gigai_version"] == store.gigai_version() and stamp["role_key"] == KEY and len(stamp["digest"]) == 64
    assert store.is_rendered(home, course) and len(renders) == 1

    before = {name: (site / name).stat().st_mtime_ns for name in PAGES}
    assert store.ensure_rendered(home, course) == site and store.resolve_page(home, KEY, "index.html") == site / "index.html"
    assert len(renders) == 1 and {name: (site / name).stat().st_mtime_ns for name in PAGES} == before


def test_a_new_version_renders_again_and_removes_the_older_versions_folder(home_target: tuple[Path, Path], shipped_root: Path, renders: list[Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, _target = home_target
    make_shipped(shipped_root)
    make_shipped(shipped_root, key="another-role", role="Another role")
    monkeypatch.setattr(store, "gigai_version", lambda: "0.0.1")
    old = store.ensure_rendered(home, store.get_course(KEY))
    other = store.ensure_rendered(home, store.get_course("another-role"))
    assert old.parent.name == "0.0.1"

    monkeypatch.setattr(store, "gigai_version", lambda: "0.0.2")
    new = store.ensure_rendered(home, store.get_course(KEY))
    assert new == store.cache_root(home) / "0.0.2" / KEY and _names(new) == PAGES | {store.STAMP_FILENAME} and len(renders) == 3
    assert not old.exists(), "the pages of the older version are removed"
    assert other.is_dir(), "another course's folder is removed when THAT course is rendered again, not before"
    store.ensure_rendered(home, store.get_course("another-role"))
    assert _names(store.cache_root(home)) == {"0.0.2"}

    # A version that is not a plain folder name never becomes a path.
    assert store.cache_dir(home, KEY, "../../x") == store.cache_root(home) / "dev" / KEY


def test_changed_data_renders_again_and_the_folder_is_replaced_whole(home_target: tuple[Path, Path], shipped_root: Path, renders: list[Path]) -> None:  # noqa: F811
    home, _target = home_target
    folder = make_shipped(shipped_root)
    site = store.ensure_rendered(home, store.get_course(KEY))
    assert (site / "practice-lesson-gamma.html").is_file()

    (folder / "paths" / "lesson-gamma.json").unlink()
    course = store.get_course(KEY)
    assert not store.is_rendered(home, course)
    assert store.ensure_rendered(home, course) == site and len(renders) == 2
    assert _names(site) == (PAGES - {"practice-lesson-gamma.html"}) | {store.STAMP_FILENAME}, "a page the course no longer has is gone"
    assert _names(site.parent) == {KEY}

    # The catalog draws no page: changing it renders nothing.
    catalog = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    catalog["titles"]["deny"].append("contractor")
    (folder / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    store.ensure_rendered(home, store.get_course(KEY))
    assert len(renders) == 2


def test_a_pathway_page_in_a_cache_folder_is_never_touched(home_target: tuple[Path, Path], shipped_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, _target = home_target
    folder = make_shipped(shipped_root)
    monkeypatch.setattr(store, "gigai_version", lambda: "0.0.1")
    old = store.ensure_rendered(home, store.get_course(KEY))
    (old / "pathway.html").write_text("LOCAL PAGE 1", encoding="utf-8")

    # A re-render of the same version carries it over...
    (folder / "paths" / "lesson-gamma.json").unlink()
    assert store.ensure_rendered(home, store.get_course(KEY)) == old
    assert (old / "pathway.html").read_text(encoding="utf-8") == "LOCAL PAGE 1" and not (old / "practice-lesson-gamma.html").exists()

    # ...and a newer version does not remove the older folder that holds one.
    monkeypatch.setattr(store, "gigai_version", lambda: "0.0.2")
    new = store.ensure_rendered(home, store.get_course(KEY))
    assert (old / "pathway.html").read_text(encoding="utf-8") == "LOCAL PAGE 1" and not (new / "pathway.html").exists()
    # It is not served as a page of the course either: the pathway page has its own place.
    assert store.resolve_page(home, KEY, "pathway.html") is None


def test_a_course_that_does_not_render_raises_and_leaves_the_folder_as_it_was(home_target: tuple[Path, Path], shipped_root: Path) -> None:  # noqa: F811
    home, _target = home_target
    folder = make_shipped(shipped_root)
    site = store.ensure_rendered(home, store.get_course(KEY))
    before = {name: (site / name).read_bytes() for name in _names(site)}

    # A marker in a lesson: the shipped schema's own check refuses the folder before any page is written.
    course = json.loads((folder / "course.json").read_text(encoding="utf-8"))
    course["modules"][0]["concepts"][0]["marker"] = {"marker": "SOME", "evidence": {"id": "b-1a2b3c", "line": "a resume line"}}
    (folder / "course.json").write_text(json.dumps(course), encoding="utf-8")
    with pytest.raises(LearningError) as refused:
        store.ensure_rendered(home, store.get_course(KEY))
    assert refused.value.code == "invalid_shipped" and "marker" in str(refused.value) and "a resume line" not in str(refused.value)
    assert store.resolve_page(home, KEY, "index.html") is None, "a course that does not render serves no page"

    # A link the renderer's audit refuses: a CourseError, from the renderer itself.
    course["modules"][0]["concepts"][0].pop("marker")
    course["modules"][0]["concepts"][0]["related"] = ["no-such-lesson"]
    (folder / "course.json").write_text(json.dumps(course), encoding="utf-8")
    with pytest.raises(learning_render.CourseError):
        store.ensure_rendered(home, store.get_course(KEY))

    assert {name: (site / name).read_bytes() for name in _names(site)} == before, "the folder is as it was"
    assert _names(site.parent) == {KEY}, "no temporary folder is left behind"


def test_a_dead_temporary_folder_is_removed_once_it_is_old(home_target: tuple[Path, Path], shipped_root: Path) -> None:  # noqa: F811
    home, _target = home_target
    folder = make_shipped(shipped_root)
    site = store.ensure_rendered(home, store.get_course(KEY))
    old_leftover, new_leftover = site.parent / f".{KEY}.1-aaaaaaaa.tmp", site.parent / f".{KEY}.2-bbbbbbbb.old"
    for leftover in (old_leftover, new_leftover):
        leftover.mkdir()
        (leftover / "index.html").write_text("half", encoding="utf-8")
    os.utime(old_leftover, (1, 1))
    (folder / "paths" / "lesson-gamma.json").unlink()
    store.ensure_rendered(home, store.get_course(KEY))
    assert not old_leftover.exists() and new_leftover.exists(), "a folder a live render may still be writing is left alone"


# ---------------------------------------------------------------------------
# 3. A page
# ---------------------------------------------------------------------------


def test_a_page_is_one_plain_html_name_of_the_cache_folder(home_target: tuple[Path, Path], shipped_root: Path, tmp_path: Path) -> None:  # noqa: F811
    home, _target = home_target
    make_shipped(shipped_root)
    site = store.cache_dir(home, KEY)
    assert store.resolve_page(home, KEY, "") == site / "index.html", "the course's own address is its entry page"
    assert store.resolve_page(home, KEY, "concept-lesson-alpha.html") == site / "concept-lesson-alpha.html"
    assert store.resolve_page(home, KEY, "practice-lesson-gamma.html") == site / "practice-lesson-gamma.html"

    (tmp_path / "secret.html").write_text("SECRET", encoding="utf-8")
    (site / "link.html").symlink_to(tmp_path / "secret.html")
    (site / "notes.txt").write_text("not a page", encoding="utf-8")
    (site / "sub").mkdir()
    (site / "sub" / "page.html").write_text("nested", encoding="utf-8")
    for name in (
        "pathway.html", "missing.html", "link.html", "notes.txt", store.STAMP_FILENAME, "sub", "sub/page.html", "../index.html", "./index.html",
        "..", "index.html/", "/index.html", "index.html%00", "index%2ehtml", "concept-lesson-alpha", "\\index.html", "INDEX.HTML",
    ):
        assert store.resolve_page(home, KEY, name) is None, name
    for key in ("no-such-role", "../" + KEY, KEY.upper(), "lp-0a1b2c3d"):
        assert store.resolve_page(home, key, "index.html") is None, key


# ---------------------------------------------------------------------------
# 4. The assets
# ---------------------------------------------------------------------------


def test_the_asset_whitelist_is_the_listing_of_the_packages_assets_folder() -> None:
    base = learning_render.package_assets_dir()
    on_disk = {path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()}
    assert store.asset_names() == on_disk and {"course.css", "course.js", "theme-init.js", "mermaid/mermaid.min.js", "hljs/highlight.min.js", "hljs/LICENSE"} <= on_disk
    for name in sorted(on_disk):
        assert store.asset_file(name) == base.joinpath(*name.split("/")) and store.asset_file(name).is_file(), name
    for name in (
        "", "/", "course.css/", "/course.css", "./course.css", "../assets/course.css", "hljs", "hljs/", "hljs/../course.css", "hljs//highlight.min.js",
        "COURSE.CSS", "course.css%00", "..", "../../learning_render.py", "/etc/passwd", "hljs\\highlight.min.js", "missing.js",
    ):
        assert store.asset_file(name) is None, name


def test_a_link_a_dotfile_and_a_file_kind_that_is_not_served_are_not_assets(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    (root / "lib").mkdir(parents=True)
    (root / "course.css").write_text("body {}", encoding="utf-8")
    (root / "lib" / "tool.js").write_text("1;", encoding="utf-8")
    (root / ".hidden.js").write_text("1;", encoding="utf-8")
    (root / "tool.exe").write_text("x", encoding="utf-8")
    (tmp_path / "outside.js").write_text("SECRET", encoding="utf-8")
    (root / "link.js").symlink_to(tmp_path / "outside.js")
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    assert store.asset_names(root) == {"course.css", "lib/tool.js"}
    assert store.asset_file("lib/tool.js", root) == root / "lib" / "tool.js"
    for name in ("link.js", "linked/outside.js", ".hidden.js", "tool.exe"):
        assert store.asset_file(name, root) is None, name


# ---------------------------------------------------------------------------
# 5. The local page
# ---------------------------------------------------------------------------


def test_the_local_page_is_not_built_until_the_file_is_in_the_projects_learning_folder(home_target: tuple[Path, Path], shipped_root: Path, tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    make_shipped(shipped_root)
    page = store.pathway_page_path(home, target, KEY)
    assert page == learning_store.learning_dir(home, target) / "shipped" / KEY / "pathway.html"
    assert "cache" not in page.relative_to(home).parts, "the user's page is not in the cache: a new version does not remove it"
    assert store.pathway_page(home, target, KEY) is None
    assert store.local_page_state(home, target, KEY) == {"state": "not_built", "built_at": None}

    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert store.pathway_page(home, target, KEY) == page
    assert store.local_page_state(home, target, KEY) == {"state": "built", "built_at": "2027-01-15T08:00:00Z"}

    page.unlink()
    (tmp_path / "elsewhere.html").write_text("SECRET", encoding="utf-8")
    page.symlink_to(tmp_path / "elsewhere.html")
    assert store.pathway_page(home, target, KEY) is None and store.local_page_state(home, target, KEY)["state"] == "not_built"
    with pytest.raises(LearningError):
        store.pathway_page_path(home, target, "../escape")
    assert store.pathway_page(home, target, "../escape") is None


# ---------------------------------------------------------------------------
# 6. The rows
# ---------------------------------------------------------------------------


def test_the_list_answers_the_shipped_courses_first_and_the_users_own_rows_as_they_were(home_target: tuple[Path, Path], shipped_root: Path, tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    done = learning_store.import_course(home, target, make_course(tmp_path / "own-course", lessons=3), "Gadget engineer", None).pathway
    own_before = pathways_response(home, target)
    assert own_before["total"] == 1 and set(own_before["pathways"][0]) == ROW_KEYS

    make_shipped(shipped_root)
    make_shipped(shipped_root, key="another-role", role="Another role")
    body = pathways_response(home, target)
    assert body["schema_version"] == "scout-learning-pathways-response:1" and body["total"] == 3
    rows = body["pathways"]
    assert [row["id"] for row in rows] == ["another-role", KEY, done.id], "shipped first, by role; then the user's own"
    assert rows[2] == own_before["pathways"][0], "a generated or imported row is unchanged"
    assert rows[1] == {
        "id": KEY, "role_text": ROLE, "requested_at": None, "status": "done", "cost": None,
        "course": {"entry": "index.html", "lessons": 4, "size_bytes": None, "generated_at": None},
        "url": f"/learning/shipped/{KEY}/index.html", "source": "shipped", "imported_from": None, "error": None, "error_code": None, "progress": None,
        "label": "Shipped with GigAI",
        "local_page": {"state": "not_built", "built_at": None, "reasons": [], "build": None, "url": f"/learning/shipped/{KEY}/pathway.html"},
    }
    assert set(rows[1]) == ROW_KEYS | {"label", "local_page"}
    assert rows[1] == shipped_summary(home, target, store.get_course(KEY))
    assert shipped_response(home, target, store.get_course(KEY)) == {"schema_version": "scout-learning-pathway-response:1", "pathway": rows[1]}
    assert not store.cache_root(home).exists(), "listing renders nothing"
    assert str(tmp_path) not in json.dumps(body), "no path of this computer is in a row"

    page = store.pathway_page_path(home, target, KEY)
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert pathways_response(home, target)["pathways"][1]["local_page"] == {
        "state": "built", "built_at": "2027-01-15T08:00:00Z", "reasons": [], "build": None, "url": f"/learning/shipped/{KEY}/pathway.html",
    }


def test_reading_costs_no_process_for_a_page_or_an_asset_and_the_project_is_named_once_for_all_shipped_rows(
    home_target: tuple[Path, Path], shipped_root: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    import subprocess

    home, target = home_target
    make_shipped(shipped_root)
    make_shipped(shipped_root, key="another-role", role="Another role")
    store.ensure_rendered(home, store.get_course(KEY))

    spawned: list[object] = []
    real_popen = subprocess.Popen.__init__

    def spy(self, *args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        return real_popen(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    assert store.resolve_page(home, KEY, "index.html") is not None and store.resolve_page(home, KEY, "concept-lesson-alpha.html") is not None
    assert store.asset_file("course.css") is not None and store.list_courses() and store.get_course(KEY) is not None
    assert spawned == [], "a page of a rendered course, an asset and the list of shipped courses start no process"

    named: list[int] = []
    real_dir = learning_store.learning_dir
    monkeypatch.setattr(learning_store, "learning_dir", lambda *args: named.append(1) or real_dir(*args))
    monkeypatch.setattr(store, "package_shipped_dir", lambda: shipped_root.parent / "nothing-ships")
    assert pathways_response(home, target)["total"] == 0
    without = len(named)
    monkeypatch.setattr(store, "package_shipped_dir", lambda: shipped_root)
    del named[:]
    assert pathways_response(home, target)["total"] == 2
    assert len(named) == without + 1, "two shipped rows name the project once more, not twice"


# ---------------------------------------------------------------------------
# 7. The CLI
# ---------------------------------------------------------------------------


def _run(home: Path, target: Path, *args: str):
    return CliRunner().invoke(cli, ["scout", "learning", *args, "--home", str(home), "--target", str(target)], catch_exceptions=False)


def _json(result) -> dict[str, object]:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def test_list_and_show_name_the_shipped_courses(home_target: tuple[Path, Path], shipped_root: Path, tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    make_shipped(shipped_root)
    only_shipped = _run(home, target, "list")
    assert only_shipped.exit_code == 0 and "No learning pathways yet" not in only_shipped.output, only_shipped.output
    done = learning_store.import_course(home, target, make_course(tmp_path / "own-course", lessons=3), "Gadget engineer", None).pathway

    listed = _run(home, target, "list")
    assert listed.exit_code == 0, listed.output
    lines = listed.output.splitlines()
    assert lines[0] == f"{KEY}  shipped  {ROLE}  (4 lessons)  [shipped with GigAI]  your pathway page is not built yet"
    assert lines[1].startswith(done.id) and "[imported]" in lines[1]
    assert "gigai scout learning open ID" in lines[-1] and "ROLE_KEY" in lines[-1]
    assert _json(_run(home, target, "list", "--json")) == pathways_response(home, target)

    shown = _run(home, target, "show", KEY)
    assert shown.exit_code == 0, shown.output
    for part in (f"{KEY}  {ROLE}", "Status: done", "Course: 4 lessons", "Source: shipped with GigAI", "Your pathway page: your pathway page is not built yet", f"gigai scout learning open {KEY}"):
        assert part in shown.output, (part, shown.output)
    assert _json(_run(home, target, "show", KEY, "--json")) == shipped_response(home, target, store.get_course(KEY))

    page = store.pathway_page_path(home, target, KEY)
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert "your pathway page was built on 2027-01-15" in _run(home, target, "list").output.splitlines()[0]

    missing = _run(home, target, "show", "no-such-role", "--json")
    assert missing.exit_code == 1 and _json(missing)["error"]["code"] == "pathway_not_found"


def test_open_takes_a_role_key_and_cancel_and_resume_refuse_one(home_target: tuple[Path, Path], shipped_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = home_target
    make_shipped(shipped_root)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    running = run_supervisor.ScoutStatus(state=run_supervisor.STATE_RUNNING, project_id="project_x", url="http://127.0.0.1:8765/", pid=4242)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: running)
    address = f"http://127.0.0.1:8765/learning/shipped/{KEY}/index.html"

    printed = _run(home, target, "open", KEY, "--no-browser")
    assert printed.exit_code == 0 and printed.output.strip() == address and opened == []
    assert _run(home, target, "open", KEY).output.strip() == address and opened == [address]
    assert _json(_run(home, target, "open", KEY, "--json")) == {"schema_version": "scout-learning-open:1", "id": KEY, "url": address, "opened": False}

    stopped = run_supervisor.ScoutStatus(state="stopped", project_id="project_x", url=None, pid=None)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: stopped)
    not_running = _run(home, target, "open", KEY, "--json")
    assert not_running.exit_code == 1 and _json(not_running)["error"]["code"] == "server_not_running"

    for command in ("cancel", "resume"):
        refused = _run(home, target, command, KEY, "--json")
        assert refused.exit_code == 1, refused.output
        error = _json(refused)["error"]
        assert error["code"] == "pathway_not_found" and "shipped with GigAI" in error["message"] and command in error["message"]
    assert not store.cache_root(home).exists() and learning_store.list_pathways(home, target) == []


# ---------------------------------------------------------------------------
# 8. What really ships
# ---------------------------------------------------------------------------


def test_every_course_that_ships_in_the_package_lists_and_renders(home_target: tuple[Path, Path]) -> None:  # noqa: F811
    home, _target = home_target
    packaged = store.package_shipped_dir()
    folders = sorted(entry.name for entry in packaged.iterdir() if entry.is_dir()) if packaged.is_dir() else []
    listed = store.list_courses(packaged)
    assert sorted(course.role_key for course in listed) == folders, "a folder of the package's shipped folder is not a valid shipped course"
    for course in listed:
        site = store.ensure_rendered(home, course)
        assert (site / "index.html").is_file() and not (site / "assets").exists(), course.role_key
