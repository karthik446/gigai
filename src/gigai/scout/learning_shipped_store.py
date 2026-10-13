"""0.1.11.10 shipped lessons, slice 3 (learning center M2): the reader of the course catalogs that ship inside GigAI.

What ships is package DATA (``gigai/scout/data/learning/shipped/<role-key>/catalog.json``; the schema and its export
are ``learning_shipped.py``). The lessons and practice pages themselves are on the public learning center, which the
catalog's ``site`` block names; GigAI renders none of them and keeps no copy. This module:

* LISTS the shipped courses (:func:`list_courses`, :func:`get_course`). A folder that is not a valid catalog is left
  out (and logged), never an error: with no shipped folder at all the list is empty.
* reads a course's catalog (:func:`read_catalog`) and says where its public pages are (:func:`site_of`,
  :func:`public_url`): an address for the user's browser to open, never one this server fetches.
* names the ONE file of a shipped course that is the user's own, the locally built pathway page
  (:func:`pathway_page_path`): ``<home>/scout/<project>/learning/shipped/<role-key>/pathway.html``. It is built from
  the resume and the stored postings, so it is NOT package data and a new GigAI version does not remove it.
* names the shared asset files (:func:`asset_file`): exactly the files of the package's assets folder (the pathway
  page, and the generated and imported courses, find ``course.css`` there).

No model call, no network, no journal read. Nothing here reads a resume or a posting.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import learning_store
from .learning_shipped import CATALOG_FILENAME, CATALOG_SCHEMAS, ROLE_KEY, ROLE_KEY_CHARS_MAX, Site, catalog_site
from .learning_store import LearningError

#: What a shipped course is called wherever a generated one says ``requested`` or ``imported``.
SOURCE_SHIPPED = "shipped"
SHIPPED_LABEL = "From the GigAI learning center"
#: Where the pathway page (and every generated course) finds the shared assets and ``course.css`` (a path of the Scout server).
ASSET_BASE = "/learning/_assets"
#: The locally built page of a shipped course (``learning_render.PATHWAY_PAGE``).
PATHWAY_PAGE = "pathway.html"
PAGE_NOT_BUILT, PAGE_BUILT, PAGE_STALE = "not_built", "built", "stale"
#: Test-only: a folder of shipped courses read INSTEAD of the package's (see :func:`shipped_root`).
_TEST_ROOT_ENV = "GIGAI_SCOUT_LEARNING_SHIPPED_DIR"

_logger = logging.getLogger("gigai.scout.server")
_MEMO_LOCK = threading.Lock()
#: folder -> (the stat signature it was read at, the course or ``None``).
_COURSES: dict[str, tuple[tuple[object, ...], ShippedCourse | None]] = {}
_ASSETS: dict[str, frozenset[str]] = {}


@dataclass(frozen=True)
class ShippedCourse:
    """One shipped course, as the list needs it: its key, the role it is for and where its catalog is."""

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


def pathway_page_path(home_root: Path, target: Path, role_key: str) -> Path:
    """Where the locally built pathway page of ``role_key`` is (or will be): the project's own learning folder."""

    if not is_role_key(role_key):
        raise LearningError("invalid_value", "a role key is lower-case words joined by dashes, at most 64 characters")
    return learning_store.learning_dir(home_root, target) / SOURCE_SHIPPED / role_key / PATHWAY_PAGE


def local_page_url(role_key: str) -> str:
    """Where the Scout server serves the user's own pathway page of a shipped course (the only page of it it serves)."""

    return f"/learning/{SOURCE_SHIPPED}/{role_key}/{PATHWAY_PAGE}"


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
    catalog_file = folder / CATALOG_FILENAME
    if folder.is_symlink() or catalog_file.is_symlink():
        return None, "a link"
    catalog = _json(catalog_file)
    if not isinstance(catalog, dict) or catalog.get("schema_version") not in CATALOG_SCHEMAS:
        return None, f"{CATALOG_FILENAME} is not a {' or '.join(CATALOG_SCHEMAS)}"
    if catalog.get("role_key") != key:
        return None, "its role_key is not the folder's name"
    role = catalog.get("role")
    if not isinstance(role, str) or not role.strip() or len(role) > learning_store.ROLE_TEXT_MAX or "\n" in role:
        return None, "it names no role"
    lessons = catalog.get("lessons")
    if not isinstance(lessons, list) or not lessons or not all(isinstance(lesson, dict) and isinstance(lesson.get("id"), str) for lesson in lessons):
        return None, "it holds no lesson"
    return ShippedCourse(role_key=key, role=role.strip(), lessons=len(lessons), data_dir=folder), None


def _course_in(folder: Path) -> ShippedCourse | None:
    """The shipped course ``folder`` holds, or ``None``. Read once per process and again only when a file changed."""

    signature = (_stat(folder / CATALOG_FILENAME),)
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


# --- the catalog and the public pages ---------------------------------------------------------------------------------


def read_catalog(course: ShippedCourse) -> dict[str, Any]:
    """The catalog of ``course`` (``catalog.json``); ``LearningError`` (``invalid_shipped``) when it cannot be read."""

    found = _json(course.data_dir / CATALOG_FILENAME)
    if not isinstance(found, dict):
        raise LearningError("invalid_shipped", f"the shipped course {course.role_key} cannot be read")
    return found


def site_of(catalog: object) -> Site | None:
    """Where the public pages of a catalog are, or ``None`` (a ``:1`` catalog): then nothing is linked."""

    return catalog_site(catalog)


def public_url(course: ShippedCourse) -> str | None:
    """The public pathway page of ``course`` on the learning center, or ``None`` when its catalog names no site.

    An address for the user's browser to open. No server request is made to it, and nothing of the user is in it.
    """

    try:
        site = site_of(read_catalog(course))
    except LearningError:
        return None
    return None if site is None else site.pathway_url(course.role_key)


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
    "ShippedCourse",
    "asset_file",
    "asset_names",
    "get_course",
    "gigai_version",
    "is_role_key",
    "list_courses",
    "local_page_state",
    "local_page_url",
    "package_shipped_dir",
    "pathway_page",
    "pathway_page_in",
    "pathway_page_path",
    "public_url",
    "read_catalog",
    "shipped_root",
    "site_of",
]
