"""0.1.11.10 Part B, packet G5: the JOB that generates a course for a role (design sections 3, 4, 5, 7).

``learning_store`` holds the pathway record, G1 (``learning_corpus``) counts what a role's stored postings ask for,
G2 (``learning_course``) plans, verifies and writes the lessons, G4 (``learning_paths``) the practice paths, G3
(``learning_render``) draws the pages. This module runs them as ONE job with a durable record, modelled on
``find_jobs/assess_all`` (a folder per job, an owner, a cancel file, a dead owner reads as ``interrupted``).

ESTIMATE FIRST (:func:`estimate`): nothing is generated without approval. The estimate is calls, page fetches,
minutes and tokens: :data:`DEFAULT_CALLS` / :data:`DEFAULT_FETCHES` / :data:`DEFAULT_MINUTES` (``tokens`` null)
while no DONE, requested course here has a cost record (``basis: "none"``), else the average of the last up to
:data:`HISTORY_COURSES` such courses' own recorded totals (``basis: "history"``; G7d: NOT a scaled per-item average
over mismatched call-metrics rows, which is what G7 found wrong).

THE STEPS (:data:`STEPS`), in order, each with a result under ``<learning>/work/<id>/``:

1. ``corpus``: G1's ``run_corpus`` (``corpus.json``, and as each part is made ``titles.json``, ``corpus-summary.json``,
   ``vocabulary-raw.json``, ``vocabulary.json``, ``counts.json``: a failed run can be read, a resume asks only for
   what has no stored answer).
2. ``course``: G2's ``run_course_steps`` (``curriculum.json``, ``sources.json``, ``modules/<m>.json``,
   ``course.json``). THE RESUME IS ALWAYS THE REFERENCE: the master's lines are read with
   ``learning_course.read_master_lines`` and handed to that one call (``master_lines=``), which gives them to the
   lesson-writing prompt only. They go nowhere else from here: not to G1, not to the paths step, not to the record,
   not to a progress line. An empty master makes every lesson NEW and is not an error.
3. ``paths``: G4's ``run_paths_step`` (``paths/<lesson>.json``), reached through :func:`paths_step` by name.
4. ``render``: G3's ``build_course`` into ``site/``.
5. ``audit``: the gates of design section 5 that code can check on the finished folder (:func:`audit_site`):
   internal links and anchors, every external link is one this job verified (NO new fetch), no stray glyph, no
   inline script, the size caps, the contact check. The first failure fails the course.
6. ``import``: ``learning_store.import_course`` into the store, with ``cost`` filled from the job's own counters.

A step whose stored result still validates is not run again (``skipped``), so a job that stopped RESUMES where it
stopped (:func:`resume`: the same id, only the steps with no result cost anything).

60 PERCENT: a course is ``done`` only when ``learning_course.course_status`` says so (at least 60 percent of the
planned lessons kept). Below that the record is ``failed`` with ``too_little_verified`` and the work folder stays.

STOPPING. The cancel file (``work/<id>/cancel``) is read before every model call and every fetch. The caps
(:data:`MAX_CALLS` model calls, :data:`MAX_FETCHES` page fetches, per run of the job) are checked there too. A
job that is stopped either way finishes with the lessons already written when they are 60 percent of the plan
(no further call, no further fetch), else it is ``failed`` with ``cancelled`` / ``cap_reached``.

MODEL FAILURES retry as the pipeline does (60 s doubling, :data:`RETRY_ATTEMPTS` attempts), then the step is
``failed`` with ``model_unavailable``. A login failure fails at once (``model_authentication_required``).

ONE COURSE AT A TIME PER PROJECT: ``<learning>/live.json`` names the job that runs (its pid, process token and
run), claimed under the store's own lock; a second start is refused with ``learning_running``. A claim whose
process is gone, or whose progress file was not touched for :data:`QUIET_SECONDS`, is not a live job: its record
reads ``interrupted`` (:func:`live_status`) and may be resumed.

What reaches the record and the API is ids, counts, codes and plain progress lines; every line passes the contact
check before it is stored (:func:`_plain`). No posting text and no resume text is written to a record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
import json
import logging
import math
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
import uuid

from . import learning_store
from .learning_store import LearningError, Pathway

SCHEMA_ESTIMATE = "scout-learning-generate:1"
#: The estimate while no ``learning`` call is recorded: about 60 calls, 1000 fetches, 45 minutes (G4 measured up to 24
#: crawl fetches and 8 to 15 re-fetches per lesson; about 150 more for the sources; the audit fetches nothing).
DEFAULT_CALLS, DEFAULT_FETCHES, DEFAULT_MINUTES = 60, 1000, 45
#: What one run of the job may spend at most.
MAX_CALLS, MAX_FETCHES = 120, 1500
#: The key of the warning the dialog shows (the wording is the UI's).
WARNING_KEY = "learning_higher_usage"
#: A progress file not touched for this long is not a live job (``pipeline.busy.BATCH_QUIET_SECONDS``).
QUIET_SECONDS = 15 * 60.0
#: A model call that fails is tried again after 60 s, 120 s, 240 s: four attempts (``pipeline/store.py``).
RETRY_BASE_SECONDS = 60.0
RETRY_ATTEMPTS = 4
#: G7c: the 60/120/240 s doubling is for a rate limit easing off. A call that timed out (the adapter already waited
#: up to its own per-call timeout, e.g. 600 s for a learning call) is retried at once instead of waiting again.
TIMEOUT_RETRY_WAIT_SECONDS = 0.0

STEP_CORPUS, STEP_COURSE, STEP_PATHS, STEP_RENDER, STEP_AUDIT, STEP_IMPORT = "corpus", "course", "paths", "render", "audit", "import"
STEPS = (STEP_CORPUS, STEP_COURSE, STEP_PATHS, STEP_RENDER, STEP_AUDIT, STEP_IMPORT)

LEARNING_RUNNING = "learning_running"
CANCELLED = "cancelled"
CAP_REACHED = "cap_reached"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_AUTHENTICATION_REQUIRED = "model_authentication_required"
PATHS_STEP_NOT_BUILT = "paths_step_not_built"
AUDIT_FAILED = "audit_failed"
STATUS_INTERRUPTED = "interrupted"

_MESSAGES = {
    CANCELLED: "Stopped before enough of the course was written. What was written is kept; resume to go on.",
    CAP_REACHED: f"Stopped at the cap of {MAX_CALLS} model calls or {MAX_FETCHES} page fetches before enough of the course was verified. What was written is kept; resume to go on.",
    MODEL_UNAVAILABLE: "Your model login refused or timed out; resume later.",
    MODEL_AUTHENTICATION_REQUIRED: "Your model login needs you to sign in again; then resume.",
    PATHS_STEP_NOT_BUILT: "This version cannot write practice paths yet, so a course cannot be finished.",
}

_LIVE_FILENAME = "live.json"
_OWNER_FILENAME = "owner.json"
_CANCEL_FILENAME = "cancel"
_LINES_FILENAME = "progress.jsonl"
_STATE_FILENAME = "job.json"
_CORPUS_FILENAME = "corpus.json"
_AUDIT_FILENAME = "audit.json"
_SITE_DIR = "site"
_PATHS_DIR = "paths"
_PATHS_MODULE = "gigai.scout.learning_paths"
#: Test-only: a JSON file of scripted model answers and pages (see :func:`_test_parts`).
_TEST_SCRIPT_ENV = "GIGAI_SCOUT_LEARNING_TEST_SCRIPT"

_logger = logging.getLogger("gigai.scout.server")


class LearningJobError(LearningError):
    """A course job was refused or could not go on; ``code`` is the API/CLI error code."""


class _Stop(Exception):
    """The job may make no further call or fetch: ``code`` is ``cancelled`` or ``cap_reached``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Failed(Exception):
    """The job ends ``failed``: the record's ``error_code`` and plain ``error``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _LostClaim(Exception):
    """Another run owns this pathway now: this one writes nothing more."""


# ---------------------------------------------------------------------------
# Plain text for the record
# ---------------------------------------------------------------------------


def _plain(text: object, fallback: str) -> str:
    """``text`` as one line the record may hold (at most 300 characters, no contact shape), else ``fallback``."""

    from .story_bank import personal_info_in_answer

    line = " ".join(str(text or "").split())[: learning_store.NOTE_MAX]
    line = "".join(char for char in line if ord(char) >= 32 and ord(char) != 127)
    return line if line and not personal_info_in_answer(line) else fallback


def _read_json(path: Path) -> Any | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: object) -> None:
    from .find_jobs.discovery.storage import atomic_write

    atomic_write(path, json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8"))


class _CorpusWork:
    """The corpus step's own files in a work folder (``learning_corpus.CorpusStore``): ``<step>.json``, numbers and concepts, never posting text."""

    def __init__(self, work: Path) -> None:
        self._work = work

    def read(self, step: str) -> Any | None:
        return _read_json(self._work / f"{step}.json")

    def write(self, step: str, data: object) -> None:
        _write_json(self._work / f"{step}.json", data)


