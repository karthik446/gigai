"""0.1.11.10 shipped lessons, slice 3 (learning center M2): the reader of the course catalogs that ship in GigAI, the list rows and the CLI.

1. THE LIST. With no shipped folder the list is empty and nothing breaks. A valid folder is one course (role key,
   role, lesson count) read from its ``catalog.json`` ALONE (``scout-learning-shipped-catalog:2``, or ``:1``, which has
   no ``site``); a folder that is not a course (a wrong schema, a role key that is not the folder's name, a name that
   is a pathway id, a link, no catalog) is left out. A role key is never a pathway id.
2. THE CATALOG AND THE SITE. ``:2`` names the learning center (``site``) and every address is built from it:
   ``<base>/lessons/<library id>/#<subtopic>``, ``<base>/practice/<library id>/``, ``<base>/pathways/<role key>/``. ``:1``
   (or a site block that is not valid) links nothing. The store renders, caches and serves NO lesson page.
3. THE ASSETS: exactly the files of the package's assets folder, by name; nothing else, no link.
4. THE LOCAL PAGE: ``not_built`` until ``<home>/scout/<project>/learning/shipped/<role-key>/pathway.html`` is there.
5. THE ROWS: ``GET /api/learning/pathways``' body lists the shipped courses first with ``source: "shipped"``, the label
   'From the GigAI learning center' and the public address of the course's pathway as ``url``.
6. THE CLI: ``list`` shows the shipped rows, ``show`` and ``open`` take a role key (``open`` prints the public address and
   needs no server), ``cancel`` and ``resume`` refuse one.
7. Every course that really ships in the package lists, holds ONLY ``catalog.json`` and names the learning center.

Everything is SYNTHETIC: the shipped course is the widget course of ``test_learning_render_shipped.py``, written as a
catalog into ``tmp_path``; the package's shipped folder is replaced by it (``package_shipped_dir``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import learning_render, learning_shipped, learning_shipped_store as store, learning_store, run_supervisor
from gigai.scout.find_jobs.api.learning import pathways_response, shipped_response, shipped_summary
from gigai.scout.learning_store import LearningError

from tests.behaviors.scout_learning.test_learning_render_shipped import shipped_course, shipped_paths
from tests.behaviors.scout_learning.test_learning_store import home_target, make_course  # noqa: F401 - home_target is the fixture

KEY = "synthetic-widget-engineer"
ROLE = "Synthetic widget engineer"
BASE = "https://scout.edgelab.one/learn"
SITE = {"base_url": BASE, "site_version": "2026.10.1", "lessons_path": "lessons", "practice_path": "practice", "pathways_path": "pathways"}
ROW_KEYS = {"id", "role_text", "requested_at", "status", "cost", "course", "url", "source", "imported_from", "error", "error_code", "progress"}


def make_catalog(*, key: str = KEY, role: str = ROLE, course: dict | None = None, paths: dict[str, dict] | None = None, site: dict | None | bool = True, schema: int = 2) -> dict:
    """The synthetic catalog of the widget course (the shape ``learning_shipped.shipped_course`` writes)."""

    data = dict(course if course is not None else shipped_course())
    practice = shipped_paths() if paths is None else paths
    lessons = [
        {"id": lesson["id"], "title": lesson["title"], "module": module["id"], "concept_keys": lesson["concept_keys"], "technologies": lesson["technologies"],
         "has_practice": lesson["id"] in practice, "subtopics": [{"id": sub["id"], "title": sub["title"]} for sub in lesson["subtopics"]]}
        for module in data["modules"] for lesson in module["concepts"]
    ]
    catalog = {
        "schema_version": f"scout-learning-shipped-catalog:{schema}", "role_key": key, "role": role,
        "titles": {"phrases": ["widget engineer"], "deny": ["intern"]},
        "vocabulary": [{"id": "widgetflow", "display": "Widgetflow", "category": "tool", "phrases": ["widgetflow"], "technical": True}],
        "expectations": {"responsibilities": ["ship-widgets", "review-widgets"], "scope_at_staff": ["owns-an-area"]},
        "technologies": {"Widget tooling": ["widgetflow", "gearbox"]},
        "modules": [{"id": module["id"], "title": module["title"]} for module in data["modules"]],
        "lessons": lessons,
    }
    if schema == 2:
        catalog["lessons"] = [{**lesson, "prerequisites": [], "depth": None} for lesson in lessons]
        catalog["pathway_version"], catalog["concept_keys_version"] = 1, 1
        if site:
            catalog["site"] = dict(SITE) if site is True else site
    return catalog


def make_shipped(root: Path, *, key: str = KEY, role: str = ROLE, **options: object) -> Path:
    """One synthetic shipped course in ``root/<key>``: ``catalog.json`` ALONE (that is all that ships)."""

    folder = root / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "catalog.json").write_text(json.dumps(make_catalog(key=key, role=role, **options), sort_keys=True), encoding="utf-8")  # type: ignore[arg-type]
    return folder


@pytest.fixture
def shipped_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty folder that stands for the package's shipped folder."""

    root = tmp_path / "shipped-package"
    root.mkdir()
    monkeypatch.setattr(store, "package_shipped_dir", lambda: root)
    return root


