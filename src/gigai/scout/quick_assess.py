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

Jev (uat-bug-015): when a Jev key exists the posting also gets ONE Jev score
through ``jev_rank.rank_postings`` -- the same cache, cost cap and fail-open
contract find-jobs uses -- stored as ``rank_score``; when there is none,
``rank_skip_reason`` says why.  See ``_jev_rank``.

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
from ..canonical import digest_imported_bytes, parse_json_bytes
from ..config import GigAIConfig, load_config
from ..model_targets import ModelTargetResolutionError
from .assessment_core import INSTRUCTIONS_DIGEST, AssessContext, AssessJob
from .assessment_core import PriorAnswer as CorePriorAnswer
from .assessment_core import assess_once
from .experience_answers import read_answers
from .find_jobs.assess_contracts import (
    ORIGIN_QUICK_ASSESS,
    AssessmentBody,
    AssessPreferences,
    AssessRequest,
    AssessResponse,
    ResolvedJob,
    ResolvedResume,
    VerdictHistoryEntry,
)
from .find_jobs.contracts import (
    FindJobsConfig,
    FindJobsContractError,
    ModelTarget,
    NotAssessedReason,
    Producer,
    UsageBlock,
)
from .find_jobs.discovery.storage import atomic_write, project_id
from .find_jobs.jev_contracts import RankScore
from .find_jobs.job_input import job_fetch_client, resolve_job
from .find_jobs.resume_input import resolve_preferences, resolve_profile, resolve_resume, resume_for_profile

#: Directory segment for assessments against pasted (ephemeral) resume text.
EPHEMERAL_RESUME_KEY = "ephemeral"

#: A list filter names ONE directory segment under ``quick_assess/``: a
#: profile id (``profile_<uuid>``) or ``"ephemeral"`` -- never a path.
_SAFE_RESUME_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_PRODUCER_CALLABLE = "scout.assess"
_PRODUCER_VERSION = "1"
_PRODUCER_ACTOR = "scout-assess"

_JEV_COST_CAP_ENV = "GIGAI_JEV_COST_CAP_USD"

_logger = logging.getLogger("gigai.scout.server")

#: Exception types that mean "the model call ran out of time" when found
#: anywhere in a raised adapter error's cause chain.
_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException)


