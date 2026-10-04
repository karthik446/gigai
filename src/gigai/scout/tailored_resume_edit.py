"""0110-10-05 B: attach an edited markdown to ONE job as its tailored resume.

``attach_edited_resume`` is what ``gigai scout resume tailor --in FILE --job-url URL`` and
``PUT /api/tailored-resumes`` both call.  The markdown is resume markdown in GigAI's format (what
``tailored_resume.render_markdown`` writes and the resumes folder holds: ``## Section``,
``### entry heading``, ``- `` bullets; ``resume_pdf.parse_resume_markdown`` is the format's one
description).  No model is called and nothing is fetched for a posting the index or a stored
assessment already holds.

Every line of the markdown becomes one line of the stored result:

- a line that is a line of the job's stored tailored resume, unchanged, stays that line (a copy
  or a rewrite keeps its cited sources);
- a line that is a resume line, unchanged, is a ``copy`` of it;
- any other body line is the writer's own text: ``kind: custom``, ``origin: user``, no source
  cited, exactly like a line edited with ``PUT /api/tailored-resumes/lines``;
- an entry heading (employer, title, dates, degree) is copy-only, as in a tailoring: it must be a
  line of the resume, unchanged.

**What a custom line may say** (the tailoring's guards, with the WHOLE resume and EVERY answer
and matching story as the sources, since an edited line cites none):

- personal info: a name-shaped line or a contact detail is refused (``personal_info_refused``;
  GigAI stores no name or contact details, 0110-046);
- numbers: every number it states is stated by the resume or an answer;
- posting skills: a skill, tool or technology the posting names appears in the resume or an
  answer (the tailoring's posting-term guard);
- in the Skills section: every word of every item appears in the resume or an answer.

A number or skill that fails is ``edited_resume_unsupported``.  Every problem is reported at
once, by the markdown's line number and the one number or skill word, never the line's text.  The fix the
message names is the honest one: save an answer that states it (``gigai scout answer``), then
attach again.

The stored resume is marked ``edited`` (``TailorEdit``: who wrote it, operator or agent, when,
and a free-text source), so the background pipeline never tailors over it (it is not the
pipeline's own file: ``pipeline.steps._users_resume``).  ``queue_recheck`` then puts the job
through the pipeline: the tailor step keeps the edited resume and the re-assessment, the Scout
ATS score and the Scout label run against it.  Lines above the first ``## `` section (a name, a
contact line) are never stored.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
import os
from pathlib import Path

from .find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
from .find_jobs.contracts import FindJobsContractError, Producer
from .quick_assess import _default_model_target
from .resume_pdf import _BULLET, _COMMENT, _HASHES, ResumeMarkdownError, parse_resume_markdown
from .resume_pii import detect_contact_details
from .tailor_no_loss import _FUNCTION_WORDS, _skill_items
from .tailored_resume import (
    ENTRY_SECTIONS,
    MAX_ENTRIES_PER_SECTION,
    MAX_TOTAL_LINES,
    TAILOR_INSTRUCTIONS_DIGEST,
    SourceRef,
    TailorContext,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    TailorEdit,
    TailorError,
    TailorJob,
    TailorRequest,
    TailorResponse,
    TailorSources,
    _TERM_TOKEN,
    _clean_token,
    _display,
    _now,
    _resume_ref,
    _span_copy,
    _stored_matrix,
    _term_variants,
    _with_ids,
    custom_line_text,
    guard_terms,
    numeric_values,
    read_tailored_resume,
    render_markdown,
    resolve_tailor_job,
    resolve_tailor_resume,
    resume_lines,
    save_tailor_response,
    shown_text,
    source_terms,
    tailor_context,
    tailor_sources,
    tailored_resume_path,
    tailored_resume_write_lock,
    text_terms,
)

_PRODUCER_CALLABLE = "scout.tailor.attach"
_PRODUCER_VERSION = "1"
#: No adapter made this version: the writer did.
_PRODUCER_ADAPTER = "none"
#: How many problems one refusal lists (the count is always the real one).
MAX_PROBLEMS_SHOWN = 10

RECHECK_SCHEMA = "scout-tailored-recheck:1"
RECHECK_NOT_QUEUED = "not_queued"


@dataclass(frozen=True)
class _Text:
    """One logical line of the markdown: where it starts and what it says (no marker, no comment)."""

    number: int
    text: str


@dataclass(frozen=True)
class _Entry:
    heading: tuple[_Text, ...]
    bullets: tuple[_Text, ...]


@dataclass(frozen=True)
class _Section:
    heading: str
    lines: tuple[_Text, ...] = ()
    entries: tuple[_Entry, ...] = ()


def _sections(markdown: str) -> list[_Section]:
    """The markdown's sections, line by line.  The format is ``parse_resume_markdown``'s and was checked by it."""

    sections: list[_Section] = []
    heading = ""
    lines: list[_Text] = []
    entries: list[tuple[list[_Text], list[_Text]]] = []
    after_blank = True

    def close() -> None:
        if heading and (lines or entries):
            sections.append(_Section(heading, tuple(lines), tuple(_Entry(tuple(head), tuple(bullets)) for head, bullets in entries)))

    for number, raw in enumerate(markdown.splitlines(), 1):
        line = raw
        while _COMMENT.search(line):
            line = _COMMENT.sub("", line)
        line = line.strip()
        if not line:
            after_blank = True
            continue
        blank_before, after_blank = after_blank, False
        hashes = _HASHES.match(line)
        if hashes and len(hashes.group(1)) == 2:
            close()
            heading, lines, entries = " ".join(hashes.group(2).split()).rstrip(":").lower(), [], []
            continue
        if not heading:
            continue  # above the first section: never stored (a name, a contact line)
        if hashes:
            entries.append(([_Text(number, " ".join(_display(line).split()))], []))
            continue
        bullet = _BULLET.match(line)
        text = " ".join((bullet.group(1) if bullet else line).split())
        if not text:
            continue
        if heading in ENTRY_SECTIONS:
            head, bullets = entries[-1]
            if bullet:
                bullets.append(_Text(number, text))
            elif not bullets:
                head.append(_Text(number, " ".join(_display(line).split())))
            else:
                bullets[-1] = _Text(bullets[-1].number, f"{bullets[-1].text} {text}")  # a hard wrap
            continue
        if not bullet and lines and not blank_before:
            lines[-1] = _Text(lines[-1].number, f"{lines[-1].text} {text}")  # a hard wrap, or the same paragraph
        else:
            lines.append(_Text(number, text))
    close()
    return sections


def _shown(text: str) -> str:
    return " ".join(_display(text).split())


class _Known:
    """What a line of the markdown can be without being the writer's own text: a stored line or a resume line."""

    def __init__(self, ctx: TailorContext, previous: TailorResponse | None) -> None:
        self._ctx = ctx
        self._single: dict[str, list[int]] = {}
        self._span: dict[str, list[int]] = {}
        assert ctx.model is not None
        for number, _text in ctx.model.lines:  # only the lines the privacy strip keeps
            self._single.setdefault(_shown(ctx.resume_lines[number - 1]), []).append(number)
            self._span.setdefault(_shown(_resume_ref(number, ctx).text), []).append(number)
        self._body: dict[str, list[TailoredLine]] = {}
        self._heading: dict[str, list[TailoredLine]] = {}
        if previous is not None:
            for section in previous.result.sections:
                for line in section.body_lines():
                    self._body.setdefault(" ".join(shown_text(line).split()), []).append(line)
                for entry in section.entries:
                    for line in entry.heading:
                        self._heading.setdefault(_shown(line.text), []).append(line)

    @staticmethod
    def _take(found: list | None):
        """The first one not used yet (a text the resume states twice is matched twice), else the first."""

        if not found:
            return None
        return found.pop(0) if len(found) > 1 else found[0]

    def heading(self, text: str) -> TailoredLine | None:
        stored = self._take(self._heading.get(text))
        if stored is not None:
            return replace(stored, id=None)
        number = self._take(self._single.get(text))
        if number is None:
            return None
        raw = self._ctx.resume_lines[number - 1]
        return TailoredLine("copy", raw, (SourceRef("resume", number, None, raw),), origin="user")

    def body(self, text: str) -> TailoredLine | None:
        stored = self._take(self._body.get(text))
        if stored is not None:
            return replace(stored, id=None)
        number = self._take(self._span.get(text))
        if number is not None:
            return _span_copy(_resume_ref(number, self._ctx), origin="user")
        number = self._take(self._single.get(text))
        if number is None:
            return None
        raw = self._ctx.resume_lines[number - 1]
        return TailoredLine("copy", raw, (SourceRef("resume", number, None, raw),), origin="user")


