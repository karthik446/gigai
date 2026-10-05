"""The master resume in the assess path (0.1.10.9 master P7): what one assessment reads, and when it is out of date.

WHAT AN ASSESSMENT READS.  Without a master resume (``master_store``) an
assessment reads the profile's own resume, and nothing here runs:
``assess_input`` answers ``None`` and ``quick_assess.run_quick_assessment``
does exactly what it did.  With one, ``ASSESS_INPUT`` names what the prompt's
RESUME is for a profile:

- ``view``: the profile's own resume (its 2-page view of the master), as
  before.  ``assess_input`` answers ``None`` here too.
- ``evidence``: the EVIDENCE VIEW (``master_selection.evidence_view``): the
  lines of the WHOLE master most relevant to this posting, picked by code, up
  to the assess prompt's own resume cap (``EVIDENCE_CAP``, 12,000 characters).
  The same prompt, the same one call: only the RESUME block differs.  A
  verdict then says what the user can truthfully claim, not what one profile's
  2 pages happen to show.  The stored assessment says so
  (``AssessResponse.resume_basis``: the master revision and the selector
  version; ``resume`` still names the profile's pinned resume).

The quick assessment, "Assess all new" and the pipeline's base assessment all
go through ``run_quick_assessment`` and so read the same input.  Three things
keep reading what they read before: a pasted resume, a profile whose resume
was replaced by hand after its selection (``tailor_master.detached``), and the
pipeline's assessment of a TAILORED resume (it reads the 2 pages that will be
sent).  A find-jobs run seals one pinned resume for all its postings and
stays on the profile's resume (DESIGN.md section 7).

``evidence_text`` is the one pure builder: the product and the live eval
(``tests/evals/run_master_assess_eval.py``) both call it.

WHEN A STORED ASSESSMENT IS OUT OF DATE (``resume_changes``, the
``resume_changed`` stale reason of ``assessment_basis``).  Only with a master
stored; without one a stored assessment is never stale for its resume, as
before.  Targeted, like the story bank's rule: the assessment's own evidence
and its own open questions are compared with the lines it could read THEN and
the lines it would read NOW.

- THEN: the master at the revision ``resume_basis`` names (an evidence
  assessment), else the profile's resume at the revision ``resume`` pins.
- NOW: the master as it is (when the profile's assessments read the evidence
  view), else the profile's resume as it is.
- The same thing then and now is never stale: the same master revision, or
  the same resume (the contact cleanup's clean copy counts as the same, and so
  does a profile the migration left on its own resume: ``master init`` makes
  nothing stale).  A newer selector version alone is not a reason either.
- ``line_changed``: a line the assessment's evidence drew on was there then
  and is not there now (retired, no longer in the profile's resume, or edited
  so that no line now backs the evidence as well: a changed number counts, a
  fixed typo does not). Evidence is traced to a line word for word, or as a
  paraphrase by the words they share: a model often joins two lines in one
  sentence, so a share of the evidence's words is enough.
- ``new_line``: a line that was not there then names the subject of one of
  its open questions (``tooling:helm`` -> a new line that names Helm).

Nothing is assessed again by this: the reason is shown, and the user
re-assesses that job with a click (or answers the existing "old assessments"
question).  An assessment made on a profile's resume before the master was
read is compared by its resume alone: a master line added later is seen by
the assessments made since.

WHICH MASTER LINES AN ASSESSMENT CITES (``MasterCitations``, ``cited_requirements``; 0110-10-15).  A row's
evidence quotes are traced to the lines of the master AS IT IS NOW by the same two rules: word for word (a piece
of the quote of at least ``CITED_QUOTE_CHARS`` characters is in the line), else as a paraphrase (the shared-word
rule above).  The selector reads the result (``master_selection.SelectionPosting.cited``): a requirement the
assessment met with a line keeps that line in the resume, whatever words the two share.  A quote that traces to
no line now (the line was retired or reworded since) cites nothing, and a row that cites nothing is not passed.

Pure except ``assess_input`` / ``ResumeCheck`` / ``stored_citations`` (committed journal reads: the
stored master, kept per journal head by ``tailor_master.stored_master``; an
earlier revision of the master or of a resume, read once per process, since a
revision never changes).  No model call here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path
import re
import threading

from .find_jobs.assess_contracts import RESUME_INPUT_EVIDENCE, ResumeBasis
from .master_resume import KIND_SKILLS, Master, skill_names
from .master_selection import EVIDENCE_CAP, CitedRequirement, EvidenceView, SelectionPosting, SelectionProfile, evidence_view

INPUT_VIEW = "view"
INPUT_EVIDENCE = RESUME_INPUT_EVIDENCE
ASSESS_INPUTS: tuple[str, ...] = (INPUT_VIEW, INPUT_EVIDENCE)
#: What an assessment of a profile reads once a master is stored (operator decision 4: the evidence view, switched
#: on only after the P7 live eval). That eval (2026-10-04, ``tests/evals/run_master_assess_eval.py``, 11 synthetic
#: cases x 2 passes a side) found no regression: no invented evidence, no invalid answer, no case with a worse
#: verdict, 6 open questions against 63. ``INPUT_VIEW`` puts every assessment back on the profile's own resume.
ASSESS_INPUT = INPUT_EVIDENCE

CHANGE_LINE = "line_changed"
CHANGE_NEW_LINE = "new_line"
#: Evidence drew on a line when they share at least this many words ...
_SHARED_WORDS = 3
#: ... and those are at least this share of the evidence's words (a model often joins two lines in one sentence).
_SHARED_SHARE = 0.4
#: A piece of an evidence quote names a master line word for word only when it is at least this long (a shorter
#: piece is in many lines).
CITED_QUOTE_CHARS = 16
#: Question categories no line of a resume answers (the candidate's settings do): never a ``new_line``.
_NOT_A_RESUME_QUESTION = frozenset({"location", "sponsorship", "visa", "authorization", "work_authorization", "eligibility", "compensation", "salary"})
_UNREAD = object()


# --- what an assessment reads ---------------------------------------------------------------------


def profile_prior(*, titles: tuple[str, ...], item_ids: tuple[str, ...] | None, profile_id: str | None = None, label: str = "") -> SelectionProfile:
    """The profile as the selector's prior: the lines its selection shows, else its titles (as a tailoring reads it)."""

    return SelectionProfile(titles=tuple(titles), base_ids=None if item_ids is None else tuple(item_ids), profile_id=profile_id, label=label)