class QuickAssessError(ValueError):
    """A quick assessment could not run; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "")

    def invoke(self, request):
        try:
            if self.deadline_seconds is None:
                return self.inner.invoke(request)
            return self._invoke_with_deadline(request)
        except BaseException as exc:
            if _is_timeout(exc):
                self.timed_out = True
            raise

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


# --- Jev score (uat-bug-015) ----------------------------------------------------------


@dataclass(frozen=True)
class _JevPosting:
    """The slice of a posting row ``jev_rank.rank_postings`` reads.

    A quick-assessed job is not an acquired ``PostingRow`` (a pasted one has
    no URL or provider), so it is handed to ``rank_postings`` as this
    instead; ``normalized_url`` is the ``job_identity``.
    """

    normalized_url: str
    content_sha256: str
    company: str
    title: str
    location: str
    text: str | None = None


def _jev_cost_cap_usd() -> float:
    """``GIGAI_JEV_COST_CAP_USD`` when set, else the default (the acquire node's own rule)."""

    from .find_jobs.jev_rank import DEFAULT_COST_CAP_USD

    raw = os.environ.get(_JEV_COST_CAP_ENV)
    try:
        return float(raw) if raw else DEFAULT_COST_CAP_USD
    except ValueError:
        return DEFAULT_COST_CAP_USD


def _jev_rank(
    job: ResolvedJob,
    resume: ResolvedResume,
    preferences: AssessPreferences,
    *,
    home_root: Path,
    target: Path,
) -> tuple[RankScore | None, str | None]:
    """``(rank_score, rank_skip_reason)`` for one quick-assessed job; exactly
    one is set, except with "Rank with Jev" off (``jev_budget.rank_enabled``):
    then neither, and Jev is not asked. ``RANK_SKIP_REASONS`` is a stored
    contract's enum, so "off" is not written into the assessment.

    One ``jev_rank.rank_postings`` call for one row: cache-first, under the
    cost cap, and fail open -- this never raises, so a Jev problem can never
    fail the assessment. The cache key is find-jobs' own (``profile_id`` +
    the pinned resume's ``revision_id``).

    Operator decision (2026-09-27): only PROFILE resumes -- the ones
    find-jobs already sends to Jev -- are scored. A pasted (ephemeral)
    resume goes to the chosen assessment model and nowhere else, so it is
    skipped here with ``"ephemeral_resume"``.
    """

    try:
        from .find_jobs import bindings, jev_budget, jev_client, market_acquisition
        from .find_jobs.jev_rank import RankPreferences, rank_postings

        if not jev_budget.rank_enabled(home_root):  # ui-pass: "Rank with Jev" is off: no call, no reason stored
            return None, None
        if not jev_client.has_api_key(home_root=home_root):
            return None, "no_key"
        if resume.pinned is None:
            return None, "ephemeral_resume"
        if not job.title.strip() and not job.company.strip():
            return None, "no_title_or_company"
        # Inert unless GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1 (same reason as
        # ``_resolve_binding``'s model seam: a server that never started a
        # run has not installed it yet).
        bindings._patch_test_jev_transport()
        http_client = market_acquisition._jev_http_client()
        try:
            scores, _total_cost, capped = rank_postings(
                (_JevPosting(job.job_identity, job.text_sha256, job.company, job.title, job.location, job.text),),  # type: ignore[arg-type]
                client=jev_client.JevClient(jev_client.require_api_key(home_root=home_root), http_client),
                resume_text=resume.text,
                prefs=RankPreferences(
                    target_titles=tuple(preferences.titles or ()),
                    countries=tuple(preferences.countries or ()),
                    visa_sponsorship_required=bool(preferences.visa_sponsorship_required),
                ),
                profile_id=resume.profile_id,
                resume_revision_id=resume.pinned.revision_id,
                home_root=home_root,
                target=target,
                cost_cap_usd=_jev_cost_cap_usd(),
            )
        finally:
            http_client.close()
        score = scores[0]
        if score.score is None:
            return None, "cost_cap" if capped else "error"
        # A cache hit is keyed by content, not URL: the same posting text
        # assessed under another URL must still carry THIS job's identity.
        if score.normalized_url != job.job_identity:
            score = replace(score, normalized_url=job.job_identity)
        return score, None
    except Exception as exc:  # noqa: BLE001 - fail open: Jev never fails an assessment
        _logger.warning("jev quick-assess score skipped: %s", type(exc).__name__)
        return None, "error"


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
    requirements" stays the legitimate requirement-free path.  Residual
    false positive: a genuinely requirement-free posting that also uses none
    of the cue words (a one-line "we are hiring" blurb) is refused.
    """

    return not _has_requirement_cue(text) and not _has_real_requirement(body)


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
) -> AssessResponse:
    """Assess ``request.job`` against ``request.resume`` and store the answer.

    ``trigger`` names what caused this assessment in the stored verdict
    history (Q4a): ``None`` (the default) records ``"assess"`` for a job
    never assessed before and ``"reassess"`` otherwise; ``POST /api/answers``
    passes ``"answer:<question_id>"``.

    Raises ``QuickAssessError`` with one of: ``job_input_invalid``,
    ``resume_input_invalid``, ``invalid_value`` (bad URL), ``job_text_unavailable``,
    ``job_fetch_failed``, ``profile_not_found``, ``profile_unavailable``,
    ``resume_unavailable``, ``resume_digest_mismatch``, ``model_target_unavailable``,
    ``model_unavailable``, ``model_denied``, ``assess_timeout``, ``model_output_invalid``,
    ``posting_requirements_unreadable``, ``target_unavailable``.
    """

    home_root = Path(home_root)
    target = Path(target)

    # 1. Job text (public data; network only for a URL).
    try:
        with job_fetch_client() as client:
            job = resolve_job(request.job, client=client, home_root=home_root)
    except FindJobsContractError as exc:
        raise QuickAssessError(exc.code, str(exc)) from exc
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
    candidate_location = preferences.location if preferences.location is not None else _config_location(target)

    # 4. Storage path first, so the response can name it and a prior
    #    ``created_at`` survives a re-assessment.
    try:
        path = quick_assess_path(home_root, target, resume.profile_id, job.job_identity)
    except Exception as exc:
        raise QuickAssessError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    previous = _read_stored(path)

    # 4b. Prior answers (P3's Q&A loop): every answered ``experience_qa``
    #     question in this gig, rendered into the prompt so the model never
    #     re-asks something the operator already answered (assess.md rule
    #     6). A pasted-text assess with no bound gig (``_resolve_workpad``
    #     never ran) has none to offer -- that is fine, not fatal: prior
    #     answers are cross-posting convenience, not a requirement.
    prior_answers: tuple[CorePriorAnswer, ...] = ()
    try:
        gig_resolved = resolved if resolved is not None else _resolve_workpad(home_root, target)
        stored_answers = read_answers(home_root=home_root, requested_target=target, gig_id=gig_resolved.gig_id)
        prior_answers = tuple(
            CorePriorAnswer(question_id=item.question_id, prompt=item.prompt, answer=item.answer)
            for item in stored_answers.values()
        )
    except QuickAssessError:
        pass

    # 5. Model target -> adapter (C1/C11), then the shared core (P1).
    model_target = request.model_target or _default_model_target(target)
    active = config if config is not None else load_config(home_root)
    binding = _resolve_binding(active, model_target, home_root=home_root)
    try:
        attempt = assess_once(
            binding,
            AssessJob(title=job.title, company=job.company, location=job.location, posting_text=job.text),
            AssessContext(
                resume_text=resume.text,
                visa_sponsorship_required=preferences.visa_sponsorship_required,
                countries=tuple(preferences.countries),
                titles=tuple(preferences.titles),
                prior_answers=prior_answers,
                location=candidate_location,
            ),
            parse=_parse_body,
        )
    finally:
        binding.close()

    if not attempt.ok:
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
        raise QuickAssessError("posting_requirements_unreadable", POSTING_UNREADABLE_MESSAGE)
    from .proposal_execution import _usage_block

    usage = _usage_block([attempt.usage] if attempt.usage is not None else [], UsageBlock)
    producer = Producer(
        _PRODUCER_CALLABLE, _PRODUCER_VERSION, _PRODUCER_ACTOR, model_target, binding.port.name or model_target.value
    )
    assessed_at = _now()
    if trigger is None:
        trigger = TRIGGER_ASSESS if previous is None else TRIGGER_REASSESS
    history = _history_with(previous, VerdictHistoryEntry(at=assessed_at, verdict=body.verdict, trigger=trigger))
    rank_score, rank_skip_reason = _jev_rank(job, resume, preferences, home_root=home_root, target=target)
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
        rank_score=rank_score,
        rank_skip_reason=rank_skip_reason,
        origin=_origin_for(request, previous),
    )
    atomic_write(path, json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    return response


__all__ = [
    "EPHEMERAL_RESUME_KEY",
    "TRIGGER_ANSWER_PREFIX",
    "TRIGGER_ASSESS",
    "TRIGGER_REASSESS",
    "QuickAssessError",
    "find_quick_assessment_by_job_identity",
    "list_quick_assessments",
    "quick_assess_dir",
    "quick_assess_path",
    "resume_key",
    "run_quick_assessment",
]