class _Trace:
    """Whether a custom line's numbers and skills are stated by the resume or an answer."""

    def __init__(self, sources: Iterable[str], terms: Iterable[str]) -> None:
        texts = list(sources)
        self._numbers = {mention.value for text in texts for mention in numeric_values(text)}
        self._terms: set[str] = set()
        for text in texts:
            self._terms |= source_terms(text)
        self._posting = frozenset(terms)

    def _word_stated(self, token: str) -> bool:
        if _term_variants(token) & self._terms:
            return True
        parts = [part for part in token.replace("/", "-").split("-") if part]
        return len(parts) > 1 and all(_term_variants(part) & self._terms for part in parts)

    def problems(self, text: str, *, skills: bool) -> list[str]:
        found = [f'the number "{mention.span}"' for mention in numeric_values(text) if mention.value not in self._numbers]
        used = text_terms(text)
        borrowed = sorted(term for term in self._posting if term in used and term not in self._terms)
        found += [f'the posting skill "{term}"' for term in borrowed]
        if skills:
            # Word by word, so a refusal names a skill and never a whole line.
            seen: set[str] = set(borrowed)
            for item in _skill_items(text):
                for match in _TERM_TOKEN.finditer(item):
                    word = _clean_token(match.group(0))
                    if not word or word.lower() in _FUNCTION_WORDS or word.lower() in seen or _term_variants(word) & seen:
                        continue
                    seen.add(word.lower())
                    if not self._word_stated(word):
                        found.append(f'the skill "{word}"')
        return found


