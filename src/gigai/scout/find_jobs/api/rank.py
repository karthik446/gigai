"""``POST /api/runs/{run_id}/rank`` -- re-rank one run with the operator's own model.

SCOPE-ADD-3 C1: a re-rank is a durable run record of the ``rank`` kind
(``rank_records``: ``runs/rank_<uuid>/``, journal-committed details and
``outputs/rank.json``, run-local progress), no longer a daemon thread in a
process-local dict. The run's own model target (the
``model_target`` sealed in the run's input: the target the run was started
with, which the UI offers from find-jobs.json's ``default_model_target``, else
``ollama_local``) ranks the run's sealed postings
against the given or selected profile's resume and the run's sealed
preferences; the sealed run is never rewritten.

The body (``RankRequest`` fields plus two flags that are not):

* ``{}`` -- a page reading the run: starts nothing. It answers the scores of
  the newest rank record for this ``(run, profile, resume revision, prefs,
  prompt)`` -- the live ones while its pass runs -- else the run's own
  sealed scores (its ranking step), with ``rank_status`` ``running`` /
  ``scored`` / ``skipped`` / ``not_requested``.
* ``{"start": true}`` -- the click. Single flight: joins the pass running
  for the same key (in this process or another live one), resumes a record
  a restart interrupted (its completed batches come from the score cache),
  answers a ``complete`` record as it is, or starts a new record. It never
  waits for the model.
* ``{"cancel": true}`` -- stops the running pass for the key: no further
  model call starts; the record finishes ``cancelled`` with the batches
  that landed.

Every answer keeps the ``RankResponse`` keys the page reads (``scores`` in
the run's own order, ``total_cost_usd`` ``"0"``: a local CLI bills no
dollars here) plus ``rank_status`` and ``rank_record`` (the record's id,
status -- ``interrupted`` when no live process runs it --, ranked/total).
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
from pathlib import Path


from ....journal import JournalArtifactMissingError, read_committed_artifact
from .. import rank_records, rank_run
from ..contracts import AcquireOutput, FindJobsContractError, FindJobsRunInput
from ..rank_contracts import RankRequest, RankResponse, RankScore
from ..model_rank import RankResult
from ..selection import rank_rows

_logger = logging.getLogger("gigai.scout.server")


@dataclass(frozen=True)
class _Skip:
    """Why a run cannot be ranked: ``reason`` and how many postings it has."""

    reason: str
    total: int = 0


@dataclass(frozen=True)
class _Resolved:
    source: rank_records.RankInput
    acquire: AcquireOutput
    #: The run's postings in its own order, then the ones added after it ended (``posted_window``).
    rows: tuple = ()


def _skip(run_id: str, reason: str, *, total: int = 0, exc: BaseException | None = None) -> _Skip:
    if exc is not None:
        # The type only: a message may name a path or quote a record.
        _logger.warning("rank (%s): %s: %s", run_id, reason, type(exc).__name__)
    return _Skip(reason, total)


def _resolve(*, home_root, target, run_id: str, profile_id: str | None) -> _Resolved | _Skip:
    """The run's sealed postings, input and the profile's resume -- or why there are none."""

    from ....canonical import parse_json_bytes
    from ....workpad import resolve_workpad
    from ...profile_records import list_profiles, selected_profile

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        return _skip(run_id, f"error:{type(exc).__name__}", exc=exc)
    try:
        raw, _commit = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=f"runs/{run_id}/outputs/acquire.json",
        )
        acquire = AcquireOutput.from_json(json.loads(raw))
    except (JournalArtifactMissingError, ValueError, FindJobsContractError, OSError) as exc:
        return _skip(run_id, "no_run_output", exc=exc)
    from ..posted_window import added_rows, with_added

    # 0110-019: the postings added to the run after it ended are ranked with it.
    rows = tuple(item.posting for item in with_added(acquire.rows, added_rows(home_root, target, run_id)))
    if not rows:
        return _skip(run_id, "no_candidates")
    total = len(rows)
    sealed_path = resolved.path / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    try:
        run_input = FindJobsRunInput.from_json(parse_json_bytes(sealed_path.read_bytes()))
    except (OSError, ValueError, FindJobsContractError) as exc:
        return _skip(run_id, "no_run_input", total=total, exc=exc)

    profile = None
    try:
        if profile_id is not None:
            profile = next((item for item in list_profiles(resolved) if item.profile_id == profile_id), None)
        else:
            profile = selected_profile(resolved, home_root=home_root, target=target)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged
        _logger.warning("rank (%s): the profile could not be read: %s", run_id, type(exc).__name__)
        profile = None
    if profile is None:
        return _skip(run_id, "no_profile", total=total)
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
        _logger.warning("rank (%s): the profile's resume could not be read: %s", run_id, type(exc).__name__)
        resume_text = None
    if not resume_text:
        return _skip(run_id, "no_resume", total=total)
    # The pass asks about the rows in the run's own rank order, so a pass
    # its call cap cuts short still scores the most promising rows.
    ordered = tuple(rank_rows(rows, acquire.rank_scores))
    source = rank_records.RankInput(
        workpad_resolved=resolved,
        parent_run_id=run_id,
        rows=ordered,  # type: ignore[arg-type]
        acquire_output_digest=acquire.digest(),
        profile_id=profile.profile_id,
        resume_revision_id=profile.resume_ref.revision_id,
        resume_text=resume_text,
        prefs=rank_run.rank_prefs(run_input.config),
        model_target=run_input.model_target.value,
        home_root=Path(home_root),
    )
    return _Resolved(source, acquire, rows)


def _scores_from_record(
    rows, record: rank_records.RankRecord | None
) -> tuple[tuple[RankScore, ...], bool]:
    """One entry per row from ``record`` (sealed result when finished, else live lines); ``(scores, any)``."""

    if record is None:
        return (), False
    result = record.result()
    if result is not None:
        scores = rank_run.to_rank_scores(result, rows)
        return scores, any(item.score is not None for item in scores)
    live = rank_records.live_scores(record)
    scores = tuple(_live_score(row, live.get(row.normalized_url)) for row in rows)
    return scores, any(item.score is not None for item in scores)


def _live_score(row, entry: dict | None) -> RankScore:
    score = entry.get("score") if isinstance(entry, dict) else None
    score = score if isinstance(score, int) and not isinstance(score, bool) else None
    blockers = tuple(str(item) for item in (entry.get("blockers") or ())) if isinstance(entry, dict) and score is not None else ()
    return RankScore(
        normalized_url=row.normalized_url,
        content_sha256=row.content_sha256 or "unknown",
        fit=rank_run.fit_for(score),
        score=score,
        reasons=(),
        mismatch_flags=blockers,
        hidden_by_default=False,
        cost_usd="0",
        cached=False,
    )


def _status(status: str, scored: int, total: int, reason: str | None) -> dict[str, object]:
    """``rank_status`` in the shape ``GET /progress`` serves (``rank_run.status_json``)."""

    text = {
        "running": f"ranking: {scored:,} of {total:,}",
        "scored": f"scored {scored} of {total}",
    }.get(status, f"skipped: {reason}" if status == "skipped" else f"{status}: {reason}")
    return {
        "status": status,
        "ranker": "model",
        "scored": scored,
        "total": total,
        "reason": reason,
        "text": text,
        "line": f"Ranking: {text}",
        "cost_cap_usd": None,
        "cost_usd": "0",
        "throttled": None,
        "spent_today_usd": None,
        "daily_budget_usd": None,
        "usage_line": None,
    }


def _body(run_id: str, scores: tuple[RankScore, ...], status: dict[str, object], record) -> dict[str, object]:
    unscored = sum(1 for item in scores if item.score is None)
    response = RankResponse(run_id, scores, "0", bool(status.get("reason") and str(status["reason"]).startswith("call_budget")), unscored)
    return {**response.to_json(), "rank_status": status, "rank_record": rank_records.record_summary(record)}


def rank_request(
    *, home_root, target, run_id: str, profile_id: str | None, start: bool = False, cancel: bool = False
) -> dict[str, object]:
    """What ``POST /rank`` answers, at once. Only ``start`` can start a pass; only ``cancel`` stops one."""

    try:
        found = _resolve(home_root=home_root, target=target, run_id=run_id, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is recorded and logged
        found = _skip(run_id, f"error:{type(exc).__name__}", exc=exc)
    if isinstance(found, _Skip):
        if start:
            _logger.warning("rank (%s, rank): skipped: %s", run_id, found.reason)
        return _body(run_id, (), _status("skipped", 0, found.total, found.reason), None)

    source = found.source
    rows = found.rows
    total = len(rows)
    outcome = rank_records.start_or_join(source, start=start and not cancel)
    record = outcome.record
    if cancel and record is not None and record.status == "running":
        rank_records.cancel(source.workpad_resolved.path, record.record_id)
    if start and not cancel:
        _logger.info("rank (%s, rank): %s %s", run_id, outcome.action, record.record_id if record else "-")

    scores, any_scored = _scores_from_record(rows, record)
    if record is not None and record.status == "running":
        scored = sum(1 for item in scores if item.score is not None)
        live = record.live_status()
        if live == "running":
            return _body(run_id, scores, _status("running", scored, total, None), record)
        return _body(run_id, scores, _status("scored" if scored else "skipped", scored, total, live), record)
    if record is not None and record.status in rank_records.FINISHED and any_scored:
        scored = sum(1 for item in scores if item.score is not None)
        reason = record.details.get("fail_open_reason")
        return _body(run_id, scores, _status("scored", scored, total, reason if isinstance(reason, str) else None), record)

    # No re-rank with scores: the run's own ranking step (sealed on its acquire output).
    own = {item.normalized_url: item for item in found.acquire.rank_scores}
    sealed = tuple(own.get(row.normalized_url) or _live_score(row, None) for row in rows)
    scored = sum(1 for item in sealed if item.score is not None)
    if record is not None and record.status in rank_records.FINISHED:
        reason = record.details.get("fail_open_reason") or record.status
        return _body(run_id, sealed, _status("scored" if scored else "skipped", scored, total, str(reason)), record)
    if scored == total:
        return _body(run_id, sealed, _status("scored", scored, total, None), record)
    return _body(run_id, sealed, _status("scored" if scored else "skipped", scored, total, "not_requested"), record)


def newest_rank_result(resolved, run_id: str, *, profile_id: str | None, resume_revision_id: str | None) -> RankResult | None:
    """The newest finished re-rank of ``run_id`` for that profile + resume revision (the reads' source)."""

    found = rank_records.newest_finished(
        resolved.path, run_id, profile_id=profile_id, resume_revision_id=resume_revision_id
    )
    return found[1] if found is not None else None


def wait_for_rank(*, timeout: float | None = None) -> bool:
    """Wait until no re-rank pass runs in this process; false when ``timeout`` ran out first."""

    return rank_records.wait_for_passes(timeout=timeout)


class RankRoutesMixin:
    """``Handler`` mixin: ``POST /api/runs/{run_id}/rank``."""

    def _handle_post_rank(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = dict(body) if isinstance(body, dict) else {}
            # Not RankRequest fields: what tells a click ("start") or a stop
            # ("cancel") from a page reading the run.
            start = payload.pop("start", False)
            cancel = payload.pop("cancel", False)
            if type(start) is not bool or type(cancel) is not bool:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "start and cancel must be true or false")
                return
            payload.setdefault("run_id", run_id)
            request = RankRequest.from_json(payload)
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        if request.run_id != run_id:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "run_id in the body must match the URL")
            return
        self._write_json(
            HTTPStatus.OK,
            rank_request(
                home_root=self._backend.home_root,
                target=self._backend.target,
                run_id=run_id,
                profile_id=request.profile_id,
                start=start,
                cancel=cancel,
            ),
        )


def rank_added(*, home_root, target, run_id: str):
    """0110-019: rank the postings just added to ``run_id`` (``posted_window``); the pass's outcome, or ``None``.

    A new pass over the run's rows with the added ones, also when the newest
    record is ``complete`` (that one never saw them). It is cache-first, so
    every row the run or an earlier pass scored is read from the score cache
    and only the added rows cost a model call. ``None`` when the run cannot
    be ranked (no profile, no resume): the added rows then stay unranked.
    """

    try:
        found = _resolve(home_root=home_root, target=target, run_id=run_id, profile_id=None)
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged
        found = _skip(run_id, f"error:{type(exc).__name__}", exc=exc)
    if isinstance(found, _Skip):
        _logger.warning("rank (%s, added postings): skipped: %s", run_id, found.reason)
        return None
    outcome = rank_records.start_or_join(found.source, start=True, rows_added=True)
    _logger.info("rank (%s, added postings): %s %s", run_id, outcome.action, outcome.record.record_id if outcome.record else "-")
    return outcome


__all__ = [
    "RankRoutesMixin",
    "newest_rank_result",
    "rank_added",
    "rank_request",
    "wait_for_rank",
]
