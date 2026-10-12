"""0.1.11.10 shipped lessons, slice 3: the reader of the courses that ship inside GigAI, and their page cache.

A shipped course is package DATA (``gigai/scout/data/learning/shipped/<role-key>/``: ``course.json``,
``catalog.json``, ``paths/<lesson>.json``; the schema and its export are ``learning_shipped.py``). It is generic:
the same for every user. This module:

* LISTS the shipped courses (:func:`list_courses`, :func:`get_course`). A folder that is not a valid shipped
  course is left out (and logged), never an error: with no shipped folder at all the list is empty.
* RENDERS a course's pages on first use (:func:`ensure_rendered`) into
  ``<home>/cache/scout/learning-shipped/<gigai-version>/<role-key>/`` with ``learning_render.render_shipped``; the
  pages find the shared assets at :data:`ASSET_BASE`. A render is kept until the GigAI version, the course's data
  or the renderer changes (a stamp file in the folder says what it was made from). The folder is replaced whole:
  the pages are written to a temporary folder beside it, which is then renamed into place. It is a cache: deleting
  it costs one render (a fifth of a second).
* names the ONE file of a shipped course that is not generic, the locally built pathway page
  (:func:`pathway_page_path`): ``<home>/scout/<project>/learning/shipped/<role-key>/pathway.html``. It is the
  user's own (built from the resume and the stored postings by a later slice), so it is NOT in the cache folder
  and a new GigAI version does not remove it. A ``pathway.html`` found in a cache folder is still never touched:
  it is carried over a re-render.
* names the shared asset files (:func:`asset_file`): exactly the files of the package's assets folder.

No model call, no network, no journal read. Nothing here reads a resume or a posting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import learning_store
from .learning_shipped import (
    CATALOG_FILENAME,
    CATALOG_SCHEMA_VERSION,
    COURSE_FILENAME,
    PATHS_DIRNAME,
    ROLE_KEY,
    ROLE_KEY_CHARS_MAX,
    SCHEMA_VERSION,
    Shipped,
    check_shipped,
)
from .learning_store import COURSE_ENTRY, LearningError

#: What a shipped course is called wherever a generated one says ``requested`` or ``imported``.
SOURCE_SHIPPED = "shipped"
SHIPPED_LABEL = "Shipped with GigAI"
#: Where the pages of every shipped course find the shared assets and ``course.css`` (a path of the Scout server).
ASSET_BASE = "/learning/_assets"
#: The locally built page of a shipped course (the renderer's links to it are ``learning_render.PATHWAY_PAGE``).
PATHWAY_PAGE = "pathway.html"
PAGE_NOT_BUILT, PAGE_BUILT, PAGE_STALE = "not_built", "built", "stale"
STAMP_FILENAME = ".render.json"
STAMP_SCHEMA = "scout-learning-shipped-render:1"
#: Test-only: a folder of shipped courses read INSTEAD of the package's (see :func:`shipped_root`).
_TEST_ROOT_ENV = "GIGAI_SCOUT_LEARNING_SHIPPED_DIR"
#: A temporary or replaced folder of a render that died is removed once it is this old.
_LEFTOVER_SECONDS = 3600.0

_logger = logging.getLogger("gigai.scout.server")
_RENDER_LOCK = threading.Lock()
_MEMO_LOCK = threading.Lock()
#: folder -> (the stat signature it was read at, the course or ``None``).
_COURSES: dict[str, tuple[tuple[object, ...], ShippedCourse | None]] = {}
#: folder -> (the stat signature it was read at, the digest of what a render reads).
_DIGESTS: dict[str, tuple[tuple[object, ...], str]] = {}
_ASSETS: dict[str, frozenset[str]] = {}


@dataclass(frozen=True)
class ShippedCourse:
    """One shipped course, as the list needs it: its key, the role it is for and where its data is."""

    role_key: str
    role: str
    lessons: int
    data_dir: Path


# --- where things are ------------------------------------------------------------------------------------------------


def package_shipped_dir() -> Path:
    """The shipped courses of the installed package (``importlib.resources``); the folder may not exist."""

    from importlib import resources

    return Path(str(resources.files("gigai.scout") / "data" / "learning" / "shipped"))


def shipped_root() -> Path:
    """Where the shipped courses are read from: the package's folder.

    Test-only: the folder ``GIGAI_SCOUT_LEARNING_SHIPPED_DIR`` names, read only while BOTH of ``bindings.py``'s
    test seams are on (a server refuses to start with them unless it was started with ``--allow-test-seams``).
    """

    named = os.environ.get(_TEST_ROOT_ENV)
    if named:
        from .find_jobs import bindings

        if bindings._test_model_enabled() and bindings._test_http_enabled():
            return Path(named)
    return package_shipped_dir()


def gigai_version() -> str:
    """The installed GigAI version, the name of the cache folder (``dev`` for a source tree that is not installed)."""

    import importlib.metadata

    try:
        return importlib.metadata.version("gigai")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def is_role_key(value: object) -> bool:
    """Whether ``value`` may name a shipped course. Never a pathway id (``lp-`` and 8 hex digits): the two cannot collide."""

    return isinstance(value, str) and ROLE_KEY.fullmatch(value) is not None and len(value) <= ROLE_KEY_CHARS_MAX and not learning_store.is_pathway_id(value)


def cache_root(home_root: Path) -> Path:
    return Path(home_root) / "cache" / "scout" / "learning-shipped"


def cache_dir(home_root: Path, role_key: str, version: str | None = None) -> Path:
    """Where the rendered pages of ``role_key`` are for this GigAI version."""

    if not is_role_key(role_key):
        raise LearningError("invalid_value", "a role key is lower-case words joined by dashes, at most 64 characters")
    name = version if version is not None else gigai_version()
    if not learning_store.is_safe_name(name):
        name = "dev"
    return cache_root(home_root) / name / role_key


def pathway_page_path(home_root: Path, target: Path, role_key: str) -> Path:
    """Where the locally built pathway page of ``role_key`` is (or will be): the project's own learning folder."""

    if not is_role_key(role_key):
        raise LearningError("invalid_value", "a role key is lower-case words joined by dashes, at most 64 characters")
    return learning_store.learning_dir(home_root, target) / SOURCE_SHIPPED / role_key / PATHWAY_PAGE


