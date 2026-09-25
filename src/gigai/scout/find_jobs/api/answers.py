"""P3: ``POST /api/answers`` and ``GET /api/answers`` -- the Q&A loop.

``POST /api/answers`` upserts one answered ``experience_qa`` question
(``gigai.scout.experience_answers.record_answer``) and, when ``reassess``
names a ``job_identity``, re-runs the whole posting's assessment through
``quick_assess.run_quick_assessment`` so the just-recorded answer (plus every
other answered question) reaches the prompt immediately (assess.md rule 6).
Operator answer 3: writing to ``experience_qa`` happens ONLY here, on an
explicit answer -- never at assess time. Operator answer 2: a re-assessment
lands in the quick-assess store (never ``runs/*/outputs``, C5) and simply
overwrites the same ``(resume, job_identity)`` file ``run_quick_assessment``
always writes to.

``GET /api/answers`` lists every answered question in this gig (the same
rows ``experience_answers.read_answers`` returns), each with the record it
lives in -- the pending list itself (stored results with verdict
``pending_user_answers`` minus these ids) is a UI/CLI-side join over
``GET /api/assessments``, never computed or stored here (plan section 8
answer 3: the pending list is DERIVED, not recorded).

Re-assess resolves the target job the same way the original assessment did:
a fetched job (``source_url`` set) is re-fetched by URL; pasted text has no
``source_url`` and its original text was never persisted (the no-leak rule),
so re-assessing a pasted-text job is not possible from the identity alone --
``reassess_unavailable`` (422) names that rather than silently reusing stale
text.
"""

from __future__ import annotations

from http import HTTPStatus

from ....native_records import NativeRecordResult
from ....private_records import PrivateRecordError
from ...experience_answers import PriorAnswer, read_answers, record_answer
from ...question_ids import normalize_question_id
from ...quick_assess import (
    TRIGGER_ANSWER_PREFIX,
    QuickAssessError,
    find_quick_assessment_by_job_identity,
    run_quick_assessment,
)
from ..assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput

_ANSWER_ERROR_STATUS: dict[str, HTTPStatus] = {
    "answer_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "reassess_unavailable": HTTPStatus.UNPROCESSABLE_ENTITY,
    "reassess_not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "workpad_layout_migration_required": HTTPStatus.CONFLICT,
    "native_record_invalid": HTTPStatus.CONFLICT,
    "native_record_too_large": HTTPStatus.CONFLICT,
    "stale_parent": HTTPStatus.CONFLICT,
}


def _status_for(code: str) -> HTTPStatus:
    return _ANSWER_ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


def _answer_to_json(item: PriorAnswer) -> dict[str, object]:
    return {
        "question_id": item.question_id,
        "prompt": item.prompt,
        "answer": item.answer,
        "record_id": item.record_id,
        "revision_id": item.revision_id,
    }


class AnswersRoutesMixin:
    """``Handler`` mixin: ``POST /api/answers`` and ``GET /api/answers``."""

    def _answers_target(self):
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _handle_post_answers(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        unknown = set(body) - {"question_id", "answer", "reassess"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown field(s): {sorted(unknown)}")
            return
        question_id = body.get("question_id")
        answer = body.get("answer")
        if not isinstance(question_id, str) or not question_id.strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "question_id must be a non-empty string")
            return
        if not isinstance(answer, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "answer must be a string")
            return
        reassess = body.get("reassess")
        job_identity: str | None = None
        if reassess is not None:
            if not isinstance(reassess, dict) or set(reassess) != {"job_identity"} or not isinstance(reassess.get("job_identity"), str):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", 'reassess must be {"job_identity": "<id>"} or null')
                return
            job_identity = reassess["job_identity"]

        target = self._answers_target()
        if target is None:
            return

        try:
            result = record_answer(
                home_root=self._backend.home_root, requested_target=target,
                question_id=question_id, prompt=question_id, answer=answer,
            )
        except PrivateRecordError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        assert isinstance(result, NativeRecordResult)

        normalized_question_id = normalize_question_id(question_id)

        reassessed: dict[str, object] | None = None
        if job_identity is not None:
            try:
                reassessed = self._reassess(target, job_identity, trigger=TRIGGER_ANSWER_PREFIX + normalized_question_id)
            except QuickAssessError as exc:
                self._error(_status_for(exc.code), exc.code, str(exc))
                return

        self._write_json(
            HTTPStatus.CREATED,
            {
                "record_id": result.record_id,
                "revision_id": result.revision_id,
                "question_id": normalized_question_id,
                "reassessed": reassessed,
            },
        )

    def _reassess(self, target, job_identity: str, *, trigger: str) -> dict[str, object]:
        """Re-run the whole assessment for ``job_identity`` and return the
        new ``AssessResponse`` JSON. Raises ``QuickAssessError``
        (``reassess_not_found`` if nothing was ever assessed for this
        identity; ``reassess_unavailable`` for a pasted-text job with no
        URL to re-fetch). ``trigger`` (Q4a) is recorded in the stored
        verdict history: ``answer:<question_id>``."""

        previous = find_quick_assessment_by_job_identity(self._backend.home_root, target, job_identity)
        if previous is None:
            raise QuickAssessError("reassess_not_found", f"no stored assessment for job_identity {job_identity!r}")
        if previous.job.source_url is None:
            raise QuickAssessError(
                "reassess_unavailable",
                "this job was assessed from pasted text, which is never stored; re-assess with --job-text again",
            )
        request = AssessRequest(
            job=AssessJobInput(job_url=previous.job.source_url, title=previous.job.title or None, company=previous.job.company or None),
            resume=AssessResumeInput(profile_id=previous.resume.profile_id),
        )
        response = run_quick_assessment(request, home_root=self._backend.home_root, target=target, trigger=trigger)
        return response.to_json()

    def _handle_get_answers(self) -> None:
        target = self._answers_target()
        if target is None:
            return
        try:
            answers = read_answers(home_root=self._backend.home_root, requested_target=target)
        except PrivateRecordError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        items = sorted(answers.values(), key=lambda item: item.question_id)
        self._write_json(HTTPStatus.OK, {"answers": [_answer_to_json(item) for item in items]})


__all__ = ["AnswersRoutesMixin"]
