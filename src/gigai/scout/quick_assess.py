"""P5 (v0.1.9): one standalone ("quick") assessment -- no run, no graph.

``run_quick_assessment`` is what ``POST /api/assess`` and ``gigai scout
assess`` both call: resolve the job (P4 ``job_input.resolve_job``), the
resume (P4 ``resume_input``), the effective preferences (find-jobs.json
countries/visa + the profile's titles, request overrides winning), the model
adapter, then ``assessment_core.assess_once`` -- the exact prompt/parse/
retry path the graph's assess node uses (P1) -- and store the answer as
plain JSON under the GigAI home.

Seams kept exactly where the plan's constraints put them:

- C1: the adapter is resolved through
  ``proposal_execution.resolve_model_adapter`` looked up as a MODULE
  ATTRIBUTE at call time, never imported by value, so the production test
  transport (``bindings._patch_test_model_transport``) and the unit tests
  that patch that name intercept this path too.
- C11: the adapter kind comes from ``request.model_target`` or
  ``find-jobs.json``'s ``default_model_target`` and is mapped to a configured
  target by ``_resolve_configured_target_name_for_adapter`` -- reused from
  ``proposal_execution``, not copied.
- Parsing: ``proposals.validate_assessment_bounds`` (the run path's own
  strict bounds, extracted in P5) then ``AssessmentBody.from_json``.

Storage: ``<home>/scout/<project_id>/quick_assess/<profile_id|ephemeral>/
<sha256(job_identity)>.json`` -- one file per (resume identity, job), latest
wins, ``created_at`` kept from the first write (the same plain atomic-write
JSON shape ``interview_prep/storage.py`` uses; operator answer 2: re-
assessments live here, never in ``runs/*/outputs``).  The stored file is the
``AssessResponse`` JSON: it never contains resume text or PASTED job text.
The text fetched from a public posting URL is stored with it as
``posting_text`` (uat-bug-014), so the job page has the posting to show.

Rank score: a quick assessment carries no rank score of its own (SCOPE-ADD-3 C2:
the Jev score is gone; ranking is the run's model rank step).  A file stored
before that may still hold ``rank_score``/``rank_skip_reason`` and still reads.

Origin (assess-origin-field): the stored item says where the operator
started it, ``quick_assess`` or ``job_page``.  See ``_origin_for``.

Timeouts (operator answer 1): the call is synchronous.  An adapter timeout
(the codex CLI's 120 s default, the local Ollama transport's own timeout)
surfaces as a ``ModelInvocationError`` raised FROM ``subprocess.
TimeoutExpired``/``httpx.TimeoutException``; ``assess_once`` folds that into
``MODEL_UNAVAILABLE`` like any other transport failure, so this module wraps
the port to observe the cause chain and reports ``assess_timeout`` (504)
instead of ``model_unavailable`` (503) when it was a timeout.  Only when the
model test seam is on (``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``) does
``GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS`` add a deadline of its own around the
fake model call (the fixture ``MockTransport`` has no socket to time out).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading

import httpx

from ..adapters.factory import AdapterFactoryError
from ..adapters.port import ModelInvocationError
from ..canonical import digest_imported_bytes, parse_json_bytes
from ..config import GigAIConfig, load_config
from ..model_targets import ModelTargetResolutionError
from .assessment_basis import posting_sha256
from .call_metrics import KIND_ASSESS, CallMeter
from .assessment_core import INSTRUCTIONS_DIGEST, AssessJob, build_assess_context
from .assessment_core import POSTING_INCOMPLETE_MESSAGE, assess_once, assess_prompt_version, constraints_digest
from . import story_bank
from .find_jobs.assess_contracts import (
    ORIGIN_QUICK_ASSESS,
    AssessmentBody,
    AssessRequest,
    AssessResponse,
    ResolvedJob,
    VerdictHistoryEntry,
)
from .find_jobs.contracts import (
    FindJobsConfig,
    FindJobsContractError,
    ModelTarget,
    NotAssessedReason,
    Producer,
    ProfileRef,
    StoryBankStamp,
    UsageBlock,
)
from .find_jobs.discovery.storage import atomic_write, project_id
from .find_jobs.job_input import job_fetch_client, resolve_job
from .find_jobs.job_source import resolve_job_for_assessment
from .find_jobs.resume_input import resolve_preferences, resolve_profile, resolve_resume, resume_for_profile

#: Directory segment for assessments against pasted (ephemeral) resume text.
EPHEMERAL_RESUME_KEY = "ephemeral"

#: A list filter names ONE directory segment under ``quick_assess/``: a
#: profile id (``profile_<uuid>``) or ``"ephemeral"`` -- never a path.
_SAFE_RESUME_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_PRODUCER_CALLABLE = "scout.assess"
_PRODUCER_VERSION = "1"
_PRODUCER_ACTOR = "scout-assess"

_logger = logging.getLogger("gigai.scout.server")

#: Exception types that mean "the model call ran out of time" when found
#: anywhere in a raised adapter error's cause chain.
_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException)


class QuickAssessError(ValueError):
    """A quick assessment could not run; ``code`` is the API/CLI error code.

    ``reason`` (0110-10-11) names WHICH rule refused, for a code two rules share:
    ``posting_requirements_unreadable`` is :data:`REASON_TOO_FEW_REQUIREMENTS` or
    :data:`REASON_NO_REQUIREMENTS`. ``None`` for every other code.
    """

    def __init__(self, code: str, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- storage -------------------------------------------------------------------------


def quick_assess_dir(home_root: Path, target: Path) -> Path:
    return home_root / "scout" / project_id(home_root, target) / "quick_assess"


def resume_key(profile_id: str | None) -> str:
    """The per-resume directory segment: the profile id, or ``"ephemeral"``."""

    return EPHEMERAL_RESUME_KEY if profile_id is None else profile_id


def quick_assess_path(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> Path:
    digest = digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")
    return quick_assess_dir(home_root, target) / resume_key(profile_id) / f"{digest}.json"


#: 0.1.10.7 M2: assessments of a job against the profile's TAILORED resume, beside ``quick_assess/``
#: (same ``<profile_id>/<sha256(job_identity)>.json``). A variant never replaces the base assessment:
#: the base verdict stays the job's verdict.
TAILORED_VARIANT_DIR = "quick_assess_tailored"


@dataclass(frozen=True)
class AssessVariant:
    """Assess the same job for the same profile against ``resume_text`` (its tailored resume) instead of the base resume."""

    resume_text: str = field(repr=False)


def tailored_variant_path(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> Path:
    digest = digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")
    return home_root / "scout" / project_id(home_root, target) / TAILORED_VARIANT_DIR / resume_key(profile_id) / f"{digest}.json"


def read_quick_assessment(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> AssessResponse | None:
    """The stored (base) assessment of ``job_identity`` for ``profile_id``, or ``None``."""

    return _read_stored(quick_assess_path(Path(home_root), Path(target), profile_id, job_identity))


def read_tailored_variant(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> AssessResponse | None:
    """The stored assessment of ``job_identity`` against ``profile_id``'s tailored resume, or ``None``."""

    return _read_stored(tailored_variant_path(Path(home_root), Path(target), profile_id, job_identity))


