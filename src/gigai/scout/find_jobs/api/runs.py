"""R0: the run lifecycle routes (``POST /api/run``, ``GET
/api/runs/{run_id}``, ``.../progress``, ``.../results``), moved out of
``present_api.py`` verbatim.
"""

from __future__ import annotations

from functools import lru_cache
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING

from ..contracts import ATSProvider, FindJobsContractError, RunRequest
from ..job_state import AssessmentFact, JobStateSources
from .server import ConfigMissingError, _RunBoundaryError, _logger

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from ..company_catalog import CompanyH1B


@lru_cache(maxsize=1)
def _catalog_h1b_index() -> dict[tuple[ATSProvider, str], CompanyH1B]:
    """``(provider, board token lower-cased)`` -> the shipped catalog's H-1B aggregate.

    Q4b-data: read-only use of the bundled company catalog
    (``company_catalog.load_company_catalog``, itself cached per process),
    built once. A catalog that fails to load (missing resource, digest
    mismatch) degrades to an empty index: the join is display-only
    enrichment and must never break ``/results``.
    """

    from ..company_catalog import CompanyCatalogError, load_company_catalog

    try:
        return load_company_catalog().h1b_by_board()
    except CompanyCatalogError:
        return {}


def attach_h1b(rows: list[object], index: dict[tuple[ATSProvider, str], CompanyH1B]) -> None:
    """Add ``h1b`` to each results row whose posting's board is in ``index``.

    ``rows`` is the served ``payload.rows`` JSON (``[{"posting": {...},
    "outcome": ...}, ...]``), mutated in place: a row gains
    ``"h1b": {"approvals": int, "fiscal_years": [...]}`` when its
    ``posting.provider``/``posting.board_token`` name a catalog record whose
    ``h1b`` is an object; every other row is left exactly as it was (no
    key, never ``null``). Exa rows have no board token and never match.
    """

    for row in rows:
        if not isinstance(row, dict):
            continue
        posting = row.get("posting")
        if not isinstance(posting, dict):
            continue
        provider_raw = posting.get("provider")
        token = posting.get("board_token")
        if not isinstance(provider_raw, str) or not isinstance(token, str) or not token:
            continue
        try:
            provider = ATSProvider(provider_raw)
        except ValueError:
            continue
        aggregate = index.get((provider, token.lower()))
        if aggregate is not None:
            row["h1b"] = aggregate.to_json()


def _verdict_of(result: object) -> str | None:
    verdict = result.get("verdict") if isinstance(result, dict) else None
    return verdict if isinstance(verdict, str) else None


def run_assessment_facts(body: dict[str, object], run_started_at: str | None) -> dict[str, AssessmentFact]:
    """This run's assessment of each posting, by ``normalized_url``.

    The same choice ``boardRows.js`` makes for a card: the run's own
    assessment, else the earlier one the run carried forward (dated by the
    run it came from, or this run when that is unknown).
    """

    facts: dict[str, AssessmentFact] = {}
    for entry in body.get("carried_forward_assessments") or ():
        if not isinstance(entry, dict) or not isinstance(entry.get("normalized_url"), str):
            continue
        from_run_date = entry.get("from_run_date")
        at = from_run_date if isinstance(from_run_date, str) and from_run_date else run_started_at
        facts[entry["normalized_url"]] = AssessmentFact(at=at, verdict=_verdict_of(entry.get("result")))
    payload = body.get("payload")
    for entry in (payload.get("assessments") if isinstance(payload, dict) else None) or ():
        posting = entry.get("posting") if isinstance(entry, dict) else None
        if isinstance(posting, dict) and isinstance(posting.get("normalized_url"), str):
            facts[posting["normalized_url"]] = AssessmentFact(at=run_started_at, verdict=_verdict_of(entry))
    return facts


def attach_job_states(
    body: dict[str, object],
    *,
    sources: JobStateSources,
    profile_id: str | None,
    run_started_at: str | None,
) -> None:
    """uat-bug-018: add ``job_state`` to each results row.

    ``rows[].job_state`` is ``{state, since, next_events}`` for the row's
    posting and the resume identity ``profile_id`` (``job_state.py`` has the
    states and their precedence). Added to the served JSON only, next to the
    sealed ``posting``/``outcome`` pair, like ``rows[].h1b``.
    """

    payload = body.get("payload")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return
    facts = run_assessment_facts(body, run_started_at)
    for row in rows:
        posting = row.get("posting") if isinstance(row, dict) else None
        identity = posting.get("normalized_url") if isinstance(posting, dict) else None
        if not isinstance(identity, str) or not identity:
            continue
        state = sources.state_for(identity, profile_id=profile_id, run_assessment=facts.get(identity))
        row["job_state"] = state.to_json()