def _refuse(code: str, lead: str, problems: list[str], tail: str = "") -> None:
    shown = "; ".join(problems[:MAX_PROBLEMS_SHOWN])
    more = f"; and {len(problems) - MAX_PROBLEMS_SHOWN} more" if len(problems) > MAX_PROBLEMS_SHOWN else ""
    raise TailorError(code, f"{lead}: {shown}{more}.{tail}")


def edited_result(
    markdown: str, *, ctx: TailorContext, job: TailorJob, previous: TailorResponse | None = None
) -> TailoredResume:
    """The validated structure of an edited markdown (see the module docstring).  Pure: no I/O.

    Raises ``TailorError``: ``resume_markdown_invalid`` / ``resume_markdown_too_large`` (the
    format, an entry heading that is not a resume line, a bound), ``personal_info_refused``,
    ``edited_resume_unsupported``.
    """

    if not isinstance(markdown, str):
        raise TailorError("wrong_type", "markdown must be a string (resume markdown in GigAI's format)")
    try:
        parse_resume_markdown(markdown)
    except ResumeMarkdownError as exc:
        raise TailorError(exc.code, str(exc)) from exc

    known = _Known(ctx, previous)
    assert ctx.model is not None
    trace = _Trace(
        [text for _number, text in ctx.model.lines] + [answer.guard_text for answer in ctx.answers.values()],
        guard_terms(job, ctx),
    )
    invalid: list[str] = []
    personal: list[str] = []
    unsupported: list[str] = []

    def body(item: _Text, section: str) -> TailoredLine | None:
        line = known.body(item.text)
        if line is not None and line.kind != "custom":
            return line
        try:
            # A custom line carried over from the stored resume is checked like a new one.
            clean = custom_line_text(item.text)
        except TailorError as exc:
            if exc.code == "personal_info_refused":
                personal.append(f"line {item.number}")
            else:
                invalid.append(f"line {item.number}: {exc}")
            return None
        unsupported.extend(f"line {item.number}: {problem}" for problem in trace.problems(clean, skills=section == "skills"))
        if line is not None and line.text == clean:
            return line
        return TailoredLine("custom", clean, (), None, None, "user", None, None)

    sections: list[TailoredSection] = []
    total = 0
    for section in _sections(markdown):
        if section.heading in ENTRY_SECTIONS:
            if len(section.entries) > MAX_ENTRIES_PER_SECTION:
                invalid.append(f"the {section.heading.capitalize()} section has more than {MAX_ENTRIES_PER_SECTION} entries")
            entries: list[TailoredEntry] = []
            for entry in section.entries:
                heading: list[TailoredLine] = []
                for item in entry.heading:
                    line = known.heading(item.text)
                    if line is None:
                        invalid.append(
                            f"line {item.number}: an entry heading (employer, title, dates, degree) must be a line of your resume, unchanged"
                        )
                    else:
                        heading.append(line)
                bullets = [line for line in (body(item, section.heading) for item in entry.bullets) if line is not None]
                total += len(entry.heading) + len(entry.bullets)
                entries.append(TailoredEntry(tuple(heading), tuple(bullets)))
            sections.append(TailoredSection(section.heading, (), tuple(entries)))
        else:
            lines = [line for line in (body(item, section.heading) for item in section.lines) if line is not None]
            total += len(section.lines)
            sections.append(TailoredSection(section.heading, tuple(lines), ()))
    if total > MAX_TOTAL_LINES:
        invalid.append(f"the resume has {total} lines; at most {MAX_TOTAL_LINES} allowed")

    if invalid:
        _refuse("resume_markdown_invalid", "this markdown cannot be stored as a tailored resume", invalid)
    if personal:
        _refuse(
            "personal_info_refused", "a line looks like a name or a contact detail", personal,
            " GigAI stores no name or contact details: you type them in Scout's Generate PDF form when you make the PDF, never in a resume line.",
        )
    if unsupported:
        _refuse(
            "edited_resume_unsupported", "neither your resume nor your answers state", unsupported,
            " Every number and skill of an edited line must come from your resume or an answer:"
            " save an answer that states it (`gigai scout answer`), then attach the resume again.",
        )
    result = _with_ids(TailoredResume((), tuple(sections)))
    if detect_contact_details(render_markdown(result)):
        raise TailorError("personal_info_refused", "the resume holds a contact detail; GigAI stores no name or contact details")
    return result