# ---------------------------------------------------------------------------
# 1. The list
# ---------------------------------------------------------------------------


def test_with_no_shipped_folder_the_list_is_empty_and_nothing_breaks(home_target: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = home_target
    monkeypatch.setattr(store, "package_shipped_dir", lambda: tmp_path / "there-is-no-such-folder")
    assert store.list_courses() == [] and store.get_course(KEY) is None
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
    (wrong_schema / "catalog.json").write_text(json.dumps({**make_catalog(key="wrong-schema"), "schema_version": "scout-learning-shipped-catalog:3"}), encoding="utf-8")
    no_lessons = make_shipped(shipped_root, key="no-lessons")
    (no_lessons / "catalog.json").write_text(json.dumps({**make_catalog(key="no-lessons"), "lessons": []}), encoding="utf-8")
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
    for name in ("wrong-schema", "no-lessons", "renamed-folder", "other-name", "no-catalog", "lp-0a1b2c3d", "Not_A_Key", "linked-course", "../another-role", "", "README.md", None, 7):
        assert store.get_course(name) is None, name
    assert no_role.is_dir() and store.get_course("no-role") is None


def test_a_course_is_read_again_when_its_file_changed(shipped_root: Path) -> None:
    folder = make_shipped(shipped_root)
    assert store.get_course(KEY).lessons == 4
    course = shipped_course()
    course["modules"].pop()
    make_shipped(shipped_root.parent / "smaller", course=course)
    os.replace(shipped_root.parent / "smaller" / KEY / "catalog.json", folder / "catalog.json")
    assert store.get_course(KEY).lessons == 3


def test_a_role_key_is_lower_case_words_joined_by_dashes_and_never_a_pathway_id() -> None:
    for good in ("mlops-engineer", "a", "ai-forward-deployed-engineer", "x" * 64, "lp-0a1b2c3", "lp-0a1b2c3d4"):
        assert store.is_role_key(good), good
    for bad in ("", "MLOps", "a_b", "-a", "a-", "a--b", "a/b", "..", "x" * 65, "lp-0a1b2c3d", "a b", "a.b", None, 3):
        assert not store.is_role_key(bad), bad


# ---------------------------------------------------------------------------
# 2. The catalog and the site
# ---------------------------------------------------------------------------


def test_a_v2_catalog_names_the_learning_center_and_every_address_is_built_from_it(shipped_root: Path) -> None:
    make_shipped(shipped_root)
    course = store.get_course(KEY)
    catalog = store.read_catalog(course)
    assert catalog["schema_version"] == learning_shipped.CATALOG_SCHEMA_VERSION == "scout-learning-shipped-catalog:2"
    site = store.site_of(catalog)
    assert site == learning_shipped.Site(BASE, "2026.10.1") and site.host == "scout.edgelab.one"
    assert site.lesson_url("lesson-alpha") == f"{BASE}/lessons/lesson-alpha/"
    assert site.lesson_url("lesson-alpha", "sub-one") == f"{BASE}/lessons/lesson-alpha/#sub-one"
    assert site.practice_url("lesson-alpha") == f"{BASE}/practice/lesson-alpha/"
    assert site.pathway_url(KEY) == f"{BASE}/pathways/{KEY}/" == store.public_url(course)
    assert site.home_url() == f"{BASE}/"
    assert site.owns(f"{BASE}/lessons/x/") and site.owns(BASE) and not site.owns("https://scout.edgelab.one/other/") and not site.owns("https://scout.edgelab.one.evil.example/learn/x")
    assert learning_shipped.library_id({"id": "a"}) == "a" and learning_shipped.library_id({"id": "a", "library_id": "b"}) == "b"
    assert "?" not in site.lesson_url("x", "y") and site.lesson_url("a b/c") == f"{BASE}/lessons/a%20b%2Fc/", "an id is quoted, never a path"


def test_a_v1_catalog_is_listed_and_links_nothing(shipped_root: Path) -> None:
    make_shipped(shipped_root, schema=1)
    course = store.get_course(KEY)
    assert course is not None and course.lessons == 4
    assert store.site_of(store.read_catalog(course)) is None and store.public_url(course) is None


def test_a_site_block_that_is_not_valid_links_nothing(shipped_root: Path) -> None:
    for bad in (
        {**SITE, "base_url": "http://scout.edgelab.one/learn"}, {**SITE, "base_url": "https://user:pw@scout.edgelab.one/learn"},
        {**SITE, "base_url": "https://scout.edgelab.one/learn?x=1"}, {**SITE, "base_url": "https://scout.edgelab.one/learn#x"}, {**SITE, "base_url": 7},
        {**SITE, "lessons_path": "../lessons"}, {**SITE, "practice_path": ""}, {**SITE, "site_version": ""}, {**SITE, "site_version": "x" * 41}, "site", {},
    ):
        make_shipped(shipped_root, site=bad)
        assert store.public_url(store.get_course(KEY)) is None, bad
    make_shipped(shipped_root, site=False)
    assert store.public_url(store.get_course(KEY)) is None
    # a trailing slash on the base is tolerated, and the default paths are the site's own
    make_shipped(shipped_root, site={"base_url": BASE + "/", "site_version": "9"})
    assert store.public_url(store.get_course(KEY)) == f"{BASE}/pathways/{KEY}/"


def test_the_store_renders_caches_and_serves_no_lesson_page() -> None:
    for gone in ("ensure_rendered", "resolve_page", "is_rendered", "cache_dir", "cache_root", "course_url", "STAMP_FILENAME"):
        assert not hasattr(store, gone), gone
    assert store.SHIPPED_LABEL == "From the GigAI learning center"
    assert store.local_page_url(KEY) == f"/learning/shipped/{KEY}/pathway.html"


# ---------------------------------------------------------------------------
# 3. The assets
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
# 4. The local page
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
# 5. The rows
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
        "course": {"entry": None, "lessons": 4, "size_bytes": None, "generated_at": None},
        "url": f"{BASE}/pathways/{KEY}/", "source": "shipped", "imported_from": None, "error": None, "error_code": None, "progress": None,
        "label": "From the GigAI learning center",
        "local_page": {"state": "not_built", "built_at": None, "reasons": [], "build": None, "url": f"/learning/shipped/{KEY}/pathway.html"},
    }
    assert set(rows[1]) == ROW_KEYS | {"label", "local_page"}
    assert rows[1] == shipped_summary(home, target, store.get_course(KEY))
    assert shipped_response(home, target, store.get_course(KEY)) == {"schema_version": "scout-learning-pathway-response:1", "pathway": rows[1]}
    assert not (home / "cache" / "scout" / "learning-shipped").exists(), "listing renders nothing"
    assert str(tmp_path) not in json.dumps(body), "no path of this computer is in a row"

    page = store.pathway_page_path(home, target, KEY)
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert pathways_response(home, target)["pathways"][1]["local_page"] == {
        "state": "built", "built_at": "2027-01-15T08:00:00Z", "reasons": [], "build": None, "url": f"/learning/shipped/{KEY}/pathway.html",
    }


