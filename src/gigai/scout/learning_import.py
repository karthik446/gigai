"""0.1.11.10 Part A: import a finished course folder into the learning-pathway store (``learning_store``).

A COURSE is a folder of static HTML somebody already generated: ``index.html``, one ``concept-<lesson>.html`` per
lesson (and usually a ``practice-<lesson>.html`` beside it), and its assets (scripts, stylesheets, images). The
import checks the whole folder first and copies nothing unless every check passes:

* ``index.html`` and at least one ``concept-*.html`` at the top;
* only files of the allowed kinds (``learning_store.ALLOWED_SUFFIXES``, and a library's ``LICENSE`` / ``NOTICE`` /
  ``COPYING``); anything else is refused by name (``course_file_kind_refused``);
* no link (symlink) anywhere, the folder itself included, and nothing that is not a plain file or a folder;
* every name ASCII letters, digits, dot, dash and underscore: no dotfile, no ``..``, no space;
* at most 2,000 files and 64 MB together, and every HTML page under 3 MB.

Then the files are copied to a temporary folder beside the courses and moved into place with one rename
(``courses/<id>/site``), and the record is written as ``done`` with ``source: imported``. The same role imported
again is a NEW pathway with a new id: nothing is ever written over silently. ``replace_id`` names the one pathway
whose course is to be replaced (its id and ``requested_at`` stay, its revision goes one on).

The pages are not changed and not read for meaning. One thing is counted for the user: the pages that hold an
inline ``<script>`` block, because the server sends every course page with ``script-src 'self'`` and such a block
does not run (``find_jobs/api/learning.py``). No model call, no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from . import learning_store
from .learning_store import Course, LearningError, Pathway

MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 2000
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_DEPTH = 12
_LESSON = re.compile(r"concept-[A-Za-z0-9._-]+\.html\Z")
_INLINE_SCRIPT = re.compile(rb"<script(?![^>]*\bsrc\s*=)[^>]*>\s*(?!</script)\S", re.IGNORECASE)
_COPY_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class CourseScan:
    """What a course folder holds, once it passed every check: ``files`` are ``(path below the folder, bytes)``."""

    files: tuple[tuple[str, int], ...]
    lessons: int
    size_bytes: int
    inline_script_pages: int


@dataclass(frozen=True)
class ImportResult:
    pathway: Pathway
    replaced: bool
    files: int
    #: Pages with an inline script block: the server's Content-Security-Policy does not run those.
    inline_script_pages: int

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "scout-learning-import:1",
            "pathway": self.pathway.to_json(),
            "replaced": self.replaced,
            "files": self.files,
            "inline_script_pages": self.inline_script_pages,
        }


def _refuse_name(relative: str, name: str) -> None:
    if not learning_store.is_safe_name(name):
        raise LearningError(
            "course_name_refused",
            f"the course holds a name that is not allowed: {relative!r} (letters, digits, dot, dash and underscore only; no leading dot, no '..')",
        )


def _has_inline_script(path: Path) -> bool:
    try:
        return _INLINE_SCRIPT.search(path.read_bytes()) is not None
    except OSError:
        return False


def scan_course(src_dir: Path) -> CourseScan:
    """Check the course folder ``src_dir`` whole; a ``LearningError`` names the first thing that is not allowed."""

    root = Path(src_dir)
    if root.is_symlink():
        raise LearningError("course_symlink", "the course folder is a link; give the folder itself")
    if not root.is_dir():
        raise LearningError("course_not_a_folder", "the course must be a folder that holds index.html")
    files: list[tuple[str, int]] = []
    total = inline = 0
    pending: list[tuple[Path, str, int]] = [(root, "", 0)]
    while pending:
        folder, prefix, depth = pending.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError as exc:
            raise LearningError("course_unreadable", f"the course folder could not be read: {prefix or '.'}") from exc
        for entry in entries:
            relative = f"{prefix}{entry.name}"
            if entry.is_symlink():
                raise LearningError("course_symlink", f"the course holds a link, which is never followed: {relative!r}")
            _refuse_name(relative, entry.name)
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                if depth + 1 > MAX_DEPTH:
                    raise LearningError("course_too_deep", f"the course has folders more than {MAX_DEPTH} deep: {relative!r}")
                pending.append((Path(entry.path), f"{relative}/", depth + 1))
                continue
            if not stat.S_ISREG(mode) or not learning_store.is_allowed_kind(entry.name):
                raise LearningError(
                    "course_file_kind_refused",
                    f"the course holds a file of a kind that is not allowed: {relative!r} (allowed: "
                    f"{' '.join(sorted(learning_store.ALLOWED_SUFFIXES))}, and LICENSE, NOTICE, COPYING)",
                )
            size = entry.stat(follow_symlinks=False).st_size
            if entry.name.lower().endswith(".html"):
                if size >= MAX_HTML_BYTES:
                    raise LearningError("course_html_too_large", f"a page of the course is 3 MB or more: {relative!r}")
                inline += _has_inline_script(Path(entry.path))
            files.append((relative, size))
            total += size
            if len(files) > MAX_FILES:
                raise LearningError("course_too_many_files", f"the course holds more than {MAX_FILES} files")
            if total > MAX_TOTAL_BYTES:
                raise LearningError("course_too_large", "the course is larger than 64 MB")
    names = {relative for relative, _size in files}
    if learning_store.COURSE_ENTRY not in names:
        raise LearningError("course_index_missing", "the course has no index.html at its top")
    lessons = sum(1 for relative in names if _LESSON.match(relative))
    if not lessons:
        raise LearningError("course_no_lessons", "the course has no lesson page (concept-<lesson>.html) at its top")
    return CourseScan(files=tuple(sorted(files)), lessons=lessons, size_bytes=total, inline_script_pages=inline)


def _copy_file(source: Path, destination: Path, expected: int) -> None:
    """Copy one plain file, never through a link, and never more bytes than the scan counted."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise LearningError("course_changed", f"the course changed while it was imported: {source.name!r}") from exc
    with os.fdopen(descriptor, "rb") as reader:
        if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
            raise LearningError("course_changed", f"the course changed while it was imported: {source.name!r}")
        copied = 0
        with destination.open("xb") as writer:
            while chunk := reader.read(_COPY_CHUNK):
                copied += len(chunk)
                if copied > expected:
                    raise LearningError("course_changed", f"the course changed while it was imported: {source.name!r}")
                writer.write(chunk)
    if copied != expected:
        raise LearningError("course_changed", f"the course changed while it was imported: {source.name!r}")