def evidence_text(
    master: Master, prior: SelectionProfile, *, title: str, posting_text: str, company: str = "", location: str = "", today: date | None = None,
) -> EvidenceView:
    """The evidence view one assessment of this posting reads: its ``markdown`` is the prompt's RESUME. Pure.

    Never longer than the assess prompt's resume cap, so the prompt cuts
    nothing off it.
    """

    return evidence_view(master, prior, SelectionPosting(title, posting_text, company, location), today=today, cap=EVIDENCE_CAP)


@dataclass(frozen=True)
class AssessInput:
    """What one assessment reads in place of the profile's resume: the evidence view, and what the record says of it."""

    view: EvidenceView
    basis: ResumeBasis

    @property
    def resume_text(self) -> str:
        return self.view.markdown


def reads_evidence(home_root: Path, profile: object) -> bool:
    """Whether an assessment of this profile reads the evidence view when a master is stored (the switch, and not detached)."""

    from .tailor_master import detached

    return ASSESS_INPUT == INPUT_EVIDENCE and not detached(home_root, profile)


def assess_input(
    *, home_root: Path, target: Path, profile: object | None, title: str, posting_text: str, company: str = "", location: str = "",
    resolved: object | None = None, today: date | None = None,
) -> AssessInput | None:
    """The evidence view this assessment reads, or ``None`` when it reads the profile's resume as before.

    ``None``: the switch is on the profile's view, the resume is not a
    profile's (pasted text), no master is stored, or the profile's resume was
    replaced by hand after its selection.  The profile's prior is the lines
    its selection shows, else its titles.
    """

    if ASSESS_INPUT != INPUT_EVIDENCE or profile is None:
        return None
    from .tailor_master import stored_master

    stored = stored_master(home_root, target, resolved=resolved)
    if stored is None or not reads_evidence(home_root, profile):
        return None
    selection = getattr(profile, "master_selection", None)
    prior = profile_prior(
        titles=tuple(profile.titles),  # type: ignore[attr-defined]
        item_ids=tuple(selection.item_ids) if selection is not None else None,
        profile_id=profile.profile_id,  # type: ignore[attr-defined]
        label=profile.label,  # type: ignore[attr-defined]
    )
    revision = stored.revision  # type: ignore[attr-defined]
    view = evidence_text(stored.master, prior, title=title, posting_text=posting_text, company=company, location=location, today=today)  # type: ignore[attr-defined]
    return AssessInput(view, ResumeBasis(INPUT_EVIDENCE, revision.revision_id, revision.revision, view.selector_version))


