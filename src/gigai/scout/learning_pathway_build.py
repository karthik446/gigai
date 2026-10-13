"""0.1.11.10 shipped lessons, slices 4b and 5b: THE BUILD of the user's own pathway page of a shipped course.

A shipped course is generic. ONE page of it is the user's own (``learning_pathway_local``): what the user's stored
postings ask for, and what the user's resume already shows of each lesson (known / some / new). Building that
page is the user's one action on a shipped course, and it is ONE build, always with the resume:

THE RESUME IS ALWAYS THE REFERENCE. There is no build without it, no switch and no option. What the user is
asked before a build is the usual consent to a model call, not whether to use the resume.

1. THE ESTIMATE (:func:`ask_response`): nothing is sent without approval. The master's lines are read
   (``learning_course.read_master_lines``) and ``learning_markers.estimate_markers`` says what the comparison
   takes: model calls, tokens in and out, seconds. No call is made. With no master resume, or one with no line,
   there is nothing to send: the estimate is zero, the answer says so, and the build needs no model.
2. THE BUILD (:func:`run`), on approval, under the project's ONE job slot (``learning_job``'s claim: a course
   being generated and a pathway page being built never run together; ``learning_running`` otherwise):
   a. the user's stored postings are counted with the course's shipped vocabulary (no model). A build that
      cannot count (no setup, no search index) fails HERE, before anything is sent;
   b. ``learning_markers.run_markers`` compares the course's lesson and subtopic TITLES with the master's lines
      (contact lines removed) in about one model call, metered as kind ``learning`` like every call of a course
      (a ``model_call`` row per call: numbers, never text), and checks every answer in code;
   c. the page is written with those markers and the record of the build with the master revision the
      comparison was made from and what it took (``cost``: ``calls``, ``tokens_in``, ``tokens_out``).
   A failure at any point writes NOTHING of a page: a partial comparison is never shown, and the page that was
   there stays as it was.
3. THE STATE of a build (:func:`build_state`), one small file beside the record: ``running`` while a live
   process builds (so the tab can say so), ``failed`` with a plain line and a code after a build that did not
   finish, nothing after one that did. It holds no resume text and no posting text.

WHERE RESUME TEXT GOES from here: the master's lines are handed to ``learning_markers`` (whose prompt builder
passes each through ``resume_privacy.guard_private``; ``test_resume_privacy_static.py`` pins it) and the checked
quotes are written on the page, which is the user's own file on this computer. Nothing of a resume is in the
record, the state file, a progress line, an error or an API answer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from . import learning_pathway_local, learning_shipped_store, learning_store
from .find_jobs.discovery.storage import atomic_write
from .learning_shipped import CATALOG_FILENAME
from .learning_shipped_store import ShippedCourse
from .learning_store import LearningError

SCHEMA_ASK = "scout-learning-pathway-build:1"
STATE_SCHEMA = "scout-learning-pathway-build-state:1"
BUILD_RUNNING, BUILD_FAILED = "running", "failed"
#: The plain line of a running build (the tab and the CLI print it).
COMPARING = "Comparing your resume with the lessons"
COUNTING = "Counting what your stored postings ask for"
WRITING = "Writing your pathway page"
#: What the user is told before the one model call of a build (the consent line of the CLI and the dialog).
CONSENT = (
    "This reads your resume to mark what you already know. It sends the lesson titles of this course and your resume "
    "lines (contact lines removed) to your model."
)
NO_RESUME = (
    "No resume found: there is no master resume line to compare the course with. Nothing is sent to a model, and the "
    "page marks nothing as known."
)

LEARNING_RUNNING = "learning_running"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_AUTHENTICATION_REQUIRED = "model_authentication_required"
MARKERS_INVALID = "markers_invalid"
CAP_REACHED = "cap_reached"
PAGE_FAILED = "pathway_page_failed"
BUILD_ERROR = "pathway_build_failed"
_KEPT = "Nothing was written; your pathway page, if you had one, is as it was."
#: The plain line of a build that did not finish, by code. No message of a model or an adapter is passed on.
_MESSAGES = {
    MODEL_UNAVAILABLE: f"Your model login refused or timed out, so your resume was not compared. {_KEPT} Build again later.",
    MODEL_AUTHENTICATION_REQUIRED: f"Your model login needs you to sign in again. {_KEPT} Then build again.",
    MARKERS_INVALID: f"The model's answer could not be read as a comparison, twice. {_KEPT} Build again.",
    CAP_REACHED: f"The comparison stopped at its cap of model calls. {_KEPT} Build again.",
    CANCELLED: f"The build was stopped. {_KEPT}",
    INTERRUPTED: f"The build was interrupted before it finished. {_KEPT} Build again.",
    BUILD_ERROR: f"The build failed unexpectedly (the server's log says why). {_KEPT}",
}
#: Reading the master's revision chain starts about 17 ``git`` processes (0.1 s on a small home), and the tab asks
#: for the list every 2 s while something runs: a LIST read reuses an answer of this process no older than this.
MASTER_REF_SECONDS = 30.0
_MASTER_REFS: dict[tuple[str, str], tuple[float, Any]] = {}
_MASTER_REFS_LOCK = threading.Lock()
NOT_GIVEN = learning_pathway_local.NOT_GIVEN

_logger = logging.getLogger("gigai.scout.server")


class PathwayBuildError(LearningError):
    """A build that did not finish; ``code`` is the API/CLI error code and the message is a plain line."""


@dataclass(frozen=True)
class BuildResult:
    """What one finished build made."""

    page: learning_pathway_local.PathwayPage
    counts: Mapping[str, Any]
    #: ``{"calls", "tokens_in", "tokens_out"}`` of the comparison (zero calls when there was no resume line).
    usage: Mapping[str, int | None]
    #: How many master lines were compared; 0 with ``master_found`` false means there is no master resume at all.
    resume_lines: int
    master_found: bool


# --- the master ------------------------------------------------------------------------------------------------------


def current_master_ref(home_root: Path, target: Path, *, max_age: float = 0.0) -> Any:
    """The master resume's current revision as ``{"revision_id", "content_sha256"}``; ``None`` when there is no master.

    Only the revision chain is read (``master_store.master_revisions``): no line of the resume. When the chain
    cannot be read the answer is :data:`NOT_GIVEN`: nothing is then said about the master. ``max_age``: an answer
    this process read at most that many seconds ago is good enough (a list read passes
    :data:`MASTER_REF_SECONDS`); the default reads the chain now, as a build does.
    """

    from ..workpad import WorkpadError
    from . import master_store

    key = (str(home_root), str(target))
    if max_age > 0:
        with _MASTER_REFS_LOCK:
            known = _MASTER_REFS.get(key)
        if known is not None and time.monotonic() - known[0] <= max_age:
            return known[1]
    try:
        chain = master_store.master_revisions(home_root=Path(home_root), target=Path(target))
    except (WorkpadError, ValueError, OSError):
        return NOT_GIVEN
    ref = {"revision_id": chain[-1].revision_id, "content_sha256": chain[-1].content_sha256} if chain else None
    with _MASTER_REFS_LOCK:
        _MASTER_REFS[key] = (time.monotonic(), ref)
    return ref


def _master_lines(home_root: Path, target: Path) -> tuple[tuple[str, str], ...]:
    """The master's ``(id, text)`` lines; ``()`` with no master. They go to ``learning_markers`` and nowhere else."""

    from ..workpad import WorkpadError
    from . import learning_course

    try:
        return learning_course.read_master_lines(Path(home_root), Path(target))
    except (WorkpadError, ValueError) as exc:
        raise PathwayBuildError("master_unreadable", "your master resume could not be read, so nothing was compared") from exc