def course_url(role_key: str, page: str = COURSE_ENTRY) -> str:
    """Where the Scout server serves a page of a shipped course."""

    return f"/learning/{SOURCE_SHIPPED}/{role_key}/{page}"


# --- the list --------------------------------------------------------------------------------------------------------


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stat(path: Path) -> tuple[object, ...]:
    try:
        found = path.stat()
    except OSError:
        return (path.name, None)
    return (path.name, found.st_size, found.st_mtime_ns)


def _problem(folder: Path) -> tuple[ShippedCourse | None, str | None]:
    """The course of ``folder`` and ``None``, or ``None`` and what is wrong with it."""

    key = folder.name
    course_file, catalog_file = folder / COURSE_FILENAME, folder / CATALOG_FILENAME
    if folder.is_symlink() or course_file.is_symlink() or catalog_file.is_symlink():
        return None, "a link"
    course, catalog = _json(course_file), _json(catalog_file)
    if not isinstance(course, dict) or course.get("schema_version") != SCHEMA_VERSION:
        return None, f"{COURSE_FILENAME} is not a {SCHEMA_VERSION}"
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        return None, f"{CATALOG_FILENAME} is not a {CATALOG_SCHEMA_VERSION}"
    if course.get("role_key") != key or catalog.get("role_key") != key:
        return None, "its role_key is not the folder's name"
    role = course.get("role")
    if not isinstance(role, str) or not role.strip() or len(role) > learning_store.ROLE_TEXT_MAX or "\n" in role:
        return None, "it names no role"
    modules = course.get("modules")
    if not isinstance(modules, list) or not all(isinstance(module, dict) and isinstance(module.get("concepts"), list) for module in modules):
        return None, "it holds no modules"
    lessons = sum(len(module["concepts"]) for module in modules)
    if lessons < 1:
        return None, "it holds no lesson"
    return ShippedCourse(role_key=key, role=role.strip(), lessons=lessons, data_dir=folder), None