def work_dir(home_root: Path, target: Path, pathway_id: str) -> Path:
    """The work folder of ``pathway_id``'s job. ``invalid_value`` for a text that is not a pathway id."""

    if not learning_store.is_pathway_id(pathway_id):
        raise LearningJobError("invalid_value", "a pathway id is lp- and 8 hex digits")
    return learning_store.learning_dir(home_root, target) / "work" / pathway_id


# ---------------------------------------------------------------------------
# Who runs: the claim of a project, the owner of a work folder
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    """One run of a job in this process."""

    run: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    pathway_id: str | None = None


_LOCK = threading.RLock()
_RUNS: dict[str, _Run] = {}


def _holder_alive(claim: object) -> bool:
    """Whether the process that wrote ``claim`` (``live.json`` / ``owner.json``) still runs that job."""

    from .pipeline.store import _pid_alive, process_token

    if not isinstance(claim, Mapping):
        return False
    pid, run = claim.get("pid"), claim.get("run")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(run, str):
        return False
    if pid == os.getpid():
        if claim.get("token") != process_token():
            return False  # this pid, an earlier process
        with _LOCK:
            current = _RUNS.get(run)
        return current is not None and not current.done.is_set()
    return _pid_alive(pid)


def _fresh(work: Path, clock: Callable[[], float] = time.time) -> bool:
    """Whether the job of ``work`` wrote or touched its progress within :data:`QUIET_SECONDS`."""

    newest = 0.0
    for name in (_LINES_FILENAME, _OWNER_FILENAME):
        try:
            newest = max(newest, (work / name).stat().st_mtime)
        except OSError:
            continue
    return clock() - newest < QUIET_SECONDS


def _job_live(work: Path) -> bool:
    return _holder_alive(_read_json(work / _OWNER_FILENAME)) and _fresh(work)


def live_status(home_root: Path, target: Path, pathway: Pathway) -> str:
    """The pathway's status as a reader sees it: ``interrupted`` for a job no live process runs. Reads files only."""

    if pathway.status not in ("queued", "running") or pathway.source != "requested":
        return pathway.status
    work = work_dir(home_root, target, pathway.id)
    if not (work / _OWNER_FILENAME).is_file():
        return pathway.status if pathway.status == "queued" else STATUS_INTERRUPTED  # asked for and never started
    if not _job_live(work):
        return STATUS_INTERRUPTED
    started = isinstance(pathway.progress, Mapping) and pathway.progress.get("step") is not None
    return "running" if pathway.status == "running" or started else "queued"


def running_id(home_root: Path, target: Path) -> str | None:
    """The pathway a live job of this project generates now (``""`` while it has no record yet), else ``None``."""

    learning = learning_store.learning_dir(home_root, target)
    claim = _read_json(learning / _LIVE_FILENAME)
    if not isinstance(claim, Mapping) or not _holder_alive(claim):
        return None
    pathway_id = claim.get("id")
    if isinstance(pathway_id, str) and learning_store.is_pathway_id(pathway_id):
        return pathway_id if _fresh(learning / "work" / pathway_id) else None
    return ""


def _claim(home_root: Path, target: Path, pathway_id: str | None) -> _Run:
    """Take the project's one job slot for a new run; ``learning_running`` while a live job has it."""

    from .pipeline.store import process_token

    learning = learning_store.learning_dir(home_root, target)
    with _LOCK, learning_store.write_lock(home_root, target):
        if running_id(home_root, target) is not None:
            raise LearningJobError(LEARNING_RUNNING, "a course is being generated for this project now; wait for it to finish or stop it")
        current = _Run(uuid.uuid4().hex, pathway_id=pathway_id)
        _RUNS[current.run] = current
        try:
            _write_json(learning / _LIVE_FILENAME, _claim_body(current, process_token()))
            if pathway_id is not None:
                _own(home_root, target, current, pathway_id)
        except BaseException:
            _RUNS.pop(current.run, None)
            raise
    return current


def _claim_body(current: _Run, token: str) -> dict[str, object]:
    return {"id": current.pathway_id, "pid": os.getpid(), "token": token, "run": current.run, "claimed_at": learning_store.now_text()}


def _own(home_root: Path, target: Path, current: _Run, pathway_id: str) -> None:
    """Name ``pathway_id`` as what ``current`` generates: the project's claim and the work folder's owner file."""

    from .pipeline.store import process_token

    current.pathway_id = pathway_id
    body = _claim_body(current, process_token())
    work = work_dir(home_root, target, pathway_id)
    work.mkdir(parents=True, exist_ok=True)
    try:
        (work / _CANCEL_FILENAME).unlink()  # a stop that was asked of an earlier run
    except OSError:
        pass
    _write_json(work / _OWNER_FILENAME, body)
    _write_json(learning_store.learning_dir(home_root, target) / _LIVE_FILENAME, body)


def _release(home_root: Path, target: Path, current: _Run) -> None:
    current.done.set()
    with _LOCK:
        _RUNS.pop(current.run, None)
    try:
        path = learning_store.learning_dir(home_root, target) / _LIVE_FILENAME
        claim = _read_json(path)
        if isinstance(claim, Mapping) and claim.get("run") == current.run:
            path.unlink()
    except (OSError, LearningError):
        pass


