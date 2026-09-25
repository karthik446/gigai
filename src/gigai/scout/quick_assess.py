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
``AssessResponse`` JSON: it never contains resume text or the full job text.

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
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
    AssessmentBody,
    AssessRequest,
    AssessResponse,
    ResolvedJob,
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


# --- the assessment ----------------------------------------------------------------


def _parse_body(raw: Mapping[str, object]) -> AssessmentBody:
    from . import proposals

    proposals.validate_assessment_bounds(raw)
    return AssessmentBody.from_json(dict(raw))


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
) -> AssessResponse:
    """Assess ``request.job`` against ``request.resume`` and store the answer.

    Raises ``QuickAssessError`` with one of: ``job_input_invalid``,
    ``resume_input_invalid``, ``invalid_value`` (bad URL), ``job_text_unavailable``,
    ``job_fetch_failed``, ``profile_not_found``, ``profile_unavailable``,
    ``resume_unavailable``, ``resume_digest_mismatch``, ``model_target_unavailable``,
    ``model_unavailable``, ``model_denied``, ``assess_timeout``, ``model_output_invalid``,
    ``target_unavailable``.
    """

    home_root = Path(home_root)
    target = Path(target)

    # 1. Job text (public data; network only for a URL).
    try:
        with job_fetch_client() as client:
            job = resolve_job(request.job, client=client)
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
    from .proposal_execution import _usage_block

    usage = _usage_block([attempt.usage] if attempt.usage is not None else [], UsageBlock)
    producer = Producer(
        _PRODUCER_CALLABLE, _PRODUCER_VERSION, _PRODUCER_ACTOR, model_target, binding.port.name or model_target.value
    )
    assessed_at = _now()
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
    )
    atomic_write(path, json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    return response


__all__ = [
    "EPHEMERAL_RESUME_KEY",
    "QuickAssessError",
    "find_quick_assessment_by_job_identity",
    "list_quick_assessments",
    "quick_assess_dir",
    "quick_assess_path",
    "resume_key",
    "run_quick_assessment",
]