# --- the lines of a resume, as they compare ---------------------------------------------------------

_MARKER = re.compile(r"\A(?:[-*•–]\s+|\d+[.)]\s+|#{1,6}\s+)")
_HEADING = re.compile(r"\A(?:#{1,6}\s+(?P<hash>.+?)|\*\*(?P<bold>[^*]+)\*\*:?|(?P<caps>[A-Z][A-Z &/]{2,40}))\s*\Z")
_COMMENT = re.compile(r"<!--.*?-->")
_SKILL_PREFIX = "skill: "
_SKILL_WORDS = frozenset({"skill"})
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _flat(text: str) -> str:
    return " ".join(re.sub(r"[*_`\"'“”‘’]", "", text).split()).casefold().strip(" .…")


def _words(text: str) -> frozenset[str]:
    from . import story_bank

    return story_bank._tokens(text)  # noqa: SLF001 - the one word rule the story bank's stale check uses


class ResumeLines:
    """A resume's or a master's lines, flattened for comparing: each line once, a Skills line as one entry per skill.

    Built once per revision and kept (``ResumeCheck``): what a quote is
    compared with (the joined text, each line's words and numbers) is worked
    out on first use and then reused for every assessment.
    """

    def __init__(self, lines: Iterable[str]) -> None:
        self.lines: tuple[str, ...] = tuple(dict.fromkeys(lines))
        self.keys: frozenset[str] = frozenset(self.lines)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ResumeLines) and self.lines == other.lines

    def __hash__(self) -> int:
        return hash(self.lines)

    @cached_property
    def _text(self) -> str:
        return "\n".join(self.lines)

    @cached_property
    def _facts(self) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
        """Each line's words and the numbers it states."""

        return {line: (_words(line), frozenset(_NUMBER.findall(line))) for line in self.lines}

    def quotes(self, quote: str) -> bool:
        """Whether these lines hold the quote word for word (spacing and case aside; ``...`` joins two pieces)."""

        pieces = _pieces(quote)
        return bool(pieces) and all(piece in self._text for piece in pieces)

    def backs(self, shared: frozenset[str], numbers: frozenset[str], added: frozenset[str]) -> bool:
        """Whether a line holds the words ``shared`` (what the evidence took from a line that went away) and ``numbers``.

        A line that is new (``added``: the edited wording of that line) may
        lack one of the words, so a fixed typo is not a change.
        """

        wanted = len(shared)
        lenient = max(2, wanted - 1) if wanted >= _SHARED_WORDS else wanted
        return any(
            len(shared & line_words) >= (lenient if line in added else wanted) and numbers <= line_numbers
            for line, (line_words, line_numbers) in self._facts.items()
        )


def _pieces(quote: str) -> list[str]:
    flat = _flat(quote)
    return [piece.strip(" .") for piece in re.split(r"\.\.\.|…", flat) if piece.strip(" .")]


def _lost(quote: str, then: ResumeLines, now: ResumeLines, gone: Sequence[str], added: frozenset[str]) -> bool:
    """Whether evidence ``quote`` drew on a line of ``then`` that is ``gone`` and no line of ``now`` backs it as well.

    It drew on a gone line when that line holds it word for word, or, as a
    paraphrase, when they share ``_SHARED_WORDS`` words that are
    ``_SHARED_SHARE`` of its own. It is still backed when a line now holds
    those same words and the numbers it took from the gone line: an edited
    number is a change, a fixed typo is not. A skill it names is lost
    when the skill is gone and no line names it.
    """

    verbatim = then.quotes(quote)
    if verbatim and now.quotes(quote):
        return False
    pieces = _pieces(quote)
    words = _words(quote)
    numbers = frozenset(_NUMBER.findall(_flat(quote)))
    for line in gone:
        line_words, line_numbers = then._facts[line]  # noqa: SLF001 - the same module's kept words
        if line.startswith(_SKILL_PREFIX):
            named = line_words - _SKILL_WORDS
            if named and named <= words and line[len(_SKILL_PREFIX):] not in now._text:  # noqa: SLF001
                return True
            continue
        shared = words & line_words
        held = verbatim and any(piece in line for piece in pieces)
        if not held and not (len(shared) >= _SHARED_WORDS and len(shared) / len(words) >= _SHARED_SHARE):
            continue
        if not shared or not now.backs(shared, numbers & line_numbers, added):
            return True
    return False