class RunRoutesMixin:
    """``Handler`` mixin: ``POST /api/run`` and the ``/api/runs/{run_id}...`` GETs."""

    def _handle_post_run(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            run_request = RunRequest.from_json(body)
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        try:
            config, config_bytes = self._backend.read_config()
        except ConfigMissingError as exc:
            self._error(
                HTTPStatus.NOT_FOUND,
                "config_missing",
                f"{exc.path} does not exist yet. Run `gigai scout install` or "
                "`gigai scout run` to write a starter find-jobs.json, then edit it.",
            )
            return
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
            return
        if run_request.config_digest != config.digest():
            self._error(HTTPStatus.CONFLICT, "config_digest_mismatch", "config_digest does not match the current config")
            return

        allocated = threading.Event()
        allocation: dict[str, str] = {}
        pre_allocation_error: BaseException | None = None
        post_allocation_error: BaseException | None = None

        def _on_run_allocated(run_id: str) -> None:
            allocation["run_id"] = run_id
            allocated.set()

        def _run() -> None:
            nonlocal pre_allocation_error, post_allocation_error
            try:
                self._backend.start_run(run_request, config_bytes, _on_run_allocated)
            except BaseException as exc:  # noqa: BLE001 - routed to the right side of allocation, not swallowed
                if allocated.is_set():
                    post_allocation_error = exc
                else:
                    pre_allocation_error = exc
                    allocated.set()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        reached = allocated.wait(self._run_start_timeout_seconds)
        if not reached:
            self._error(HTTPStatus.GATEWAY_TIMEOUT, "run_start_timeout", "run did not allocate a run_id in time")
            return
        if pre_allocation_error is not None:
            if isinstance(pre_allocation_error, _RunBoundaryError):
                _logger.warning(
                    "find-jobs run failed to start: %s (%s)",
                    pre_allocation_error.code,
                    pre_allocation_error,
                )
                self._error(
                    pre_allocation_error.status,
                    pre_allocation_error.code,
                    str(pre_allocation_error),
                )
                return
            if isinstance(pre_allocation_error, FindJobsContractError):
                _logger.warning(
                    "find-jobs run failed to start: %s (%s)",
                    pre_allocation_error.code,
                    pre_allocation_error,
                )
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, pre_allocation_error.code, str(pre_allocation_error))
                return
            _logger.exception("find-jobs run failed to start", exc_info=pre_allocation_error)
            raise pre_allocation_error
        # post_allocation_error (if any) surfaces via run_status, not here: the POST
        # response is already committed to a run_id once allocation happened. A
        # "run finished" event belongs at that same layer (this handler only ever
        # observes allocation, not completion) -- out of scope for this module.
        run_id = allocation["run_id"]
        _logger.info("find-jobs run started: run_id=%s", run_id)
        payload = {
            "schema_version": "scout-find-jobs-run-response:1",
            "run_id": run_id,
            "status": "pending",
            "node_receipts": [],
        }
        self._write_json(HTTPStatus.ACCEPTED, payload)

    def _handle_get_run_status(self, run_id: str) -> None:
        try:
            status_response = self._backend.run_status(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, status_response.to_json())

    def _handle_get_run_results(self, run_id: str) -> None:
        try:
            results_response = self._backend.run_results(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        body = results_response.to_json()
        # uat-bug-009: the run view's "Resume used" card showed the raw
        # `record_… (revision_…)` ids -- the same bug uat-bug-004 already
        # fixed for the Configuration card, just never carried over here.
        # Reuse the exact same additive fields (resume_label/
        # resume_created_at, resolved via backend.resume_metadata() ->
        # run.resolve_newest_resume_details), added next to
        # RunResultsResponse's own sealed to_json() rather than on the
        # PresentPayload contract itself, so the sealed payload shape
        # stays untouched (same reasoning as /api/config's resume_label).
        #
        # resume_metadata() resolves the *newest* committed resume, which
        # is not necessarily the resume this specific (possibly older)
        # run was pinned to -- so the label/date are only attached when
        # they identify the same record+revision as this run's actual
        # payload.pinned_resume; otherwise the UI falls back to the raw
        # ids it already carries, never a mislabeled resume.
        pinned = results_response.payload.pinned_resume
        resume_label: str | None = None
        resume_created_at: str | None = None
        if pinned is not None:
            try:
                preview = self._backend.resume_preview()
            except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
                preview = None
            if preview is not None and preview.record_id == pinned.record_id and preview.revision_id == pinned.revision_id:
                try:
                    metadata = self._backend.resume_metadata()
                except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
                    metadata = None
                if metadata is not None:
                    resume_label, resume_created_at = metadata
        body["resume_label"] = resume_label
        body["resume_created_at"] = resume_created_at
        # uat-bug-009: a posting skipped this run as unchanged (because a
        # successful assessment of it already exists for the current
        # resume revision) carries that earlier result into this run's
        # present output -- additively, alongside (never inside) the
        # sealed assessed/not-assessed partition, so the card can show
        # the carried fit/reasons instead of a bare "Not assessed".
        try:
            carried_forward = self._backend.carried_forward_assessments(run_id)
        except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
            carried_forward = ()
        body["carried_forward_assessments"] = [item.to_json() for item in carried_forward]
        # P6: additive -- Jev's pre-rank scores for this run's postings
        # against the gig's selected profile. run-reads-fast (uat-bug-022,
        # uat-bug-021 addendum 2): a read only READS scores, the ones
        # already stored (the score cache, then the run's own sealed
        # scores; ``run_reads.stored_rank_scores``). It used to score
        # every row not yet cached, one Jev call each, inside this GET. A
        # row with no stored score has no entry; no profile, no key or an
        # unreadable cache is an empty list, never a broken /results.
        try:
            rank_scores = self._run_stored_rank_scores(run_id)
        except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
            rank_scores = ()
        body["rank_scores"] = [item.to_json() for item in rank_scores]
        # Q4b-data: the H-1B join. ``rows[].h1b`` (field contract shared with
        # Q4b-ui) is added to the served JSON only, next to the sealed
        # ``posting``/``outcome`` pair -- the sealed present payload and its
        # contracts are untouched, same reasoning as the enrichments above.
        try:
            payload_json = body.get("payload")
            if isinstance(payload_json, dict) and isinstance(payload_json.get("rows"), list):
                attach_h1b(payload_json["rows"], _catalog_h1b_index())
        except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
            _logger.exception("H-1B join skipped for run %s", run_id)
        # uat-bug-018: ``rows[].job_state``, derived from the application
        # events, the tailored-resume store and the latest assessment (this
        # run's, or the quick store's when it is newer) for the gig's
        # selected profile. The events read takes no journal writer lock.
        try:
            self._attach_results_job_states(run_id, body)
        except Exception:  # noqa: BLE001 - display-only enrichment must never break /results
            _logger.exception("job state skipped for run %s", run_id)
        self._write_json(HTTPStatus.OK, body)

    def _attach_results_job_states(self, run_id: str, body: dict[str, object]) -> None:
        from ....canonical import parse_json_bytes
        from ....workpad import resolve_workpad
        from ...profile_records import ProfileRecordError, selected_profile

        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            return
        resolved = resolve_workpad(
            home_root=backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True
        )
        try:
            selection = selected_profile(resolved, home_root=backend.home_root, target=target)
        except ProfileRecordError:
            selection = None
        if selection is None:
            return  # no resume identity to read the stores for
        run_started_at: str | None = None
        try:
            details = parse_json_bytes((resolved.path / "runs" / run_id / "run-details.json").read_bytes())
            if isinstance(details, dict) and isinstance(details.get("started_at"), str):
                run_started_at = details["started_at"]
        except (OSError, ValueError):
            run_started_at = None
        attach_job_states(
            body,
            sources=JobStateSources(home_root=backend.home_root, target=target, resolved=resolved),
            profile_id=selection.profile_id,
            run_started_at=run_started_at,
        )

    def _handle_get_run_progress(self, run_id: str) -> None:
        try:
            progress_response = self._backend.run_progress(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, progress_response)
