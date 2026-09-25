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
        # against the gig's selected profile. `compute_rank_scores` is
        # cache-first (jev_rank.py), so this never re-spends after a `/rank`
        # call already scored the same rows for the same profile/resume
        # revision; no key, no rows, or any Jev failure degrades to an
        # empty response (fail open), never breaking /results itself.
        try:
            from .rank import compute_rank_scores

            rank_response = compute_rank_scores(
                home_root=self._backend.home_root,
                target=self._backend.target,
                run_id=run_id,
                profile_id=None,
            )
            rank_scores = rank_response.scores
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
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_run_progress(self, run_id: str) -> None:
        try:
            progress_response = self._backend.run_progress(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, progress_response)