def resume_lines(markdown: str) -> ResumeLines:
    """The lines of resume markdown (GigAI's format or another shape), as they compare. Pure."""

    out: list[str] = []
    in_skills = False
    for raw in markdown.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        heading = _HEADING.match(line)
        if heading is not None and not line.startswith("###"):
            name = heading.group("hash") or heading.group("bold") or heading.group("caps") or ""
            in_skills = "skill" in name.casefold()
            continue
        text = _MARKER.sub("", line)
        if in_skills:
            out.extend(_SKILL_PREFIX + _flat(name) for name in skill_names(text)[1])
            continue
        flat = _flat(text)
        if flat:
            out.append(flat)
    return ResumeLines(out)


def master_lines(master: Master) -> ResumeLines:
    """Every line of a master, as ``resume_lines`` reads a resume made of them."""

    return resume_lines(master.markdown(ids=False))


# --- which master lines an assessment cites -----------------------------------------------------------


class MasterCitations:
    """The lines of one master, kept flat, so the evidence quotes of many assessment rows are traced to them.

    Every line but the Skills lines can be cited (a summary, a bullet, a line under a degree, an Other line).
    """

    def __init__(self, master: Master) -> None:
        self._lines = [(item.id, _flat(item.text), _words(item.text)) for item in master.items.values() if item.kind != KIND_SKILLS]
        self._skills = [(name, _flat(name)) for name in master.skills()]

    def quote(self, quote: str) -> tuple[frozenset[str], frozenset[str]]:
        """``(master line ids, skill names)`` one evidence quote cites.

        Word for word first; else as a paraphrase by shared words; a skill name only when no line is cited.
        """

        pieces = [piece for piece in _pieces(quote) if len(piece) >= CITED_QUOTE_CHARS]
        found = {item_id for item_id, flat, _line_words in self._lines if any(piece in flat for piece in pieces)}
        words = _words(quote)
        if not found and words:
            for item_id, _flat_line, line_words in self._lines:
                shared = words & line_words
                if len(shared) >= _SHARED_WORDS and len(shared) / len(words) >= _SHARED_SHARE:
                    found.add(item_id)
        if found:
            return frozenset(found), frozenset()
        flat_quote = _flat(quote)
        return frozenset(), frozenset(name for name, flat in self._skills if flat and flat in flat_quote)

    def row(self, evidence: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """``(line ids, skill names)`` all the evidence of one row cites; the skills only when it cites no line."""

        lines: set[str] = set()
        skills: set[str] = set()
        for quote in evidence:
            found, named = self.quote(quote)
            lines |= found
            skills |= named
        return tuple(sorted(lines)), (() if lines else tuple(sorted(skills)))


def cited_requirements(master: Master, matrix: Iterable[object], *, citations: MasterCitations | None = None) -> tuple[CitedRequirement, ...]:
    """The rows of an assessment's matrix that cite master lines, as the selector reads them. Pure.

    ``matrix``: ``RequirementMatrixRow`` items (``requirement``, ``resume_evidence``, ``status``,
    ``requirement_class``).  A row's id is its place in the matrix (``r<n>``, from 1); it is mandatory unless
    its class is ``nice_to_have``, and met when its status is ``met``.  A row that cites no line is left out.
    """

    from .find_jobs.contracts import MatrixStatus, RequirementClass

    citations = citations or MasterCitations(master)
    out: list[CitedRequirement] = []
    for place, row in enumerate(matrix, 1):
        lines, _skills = citations.row(row.resume_evidence)  # type: ignore[attr-defined]
        if lines:
            out.append(CitedRequirement(
                f"r{place}", row.requirement, row.requirement_class != RequirementClass.NICE_TO_HAVE, lines,  # type: ignore[attr-defined]
                met=row.status == MatrixStatus.MET,  # type: ignore[attr-defined]
            ))
    return tuple(out)


def stored_citations(home_root: Path, target: Path, master: Master, profile_id: str | None, job_identity: str | None) -> tuple[CitedRequirement, ...]:
    """What the stored (base) assessment of ``job_identity`` for ``profile_id`` cites of ``master``; ``()`` without one.

    One small file read (``quick_assess.read_quick_assessment``), no journal read and no model.  An assessment
    that cannot be read is no assessment: the selection is then made by words, as before.
    """

    if profile_id is None or not job_identity:
        return ()
    from .quick_assess import read_quick_assessment

    try:
        stored = read_quick_assessment(home_root, target, profile_id, job_identity)
    except (OSError, ValueError, RuntimeError):
        return ()  # no project is bound here: there is no assessment either
    if stored is None:
        return ()
    return cited_requirements(master, stored.result.matrix)


# --- the targeted stale rule -------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumeChange:
    """Why the resume makes ONE assessment stale.

    ``line_changed``: a line it quoted for ``requirement`` is not there any
    more.  ``new_line``: a line that was not there names the subject of its
    open ``question_id``.  The assessment's own requirement and question words
    only: never a line of the resume.
    """

    change: str
    requirement: str = ""
    question_id: str | None = None
    question: str = ""

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"change": self.change}
        if self.requirement:
            value["requirement"] = self.requirement
        if self.question_id is not None:
            value["question_id"] = self.question_id
            if self.question:
                value["question"] = self.question
        return value