@dataclass(frozen=True)
class AttachedResume:
    """What an attach did: the stored resume, whether it changed anything, and the file in the resumes folder."""

    response: TailorResponse
    changed: bool
    #: ``resumes_folder.SavedFile`` of the markdown, or ``None`` (nothing changed, or the folder could not be written).
    saved: object | None = None


def edit_mark(written_by: object, source: object, *, now: str | None = None) -> TailorEdit:
    """The ``edited`` mark for a write: ``written_by`` is operator or agent, ``source`` one line of free text or none."""

    from . import story_bank

    try:
        writer = story_bank.actor_value(written_by)  # type: ignore[arg-type]
        cleaned = story_bank.clean_source(source)
    except story_bank.StoryBankError as exc:
        raise TailorError(exc.code, str(exc)) from exc
    return TailorEdit(writer, now or _now(), cleaned)


def _assessed_job(home_root: Path, target: Path, profile_id: str | None, job_url: str):
    """The posting this profile's stored assessment of the job was made on, or ``None``: nothing is fetched for it.

    What the pipeline's steps read (``pipeline.steps._inputs``).  A job with no stored
    assessment, or one whose posting text was not stored, is resolved like a tailoring's.
    """

    from .find_jobs.job_state import normalize_job_identity
    from .quick_assess import read_quick_assessment

    if profile_id is None:
        return None
    try:
        base = read_quick_assessment(home_root, target, profile_id, normalize_job_identity(job_url))
    except FindJobsContractError:
        return None  # not a posting link: the resolution below says so
    if base is None or not base.posting_text:
        return None
    return replace(base.job, text=base.posting_text)