def test_a_v1_row_has_no_url_so_the_tab_shows_no_open_link(home_target: tuple[Path, Path], shipped_root: Path) -> None:  # noqa: F811
    home, target = home_target
    make_shipped(shipped_root, schema=1)
    row = shipped_summary(home, target, store.get_course(KEY))
    assert row["url"] is None and row["label"] == "From the GigAI learning center" and row["local_page"]["url"] == f"/learning/shipped/{KEY}/pathway.html"


def test_reading_costs_no_process_for_an_asset_and_the_project_is_named_once_for_all_shipped_rows(
    home_target: tuple[Path, Path], shipped_root: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    import subprocess

    home, target = home_target
    make_shipped(shipped_root)
    make_shipped(shipped_root, key="another-role", role="Another role")

    spawned: list[object] = []
    real_popen = subprocess.Popen.__init__

    def spy(self, *args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        return real_popen(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    assert store.asset_file("course.css") is not None and store.list_courses() and store.get_course(KEY) is not None and store.public_url(store.get_course(KEY))
    assert spawned == [], "an asset, the list of shipped courses and their public address start no process"

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
# 6. The CLI
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
    assert lines[0] == f"{KEY}  shipped  {ROLE}  (4 lessons)  [from the GigAI learning center]  your pathway page is not built yet"
    assert lines[1] == "More pathways on scout.edgelab.one: https://scout.edgelab.one/learn/"
    assert lines[2].startswith(done.id) and "[imported]" in lines[2]
    assert "gigai scout learning open ID" in lines[-1] and "ROLE_KEY" in lines[-1]
    assert _json(_run(home, target, "list", "--json")) == pathways_response(home, target)

    shown = _run(home, target, "show", KEY)
    assert shown.exit_code == 0, shown.output
    for part in (f"{KEY}  {ROLE}", "Status: done", "Course: 4 lessons", "Source: From the GigAI learning center", "scout.edgelab.one", "Your pathway page: your pathway page is not built yet", f"gigai scout learning open {KEY}"):
        assert part in shown.output, (part, shown.output)
    assert _json(_run(home, target, "show", KEY, "--json")) == shipped_response(home, target, store.get_course(KEY))

    page = store.pathway_page_path(home, target, KEY)
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert "your pathway page was built on 2027-01-15" in _run(home, target, "list").output.splitlines()[0]

    missing = _run(home, target, "show", "no-such-role", "--json")
    assert missing.exit_code == 1 and _json(missing)["error"]["code"] == "pathway_not_found"


def test_open_prints_the_public_address_and_needs_no_server_and_cancel_and_resume_refuse_a_role_key(
    home_target: tuple[Path, Path], shipped_root: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    home, target = home_target
    make_shipped(shipped_root)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    stopped = run_supervisor.ScoutStatus(state="stopped", project_id="project_x", url=None, pid=None)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: stopped)
    address = f"{BASE}/pathways/{KEY}/"

    printed = _run(home, target, "open", KEY, "--no-browser")
    assert printed.exit_code == 0 and printed.output.strip() == address and opened == [], "no server is needed: the address is the site's"
    assert _run(home, target, "open", KEY).output.strip() == address and opened == [address]
    assert _json(_run(home, target, "open", KEY, "--json")) == {"schema_version": "scout-learning-open:1", "id": KEY, "url": address, "opened": False}
    assert opened == [address], "--json never opens a browser"

    make_shipped(shipped_root, schema=1)
    unlinked = _run(home, target, "open", KEY, "--json")
    assert unlinked.exit_code == 1 and _json(unlinked)["error"]["code"] == "course_not_linked"

    for command in ("cancel", "resume"):
        refused = _run(home, target, command, KEY, "--json")
        assert refused.exit_code == 1, refused.output
        error = _json(refused)["error"]
        assert error["code"] == "pathway_not_found" and "shipped with GigAI" in error["message"] and command in error["message"]
    assert not (home / "cache" / "scout" / "learning-shipped").exists() and learning_store.list_pathways(home, target) == []


# ---------------------------------------------------------------------------
# 7. What really ships
# ---------------------------------------------------------------------------


def test_every_course_that_ships_in_the_package_lists_holds_only_its_catalog_and_names_the_learning_center() -> None:
    packaged = store.package_shipped_dir()
    folders = sorted(entry.name for entry in packaged.iterdir() if entry.is_dir()) if packaged.is_dir() else []
    listed = store.list_courses(packaged)
    assert sorted(course.role_key for course in listed) == folders, "a folder of the package's shipped folder is not a valid shipped course"
    assert folders, "two courses ship"
    for course in listed:
        assert sorted(entry.name for entry in course.data_dir.iterdir()) == ["catalog.json"], "only the catalog ships: no course.json and no paths/"
        catalog = store.read_catalog(course)
        assert catalog["schema_version"] == learning_shipped.CATALOG_SCHEMA_VERSION
        assert catalog["site"] == SITE | {"site_version": catalog["site"]["site_version"]} and catalog["site"]["base_url"] == BASE
        assert catalog["pathway_version"] == 1 and catalog["concept_keys_version"] == 1
        assert store.public_url(course) == f"{BASE}/pathways/{course.role_key}/"
        for lesson in catalog["lessons"]:
            assert lesson["prerequisites"] == [] and lesson["depth"] is None and learning_shipped.library_id(lesson) == lesson["id"]