def wait_for_jobs(*, timeout: float | None = None) -> bool:
    """Wait until no job runs in this process; false when ``timeout`` ran out first."""

    with _LOCK:
        running = [item for item in _RUNS.values() if not item.done.is_set()]
    return all(item.done.wait(timeout) for item in running)


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


#: History sizes the estimate from at most this many of the most recent done, requested, costed courses.
HISTORY_COURSES = 3


def _history_courses(home_root: Path, target: Path) -> list[Mapping[str, object]]:
    """Up to :data:`HISTORY_COURSES` most recent DONE, requested courses whose cost record has a call count.

    A course generated here that failed, or one imported (its cost is another machine's), never sizes the
    estimate. A course with no cost record at all (``cli_model_calls`` null: written before this ran, or by a
    build that stored none) is skipped the same way.
    """

    found = []
    for pathway in learning_store.list_pathways(home_root, target):
        if pathway.source != "requested" or pathway.status != "done":
            continue
        if not isinstance(pathway.cost.get("cli_model_calls"), int):
            continue
        found.append(pathway.cost)
        if len(found) == HISTORY_COURSES:
            break
    return found


def estimate(home_root: Path, target: Path) -> dict[str, object]:
    """``{calls, fetches, minutes, tokens, basis}`` of one course. Makes no call and starts nothing.

    ``basis: "none"`` (the constants of :data:`DEFAULT_CALLS` / :data:`DEFAULT_FETCHES` / :data:`DEFAULT_MINUTES`;
    ``tokens`` is ``None``) while no DONE, requested course with a cost record is stored here, else ``"history"``:
    the average over the last up to :data:`HISTORY_COURSES` such courses of their own recorded ``cli_model_calls``,
    ``web_fetches``, ``worker_minutes`` (already active time, retry waits excluded: :meth:`LearningJob._cost`).
    ``tokens`` averages only over the courses of that same set whose cost holds one (an older record read ``null``);
    ``None`` when none of them do.
    """

    answer: dict[str, object] = {"calls": DEFAULT_CALLS, "fetches": DEFAULT_FETCHES, "minutes": DEFAULT_MINUTES, "tokens": None, "basis": "none"}
    courses = _history_courses(home_root, target)
    if not courses:
        return answer
    calls = [int(cost["cli_model_calls"]) for cost in courses]  # type: ignore[arg-type]
    fetches = [int(cost["web_fetches"]) for cost in courses if isinstance(cost.get("web_fetches"), int)]  # type: ignore[arg-type]
    minutes = [float(cost["worker_minutes"]) for cost in courses if isinstance(cost.get("worker_minutes"), (int, float))]  # type: ignore[arg-type]
    tokens = [int(cost["tokens"]) for cost in courses if isinstance(cost.get("tokens"), int)]  # type: ignore[arg-type]
    answer["basis"] = "history"
    answer["calls"] = min(MAX_CALLS, max(1, round(sum(calls) / len(calls))))
    if fetches:
        answer["fetches"] = min(MAX_FETCHES, max(1, round(sum(fetches) / len(fetches))))
    if minutes:
        answer["minutes"] = max(1, math.ceil(sum(minutes) / len(minutes)))
    if tokens:
        answer["tokens"] = round(sum(tokens) / len(tokens))
    return answer


def ask_response(home_root: Path, target: Path, role_text: str) -> dict[str, object]:
    """What ``POST /api/learning/pathways`` answers without ``approve`` (also the CLI's question): the facts, nothing started."""

    role = learning_store.clean_role_text(role_text)
    return {
        "schema_version": SCHEMA_ESTIMATE,
        "status": "ask",
        "role_text": role,
        "estimate": estimate(home_root, target),
        "caps": {"calls": MAX_CALLS, "fetches": MAX_FETCHES},
        "warning": WARNING_KEY,
    }


def estimate_words(found: Mapping[str, object]) -> str:
    """The estimate as one line of the CLI's question."""

    words = f"about {found['calls']} model calls, about {found['fetches']} page fetches from public documentation sites, about {found['minutes']} minutes"
    tokens = found.get("tokens")
    if found.get("basis") != "history":
        return words + " (no recorded course yet to estimate tokens or time from)"
    if isinstance(tokens, (int, float)) and tokens >= 1000:
        words += f", about {tokens / 1000:.0f}k tokens"
    return words + " (sized from your last course(s))"


# ---------------------------------------------------------------------------
# The paths step (G4), by name
# ---------------------------------------------------------------------------


def paths_step() -> Callable[..., Mapping[str, Any]]:
    """G4's ``learning_paths.run_paths_step``, looked up by name; ``paths_step_not_built`` while it is not there."""

    import importlib
    import importlib.util

    if importlib.util.find_spec(_PATHS_MODULE) is None:
        raise LearningJobError(PATHS_STEP_NOT_BUILT, _MESSAGES[PATHS_STEP_NOT_BUILT])
    step = getattr(importlib.import_module(_PATHS_MODULE), "run_paths_step", None)
    if not callable(step):
        raise LearningJobError(PATHS_STEP_NOT_BUILT, _MESSAGES[PATHS_STEP_NOT_BUILT])
    return step  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# The audit gates (design section 5), on the finished folder; no fetch
# ---------------------------------------------------------------------------


class _Hrefs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://")):
                self.external.append(href)


def _urls(value: object) -> set[str]:
    from .learning_course import course_urls

    return {url.split("#", 1)[0] for url in course_urls(value)}


def verified_urls(sources: Mapping[str, Any] | None, paths: Mapping[str, Any] | None) -> set[str]:
    """Every address this job verified: the lesson sources of step 7 and the links of the practice paths of step 9."""

    return _urls(dict(sources or {}).get("lessons", {})) | _urls(dict(paths or {}))


