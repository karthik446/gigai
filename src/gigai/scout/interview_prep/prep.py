"""Build and load one posting's interview prep (S18 minimal first slice).

Public surface (the packet's owned function, callable by the CLI now and the
API/UI later):

- ``build_prep(*, home_root, target, posting_url, run_id=None, refresh=False,
  budget_usd=0.50, on_progress=None) -> InterviewPrep``
- ``load_prep(*, home_root, target, posting_url) -> InterviewPrep | None``

Idempotent per (posting, resume revision): a prep already stored for this
posting's ``normalized_url`` under the *same* resume ``content_sha256`` is
returned unchanged unless ``refresh=True``. Storage:
``interview_prep.storage`` (plain atomic-write JSON, coordinator decision --
see that module's docstring).

Privacy (packet requirement, restated here in code): the resume is sent
ONLY to the configured category-prediction model call
(``categories.predict_categories``) -- never to the company-research
web-search request (``company_research.research_company``, whose query is
built from company name + role title alone). Assert this at the call sites
below by construction: ``research_company`` has no resume parameter at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Callable

import httpx

from ...config import load_config
from ...workpad import resolve_workpad
from ..find_jobs.contracts import FindJobsConfig, normalize_url
from . import company_research, prep_notes, role_research
from .categories import CategoryPredictionError, predict_categories
from .posting import PostingUnavailableError, resolve_posting
from .resume import ResumeUnavailableError, current_resume
from .storage import atomic_write, prep_path
from .types import InterviewPrep

_DEFAULT_BUDGET_USD = 0.50


class InterviewPrepError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_prep(*, home_root: Path, target: Path, posting_url: str) -> InterviewPrep | None:
    """Return the stored prep for this posting, or ``None`` if none exists."""

    normalized = normalize_url(posting_url)
    path = prep_path(home_root, target, normalized)
    if not path.is_file():
        return None
    return InterviewPrep.from_json(json.loads(path.read_text(encoding="utf-8")))


def build_prep(
    *,
    home_root: Path,
    target: Path,
    posting_url: str,
    run_id: str | None = None,
    refresh: bool = False,
    budget_usd: float = _DEFAULT_BUDGET_USD,
    gig_id: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> InterviewPrep:
    """Build (or return the idempotent cached) interview prep for one posting.

    Raises :class:`InterviewPrepError` when the posting or resume cannot be
    resolved. Never raises for a missing OpenAI key or a web-search provider
    error -- those degrade the company-research section only (``skipped``),
    per the packet's partial-prep contract; the rest of the prep still
    builds. Category prediction failures (no configured model target,
    ambiguous target, model denied) DO raise -- "no silent provider
    fallback" applies to the model call, not the web-search call.
    """

    def _progress(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)

    normalized = normalize_url(posting_url)
    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=gig_id, allow_semantic_state=True)

    try:
        resume_identity, resume_bytes = current_resume(home_root=home_root, requested_target=target, gig_id=gig_id)
    except ResumeUnavailableError as exc:
        raise InterviewPrepError(exc.code, str(exc)) from exc

    if not refresh:
        cached = load_prep(home_root=home_root, target=target, posting_url=posting_url)
        if cached is not None and cached.resume is not None and cached.resume.content_sha256 == resume_identity.content_sha256:
            return cached

    _progress({"stage": "resolve_posting"})
    try:
        info = resolve_posting(resolved=resolved, normalized_url=normalized, run_id=run_id)
    except PostingUnavailableError as exc:
        raise InterviewPrepError(exc.code, str(exc)) from exc

    posting = info.posting
    resume_text = resume_bytes.decode("utf-8", errors="replace")

    _progress({"stage": "company_research"})
    with httpx.Client(timeout=30.0) as verify_client:
        company = company_research.research_company(
            company=posting.company, title=posting.title, budget_usd=budget_usd,
            verify_client=verify_client, on_progress=_progress, home_root=home_root,
        )

    _progress({"stage": "role_research"})
    role = role_research.research_role(posting.text or "")

    _progress({"stage": "question_categories"})
    config = load_config(home_root)
    model_target = _find_jobs_model_target(target)
    try:
        categories, resolved_target_name = predict_categories(
            config=config, model_target=model_target,
            title=posting.title, company=posting.company,
            posting_text=posting.text or "", resume_text=resume_text,
            company_claims=tuple(claim.claim for claim in company.claims),
            home_root=home_root,
        )
    except CategoryPredictionError as exc:
        raise InterviewPrepError(exc.code, str(exc)) from exc

    notes = prep_notes.build_prep_notes(info.matrix)

    now = _now()
    created_at = now
    existing = load_prep(home_root=home_root, target=target, posting_url=posting_url)
    if existing is not None:
        created_at = existing.created_at

    prep = InterviewPrep(
        posting_id=normalized,
        company=posting.company,
        title=posting.title,
        resume=resume_identity,
        company_research=company,
        role_research=role,
        question_categories=categories,
        prep_notes=notes,
        model_target=resolved_target_name,
        cost_usd=company.cost_usd,
        created_at=created_at,
        refreshed_at=now,
    )
    _save(home_root, target, prep)
    _progress({"stage": "prep_done", "cost_usd": prep.cost_usd})
    return prep


def _save(home_root: Path, target: Path, prep: InterviewPrep) -> None:
    path = prep_path(home_root, target, prep.posting_id)
    atomic_write(path, json.dumps(prep.to_json(), indent=2, sort_keys=True).encode("utf-8"))


def _find_jobs_model_target(target: Path) -> str:
    """Read ``find-jobs.json``'s ``default_model_target`` (the run's configured target)."""

    path = target / "find-jobs.json"
    if not path.is_file():
        raise InterviewPrepError("find_jobs_config_missing", "no find-jobs.json is set for this project; run `gigai scout run` first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    config = FindJobsConfig.from_json(payload)
    return config.default_model_target.value


__all__ = ["InterviewPrepError", "build_prep", "load_prep"]
