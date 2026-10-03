"""0.1.10.7 PL5: the pipeline's routes -- status, approvals, "process now".

- ``GET /api/pipeline``: what the background pipeline is doing
  (``pipeline.overview``): lanes, today's counters against their caps, queue
  counts per state, the approvals that wait, each job's steps and Scout
  label, the last errors.
- ``GET /api/pipeline/approvals``: the approvals (``state`` narrows them).
- ``POST /api/pipeline/approvals/{approval_id}`` ``{"approve": true|false}``:
  approve (its jobs open and the runner runs them, within the daily cap) or
  deny (its jobs are cancelled; no model call).
- ``POST /api/pipeline/process`` ``{"job_identity", "profile_id"?, "force"?}``:
  queue one assessed job now. It never waits for a model: the server's runner
  thread runs the steps (``gigai scout pipeline run --once`` without one).

SYSTEM DATA ONLY: ids, codes, counts, numbers and timestamps. A job is named
by its job identity (the posting's public link). No posting, resume, answer
or story text in any of these responses.

The write paths that trigger the pipeline (answers, stories, profiles) call
``_pipeline_fire`` / ``_pipeline_profile_changed`` here: the trigger never
fails the write, and the runner is woken instead of waiting for its poll.
"""

from __future__ import annotations

from http import HTTPStatus
import re
from urllib.parse import parse_qs, urlsplit

from ....private_records import PrivateRecordError
from ....workpad import WorkpadError
from ...pipeline import triggers
from ...pipeline.overview import overview
from ...pipeline.steps import StepError
from ...pipeline.store import APPROVAL_STATES, DECIDED_BY, PipelineStoreError
from ..contracts import FindJobsContractError, normalize_url
from .story_bank import ACTOR_HEADER

PROCESS_SCHEMA = "scout-pipeline-process:1"
APPROVAL_SCHEMA = "scout-pipeline-approval:1"

_APPROVAL_PATH = re.compile(r"^/api/pipeline/approvals/([A-Za-z0-9_.:-]{1,80})$")
_ERROR_STATUS = {
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "approval_not_found": HTTPStatus.NOT_FOUND,
    "assessment_missing": HTTPStatus.NOT_FOUND,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "profile_unavailable": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "posting_text_unavailable": HTTPStatus.UNPROCESSABLE_ENTITY,
}