def _read_stored(path: Path) -> AssessResponse | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return AssessResponse.from_json(parse_json_bytes(path.read_bytes()))
    except Exception:
        return None


def list_quick_assessments(
    home_root: Path, target: Path, *, profile_id: str | None = None, verdict: str | None = None
) -> tuple[AssessResponse, ...]:
    """Every stored quick assessment for this project, newest ``updated_at`` first.

    ``profile_id`` narrows to one resume identity (``"ephemeral"`` selects
    the pasted-resume assessments); ``verdict`` narrows to one verdict value.
    Files that no longer parse are skipped, never raised.

    P3 (v0.1.9): ordered by ``updated_at`` (last ASSESSED, which a
    re-assessment after answers bumps) rather than P5's ``created_at`` --
    P5 had nothing that recorded a re-assessment, so the two were always
    equal; P3's Q&A loop is exactly what makes them diverge, and the
    re-assessed item belongs first.
    """

    if profile_id is not None and not _SAFE_RESUME_KEY.fullmatch(profile_id):
        raise QuickAssessError("invalid_value", "profile_id filter is not a profile id")
    try:
        root = quick_assess_dir(home_root, target)
    except Exception as exc:
        raise QuickAssessError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    if not root.is_dir():
        return ()
    subdirs = [root / profile_id] if profile_id is not None else sorted(p for p in root.iterdir() if p.is_dir())
    items: list[AssessResponse] = []
    for subdir in subdirs:
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("*.json")):
            stored = _read_stored(path)
            if stored is None:
                continue
            if verdict is not None and (stored.result.verdict is None or stored.result.verdict.value != verdict):
                continue
            items.append(stored)
    items.sort(key=lambda item: (item.updated_at, item.stored_path), reverse=True)
    return tuple(items)