def attach_edited_resume(
    markdown: str,
    *,
    job_url: str,
    home_root: Path,
    target: Path,
    profile_id: str | None = None,
    written_by: str = "operator",
    source: str | None = None,
    resolved_job=None,
) -> AttachedResume:
    """Store ``markdown`` as the tailored resume of the job at ``job_url`` for one profile (default: the selected one).

    Validated by ``edited_result``; stored where a tailoring is stored, marked ``edited``, and its
    markdown written to the resumes folder.  Attaching what is already stored changes nothing
    (``changed`` false).  Raises ``TailorError`` with ``edited_result``'s codes or the tailoring's
    input codes (``job_input_invalid``, ``job_fetch_failed``, ``profile_not_found`` ...).
    """

    home_root, target = Path(home_root), Path(target)
    mark = edit_mark(written_by, source)
    try:
        request = TailorRequest(job=AssessJobInput(job_url=job_url or None), resume=AssessResumeInput(profile_id=profile_id or None))
    except FindJobsContractError as exc:
        raise TailorError(exc.code, str(exc)) from exc
    resume = resolve_tailor_resume(request, home_root=home_root, target=target)
    if resolved_job is None:
        resolved_job = _assessed_job(home_root, target, resume.profile_id, job_url)
    job = resolve_tailor_job(request, home_root=home_root, target=target, resolved_job=resolved_job)
    answers = tailor_sources(
        home_root=home_root, target=target, profile_id=resume.profile_id, resume_text=resume.text, title=job.title,
        posting_text=job.text,
    )
    try:
        path = tailored_resume_path(home_root, target, resume.profile_id, job.job_identity)
    except Exception as exc:  # noqa: BLE001 - the same typed failure a tailoring gives for a folder with no project
        raise TailorError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    matrix, assessment_path = _stored_matrix(home_root, target, job.job_identity)
    ctx = tailor_context(resume.text, answers=answers, matrix=matrix)
    tailor_job = TailorJob(title=job.title, company=job.company, location=job.location, posting_text=job.text)
    # The stored resume is read and replaced under the store's write lock: no model call is out, only local work.
    with tailored_resume_write_lock(path):
        previous = read_tailored_resume(path)
        result = edited_result(markdown, ctx=ctx, job=tailor_job, previous=previous)
        if previous is not None and previous.result == result:
            return AttachedResume(previous, False)
        response = TailorResponse(
            job=job,
            resume=resume,
            sources=TailorSources(
                resume_content_sha256=resume.content_sha256,
                resume_line_count=len(resume_lines(resume.text)),
                answers={key: item.revision_id for key, item in answers.items()},
                assessment_stored_path=assessment_path,
            ),
            result=result,
            markdown=render_markdown(result),
            producer=Producer(_PRODUCER_CALLABLE, _PRODUCER_VERSION, mark.written_by, _default_model_target(target), _PRODUCER_ADAPTER),
            usage=None,
            instructions_digest=TAILOR_INSTRUCTIONS_DIGEST,
            created_at=previous.created_at if previous is not None else mark.edited_at,
            updated_at=mark.edited_at,
            stored_path=os.fspath(path),
            markdown_path=os.fspath(path.with_suffix(".md")),
            edited=mark,
        )
        saved = save_tailor_response(response, home_root=home_root)
    return AttachedResume(response, True, saved)


def queue_recheck(home_root: Path, target: Path, attached: AttachedResume) -> dict[str, object]:
    """Put the job of an attached resume through the pipeline; what was queued.  Never raises.

    The tailor step keeps the edited resume (it is the user's) and the re-assessment, the Scout
    ATS score and the Scout label run against it.  ``result`` is ``steps.enqueue_job``'s
    (``enqueued`` ...), ``unchanged`` when the attach changed nothing, or ``not_queued`` with
    the ``error_code`` (``assessment_missing``: the job has no assessment for this profile yet).
    Ids and codes only.
    """

    from .pipeline import triggers
    from .pipeline.steps import StepError
    from .pipeline.store import PipelineStoreError

    response = attached.response
    body: dict[str, object] = {
        "schema_version": RECHECK_SCHEMA, "profile_id": response.resume.profile_id, "job_identity": response.job.job_identity,
        "error_code": None,
    }
    if not attached.changed:
        return {**body, "result": "unchanged"}
    if response.resume.profile_id is None:
        return {**body, "result": RECHECK_NOT_QUEUED, "error_code": "profile_unavailable"}
    try:
        queued = triggers.process_now(Path(home_root), Path(target), response.resume.profile_id, response.job.job_identity, force=True)
    except (StepError, PipelineStoreError) as exc:
        return {**body, "result": RECHECK_NOT_QUEUED, "error_code": exc.code}
    except Exception as exc:  # noqa: BLE001 - the resume is stored: a pipeline failure never fails the attach
        return {**body, "result": RECHECK_NOT_QUEUED, "error_code": getattr(exc, "code", None) or "scout_pipeline_failed"}
    return {**body, "result": queued["result"]}


__all__ = [
    "MAX_PROBLEMS_SHOWN",
    "RECHECK_NOT_QUEUED",
    "RECHECK_SCHEMA",
    "AttachedResume",
    "attach_edited_resume",
    "edit_mark",
    "edited_result",
    "queue_recheck",
]