def _match_approval_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{approval_id}`` from ``/api/pipeline/approvals/{approval_id}``.

    The shape of ``story_bank._match_answer_id`` (``server.py``'s dispatch and
    ``tests/api_e2e/route_inventory.py``'s scanner recognize the name).
    """

    found = None if suffix else _APPROVAL_PATH.match(path)
    return None if found is None else found.group(1)


class PipelineRoutesMixin:
    """``Handler`` mixin: ``GET /api/pipeline``, the approvals routes and ``POST /api/pipeline/process``."""

    def _pipeline_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
        return target

    def _pipeline_runner(self):
        return getattr(self.server, "pipeline_runner", None)

    def _pipeline_kick(self) -> bool:
        """Wake the server's runner; ``False`` when this server runs none."""

        runner = self._pipeline_runner()
        if runner is None:
            return False
        runner.kick()
        return True

    def _pipeline_fire(self, pending) -> None:
        """Queue what a saved answer or story concerns and wake the runner. Never raises (``Pending.fire``)."""

        fired = pending.fire()
        if fired.enqueued:
            self._pipeline_kick()

    def _pipeline_profile_changed(self, target, profile_id: str | None) -> None:
        """A profile's resume or settings changed: re-open its steps whose inputs changed. Never raises."""

        if triggers.profile_changed(self._backend.home_root, target, profile_id).enqueued:
            self._pipeline_kick()
        runner = self._pipeline_runner()
        if runner is not None:
            runner.kick_rank()  # DESIGN 10.6: the profile's demand set or candidate changed, so the rank lane looks now

    def _pipeline_fail(self, exc: BaseException) -> None:
        code = getattr(exc, "code", None)
        code = code if isinstance(code, str) else "scout_pipeline_failed"
        self._error(_ERROR_STATUS.get(code, HTTPStatus.CONFLICT), code, str(exc))

    def _handle_get_pipeline(self) -> None:
        if urlsplit(self.path).query:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no query keys")
            return
        try:
            body = overview(self._backend.home_root, getattr(self._backend, "target", None), runner=self._pipeline_runner())
        except PipelineStoreError as exc:
            self._pipeline_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_pipeline_approvals(self) -> None:
        target = self._pipeline_target()
        if target is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - {"state"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        state = (query.get("state") or [None])[0]
        if state is not None and state not in APPROVAL_STATES:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bad_enum", f"state must be one of: {', '.join(sorted(APPROVAL_STATES))}")
            return
        try:
            body = triggers.approvals(self._backend.home_root, target, state=state)
        except PipelineStoreError as exc:
            self._pipeline_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _pipeline_body(self, allowed: frozenset[str]) -> dict[str, object] | None:
        body = self._read_json_body()
        if body is None:
            return None
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return None
        unknown = sorted(set(body) - allowed)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return None
        return body

    def _handle_post_pipeline_approval(self, approval_id: str) -> None:
        body = self._pipeline_body(frozenset({"approve", "actor"}))
        if body is None:
            return
        approve, actor = body.get("approve"), body.get("actor")
        if type(approve) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "approve (required) must be true or false")
            return
        if actor is not None and type(actor) is not str:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "actor must be a string")
            return
        decided_by = (actor or self.headers.get(ACTOR_HEADER) or "operator").strip().lower()
        if decided_by not in DECIDED_BY:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bad_enum", f"actor must be one of: {', '.join(sorted(DECIDED_BY))}")
            return
        target = self._pipeline_target()
        if target is None:
            return
        try:
            approval = triggers.decide(self._backend.home_root, target, approval_id, approve=approve, decided_by=decided_by)
        except PipelineStoreError as exc:
            self._pipeline_fail(exc)
            return
        runner = self._pipeline_kick() if approve else self._pipeline_runner() is not None
        self._write_json(HTTPStatus.OK, {"schema_version": APPROVAL_SCHEMA, "approval": approval, "runner": runner})

    def _handle_post_pipeline_process(self) -> None:
        body = self._pipeline_body(frozenset({"job_identity", "profile_id", "force"}))
        if body is None:
            return
        job, profile_id, force = body.get("job_identity"), body.get("profile_id"), body.get("force", False)
        if type(job) is not str or not job.strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "job_identity (required) must be a non-empty string")
            return
        if (profile_id is not None and type(profile_id) is not str) or type(force) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "profile_id must be a string and force true or false")
            return
        target = self._pipeline_target()
        if target is None:
            return
        home_root = self._backend.home_root
        try:
            identity = job if job.startswith("text:sha256:") else normalize_url(job)
            if not profile_id:
                from ....workpad import resolve_workpad
                from ... import profile_records

                resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
                selected = profile_records.selected_profile(resolved, home_root=home_root, target=target)
                if selected is None:
                    raise StepError("profile_unavailable", "no scout profile is selected for this project; pass profile_id")
                profile_id = selected.profile_id
            queued = triggers.process_now(home_root, target, profile_id, identity, force=force)
        except (StepError, PipelineStoreError, FindJobsContractError, WorkpadError, PrivateRecordError) as exc:
            self._pipeline_fail(exc)
            return
        self._write_json(
            HTTPStatus.ACCEPTED,
            {
                "schema_version": PROCESS_SCHEMA,
                "result": queued["result"],
                "profile_id": queued["profile_id"],
                "job_identity": queued["job"],
                "input_digest": queued["input_digest"],
                # Whether this server runs the pipeline's thread; false: run `gigai scout pipeline run --once`.
                "runner": self._pipeline_kick(),
            },
        )


__all__ = ["APPROVAL_SCHEMA", "PROCESS_SCHEMA", "PipelineRoutesMixin", "_match_approval_id"]