def _catalog(course: ShippedCourse) -> dict[str, Any]:
    try:
        catalog = json.loads((course.data_dir / CATALOG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LearningError("invalid_shipped", f"the shipped course {course.role_key} cannot be read") from exc
    if not isinstance(catalog, dict):
        raise LearningError("invalid_shipped", f"the shipped course {course.role_key} cannot be read")
    return catalog


# --- 1. the estimate -------------------------------------------------------------------------------------------------


def estimate_words(estimate: Mapping[str, object]) -> str:
    """The estimate as one line: 'about 1 model call, about 33k tokens, about 92 seconds'."""

    calls, seconds = int(estimate.get("calls", 0) or 0), int(estimate.get("seconds", 0) or 0)  # type: ignore[call-overload]
    tokens = int(estimate.get("tokens_in", 0) or 0) + int(estimate.get("tokens_out", 0) or 0)  # type: ignore[call-overload]
    shown = f"{tokens / 1000:.0f}k" if tokens >= 1000 else str(tokens)
    return f"about {calls} model call{'' if calls == 1 else 's'}, about {shown} tokens, about {seconds} second{'' if seconds == 1 else 's'}"


def ask_response(home_root: Path, target: Path, course: ShippedCourse) -> dict[str, object]:
    """What a build of ``course``'s pathway page would take and send: the facts, NOTHING sent and nothing started.

    ``resume.lines`` is how many master lines would be compared (0: no master resume, or one with no line; then
    ``estimate`` is zero, ``sends`` says nothing is sent, and a build needs no model). ``estimate`` is
    ``learning_markers.estimate_markers``' arithmetic on the prompt that would be sent: ``calls``, ``tokens_in``,
    ``tokens_out``, ``seconds``. ``sends`` is the consent line. The answer holds numbers and fixed sentences only.
    """

    from . import learning_markers
    from .learning_course import guarded_master_lines

    learning_store.learning_dir(home_root, target)  # a folder that is no project is refused before the resume is read
    lines = _master_lines(home_root, target)
    found = learning_markers.estimate_markers(_catalog(course), lines)
    compared = len(guarded_master_lines(lines))
    return {
        "schema_version": SCHEMA_ASK,
        "status": "ask",
        "role_key": course.role_key,
        "role_text": course.role,
        "resume": {"lines": compared},
        "estimate": {
            "calls": found["calls"], "tokens_in": found["tokens_in_estimate"], "tokens_out": found["tokens_out_estimate"], "seconds": found["seconds"],
        },
        "sends": CONSENT if compared else NO_RESUME,
    }


# --- 3. the state of a build -----------------------------------------------------------------------------------------


def state_path(learning: Path, role_key: str) -> Path:
    """Where the state of ``role_key``'s build is, beside the record of its page."""

    return learning_pathway_local.local_record_path(learning, role_key).with_suffix(".build.json")


def _read_state(learning: Path, role_key: str) -> dict[str, Any] | None:
    try:
        path = state_path(learning, role_key)
    except LearningError:
        return None
    if path.is_symlink() or path.parent.is_symlink():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("schema") != STATE_SCHEMA or stored.get("role_key") != role_key:
        return None
    return stored


def _write_state(learning: Path, role_key: str, body: Mapping[str, object]) -> None:
    atomic_write(state_path(learning, role_key), (json.dumps({"schema": STATE_SCHEMA, "role_key": role_key, **body}, sort_keys=True) + "\n").encode("utf-8"))


def _clear_state(learning: Path, role_key: str) -> None:
    try:
        state_path(learning, role_key).unlink()
    except (OSError, LearningError):
        pass


def build_state(learning: Path, role_key: str) -> dict[str, object] | None:
    """The build of ``role_key``'s page as a reader sees it, or ``None`` (none runs and the last one finished).

    ``{"status": "running", "started_at", "detail"}`` while a live process builds; ``{"status": "failed",
    "error_code", "error", "finished_at"}`` after a build that did not finish (``interrupted`` for one whose
    process is gone). Reads one small file; no process is started.
    """

    from . import learning_job

    stored = _read_state(learning, role_key)
    if stored is None:
        return None
    if stored.get("status") == BUILD_RUNNING:
        if learning_job._holder_alive(stored):
            return {"status": BUILD_RUNNING, "started_at": stored.get("started_at"), "detail": stored.get("detail")}
        return {"status": BUILD_FAILED, "error_code": INTERRUPTED, "error": _MESSAGES[INTERRUPTED], "finished_at": None}
    if stored.get("status") == BUILD_FAILED:
        return {"status": BUILD_FAILED, "error_code": stored.get("error_code"), "error": stored.get("error"), "finished_at": stored.get("finished_at")}
    return None


# --- 2. the build ----------------------------------------------------------------------------------------------------


def _failure(exc: BaseException) -> PathwayBuildError:
    """``exc`` as the plain refusal a reader gets: a code and a fixed line, never a model's or an adapter's own words."""

    from . import learning_job
    from .learning_render import CourseError

    if isinstance(exc, PathwayBuildError):
        return exc
    if isinstance(exc, CourseError):
        return PathwayBuildError(PAGE_FAILED, f"Your pathway page did not pass its checks ({len(exc.errors)} problem(s): {exc.errors[0][:160]}). {_KEPT}")
    if isinstance(exc, LearningError):
        code = exc.code
        if code == MODEL_UNAVAILABLE and learning_job._is_login_failure(exc):
            code = MODEL_AUTHENTICATION_REQUIRED
        if code in _MESSAGES:
            return PathwayBuildError(code, _MESSAGES[code])
        return PathwayBuildError(code, str(exc))  # this code's own words: the index, the setup, the shipped course
    return PathwayBuildError(BUILD_ERROR, _MESSAGES[BUILD_ERROR])


def _record_cost(learning: Path, role_key: str, usage: Mapping[str, int | None]) -> None:
    """Add what the comparison took to the record of the page: numbers only."""

    record = learning_pathway_local.read_local_record(learning, role_key)
    if record is None:
        return
    record["cost"] = {"calls": int(usage.get("calls") or 0), "tokens_in": usage.get("tokens_in"), "tokens_out": usage.get("tokens_out")}
    atomic_write(learning_pathway_local.local_record_path(learning, role_key), (json.dumps(record, sort_keys=True, indent=1) + "\n").encode("utf-8"))


def _build(
    home_root: Path, target: Path, course: ShippedCourse, learning: Path, *, model_port: Any, cancel: Callable[[], bool] | None,
    say: Callable[[str], None],
) -> BuildResult:
    """Count, compare, write. The page and its record are written last: everything before can fail and leave no trace."""

    from . import learning_markers
    from .learning_course import guarded_master_lines

    role_key = course.role_key
    catalog = _catalog(course)

    say(COUNTING)
    counts = learning_pathway_local.count_postings(home_root, target, catalog)

    lines = _master_lines(home_root, target)
    master = current_master_ref(home_root, target)
    markers: dict[str, Any] | None = None
    usage: Mapping[str, int | None] = {"calls": 0, "tokens_in": None, "tokens_out": None}
    compared = len(guarded_master_lines(lines))
    if isinstance(master, dict):
        # THE RESUME IS ALWAYS THE REFERENCE: whenever there is a master, the course is compared with it.
        if compared:
            say(COMPARING)
        markers, usage, _stats = learning_markers.run_markers(home_root, target, catalog, lines, model_port, cancel=cancel)
    elif lines:
        raise PathwayBuildError("master_unreadable", "your master resume could not be read, so nothing was compared")

    say(WRITING)
    out_path = learning_shipped_store.pathway_page_path(Path(home_root), Path(target), role_key)
    page = learning_pathway_local.build_pathway_page(catalog, counts, markers, out_path, learning_shipped_store.ASSET_BASE)
    learning_pathway_local.write_local_record(
        home_root, target, role_key, catalog=catalog, counts=counts, master=master if markers is not None else None, learning=learning,
    )
    _record_cost(learning, role_key, usage)
    return BuildResult(page=page, counts=counts, usage=usage, resume_lines=compared, master_found=isinstance(master, dict))


def _model_port() -> Any | None:
    """Test-only: the scripted model of the api-e2e journey (``learning_job._test_script``), else ``None`` (the user's target)."""

    from . import learning_job

    script = learning_job._test_script()
    return learning_job._ScriptedModel(script) if script is not None else None


def _claim(home_root: Path, target: Path) -> Any:
    """The project's one job slot (``learning_job``'s), or ``learning_running``."""

    from . import learning_job

    try:
        return learning_job._claim(home_root, target, None)
    except LearningError as exc:
        if exc.code == LEARNING_RUNNING:
            raise PathwayBuildError(LEARNING_RUNNING, "a course is being generated or a pathway page is being built for this project now; wait for it to finish") from exc
        raise


def _run(
    home_root: Path, target: Path, course: ShippedCourse, current: Any, *, model_port: Any, on_progress: Callable[[str], None] | None,
) -> BuildResult:
    """One claimed build to its end: the state file says ``running``, then nothing (built) or ``failed``; the claim is released."""

    from . import learning_job
    from .pipeline.store import process_token

    learning = learning_store.learning_dir(home_root, target)
    role_key = course.role_key
    owner = {"pid": os.getpid(), "token": process_token(), "run": current.run, "started_at": learning_store.now_text()}

    def say(text: str) -> None:
        _write_state(learning, role_key, {"status": BUILD_RUNNING, **owner, "detail": text})
        if on_progress is not None:
            on_progress(text)

    try:
        say("Starting")
        result = _build(home_root, target, course, learning, model_port=model_port if model_port is not None else _model_port(), cancel=current.cancel.is_set, say=say)
    except BaseException as exc:  # noqa: BLE001 - every end of a build is written down as a plain line, then raised as one
        if not isinstance(exc, Exception):
            _write_state(learning, role_key, {"status": BUILD_FAILED, "error_code": INTERRUPTED, "error": _MESSAGES[INTERRUPTED], "finished_at": learning_store.now_text()})
            raise  # Ctrl-C, the interpreter going down
        failure = _failure(exc)
        if failure.code == BUILD_ERROR:
            _logger.exception("pathway build (%s): the build ended with an error", role_key)
        _write_state(learning, role_key, {"status": BUILD_FAILED, "error_code": failure.code, "error": str(failure), "finished_at": learning_store.now_text()})
        raise failure from exc
    else:
        _clear_state(learning, role_key)
        return result
    finally:
        learning_job._release(home_root, target, current)


def run(
    home_root: Path, target: Path, course: ShippedCourse, *, model_port: Any | None = None, on_progress: Callable[[str], None] | None = None,
) -> BuildResult:
    """Build ``course``'s pathway page HERE, in the foreground, to its end. The caller has the user's approval.

    Raises :class:`PathwayBuildError` with a plain line: ``learning_running`` (a course is generated or a page is
    built for this project now), ``search_index_unavailable`` / ``config_unavailable`` (before anything is sent),
    ``model_unavailable``, ``model_authentication_required``, ``markers_invalid``, ``cap_reached``, ``cancelled``,
    ``pathway_page_failed``. In every such case no page was written and the one that was there stays.
    ``model_port`` (``learning_course.CourseModel``) answers the comparison; left out, the user's own target does.
    """

    return _run(home_root, target, course, _claim(home_root, target), model_port=model_port, on_progress=on_progress)


def start(home_root: Path, target: Path, course: ShippedCourse, *, model_port: Any | None = None) -> None:
    """Start the same build on a thread of this process (the Scout server) and return; :func:`build_state` says how it goes.

    The claim is taken and the state written BEFORE this returns, so the caller's next read sees ``running``.
    """

    from .pipeline.store import process_token
    from . import learning_job

    current = _claim(home_root, target)
    try:
        learning = learning_store.learning_dir(home_root, target)
        _write_state(learning, course.role_key, {
            "status": BUILD_RUNNING, "pid": os.getpid(), "token": process_token(), "run": current.run,
            "started_at": learning_store.now_text(), "detail": "Starting",
        })
    except BaseException:  # noqa: BLE001 - releases the slot and re-raises: nothing is swallowed
        learning_job._release(home_root, target, current)
        raise

    def work() -> None:
        try:
            _run(home_root, target, course, current, model_port=model_port, on_progress=None)
        except PathwayBuildError:
            pass  # written down in the state file
        except BaseException:  # noqa: BLE001 - a thread of the server never dies with a traceback on stderr only
            _logger.exception("pathway build (%s): the run ended with an error", course.role_key)

    current.thread = threading.Thread(target=work, name=f"scout-learning-pathway-{course.role_key}", daemon=True)
    current.thread.start()


__all__ = [
    "BUILD_FAILED",
    "BUILD_RUNNING",
    "BuildResult",
    "COMPARING",
    "CONSENT",
    "MASTER_REF_SECONDS",
    "NO_RESUME",
    "PathwayBuildError",
    "SCHEMA_ASK",
    "ask_response",
    "build_state",
    "current_master_ref",
    "estimate_words",
    "run",
    "start",
    "state_path",
]