def _subject(question_id: str) -> frozenset[str]:
    """The words a line must name to answer this question (``tooling:helm`` -> helm); empty: no line of a resume answers it."""

    from .question_ids import normalize_question_id

    normalized = normalize_question_id(question_id)
    category, _, value = normalized.partition(":")
    if value and category in _NOT_A_RESUME_QUESTION:
        return frozenset()
    return _words(value or normalized)


def resume_changes(*, matrix: Iterable[object], questions: Iterable[object], then: ResumeLines, now: ResumeLines) -> tuple[ResumeChange, ...]:
    """The changes between ``then`` and ``now`` that concern ONE assessment; empty: the resume leaves it current. Pure.

    ``matrix`` are its rows (``requirement``, ``resume_evidence``) and
    ``questions`` the questions it left open (``question_id``, ``question``).
    See the module text for the two rules.
    """

    from . import story_bank

    if then.keys == now.keys:
        return ()
    found: list[ResumeChange] = []
    # A quote can only have lost its line when a line is gone; a question can only be answered by a line that is new.
    gone = [line for line in then.lines if line not in now.keys]
    added = now.keys - then.keys
    if gone:
        for row in matrix:
            quotes = [quote for quote in getattr(row, "resume_evidence", ()) or () if quote.strip() and not story_bank.cited_ids([quote])]
            if any(_lost(quote, then, now, gone, added) for quote in quotes):
                found.append(ResumeChange(CHANGE_LINE, requirement=str(getattr(row, "requirement", "") or "")))
    asked = [(str(getattr(item, "question_id", "") or ""), str(getattr(item, "question", "") or "")) for item in questions]
    asked = [(question_id, question) for question_id, question in asked if question_id]
    new = [_words(line) for line in now.lines if line in added] if asked else []
    if new:
        for question_id, question in asked:
            subject = _subject(question_id)
            if subject and any(subject <= words for words in new):
                found.append(ResumeChange(CHANGE_NEW_LINE, question_id=question_id, question=story_bank.one_line(question)))
    return tuple(found)


# --- one request's reads -----------------------------------------------------------------------------

_LOCK = threading.Lock()
#: (workpad path, revision id) -> the lines of that revision of the master or of a resume. A revision never changes.
_revision_lines: dict[tuple[str, str], ResumeLines] = {}