def find_quick_assessment_by_job_identity(
    home_root: Path, target: Path, job_identity: str
) -> AssessResponse | None:
    """The stored quick assessment for ``job_identity``, across every resume
    identity directory (P3's re-assess: the request names only the job, not
    which profile/ephemeral resume it was originally assessed against)."""

    for item in list_quick_assessments(home_root, target):
        if item.job.job_identity == job_identity:
            return item
    return None


# --- model binding observation --------------------------------------------------------


def _is_timeout(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _TIMEOUT_TYPES):
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass
class _ObservedPort:
    """Wraps a resolved adapter port to (a) notice a timeout in whatever it
    raises and (b) optionally enforce a deadline of its own (test seam only)."""

    inner: object
    deadline_seconds: float | None = None
    timed_out: bool = False
    #: The model id the last successful call answered with (``InvocationResult.resolved_model``).
    resolved_model: str | None = None

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "")

    def invoke(self, request):
        try:
            if self.deadline_seconds is None:
                result = self.inner.invoke(request)
            else:
                result = self._invoke_with_deadline(request)
        except BaseException as exc:
            if _is_timeout(exc):
                self.timed_out = True
            raise
        self.resolved_model = getattr(result, "resolved_model", None) or self.resolved_model
        return result

    def _invoke_with_deadline(self, request):
        outcome: dict[str, object] = {}

        def run() -> None:
            try:
                outcome["result"] = self.inner.invoke(request)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
                outcome["error"] = exc

        worker = threading.Thread(target=run, name="gigai-quick-assess-model", daemon=True)
        worker.start()
        worker.join(self.deadline_seconds)
        if worker.is_alive():
            raise TimeoutError(f"model call exceeded the {self.deadline_seconds:g} s assess timeout")
        if "error" in outcome:
            raise outcome["error"]  # type: ignore[misc]
        return outcome["result"]



@dataclass(frozen=True)
class _ObservedBinding:
    inner: object
    port: _ObservedPort = field(repr=False)

    def request(self, **kwargs):
        return self.inner.request(**kwargs)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


def _seam_deadline_seconds() -> float | None:
    """``GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS``, read ONLY while the model test seam is on."""

    from .find_jobs import bindings

    if not bindings._test_model_enabled():
        return None
    return bindings._test_assess_timeout_seconds()


# --- verdict history (Q4a) ----------------------------------------------------------

#: ``VerdictHistoryEntry.trigger`` for the first assessment of a job.
TRIGGER_ASSESS = "assess"
#: ... for a later assessment with no answer in between (CLI/API assess again).
TRIGGER_REASSESS = "reassess"
#: ... prefix for a re-assessment after ``POST /api/answers`` (``answer:<question_id>``).
TRIGGER_ANSWER_PREFIX = "answer:"


def _history_with(previous: AssessResponse | None, entry: VerdictHistoryEntry) -> tuple[VerdictHistoryEntry, ...]:
    """The stored history plus ``entry`` (operator answer 4: re-assess APPENDS).

    A file written before the history field existed carries none; its one
    known state (the verdict it holds, at its ``updated_at``) is
    reconstructed as the first entry so the original assessment is never
    lost from the timeline the job page shows.
    """

    entries: list[VerdictHistoryEntry] = list(previous.history) if previous is not None else []
    if previous is not None and not entries:
        entries.append(
            VerdictHistoryEntry(
                at=previous.updated_at or previous.created_at,
                verdict=previous.result.verdict,
                trigger=TRIGGER_ASSESS,
            )
        )
    entries.append(entry)
    return tuple(entries)


# --- origin (assess-origin-field) -----------------------------------------------------


def _origin_for(request: AssessRequest, previous: AssessResponse | None) -> str | None:
    """The ``origin`` to store for this assessment.

    - The request names one: that is stored (the job page sends
      ``job_page``; "+ Assess a job" and ``gigai scout assess`` send
      ``quick_assess``).
    - The request names none and the job was assessed before: the stored
      origin stays as it is, ``None`` included.  This is every
      re-assessment after an answer (``POST /api/answers``, ``gigai scout
      answer --reassess``): answering never moves a posting between the
      UI's lists, and a file written before this field stays without one.
    - The request names none and the job is new: ``quick_assess`` (a bare
      ``POST /api/assess``).
    """

    if request.origin is not None:
        return request.origin
    if previous is not None:
        return previous.origin
    return ORIGIN_QUICK_ASSESS


