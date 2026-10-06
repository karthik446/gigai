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

0.1.11 N2 (SPEC 5.3): WHEN THE MASTER IS THE BASIS, THE CHECK READS THE MASTER.  Everything above
is the check of a profile with no master, or detached from it (``tailor_master.tailoring_basis``);
it is unchanged.  For a profile whose resumes are made from the master, the markdown is a HAND-BACK
(``handback_result``): the rules are ``handback_check``'s, read against every line of the master by
id plus the answers and the matching stories, never against the profile's own 2-page resume (which
refused 16 of 16 sound hand-backs in the spike: master roles and the master's own Skills lines).

- a line of the job's stored resume, unchanged and where the stored resume has it, stays that line;
- a master line, word for word, is a ``copy`` of it (its ref carries the master id);
- an entry heading and its title/dates line are copies of the master's;
- any other line cites its sources (``<!-- src: b-23b6dc, A tooling:temporal -->``) and is stored in
  the shape of a 0.1.10 rewrite: ``kind: rewritten``, its cited refs (each master ref with
  ``item_id``), ``origin: user``, and the master line it rewords as its ``alternative``.  So the
  page's per-line choice shows the original and ``apply_line_choice`` puts it back: Restore per line.
  A Skills line needs no citation: code finds the master Skills lines and the answers that state it.

A refusal (``HandbackRefused``) lists EVERY problem as ``{line, code, what, fix}``, by line number
and the one word or number, never the line's text; its text is built here, not by the CLI.  The
resume must also print on ``LENGTH_RULE.max_pages`` pages at the tightest spacing the PDF may choose
(``over_page_limit``, with the count); when no renderer can count them, the length is not checked.