def _course_in(folder: Path) -> ShippedCourse | None:
    """The shipped course ``folder`` holds, or ``None``. Read once per process and again only when a file changed."""

    signature = (_stat(folder / COURSE_FILENAME), _stat(folder / CATALOG_FILENAME))
    key = str(folder)
    with _MEMO_LOCK:
        known = _COURSES.get(key)
    if known is not None and known[0] == signature:
        return known[1]
    course, problem = _problem(folder)
    if problem is not None:
        _logger.warning("the shipped course %s is left out: %s", folder.name, problem)
    with _MEMO_LOCK:
        _COURSES[key] = (signature, course)
    return course


def list_courses(root: Path | None = None) -> list[ShippedCourse]:
    """Every shipped course, by role. Empty when the folder is missing; a folder that is not a course is left out."""

    base = shipped_root() if root is None else Path(root)
    try:
        folders = [entry for entry in base.iterdir() if is_role_key(entry.name) and entry.is_dir()]
    except OSError:
        return []
    found = [course for course in (_course_in(folder) for folder in folders) if course is not None]
    return sorted(found, key=lambda course: (course.role.casefold(), course.role_key))


def get_course(role_key: object, root: Path | None = None) -> ShippedCourse | None:
    """The shipped course of ``role_key``, or ``None`` (also for a text that is not a role key)."""

    if not is_role_key(role_key):
        return None
    folder = (shipped_root() if root is None else Path(root)) / str(role_key)
    return _course_in(folder) if folder.is_dir() else None


# --- the page cache --------------------------------------------------------------------------------------------------


def _render_inputs(data_dir: Path) -> list[Path]:
    """The files a render reads: ``course.json`` and every ``paths/*.json`` (``catalog.json`` draws no page)."""

    try:
        paths = sorted(entry for entry in (data_dir / PATHS_DIRNAME).iterdir() if entry.name.endswith(".json") and entry.is_file())
    except OSError:
        paths = []
    return [data_dir / COURSE_FILENAME, *paths]