# --- unreadable-posting guard (uat-bug-029) -------------------------------------------

#: What the operator is told when the posting text carries no requirements.
POSTING_UNREADABLE_MESSAGE = "Couldn't read this posting's requirements"

# Words that mark a requirements-like section or bullet in real posting text.
_REQUIREMENT_CUES = re.compile(
    r"requirements?|qualifications?|what you(?:'|\u2019)?ll need|what you need|you have|you(?:'|\u2019)?ve|"
    r"must[- ]have|nice[- ]to[- ]have|preferred|minimum|proficien|experience (?:with|in)|"
    r"\d+\+?\s*years?|years? of|skills?\b|responsibilities|you will|you(?:'|\u2019)ll|about the role|who you are",
    re.IGNORECASE,
)
_NO_STATED_REQUIREMENTS = "no stated requirements"


def _has_requirement_cue(text: str) -> bool:
    return _REQUIREMENT_CUES.search(text) is not None


#: 0110-8-09: why a model call that answered left no stored assessment (the ``QuickAssessError`` code and the call's ``error_code``).
ERROR_POSTING_UNREADABLE = "posting_requirements_unreadable"
ERROR_NOT_STORED = "assessment_not_stored"
#: 0110-10-11: the two rules behind ``posting_requirements_unreadable`` (``QuickAssessError.reason``, a batch failure's
#: ``reason``). The model answered in both; nothing is stored. Until now a batch said only the shared code, so a posting
#: refused by one rule could not be told from one refused by the other.
#: - the model said "Matched" on fewer than three requirement rows for a posting of 1,200+ characters
#:   (``assessment_core.posting_looks_incomplete``, uat-bug-046: the text looks cut off);
REASON_TOO_FEW_REQUIREMENTS = "matched_on_too_few_requirements"
#: - the text has no requirement wording at all AND the model found no requirement (:func:`posting_requirements_unreadable`, uat-bug-029).
REASON_NO_REQUIREMENTS = "no_requirements_in_text"
POSTING_UNREADABLE_REASONS: tuple[str, ...] = (REASON_TOO_FEW_REQUIREMENTS, REASON_NO_REQUIREMENTS)


def _has_real_requirement(body: AssessmentBody) -> bool:
    return any(row.requirement.strip().lower().rstrip(".") != _NO_STATED_REQUIREMENTS for row in body.matrix)


def posting_requirements_unreadable(text: str, body: AssessmentBody) -> bool:
    """True when this answer must NOT be presented as an assessment.

    The rule needs BOTH: the posting text has no requirement-like cue
    (no "requirements"/"qualifications"/"you have"/"N years"/"skills"/...
    anywhere) AND the model found no real requirement (an empty matrix, or
    only "No stated requirements").  Either alone is not enough: a real
    posting without headings that yields real rows stays assessed, and a
    posting with requirement wording whose model answer is "No stated
    requirements" stays the legitimate requirement-free path.  A third
    condition: the text must not read as running prose (``_reads_as_prose``),
    so a short cue-free blurb is assessed while a list-of-names scrape is
    refused.  Residual: a cue-free blurb written as short lines is refused.
    A previously stored bad Matched file is left in place (never deleted).
    """

    return not _has_requirement_cue(text) and not _has_real_requirement(body) and not _reads_as_prose(text)


#: A line this long (in words) is running prose, not a nav/list label.
_PROSE_LINE_WORDS = 8
_PROSE_MIN_WORDS = 15
_PROSE_MIN_SHARE = 0.6


def _reads_as_prose(text: str) -> bool:
    """A short real blurb: at least 15 words and 60% of them in lines of 8+ words.

    The scraped NexHealth page (integration names, nav labels, a few slogans)
    puts only ~42% of its words in long lines, so it stays unreadable; a
    one-paragraph "we are hiring" blurb is prose and is assessed.
    """

    counts = [len(line.split()) for line in text.splitlines() if line.strip()]
    total = sum(counts)
    if total < _PROSE_MIN_WORDS:
        return False
    return sum(n for n in counts if n >= _PROSE_LINE_WORDS) / total >= _PROSE_MIN_SHARE


# --- the assessment ----------------------------------------------------------------


def _parse_body(raw: Mapping[str, object]) -> AssessmentBody:
    from . import proposals

    proposals.validate_assessment_bounds(raw)
    return AssessmentBody.from_json(dict(raw))


