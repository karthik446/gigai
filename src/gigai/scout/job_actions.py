"""0.1.11 N5 (SPEC 2.1, 2.4, 4.4, 5.3): what the job commands and the job routes both call, so they cannot drift.

``gigai scout suggestions add | resolve | dismiss | add-line | list`` and ``GET`` / ``POST /api/jobs/suggestions``;
``gigai scout resume pick`` and ``POST /api/job-resumes/pick``; the part of ``gigai scout resume store`` that
follows the stored hand-back.  One function per action, each answering one JSON object.

SUGGESTIONS.  A job's suggestion record is written by its assessment (``scout.suggestions``).  Here an agent or the user ADDS one (``source`` is who wrote it, the ``--as`` value every
write carries), RESOLVES one (``done``, with how: ``job_resume_edit``, ``master_line`` or ``answer``, and what it
points at) or DISMISSES one.  Resolving is recorded, never a delete; a suggestion id is never used twice.  A line a
new suggestion names must be a line of the master, and a requirement one of the job's rows.  Every change is read
and written under the record's own lock.

THE PICK.  ``pick_view`` is what is STORED for one job, read and nothing else (SPEC 2.4: opening a job never
recomputes): the job resume, who picked it, the Picked / Left out counts, the gate, the stale list, the conflicts
and a waiting proposal.  ``pick_action`` is the five explicit steps:

==================  =============================================================================================
``refresh``         the code-only re-pick. Refused (``assessment_stale``) while the stored assessment is stale:
                    a new selection never sits beside scores made on other evidence; the step then is Re-assess
``draft``           the explicit draft for a job whose gate holds. Refused when the gate suggests a resume
``shorten``         RETIRED in 0.1.11.5 (a pick has no page budget to tighten): always refused, ``shorten_retired``, with
                    one plain sentence (the spacing slider, removing a point); nothing is read or written. Not refused
                    for an old assessment: it is the stored pick, cut further, not a new reading of the job
``use_proposed``    the waiting selection REPLACES the stored job resume: the one step that does
``dismiss_proposed``  the waiting selection is dropped; the stored job resume is not touched
==================  =============================================================================================

``refresh``, ``draft`` and ``shorten`` are ``scout.pick``'s (``pick.settle_stored`` / ``shorten_stored``, through ``job_resume_port``); a GigAI that
does not hold that action answers ``pick_not_available``.  No action here calls a model.  A step that is refused
says so for the USER: what happened and what to do, in plain words, never the name of a module or a function.

AFTER A HAND-BACK (``after_handback``): the final-selection check is made again on what the job resume now prints
(a reworded line counts for every master id it cites) and the suggestions the writer named are set to ``done``
with ``how: job_resume_edit``.  The names are checked BEFORE the resume is stored (``check_resolves``), so a
hand-back that names a suggestion the job does not have stores nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re

from . import job_brief, job_resume_port
from . import suggestions as store
from .job_resume_port import ACTION_DRAFT, ACTION_REFRESH, ACTION_SHORTEN, NotBuilt

SUGGESTIONS_SCHEMA = "scout-job-suggestions-response:1"
PICK_SCHEMA = "scout-job-resume-pick:1"

ACTION_USE_PROPOSED = "use_proposed"
ACTION_DISMISS_PROPOSED = "dismiss_proposed"
PICK_ACTIONS: tuple[str, ...] = (ACTION_REFRESH, ACTION_DRAFT, ACTION_SHORTEN, ACTION_USE_PROPOSED, ACTION_DISMISS_PROPOSED)

#: ``resolve``'s ``how`` (``dismiss`` is its own action).
RESOLVE_HOWS: tuple[str, ...] = ("job_resume_edit", "master_line", "answer")
HOW_JOB_RESUME_EDIT = "job_resume_edit"
HOW_DISMISSED = "dismissed"
STATUSES: tuple[str, ...] = ("open", "done", "dismissed")
#: What a suggestion may be about (``assess_contracts.SUGGESTION_KINDS``; read from there when this GigAI holds it).
SUGGESTION_KINDS: tuple[str, ...] = ("reword", "keyword", "order", "gap", "master_line")

_STALE_ASSESSMENT = "assessment_stale"
_GATE_SUGGEST = "suggest"
_SUGGESTION_ID = re.compile(r"\Asg-\d+\Z")


class JobActionError(ValueError):
    """A job action that is refused; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _refused(exc: Exception) -> JobActionError:
    return JobActionError(getattr(exc, "code", "invalid_value"), str(exc))


def _clock() -> str:
    from .tailored_resume import _now

    return _now()


def _actor(actor: object) -> str:
    from . import story_bank

    try:
        return story_bank.actor_value(actor)  # type: ignore[arg-type]
    except story_bank.StoryBankError as exc:
        raise _refused(exc) from exc


def _job(home_root: Path, target: Path, job_url: str, profile_id: str | None) -> job_brief.StoredJob:
    try:
        return job_brief.stored_job(home_root, target, job_url, profile_id)
    except job_brief.BriefError as exc:
        raise _refused(exc) from exc


def default_profile(home_root: Path, target: Path, job_url: str, profile_id: str | None = None) -> str | None:
    """The role RECORDED on what a job command writes when none is named (``resume store``).

    ``profile_id`` given: it, unchanged.  Otherwise the role on the job's one assessment (``job_brief.stored_job``).
    0.1.11.9: it selects no record (a job has one of each), so nothing is guessed and nothing is refused; until then
    two roles with a stored resume for the job refused (``profile_ambiguous``).
    A job with no stored assessment answers ``None`` (the caller's own default and refusals stand).
    """

    if profile_id:
        return profile_id
    try:
        return job_brief.stored_job(home_root, target, job_url, None).profile_id
    except job_brief.BriefError as exc:
        if exc.code == "assessment_missing":
            return None
        raise _refused(exc) from exc