def _digest(course: ShippedCourse) -> str:
    """A digest of everything the pages are made from: the course's data, the renderer and where the assets are."""

    from . import learning_render

    inputs = _render_inputs(course.data_dir)
    renderer = Path(learning_render.__file__)
    signature = tuple(_stat(path) for path in (*inputs, renderer))
    key = str(course.data_dir)
    with _MEMO_LOCK:
        known = _DIGESTS.get(key)
    if known is not None and known[0] == signature:
        return known[1]
    digest = hashlib.sha256(ASSET_BASE.encode("utf-8"))
    for path in (*inputs, renderer):
        digest.update(b"\0" + path.name.encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    value = digest.hexdigest()
    with _MEMO_LOCK:
        _DIGESTS[key] = (signature, value)
    return value


def _stamp(course: ShippedCourse, version: str) -> dict[str, object]:
    return {"schema_version": STAMP_SCHEMA, "gigai_version": version, "role_key": course.role_key, "digest": _digest(course)}


def _check_data(course: ShippedCourse) -> None:
    """The shipped schema's own check on the whole folder (``learning_shipped.check_shipped``) before a render."""

    data = course.data_dir
    paths: dict[str, dict[str, Any]] = {}
    for path in _render_inputs(data)[1:]:
        found = _json(path)
        if not isinstance(found, dict):
            raise LearningError("invalid_shipped", f"the shipped course {course.role_key} holds a practice path that is not JSON: {path.name}")
        paths[path.stem] = found
    shipped = Shipped(course=_json(data / COURSE_FILENAME) or {}, catalog=_json(data / CATALOG_FILENAME) or {}, paths=paths, removed={})
    problems = check_shipped(shipped)
    if problems:
        raise LearningError("invalid_shipped", f"the shipped course {course.role_key} is not valid: " + "; ".join(problems[:5]))


def _remove(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def _tidy(final: Path) -> None:
    """Remove what older renders left: this course's folder of another GigAI version, and dead temporary folders.

    A folder that holds a ``pathway.html`` is the user's and is never removed.
    """

    root, current = final.parent.parent, final.parent
    now = time.time()
    try:
        versions = list(root.iterdir())
    except OSError:
        return
    for version in versions:
        if version.is_symlink() or not version.is_dir():
            continue
        try:
            entries = list(version.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            leftover = entry.name.startswith(f".{final.name}.") and entry.name.endswith((".tmp", ".old"))
            other_version = version != current and entry.name == final.name
            if not (leftover or other_version) or (entry / PATHWAY_PAGE).exists():
                continue
            try:
                if leftover and now - entry.stat().st_mtime < _LEFTOVER_SECONDS:
                    continue
            except OSError:
                continue
            _remove(entry)
        if version != current:
            try:
                version.rmdir()  # only when nothing is left in it
            except OSError:
                pass


def _render(final: Path, course: ShippedCourse, stamp: dict[str, object]) -> None:
    from . import learning_render

    _check_data(course)
    final.parent.mkdir(parents=True, exist_ok=True)
    mark = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    fresh, old = final.parent / f".{final.name}.{mark}.tmp", final.parent / f".{final.name}.{mark}.old"
    try:
        learning_render.render_shipped(course.data_dir, fresh, ASSET_BASE)
        (fresh / STAMP_FILENAME).write_text(json.dumps(stamp, sort_keys=True), encoding="utf-8")
        replaced = False
        if final.is_symlink():
            final.unlink()
        elif final.exists():
            os.replace(final, old)
            replaced = True
        os.replace(fresh, final)
        if replaced:
            kept = old / PATHWAY_PAGE
            if kept.is_file() and not kept.is_symlink():
                os.replace(kept, final / PATHWAY_PAGE)
            _remove(old)
    finally:
        if fresh.exists():
            _remove(fresh)
    _tidy(final)


def is_rendered(home_root: Path, course: ShippedCourse, *, version: str | None = None) -> bool:
    """Whether the cache holds this course's pages as this version, this data and this renderer make them."""

    used = version if version is not None else gigai_version()
    return _json(cache_dir(home_root, course.role_key, used) / STAMP_FILENAME) == _stamp(course, used)


def ensure_rendered(home_root: Path, course: ShippedCourse, *, version: str | None = None) -> Path:
    """The folder of ``course``'s rendered pages, rendering them first when the cache does not hold them.

    Raises ``learning_render.CourseError`` or :class:`LearningError` (``invalid_shipped``) for a course that does
    not render, ``OSError`` when the cache cannot be written; a folder already there is then left as it was.
    """

    used = version if version is not None else gigai_version()
    final = cache_dir(home_root, course.role_key, used)
    stamp = _stamp(course, used)
    if _json(final / STAMP_FILENAME) == stamp:
        return final
    with _RENDER_LOCK:
        if _json(final / STAMP_FILENAME) != stamp:
            try:
                _render(final, course, stamp)
            except OSError:
                # Another process may have rendered the same thing at the same moment: its folder is as good.
                if _json(final / STAMP_FILENAME) != stamp:
                    raise
    return final


def resolve_page(home_root: Path, role_key: str, page: str, *, root: Path | None = None) -> Path | None:
    """The rendered page ``page`` of the shipped course ``role_key`` (``""`` is the entry page), or ``None``.

    Only a plain ``.html`` file directly in the course's cache folder: never a path with a ``/``, a dotfile, a
    link, nor ``pathway.html`` (that one is the user's: :func:`pathway_page`). ``None`` too for an unknown course
    and for one that does not render (logged).
    """

    from .learning_render import CourseError

    course = get_course(role_key, root)
    name = page or COURSE_ENTRY
    if course is None or not learning_store.is_safe_name(name) or not name.endswith(".html") or name == PATHWAY_PAGE:
        return None
    try:
        site = ensure_rendered(home_root, course)
    except (CourseError, LearningError, OSError) as exc:
        _logger.warning("the shipped course %s could not be rendered: %s", course.role_key, exc)
        return None
    candidate = site / name
    return candidate if not candidate.is_symlink() and candidate.is_file() else None


# --- the local page --------------------------------------------------------------------------------------------------


def pathway_page_in(learning: Path, role_key: str) -> Path | None:
    """:func:`pathway_page` for a project whose learning folder (``learning_store.learning_dir``) is already known.

    Naming the project is the costly part (a ``git`` process): a caller that asks about several courses names it once.
    """

    if not is_role_key(role_key):
        return None
    page = Path(learning) / SOURCE_SHIPPED / role_key / PATHWAY_PAGE
    if any(part.is_symlink() for part in (Path(learning), page.parent.parent, page.parent, page)):
        return None
    return page if page.is_file() else None


def pathway_page(home_root: Path, target: Path, role_key: str) -> Path | None:
    """The locally built pathway page of ``role_key`` when there is one (a plain file, no link on the way)."""

    try:
        return pathway_page_in(learning_store.learning_dir(home_root, target), role_key)
    except LearningError:
        return None


def local_page_state(home_root: Path, target: Path, role_key: str, *, learning: Path | None = None) -> dict[str, object]:
    """``{"state", "built_at"}`` of the local pathway page: ``not_built``, ``built`` or ``stale``, and when it was built.

    The rule is the page builder's (``learning_pathway_local.state``). Asked from here it costs two small file
    reads: ``stale`` is said for a page built by another GigAI version or from another shipped catalog. No resume
    is read and no posting is counted here; a caller that has the master's revision or a fresh count asks the
    builder directly. A page with no record of its build is ``built`` with its file time.
    ``learning``: the project's learning folder when the caller already named it.
    """

    from . import learning_pathway_local  # imported here: that module imports this one

    found = learning_pathway_local.state(home_root, target, role_key, learning=learning)
    return {"state": found["state"], "built_at": found["built_at"]}


# --- the shared assets -----------------------------------------------------------------------------------------------


def asset_names(root: Path | None = None) -> frozenset[str]:
    """The files of the package's assets folder, as paths below it (``course.css``, ``hljs/highlight.min.js``).

    This listing IS the whitelist of ``GET /learning/_assets/...``. Listed once per process.
    """

    from .learning_render import package_assets_dir

    base = package_assets_dir() if root is None else Path(root)
    key = str(base)
    with _MEMO_LOCK:
        known = _ASSETS.get(key)
    if known is not None:
        return known
    names: set[str] = set()
    for folder, folders, files in os.walk(base, followlinks=False):
        folders[:] = sorted(name for name in folders if learning_store.is_safe_name(name) and not (Path(folder) / name).is_symlink())
        for name in files:
            path = Path(folder) / name
            if learning_store.is_safe_name(name) and learning_store.is_allowed_kind(name) and not path.is_symlink() and path.is_file():
                names.add(path.relative_to(base).as_posix())
    found = frozenset(names)
    with _MEMO_LOCK:
        _ASSETS[key] = found
    return found


def asset_file(relpath: str, root: Path | None = None) -> Path | None:
    """The shared asset ``relpath`` names, or ``None``: only a name that is, character for character, in the listing."""

    from .learning_render import package_assets_dir

    if relpath not in asset_names(root):
        return None
    return (package_assets_dir() if root is None else Path(root)).joinpath(*relpath.split("/"))


__all__ = [
    "ASSET_BASE",
    "PAGE_BUILT",
    "PAGE_NOT_BUILT",
    "PAGE_STALE",
    "PATHWAY_PAGE",
    "SHIPPED_LABEL",
    "SOURCE_SHIPPED",
    "STAMP_FILENAME",
    "ShippedCourse",
    "asset_file",
    "asset_names",
    "cache_dir",
    "cache_root",
    "course_url",
    "ensure_rendered",
    "get_course",
    "gigai_version",
    "is_rendered",
    "is_role_key",
    "list_courses",
    "local_page_state",
    "package_shipped_dir",
    "pathway_page",
    "pathway_page_in",
    "pathway_page_path",
    "resolve_page",
    "shipped_root",
]