def _config_location(target: Path) -> str:
    """``find-jobs.json``'s ``location`` (the operator's own "Denver, CO"),
    tolerantly: missing/unreadable/starter/null -> ``""`` (rendered
    "unknown" by ``assessment_core.render_assess_prompt``).

    assess-prompt-v2 (v0.1.9), operator decision: the candidate's location
    reaches the prompt as {{candidate_location}} so assess.md rule 4 can
    decide a posting's state/province restriction without asking; a request
    ``preferences.location`` overrides it (see ``run_quick_assessment``).
    """

    path = target / "find-jobs.json"
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        return FindJobsConfig.from_json(parse_json_bytes(path.read_bytes())).location or ""
    except Exception:
        return ""


def _config_work_mode(home_root: Path, target: Path) -> str:
    """The default profile's work mode, as a run for it would seal it (0110-038).

    ``find-jobs.json``'s ``work_mode``, else the setup's saved answer
    (``effective_config.default_search_settings``); missing or unreadable ->
    ``""`` (no CANDIDATE WORK MODE paragraph in the prompt).
    """

    from .find_jobs.effective_config import default_search_settings

    try:
        settings = default_search_settings(home_root=home_root, target=target)
    except Exception:  # noqa: BLE001 - a missing or unreadable setting means no work-mode paragraph, never a failed assessment
        return ""
    return "" if settings is None else settings.work_mode


def candidate_location_and_work_mode(preferences, profile, *, home_root: Path, target: Path) -> tuple[str, str]:
    """The candidate's own location and work mode, as the assess prompt gets them.

    ``preferences`` are the effective ones (``resolve_preferences``); its
    ``location`` is set only when the request carried one. One rule for the
    assessment itself and for ``assessment_basis`` (what a stored assessment
    is compared with), so the two cannot drift.

    Location (assess-prompt-v2): the request's ``preferences.location`` when
    given, else the profile's own (0110-022: a profile with its own search
    settings is assessed for ITS location), else find-jobs.json's. Work mode
    (0110-038): the profile's own, else the default's (the shared
    find-jobs.json, filled from the setup's saved answer like a run's sealed
    config); "any"/none adds nothing to the prompt.
    """

    own_settings = None if profile is None else profile.search_settings
    if preferences.location is not None:
        location = preferences.location
    elif own_settings is not None:
        location = own_settings.location or ""
    else:
        location = _config_location(target)
    work_mode = own_settings.work_mode if own_settings is not None else _config_work_mode(home_root, target)
    return location, work_mode


#: A model id as an adapter names it (``claude-opus-5-5``, ``gpt-5.1-codex``, ``llama3.1:8b``): never free text.
_MODEL_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}\Z")


def _model_id(value: str | None) -> str | None:
    """``value`` when it is shaped like a model id, else ``None`` (nothing else is stored as one)."""

    return value if value is not None and _MODEL_ID.fullmatch(value) else None


def _default_model_target(target: Path) -> ModelTarget:
    """``find-jobs.json``'s ``default_model_target``, tolerantly (missing/
    unreadable/starter -> the contract default, ``ollama_local``)."""

    path = target / "find-jobs.json"
    if path.is_symlink() or not path.is_file():
        return ModelTarget.OLLAMA_LOCAL
    try:
        return FindJobsConfig.from_json(parse_json_bytes(path.read_bytes())).default_model_target
    except Exception:
        return ModelTarget.OLLAMA_LOCAL


def _resolve_workpad(home_root: Path, target: Path):
    from ..workpad import resolve_workpad

    try:
        return resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:
        raise QuickAssessError("profile_unavailable", "no Scout gig is available for this folder; run `gigai scout install` or pass --resume-text") from exc


def _apply_job_overrides(job: ResolvedJob, request: AssessRequest) -> ResolvedJob:
    title = request.job.title if request.job.title is not None else job.title
    company = request.job.company if request.job.company is not None else job.company
    if title == job.title and company == job.company:
        return job
    return ResolvedJob(
        job_identity=job.job_identity,
        source_url=job.source_url,
        normalized_url=job.normalized_url,
        fetch_kind=job.fetch_kind,
        title=title,
        company=company,
        location=job.location,
        text=job.text,
        text_sha256=job.text_sha256,
    )


