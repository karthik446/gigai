"""0.1.11.10 Part A: the store of learning pathways (a course per role, as static HTML).

A PATHWAY is one role the user asked a course for ("MLOps", "Forward deployed engineer"). Its record says what was
asked, how far it is (``queued | running | done | failed``), what it cost, and, once ``done``, where its COURSE is: a
folder of static HTML the Scout server serves (``find_jobs/api/learning.py``).

Layout, per project like the other user-level stores (``find_jobs/discovery/storage.py``)::

    <home>/scout/<project_id>/learning/records/<id>.json     one record, schema ``scout-learning-pathway:1``
    <home>/scout/<project_id>/learning/courses/<id>/site/**   the course's files, as imported

What a record does NOT hold: nothing about READING it (no reading progress, no cursor, no "completed" mark), no path
of the user's computer (``imported_from`` is a folder's own name), and no contact data (``role_text`` is checked like
every other free text: ``story_bank.personal_info_in_answer``).

0.1.11.10 Part B (G5): a record a course is GENERATED for may also hold ``progress`` (how far the generation is:
its steps, the model calls and page fetches so far, the caps, what was dropped and why; never how far anybody read)
and ``error_code`` (the code of ``error``). Both are optional keys: a record written before them still reads.

No model call and no network here. A record is written whole and atomically (``storage.atomic_write``) under a lock
on the records folder. A link (symlink) is never followed: not for a record, not for any part of a course tree.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
from typing import Iterator, Mapping

from .find_jobs.discovery.storage import atomic_write, project_id

SCHEMA = "scout-learning-pathway:1"
STATUSES = ("queued", "running", "done", "failed")
SOURCES = ("requested", "imported")
ROLE_TEXT_MAX = 200
NOTE_MAX = 300
COURSE_ENTRY = "index.html"
#: The kinds of file a course may hold, and the only kinds the server sends.
ALLOWED_SUFFIXES = frozenset({".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".gif", ".webp", ".woff", ".woff2", ".txt", ".map", ".md"})
#: Files with no extension a course may also hold: the licence notices of the libraries it ships. Sent as plain text.
ALLOWED_BARE_NAMES = frozenset({"LICENSE", "NOTICE", "COPYING"})
#: The keys of ``cost``; each is ``None`` when it is not known. ``tokens`` (0.1.11.10 G7d): the job's own meter total
#: at the end of a generated course, additive (a record written before it reads it as ``None``); the note keeps its
#: wording (``"about Nk tokens"``) and is not parsed back out of it.
COST_KEYS = ("cli_model_calls", "worker_minutes", "web_fetches", "web_searches", "tokens", "note")
_RECORD_KEYS = frozenset(
    {"schema", "id", "role_text", "requested_at", "updated_at", "status", "cost", "course", "source", "imported_from", "error", "revision"}
)
#: Keys a record may hold besides ``_RECORD_KEYS`` (G5); one written before them has neither.
_OPTIONAL_KEYS = frozenset({"progress", "error_code"})
_COURSE_KEYS = frozenset({"entry", "lessons", "size_bytes", "generated_at"})
#: ``progress``: the generation of a course, never the reading of it.
PROGRESS_KEYS = ("step", "steps", "calls", "fetches", "max_calls", "max_fetches", "dropped")
PROGRESS_STEP_KEYS = ("id", "status", "started_at", "finished_at", "detail")
PROGRESS_STEP_STATUSES = ("pending", "running", "done", "skipped", "failed")
PROGRESS_STEPS_MAX = 20
PROGRESS_DROPPED_MAX = 200
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_ID = re.compile(r"lp-[0-9a-f]{8}\Z")
#: One part of a course path: ASCII letters, digits, dot, dash and underscore; never a leading dot.
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\Z")


class LearningError(ValueError):
    """A learning-pathway call was refused; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Course:
    """What a ``done`` pathway serves: ``entry`` is the page to open, ``lessons`` the number of lesson pages."""

    entry: str
    lessons: int
    size_bytes: int
    generated_at: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"entry": self.entry, "lessons": self.lessons, "size_bytes": self.size_bytes, "generated_at": self.generated_at}


