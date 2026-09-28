"""P6: ``POST /api/runs/{run_id}/rank`` -- Jev pre-rank for one run's postings.

Resolves the run's own sealed ``outputs/acquire.json`` rows (the same file
``carried_forward_assessments`` in ``server.py`` reads) against the given or
selected profile's resume and scores them with ``jev_rank`` (cache-first,
cost-capped, under the day's budget). It never rewrites the sealed acquire
output.

Who may spend (operator decision 2026-09-28: Jev had cost $1.00): a
find-jobs run, and ``POST /rank``, which is an explicit "Score with Jev".
A page that READS a run never does:

* ``compute_rank_scores`` -- what ``GET /results`` attaches as
  ``rank_scores`` -- reads the score cache and asks Jev nothing;
* ``POST /rank`` with ``"start": true`` in its body is the click. It does
  not wait for Jev: it starts one ranking pass in the background, or joins
  the one already running for the same ``(run, profile, resume revision)``
  (single flight: a second click, a second tab never start a second pass
  or spend twice), and answers at once with the scores cached so far and a
  ``rank_status`` whose ``status`` is ``running``.
* ``POST /rank`` WITHOUT ``start`` (``{}``, what a page sends when it
  opens a run, and what a caller repeats while a pass runs) starts
  nothing: the scores cached so far, and ``running`` while a pass runs,
  then that pass's own result (what it cost, why it stopped) until the
  next click, else ``not_requested`` when postings are still unscored.

uat-bug-021: failing open is never silent. Every answer carries a
``jev_rank.RankStatus`` (``scored N of M`` / ``skipped: <reason>``) and a
pass logs it once. Reasons here: ``error:<ExceptionType>`` (the project
could not be resolved, or anything unexpected), ``no_run_output`` (the
run's sealed postings could not be read), ``no_candidates`` (it has no
postings), ``no_profile``, ``no_key``, ``no_resume``, ``not_requested``
(unscored postings and no pass was asked for), ``disabled`` ("Rank with
Jev" is off, ``jev_budget.rank_enabled``: a click starts no pass and
nothing asks Jev), and the pass's own
``cost_cap_reached`` / ``daily_budget_reached`` / ``jev_error:<code>``.

``GET /api/jev/usage`` (``JevUsageRoutesMixin``): today's Jev spend and the
daily budget, ``jev_budget.usage``. ``POST /rank`` answers the same block
as ``usage``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from http import HTTPStatus
from pathlib import Path
import threading

import httpx

from ....journal import JournalArtifactMissingError, read_committed_artifact
from .. import jev_budget
from ..contracts import AcquireOutput, FindJobsContractError
from ..jev_contracts import RankRequest, RankResponse
from ..jev_rank import (
    DEFAULT_COST_CAP_USD,
    RUN_CONCURRENCY,
    RUN_RETRIES,
    RankPreferences,
    RankStatus,
    log_rank_status,
    rank_postings_report,
    read_cached_scores,
)

_logger = logging.getLogger("gigai.scout.server")


@dataclass(frozen=True)
class _Resolved:
    """What a ranking of one run needs: its postings, the profile, the resume.

    ``prefs`` is the exact ``RankPreferences`` this route sends Jev (built
    once, here, so the read path -- ``_cached``/``read_cached_scores`` --
    and the write path -- ``rank_postings_report`` in ``_rank_run`` -- key
    the cache identically; uat-bug-021 decision d, the cache key includes a
    digest of these preferences).
    """

    project_id: str
    rows: tuple
    profile_id: str
    resume_revision_id: str
    titles: tuple[str, ...]
    resume_text: str
    prefs: RankPreferences


def _skip(
    run_id: str, reason: str, *, home_root, total: int = 0, exc: BaseException | None = None
) -> tuple[RankResponse, RankStatus]:
    if exc is not None:
        # The type only: a message may name a path or quote a record.
        _logger.warning("jev rank (%s): %s: %s", run_id, reason, type(exc).__name__)
    return RankResponse(run_id, (), "0", False, 0), RankStatus.skipped(reason, total=total, home_root=home_root)


def _resolve(
    *, home_root, target, run_id: str, profile_id: str | None
) -> _Resolved | tuple[RankResponse, RankStatus]:
    """The run's postings and the profile's resume, or why there are none."""

    from ....workpad import resolve_workpad
    from ...profile_records import list_profiles, selected_profile
    from .. import jev_client

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        return _skip(run_id, f"error:{type(exc).__name__}", home_root=home_root, exc=exc)
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=f"runs/{run_id}/outputs/acquire.json",
        )
        acquire = AcquireOutput.from_json(json.loads(raw))
    except (JournalArtifactMissingError, ValueError, FindJobsContractError, OSError) as exc:
        return _skip(run_id, "no_run_output", home_root=home_root, exc=exc)
    rows = tuple(item.posting for item in acquire.rows)
    if not rows:
        return _skip(run_id, "no_candidates", home_root=home_root)
    total = len(rows)

    profile = None
    try:
        if profile_id is not None:
            profile = next((item for item in list_profiles(resolved) if item.profile_id == profile_id), None)
        else:
            profile = selected_profile(resolved, home_root=home_root, target=target)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged
        _logger.warning("jev rank (%s): the profile could not be read: %s", run_id, type(exc).__name__)
        profile = None
    if profile is None:
        return _skip(run_id, "no_profile", home_root=home_root, total=total)

    try:
        has_key = jev_client.has_api_key(home_root=home_root)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        return _skip(run_id, f"error:{type(exc).__name__}", home_root=home_root, total=total, exc=exc)
    if not has_key:
        return _skip(run_id, "no_key", home_root=home_root, total=total)

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
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged
        _logger.warning("jev rank (%s): the profile's resume could not be read: %s", run_id, type(exc).__name__)
        resume_text = None
    if not resume_text:
        return _skip(run_id, "no_resume", home_root=home_root, total=total)
    titles = tuple(profile.titles)
    return _Resolved(
        project_id=resolved.project_id,
        rows=rows,
        profile_id=profile.profile_id,
        resume_revision_id=profile.resume_ref.revision_id,
        titles=titles,
        resume_text=resume_text,
        # Matches the RankPreferences built for rank_postings_report in
        # _rank_run below exactly: this route does not yet read
        # find-jobs.json's countries/visa flag (a pre-existing gap, not
        # this packet's target -- only the cache key changed here).
        prefs=RankPreferences(target_titles=titles, countries=(), visa_sponsorship_required=False),
    )


def _cached(resolved: _Resolved, *, home_root, target, run_id: str) -> RankResponse:
    scores = read_cached_scores(
        resolved.rows,
        resume_text=resolved.resume_text,
        prefs=resolved.prefs,
        profile_id=resolved.profile_id,
        resume_revision_id=resolved.resume_revision_id,
        home_root=home_root,
        target=target,
    )
    return RankResponse(run_id, scores, "0.000000", False, sum(1 for item in scores if item.score is None))


def compute_rank_scores(
    *, home_root, target, run_id: str, profile_id: str | None, cost_cap_usd: float | None = None
) -> RankResponse:
    """The scores ALREADY paid for, for ``run_id``'s postings against
    ``profile_id`` (or the gig's selected profile). Jev is never asked: a
    page that reads a run does not spend (``cost_cap_usd`` is accepted for
    the callers that pass it and has nothing to bound). One entry per
    posting, unscored where no score is cached. Never raises -- run,
    profile or resume unavailable, or no key, is an empty ``RankResponse``,
    so a display-only caller (``/results``) is never broken by it.
    """

    try:
        resolved = _resolve(home_root=home_root, target=target, run_id=run_id, profile_id=profile_id)
        if not isinstance(resolved, _Resolved):
            return resolved[0]
        return _cached(resolved, home_root=home_root, target=target, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged
        _logger.warning("jev rank (%s): cached scores could not be read: %s", run_id, type(exc).__name__)
        return RankResponse(run_id, (), "0", False, 0)


def rank_run(
    *,
    home_root,
    target,
    run_id: str,
    profile_id: str | None,
    cost_cap_usd: float | None = None,
    where: str = "rank",
) -> tuple[RankResponse, RankStatus]:
    """One whole ranking pass, waited for: the scores and what happened, logged once.

    What ``POST /rank`` runs in the background. INFO when every posting was
    scored, WARNING for a skip or a pass cut short. Never raises.
    """

    response, status = _rank_run(
        home_root=home_root, target=target, run_id=run_id, profile_id=profile_id, cost_cap_usd=cost_cap_usd, where=where
    )
    log_rank_status(status, run_id=run_id, where=where)
    return response, status


def _rank_run(
    *,
    home_root,
    target,
    run_id: str,
    profile_id: str | None,
    cost_cap_usd: float | None,
    where: str,
    resolved: _Resolved | None = None,
) -> tuple[RankResponse, RankStatus]:
    from .. import jev_client
    from ..jev_client import JevClient

    total = 0
    try:
        found = resolved or _resolve(home_root=home_root, target=target, run_id=run_id, profile_id=profile_id)
        if not isinstance(found, _Resolved):
            return found
        total = len(found.rows)
        if not jev_budget.rank_enabled(home_root):  # ui-pass: "Rank with Jev" is off
            return _skip(run_id, "disabled", home_root=home_root, total=total)
        cap = cost_cap_usd if cost_cap_usd is not None else DEFAULT_COST_CAP_USD
        api_key = jev_client.require_api_key(home_root=home_root)
        http_client = _jev_http_client()
        try:
            report = rank_postings_report(
                found.rows,
                client=JevClient(api_key, http_client),
                resume_text=found.resume_text,
                prefs=found.prefs,
                profile_id=found.profile_id,
                resume_revision_id=found.resume_revision_id,
                home_root=home_root,
                target=target,
                cost_cap_usd=cap,
                concurrency=RUN_CONCURRENCY,
                retries=RUN_RETRIES,
                where=where,
                run_id=run_id,
            )
        finally:
            http_client.close()
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        return _skip(run_id, f"error:{type(exc).__name__}", home_root=home_root, total=total, exc=exc)
    unscored = sum(1 for item in report.scores if item.score is None)
    response = RankResponse(run_id, report.scores, f"{report.total_cost_usd:.6f}", report.capped, unscored)
    return response, RankStatus.from_report(report, cost_cap_usd=cap)


def _jev_http_client() -> httpx.Client:
    """The transport a ``/rank`` call uses -- its own module-level function
    so ``bindings.py``'s test seam (``GIGAI_SCOUT_FIND_JOBS_TEST_JEV``) can
    patch it exactly like ``market_acquisition._jev_http_client``."""

    return httpx.Client(timeout=30.0)


# --- single flight -----------------------------------------------------------------------


@dataclass
class _Pass:
    """One ranking pass in this server process."""

    thread: threading.Thread | None = None
    result: tuple[RankResponse, RankStatus] | None = None
    done: threading.Event = field(default_factory=threading.Event)


_PASSES: dict[tuple[str, str, str, str, str], _Pass] = {}
_PASSES_LOCK = threading.Lock()


def start_or_join_rank(
    *,
    home_root,
    target,
    run_id: str,
    profile_id: str | None,
    cost_cap_usd: float | None = None,
    start: bool = False,
) -> tuple[RankResponse, RankStatus]:
    """What ``POST /rank`` answers, at once. Only ``start=True`` can spend.

    * a pass is running for this ``(run, profile, resume revision)``: the
      scores cached so far, ``status: running``. Nothing is started.
    * ``start`` and none is running: every posting is already scored ->
      ``scored N of N``, no pass; else a pass starts in the background ->
      ``status: running``.
    * no ``start`` and none is running: the result of the pass that ended
      last, if one did; else the scores cached so far (``not_requested``
      when some posting has none).

    The passes are this process's (the Scout server is one process); a
    run's own pass in its child process ends before the run's output that
    this reads exists.
    """

    try:
        resolved = _resolve(home_root=home_root, target=target, run_id=run_id, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        return _skip(run_id, f"error:{type(exc).__name__}", home_root=home_root, exc=exc)
    if not isinstance(resolved, _Resolved):
        if start:
            log_rank_status(resolved[1], run_id=run_id, where="rank")
        return resolved
    key = (str(Path(home_root)), resolved.project_id, run_id, resolved.profile_id, resolved.resume_revision_id)
    cap = cost_cap_usd if cost_cap_usd is not None else DEFAULT_COST_CAP_USD

    with _PASSES_LOCK:
        current = _PASSES.get(key)
        running = current is not None and not current.done.is_set()
        if not running:
            if not start and current is not None and current.result is not None:
                return current.result
            cached = _cached(resolved, home_root=home_root, target=target, run_id=run_id)
            scored = len(cached.scores) - cached.unscored
            spent, budget = jev_budget.spent_today_usd(home_root), jev_budget.daily_budget_usd(home_root)
            if cached.unscored == 0:
                return cached, RankStatus("scored", scored, scored, None, cap, 0.0, None, spent, budget)
            enabled = jev_budget.rank_enabled(home_root)
            if not start or not enabled:
                # ui-pass: with "Rank with Jev" off a click starts no pass
                # either; the unscored postings say why.
                status = "scored" if scored else "skipped"
                reason = "not_requested" if enabled else "disabled"
                if start:
                    log_rank_status(
                        RankStatus(status, scored, len(cached.scores), reason, cap, 0.0, None, spent, budget),
                        run_id=run_id, where="rank",
                    )
                return cached, RankStatus(status, scored, len(cached.scores), reason, cap, 0.0, None, spent, budget)
            current = _Pass()
            _PASSES[key] = current

            def work(this: _Pass = current) -> None:
                try:
                    response, status = _rank_run(
                        home_root=home_root, target=target, run_id=run_id, profile_id=profile_id,
                        cost_cap_usd=cost_cap_usd, where="rank", resolved=resolved,
                    )
                    log_rank_status(status, run_id=run_id, where="rank")
                    this.result = (response, status)
                finally:
                    this.done.set()

            current.thread = threading.Thread(target=work, name=f"jev-rank-{run_id}", daemon=True)
            current.thread.start()

    cached = _cached(resolved, home_root=home_root, target=target, run_id=run_id)
    scored = len(cached.scores) - cached.unscored
    return cached, RankStatus.running(scored=scored, total=len(cached.scores), cost_cap_usd=cap, home_root=home_root)


def wait_for_rank(*, timeout: float | None = None) -> bool:
    """Wait until no pass is running in this process; false when ``timeout`` ran out first."""

    with _PASSES_LOCK:
        running = [item for item in _PASSES.values() if not item.done.is_set()]
    return all(item.done.wait(timeout) for item in running)


class RankRoutesMixin:
    """``Handler`` mixin: ``POST /api/runs/{run_id}/rank``."""

    def _handle_post_rank(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = dict(body) if isinstance(body, dict) else {}
            # Not a RankRequest field: the one thing that tells a click
            # ("Score with Jev") from a page reading the run.
            start = payload.pop("start", False)
            if type(start) is not bool:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "start must be true or false")
                return
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
        response, status = start_or_join_rank(
            home_root=self._backend.home_root,
            target=self._backend.target,
            run_id=run_id,
            profile_id=request.profile_id,
            cost_cap_usd=cost_cap,
            start=start,
        )
        # uat-bug-021: additive, next to the RankResponse's own keys.
        self._write_json(
            HTTPStatus.OK,
            {**response.to_json(), "rank_status": status.to_json(), "usage": jev_budget.usage(self._backend.home_root)},
        )


class JevUsageRoutesMixin:
    """``Handler`` mixin: ``GET /api/jev/usage`` -- today's Jev spend and the daily budget."""

    def _handle_get_jev_usage(self) -> None:
        self._write_json(HTTPStatus.OK, jev_budget.usage(self._backend.home_root))


__all__ = [
    "JevUsageRoutesMixin",
    "RankRoutesMixin",
    "compute_rank_scores",
    "rank_run",
    "start_or_join_rank",
    "wait_for_rank",
]