def _resolve_binding(config: GigAIConfig, model_target: ModelTarget, *, home_root: Path) -> _ObservedBinding:
    from . import proposal_execution
    from .find_jobs import bindings

    # Inert unless GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1: the same fixture
    # transport the run path installs before its first model call, installed
    # here too so a server that has never started a run still intercepts.
    bindings._patch_test_model_transport(config)
    try:
        adapter_target = proposal_execution._resolve_configured_target_name_for_adapter(config, model_target.value)
        binding = proposal_execution.resolve_model_adapter(config, adapter_target, home_root=home_root)
    except (
        AdapterFactoryError,
        ModelTargetResolutionError,
        proposal_execution.ScoutProposalExecutionError,
        KeyError,
        # uat-bug-035: a CLI target whose executable is not on PATH
        # ("claude executable is not available on PATH"; codex the same).
        ModelInvocationError,
    ) as exc:
        raise QuickAssessError("model_target_unavailable", str(exc) or "the configured model target or credential is unavailable") from exc
    return _ObservedBinding(binding, _ObservedPort(binding.port, _seam_deadline_seconds()))


def run_quick_assessment(
    request: AssessRequest,
    *,
    home_root: Path,
    target: Path,
    config: GigAIConfig | None = None,
    trigger: str | None = None,
    resolved_job: ResolvedJob | None = None,
    variant: AssessVariant | None = None,
) -> AssessResponse:
    """Assess ``request.job`` against ``request.resume`` and store the answer.

    0.1.10.7 M2 (the pipeline): ``resolved_job`` is the job already resolved
    (nothing is fetched); ``variant`` assesses against the tailored resume's
    text and stores the answer at ``tailored_variant_path``, never over the
    base assessment.

    ``trigger`` names what caused this assessment in the stored verdict
    history (Q4a): ``None`` (the default) records ``"assess"`` for a job
    never assessed before and ``"reassess"`` otherwise; ``POST /api/answers``
    passes ``"answer:<question_id>"``.

    Raises ``QuickAssessError`` with one of: ``job_input_invalid``,
    ``resume_input_invalid``, ``invalid_value`` (bad URL), ``job_text_unavailable``,
    ``job_fetch_failed``, ``profile_not_found``, ``profile_unavailable``,
    ``resume_unavailable``, ``resume_digest_mismatch``, ``model_target_unavailable``,
    ``model_unavailable``, ``model_denied``, ``assess_timeout``, ``model_output_invalid``,
    ``posting_requirements_unreadable``, ``target_unavailable``, ``assessment_not_stored``.

    0110-8-09, the invariant: a model call of this assessment is ``ok`` in the
    call metrics ONLY when its answer was stored. An answer that is withheld
    (``posting_requirements_unreadable``) or cannot be written
    (``assessment_not_stored``) settles its call as an error with that code, and
    the caller gets the same code: a success never leaves nothing behind.
    """

    home_root = Path(home_root)
    target = Path(target)

    # 1. Job text (public data; network only for a URL).
    try:
        if resolved_job is not None:
            job = resolved_job
        else:
            # 0110-10-04: the posting Scout already holds (the index's, or the one the stored assessment was
            # made on) before the URL; a re-assessment never becomes a scrape of the company's page.
            job = resolve_job_for_assessment(
                request.job, home_root=home_root, target=target, open_client=lambda: job_fetch_client(),
                resolve=lambda job_input_, client: resolve_job(job_input_, client=client, home_root=home_root),
            )
    except FindJobsContractError as exc:
        raise QuickAssessError(exc.code, str(exc)) from exc
    # PL2: the posting's digest as fetched (the index's ``content_sha256``), before any title override.
    posting_digest = posting_sha256(job.title, job.text)
    job = _apply_job_overrides(job, request)

    # 2. Resume identity + text (the pinned profile resume, or ephemeral).
    profile = None
    resolved = None
    try:
        if request.resume.is_ephemeral:
            resume = resolve_resume(request.resume, resolved=None, home_root=home_root, target=target)  # type: ignore[arg-type]
        else:
            resolved = _resolve_workpad(home_root, target)
            profile = resolve_profile(request.resume, resolved=resolved, home_root=home_root, target=target)
            assert profile is not None
            resume = resume_for_profile(profile, resolved=resolved, home_root=home_root, target=target)
    except FindJobsContractError as exc:
        raise QuickAssessError(exc.code, str(exc)) from exc

    # 3. Effective preferences: find-jobs.json (visa, countries) + profile
    #    titles, with the request's own values overriding both.
    preferences = resolve_preferences(request.preferences, target=target, profile=profile)
    assert preferences.countries is not None and preferences.titles is not None
    assert preferences.visa_sponsorship_required is not None
    # 3b. The candidate's own location (assess-prompt-v2): the request's
    #     ``preferences.location`` when given, else find-jobs.json's
    #     ``location``. Kept OUT of the echoed ``preferences`` unless the
    #     request carried it, so a stored/served response's preferences
    #     object is unchanged for every caller that never sends one.
    # 0110-022: a profile with its own search settings is assessed for ITS
    #     location, not the default profile's.
    # 0110-038: and for its own work mode ("any"/none adds nothing to the prompt).
    candidate_location, candidate_work_mode = candidate_location_and_work_mode(
        preferences, profile, home_root=home_root, target=target
    )

    # 4. Storage path first, so the response can name it and a prior
    #    ``created_at`` survives a re-assessment.
    try:
        if variant is not None:
            resume = replace(resume, text=variant.resume_text)
            path = tailored_variant_path(home_root, target, resume.profile_id, job.job_identity)
        else:
            path = quick_assess_path(home_root, target, resume.profile_id, job.job_identity)
    except Exception as exc:
        raise QuickAssessError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    previous = _read_stored(path)

    # 4b. Prior answers (P3's Q&A loop), user-level since 0.1.10.7 C: every
    #     answer the user gave, for any profile and any posting, rendered
    #     into the prompt so the model never re-asks something already
    #     answered (assess.md rule 8). ``bank_answers`` is the same answers
    #     as short lines (id, the question as asked, a one-line answer) for
    #     the STORY BANK paragraph: a requirement worded differently reuses
    #     the answer in this same call. ``for_job`` adds the few stories that
    #     match THIS posting (searched locally) to those lines, as evidence.
    #     A pasted resume reads the same bank. No gig: none, not fatal.
    #     One builder for every path that renders the assess prompt
    #     (``story_bank.assess_bank``: this, assess-all and a find-jobs run).
    bank = story_bank.assess_bank(home_root=home_root, target=target, profile_id=resume.profile_id, resume_text=resume.text)
    job_bank = bank.for_job(title=job.title, text=job.text)

    # 4c. 0.1.10.9 master P7: with a master resume stored, the prompt's RESUME for a profile is the evidence view of
    #     the master for THIS posting (``assess_master``), not the profile's 2 pages. ``None`` (no master, a pasted
    #     resume, a resume replaced by hand, the tailored variant, or the switch on the profile's view): the
    #     profile's resume, exactly as before. ``resume`` stays the identity the assessment is stored under.
    master_input = None
    if variant is None and profile is not None:
        from .assess_master import assess_input

        master_input = assess_input(
            home_root=home_root, target=target, profile=profile, title=job.title, posting_text=job.text, company=job.company,
            location=job.location, resolved=resolved,
        )

    # 5. Model target -> adapter (C1/C11), then the shared core (P1).
    model_target = request.model_target or _default_model_target(target)
    active = config if config is not None else load_config(home_root)
    # 0.1.10.7 E: every call of this binding is recorded (``call_metrics``), a timeout of the test seam included.
    meter = CallMeter(
        KIND_ASSESS, model_target.value, home_root, target, profile_id=resume.profile_id, job=job.job_identity
    )
    binding = meter.bind(_resolve_binding(active, model_target, home_root=home_root))
    try:
        attempt = assess_once(
            binding,
            AssessJob(title=job.title, company=job.company, location=job.location, posting_text=job.text),
            build_assess_context(
                resume_text=resume.text if master_input is None else master_input.resume_text,
                visa_sponsorship_required=preferences.visa_sponsorship_required,
                countries=tuple(preferences.countries),
                titles=tuple(preferences.titles),
                location=candidate_location,
                bank=job_bank,
                work_mode=candidate_work_mode,
            ),
            parse=_parse_body,
        )
    finally:
        binding.close()

    if not attempt.ok:
        if attempt.incomplete_posting:
            # uat-bug-046: nothing is stored; the job stays "not assessed".
            meter.unused(ERROR_POSTING_UNREADABLE)  # 0110-8-09: the call answered, its answer is not kept
            raise QuickAssessError(ERROR_POSTING_UNREADABLE, POSTING_INCOMPLETE_MESSAGE, reason=REASON_TOO_FEW_REQUIREMENTS)
        reason = attempt.not_assessed_reason
        if reason is NotAssessedReason.MODEL_OUTPUT_INVALID:
            detail = attempt.validation_error or "the model's answer did not match the assessment schema"
            raise QuickAssessError("model_output_invalid", f"the model's answer was invalid after one retry: {detail}")
        if reason is NotAssessedReason.MODEL_DENIED:
            raise QuickAssessError("model_denied", "the configured policy refused this model call")
        if binding.port.timed_out:
            raise QuickAssessError("assess_timeout", "the model call timed out; try again or pick a faster model target")
        raise QuickAssessError("model_unavailable", "the configured model is unavailable right now")

    body = attempt.parsed
    assert isinstance(body, AssessmentBody)
    if posting_requirements_unreadable(job.text, body):
        # Nothing is stored: the job stays "not assessed", never Matched.
        meter.unused(ERROR_POSTING_UNREADABLE)  # 0110-8-09
        raise QuickAssessError(ERROR_POSTING_UNREADABLE, POSTING_UNREADABLE_MESSAGE, reason=REASON_NO_REQUIREMENTS)
    from .proposal_execution import _usage_block

    usage = _usage_block([attempt.usage] if attempt.usage is not None else [], UsageBlock)
    producer = Producer(
        _PRODUCER_CALLABLE, _PRODUCER_VERSION, _PRODUCER_ACTOR, model_target, binding.port.name or model_target.value
    )
    assessed_at = _now()
    if trigger is None:
        trigger = TRIGGER_ASSESS if previous is None else TRIGGER_REASSESS
    history = _history_with(previous, VerdictHistoryEntry(at=assessed_at, verdict=body.verdict, trigger=trigger))
    response = AssessResponse(
        job=job,
        resume=resume,
        preferences=preferences,
        result=body,
        producer=producer,
        usage=usage,
        instructions_digest=INSTRUCTIONS_DIGEST,
        created_at=previous.created_at if previous is not None else assessed_at,
        stored_path=os.fspath(path),
        updated_at=assessed_at,
        history=history,
        posting_text=None if job.fetch_kind == "pasted" else job.text,
        origin=_origin_for(request, previous),
        # 0110-039: the basis, as a run seals it: what this verdict was made
        # with, so a later read can tell it from what the profile would be
        # assessed with now (``assessment_basis``).
        prompt_version=assess_prompt_version(candidate_work_mode),
        constraints_digest=constraints_digest(
            visa_sponsorship_required=preferences.visa_sponsorship_required,
            countries=tuple(preferences.countries),
            location=candidate_location,
            work_mode=candidate_work_mode,
        ),
        story_bank=None if bank.profile_id is None else StoryBankStamp(bank.profile_id, bank.digest, dict(bank.marks)),
        # 0.1.10.7 PL2: the rest of a run's seal (``assessment_basis``).
        profile_ref=None if profile is None else ProfileRef(profile.profile_id, profile.revision, profile.content_digest),
        posting_sha256=posting_digest,
        model=_model_id(getattr(binding.port, "resolved_model", None)),
        resume_basis=None if master_input is None else master_input.basis,
    )
    try:
        atomic_write(path, json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    except OSError as exc:
        # 0110-8-09: the model answered and the answer could not be written: a named failure, never a silent success.
        meter.unused(ERROR_NOT_STORED)
        raise QuickAssessError(ERROR_NOT_STORED, f"the assessment could not be stored ({type(exc).__name__}); nothing was saved") from exc
    if (bank.entries or bank.stories) and variant is None:
        # Which answers and stories this assessment cited ("Story bank <id>: ...").
        story_bank.record_reuse(
            home_root=home_root, target=target, entries=bank.entries, stories=bank.stories,
            evidence=[evidence for row in body.matrix for evidence in row.resume_evidence],
            posting={"job_identity": job.job_identity, "title": job.title, "company": job.company, "url": job.source_url},
        )
    return response


__all__ = [
    "EPHEMERAL_RESUME_KEY",
    "ERROR_NOT_STORED",
    "ERROR_POSTING_UNREADABLE",
    "POSTING_UNREADABLE_REASONS",
    "REASON_NO_REQUIREMENTS",
    "REASON_TOO_FEW_REQUIREMENTS",
    "TAILORED_VARIANT_DIR",
    "TRIGGER_ANSWER_PREFIX",
    "TRIGGER_ASSESS",
    "TRIGGER_REASSESS",
    "AssessVariant",
    "QuickAssessError",
    "candidate_location_and_work_mode",
    "find_quick_assessment_by_job_identity",
    "list_quick_assessments",
    "quick_assess_dir",
    "quick_assess_path",
    "read_quick_assessment",
    "read_tailored_variant",
    "resume_key",
    "run_quick_assessment",
    "tailored_variant_path",
]