class ResumeCheck:
    """One request's ``resume_changed`` check over stored assessments (``assessment_basis.BasisCheck`` owns one).

    Without a master stored, ``changes`` answers ``()`` after one kept read.
    ``profiles`` answers the profile record of an id (the caller's kept read).
    """

    def __init__(self, *, home_root: Path, target: Path, resolved: object, profile: Callable[[str], object | None]) -> None:
        self._home_root = Path(home_root)
        self._target = Path(target)
        self._resolved = resolved
        self._profile = profile
        self._stored: object = _UNREAD
        self._now_master: ResumeLines | None = None
        self._now_resume: dict[str, ResumeLines | None] = {}
        self._evidence_now: dict[str, bool] = {}

    def _master(self):
        if self._stored is _UNREAD:
            from .tailor_master import stored_master

            self._stored = stored_master(self._home_root, self._target, resolved=self._resolved)
        return self._stored

    def _kept(self, revision_id: str, read) -> ResumeLines | None:
        key = (str(self._resolved.path), revision_id)  # type: ignore[attr-defined]
        with _LOCK:
            found = _revision_lines.get(key)
        if found is None:
            found = read()
            if found is not None:
                with _LOCK:
                    _revision_lines[key] = found
        return found

    def _master_then(self, basis: ResumeBasis) -> ResumeLines | None:
        from .master_store import load_master

        def read() -> ResumeLines | None:
            earlier = load_master(home_root=self._home_root, target=self._target, gig_id=self._resolved.gig_id, revision=basis.master_revision)  # type: ignore[attr-defined]
            if earlier is None or earlier.revision.revision_id != basis.master_revision_id:
                return None
            return master_lines(earlier.master)

        return self._kept(basis.master_revision_id, read)

    def _resume(self, pinned: object) -> ResumeLines | None:
        from .proposal_execution import read_pinned_resume

        def read() -> ResumeLines | None:
            content = read_pinned_resume(self._home_root, self._target, self._resolved.gig_id, pinned)  # type: ignore[attr-defined]
            return resume_lines(content.decode("utf-8", errors="replace"))

        return self._kept(pinned.revision_id, read)  # type: ignore[attr-defined]

    def changes(self, item: object) -> tuple[ResumeChange, ...]:
        """Why the resume makes ``item`` (a stored ``AssessResponse``) stale; ``()``: it does not, or it cannot be said."""

        resume = item.resume  # type: ignore[attr-defined]
        if resume.profile_id is None or resume.pinned is None:
            return ()
        stored = self._master()
        if stored is None:
            return ()
        profile = self._profile(resume.profile_id)
        if profile is None:
            return ()
        if resume.profile_id not in self._evidence_now:
            self._evidence_now[resume.profile_id] = reads_evidence(self._home_root, profile)
        evidence_now = self._evidence_now[resume.profile_id]
        basis: ResumeBasis | None = item.resume_basis  # type: ignore[attr-defined]
        if basis is not None:
            if evidence_now and basis.master_revision_id == stored.revision.revision_id:  # type: ignore[attr-defined]
                return ()
            then = self._master_then(basis)
        else:
            from .contact_cleanup import same_resume_revision

            if same_resume_revision(self._home_root, resume.pinned.revision_id, profile.resume_ref.revision_id):
                return ()
            then = self._resume(resume.pinned)
        if then is None:
            return ()
        if evidence_now:
            if self._now_master is None:
                self._now_master = master_lines(stored.master)  # type: ignore[attr-defined]
            now: ResumeLines | None = self._now_master
        else:
            if resume.profile_id not in self._now_resume:
                self._now_resume[resume.profile_id] = self._resume(profile.resume_ref)
            now = self._now_resume[resume.profile_id]
        if now is None:
            return ()
        result = item.result  # type: ignore[attr-defined]
        return resume_changes(matrix=result.matrix, questions=result.structured_questions, then=then, now=now)


__all__ = [
    "ASSESS_INPUT",
    "ASSESS_INPUTS",
    "CHANGE_LINE",
    "CHANGE_NEW_LINE",
    "CITED_QUOTE_CHARS",
    "INPUT_EVIDENCE",
    "INPUT_VIEW",
    "AssessInput",
    "MasterCitations",
    "ResumeChange",
    "ResumeCheck",
    "ResumeLines",
    "assess_input",
    "cited_requirements",
    "evidence_text",
    "master_lines",
    "profile_prior",
    "reads_evidence",
    "resume_changes",
    "resume_lines",
    "stored_citations",
]
