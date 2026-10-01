"""0110-007: the routes an agent uses to find its way around -- ``GET /api``, ``GET /api/openapi.json``,
``GET /llms.txt`` -- and ``GET /api/jobs?url=``, one job with everything known about it.

The first three answer from ``openapi.py``'s route table. ``GET /api/jobs`` is read only: it calls
no model and no network, only the stores the UI's job page already reads, and it takes one
posting URL (raw or normalized; a pasted job's ``text:sha256:...`` identity works too). It joins:

* the newest run that acquired the posting (its row: posting with text, rank, work-mode fit,
  H-1B) and the ids of every run that did;
* every assessment of the job -- the run's own, the one carried forward, and each quick
  assessment (one per resume) -- with the requirement matrix and questions;
* ``open_questions``: the questions those assessments ask that no stored answer covers yet;
* the stored tailored resumes (ids and links, not their text);
* ``job_state`` with the events it accepts next, and the job's application events;
* ``links``: the calls that act on the job (assess, tailor, PDF, mark applied).

``404 not_found`` when no run, quick assessment, tailored resume or application event names the
job; ``422`` for a missing/blank/unparseable ``url`` or an unknown query key (naming the allowed one).
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from ....canonical import parse_json_bytes
from ... import story_bank
from ...experience_answers import read_answers
from ...question_ids import normalize_question_id
from ...quick_assess import QuickAssessError, list_quick_assessments
from ...tailored_resume import list_tailored_resumes
from ..contracts import AcquireOutput, FindJobsContractError
from ..job_state import JobStateSources, is_text_identity, normalize_job_identity
from . import openapi

JOB_RESPONSE_SCHEMA = "scout-job-response:1"
_logger = logging.getLogger("gigai.scout.server")


def _has_profiles(resolved) -> bool:
    from ... import profile_records

    try:
        return bool(profile_records.list_profiles(resolved))
    except Exception:  # noqa: BLE001 - unreadable profiles: treat the gig as profile-less
        return False


def _link(method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
    link: dict[str, object] = {"method": method, "path": path}
    if body is not None:
        link["body"] = body
    return link


def _run_assessment_entry(run_id: str, source: str, profile_id: str | None, assessment: dict[str, object]) -> dict[str, object]:
    body = {key: value for key, value in assessment.items() if key != "posting"}
    return {"source": source, "run_id": run_id, "profile_id": profile_id, **body}


def _quick_entry(item) -> dict[str, object]:
    return {
        "source": "quick",
        "run_id": None,
        "profile_id": item.resume.profile_id or "ephemeral",
        "created_at": item.created_at,
        "updated_at": item.updated_at or item.created_at,
        **item.result.to_json(),
    }


class AgentRoutesMixin:
    """``Handler`` mixin: ``GET /api``, ``GET /api/openapi.json``, ``GET /llms.txt``, ``GET /api/jobs``."""

    def _handle_get_api_index(self) -> None:
        self._write_json(HTTPStatus.OK, openapi.index_document())

    def _handle_get_openapi(self) -> None:
        from .server import _gigai_version

        self._write_json(HTTPStatus.OK, openapi.openapi_document(version=_gigai_version()))

    def _handle_get_llms(self) -> None:
        self._write_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", openapi.llms_text().encode("utf-8"))

    # ------------------------------------------------------------------ GET /api/jobs

    def _runs_with_posting(self, resolved, identity: str) -> list[tuple[str, str]]:
        """``(run_id, outcome)`` of every run whose sealed acquire output holds the posting, newest first."""

        from ....journal import JournalArtifactMissingError, read_committed_artifact
        from .runs_list import _run_ids_newest_first

        found: list[tuple[str, str]] = []
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
                if identity in (row.posting.normalized_url, row.posting.url):
                    found.append((run_id, row.outcome.value))
                    break
        return found

    def _handle_get_job(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = set(query) - {"url"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field(s): {sorted(unknown)}")
            return
        raw_url = (query.get("url") or [""])[-1].strip()
        if not raw_url:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be the posting URL (raw or normalized)")
            return
        try:
            identity = normalize_job_identity(raw_url)
        except FindJobsContractError:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be a posting URL")
            return
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        home_root = backend.home_root

        try:
            body = self._job_aggregate(identity, home_root=home_root, target=target)
        except QuickAssessError as exc:
            self._error(HTTPStatus.NOT_FOUND if exc.code == "target_unavailable" else HTTPStatus.CONFLICT, exc.code, str(exc))
            return
        if body is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no run, assessment or tailored resume names that job")
            return
        self._write_json(HTTPStatus.OK, body)

    def _job_aggregate(self, identity: str, *, home_root: Path, target: Path) -> dict[str, object] | None:
        from ....workpad import resolve_workpad

        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except Exception:  # noqa: BLE001 - an unbound folder simply has no runs; the stores below say the rest
            resolved = None

        run_hits = self._runs_with_posting(resolved, identity) if resolved is not None else []
        quick_items = [item for item in list_quick_assessments(home_root, target) if item.job.job_identity == identity]
        tailored_items = list(list_tailored_resumes(home_root, target, job_identity=identity))

        posting: dict[str, object] | None = None
        row: dict[str, object] = {}
        run_assessments: list[dict[str, object]] = []
        joins = None
        if run_hits:
            try:
                latest_run = run_hits[0][0]
                run_resolved, view, joins = self._run_view(latest_run)
                from .run_reads import posting_detail, posting_rows

                url = next(
                    (
                        entry.posting.normalized_url
                        for entry in view.rows
                        if identity in (entry.posting.normalized_url, entry.posting.url)
                    ),
                    identity,
                )
                run_profile_id = getattr(joins.profile, "profile_id", None)
                rows = posting_rows(view, url)
                if rows is not None:
                    self._join_row_fields(rows, resolved=run_resolved, view=view, joins=joins)
                    detail = posting_detail(latest_run, rows)
                    row = detail["row"]  # type: ignore[assignment]
                    posting = row.get("posting")  # type: ignore[assignment]
                    if detail["assessment"] is not None:
                        run_assessments.append(_run_assessment_entry(latest_run, "run", run_profile_id, detail["assessment"]))  # type: ignore[arg-type]
                    if detail["carried_forward"] is not None:
                        carried = detail["carried_forward"]
                        result = carried.get("result") if isinstance(carried, dict) else None  # type: ignore[union-attr]
                        if isinstance(result, dict):
                            run_assessments.append(_run_assessment_entry(latest_run, "carried_forward", run_profile_id, result))
            except Exception:  # noqa: BLE001 - a run that cannot be read must not hide the stores below
                _logger.exception("job %s: the newest run could not be read", identity)

        if posting is None and quick_items:
            job = quick_items[0].job.to_json()
            job.pop("text", None)
            job["text"] = quick_items[0].posting_text
            posting = job

        sources = JobStateSources(
            home_root=home_root, target=target, resolved=resolved, events=getattr(joins, "events", None)
        )
        events = [dict(item) for item in sources.events_for(identity)] if resolved is not None else []
        if posting is None and not run_hits and not quick_items and not tailored_items and not events:
            return None

        assessments = [*run_assessments, *(_quick_entry(item) for item in quick_items)]
        # 0110-034: an assessment's questions are answered by ITS profile's story bank
        # (own answers plus a shared profile's), never by another profile's. A gig with
        # no profile keeps the gig-wide answers; a pasted resume has none.
        gig_wide: dict[str, object] | None = None
        banks: dict[str, tuple[story_bank.BankEntry, ...]] = {}
        open_questions: list[dict[str, object]] = []
        answered: dict[str, dict[str, object]] = {}
        bank_suggestions: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in assessments:
            entry_profile = entry.get("profile_id")
            entries: tuple[story_bank.BankEntry, ...] = ()
            answers: dict[str, object] = {}
            if isinstance(entry_profile, str) and entry_profile != "ephemeral":
                if entry_profile not in banks:
                    try:
                        banks[entry_profile] = story_bank.read_bank(home_root=home_root, target=target, profile_id=entry_profile, with_postings=False)
                    except Exception:  # noqa: BLE001 - an unreadable bank answers nothing
                        banks[entry_profile] = ()
                entries = banks[entry_profile]
                answers = {item.question_id: item for item in entries}
            elif entry_profile is None:
                if gig_wide is None:
                    gig_wide = dict(read_answers(home_root=home_root, requested_target=target)) if resolved is None or not _has_profiles(resolved) else {}
                answers = gig_wide
            for question in entry.get("structured_questions") or []:  # type: ignore[union-attr]
                normalized = normalize_question_id(str(question["question_id"]))  # type: ignore[index]
                prior = answers.get(normalized)
                if prior is not None:
                    text = getattr(prior, "question", None) or getattr(prior, "prompt", "")
                    answered[normalized] = {"question_id": normalized, "prompt": text, "answer": prior.answer}  # type: ignore[attr-defined]
                elif normalized not in seen:
                    seen.add(normalized)
                    open_questions.append(dict(question))  # type: ignore[arg-type]
                    bank_suggestions.extend(story_bank.suggestions_for([question], entries))  # type: ignore[list-item]

        profile_id = getattr(getattr(joins, "profile", None), "profile_id", None)
        if profile_id is None and quick_items and quick_items[0].resume.profile_id:
            profile_id = quick_items[0].resume.profile_id
        state = row.get("job_state") if isinstance(row.get("job_state"), dict) else None
        if state is None:
            try:
                quick_for_profile = next((item for item in quick_items if item.resume.profile_id == profile_id), None)
                state = sources.state_for(identity, profile_id=profile_id, quick=quick_for_profile).to_json()
            except Exception:  # noqa: BLE001 - display-only: no state beats a failed read
                _logger.exception("job %s: state could not be derived", identity)
                state = None

        tailored = [
            {
                "profile_id": item.resume.profile_id or "ephemeral",
                "job_identity": item.job.job_identity,
                "company": item.job.company,
                "title": item.job.title,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "links": {
                    "pdf": _link("POST", "/api/tailored-resumes/pdf", {"profile_id": item.resume.profile_id or "ephemeral", "job_identity": item.job.job_identity}),
                    "line": _link("PUT", "/api/tailored-resumes/lines", {"profile_id": item.resume.profile_id or "ephemeral", "job_identity": item.job.job_identity, "updated_at": item.updated_at, "line_id": "<L id from the resume>", "use": "original"}),
                    "resume": _link("GET", f"/api/tailored-resumes?profile_id={quote(item.resume.profile_id or 'ephemeral', safe='')}&job_identity={quote(item.job.job_identity, safe='')}"),
                },
            }
            for item in tailored_items
        ]

        source_url = identity if not is_text_identity(identity) else None
        assess_job = {"job_url": source_url} if source_url else {"text": "<the pasted posting text>"}
        next_events = list(state["next_events"]) if isinstance(state, dict) else []  # type: ignore[index]
        links: dict[str, object] = {
            "self": _link("GET", f"/api/jobs?url={quote(identity, safe='')}"),
            "assess": _link("POST", "/api/assess", {"job": assess_job}),
            "tailor": _link("POST", "/api/tailored-resumes", {"job": assess_job}),
            "pdf": tailored[0]["links"]["pdf"] if tailored else None,  # type: ignore[index]
            "mark_applied": _link("POST", "/api/applications", {"normalized_url" if source_url else "job_identity": identity, "event_kind": "applied"})
            if "applied" in next_events
            else None,
            "run_posting": _link("GET", "/api/runs/" + run_hits[0][0] + "/posting?url=" + quote(identity, safe="")) if run_hits else None,
        }
        return {
            "schema_version": JOB_RESPONSE_SCHEMA,
            "job_identity": identity,
            "posting": posting,
            "runs": [{"run_id": run_id, "outcome": outcome} for run_id, outcome in run_hits],
            "rank": row.get("rank"),
            "rank_score": row.get("rank_score"),
            "work_mode_fit": row.get("work_mode_fit"),
            "h1b": row.get("h1b"),
            "assessments": assessments,
            "open_questions": open_questions,
            "bank_suggestions": bank_suggestions,
            "answers": [answered[key] for key in sorted(answered)],
            "tailored_resumes": tailored,
            "job_state": state,
            "application_events": events,
            "links": links,
        }


__all__ = ["JOB_RESPONSE_SCHEMA", "AgentRoutesMixin"]