def suggestion_ids(value: object) -> tuple[str, ...]:
    """``sg-1,sg-3`` (or a list of ids) as ids, in order, each once; ``invalid_value`` for anything that is not ``sg-<n>``."""

    if value is None or value == "" or value == []:
        return ()
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    ids = [str(part).strip() for part in parts if str(part).strip()]
    bad = [item for item in ids if not _SUGGESTION_ID.fullmatch(item)]
    if bad:
        raise JobActionError("invalid_value", f"a suggestion is named by its id (sg-<n>); not one: {bad[0]}")
    return tuple(dict.fromkeys(ids))


# --- suggestions ---------------------------------------------------------------------------------------------


def _counts(items: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {status: sum(1 for item in items if item.get("status") == status) for status in STATUSES}


def _suggestions_body(record: object, *, status: str | None = None) -> dict[str, object]:
    stored = record.to_json()  # type: ignore[attr-defined]
    items: list[Mapping[str, object]] = list(stored["suggestions"])
    return {
        "schema_version": SUGGESTIONS_SCHEMA,
        "job_identity": stored["job_identity"],
        "profile_id": stored["profile_id"],
        "updated_at": stored["updated_at"],
        "gate": stored["gate"],
        "counts": _counts(items),
        "suggestions": [item for item in items if status is None or item.get("status") == status],
    }


def _record_path(home_root: Path, target: Path, job: job_brief.StoredJob) -> Path:
    return store.suggestions_path(Path(home_root), Path(target), job.profile_id, job.job_identity)


def _missing(job: job_brief.StoredJob) -> JobActionError:
    return JobActionError(
        "suggestions_not_found",
        "this job has no suggestion record yet: it is written when the job is assessed (`gigai scout jobs assess URL --again`, or `gigai scout assess --job-url URL` for a job assessed by its URL; one model call, on the user's yes)",
    )


#: What the OPEN read adds to the suggestions (``GET /api/jobs/suggestions``, the page): ``pick_view``'s stored view.
#: ``gate`` and ``requirements`` are the view's, checked against the stored resume as it is (COVER-01), over the record's.
OPEN_KEYS: tuple[str, ...] = ("verdict", "basis", "master_stored", "master_education", "gate", "stale", "stale_lines", "picked", "problems", "added_by_code", "conflicts", "selection_error", "proposed", "selected_lines", "requirements", "fact_checks", "fact_checks_posting")


def list_suggestions(
    home_root: Path, target: Path, job_url: str, *, profile_id: str | None = None, status: str | None = None, with_view: bool = False,
) -> dict[str, object]:
    """The suggestions of one job as they are stored; ``status`` keeps one of open, done, dismissed. Read only.

    ``with_view``: also the job's stored view for the page (SPEC 2.4, opening a job): the derived ``stale`` list (and
    ``stale_lines``: the printed lines the master retired or reworded), the
    selection (``picked``: who, pages, max pages), its ``conflicts``, the ``proposed`` selection (the line ids of each side
    for Compare) and the requirement ``requirements`` rows. Nothing is recomputed and nothing is written.
    """

    if status is not None and status not in STATUSES:
        raise JobActionError("invalid_value", "status must be one of: " + ", ".join(STATUSES))
    job = _job(home_root, target, job_url, profile_id)
    record = store.read_record(_record_path(home_root, target, job))
    if record is None:
        raise _missing(job)
    body = _with_drafts(_suggestions_body(record, status=status), home_root, target, job)
    if with_view:
        view = pick_view(home_root, target, job_url, profile_id=job.profile_id)
        body.update({key: view[key] for key in OPEN_KEYS})
    return body


def _change(home_root: Path, target: Path, job_url: str, profile_id: str | None, change) -> dict[str, object]:  # noqa: ANN001 - record -> record
    """Read the job's record, apply ``change`` and write it, under the record's lock; the suggestions after it."""

    job = _job(home_root, target, job_url, profile_id)
    path = store.suggestions_write_path(Path(home_root), Path(target), job.profile_id, job.job_identity)  # 0.1.11.9: the job's own record
    try:
        with store.record_write_lock(path):
            record = store.read_record(path)
            if record is None:
                raise _missing(job)
            record = change(record)
            store.save_record(record)
    except store.SuggestionError as exc:
        raise _refused(exc) from exc
    return _with_drafts(_suggestions_body(record), home_root, target, job)


def add_suggestion(
    home_root: Path, target: Path, job_url: str, *, kind: str, why: str, actor: str = "operator", profile_id: str | None = None,
    line: str | None = None, requirement: str | None = None, posting_phrase: str | None = None, now: str | None = None,
) -> dict[str, object]:
    """One more open suggestion on the job, written by ``actor`` (operator or agent). The answer names it: ``added``."""

    source = _actor(actor)
    if not isinstance(why, str) or not why.strip():
        raise JobActionError("invalid_value", "why says what would help, in one or two sentences")
    if line is None and requirement is None:
        raise JobActionError("invalid_value", "a suggestion names a master line (line), a requirement row (requirement), or both")
    if line is not None:
        from .tailor_master import stored_master

        stored = stored_master(Path(home_root), Path(target))
        if stored is not None and line not in stored.master.items:  # type: ignore[attr-defined]
            raise JobActionError("unknown_line", f"{line} is not a line of your master resume (`gigai scout resume master show --json` lists them)")
    added: list[str] = []

    def change(record):  # noqa: ANN001, ANN202
        if requirement is not None and requirement not in {row.id for row in record.requirements}:
            raise JobActionError("unknown_requirement", f"{requirement} is not a requirement row of this job")
        after = store.add_suggestion(
            record, kind=kind, why=why, source=source, now=now or _clock(), line=line, requirement=requirement, posting_phrase=posting_phrase or None,
        )
        added.append(after.suggestions[-1].id)
        return after

    body = _change(home_root, target, job_url, profile_id, change)
    return {**body, "added": added[0]}


def resolve_suggestion(
    home_root: Path, target: Path, job_url: str, suggestion_id: str, *, how: str, actor: str = "operator", profile_id: str | None = None,
    ref: str | None = None, now: str | None = None,
) -> dict[str, object]:
    """Close one suggestion as ``done``: ``how`` is :data:`RESOLVE_HOWS`, ``ref`` the master id or the question id it points at."""

    by = _actor(actor)
    if how not in RESOLVE_HOWS:
        raise JobActionError("invalid_value", "how must be one of: " + ", ".join(RESOLVE_HOWS) + " (to dismiss one, use dismiss)")
    (name,) = suggestion_ids([suggestion_id]) or ("",)
    body = _change(home_root, target, job_url, profile_id, lambda record: store.resolve_suggestion(record, name, by=by, how=how, now=now or _clock(), ref=ref or None))
    return {**body, "resolved": name}


def dismiss_suggestion(
    home_root: Path, target: Path, job_url: str, suggestion_id: str, *, actor: str = "operator", profile_id: str | None = None, now: str | None = None,
) -> dict[str, object]:
    """Close one suggestion as ``dismissed``. It stays in the record."""

    by = _actor(actor)
    (name,) = suggestion_ids([suggestion_id]) or ("",)
    body = _change(home_root, target, job_url, profile_id, lambda record: store.resolve_suggestion(record, name, by=by, how=HOW_DISMISSED, now=now or _clock()))
    return {**body, "dismissed": name}


# --- 0.3.2 gyl: the drafted line of a must-have the user said yes to --------------------------------------------

#: The trigger of the history entry ``add_line`` writes on the stored assessment: ``master_line:<line id>``.
TRIGGER_MASTER_LINE = "master_line"


def _matrix_rows(job: job_brief.StoredJob) -> dict[str, object]:
    """The stored assessment's matrix rows that carry an id, by id."""

    matrix = getattr(getattr(job.assessment, "result", None), "matrix", None) or ()
    return {row.id: row for row in matrix if getattr(row, "id", None)}


def _answered_rows(job: job_brief.StoredJob) -> dict[str, object]:
    """The stored assessment's ``answered`` rows (the user said yes; no line of the master shows it), by row id."""

    return {row_id: row for row_id, row in _matrix_rows(job).items() if job_brief._word(getattr(row, "status", None)) == "answered"}  # noqa: SLF001 - the brief's own reading of an enum


def _row_draft(home_root: Path, target: Path, row: object, master: object | None):  # noqa: ANN202 - drafted_line.Draft | None
    """The line drafted from the answer or story ``row`` rests on (``drafted_line``), or ``None``: it is no longer there."""

    from . import drafted_line, stories, story_bank

    question_id, story_id = drafted_line.answer_ref(tuple(getattr(row, "sources", ()) or ()))
    said: str | None = None
    try:
        if question_id is not None:
            entry = story_bank.get_answer(home_root=home_root, target=target, question_id=question_id, with_jobs=False)
            said = None if entry is None else entry.answer
        elif story_id is not None:
            story = stories.get_story(home_root=home_root, target=target, story_id=story_id)
            said = None if story is None else story.text()
    except Exception:  # noqa: BLE001 - a read never fails on a story bank it cannot read: no line is drafted then
        said = None
    return drafted_line.draft_for(row, master, said, question_id=question_id, story_id=story_id)


def _with_drafts(body: dict[str, object], home_root: Path, target: Path, job: job_brief.StoredJob) -> dict[str, object]:
    """``body`` with ``draft`` ({text, entry_id, backed}) on each open ``master_line`` suggestion of an ``answered`` row.

    Derived on this read from the answer or story and the stored master, and stored nowhere (a suggestion record holds
    ids only). No draft (no answer to draft from, no master) leaves the suggestion as it is stored.
    """

    rows = _answered_rows(job)
    wanted = {
        str(item["requirement"]) for item in body["suggestions"]  # type: ignore[union-attr]
        if item.get("kind") == "master_line" and item.get("status") == "open" and item.get("requirement") in rows
    }
    if not wanted:
        return body
    from .tailor_master import stored_master

    stored = stored_master(Path(home_root), Path(target))
    master = None if stored is None else stored.master
    drafts = {row_id: _row_draft(Path(home_root), Path(target), rows[row_id], master) for row_id in wanted}
    body["suggestions"] = [
        {**item, "draft": drafts[item["requirement"]].to_json()} if item.get("requirement") in wanted and item.get("status") == "open"
        and item.get("kind") == "master_line" and drafts[item["requirement"]] is not None else item
        for item in body["suggestions"]  # type: ignore[union-attr]
    ]
    return body


def _rechecked_row(row: object, line_id: str, stored: object):  # noqa: ANN202 - RequirementMatrixRow
    """``row`` read again by the strict check with the new line among its sources: ``met`` when the line shows it, else as it was."""

    from .assess_master import strict_master
    from .assess_strict import strict_rows
    from .find_jobs.assess_contracts import RequirementMatrixRow

    candidate = {**row.to_json(), "status": "met", "sources": [line_id, *row.sources]}  # type: ignore[attr-defined]
    candidate.pop("code_note", None)
    strict_rows([candidate], strict_master(stored.master.markdown(ids=True)))  # type: ignore[attr-defined]
    return RequirementMatrixRow.from_json(candidate) if candidate["status"] == "met" else row


def _added_before(master: object, backed: str, entry_id: str | None) -> tuple[str, ...]:
    """The ids of the lines of ``master`` under ``entry_id`` backed by ``backed``, whatever their words: among them what an
    earlier ``add_line`` of the same suggestion wrote before it stopped (the master was written, a later step was not)."""

    items = getattr(master, "items", {}).values()
    return tuple(item.id for item in items if backed in item.backed and item.entry_id == entry_id)


def add_line(
    home_root: Path, target: Path, job_url: str, suggestion_id: str, *, text: str | None = None, ref: str | None = None,
    actor: str = "operator", profile_id: str | None = None, now: str | None = None,
) -> dict[str, object]:
    """0.3.2 gyl: add the drafted line of an open ``master_line`` suggestion to the master, then settle the job on it. No model call.

    ``text``: the user's own words for the line (the draft when not given). The line is written by ``master_edit.add_line``
    under the newest role, backed by the answer or story (``master_edit.evidence_for``): the revision and backing
    ``gigai scout resume master add`` makes. ``ref``: a line the master HAS (``LINE``): nothing is written, the job
    settles on that line when the strict check finds the requirement on it (refused, nothing changed, when it does not).
    Then, in code: the row is read again by the strict check against the new
    master (``met`` when the line shows it), the gate is decided again, the stored assessment is written back with a
    history entry ``master_line:<line id>``, the suggestion is resolved (``how: master_line``, ``ref``: the line id) and
    the resume is picked again (``refresh``). A pick that is refused (the gate holds on another row, an old assessment)
    is said in ``repick``; the line and the assessment stay written.

    Failure semantics: the steps run master, assessment, suggestion, pick, and no step undoes an earlier one. Each is
    safe to run again, so the same call run again finishes what a failed one left: a line under the same role backed by
    the same answer or story that shows the requirement to the strict read, whatever its words (and a ``near_duplicate``
    refusal naming such a line), is the line an earlier call wrote (no second line), an assessment whose history has
    ``master_line:<line id>`` is not written again, and the resolve and the pick repeat as they do on their own. A line of
    the same backing that does not show it is not taken for one. A suggestion that is done says so, with its line.

    Raises ``JobActionError``: ``suggestion_not_found``, ``invalid_value`` (not an open master_line of an answered row,
    done already, a ``ref`` the master has not or that does not show the requirement), ``no_master``,
    ``answer_not_found``, ``master_place_invalid`` (the master has no role to put the line under), ``near_duplicate``
    (nothing written), and ``master_edit``'s own codes.
    """

    from dataclasses import replace
    import json

    from . import master_edit, quick_assess
    from .find_jobs.assess_contracts import VerdictHistoryEntry
    from .find_jobs.discovery.storage import atomic_write
    from .requirement_weights import settled_verdict
    from .resume_gate import gate
    from .tailor_master import stored_master

    by = _actor(actor)
    (name,) = suggestion_ids([suggestion_id]) or ("",)
    home_root, target = Path(home_root), Path(target)
    job = _job(home_root, target, job_url, profile_id)
    record = store.read_record(_record_path(home_root, target, job))
    if record is None:
        raise _missing(job)
    item = next((suggestion for suggestion in record.suggestions if suggestion.id == name), None)
    if item is None:
        raise JobActionError("suggestion_not_found", f"this job has no suggestion {name} (`gigai scout suggestions list --job-url URL` lists them)")
    if item.kind == "master_line" and item.status == "done":
        done = getattr(item.resolved, "ref", None)
        raise JobActionError("invalid_value", f"{name} is already done" + (f" with line {done} of your master" if done else "") + "; nothing was changed")
    row = _matrix_rows(job).get(item.requirement or "") if item.kind == "master_line" and item.status == "open" else None
    state = None if row is None else job_brief._word(getattr(row, "status", None))  # noqa: SLF001 - the brief's own reading of an enum
    # ``met``: only when an earlier add_line of this suggestion settled the row and stopped before the resolve (below).
    if row is None or state not in ("answered", "met"):
        raise JobActionError("invalid_value", f"{name} has no drafted line: a line is added for an open master_line suggestion on a must-have you said yes to")
    stored = stored_master(home_root, target)
    if stored is None:
        raise JobActionError("no_master", "there is no master resume to add the line to: build it first (`gigai scout resume master init`)")
    chosen = ref.strip() if isinstance(ref, str) and ref.strip() else None
    if chosen is not None and chosen not in stored.master.items:
        raise JobActionError("invalid_value", f"your master has no line {chosen}: name a line it has (`gigai scout resume master show` lists them)")
    draft = _row_draft(home_root, target, row, stored.master)
    if draft is None and state == "answered" and chosen is None:
        raise JobActionError("answer_not_found", "the answer or story this requirement rests on is no longer in your story bank, so no line is drafted from it")
    line_text = (text.strip() if isinstance(text, str) and text.strip() else draft.text) if draft is not None else ""
    # What an earlier call of this suggestion wrote: a line under the draft's role with its backing that settled the row
    # (``met``: among the row's sources) or shows the requirement to the strict read. A line of the same backing that does
    # not (one the user added by hand, "Mentored four engineers") is not it: the draft is written beside it.
    earlier = chosen
    if chosen is None and draft is not None:
        candidates = _added_before(stored.master, draft.backed, draft.entry_id)
        earlier = next((
            line for line in candidates
            if (line in row.sources if state == "met" else _rechecked_row(row, line, stored) is not row)  # type: ignore[union-attr]
        ), None)
    if state == "met" and (earlier is None or earlier not in row.sources):  # type: ignore[union-attr]
        raise JobActionError("invalid_value", f"{name} has no drafted line: its requirement is met by a line of your master already")
    if chosen is not None and state == "answered" and _rechecked_row(row, chosen, stored) is row:
        raise JobActionError(
            "invalid_value",
            f"line {chosen} does not show “{row.requirement}” to the strict read (it needs the requirement's own words), so nothing "  # type: ignore[union-attr]
            "was changed: name another line, or add one (without --ref)",
        )
    if earlier is not None:
        line_id, written, warnings = earlier, stored, ()
    else:
        assert draft is not None
        if draft.entry_id is None:
            source = f"--from-answer {draft.question_id}" if draft.question_id is not None else f"--from-story {draft.story_id}"
            raise JobActionError(
                "master_place_invalid",
                "your master has no role to put the line under. Add it where it goes yourself: `gigai scout resume master add --from-answer "
                f"...` (here: `gigai scout resume master add --text \"{line_text}\" --section other {source}`, or --entry ENTRY_ID once a role is "
                f"there), then settle this suggestion on it: `gigai scout suggestions add-line {name} --job-url URL --ref LINE`",
            )
        try:
            evidence = master_edit.evidence_for(home_root=home_root, target=target, question_id=draft.question_id, story_id=draft.story_id)
            write = master_edit.add_line(home_root=home_root, target=target, text=line_text, entry_id=draft.entry_id, evidence=evidence, actor=by)
        except master_edit.MasterStoreError as exc:
            raise _refused(exc) from exc
        refused = write.status == "near_duplicate" or not write.ids
        near_ids = [str(line.id) for line in write.near_duplicates if getattr(line, "id", None)] if refused else []
        # A line like this one that carries this suggestion's own backing and shows the requirement: what an earlier call
        # wrote, in other words.
        own = next((
            line for line in near_ids
            if draft.backed in getattr(stored.master.items.get(line), "backed", ()) and _rechecked_row(row, line, stored) is not row
        ), None)
        if own is not None:
            line_id, written, warnings = own, stored, ()
        elif refused:
            raise JobActionError(
                "near_duplicate",
                "your master has a line like this one already" + (f" ({', '.join(near_ids)})" if near_ids else "") + ", so nothing was written. "
                f"Change the line's words, or settle the suggestion on that line: `gigai scout suggestions add-line {name} --job-url URL --ref LINE`",
            )
        else:
            line_id, written, warnings = write.ids[0], write.stored, tuple(write.warnings)
    when = now or _clock()
    trigger = f"{TRIGGER_MASTER_LINE}:{line_id}"
    # The stored assessment, read again, settled on the new line in code: the strict check, the gate, the history.
    assessment = quick_assess.read_quick_assessment(home_root, target, job.profile_id, job.job_identity) or job.assessment
    body = assessment.result  # type: ignore[attr-defined]
    matrix = body.matrix  # type: ignore[attr-defined]
    if not any(entry.trigger == trigger for entry in assessment.history):  # type: ignore[attr-defined]
        matrix = tuple(_rechecked_row(each, line_id, written) if getattr(each, "id", None) == row.id else each for each in matrix)  # type: ignore[union-attr]
        verdict = settled_verdict(body.verdict, matrix, body.structured_questions)  # type: ignore[attr-defined]
        decided = gate(matrix, body.structured_questions, verdict)  # type: ignore[attr-defined]
        settled = replace(
            assessment, result=replace(body, matrix=matrix, verdict=verdict), resume_gate=decided.record(), updated_at=when,
            history=quick_assess._history_with(assessment, VerdictHistoryEntry(at=when, verdict=verdict, trigger=trigger)),  # type: ignore[arg-type]  # noqa: SLF001 - the history's one append rule
        )
        path = quick_assess.quick_assess_write_path(home_root, target, job.profile_id, job.job_identity)
        atomic_write(path, json.dumps(settled.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    resolve_suggestion(home_root, target, job_url, name, how="master_line", actor=by, profile_id=job.profile_id, ref=line_id, now=when)
    repick: dict[str, object] | None = None
    try:
        pick_action(home_root, target, job_url, ACTION_REFRESH, profile_id=job.profile_id, now=when)
    except (JobActionError, NotBuilt) as exc:
        repick = {"code": getattr(exc, "code", "pick_not_available"), "message": str(exc)}
    status = next((job_brief._word(each.status) for each in matrix if getattr(each, "id", None) == row.id), None)  # noqa: SLF001 - the brief's own reading of an enum
    shown = written.master.items.get(line_id)  # type: ignore[attr-defined]
    return {
        **list_suggestions(home_root, target, job_url, profile_id=job.profile_id),
        "resolved": name, "line": {"id": line_id, "entry_id": getattr(shown, "entry_id", None), "backed": draft.backed if draft is not None else None},
        "row": {"id": row.id, "status": status}, "repick": repick, "warnings": list(warnings),
    }


# --- the hand-back's follow-up (SPEC 5.3 "What is stored") ------------------------------------------------------


def check_resolves(home_root: Path, target: Path, job_url: str, profile_id: str | None, ids: Sequence[str]) -> None:
    """Refuse (``suggestion_not_found``) BEFORE a hand-back is stored when it names a suggestion the job does not have."""

    if not ids:
        return
    listed = {str(item["id"]) for item in list_suggestions(home_root, target, job_url, profile_id=profile_id)["suggestions"]}  # type: ignore[union-attr, index]
    unknown = [item for item in ids if item not in listed]
    if unknown:
        raise JobActionError("suggestion_not_found", f"this job has no suggestion {unknown[0]} (`gigai scout suggestions list --job-url URL` lists them)")


def after_handback(
    home_root: Path, target: Path, response: object, *, resolves: Sequence[str] = (), actor: str = "operator", now: str | None = None,
) -> dict[str, object] | None:
    """What follows a STORED hand-back, with no model call: the final-selection check on what the resume now prints, and
    the named suggestions set to ``done`` (``how: job_resume_edit``).

    ``response``: the stored job resume (``TailorResponse``).  Returns ``{gate, resolved, suggestions}``, or ``None``
    when the job has no suggestion record (it was assessed before 0.1.11): the hand-back is stored either way.
    """

    by = _actor(actor)
    when = now or _clock()
    profile_id, identity = response.resume.profile_id, response.job.job_identity  # type: ignore[attr-defined]
    path = store.suggestions_write_path(Path(home_root), Path(target), profile_id, identity)
    try:
        with store.record_write_lock(path):
            record = store.read_record(path)
            if record is None:
                return None
            rows = [store.RequirementRow(row.id, row.requirement_class, row.status, row.sources) for row in record.requirements]
            # Recomputed from the STORED resume (SPEC 10.2 item 5): a recorded conflict the resume no longer has is gone.
            # COVER-01: a row that cites a role, a project or a school is kept while the resume shows that entry.
            printed = store.printed_ids(response.result)  # type: ignore[attr-defined]
            selection, conflicts = store.live_selection(record.selection, printed)
            check = store.check_selection(rows, printed, entries=store.shown_entry_ids(response.result), conflicts=conflicts)  # type: ignore[attr-defined]
            record = store.with_selection(
                record, now=when, check=check, selection=selection, proposed=record.proposed, selection_error=record.selection_error,
            )
            for name in resolves:
                record = store.resolve_suggestion(record, name, by=by, how=HOW_JOB_RESUME_EDIT, now=when)
            store.save_record(record)
    except store.SuggestionError as exc:
        raise _refused(exc) from exc
    return {"gate": dict(record.gate), "resolved": list(resolves), "counts": _counts([item.to_json() for item in record.suggestions])}


# --- the pick -------------------------------------------------------------------------------------------------------


def _resume_view(home_root: Path, resume: object | None, replaceable: bool) -> dict[str, object] | None:
    """The stored job resume as ``resume pick`` shows it: its markdown, who made it, and where its file and the job's folder are."""

    from . import jobs_folder

    if resume is None:
        return None
    selection = getattr(resume, "selection", None)
    edited = getattr(resume, "edited", None)
    folder = jobs_folder.stored_job_folder(Path(home_root), resume.stored_path)  # type: ignore[attr-defined]
    return {
        "updated_at": resume.updated_at,  # type: ignore[attr-defined]
        "made_by": resume.producer.callable,  # type: ignore[attr-defined]
        "edited": None if edited is None else {"written_by": edited.written_by, "edited_at": edited.edited_at, "source": edited.source},
        # Whether a re-pick or a re-assessment may replace it; false: it is the user's, and a new selection waits as proposed.
        "replaceable": replaceable,
        "lines": resume.result.line_count(),  # type: ignore[attr-defined]
        "counts": None if selection is None else {
            "picked": len(selection.picked), "left_out": len(selection.left_out), "cut_for_length": len(selection.cut_for_length),
        },
        # 0.1.11.4 J1: <jobs>/<company>/<role>/resume.md, and the job's folder itself (paths only, for "Open folder").
        "folder_path": None if folder is None else folder.resume_shown,
        "job_folder": None if folder is None else folder.shown,
        "markdown": resume.markdown,  # type: ignore[attr-defined]
    }


def _basis(home_root: Path, target: Path, check: object, profile_id: str, resolved: object | None) -> tuple[str | None, bool, bool | None, str | None]:
    """``(basis, master_stored, master_education, master_text)`` for the job's profile as it is now.

    ``basis``: ``master`` | ``profile_resume`` (the brief's own rule, ``tailor_master.tailoring_basis``), or ``None``:
    no gig, or the profile is gone.  Only ``master`` is picked from; ``profile_resume`` with a master stored is a
    profile whose resume was put there by hand.  ``check``: the view's ``BasisCheck``, which has read the profiles
    for the stale check already (nothing is read twice).  ``master_education`` (0.1.11.4 item 9d): the stored master
    has at least one Education entry; ``None`` with no master.  ``master_text`` (0.1.11.12): the stored master's
    text, ids off, what the fact guard reads; ``None`` with no master.  Read from the master this function loads anyway.
    """

    from .tailor_master import stored_master, tailoring_basis

    if resolved is None:
        return None, False, None, None
    stored = stored_master(home_root, target, resolved=resolved)
    has_master = stored is not None
    education = None if stored is None else any(entry.section == "education" for entry in stored.master.entries.values())
    master_text = None if stored is None else stored.master.markdown(ids=False)
    try:
        profile = check._profile(profile_id)  # type: ignore[attr-defined]  # noqa: SLF001 - the profiles this request already read
    except Exception:  # noqa: BLE001 - a view never fails on what it cannot read: the basis is then unknown
        return None, has_master, education, master_text
    return (None if profile is None else tailoring_basis(home_root, profile, master_stored=has_master)), has_master, education, master_text


def _fact_checks(resume: object | None, master_text: str | None, assessment: object) -> tuple[list[str] | None, bool]:
    """0.1.11.12 (dossier-B): the words and numbers the stored resume states that neither the master nor the posting
    states (``tailor_fact_guard``), computed on this read and stored nowhere, and whether the posting's text was there
    to check against.  ``None``: nothing to check against (no master) or no resume; ``[]``: checked, nothing to read.
    A pasted job keeps no posting text (``AssessResponse.posting_text`` is never stored): the master alone is the source
    then, and the second value is False so the CLI and the page say so instead of "or the posting"."""

    from .tailor_fact_guard import unverified_facts

    posting_text = getattr(assessment, "posting_text", None)
    with_posting = isinstance(posting_text, str) and bool(posting_text.strip())
    markdown = getattr(resume, "markdown", None)
    if master_text is None or not isinstance(markdown, str):
        return None, with_posting
    return list(unverified_facts(markdown, master_text, posting_text if with_posting else None)), with_posting


def pick_view(home_root: Path, target: Path, job_url: str, *, profile_id: str | None = None) -> dict[str, object]:
    """What is stored for one job's resume (the module text). Reads only: nothing is written.

    The requirement rows and the gate are the record's checked against the stored resume as it is now
    (``suggestions.recheck_stored``, COVER-01): a record 0.1.11.12 wrote read a role, a project or a school a row
    cites as a lost line, and only a new write would mend it. Everything else is as stored.
    """

    from ..workpad import WorkpadError, resolve_workpad
    from .assessment_basis import BasisCheck
    from .tailored_resume import read_tailored_resume, tailored_resume_path

    home_root, target = Path(home_root), Path(target)
    job = _job(home_root, target, job_url, profile_id)
    resume_path = tailored_resume_path(home_root, target, job.profile_id, job.job_identity)
    resume = read_tailored_resume(resume_path)
    record = job.record or {}
    selection = record.get("selection") if isinstance(record.get("selection"), Mapping) else None
    proposed = record.get("proposed") if isinstance(record.get("proposed"), Mapping) else None
    try:  # the gig's workpad once: the stale check and the kept master both read through it
        resolved: object | None = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except WorkpadError:
        resolved = None
    check = BasisCheck(home_root=home_root, target=target, resolved=resolved)
    reason = check.reason(job.assessment)  # type: ignore[arg-type]
    # (the stored resume read above: the stale list is about the lines it STILL prints, and no file is read twice)
    codes, changed = store.stale_view(home_root, target, job.profile_id, job.job_identity, assessment_stale=reason, resolved=resolved, resume=resume)
    stale = list(codes)
    replaceable = bool(store.is_replaceable(resume, store.read_suggestions(home_root, target, job.profile_id, job.job_identity), path=resume_path))
    requirements, gate = store.recheck_stored(record, resume.result if resume is not None else None)
    if gate is None and getattr(job.assessment, "resume_gate", None) is not None:
        stored_gate = job.assessment.resume_gate  # type: ignore[attr-defined]
        gate = {"decision": stored_gate.decision, "ready": None, "reasons": [reason.to_json() for reason in stored_gate.reasons]}
    questions = job_brief.open_questions(home_root, target, job.assessment)
    asked = {str(item["row"]): str(item["question_id"]) for item in questions if item["row"]}
    basis, master_stored, master_education, master_text = _basis(home_root, target, check, job.profile_id, resolved)
    fact_checks, fact_checks_posting = _fact_checks(resume, master_text, job.assessment)
    return {
        "schema_version": PICK_SCHEMA,
        "job_identity": job.job_identity,
        "profile_id": job.profile_id,
        # What a resume for this job is made from NOW (``tailor_master.tailoring_basis``): only ``master`` is picked from.
        "basis": basis,
        "master_stored": master_stored,
        # 0.1.11.4 item 9d: the master holds an Education entry (null: no master). The page says "no education" from it.
        "master_education": master_education,
        # 0.1.11.12 (dossier-B): checks for the user to read, not errors: each a word or number of the stored resume that
        # neither the master nor the posting states (null: no master to check against). Nothing is rewritten or blocked.
        # `fact_checks_posting`: the posting's text was there (false for a pasted job: the master alone was the source).
        "fact_checks": fact_checks,
        "fact_checks_posting": fact_checks_posting,
        "verdict": job_brief._word(getattr(job.assessment.result, "verdict", None)),  # type: ignore[attr-defined]  # noqa: SLF001 - the brief's own reading of an enum
        "gate": None if gate is None else dict(gate),
        "stale": stale,
        # Behind ``picked_line_changed``: each printed line the master retired or reworded since the pick (ids only, no text).
        "stale_lines": [dict(item) for item in changed],
        "resume": _resume_view(home_root, resume, replaceable),
        # 0.1.11.4 E1: a file IS stored for this job and cannot be read. It is the user's: no pick writes over it.
        "resume_unreadable": resume is None and store.stored_unreadable(resume_path),
        "picked": None if selection is None else {
            # (``pages``: null for a pick of 0.1.11.5, which counts no page; ``max_bullets``: the most bullets it holds, null before.)
            key: selection.get(key)
            for key in ("picked_by", "fallback", "draft", "made_at", "pages", "max_pages", "max_bullets", "pick_rules_version", "selector_version")
        },
        "problems": list(selection.get("problems", ())) if selection is not None else [],  # type: ignore[arg-type]
        "added_by_code": list(selection.get("added_by_code", ())) if selection is not None else [],  # type: ignore[arg-type]
        "conflicts": list(selection.get("conflicts", ())) if selection is not None else [],  # type: ignore[arg-type]
        "selection_error": record.get("selection_error"),
        # What the page's Compare needs: the master line ids each side prints (names are the master's, by id).
        "proposed": None if proposed is None else {
            **{key: proposed.get(key) for key in ("picked_by", "fallback", "draft", "made_at", "pages", "max_pages", "max_bullets", "conflicts")},
            "lines": list(store.recorded_marks(proposed)),
        },
        "selected_lines": list(store.recorded_marks(selection)),
        # The requirement rows (id, class, status, sources, in_resume, coverage), checked against the stored resume (above). A row an
        # open question asks also names it (`question_id`, what `gigai scout answers save` takes); `open_questions` lists them all.
        "requirements": [{**row, "question_id": asked.get(str(row.get("id")))} for row in requirements],
        "open_questions": list(questions),
    }


def pick_action(home_root: Path, target: Path, job_url: str, action: str, *, profile_id: str | None = None, now: str | None = None) -> dict[str, object]:
    """One explicit step on a job's resume (the module text's table), then the view after it. No model call.

    Raises ``JobActionError`` (``invalid_value``, ``assessment_stale``, ``draft_not_needed``, ``no_proposed_resume``,
    ``proposal_stale``, ``stored_resume_unreadable`` ...)
    or ``NotBuilt`` (``pick_not_available``).
    """

    if action not in PICK_ACTIONS:
        raise JobActionError("invalid_value", "action must be one of: " + ", ".join(PICK_ACTIONS))
    if action == ACTION_SHORTEN:
        # Retired in 0.1.11.5: said before anything is read. (The sentence is ``scout.pick``'s when it is built.)
        try:
            job_resume_port.shorten_stored()(home_root, target, profile_id, job_url, now=now or "")
        except NotBuilt:
            raise
        except RuntimeError as exc:
            raise _refused(exc) from exc
    home_root, target = Path(home_root), Path(target)
    before = pick_view(home_root, target, job_url, profile_id=profile_id)
    pair = (str(before["profile_id"]), str(before["job_identity"]))
    when = now or _clock()
    extra: dict[str, object] = {}
    if before["resume_unreadable"] and action != ACTION_DISMISS_PROPOSED:
        # Said before anything is computed: the step would make a resume that could only wait beside a file nobody can read.
        raise JobActionError("stored_resume_unreadable", store.STORED_UNREADABLE)
    if action in (ACTION_USE_PROPOSED, ACTION_DISMISS_PROPOSED):
        try:
            if action == ACTION_USE_PROPOSED:
                store.use_proposed(home_root, target, *pair, now=when)
            elif before["proposed"] is None:
                raise JobActionError("no_proposed_resume", "no new suggested resume is waiting for this job")
            else:
                store.dismiss_proposed(home_root, target, *pair, now=when)
        except store.SuggestionError as exc:
            raise _refused(exc) from exc
    else:
        stale = [code for code in before["stale"] if str(code).startswith(_STALE_ASSESSMENT)]  # type: ignore[union-attr]
        decision = (before["gate"] or {}).get("decision") if isinstance(before["gate"], Mapping) else None
        if action == ACTION_REFRESH and stale:
            raise JobActionError(
                "assessment_stale",
                f"this job's assessment is old ({stale[0]}): a new pick would sit beside scores made on other evidence. Re-assess it instead "
                "(`gigai scout jobs assess URL --again`, or `gigai scout assess --job-url URL` for a job assessed by its URL; one model call, on the user's yes)",
            )
        if action == ACTION_DRAFT and decision == _GATE_SUGGEST:
            raise JobActionError(
                "draft_not_needed",
                "A resume is suggested for this job already; a draft is for a job that is held. Pick it again: `gigai scout resume pick --job-url URL --refresh`.",
            )
        settle = job_resume_port.settle_stored()
        try:
            settle(home_root, target, *pair, action=action, now=when)
        except NotBuilt:
            raise
        except ValueError as exc:  # the pick's and the record's own typed refusals (``PickError`` is a RuntimeError, below)
            raise _refused(exc) from exc
        except RuntimeError as exc:
            if getattr(exc, "code", None) is None:
                raise
            raise _refused(exc) from exc
    return {**pick_view(home_root, target, job_url, profile_id=pair[0]), "action": action, **extra}


__all__ = [
    "ACTION_DISMISS_PROPOSED",
    "ACTION_DRAFT",
    "ACTION_REFRESH",
    "ACTION_SHORTEN",
    "ACTION_USE_PROPOSED",
    "HOW_DISMISSED",
    "HOW_JOB_RESUME_EDIT",
    "PICK_ACTIONS",
    "PICK_SCHEMA",
    "RESOLVE_HOWS",
    "STATUSES",
    "SUGGESTIONS_SCHEMA",
    "SUGGESTION_KINDS",
    "TRIGGER_MASTER_LINE",
    "JobActionError",
    "NotBuilt",
    "add_line",
    "add_suggestion",
    "after_handback",
    "check_resolves",
    "dismiss_suggestion",
    "list_suggestions",
    "pick_action",
    "pick_view",
    "resolve_suggestion",
    "suggestion_ids",
]
