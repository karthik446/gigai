"""0110-019: ``POST /api/runs/{run_id}/posted-window`` -- "Find postings from the last N days".

The Jobs page's "Posted" chips filter the postings shown by their own date
with no request. When the chosen window is wider than what the shown run
searched, its one button posts here.

The body:

* ``{}`` -- a page reading the run: searches nothing. It answers
  ``run_days`` (the window the run itself searched), ``searched_days`` (the
  widest window searched for the run so far, the button's threshold),
  ``choices`` (the chips, in days), ``added_total`` (postings added to the
  run after it ended) and ``search`` (the last search made, or ``null``).
* ``{"days": N}`` (1..365) -- the click. Searches the boards stored on this
  machine for the run's own roles and filters over the last ``N`` days
  (``posted_window.find_older``: the company index and the board cache, no
  board request), and adds to the run the postings it does not hold yet.
  The run in Runs is the same one: no run is created and the sealed run is
  not rewritten.

Only the added postings are then ranked and assessed, with what the run
already has:

* rank: a re-rank record of the run over its rows and the added ones
  (``rank.rank_added``). It is cache-first, so the rows the run scored are
  read from the score cache and only the added rows cost a model call.
  ``rank`` in the answer names the record; ``POST /rank {}`` follows it.
* assess: when that pass ends, an "assess all" job (``api/assess_all``)
  whose queue is the added postings only, at most the run's own assess cap
  of them, in rank order. Each goes through the job page's single-posting
  path into the quick-assess store, so nothing the run assessed, no stored
  assessment and no answer is touched. ``assess`` in the answer says how
  many were added and the cap; ``POST /assess-all {}`` follows the job.

``skip_reason`` names why nothing was searched: ``no_run_input`` (the run
sealed no input), ``run_not_finished`` (its own acquire may still be going),
``sources_update_required`` (nothing is stored yet: run Update sources).
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from pathlib import Path
import threading

from .. import posted_window
from ..contracts import MAX_AGE_DAYS_MAXIMUM, selection_cap_limit

_logger = logging.getLogger("gigai.scout.server")

RESPONSE_SCHEMA = "scout-find-jobs-posted-window:1"
_FOLLOWERS: list[threading.Thread] = []
_FOLLOWERS_LOCK = threading.Lock()


def _assess_limit(evidence) -> int | None:
    """How many of the added postings are assessed: the run's own cap (``None``: no run input)."""

    assess = getattr(evidence, "assess_output", None)
    cap = getattr(assess, "selection_cap", None)
    if cap is None and evidence.run_input is not None:
        cap = evidence.run_input.selection_cap
    return selection_cap_limit(cap) if cap is not None else None


def _follow(backend, run_id: str, urls: frozenset[str], limit: int | None, outcome) -> None:
    """After the rank pass of the added rows: assess the first ``limit`` of them (rank order)."""

    from .. import rank_records
    from .assess_all import assess_all_request
    from .rank import rank_added

    try:
        if outcome is not None and outcome.record is not None:
            rank_records.wait_for_record(outcome.record.record_id)
            if outcome.action == "joined":
                # The pass that was running started before the rows were added.
                again = rank_added(home_root=backend.home_root, target=backend.target, run_id=run_id)
                if again is not None and again.record is not None:
                    rank_records.wait_for_record(again.record.record_id)
        body = assess_all_request(backend, run_id, start=True, only=urls, limit=limit)
        job = body.get("job")
        _logger.info(
            "posted window (%s): assess the added postings: %s",
            run_id, job.get("text") if isinstance(job, dict) else body.get("skip_reason") or "nothing to assess",
        )
    except Exception as exc:  # noqa: BLE001 - the added rows stay listed; Rank and Assess all new still work
        _logger.warning("posted window (%s): the added postings were not assessed: %s", run_id, type(exc).__name__)


def posted_window_request(backend, run_id: str, *, days: int | None = None) -> dict[str, object]:
    """What ``POST /posted-window`` answers. ``LookupError`` when there is no such run."""

    from ...projection import read_run_evidence
    from .rank import rank_added

    resolved = backend._require_run(run_id)
    home_root, target = Path(backend.home_root), Path(backend.target)
    evidence = read_run_evidence(resolved, run_id)
    window = posted_window.read(home_root, target, run_id)
    run_input = evidence.run_input
    body: dict[str, object] = {
        "schema_version": RESPONSE_SCHEMA,
        "run_id": run_id,
        "run_days": None,
        "searched_days": None,
        "choices": list(posted_window.WINDOW_CHOICES),
        "added_total": len(window.rows) if window is not None else 0,
        "search": dict(window.searches[-1]) if window is not None and window.searches else None,
        "rank": None,
        "assess": None,
        "skip_reason": None,
    }
    if run_input is None or evidence.acquire_output is None:
        body["skip_reason"] = "no_run_input"
        return body
    base = posted_window.run_days(run_input.config, started_at=evidence.started_at)

    def searched(record) -> int:
        return max(base, (record.searched_days if record is not None else None) or 0)

    body["run_days"] = base
    body["searched_days"] = searched(window)
    if days is None:
        return body
    if not evidence.terminal:
        body["skip_reason"] = "run_not_finished"
        return body

    outcome = posted_window.find_older(
        home_root=home_root,
        target=target,
        run_id=run_id,
        config=run_input.config,
        run_rows=tuple(row.posting for row in evidence.acquire_output.rows),
        days=days,
    )
    body["search"] = outcome.search
    if outcome.needs_update:
        body["skip_reason"] = "sources_update_required"
        return body
    body["added_total"] = len(outcome.window.rows) if outcome.window is not None else 0
    body["searched_days"] = searched(outcome.window)
    if not outcome.added:
        return body

    urls = frozenset(row.normalized_url for row in outcome.added)
    limit = _assess_limit(evidence)
    ranked = rank_added(home_root=home_root, target=target, run_id=run_id)
    if ranked is not None and ranked.record is not None:
        body["rank"] = {"record_id": ranked.record.record_id, "action": ranked.action}
    body["assess"] = {"added": len(urls), "limit": limit}
    follower = threading.Thread(
        target=_follow, args=(backend, run_id, urls, limit, ranked), name=f"scout-posted-window-{run_id}", daemon=True
    )
    with _FOLLOWERS_LOCK:
        _FOLLOWERS[:] = [item for item in _FOLLOWERS if item.is_alive()] + [follower]
    follower.start()
    return body


def wait_for_posted_window(*, timeout: float | None = None) -> bool:
    """Wait until no search's rank-then-assess step runs in this process; false when ``timeout`` ran out first."""

    with _FOLLOWERS_LOCK:
        running = list(_FOLLOWERS)
    for item in running:
        item.join(timeout)
    return not any(item.is_alive() for item in running)


class PostedWindowRoutesMixin:
    """``Handler`` mixin: ``POST /api/runs/{run_id}/posted-window``."""

    def _handle_post_posted_window(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict) or set(body) - {"days"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", 'body is {} or {"days": <whole number>}')
            return
        days = body.get("days")
        if days is not None and type(days) is not int:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "days must be a whole number")
            return
        if days is not None and not posted_window.valid_days(days):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"days must be from 1 to {MAX_AGE_DAYS_MAXIMUM}")
            return
        if getattr(self._backend, "target", None) is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        try:
            response = posted_window_request(self._backend, run_id, days=days)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, response)


__all__ = ["PostedWindowRoutesMixin", "RESPONSE_SCHEMA", "posted_window_request", "wait_for_posted_window"]