def audit_site(site: Path, *, course: Mapping[str, Any], paths: Mapping[str, Any], verified: set[str]) -> list[dict[str, str]]:
    """The gates code can check on a rendered course: ``[{gate, detail}]``, empty when every gate passes. No fetch."""

    from . import learning_import, learning_render
    from .learning_course import contact_findings

    found: list[dict[str, str]] = []

    def fail(gate: str, details: object) -> None:
        found.extend({"gate": gate, "detail": str(detail)} for detail in (details if isinstance(details, list) else [details]))

    fail("internal_links", learning_render.audit_links(site))
    for page in sorted(site.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        fail("glyphs", learning_render.check_glyphs(text, page.name))
        parser = _Hrefs()
        parser.feed(text)
        for url in sorted({href.split("#", 1)[0] for href in parser.external} - verified):
            fail("external_links", f"{page.name}: a link to an address that was not verified in this run: {url}")
    try:
        scan = learning_import.scan_course(site)
    except LearningError as exc:
        fail("size", f"{exc.code}: {exc}")
    else:
        if scan.inline_script_pages:
            fail("inline_script", f"{scan.inline_script_pages} page(s) hold an inline script block")
    flagged = [*contact_findings(dict(course)), *contact_findings(dict(paths), "paths")]
    if flagged:
        fail("contact_data", "text that looks like contact data at: " + ", ".join(flagged[:5]))
    return found


_GATE_WORDS = {
    "internal_links": "a link inside the course does not lead anywhere",
    "external_links": "a link leads to a page that was not verified in this run",
    "glyphs": "a page holds a character that is not allowed",
    "size": "the course is larger than a course may be",
    "inline_script": "a page holds an inline script",
    "contact_data": "the course holds text that looks like contact data",
}


# ---------------------------------------------------------------------------
# The model of a job: cancel, cap, retry
# ---------------------------------------------------------------------------


def _is_login_failure(exc: BaseException) -> bool:
    from ..adapters.port import ModelAuthenticationRequired

    return isinstance(exc.__cause__, ModelAuthenticationRequired) or str(exc).startswith("authentication_required")


def _is_timeout_failure(exc: BaseException) -> bool:
    """Whether ``exc`` (a ``model_unavailable`` failure's cause) was the adapter's own call timing out.

    ``call_metrics.error_code_of`` already tells a timeout from any other model failure by the adapter's own
    words (``process.run_json_process`` raises "CLI model invocation timed out"); reused here so the job's
    progress line and retry wait agree with what ``call_metrics`` records for the same call.
    """

    from .call_metrics import ERROR_TIMEOUT, error_code_of

    return error_code_of(exc.__cause__ or exc) == ERROR_TIMEOUT


class _JobModel:
    """The model as the steps see it: the cancel file and the cap before every call, the pipeline's retry after a failure."""

    def __init__(self, job: "LearningJob", inner: Any) -> None:
        self._job = job
        self._inner = inner

    @property
    def calls(self) -> int:
        return self._job.calls

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:
        job = self._job
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            job.before_call()
            try:
                return self._inner.ask(prompt, items=items, timeout_seconds=timeout_seconds)
            except LearningError as exc:
                if exc.code != MODEL_UNAVAILABLE:
                    raise
                if _is_login_failure(exc):
                    # The adapter's own words, as an assessment's error carries them (``authentication_required: ...``).
                    raise _Failed(MODEL_AUTHENTICATION_REQUIRED, _plain(str(exc), _MESSAGES[MODEL_AUTHENTICATION_REQUIRED])) from exc
                if attempt == RETRY_ATTEMPTS:
                    raise _Failed(MODEL_UNAVAILABLE, _MESSAGES[MODEL_UNAVAILABLE]) from exc
                if _is_timeout_failure(exc):
                    job.say(f"The model took too long to answer (attempt {attempt} of {RETRY_ATTEMPTS}); trying again now")
                    job.wait(TIMEOUT_RETRY_WAIT_SECONDS)
                    continue
                wait = RETRY_BASE_SECONDS * 2 ** (attempt - 1)
                job.say(f"The model did not answer (attempt {attempt} of {RETRY_ATTEMPTS}); trying again in {round(wait / 60)} min")
                job.wait(wait)
        raise AssertionError("unreachable")

    def invalid_output(self) -> None:
        self._inner.invalid_output()


# ---------------------------------------------------------------------------
# Test-only parts (a real server in another process cannot be handed a fake)
# ---------------------------------------------------------------------------

class _ScriptedModel:
    """Test-only: the answers of a script file. A rule is ``{"when": [text, ...], "answer": ...}``; the first rule whose
    every text is in the prompt answers it (so a resumed job gets the same answers, whatever was asked before)."""

    def __init__(self, script: Mapping[str, Any]) -> None:
        self._script = script

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:  # noqa: ARG002 - a rule reads the prompt only
        from .learning_corpus import LearningCorpusError

        time.sleep(float(self._script.get("delay_seconds") or 0))
        for rule in self._script.get("answers") or []:
            if isinstance(rule, Mapping) and all(text in prompt for text in rule.get("when") or ["\x00"]):
                answer = rule.get("answer")
                return answer if isinstance(answer, str) else json.dumps(answer)
        raise LearningCorpusError("test_script_no_answer", "the test script has no answer for this prompt")

    def invalid_output(self) -> None:
        return None


class _ScriptedPages:
    """Test-only: pages from a script file, under the job's own budget and cancel hook. No name is resolved, nothing is sent."""

    def __init__(self, pages: Mapping[str, str], budget: Any, cancel: Callable[[], bool]) -> None:
        self._pages, self.budget, self._cancel = pages, budget, cancel

    def fetch_page(self, url: str) -> Any:
        from .learning_fetch import STOP_BUDGET, STOP_CANCELLED, Fetched, FetchStopped, parse_page

        if self._cancel():
            raise FetchStopped(STOP_CANCELLED)
        if not self.budget.take():
            raise FetchStopped(STOP_BUDGET)
        address = url.split("#", 1)[0]
        body = self._pages.get(address)
        if body is None:
            return Fetched(requested_url=url, url=address, status=404, error="http_404")
        return Fetched(requested_url=url, url=address, status=200, page=parse_page(address, body))


def _test_script() -> Mapping[str, Any] | None:
    """The script of the api-e2e journey, or ``None``. Read only while BOTH of ``bindings.py``'s test seams are on
    (a server refuses to start with them unless it was started with ``--allow-test-seams``) and the file is named."""

    path = os.environ.get(_TEST_SCRIPT_ENV)
    if not path:
        return None
    from .find_jobs import bindings

    if not (bindings._test_model_enabled() and bindings._test_http_enabled()):
        return None
    script = _read_json(Path(path))
    return script if isinstance(script, Mapping) else None


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


class LearningJob:
    """One run of the generation of ``pathway_id``'s course. ``run`` does the steps and settles the record.

    ``model`` (a ``learning_course.CourseModel``), ``fetcher_factory`` (``(budget, cancel) -> PageFetcher``) and
    ``paths_step`` (G4's function) default to the real ones; a test passes fakes. ``sleep`` waits between retries,
    ``on_progress`` is told every plain progress line (the CLI prints them).
    """

    def __init__(
        self, home_root: Path, target: Path, pathway_id: str, *, model: Any = None,
        fetcher_factory: Callable[[Any, Callable[[], bool]], Any] | None = None,
        paths_step: Callable[..., Mapping[str, Any]] | None = None, sleep: Callable[[float], object] = time.sleep,
        on_progress: Callable[[str], object] | None = None, max_calls: int = MAX_CALLS, max_fetches: int = MAX_FETCHES,
    ) -> None:
        self.home_root, self.target, self.pathway_id = Path(home_root), Path(target), pathway_id
        self.work = work_dir(self.home_root, self.target, pathway_id)
        self._model, self._fetcher_factory, self._paths_step = model, fetcher_factory, paths_step
        self._sleep, self._on_progress = sleep, on_progress
        self.max_calls, self.max_fetches = max_calls, max_fetches
        self._run: _Run | None = None
        self._budget: Any = None
        self._calls_now = 0
        self._calls_before = self._fetches_before = 0
        self._seconds_before = 0.0
        self._wait_seconds_before = 0.0
        self._wait_seconds_now = 0.0
        self._began = time.monotonic()
        self._steps: list[dict[str, object]] = []
        self._step: str | None = None
        self._dropped: list[dict[str, str]] = []
        self._listed: list[dict[str, object]] = []
        self._paths_cut_short = False
        self._tokens_before = 0
        self._captured: list[Any] = []
        self._role_text = ""

    # -- counters, the cancel file, the caps ---------------------------------------------------------

    @property
    def calls(self) -> int:
        return self._calls_before + self._calls_now

    @property
    def fetches(self) -> int:
        return self._fetches_before + (self._budget.used if self._budget is not None else 0)

    def cancelled(self) -> bool:
        return (self._run is not None and self._run.cancel.is_set()) or (self.work / _CANCEL_FILENAME).is_file()

    def before_call(self) -> None:
        """Before every model call: the cancel file, then the cap. The call is counted here."""

        if self.cancelled():
            raise _Stop(CANCELLED)
        if self._calls_now >= self.max_calls:
            raise _Stop(CAP_REACHED)
        self._calls_now += 1
        self._beat()

    def wait(self, seconds: float) -> None:
        """Wait ``seconds`` in steps of at most a second: the cancel file is read and the progress file touched meanwhile.

        Every call here is a RETRY wait (:data:`TIMEOUT_RETRY_WAIT_SECONDS` or the rate-limit doubling, the only
        caller is ``_JobModel.ask``): counted apart from ``_began`` so :func:`LearningJob._cost`'s ``worker_minutes``
        is active time, not time spent waiting for a rate limit to ease off.
        """

        left = float(seconds)
        while left > 0:
            if self.cancelled():
                raise _Stop(CANCELLED)
            step = min(1.0, left)
            self._sleep(step)
            left -= step
            self._wait_seconds_now += step
            self._beat()

    def _beat(self) -> None:
        try:
            os.utime(self.work / _LINES_FILENAME)
        except OSError:
            pass

    # -- the record ------------------------------------------------------------------------------------

    def _progress(self) -> dict[str, object]:
        return {
            "step": self._step, "steps": [dict(item) for item in self._steps], "calls": self.calls, "fetches": self.fetches,
            "max_calls": self.max_calls, "max_fetches": self.max_fetches, "dropped": list(self._dropped[: learning_store.PROGRESS_DROPPED_MAX]),
        }

    def _check_claim(self) -> None:
        """``_LostClaim`` unless this run still owns the pathway and the project's job slot."""

        if self._run is None:
            raise _LostClaim()
        for claim in (self.work / _OWNER_FILENAME, self.work.parent.parent / _LIVE_FILENAME):
            held = _read_json(claim)
            if not isinstance(held, Mapping) or held.get("run") != self._run.run:
                raise _LostClaim()  # a job that hung past the quiet time and woke after another took its place

    def _update(self, **changes: object) -> Pathway:
        """Write the record with the progress as it is now. ``_LostClaim`` when another run owns the pathway."""

        self._check_claim()
        with learning_store.write_lock(self.home_root, self.target):
            current = learning_store.get_pathway(self.home_root, self.target, self.pathway_id)
            if current is None:
                raise _LostClaim()
            changed = replace(current, updated_at=learning_store.now_text(), revision=current.revision + 1, progress=self._progress(), **changes)  # type: ignore[arg-type]
            return learning_store.write_pathway(self.home_root, self.target, changed)

    def say(self, text: object) -> None:
        """One plain progress line: the progress file, the running step's ``detail``, the record, the caller."""

        from .find_jobs.progress import _append_line

        line = _plain(text, "Working")
        _append_line(self.work / _LINES_FILENAME, {"at": learning_store.now_text(), "step": self._step, "text": line, "calls": self.calls, "fetches": self.fetches})
        for item in self._steps:
            if item["id"] == self._step:
                item["detail"] = line
        self._update()
        if self._on_progress is not None:
            self._on_progress(line)

    def _mark(self, step: str, status: str, detail: str | None = None) -> None:
        for item in self._steps:
            if item["id"] != step:
                continue
            item["status"] = status
            if status == "running":
                item["started_at"] = learning_store.now_text()
            else:
                item["finished_at"] = learning_store.now_text()
            if detail is not None:
                item["detail"] = _plain(detail, "Working")
        self._step = step

    def _do(self, step: str, body: Callable[[], tuple[Any, bool]]) -> Any:
        """Run one step: ``running``, then ``done`` or ``skipped`` (its stored result was still valid)."""

        self._mark(step, "running")
        self._update()
        value, skipped = body()
        self._mark(step, "skipped" if skipped else "done")
        self._update()
        return value

    # -- the steps ---------------------------------------------------------------------------------------

    def _corpus(self, model: _JobModel, role_text: str) -> tuple[dict[str, Any], bool]:
        from . import learning_corpus

        stored = _read_json(self.work / _CORPUS_FILENAME)
        if (
            isinstance(stored, dict) and stored.get("schema_version") == learning_corpus.SCHEMA_VERSION and stored.get("status") == "done"
            and stored.get("role_text") == role_text and isinstance(stored.get("concepts"), list) and isinstance(stored.get("corpus"), dict)
        ):
            return stored, True
        filters = learning_corpus.corpus_filters(self.home_root, self.target)
        # The step files (titles, corpus-summary, vocabulary-raw, vocabulary, counts) are written as each is made: a
        # failed run can be read, and a resume asks again only for what has no stored answer.
        found = learning_corpus.run_corpus(
            self.home_root, role_text, model=model, filters=filters, progress=self.say, store=_CorpusWork(self.work),  # type: ignore[arg-type]
        )
        _write_json(self.work / _CORPUS_FILENAME, found)
        self.say(f"Reading {found['corpus'].get('with_text', 0)} postings")  # type: ignore[union-attr]
        return found, False

    def _course(self, model: _JobModel, fetcher: Any, role_text: str, corpus: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        from . import learning_course

        had = learning_course.CourseWork(self.work).read(learning_course.STEP_COURSE) is not None
        spent = (self.calls, self.fetches)
        course = learning_course.run_course_steps(
            self.work, model=model, fetcher=fetcher, role_text=role_text, corpus=corpus["corpus"], counted_concepts=corpus["concepts"],
            # The resume is ALWAYS the reference for known/some/new. This is the one place the master's lines are read
            # and the one call they are handed to (step 8, the lesson text).
            master_lines=learning_course.read_master_lines(self.home_root, self.target),
            cancel=self.cancelled, progress=self.say,
        )
        return course, had and spent == (self.calls, self.fetches)

    def _partial_course(self, role_text: str, corpus: Mapping[str, Any], code: str) -> dict[str, Any]:
        """The course of what is stored when the job may ask nothing more; ``_Failed(code)`` when that is too little.

        A module with no stored text is listed in ``dropped`` with ``code`` as its reason. No call, no fetch.
        """

        from . import learning_course as lc

        work = lc.CourseWork(self.work)
        curriculum = work.read(lc.STEP_CURRICULUM, lc.check_curriculum)
        sources = work.read(lc.STEP_SOURCES)
        if curriculum is None or not isinstance(sources, dict) or not isinstance(sources.get("lessons"), dict):
            raise _Failed(code, _MESSAGES[code])
        modules: dict[str, Any] = {}
        dropped: list[dict[str, object]] = []
        for module in curriculum["modules"]:
            lesson_ids = [lesson["id"] for lesson in module["lessons"] if sources["lessons"].get(lesson["id"])]
            stored = work.read(lc.module_step(module["id"]))
            if lesson_ids and stored is not None and lc.check_module(stored, lesson_ids):
                modules[module["id"]] = stored
            elif lesson_ids:
                dropped.append({"what": f"module:{module['id']}", "title": module["title"], "reason": code, "lessons": lesson_ids})
        gone = {str(item.get("what")) for item in sources.get("dropped", []) if isinstance(item, dict)}
        unsourced = [
            {"what": f"lesson:{lesson['id']}", "title": lesson["title"], "reason": code}
            for module in curriculum["modules"] for lesson in module["lessons"]
            if not sources["lessons"].get(lesson["id"]) and f"lesson:{lesson['id']}" not in gone
        ]
        course = lc.assemble_course(
            role_text, corpus["corpus"], corpus["concepts"], curriculum, {**sources, "dropped": [*sources.get("dropped", []), *unsourced]},
            {"modules": modules, "dropped": dropped, "log": []},
        )
        work.write(lc.STEP_COURSE, course)
        return course

    def _stored_paths(self, course: Mapping[str, Any]) -> dict[str, dict]:
        from . import learning_render

        ids = {concept["id"] for module in course.get("modules", []) for concept in module.get("concepts", [])}
        try:
            return learning_render.load_paths(self.work / _PATHS_DIR, ids)
        except (OSError, ValueError):
            return {}

    def _paths(self, model: _JobModel, fetcher: Any, course: Mapping[str, Any]) -> tuple[dict[str, dict], bool]:
        from . import learning_course as lc

        step = self._paths_step
        assert step is not None
        had = (self.work / _PATHS_DIR).is_dir()
        spent = (self.calls, self.fetches)
        sources = lc.CourseWork(self.work).read(lc.STEP_SOURCES) or {}
        # Built from the course and its verified sources only: no resume line is an argument of this step.
        answer = step(self.work, model=model, fetcher=fetcher, course=course, sources=sources, cancel=self.cancelled, progress=self.say)
        paths = {str(lesson_id): dict(path) for lesson_id, path in dict(answer.get("paths") or {}).items() if isinstance(path, Mapping)}
        # G4 names a lesson by its id: here it is the lesson's practice path that is not included, not the lesson.
        self._drop(answer.get("dropped"), kind="path")
        if answer.get("stopped"):
            self._paths_cut_short = True  # the fetch budget ended the step: the lessons it did not reach have no practice page
        return paths, had and spent == (self.calls, self.fetches)

    def _drop(self, items: object, *, kind: str | None = None) -> None:
        """Note what is not in the course: ``{what, reason}`` for the record, and with its title for the course's first page."""

        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            what = _plain(item.get("what"), "an item")
            if kind is not None and ":" not in what and what != "an item":
                what = f"{kind}:{what}"
            entry = {"what": what, "reason": _plain(item.get("reason"), "dropped")[:80]}
            if entry not in self._dropped:
                self._dropped.append(entry)
                self._listed.append({**{key: value for key, value in item.items() if key in ("title", "lessons")}, **entry})

    def _usable_paths(self, course: Mapping[str, Any], paths: Mapping[str, dict]) -> dict[str, dict]:
        """The paths the renderer accepts; one it refuses is dropped (``path_invalid``) and its lesson stays without a practice page."""

        from . import learning_render

        ids = {concept["id"] for module in course.get("modules", []) for concept in module.get("concepts", [])}
        kept: dict[str, dict] = {}
        for lesson_id, path in paths.items():
            if lesson_id not in ids:
                continue
            if learning_render.validate_path(path, lesson_id):
                self._drop([{"what": f"path:{lesson_id}", "reason": "path_invalid"}])
            else:
                kept[lesson_id] = path
        return kept

    def _render(self, course: Mapping[str, Any], paths: Mapping[str, dict]) -> tuple[Path, bool]:
        from . import learning_render

        site = self.work / _SITE_DIR
        shutil.rmtree(site, ignore_errors=True)
        errors = learning_render.validate_course(dict(course), dict(paths))
        if errors:
            raise _Failed("course_invalid", _plain(sorted(errors)[0], "The course did not pass the renderer's checks."))
        try:
            learning_render.build_course(dict(course), site, dict(paths))
        except learning_render.CourseError as exc:
            _write_json(self.work / _AUDIT_FILENAME, {"render": exc.errors})
            raise _Failed("course_render_failed", _plain(exc.errors[0] if exc.errors else "", "The course could not be drawn.")) from exc
        self.say(f"Drew {len(list(site.glob('*.html')))} pages")
        return site, False

    def _audit(self, site: Path, course: Mapping[str, Any], paths: Mapping[str, dict]) -> tuple[None, bool]:
        from . import learning_course as lc

        sources = lc.CourseWork(self.work).read(lc.STEP_SOURCES)
        lessons = sum(len(module.get("concepts", [])) for module in course.get("modules", []))
        self.say(f"Checking links for {lessons} lessons ({len(self._dropped)} dropped so far)")
        found = audit_site(site, course=course, paths=paths, verified=verified_urls(sources, paths))
        _write_json(self.work / _AUDIT_FILENAME, {"problems": found})
        if found:
            first = found[0]
            words = _GATE_WORDS.get(first["gate"], "a check of the finished course failed")
            raise _Failed(AUDIT_FAILED, _plain(f"The finished course did not pass its checks: {words} ({first['detail']})", f"The finished course did not pass its checks: {words}."))
        return None, False

    def _tokens(self) -> int:
        """The tokens of this job's metered calls so far (``call_metrics``: what the meter recorded for each call)."""

        from .call_metrics import total_metrics

        total = total_metrics(self._captured)
        return self._tokens_before + ((total.input_tokens or 0) + (total.output_tokens or 0) if total is not None else 0)

    def _cost(self) -> dict[str, object]:
        wall = self._seconds_before + time.monotonic() - self._began
        wait = self._wait_seconds_before + self._wait_seconds_now
        minutes = round(max(0.0, wall - wait) / 60, 1)
        tokens = self._tokens()
        note = f"about {round(tokens / 1000)}k tokens" if tokens >= 1000 else None
        return {"cli_model_calls": self.calls, "worker_minutes": minutes, "web_fetches": self.fetches, "web_searches": 0, "tokens": tokens or None, "note": note}

    def _import(self, site: Path) -> tuple[Pathway, bool]:
        """Store the course under this pathway: one write, in which the record becomes ``done`` as a GENERATED course."""

        def finish(stored: Pathway) -> Pathway:
            assert stored.course is not None
            self._mark(STEP_IMPORT, "done")
            self._step = None
            return replace(
                stored, source="requested", imported_from=None, error=None, error_code=None, progress=self._progress(),
                course=replace(stored.course, generated_at=learning_store.now_text()),
            )

        self._check_claim()
        try:
            result = learning_store.import_course(self.home_root, self.target, site, self._role_text, self._cost(), replace_id=self.pathway_id, finish=finish)
        except BaseException:
            self._mark(STEP_IMPORT, "running")  # it did not end: the failure below says why
            raise
        return result.pathway, False

    # -- the run -------------------------------------------------------------------------------------------

    def _stop_code(self, exc: BaseException) -> str | None:
        from .learning_course import LearningCourseStopped
        from .learning_fetch import STOP_CANCELLED, FetchStopped

        if isinstance(exc, _Stop):
            return exc.code
        if isinstance(exc, LearningCourseStopped):
            return CANCELLED
        if isinstance(exc, FetchStopped):
            return CANCELLED if exc.code == STOP_CANCELLED else CAP_REACHED
        return None

    def _steps_in_order(self, model: _JobModel, fetcher: Any) -> Pathway:
        from . import learning_course as lc

        role_text = self._role_text
        if self._paths_step is None:
            self._paths_step = paths_step()  # before any call is spent: without it no course can be finished
        try:
            corpus = self._do(STEP_CORPUS, lambda: self._corpus(model, role_text))
        except Exception as exc:  # noqa: BLE001 - a stop is told apart from a failure, which is raised again
            code = self._stop_code(exc)
            if code is None:
                raise
            raise _Failed(code, _MESSAGES[code]) from exc

        stopped: str | None = None
        try:
            course = self._do(STEP_COURSE, lambda: self._course(model, fetcher, role_text, corpus))
        except Exception as exc:  # noqa: BLE001 - as above
            stopped = self._stop_code(exc)
            if stopped is None:
                raise
            course = self._partial_course(role_text, corpus, stopped)
            self._mark(STEP_COURSE, "done", "Stopped: the lessons already written are kept")
        if stopped is None and self._budget.left == 0:
            stopped = CAP_REACHED  # the fetch budget ended the source checks: nothing more is fetched
        self._drop(course.get("dropped"))
        planned, kept = int(course.get("planned_lessons", 0)), int(course.get("kept_lessons", 0))
        status, reason = lc.course_status(planned, kept)
        if status != lc.STATUS_DONE:
            if stopped is not None:
                raise _Failed(stopped, _MESSAGES[stopped])
            raise _Failed(
                str(reason),
                f"Too few lessons passed the checks ({kept} of {planned}; a course needs {lc.DONE_PERCENT} percent). What was written is kept; resume to try the rest again.",
            )

        if stopped is None:
            try:
                paths = self._do(STEP_PATHS, lambda: self._paths(model, fetcher, course))
            except Exception as exc:  # noqa: BLE001 - as above
                stopped = self._stop_code(exc)
                if stopped is None:
                    raise
                paths = self._stored_paths(course)
                self._mark(STEP_PATHS, "done", "Stopped: the practice paths already verified are kept")
        else:
            paths = self._stored_paths(course)
            self._mark(STEP_PATHS, "skipped", "Stopped: no further practice path is written")
        paths = self._usable_paths(course, paths)
        # The index page lists what is not in the course and why: the lessons and modules G2 dropped, and G4's paths.
        shown = {**course, "dropped": [dict(item) for item in self._listed]}

        site = self._do(STEP_RENDER, lambda: self._render(shown, paths))
        self._do(STEP_AUDIT, lambda: self._audit(site, shown, paths))
        self._mark(STEP_IMPORT, "running")
        self._update()
        done, _skipped = self._import(site)
        return done

    def run(self, current: _Run | None = None) -> Pathway:
        """Do the steps (in the calling thread) and settle the record: ``done``, or ``failed`` with a code and a plain reason.

        ``current`` is the claim :func:`start` / :func:`resume` already took; without one the job claims the
        project itself (``learning_running`` when a live job has it).
        """

        from .call_metrics import capture_calls

        self._run = current or _claim(self.home_root, self.target, self.pathway_id)
        closers: list[Any] = []
        try:
            record = learning_store.get_pathway(self.home_root, self.target, self.pathway_id)
            if record is None:
                raise LearningJobError("pathway_not_found", f"there is no learning pathway {self.pathway_id!r}")
            self._role_text = record.role_text
            before = record.progress or {}
            self._calls_before, self._fetches_before = int(before.get("calls") or 0), int(before.get("fetches") or 0)  # type: ignore[call-overload]
            state = _read_json(self.work / _STATE_FILENAME)
            self._seconds_before = float(state.get("seconds") or 0) if isinstance(state, dict) else 0.0
            self._wait_seconds_before = float(state.get("wait_seconds") or 0) if isinstance(state, dict) else 0.0
            self._tokens_before = int(state.get("tokens") or 0) if isinstance(state, dict) else 0
            self._steps = [{"id": step, "status": "pending", "started_at": None, "finished_at": None, "detail": None} for step in STEPS]
            self._update(status="running", error=None, error_code=None)
            try:
                with capture_calls() as captured:
                    self._captured = captured
                    done = self._steps_in_order(*self._parts(closers))
                self._finish_state()
                return done
            except _LostClaim:
                raise
            except _Failed as exc:
                return self._fail(exc.code, str(exc))
            except KeyboardInterrupt:
                self._fail(CANCELLED, _MESSAGES[CANCELLED])
                raise
            except LearningError as exc:
                return self._fail(exc.code, _plain(str(exc), "The course could not be finished; resume to try again."))
            except Exception as exc:  # noqa: BLE001 - the record must end, whatever went wrong
                _logger.exception("learning job (%s): the job failed", self.pathway_id)
                return self._fail("learning_failed", f"The course could not be finished ({type(exc).__name__}); resume to try again.")
        except _LostClaim:
            _logger.warning("learning job (%s): another run owns this pathway; this one stopped", self.pathway_id)
            stored = learning_store.get_pathway(self.home_root, self.target, self.pathway_id)
            if stored is None:
                raise LearningJobError("pathway_not_found", f"there is no learning pathway {self.pathway_id!r}") from None
            return stored
        finally:
            for item in closers:
                try:
                    item.close()
                except Exception:  # noqa: BLE001 - closing a client never fails the job
                    pass
            _release(self.home_root, self.target, self._run)

    def _parts(self, closers: list[Any]) -> tuple[_JobModel, Any]:
        """The model and the fetcher of this run: the ones given, the test script's, else the real ones."""

        from .learning_fetch import FetchBudget, HttpxFetcher, PageFetcher

        self._budget = FetchBudget(self.max_fetches)
        script = _test_script() if self._model is None and self._fetcher_factory is None else None
        if script is not None:
            if self._paths_step is None:
                self._paths_step = lambda *_args, **_kwargs: {"paths": {}, "dropped": []}
            return _JobModel(self, _ScriptedModel(script)), _ScriptedPages(dict(script.get("pages") or {}), self._budget, self.cancelled)
        if self._fetcher_factory is not None:
            fetcher = self._fetcher_factory(self._budget, self.cancelled)
        else:
            network = HttpxFetcher()
            closers.append(network)
            fetcher = PageFetcher(network, budget=self._budget, cancel=self.cancelled)
        inner = self._model
        if inner is None:
            from .learning_course import MeteredCourseModel

            inner = MeteredCourseModel(self.home_root, self.target)
            closers.append(inner)
        return _JobModel(self, inner), fetcher

    def _finish_state(self) -> None:
        try:
            _write_json(
                self.work / _STATE_FILENAME,
                {
                    "seconds": round(self._seconds_before + time.monotonic() - self._began, 1),
                    "wait_seconds": round(self._wait_seconds_before + self._wait_seconds_now, 1),
                    "tokens": self._tokens(),
                },
            )
        except OSError:
            pass

    def _fail(self, code: str, message: str) -> Pathway:
        """Settle the record ``failed``. The work folder stays: a resume goes on from what is stored."""

        self._finish_state()
        plain = _plain(message, "The course could not be finished; resume to try again.")
        for item in self._steps:
            if item["status"] == "running":
                item["status"], item["finished_at"], item["detail"] = "failed", learning_store.now_text(), plain
        from .find_jobs.progress import _append_line

        _append_line(self.work / _LINES_FILENAME, {"at": learning_store.now_text(), "step": self._step, "text": plain, "code": code, "calls": self.calls, "fetches": self.fetches})
        failed = self._update(status="failed", error=plain, error_code=code)
        if self._on_progress is not None:
            self._on_progress(plain)
        return failed


# ---------------------------------------------------------------------------
# Start, resume, cancel
# ---------------------------------------------------------------------------


JobFactory = Callable[[Path, Path, str], LearningJob]


def _thread(job: LearningJob, current: _Run) -> None:
    def work() -> None:
        try:
            job.run(current)
        except BaseException:  # noqa: BLE001 - a thread of the server never dies with a traceback on stderr only
            _logger.exception("learning job (%s): the run ended with an error", job.pathway_id)

    current.thread = threading.Thread(target=work, name=f"scout-learning-{job.pathway_id}", daemon=True)
    current.thread.start()


def _new(home_root: Path, target: Path, role_text: str) -> tuple[Pathway, _Run]:
    """A new ``queued`` request that this process has the project's job slot for."""

    learning_store.clean_role_text(role_text)  # refused before anything is claimed or stored
    current = _claim(home_root, target, None)
    try:
        pathway = learning_store.create_request(home_root, target, role_text)
        with learning_store.write_lock(home_root, target):
            _own(home_root, target, current, pathway.id)
    except BaseException:
        _release(home_root, target, current)
        raise
    return pathway, current


def _resumable(home_root: Path, target: Path, pathway_id: str) -> Pathway:
    pathway = learning_store.get_pathway(home_root, target, pathway_id)
    if pathway is None:
        raise LearningJobError("pathway_not_found", f"there is no learning pathway {pathway_id!r}")
    status = live_status(home_root, target, pathway)
    if pathway.source != "requested" or status == "done":
        raise LearningJobError("not_resumable", "this pathway has its course already; there is nothing to resume")
    if status == "running" or (status == "queued" and (work_dir(home_root, target, pathway_id) / _OWNER_FILENAME).is_file()):
        raise LearningJobError(LEARNING_RUNNING, "this course is being generated now")
    return pathway


def start(home_root: Path, target: Path, role_text: str, *, job_factory: JobFactory | None = None) -> Pathway:
    """Approved: store the request (``queued``) and generate its course on a thread of this process. Back at once."""

    pathway, current = _new(Path(home_root), Path(target), role_text)
    _thread((job_factory or LearningJob)(Path(home_root), Path(target), pathway.id), current)
    return pathway


def resume(home_root: Path, target: Path, pathway_id: str, *, job_factory: JobFactory | None = None) -> Pathway:
    """Go on with a ``failed`` or ``interrupted`` job on a thread of this process: the same id, the stored results kept."""

    home_root, target = Path(home_root), Path(target)
    _resumable(home_root, target, pathway_id)
    current = _claim(home_root, target, pathway_id)
    try:
        # Before the answer goes out: a reader that asks right after it never sees the earlier failure again.
        with learning_store.write_lock(home_root, target):
            stored = learning_store.get_pathway(home_root, target, pathway_id)
            if stored is None:
                raise LearningJobError("pathway_not_found", f"there is no learning pathway {pathway_id!r}")
            pathway = learning_store.write_pathway(
                home_root, target,
                replace(stored, status="running", error=None, error_code=None, updated_at=learning_store.now_text(), revision=stored.revision + 1),
            )
    except BaseException:
        _release(home_root, target, current)
        raise
    _thread((job_factory or LearningJob)(home_root, target, pathway_id), current)
    return pathway


def run_foreground(
    home_root: Path, target: Path, *, role_text: str | None = None, pathway_id: str | None = None,
    on_progress: Callable[[str], object] | None = None, job_factory: Callable[..., LearningJob] | None = None,
) -> Pathway:
    """The CLI's run: the same job, the same one-at-a-time claim, in the calling thread. A new request or a resume."""

    home_root, target = Path(home_root), Path(target)
    if pathway_id is None:
        pathway, current = _new(home_root, target, str(role_text or ""))
    else:
        pathway = _resumable(home_root, target, pathway_id)
        current = _claim(home_root, target, pathway_id)
    return (job_factory or LearningJob)(home_root, target, pathway.id, on_progress=on_progress).run(current)


def cancel(home_root: Path, target: Path, pathway_id: str) -> bool:
    """Ask the job of ``pathway_id`` to stop (any process): no further call or fetch starts. False when none runs."""

    home_root, target = Path(home_root), Path(target)
    pathway = learning_store.get_pathway(home_root, target, pathway_id)
    if pathway is None:
        raise LearningJobError("pathway_not_found", f"there is no learning pathway {pathway_id!r}")
    work = work_dir(home_root, target, pathway_id)
    if pathway.status not in ("queued", "running") or not _job_live(work):
        return False
    owner = _read_json(work / _OWNER_FILENAME)
    with _LOCK:
        current = _RUNS.get(str(owner.get("run"))) if isinstance(owner, Mapping) else None
    if current is not None:
        current.cancel.set()
    (work / _CANCEL_FILENAME).write_text(learning_store.now_text(), encoding="utf-8")
    return True


__all__ = [
    "DEFAULT_CALLS",
    "DEFAULT_FETCHES",
    "DEFAULT_MINUTES",
    "MAX_CALLS",
    "MAX_FETCHES",
    "QUIET_SECONDS",
    "RETRY_ATTEMPTS",
    "RETRY_BASE_SECONDS",
    "SCHEMA_ESTIMATE",
    "STEPS",
    "WARNING_KEY",
    "LearningJob",
    "LearningJobError",
    "ask_response",
    "audit_site",
    "cancel",
    "estimate",
    "estimate_words",
    "live_status",
    "paths_step",
    "resume",
    "run_foreground",
    "running_id",
    "start",
    "verified_urls",
    "wait_for_jobs",
    "work_dir",
]
