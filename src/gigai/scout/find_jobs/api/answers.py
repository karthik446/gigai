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

Q4b-data: a posting assessed only by a find-jobs run has no quick-assess
store entry (C5: run results live in ``runs/*/outputs``, never the store).
Its job page identity is the posting's ``normalized_url``, so when the store
has nothing for ``job_identity`` the route falls back to the newest run whose
sealed ``outputs/acquire.json`` acquired that posting: its URL/title/company
(and the run's own profile) become the quick-assess request, which re-fetches
the posting and lands the result in the store with the ``answer:<id>``
trigger -- so the page's history reads "Re-assessed after you answered ...".
``reassess_not_found`` (404) stays for an identity no run or store knows.
"""

from __future__ import annotations

from http import HTTPStatus

from ....native_records import NativeRecordResult
from ....private_records import PrivateRecordError
from ...experience_answers import PriorAnswer, read_answers, record_answer
from ...question_ids import normalize_question_id
from ..contracts import AcquireOutput, FindJobsContractError, PostingRow, normalize_url
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


def find_run_posting(home_root, target, job_identity: str) -> tuple[PostingRow, str | None] | None:
    """The newest find-jobs run's acquired posting for ``job_identity``, plus that run's profile id.

    Q4b-data: ``job_identity`` is the posting's ``normalized_url`` (the job
    page's card <-> assessment join key). Runs are enumerated newest first
    exactly as ``GET /api/runs`` does (``runs_list._run_ids_newest_first``)
    and each one's sealed ``outputs/acquire.json`` is read through the
    journal (never a full replay); a run without a sealed acquire output,
    or whose output no longer parses, is skipped. Returns ``None`` when no
    run acquired the posting. The profile id is the run's own
    (``runs_list._run_profile_id``; ``None`` -> the gig's selected profile),
    so the re-assessment is scored against the resume the run used.
    """

    from ....canonical import parse_json_bytes
    from ....journal import JournalArtifactMissingError, read_committed_artifact
    from ....workpad import resolve_workpad
    from .runs_list import _run_ids_newest_first, _run_profile_id

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception:  # noqa: BLE001 - an unbound folder simply has no runs to fall back to
        return None
    try:
        wanted = normalize_url(job_identity)
    except Exception:  # noqa: BLE001 - a non-URL identity (text:sha256:...) can never be a run posting
        wanted = job_identity
    for run_id in _run_ids_newest_first(resolved):
        try:
            raw, _commit = read_committed_artifact(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                path=f"runs/{run_id}/outputs/acquire.json",
            )
            output = AcquireOutput.from_json(parse_json_bytes(raw))
        except (JournalArtifactMissingError, ValueError, FindJobsContractError):
            continue
        for row in output.rows:
            posting = row.posting
            if job_identity in (posting.normalized_url, posting.url) or wanted == posting.normalized_url:
                return posting, _run_profile_id(resolved=resolved, run_id=run_id, default_profile_id=None)
    return None


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
            run_posting = find_run_posting(self._backend.home_root, target, job_identity)
            if run_posting is None:
                raise QuickAssessError("reassess_not_found", f"no stored assessment for job_identity {job_identity!r}")
            posting, profile_id = run_posting
            request = AssessRequest(
                job=AssessJobInput(job_url=posting.url, title=posting.title or None, company=posting.company or None),
                resume=AssessResumeInput(profile_id=profile_id),
            )
        else:
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


__all__ = ["AnswersRoutesMixin", "find_run_posting"]
