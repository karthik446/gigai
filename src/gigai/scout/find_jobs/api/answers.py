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

0.1.10.7 C: an answer belongs to the USER, not to a profile
(``story_bank.py``, ``story-bank-contract.md``). ``POST /api/answers`` saves
one answer for every profile to reuse (``story_bank.save_answer``: the
contact-data check, then the same ``experience_qa`` write as before).
Optional ``question`` keeps the question's own words with the answer;
optional ``tag``; optional ``from_bank`` names the question a near-match
suggestion came from, when the user confirmed it; optional ``actor``
(``operator`` or ``agent``; also the ``X-GigAI-Actor`` header; 0110-10-04:
without either, ``operator`` for the Scout UI and ``agent`` for any other
loopback caller, ``_story_bank_actor``) is recorded on the answer; optional
``source`` is free text, where the answer came from; optional ``revision`` is the revision the writer
read (``409 revision_conflict`` with the current ``answer`` when it has
changed since). The reply carries the saved ``answer`` in the one Answer
shape. ``GET /api/answers`` lists every answer in that shape (``q`` and
``tag`` narrow it). One answer, the edit, the delete and the near match are
``/api/answers/{question_id}`` and ``/api/answers/match`` (``api/story_bank.py``).

0.1.11.6 AN1: WHICH PROFILE the re-assessment is for. ``reassess`` takes an
optional ``profile_id`` beside ``job_identity`` (the job page sends its own);
the new assessment is made for and stored under that profile, whoever else
holds the job (``quick_assess.reassess_target``). An id that is no profile of
this gig answers ``404 profile_not_found``. Without it the one profile that
holds the job is taken, as before; a job assessed for more than one active
profile answers ``409 reassess_profile_required`` (until 0.1.11.6 the profile
of the NEWEST stored assessment was taken: an answer given on one profile's
page re-assessed the job for another). Both refusals come BEFORE the answer
is saved: nothing is written and no model is called. A named profile with no
stored assessment of its own is assessed from the address another holder's
(or a run's) names, and that first item is the job page's (``job_page``).

Origin (assess-origin-field): a re-assessment keeps the stored item's
``origin`` (the request names none, so ``quick_assess._origin_for`` leaves it
as it is); the run-only posting above has no stored item yet and its first
one is the job page's, so that request says ``job_page``.
"""

from __future__ import annotations

from http import HTTPStatus

from urllib.parse import parse_qs, urlsplit

from ....private_records import PrivateRecordError
from ... import story_bank
from ...pipeline import triggers as pipeline_triggers
from ...question_ids import normalize_question_id
from ..contracts import AcquireOutput, FindJobsContractError, PostingRow, normalize_url
from ...quick_assess import (
    TRIGGER_ANSWER_PREFIX,
    QuickAssessError,
    ReassessTarget,
    reassess_target,
    run_quick_assessment,
)
from ..assess_contracts import ORIGIN_JOB_PAGE, AssessJobInput, AssessRequest, AssessResumeInput
from .assess import write_assess_error
from .story_bank import ANSWERS_SCHEMA, ERROR_STATUS, answers_response, error_extra

_ANSWER_ERROR_STATUS: dict[str, HTTPStatus] = {
    "answer_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "reassess_unavailable": HTTPStatus.UNPROCESSABLE_ENTITY,
    "reassess_not_found": HTTPStatus.NOT_FOUND,
    "reassess_profile_required": HTTPStatus.CONFLICT,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    **ERROR_STATUS,
    "workpad_layout_migration_required": HTTPStatus.CONFLICT,
    "native_record_invalid": HTTPStatus.CONFLICT,
    "native_record_too_large": HTTPStatus.CONFLICT,
    "stale_parent": HTTPStatus.CONFLICT,
}


def _status_for(code: str) -> HTTPStatus:
    return _ANSWER_ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


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

        unknown = set(body) - {"question_id", "answer", "reassess", "question", "tag", "from_bank", "actor", "revision", "source"}
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
        profile_id: str | None = None
        if reassess is not None:
            shaped = isinstance(reassess, dict) and {"job_identity"} <= set(reassess) <= {"job_identity", "profile_id"}
            if not shaped or not isinstance(reassess.get("job_identity"), str) or not isinstance(reassess.get("profile_id"), str | None):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", 'reassess must be {"job_identity": "<id>"}, with an optional "profile_id", or null')
                return
            job_identity, profile_id = reassess["job_identity"], reassess.get("profile_id")

        for key in ("question", "tag", "from_bank", "actor", "source"):
            if body.get(key) is not None and not isinstance(body[key], str):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"{key} must be a string")
                return

        target = self._answers_target()
        if target is None:
            return
        home_root = self._backend.home_root

        # 0.1.11.6: which profile the re-assessment is for, settled before anything is written (one read of the store).
        plan: ReassessTarget | None = None
        if job_identity is not None:
            try:
                plan = reassess_target(home_root, target, job_identity, profile_id=profile_id)
            except QuickAssessError as exc:
                self._error(_status_for(exc.code), exc.code, str(exc))
                return

        # Who writes (operator, or an agent), which revision it read, and which posting asked.
        try:
            actor = self._story_bank_actor(body.get("actor"))
            entry = story_bank.save_answer(
                home_root=home_root, target=target, question_id=question_id, answer=answer,
                question=body.get("question"), tag=body.get("tag"),
                job=self._answer_job(target, job_identity, plan), confirmed_from=body.get("from_bank"), actor=actor,
                source=body.get("source"),
                expected_revision=story_bank.revision_value(body.get("revision")),
            )
        except (PrivateRecordError, story_bank.StoryBankError) as exc:
            extra = error_extra(exc)
            if extra is not None:
                self._error_with_extra(_status_for(exc.code), exc.code, str(exc), extra)
                return
            self._error(_status_for(exc.code), exc.code, str(exc))
            return

        normalized_question_id = normalize_question_id(question_id)

        # 0.1.10.7 PL5: the jobs that asked this question enter the pipeline. Who asked is read now, before the
        # re-assessment answers it; they are queued after it, whether or not it worked (the answer is saved).
        asked = pipeline_triggers.pending_answer(home_root, target, entry, job_identity=job_identity)
        reassessed: dict[str, object] | None = None
        try:
            if job_identity is not None and plan is not None:
                try:
                    reassessed = self._reassess(target, job_identity, plan, trigger=TRIGGER_ANSWER_PREFIX + normalized_question_id)
                except QuickAssessError as exc:
                    write_assess_error(self, _status_for(exc.code), exc)  # 0110-10-13: the typed cause's facts
                    return
        finally:
            self._pipeline_fire(asked)

        self._write_json(
            HTTPStatus.CREATED,
            {
                "schema_version": ANSWERS_SCHEMA,
                "record_id": entry.record_id,
                "revision_id": entry.revision_id,
                "question_id": normalized_question_id,
                "answer": entry.to_json(),
                "reassessed": reassessed,
            },
        )

    def _answer_job(self, target, job_identity: str | None, plan: ReassessTarget | None) -> dict[str, object] | None:
        """The posting an answer is tied to (its ``jobs``): the ``reassess`` job, ``None`` without one."""

        if job_identity is None:
            return None
        home_root = self._backend.home_root
        previous = None if plan is None else plan.previous
        if previous is not None:
            return {"job_identity": job_identity, "title": previous.job.title, "company": previous.job.company, "url": previous.job.source_url}
        run_posting = find_run_posting(home_root, target, job_identity)
        if run_posting is not None:
            row, _profile_id = run_posting
            return {"job_identity": job_identity, "title": row.title, "company": row.company, "url": row.url}
        return {"job_identity": job_identity, "title": "", "company": "", "url": None}

    def _reassess(self, target, job_identity: str, plan: ReassessTarget, *, trigger: str) -> dict[str, object]:
        """Re-run the whole assessment for ``job_identity``, for ``plan``'s
        profile, and return the new ``AssessResponse`` JSON. Raises ``QuickAssessError``
        (``reassess_not_found`` if nothing was ever assessed for this
        identity; ``reassess_unavailable`` for a pasted-text job with no
        URL to re-fetch). ``trigger`` (Q4a) is recorded in the stored
        verdict history: ``answer:<question_id>``."""

        previous = plan.previous
        if previous is None:
            run_posting = find_run_posting(self._backend.home_root, target, job_identity)
            if run_posting is None:
                raise QuickAssessError("reassess_not_found", f"no stored assessment for job_identity {job_identity!r}")
            posting, profile_id = run_posting
            profile_id = plan.profile_id or profile_id  # the profile the caller named, else the run's own
            request = AssessRequest(
                job=AssessJobInput(job_url=posting.url, title=posting.title or None, company=posting.company or None),
                resume=AssessResumeInput(profile_id=profile_id),
                origin=ORIGIN_JOB_PAGE,
            )
        else:
            if previous.job.source_url is None:
                raise QuickAssessError(
                    "reassess_unavailable",
                    "this job was assessed from pasted text, which is never stored; re-assess with --job-text again",
                )
            request = AssessRequest(
                job=AssessJobInput(job_url=previous.job.source_url, title=previous.job.title or None, company=previous.job.company or None),
                resume=AssessResumeInput(profile_id=plan.profile_id),
                # A profile with no stored assessment of its own (the address is another holder's): its first is the job page's.
                origin=None if plan.own else ORIGIN_JOB_PAGE,
            )
        response = run_quick_assessment(request, home_root=self._backend.home_root, target=target, trigger=trigger)
        return story_bank.attach_suggestions(response.to_json(), home_root=self._backend.home_root, target=target)

    def _handle_get_answers(self) -> None:
        target = self._answers_target()
        if target is None:
            return
        home_root = self._backend.home_root
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = sorted(set(query) - {"q", "tag"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}; allowed: q, tag")
            return
        try:
            body = answers_response(home_root, target, q=(query.get("q") or [None])[0], tag=(query.get("tag") or [None])[0])
        except (PrivateRecordError, story_bank.StoryBankError) as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, body)


__all__ = ["AnswersRoutesMixin", "find_run_posting"]
