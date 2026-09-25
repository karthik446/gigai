"""P6: ``POST /api/runs/{run_id}/rank`` -- Jev pre-rank for one run's postings.

Resolves the run's own sealed ``outputs/acquire.json`` rows (the same file
``carried_forward_assessments`` in ``server.py`` reads) against the given or
selected profile's resume, calls ``jev_rank.rank_postings`` (cache-first,
cost-capped), and returns the scores -- it never rewrites the sealed acquire
output. ``runs.py``'s ``/results`` attaches ``rank_scores`` the same way, by
calling this module's ``compute_rank_scores`` again: a cache hit costs
nothing, so the two routes never disagree and a rerun after ``/rank`` is
free (LIVE ACCEPTANCE's own cache-hit proof).

No key, no rows, or any Jev failure -> ``RankResponse(scores=(), ...)``, the
same fail-open contract ``market_acquisition._rank_candidates`` uses for the
run's own selection ordering (P6's behavior rule: no key -> today's
ordering, never a failed run).
"""

from __future__ import annotations

import json
from http import HTTPStatus

import httpx

from ....journal import JournalArtifactMissingError, read_committed_artifact
from ..contracts import AcquireOutput, FindJobsContractError
from ..jev_contracts import RankRequest, RankResponse
from ..jev_rank import DEFAULT_COST_CAP_USD, RankPreferences, rank_postings


def compute_rank_scores(
    *, home_root, target, run_id: str, profile_id: str | None, cost_cap_usd: float | None = None
) -> RankResponse:
    """Score ``run_id``'s acquire rows against ``profile_id`` (or the gig's
    selected profile), cache-first. Never raises -- every failure mode
    (run/profile/resume unavailable, no key, Jev error) returns an empty,
    uncapped ``RankResponse`` rather than propagating, so a display-only
    caller (``/results``) is never broken by a ranking failure.
    """

    from ....workpad import resolve_workpad
    from ...profile_records import list_profiles, selected_profile
    from .. import jev_client
    from ..jev_client import JevClient

    empty = RankResponse(run_id, (), "0", False, 0)
    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception:
        return empty
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=f"runs/{run_id}/outputs/acquire.json",
        )
        acquire = AcquireOutput.from_json(json.loads(raw))
    except (JournalArtifactMissingError, ValueError, FindJobsContractError, OSError):
        return empty
    rows = tuple(item.posting for item in acquire.rows)
    if not rows:
        return empty

    profile = None
    try:
        if profile_id is not None:
            profile = next((item for item in list_profiles(resolved) if item.profile_id == profile_id), None)
        else:
            profile = selected_profile(resolved, home_root=home_root, target=target)
    except Exception:
        profile = None
    if profile is None:
        return empty

    if not jev_client.has_api_key(home_root=home_root):
        return empty

    try:
        from .... import private_records

        record = private_records.read_record(
            home_root=home_root,
            requested_target=target,
            record_id=profile.resume_ref.record_id,
            revision_id=profile.resume_ref.revision_id,
            content=True,
            gig_id=resolved.gig_id,
        )
        content = record.get("content")
        resume_text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else None
    except Exception:
        resume_text = None
    if not resume_text:
        return empty

    cap = cost_cap_usd if cost_cap_usd is not None else DEFAULT_COST_CAP_USD
    try:
        api_key = jev_client.require_api_key(home_root=home_root)
        client = JevClient(api_key, _jev_http_client())
        prefs = RankPreferences(target_titles=tuple(profile.titles), countries=(), visa_sponsorship_required=False)
        scores, total_cost, capped = rank_postings(
            rows,
            client=client,
            resume_text=resume_text,
            prefs=prefs,
            profile_id=profile.profile_id,
            resume_revision_id=profile.resume_ref.revision_id,
            home_root=home_root,
            target=target,
            cost_cap_usd=cap,
        )
    except Exception:
        return empty
    unscored = sum(1 for item in scores if item.score is None)
    return RankResponse(run_id, scores, f"{total_cost:.6f}", capped, unscored)


def _jev_http_client() -> httpx.Client:
    """The transport a ``/rank`` call uses -- its own module-level function
    so ``bindings.py``'s test seam (``GIGAI_SCOUT_FIND_JOBS_TEST_JEV``) can
    patch it exactly like ``market_acquisition._jev_http_client``."""

    return httpx.Client(timeout=30.0)


class RankRoutesMixin:
    """``Handler`` mixin: ``POST /api/runs/{run_id}/rank``."""

    def _handle_post_rank(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = dict(body) if isinstance(body, dict) else {}
            payload.setdefault("run_id", run_id)
            request = RankRequest.from_json(payload)
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        if request.run_id != run_id:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "run_id in the body must match the URL")
            return
        cost_cap = None
        if request.cost_cap_usd is not None:
            try:
                cost_cap = float(request.cost_cap_usd)
            except ValueError:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "cost_cap_usd must be a decimal string")
                return
        response = compute_rank_scores(
            home_root=self._backend.home_root,
            target=self._backend.target,
            run_id=run_id,
            profile_id=request.profile_id,
            cost_cap_usd=cost_cap,
        )
        self._write_json(HTTPStatus.OK, response.to_json())


__all__ = ["RankRoutesMixin", "compute_rank_scores"]