@dataclass(frozen=True)
class Pathway:
    id: str
    role_text: str
    requested_at: str
    updated_at: str
    status: str
    cost: Mapping[str, object]
    course: Course | None
    source: str
    imported_from: str | None
    error: str | None
    revision: int
    progress: Mapping[str, object] | None = None
    error_code: str | None = None

    def to_json(self) -> dict[str, object]:
        """The record as stored. ``progress`` and ``error_code`` are keys only when set: a record with neither is what it always was."""

        record: dict[str, object] = {
            "schema": SCHEMA,
            "id": self.id,
            "role_text": self.role_text,
            "requested_at": self.requested_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "cost": {key: self.cost.get(key) for key in COST_KEYS},
            "course": self.course.to_json() if self.course is not None else None,
            "source": self.source,
            "imported_from": self.imported_from,
            "error": self.error,
            "revision": self.revision,
        }
        if self.progress is not None:
            record["progress"] = clean_progress(self.progress)
        if self.error_code is not None:
            record["error_code"] = self.error_code
        return record


# --- values ----------------------------------------------------------------------------------------------------------


def now_text(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return f"lp-{secrets.token_hex(4)}"


def is_pathway_id(value: object) -> bool:
    return isinstance(value, str) and _ID.match(value) is not None


def is_safe_name(part: str) -> bool:
    """Whether ``part`` may be one part of a course path (so never ``..``, a dotfile, a slash or a non-ASCII name)."""

    return _NAME.match(part) is not None and ".." not in part


def is_allowed_kind(name: str) -> bool:
    """Whether a file called ``name`` is of a kind a course may hold (``ALLOWED_SUFFIXES``, ``ALLOWED_BARE_NAMES``)."""

    suffix = Path(name).suffix.lower()
    return suffix in ALLOWED_SUFFIXES if suffix else name in ALLOWED_BARE_NAMES


def clean_role_text(role_text: object) -> str:
    """The role as typed, ends trimmed. ``invalid_value`` (empty, over 200 characters, more than one line) or ``personal_info_refused``."""

    if not isinstance(role_text, str) or not role_text.strip():
        raise LearningError("invalid_value", "a role is required (for example: MLOps engineer)")
    text = role_text.strip()
    if len(text) > ROLE_TEXT_MAX:
        raise LearningError("invalid_value", f"a role is at most {ROLE_TEXT_MAX} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise LearningError("invalid_value", "a role is one line of plain text")
    from .story_bank import personal_info_in_answer

    found = personal_info_in_answer(text)
    if found:
        raise LearningError(
            "personal_info_refused",
            f"this role looks like it holds personal information ({', '.join(found)}); a role is a job title, never a name or "
            "contact details. Remove it and try again",
        )
    return text


def _count(value: object, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LearningError("invalid_value", f"cost.{key} must be a whole number, 0 or more, or null")
    return value


def clean_cost(cost: object) -> dict[str, object]:
    """``cost`` with every key present (``None`` when not known). ``invalid_value`` for an unknown key or a wrong type."""

    if cost is None:
        cost = {}
    if not isinstance(cost, Mapping):
        raise LearningError("invalid_value", "cost must be a JSON object")
    unknown = sorted(str(key) for key in set(cost) - set(COST_KEYS))
    if unknown:
        raise LearningError("invalid_value", f"cost has unknown keys: {', '.join(unknown)} (allowed: {', '.join(COST_KEYS)})")
    minutes = cost.get("worker_minutes")
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes < 0 or minutes != minutes):
        raise LearningError("invalid_value", "cost.worker_minutes must be a number, 0 or more, or null")
    note = cost.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > NOTE_MAX or any(ord(char) < 32 for char in note):
            raise LearningError("invalid_value", f"cost.note must be one line of at most {NOTE_MAX} characters, or null")
        from .story_bank import personal_info_in_answer

        if personal_info_in_answer(note):
            raise LearningError("personal_info_refused", "cost.note looks like it holds personal information; remove it and try again")
    return {
        "cli_model_calls": _count(cost.get("cli_model_calls"), "cli_model_calls"),
        "worker_minutes": minutes,
        "web_fetches": _count(cost.get("web_fetches"), "web_fetches"),
        "web_searches": _count(cost.get("web_searches"), "web_searches"),
        "tokens": _count(cost.get("tokens"), "tokens"),
        "note": note or None,
    }


def _line(value: object, limit: int, what: str) -> str:
    """``value`` as one line of plain text that passes the contact check, else ``invalid_value`` / ``personal_info_refused``."""

    if not isinstance(value, str) or not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise LearningError("invalid_value", f"{what} must be one line of at most {limit} characters")
    from .story_bank import personal_info_in_answer

    if personal_info_in_answer(value):
        raise LearningError("personal_info_refused", f"{what} looks like it holds personal information")
    return value


def clean_progress(progress: object) -> dict[str, object] | None:
    """``progress`` (the GENERATION of a course) key for key, or ``None``. ``invalid_value`` for anything else.

    ``{step, steps: [{id, status, started_at, finished_at, detail}], calls, fetches, max_calls, max_fetches,
    dropped: [{what, reason}]}``. Every text is one line and passes the contact check.
    """

    if progress is None:
        return None
    if not isinstance(progress, Mapping) or set(progress) != set(PROGRESS_KEYS):
        raise LearningError("invalid_value", f"progress must hold exactly: {', '.join(PROGRESS_KEYS)}")
    step, steps, dropped = progress["step"], progress["steps"], progress["dropped"]
    if not (step is None or (isinstance(step, str) and _CODE.match(step))):
        raise LearningError("invalid_value", "progress.step must be a step id or null")
    if not isinstance(steps, list) or len(steps) > PROGRESS_STEPS_MAX or not isinstance(dropped, list):
        raise LearningError("invalid_value", "progress.steps and progress.dropped must be lists")
    clean_steps = []
    for item in steps:
        if not isinstance(item, Mapping) or set(item) != set(PROGRESS_STEP_KEYS):
            raise LearningError("invalid_value", f"a progress step must hold exactly: {', '.join(PROGRESS_STEP_KEYS)}")
        if not (isinstance(item["id"], str) and _CODE.match(item["id"])) or item["status"] not in PROGRESS_STEP_STATUSES:
            raise LearningError("invalid_value", "a progress step needs an id and a known status")
        for key in ("started_at", "finished_at"):
            if not (item[key] is None or (isinstance(item[key], str) and len(item[key]) <= 40)):
                raise LearningError("invalid_value", f"a progress step's {key} must be a time or null")
        detail = None if item["detail"] is None else _line(item["detail"], NOTE_MAX, "a progress step's detail")
        clean_steps.append({"id": item["id"], "status": item["status"], "started_at": item["started_at"], "finished_at": item["finished_at"], "detail": detail})
    clean_dropped = []
    for item in dropped[:PROGRESS_DROPPED_MAX]:
        if not isinstance(item, Mapping) or set(item) != {"what", "reason"}:
            raise LearningError("invalid_value", "a dropped entry must hold exactly: what, reason")
        clean_dropped.append({"what": _line(item["what"], 160, "a dropped entry's what"), "reason": _line(item["reason"], 80, "a dropped entry's reason")})
    return {
        "step": step, "steps": clean_steps, "calls": _count(progress["calls"], "calls") or 0, "fetches": _count(progress["fetches"], "fetches") or 0,
        "max_calls": _count(progress["max_calls"], "max_calls") or 0, "max_fetches": _count(progress["max_fetches"], "max_fetches") or 0,
        "dropped": clean_dropped,
    }


def _course_from_json(value: object) -> Course | None:
    if not isinstance(value, dict) or set(value) != _COURSE_KEYS:
        raise ValueError("course")
    entry, lessons, size, generated = value["entry"], value["lessons"], value["size_bytes"], value["generated_at"]
    if entry != COURSE_ENTRY or isinstance(lessons, bool) or not isinstance(lessons, int) or isinstance(size, bool) or not isinstance(size, int):
        raise ValueError("course")
    if lessons < 0 or size < 0 or not (generated is None or isinstance(generated, str)):
        raise ValueError("course")
    return Course(entry=entry, lessons=lessons, size_bytes=size, generated_at=generated)


def pathway_from_json(value: object) -> Pathway:
    """The record ``value`` holds. ``ValueError`` when it is not a ``scout-learning-pathway:1`` record, key for key."""

    if not isinstance(value, dict) or not (_RECORD_KEYS <= set(value) <= _RECORD_KEYS | _OPTIONAL_KEYS) or value["schema"] != SCHEMA:
        raise ValueError("not a learning pathway record")
    revision = value["revision"]
    error_code = value.get("error_code")
    if not (error_code is None or (isinstance(error_code, str) and _CODE.match(error_code))):
        raise ValueError("not a learning pathway record")
    texts = (value["role_text"], value["requested_at"], value["updated_at"])
    if not is_pathway_id(value["id"]) or not all(isinstance(text, str) and text for text in texts):
        raise ValueError("not a learning pathway record")
    if value["status"] not in STATUSES or value["source"] not in SOURCES or isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("not a learning pathway record")
    for optional in (value["imported_from"], value["error"]):
        if not (optional is None or isinstance(optional, str)):
            raise ValueError("not a learning pathway record")
    try:
        cost = clean_cost(value["cost"])
        progress = clean_progress(value.get("progress"))
    except LearningError as exc:
        raise ValueError("not a learning pathway record") from exc
    course = None if value["course"] is None else _course_from_json(value["course"])
    if (value["status"] == "done") != (course is not None):
        raise ValueError("not a learning pathway record")
    return Pathway(
        id=value["id"], role_text=value["role_text"], requested_at=value["requested_at"], updated_at=value["updated_at"],
        status=value["status"], cost=cost, course=course, source=value["source"], imported_from=value["imported_from"],
        error=value["error"], revision=revision, progress=progress, error_code=error_code,
    )


# --- where things are ------------------------------------------------------------------------------------------------


def learning_dir(home_root: Path, target: Path) -> Path:
    try:
        return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "learning"
    except Exception as exc:  # noqa: BLE001 - any failure to name the project is one typed refusal
        raise LearningError("target_unavailable", "this folder is not bound to a GigAI project") from exc


def records_dir(home_root: Path, target: Path) -> Path:
    return learning_dir(home_root, target) / "records"


def courses_dir(home_root: Path, target: Path) -> Path:
    return learning_dir(home_root, target) / "courses"


def course_site_dir(home_root: Path, target: Path, pathway_id: str) -> Path:
    """Where the files of ``pathway_id``'s course are. ``invalid_value`` for an id that is not ``lp-`` and 8 hex digits."""

    if not is_pathway_id(pathway_id):
        raise LearningError("invalid_value", "a pathway id is lp- and 8 hex digits")
    return courses_dir(home_root, target) / pathway_id / "site"


def _record_path(home_root: Path, target: Path, pathway_id: str) -> Path:
    return records_dir(home_root, target) / f"{pathway_id}.json"


def _read(path: Path) -> Pathway | None:
    """The record at ``path``; ``None`` for no file, a link, or a file that is not a record."""

    if path.is_symlink() or not path.is_file():
        return None
    try:
        record = pathway_from_json(json.loads(path.read_bytes().decode("utf-8")))
    except (OSError, ValueError):
        return None
    return record if path.name == f"{record.id}.json" else None


@contextmanager
def write_lock(home_root: Path, target: Path) -> Iterator[None]:
    """One writer at a time for the pathways of a project: a lock on the records folder (as ``suggestions.record_write_lock``)."""

    folder = records_dir(home_root, target)
    folder.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def write_pathway(home_root: Path, target: Path, pathway: Pathway) -> Pathway:
    """Store ``pathway`` whole, atomically. The caller holds ``write_lock`` around its read and this."""

    pathway_from_json(pathway.to_json())  # a record that would not read back is never written
    atomic_write(_record_path(home_root, target, pathway.id), json.dumps(pathway.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    return pathway


# --- the module's API ------------------------------------------------------------------------------------------------


def list_pathways(home_root: Path, target: Path) -> list[Pathway]:
    """Every stored pathway, the newest request first. Reads files only."""

    folder = records_dir(home_root, target)
    if folder.is_symlink() or not folder.is_dir():
        return []
    found = [record for record in (_read(path) for path in folder.glob("lp-*.json")) if record is not None]
    return sorted(found, key=lambda record: (record.requested_at, record.id), reverse=True)


def get_pathway(home_root: Path, target: Path, pathway_id: str) -> Pathway | None:
    """One pathway, or ``None`` (also for a text that is not a pathway id)."""

    if not is_pathway_id(pathway_id):
        return None
    return _read(_record_path(home_root, target, pathway_id))


def create_request(home_root: Path, target: Path, role_text: str, *, now: datetime | None = None) -> Pathway:
    """Store a new request for ``role_text``: ``queued``, no course yet. Nothing is generated here."""

    text = clean_role_text(role_text)
    stamp = now_text(now)
    with write_lock(home_root, target):
        pathway_id = new_id()
        while _record_path(home_root, target, pathway_id).exists():
            pathway_id = new_id()
        return write_pathway(
            home_root, target,
            Pathway(
                id=pathway_id, role_text=text, requested_at=stamp, updated_at=stamp, status="queued", cost=clean_cost(None),
                course=None, source="requested", imported_from=None, error=None, revision=1,
            ),
        )


def import_course(
    home_root: Path, target: Path, src_dir: Path, role_text: str, cost: object = None, *, replace_id: str | None = None, finish=None,
):
    """Import a finished course folder as a ``done`` pathway: ``learning_import.import_course``, which says the rules.

    Returns its ``ImportResult`` (``.pathway`` is the stored record). ``finish`` is the generating job's (G5).
    """

    from .learning_import import import_course as run_import

    return run_import(home_root, target, src_dir, role_text, cost, replace_id=replace_id, finish=finish)


def _has_link(root: Path, parts: tuple[str, ...]) -> bool:
    """Whether ``root`` or anything on the way down ``parts`` is a link."""

    current = root
    if current.is_symlink():
        return True
    for part in parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def resolve_course_file(home_root: Path, target: Path, pathway_id: str, relpath: str) -> Path | None:
    """The file ``relpath`` of ``pathway_id``'s course, or ``None``: the one gate a served course file passes.

    ``relpath`` is the request's path below the course, already percent-decoded (``""`` is the entry page). ``None``
    when the pathway is unknown or not ``done``, and for a path that is not a plain file of an allowed kind inside the
    course: a ``..`` or empty part, a dotfile, a backslash, a non-ASCII name, a link anywhere on the way, a folder.
    """

    if not is_pathway_id(pathway_id):
        return None
    learning = learning_dir(home_root, target)  # resolved once: naming the project is the costly part of a file request
    record = _read(learning / "records" / f"{pathway_id}.json")
    if record is None or record.status != "done" or record.course is None:
        return None
    parts = tuple((relpath or record.course.entry).split("/"))
    if not parts or not all(is_safe_name(part) for part in parts):
        return None
    if not is_allowed_kind(parts[-1]):
        return None
    if _has_link(learning, ("courses", pathway_id, "site", *parts)):
        return None
    site = learning / "courses" / pathway_id / "site"
    candidate = site.joinpath(*parts)
    try:
        inside = candidate.resolve(strict=True).is_relative_to(site.resolve(strict=True))
    except OSError:
        return None
    return candidate if inside and candidate.is_file() else None


__all__ = [
    "ALLOWED_BARE_NAMES",
    "ALLOWED_SUFFIXES",
    "COST_KEYS",
    "COURSE_ENTRY",
    "Course",
    "LearningError",
    "Pathway",
    "PROGRESS_KEYS",
    "PROGRESS_STEP_STATUSES",
    "ROLE_TEXT_MAX",
    "SCHEMA",
    "STATUSES",
    "clean_cost",
    "clean_progress",
    "clean_role_text",
    "course_site_dir",
    "courses_dir",
    "create_request",
    "get_pathway",
    "import_course",
    "is_allowed_kind",
    "is_pathway_id",
    "is_safe_name",
    "learning_dir",
    "list_pathways",
    "new_id",
    "now_text",
    "pathway_from_json",
    "records_dir",
    "resolve_course_file",
    "write_lock",
    "write_pathway",
]