def _shown_name(src_dir: Path) -> str | None:
    """The folder's own name, for display: never a path. ``None`` when the name is not plain or looks like contact data."""

    name = Path(src_dir).resolve(strict=False).name
    if not name or len(name) > 80 or not learning_store.is_safe_name(name):
        return None
    from .story_bank import personal_info_in_answer

    return None if personal_info_in_answer(name) else name


def import_course(
    home_root: Path, target: Path, src_dir: Path, role_text: str, cost: object = None, *, replace_id: str | None = None,
    finish: Callable[[Pathway], Pathway] | None = None,
) -> ImportResult:
    """Import the course folder ``src_dir`` for ``role_text``; the stored pathway is ``done`` with ``source: imported``.

    Without ``replace_id`` this is always a NEW pathway (a new id), also for a role imported before. With it, that
    pathway's course is replaced: ``pathway_not_found`` when there is none, ``pathway_busy`` while it is ``running``.
    Raises ``LearningError`` and stores nothing when a check of the module's docstring fails.

    ``finish`` (0.1.11.10 G5) is the job that GENERATED the course storing it under its own pathway: it is handed
    the ``done`` record just before the one write and answers the record to store (``source: requested``, its
    progress), so no reader ever sees a generated course as an imported one. The job owns the record, so its
    ``running`` status does not refuse the import. ``None``: an import, exactly as above.
    """

    home_root, target = Path(home_root), Path(target)
    text = learning_store.clean_role_text(role_text)
    clean_cost = learning_store.clean_cost(cost)
    if replace_id is not None and not learning_store.is_pathway_id(replace_id):
        raise LearningError("pathway_not_found", f"there is no learning pathway {replace_id!r}")
    scan = scan_course(src_dir)
    courses = learning_store.courses_dir(home_root, target)
    if learning_store.learning_dir(home_root, target).is_symlink() or courses.is_symlink():
        raise LearningError("course_symlink", "the learning folder of this project is a link; nothing is written through a link")
    courses.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", suffix=".tmp", dir=courses))
    try:
        for relative, size in scan.files:
            _copy_file(Path(src_dir).joinpath(*relative.split("/")), staging.joinpath("site", *relative.split("/")), size)
        stamp = learning_store.now_text()
        with learning_store.write_lock(home_root, target):
            current = None
            if replace_id is not None:
                current = learning_store.get_pathway(home_root, target, replace_id)
                if current is None:
                    raise LearningError("pathway_not_found", f"there is no learning pathway {replace_id!r}")
                if current.status == "running" and finish is None:
                    raise LearningError("pathway_busy", f"learning pathway {replace_id} is being generated now; replace it when that ended")
                pathway_id = replace_id
            else:
                pathway_id = learning_store.new_id()
                while learning_store.get_pathway(home_root, target, pathway_id) is not None or (courses / pathway_id).exists():
                    pathway_id = learning_store.new_id()
            course = Course(entry=learning_store.COURSE_ENTRY, lessons=scan.lessons, size_bytes=scan.size_bytes, generated_at=None)
            if current is None:
                record = Pathway(
                    id=pathway_id, role_text=text, requested_at=stamp, updated_at=stamp, status="done", cost=clean_cost, course=course,
                    source="imported", imported_from=_shown_name(Path(src_dir)), error=None, revision=1,
                )
            else:
                record = replace(
                    current, role_text=text, updated_at=stamp, status="done", cost=clean_cost, course=course, source="imported",
                    imported_from=_shown_name(Path(src_dir)), error=None, revision=current.revision + 1,
                )
            if finish is not None:
                record = finish(record)
            final = courses / pathway_id
            old = None
            if final.is_symlink():
                raise LearningError("course_symlink", "the stored course is a link; nothing is written through a link")
            if final.exists():
                old = Path(tempfile.mkdtemp(prefix=".old-", suffix=".tmp", dir=courses)) / "course"
                os.rename(final, old)
            try:
                os.rename(staging, final)  # one rename: the course is there whole, or not at all
                learning_store.write_pathway(home_root, target, record)
            except BaseException:  # noqa: BLE001 - whatever stopped the move, the old course is put back; then it is raised again
                if final.exists():
                    shutil.rmtree(final, ignore_errors=True)
                if old is not None:
                    os.rename(old, final)
                raise
            if old is not None:
                shutil.rmtree(old.parent, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ImportResult(pathway=record, replaced=current is not None, files=len(scan.files), inline_script_pages=scan.inline_script_pages)


__all__ = ["CourseScan", "ImportResult", "MAX_FILES", "MAX_HTML_BYTES", "MAX_TOTAL_BYTES", "import_course", "scan_course"]