THE CHECK IS A GUARD, NOT PROOF: numbers, names, ownership, entries and sources.  It does not prove
that a reworded line is true (``handback_check.WHAT_THE_CHECK_IS``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re

from ..canonical import digest_imported_bytes
from .find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
from .find_jobs.contracts import FindJobsContractError, Producer
from .handback_check import (
    MASTER,
    STORED,
    HandbackEntry,
    HandbackLine,
    HandbackSection,
    LineSource,
    MasterLines,
    Problem,
    check_handback,
    cited_ids,
    master_lines,
    over_page_limit,
    place_of,
    refusal_text,
)
from .master_resume import Master
from .quick_assess import _default_model_target
from .resume_pdf import _BULLET, _COMMENT, _HASHES, ResumeMarkdownError, parse_resume_markdown
from .resume_pii import detect_contact_details
from .tailor_no_loss import _FUNCTION_WORDS, _skill_items
from .tailored_resume import (
    ENTRY_SECTIONS,
    LENGTH_RULE,
    MAX_ENTRIES_PER_SECTION,
    MAX_TOTAL_LINES,
    TAILOR_INSTRUCTIONS_DIGEST,
    AnswerSource,
    LineAlternative,
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
    heading_only,
    heading_only_line,
    is_earlier_heading,
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
RECHECK_PIPELINE_OFF = "pipeline_off"


#: One logical line of the markdown: where it starts, what it says (no marker, no comment), and the sources its
#: trailing ``<!-- src: ... -->`` comment cites (read by the hand-back check only).
_Text = HandbackLine
_Entry = HandbackEntry
_Section = HandbackSection


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
        trailing = ""
        while True:
            comment = _COMMENT.search(line)
            if comment is None:
                break
            line, trailing = line[: comment.start()], comment.group(0) + trailing
        cited = cited_ids(trailing)
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
                bullets.append(_Text(number, text, cited))
            elif not bullets:
                head.append(_Text(number, " ".join(_display(line).split())))
            else:
                last = bullets[-1]
                bullets[-1] = _Text(last.number, f"{last.text} {text}", tuple(dict.fromkeys((*last.cited, *cited))))  # a hard wrap
            continue
        if not bullet and lines and not blank_before:
            last = lines[-1]
            lines[-1] = _Text(last.number, f"{last.text} {text}", tuple(dict.fromkeys((*last.cited, *cited))))  # a hard wrap, or the same paragraph
        else:
            lines.append(_Text(number, text, cited))
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
        #: A stored role shown by its heading alone, by the one line it is listed by (``tailored_resume.EARLIER_HEADING``).
        self.earlier: dict[str, TailoredEntry] = {}
        if previous is not None:
            for section in previous.result.sections:
                for role in heading_only(section):
                    self.earlier.setdefault(heading_only_line([line.text for line in role.heading]), role)
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
                if section.heading == "experience" and entry.heading and is_earlier_heading(entry.heading[0].text):
                    # The block of roles listed by their heading alone: each line is such a role of the stored resume.
                    for item in (*entry.heading[1:], *entry.bullets):
                        role = known.earlier.get(item.text)
                        if role is None:
                            invalid.append(f"line {item.number}: a line under Earlier experience must be a role your resume lists there, unchanged")
                        else:
                            entries.append(TailoredEntry(tuple(replace(line, id=None) for line in role.heading), ()))
                    total += len(entry.heading) + len(entry.bullets)
                    continue
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


# --- 0.1.11 N2: the hand-back, checked against the master (SPEC 5.3) ---------------------------------


class HandbackRefused(TailorError):
    """A hand-back the master check refused: EVERY problem, as ``{line, code, what, fix}`` (``handback_check.Problem``).

    ``code`` is ``edited_resume_unsupported`` (``personal_info_refused`` when that is the only rule broken), the
    codes a refused edit always had; each problem carries its own rule's code.  The message is
    ``handback_check.refusal_text``: line numbers and the one word or number, never a line's text.
    """

    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems: tuple[Problem, ...] = tuple(problems)
        codes = {item.code for item in self.problems}
        super().__init__("personal_info_refused" if codes == {"personal_info_refused"} else "edited_resume_unsupported", refusal_text(self.problems))


class _Stored:
    """The job's stored resume as a hand-back keeps it: its body lines by where they stand and what they show, its headings by text.

    A ``custom`` line (the 0.1.10 edit: text with no source) is not kept: it is checked like a new line, and
    needs a citation like one.
    """

    def __init__(self, previous: TailoredResume | None) -> None:
        self._body: dict[tuple[str, str], list[TailoredLine]] = {}
        self._heading: dict[str, list[TailoredLine]] = {}
        for section in previous.sections if previous is not None else ():
            for line in section.lines:
                self._add(place_of(section.heading), line)
            for entry in section.entries:
                place = place_of(section.heading, _shown(entry.heading[0].text) if entry.heading else "")
                for line in entry.heading:
                    self._heading.setdefault(_shown(line.text), []).append(line)
                for line in entry.bullets:
                    self._add(place, line)

    def _add(self, place: str, line: TailoredLine) -> None:
        if line.kind != "custom":
            self._body.setdefault((place, " ".join(shown_text(line).split())), []).append(line)

    def places(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._body)

    def body(self, place: str, text: str) -> TailoredLine | None:
        return _Known._take(self._body.get((place, text)))

    def heading(self, text: str) -> TailoredLine | None:
        return _Known._take(self._heading.get(text))


def _master_ref(lines: MasterLines, item_id: str) -> SourceRef:
    number = lines.items[item_id]
    return SourceRef("resume", number, None, lines.text(number), (), item_id)


def _reworded(text: str, source: LineSource, lines: MasterLines, answers: Mapping[str, AnswerSource], *, skills: bool) -> TailoredLine:
    """A reworded line in the stored shape of a 0.1.10 rewrite: its cited refs, ``origin: user``, the master line as alternative."""

    master_refs = tuple(_master_ref(lines, item_id) for item_id in source.master_ids)
    refs = master_refs + tuple(SourceRef("answer", None, key, answers[key].guard_text) for key in source.answer_ids)
    if not refs:  # a Skills line that lists nothing (a label alone): the writer's own text, no source claimed
        return TailoredLine("custom", text, (), None, None, "user", None, None)
    original: LineAlternative | None = None
    if len(master_refs) == 1:
        original = LineAlternative("copy", master_refs[0].text, master_refs)
    elif master_refs and not skills:
        # Several lines of one entry reworded into one: the original is those lines, one after the other (the 0.1.10 shape).
        original = LineAlternative("copy", " ".join(_shown(ref.text) for ref in master_refs), master_refs)
    return TailoredLine("rewritten", text, refs, None, None, "user", original)


def handback_result(
    markdown: str,
    *,
    master: Master,
    answers: Mapping[str, AnswerSource],
    previous: TailoredResume | None = None,
    pages: int | None = None,
) -> TailoredResume:
    """The validated structure of a hand-back, checked against the MASTER (see the module text).  Pure: no I/O.

    A guard, not proof: it does not prove that a reworded line is true (``handback_check``).

    ``answers``: the user's answers and the stories that match the posting (``tailor_sources``).  ``previous``:
    the job's stored resume, whose unchanged lines keep their sources.  ``pages``: the pages the markdown
    prints on, counted by the caller, or ``None`` when no renderer could count them.

    Raises ``HandbackRefused`` (every problem at once) or ``TailorError``: ``resume_markdown_invalid`` /
    ``resume_markdown_too_large`` (the format, a bound), ``personal_info_refused``.
    """

    if not isinstance(markdown, str):
        raise TailorError("wrong_type", "markdown must be a string (resume markdown in GigAI's format)")
    try:
        parse_resume_markdown(markdown)
    except ResumeMarkdownError as exc:
        raise TailorError(exc.code, str(exc)) from exc

    parsed = _sections(markdown)
    stored = _Stored(previous)
    numbered = master_lines(master)
    checked = check_handback(parsed, master, answers, stored=stored.places())
    problems = list(checked.problems)
    if pages is not None and pages > LENGTH_RULE.max_pages:
        problems.append(over_page_limit(pages))
    invalid: list[str] = []

    def heading(item: _Text) -> TailoredLine | None:
        found = checked.headings.get(item.number)
        if found is None:
            return None
        kept = stored.heading(item.text)
        if kept is not None:
            return replace(kept, id=None)
        entry_id, index = found
        number = numbered.entries[entry_id][index]
        raw = numbered.text(number)
        return TailoredLine("copy", raw, (SourceRef("resume", number, None, raw, (), entry_id),), origin="user")

    def body(item: _Text, place: str, section: str) -> list[TailoredLine]:
        source = checked.lines.get(item.number)
        if source is None:
            return []
        if source.kind == STORED:
            kept = stored.body(place, item.text)
            assert kept is not None
            return [replace(kept, id=None)]
        if source.kind == MASTER:  # one master line, or a paragraph of several: a copy of each
            refs = [_master_ref(numbered, item_id) for item_id in source.master_ids]
            return [TailoredLine("copy", ref.text, (ref,), origin="user") for ref in refs]
        try:
            clean = custom_line_text(item.text)
        except TailorError as exc:  # the length and control-character bounds of a line someone typed
            if exc.code != "personal_info_refused":
                invalid.append(f"line {item.number}: {exc}")
                return []
            # The bounds held (they are checked first) and the check already read the line for personal info: what is
            # left is a Skills line shaped like a name that lists known skills ("Model Context Protocol").
            clean = item.text
        return [_reworded(clean, source, numbered, answers, skills=section == "skills")]

    sections: list[TailoredSection] = []
    total = 0
    for section in parsed:
        if section.heading in ENTRY_SECTIONS:
            if len(section.entries) > MAX_ENTRIES_PER_SECTION:
                invalid.append(f"the {section.heading.capitalize()} section has more than {MAX_ENTRIES_PER_SECTION} entries")
            entries: list[TailoredEntry] = []
            for entry in section.entries:
                if section.heading == "experience" and entry.heading and is_earlier_heading(entry.heading[0].text):
                    # The block of roles listed by their heading alone (0.1.11.4 item 9): each is its master heading, no bullet.
                    for item in entry.heading[1:]:
                        entry_id = checked.earlier.get(item.number)
                        if entry_id is not None:
                            raws = [(number, numbered.text(number)) for number in numbered.entries[entry_id]]
                            entries.append(TailoredEntry(
                                tuple(TailoredLine("copy", raw, (SourceRef("resume", number, None, raw, (), entry_id),), origin="user") for number, raw in raws), (),
                            ))
                    total += len(entry.heading)
                    continue
                place = place_of(section.heading, entry.heading[0].text if entry.heading else "")
                head = [line for line in (heading(item) for item in entry.heading) if line is not None]
                bullets = [line for item in entry.bullets for line in body(item, place, section.heading)]
                total += len(entry.heading) + len(entry.bullets)
                entries.append(TailoredEntry(tuple(head), tuple(bullets)))
            sections.append(TailoredSection(section.heading, (), tuple(entries)))
        else:
            place = place_of(section.heading)
            lines = [line for item in section.lines for line in body(item, place, section.heading)]
            total += len(section.lines)
            sections.append(TailoredSection(section.heading, tuple(lines), ()))
    if total > MAX_TOTAL_LINES:
        invalid.append(f"the resume has {total} lines; at most {MAX_TOTAL_LINES} allowed")

    if invalid:
        _refuse("resume_markdown_invalid", "this markdown cannot be stored as a tailored resume", invalid)
    if problems:
        raise HandbackRefused(problems)
    result = _with_ids(TailoredResume((), tuple(sections)))
    if detect_contact_details(render_markdown(result)):
        raise TailorError("personal_info_refused", "the resume holds a contact detail; GigAI stores no name or contact details")
    return result


def _master_basis(home_root: Path, target: Path, profile_id: str | None):
    """The stored master (``master_store.StoredMaster``) when this profile's resumes are made from it, else ``None``.

    ``tailor_master.tailoring_basis`` decides, as for a tailoring: a master is stored and can be read, the resume
    is a profile's, and the profile is not detached.  ``None`` keeps the 0.1.10 check against the profile's own resume.
    """

    if profile_id is None:
        return None
    from ..workpad import WorkpadError, resolve_workpad
    from . import profile_records
    from .tailor_master import BASIS_MASTER, stored_master, tailoring_basis

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        stored = stored_master(home_root, target, resolved=resolved)
        if stored is None:
            return None
        profile = next((record for record in profile_records.list_profiles(resolved) if record.profile_id == profile_id and record.state != "deleted"), None)
    except (WorkpadError, profile_records.ProfileRecordError):
        return None
    return stored if tailoring_basis(home_root, profile, master_stored=True) == BASIS_MASTER else None


def _pages(markdown: str) -> int | None:
    """The fewest pages the markdown prints on (the tightest spacing the PDF may choose); ``None`` when that cannot be counted."""

    try:
        from .resume_pdf import SPACING_MIN, measure_markdown

        return measure_markdown(markdown, spacing_scale=SPACING_MIN)[0]
    except Exception:  # noqa: BLE001 - no renderer (Typst missing or failing) or markdown the format check refuses next: the length is not checked, never guessed
        return None


# --- 0.1.11.4 E1: the revision a file was made from ---------------------------------------------------------

#: ``resume_file_stale``: the file names another revision of the job resume than the one stored (one sentence, with the fix).
STALE_FILE = (
    "This file was made from an older version of the resume. Get the current one with "
    "gigai scout resume brief --job-url URL --out FILE, then make your edits again."
)
_REVISION_LINE = re.compile(r"^[ \t]*<!--[ \t]*gigai-resume:(.*?)-->[ \t]*$", re.MULTILINE)
_REVISION_FIELDS = re.compile(r"\Aupdated_at=(\S+)[ \t]+sha256=([0-9a-f]{64})\Z")


def _markdown_sha256(markdown: str) -> str:
    return digest_imported_bytes(markdown.encode("utf-8")).removeprefix("sha256:")


def revision_comment(stored: TailorResponse) -> str:
    """The comment ``resume brief`` writes above the job resume: the stored revision the text was made from.

    ``<!-- gigai-resume: updated_at=... sha256=... -->``: a comment above the first section, which no reader of
    resume markdown stores or prints (like a line's ``<!-- R12 -->``).  ``attach_edited_resume`` reads it back."""

    return f"<!-- gigai-resume: updated_at={stored.updated_at} sha256={_markdown_sha256(stored.markdown)} -->"


def recorded_revision(markdown: str) -> tuple[str, str] | None:
    """``(updated_at, sha256)`` of the revision comment in ``markdown``, or ``None``: it has none (or not one this GigAI wrote)."""

    found = _REVISION_LINE.search(markdown) if isinstance(markdown, str) else None
    fields = _REVISION_FIELDS.match(found.group(1).strip()) if found is not None else None
    return None if fields is None else (fields.group(1), fields.group(2))


@dataclass(frozen=True)
class AttachedResume:
    """What an attach did: the stored resume, whether it changed anything, and the file in the job's folder."""

    response: TailorResponse
    changed: bool
    #: ``jobs_folder.SavedJobFile`` of the markdown, or ``None`` (nothing changed, or the folder could not be written).
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


def _fitted(
    result: TailoredResume, *, master: Master, job: object, home_root: Path, target: Path, profile_id: str | None, job_url: str,
) -> TailoredResume:
    """``result`` cut to the page limit by SPEC 3.2: ``pick``'s cut order and ``tailor_length.fit_by_cuts``, the cut recorded (``length``).

    The cut order reads the job's stored assessment (its rows' sources protect the last printed evidence of a
    mandatory row) and the posting; the pages are the check's own (the tightest spacing the PDF may choose).
    Raises ``HandbackRefused`` (``over_page_limit``) when even every cut leaves it over the limit.
    """

    from datetime import date

    from .assess_master import cited_requirements
    from .find_jobs.job_state import normalize_job_identity
    from .master_selection import SelectionPosting, SelectionProfile
    from .quick_assess import read_quick_assessment
    from .tailor_length import fit_by_cuts
    from .tailor_master import MODE_VIEW, cut_order, job_candidates

    today = date.today()
    assessment = None
    if profile_id is not None:
        try:
            assessment = read_quick_assessment(home_root, target, profile_id, normalize_job_identity(job_url))
        except FindJobsContractError:
            assessment = None
    matrix = tuple(assessment.result.matrix) if assessment is not None else ()
    posting = SelectionPosting(getattr(job, "title", "") or "", getattr(job, "text", "") or "", getattr(job, "company", "") or "", getattr(job, "location", "") or "")
    posting = replace(posting, cited=cited_requirements(master, matrix))
    candidates = job_candidates(master, SelectionProfile(), posting, mode=MODE_VIEW, today=today)
    cuts, refill = cut_order(result, candidates, master, today=today)

    def measure(shown: TailoredResume) -> int | None:
        return _pages(render_markdown(shown))

    fitted = fit_by_cuts(result, cuts, measure=measure, max_pages=LENGTH_RULE.max_pages, refill=refill)
    pages = measure(fitted)
    if pages is not None and pages > LENGTH_RULE.max_pages:
        raise HandbackRefused([over_page_limit(pages)])
    return fitted


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
    if base is None:
        return None
    if base.job.fetch_kind == "pasted":
        # A pasted posting has no link to fetch (E2EFIX F1): its stored assessment is the job, whatever text it kept.
        return replace(base.job, text=base.posting_text or base.job.text or "")
    if not base.posting_text:
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
    fit: bool = False,
    force: bool = False,
) -> AttachedResume:
    """Store ``markdown`` as the tailored resume of the job at ``job_url`` for one profile (default: the selected one).

    Validated by ``handback_result`` against the MASTER when this profile's resumes are made from it
    (0.1.11 N2; the stored resume then names the master revision it was checked against), else by
    ``edited_result`` against the profile's own resume, as before.  Stored where a tailoring is
    stored, marked ``edited``, and its markdown written to the job's folder of the jobs folder.  Attaching what is
    already stored changes nothing (``changed`` false).  Raises ``TailorError`` with the check's
    codes (``HandbackRefused`` lists every problem) or the tailoring's input codes
    (``job_input_invalid``, ``job_fetch_failed``, ``profile_not_found`` ...).

    ``fit`` (0.1.11 N5b, SPEC 5.3): a master-checked hand-back over the page limit is cut to it by code (3.2), the
    cut recorded on the stored resume (``length``, so one Restore puts it back); without ``fit`` it is refused
    (``over_page_limit``).  It changes nothing for a resume that fits.

    0.1.11.4 E1: a file that names the revision it was made from (``revision_comment``) is refused when the stored
    resume is another one (``resume_file_stale``: its lines would be checked against a resume they were not copied
    from), and a stored file that cannot be read is not written over (``stored_resume_unreadable``); ``force``
    stores the file in both cases.  A change of the stored resume drops the selection that waited beside the old one.
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
    # 0.1.11 N2: a profile whose resumes are made from the master is checked against the master, every line by id.
    basis = _master_basis(home_root, target, resume.profile_id)
    master_source = None
    line_count = len(resume_lines(resume.text))
    pages = None
    if basis is not None:
        from .tailor_master import MasterSource

        master_source = MasterSource(basis.revision.revision_id, basis.revision.revision, basis.revision.content_sha256)
        line_count = len(master_lines(basis.master).lines)
        pages = _pages(markdown) if isinstance(markdown, str) else None
    # The stored resume is read and replaced under the store's write lock: no model call is out, only local work.
    with tailored_resume_write_lock(path):
        previous = read_tailored_resume(path)
        if not force:
            from . import suggestions

            if previous is None and suggestions.stored_unreadable(path):
                raise TailorError("stored_resume_unreadable", suggestions.STORED_UNREADABLE)
            made_from = recorded_revision(markdown)
            if previous is not None and made_from is not None and made_from[1] != _markdown_sha256(previous.markdown):
                raise TailorError("resume_file_stale", STALE_FILE)
        if basis is None:
            result = edited_result(markdown, ctx=ctx, job=tailor_job, previous=previous)
        else:
            over = fit and pages is not None and pages > LENGTH_RULE.max_pages
            result = handback_result(
                markdown, master=basis.master, answers=answers, previous=None if previous is None else previous.result,
                pages=None if over else pages,  # --fit: the length is code's to settle below
            )
            if over:
                result = _fitted(result, master=basis.master, job=job, home_root=home_root, target=target, profile_id=resume.profile_id, job_url=job_url)
        if previous is not None and previous.result == result:
            return AttachedResume(previous, False)
        response = TailorResponse(
            job=job,
            resume=resume,
            sources=TailorSources(
                resume_content_sha256=resume.content_sha256,
                resume_line_count=line_count,
                answers={key: item.revision_id for key, item in answers.items()},
                assessment_stored_path=assessment_path,
                master=master_source,
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
        # 0.1.11.4 J1: the job's resume.md may be the very file handed back (edited in place): it is then replaced, not doubled.
        saved = save_tailor_response(response, home_root=home_root, imported=digest_imported_bytes(markdown.encode("utf-8")))
        # The selection that waited beside the resume as it was is not this one's: it goes with it.
        from .suggestions import drop_proposal_after_edit

        drop_proposal_after_edit(home_root, target, response)
    return AttachedResume(response, True, saved)


def queue_recheck(home_root: Path, target: Path, attached: AttachedResume) -> dict[str, object]:
    """Put the job of an attached resume through the pipeline; what was queued.  Never raises.

    The tailor step keeps the edited resume (it is the user's) and the re-assessment, the Scout
    ATS score and the Scout label run against it.  ``result`` is ``steps.enqueue_job``'s
    (``enqueued`` ...), ``unchanged`` when the attach changed nothing, or ``not_queued`` with
    the ``error_code`` (``assessment_missing``: the job has no assessment for this profile yet),
    or ``pipeline_off`` (0.1.11 default: nothing queued, no model call, no error).
    Ids and codes only.
    """

    from .pipeline import triggers
    from .pipeline.settings import pipeline_setting
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
    if not pipeline_setting(Path(home_root), Path(target)).enabled:
        return {**body, "result": RECHECK_PIPELINE_OFF}  # 0.1.11: nothing is queued and no model call is made
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
    "RECHECK_PIPELINE_OFF",
    "RECHECK_SCHEMA",
    "STALE_FILE",
    "AttachedResume",
    "HandbackRefused",
    "attach_edited_resume",
    "edit_mark",
    "edited_result",
    "handback_result",
    "queue_recheck",
    "recorded_revision",
    "revision_comment",
]
