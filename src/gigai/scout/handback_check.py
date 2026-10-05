"""0.1.11 N2 (SPEC 5.3): the hand-back check, read against the MASTER resume.

THIS IS A GUARD, NOT PROOF.  It guards numbers, names, ownership, entries and sources.  It does not
prove that a reworded line is true: a line can pass every rule here and still stretch what its source
says (in the spike the prototype of these rules refused every planted invention and caught 1 of the 15
real stretches that models wrote).  The human check is the job page, which shows every changed line
beside its master line with its sources and Restore.

What it reads (G1): the master as stored now, every line by id, plus the user's answers and the stories
that match the posting.  Not the profile's own 2-page resume (the 0.1.10 check read that and refused 16
of 16 sound hand-backs).  A profile with no master, or detached from it, keeps the 0.1.10 check
(``tailored_resume_edit.edited_result``); nothing here runs for it.

Line by line (``check_handback``), each refusal a code of ``CODES``:

- an entry heading and its title/dates lines are the master's, unchanged (``heading_not_master``);
- a line of the job's stored resume, unchanged and where the stored resume has it, keeps its stored sources;
- a master line, word for word, needs no citation and stands under its own entry (G3,
  ``line_under_wrong_entry``);
- any other line cites (G2) ``<!-- src: b-23b6dc, A tooling:temporal -->``: master line ids and/or
  ``A <question_id>`` / ``A story:<slug>`` (``no_source``, ``unknown_source``), and then:
  - its numbers are stated by a CITED source (``number_not_in_sources``);
  - its cited master lines belong to one entry, the one it stands under (G3: ``cross_entry``,
    ``line_under_wrong_entry``); a line that cites no line of the entry may stand under it only when a
    cited answer names the entry's company or project (A4, ``answer_names_no_role``);
  - a technology, product or organisation it names is named by a cited source, by another line of the
    same entry, or by the master's Skills (G5 at strictness (b), ``names_something_new``);
  - a leadership verb (``LEAD_FAMILIES``) is used by a cited source (G6, ``ownership_upgrade``);
  - a cited answer is read per CLAIM: it is split into sentences and clauses, and a term is supported
    only by a clause that does not deny it.  "No Cassandra in production; MongoDB at two employers"
    supports MongoDB and refuses Cassandra (``answer_says_no``, naming the term);
  - a cited source that hedges a term (``familiar``, ``exposure``, ``basic``, ``some``, ``learning``,
    ``coursework``) does not support that term stated plainly (``level_upgrade``);
- a Skills item: each part (``skill_groups.skill_parts``: split on ``/``, ``,``, the middle dot and
  parentheses) is a part of a master skill, or stated by a clause of an answer that does not deny it
  (G8, ``skill_not_stated``); the master's own Skills lines always pass;
- any line the writer wrote: no name, no contact detail (``personal_info_refused``).

The whole resume (G7): roles newest first (``roles_out_of_order``), no entry without a line
(``empty_entry``).  The page limit (``over_page_limit``) is measured by the caller, which owns the
renderer; ``over_page_limit`` here only words it.

Word matching (G9) goes through the selector's stem (``master_selection._stem``): "fine-tuned" and
"fine-tuning", "service" and "microservices", "API" and "APIs" are one word.

Honest refusals (G10): every problem is ``{line, code, what, fix}``, all reported at once, by the
markdown's line number and the one word, number or id, never the line's text.  ``fix`` is the true fix
for that code; "save an answer that states it" is offered for ``skill_not_stated`` only.

HEURISTICS, said plainly: what counts as a "name" (a technical-looking or Capitalized token that does
not open a sentence), the clause split of a source (sentence ends, ``;`` and contrast words; never a
comma), the scope of a "no" and of a hedge word (everything after it in its clause, or the whole
clause when the clause ends on it), what "names" an entry in an answer (a part of its heading that no
other entry's heading has) and the hedge words themselves are rules of thumb.  A "no" errs toward
refusing; a name at the start of a sentence, a lowercase word and a paraphrase are not seen at all.
Each wrong refusal costs the writer a round, and each rule of thumb is in the packet's NOT PROVEN.

Pure: no file, no model, no renderer.  ``tailored_resume_edit`` parses the markdown, calls this, and
turns the answer into stored lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from functools import lru_cache
import re

from .master_resume import KIND_SKILLS, Master, MasterEntry, MasterItem
from .master_selection import _stem
from .posting_keywords import alias_table
from .skill_groups import skill_parts
from .tailor_no_loss import _FUNCTION_WORDS, _NAME_WITH_DIGITS, _OWNERSHIP, normalize
from .tailor_skills import _NEGATION, _SENTENCE
from .tailored_resume import (
    ENTRY_SECTIONS,
    LENGTH_RULE,
    TERM_ALIASES,
    TERM_STOP_WORDS,
    AnswerSource,
    _TERM_TOKEN,
    _clean_token,
    _display,
    _is_technical,
    canonical_term,
    personal_info_found,
    unsupported_numbers,
)

#: What the check is, in the words the docs, the brief and a report use (SPEC 5.3, Codex item 6).
WHAT_THE_CHECK_IS = (
    "This check is a guard on numbers, names, ownership, entries and sources. "
    "It does not prove that a reworded line is true."
)

#: Every refusal code, in the order of SPEC 5.3's table.
CODES: tuple[str, ...] = (
    "line_under_wrong_entry", "heading_not_master", "no_source", "unknown_source", "number_not_in_sources", "cross_entry",
    "names_something_new", "ownership_upgrade", "answer_says_no", "level_upgrade", "answer_names_no_role", "skill_not_stated",
    "personal_info_refused", "roles_out_of_order", "empty_entry", "over_page_limit",
)

#: The ownership families that are claims about the person's role (``tailor_no_loss.OWNERSHIP_FAMILIES`` also has
#: build/design/create/launch: verbs an honest reword may swap among, so only the leadership ones are held).
LEAD_FAMILIES: frozenset[str] = frozenset(
    {"own", "lead", "architect", "found", "head", "manage", "direct", "establish", "pioneer", "sole", "hire", "mentor"}
)
#: The words that state a term at a lower level than a plain claim does (``_HEDGE`` reads them: "some of", "machine
#: learning" and "Visual Basic" hedge nothing).
HEDGES: tuple[str, ...] = ("familiar", "exposure", "basic", "some", "learning", "coursework")

_COMMENTS = re.compile(r"<!--(.*?)-->", re.DOTALL)
_SRC = re.compile(r"\A\s*src\s*:\s*(.*?)\s*\Z", re.IGNORECASE | re.DOTALL)
_HEDGE_WORD = (
    r"\b(?:familiar|familiarity|exposure|exposed|basics|coursework)\b|(?<!visual )\bbasic\b|\bsome\b(?!\s+of\b)"
    r"|(?:\A\W*|\b(?:currently|still|am|been|now|started|just|actively)\s+)learning\b"
)
_HEDGE = re.compile(_HEDGE_WORD, re.IGNORECASE)
#: A clause that ENDS on a hedge ("Kubernetes (basic)", "Go: familiar", "Rust coursework") hedges everything it names.
_ENDS_HEDGE = re.compile(r"(?:" + _HEDGE_WORD + r")\W*\Z", re.IGNORECASE)
#: A clause that ENDS on a bare no ("Temporal: no", "Kafka - never") denies everything it names.
_ENDS_NEGATIVE = re.compile(r"\b(?:no|none|never|not|nope|n/?a)\W*\Z", re.IGNORECASE)
#: Where one sentence turns: what stands after it is a claim of its own ("No, but I ran Temporal for two years").
_CONTRAST = re.compile(r"\s*,?\s+\b(?:but|however|though|although|whereas|yet|except)\b,?\s*", re.IGNORECASE)
#: "own", "lead" and "managed" where they claim nothing about the person: ``its own tools``, ``leading to``, ``managed service``.
_NOT_OWNERSHIP = re.compile(
    r"\b(?:its|their|his|her|our|my|your|\w+['’]s)\s+own\b|\bleading\s+to\b|\blead\s+times?\b|\b(?:self|fully|un)[- ]?managed\b"
    r"|\bmanaged\s+(?:service|services|cluster|clusters|database|databases|offering|offerings|kubernetes)\b",
    re.IGNORECASE,
)
#: A name's end in an entry heading: ``Acme Corp — Senior Engineer (2019–2023)``, ``Loomhand: a personal workbench``.
_HEADING_PARTS = re.compile(r"\s+[—–-]\s+|\s*[|:(),]\s*|\s+(?:at|@)\s+")
_ID_PARTS = re.compile(r"[:_.\-\s]+")
#: Tokens that look technical to ``_is_technical`` (an inner dot) and name nothing.
_NOT_NAMES = frozenset({"e.g", "i.e", "etc", "vs", "a.k.a", "u.s", "u.k", "no", "ok"})
#: What an answer may say without stating anything ("No.", "Yes", "Not yet").
_FILLER = frozenset({"yes", "yeah", "yep", "not", "none", "never", "nope", "without", "yet", "n/a", "and", "the", "but"})
#: "services" names what "microservices" states only from this many letters up ("SQL" never names "PostgreSQL").
_MIN_TAIL = 5
_HEAD_PREFIXES = ("micro", "multi")
_SENTENCE_OPENERS = frozenset(".!?\n(\"'[;:")

STORED = "stored"
MASTER = "master"
REWORDED = "reworded"


# --- what the caller hands in ------------------------------------------------------------------


@dataclass(frozen=True)
class HandbackLine:
    """One logical line of the markdown: where it starts, what it says (no marker, no comment), what it cites."""

    number: int
    text: str
    cited: tuple[str, ...] = ()


@dataclass(frozen=True)
class HandbackEntry:
    heading: tuple[HandbackLine, ...]
    bullets: tuple[HandbackLine, ...]


@dataclass(frozen=True)
class HandbackSection:
    heading: str
    lines: tuple[HandbackLine, ...] = ()
    entries: tuple[HandbackEntry, ...] = ()


def cited_ids(trailing: str) -> tuple[str, ...]:
    """The ids a line cites: every ``<!-- src: b-23b6dc, A tooling:temporal -->`` among its trailing comments.

    Any other comment (``<!-- R12 -->`` of a stored file, ``<!-- id:b-23b6dc -->`` of the master) cites nothing."""

    ids: list[str] = []
    for body in _COMMENTS.findall(trailing):
        found = _SRC.match(body)
        if found is not None:
            ids += [" ".join(part.split()) for part in found.group(1).split(",") if part.strip()]
    return tuple(dict.fromkeys(ids))


def place_of(section: str, entry_heading: str | None = None) -> str:
    """Where a body line stands: under an entry (its heading as shown), or in a lines section."""

    return f"## {section}" if entry_heading is None else entry_heading


# --- what it answers -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    """One refusal: the markdown's line (``None``: the whole resume), the rule, the one word or number, the true fix."""

    line: int | None
    code: str
    what: str
    fix: str

    def to_json(self) -> dict[str, object]:
        return {"line": self.line, "code": self.code, "what": self.what, "fix": self.fix}


@dataclass(frozen=True)
class LineSource:
    """What one accepted body line is: a ``stored`` line kept, a ``master`` line word for word, or a ``reworded`` one.

    ``master_ids`` / ``answer_ids``: the master line it is (several: a paragraph of master lines, one after the
    other), or the sources a reworded line cites (a Skills line: the master Skills lines and the answers that
    state its parts, found by code)."""

    kind: str
    master_ids: tuple[str, ...] = ()
    answer_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Handback:
    """The check's answer: every problem (by line), and what each accepted line is, by its line number."""

    problems: tuple[Problem, ...]
    lines: Mapping[int, LineSource] = field(default_factory=dict)
    #: heading line number -> ``(master entry id, 0 for the heading or k for its k-th title/dates line)``.
    headings: Mapping[int, tuple[str, int]] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return not self.problems


_FIXES: Mapping[str, str] = {
    "heading_not_master": "copy the entry's heading and its title/dates line from your master resume, unchanged (`gigai scout resume master show`)",
    "no_source": (
        "this line is not in your master: add it with `gigai scout resume master add`, or cite the line it rewords "
        "(end the line with `<!-- src: <master line id> -->`)"
    ),
    "unknown_source": "cite a master line id (`gigai scout resume master show` lists them) or `A <question_id>` / `A story:<slug>` of an answer or a story you have",
    "number_not_in_sources": "keep the cited line's number, or cite the line that states this one",
    "cross_entry": "one line, one role or project: cite lines of one entry only",
    "line_under_wrong_entry": "put the line under its own role or project, or cite a line of the entry it stands under",
    "names_something_new": (
        "drop it, or cite the master line or the answer that names it; if it is true and your master does not say so, "
        "add it to your master first (`gigai scout resume master add`)"
    ),
    "ownership_upgrade": "keep the verb the cited line uses",
    "answer_says_no": "an answer that says no to a thing does not support that thing: drop it from the line, or cite a master line that states it",
    "level_upgrade": "keep the level the source states, or cite a line that states it plainly",
    "answer_names_no_role": "an answer goes under a role only when it names that role: cite a line of this entry too, or list the skill in the Skills section",
    "skill_not_stated": "save an answer that states it (`gigai scout answer`) or add it to your master's Skills, then hand the resume back again",
    "personal_info_refused": "GigAI stores no name or contact details: you type them in Scout's Generate PDF form when you make the PDF, never in a resume line",
    "roles_out_of_order": "put the roles in date order, newest first",
    "empty_entry": "show at least one of its lines, or leave the entry out",
    "over_page_limit": (
        f"cut lines until it prints on {LENGTH_RULE.max_pages} pages "
        "(`gigai scout resume pdf --in FILE --out FILE.pdf --json` prints `pages`)"
    ),
}


def problem(line: int | None, code: str, what: str) -> Problem:
    return Problem(line, code, what, _FIXES[code])


def over_page_limit(pages: int) -> Problem:
    """The whole-resume refusal for a resume the caller measured at more than the page limit."""

    return problem(None, "over_page_limit", f"{pages} pages; at most {LENGTH_RULE.max_pages}")


def refusal_text(problems: Sequence[Problem]) -> str:
    """The refusal as one text: every problem, by line number.  Never a line's text."""

    ordered = sorted(problems, key=lambda item: (item.line is None, item.line or 0))
    count = len(ordered)
    out = [f"not stored: {count} problem{'' if count == 1 else 's'} in the resume you handed back (nothing was changed)."]
    for item in ordered:
        where = "the whole resume" if item.line is None else f"line {item.line}"
        out.append(f"{where}: {item.code}: {item.what}. Fix: {item.fix}.")
    return "\n".join(out)


# --- the master, numbered like a resume ----------------------------------------------------------


@dataclass(frozen=True)
class MasterLines:
    """The whole master numbered as ``resume_lines(master.markdown(ids=False))`` numbers it: what a stored ref's ``line`` counts."""

    lines: tuple[str, ...]
    #: master line id -> its number.
    items: Mapping[str, int]
    #: master entry id -> the numbers of its heading and of each title/dates line under it.
    entries: Mapping[str, tuple[int, ...]]

    def text(self, number: int) -> str:
        return self.lines[number - 1]


def master_lines(master: Master) -> MasterLines:
    lines: list[str] = []
    items: dict[str, int] = {}
    entries: dict[str, tuple[int, ...]] = {}
    for section in master.sections:
        lines.append(f"## {section.capitalize()}")
        if section in ENTRY_SECTIONS:
            for entry in master.entries_in(section):
                first = len(lines) + 1
                lines += [f"### {entry.heading}", *entry.sublines]
                entries[entry.id] = tuple(range(first, len(lines) + 1))
                for item_id in entry.bullets:
                    lines.append(f"- {master.items[item_id].text}")
                    items[item_id] = len(lines)
        else:
            for item in master.in_section(section):
                lines.append(f"- {item.text}")
                items[item.id] = len(lines)
    return MasterLines(tuple(lines), items, entries)


# --- words (G9) ------------------------------------------------------------------------------------


def shown(text: str) -> str:
    """A line as it compares: no markdown marker, whitespace collapsed."""

    return " ".join(_display(text).split())


def _root(word: str) -> str:
    """One form for the forms of a word, through the selector's stem: ``APIs`` is ``api``, ``fine-tuned`` is ``fine-tun``."""

    lowered = canonical_term(word)
    if len(lowered) > 3 and lowered.endswith("s") and not lowered.endswith("ss"):
        lowered = lowered[:-1]
    return _stem(lowered)


def term_keys(word: str) -> frozenset[str]:
    """What one word matches under: its canonical spelling (aliases folded: ``k8s`` is ``kubernetes``) and its stem."""

    return frozenset({canonical_term(word), _root(word)} - {""})


def _units(token: str) -> list[str]:
    """A token, the parts of a slash-joined one (``OpenAI/Anthropic``) and the parts of a hyphenated one (``tool-calling``)."""

    out = [token]
    for part in token.split("/") if "/" in token else (token,):
        if part and part != token:
            out.append(part)
        if "-" in part:
            out += [piece for piece in part.split("-") if piece]
    return out


def _words(text: str) -> list[tuple[int, str]]:
    """``(offset, word)`` for every word of ``text``; a compound also counts by its parts."""

    out: list[tuple[int, str]] = []
    for match in _TERM_TOKEN.finditer(text):
        token = _clean_token(match.group(0))
        if token:
            out += [(match.start(), unit) for unit in _units(token)]
    return out


def text_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for _offset, word in _words(text):
        keys |= term_keys(word)
    return keys


def _named(keys: Iterable[str], have: Set[str]) -> bool:
    """True when ``have`` (the keys of some source text) states a word with ``keys``, inflections included.

    Also "services" for a source's "microservices" (the source's longer compound ends in the word), and the
    other way round for ``micro``/``multi`` only: "Search" never names what "Elasticsearch" does not state."""

    keys = tuple(keys)
    if any(key in have for key in keys):
        return True
    for key in keys:
        if len(key) >= _MIN_TAIL and any(len(other) > len(key) and other.endswith(key) for other in have):
            return True
        for prefix in _HEAD_PREFIXES:
            if key.startswith(prefix) and len(key) - len(prefix) >= _MIN_TAIL and key[len(prefix):].lstrip("-") in have:
                return True
    return False


@lru_cache(maxsize=1)
def _known_technology() -> frozenset[str]:
    """Every spelling the shipped tables know as a technology: what makes a sentence's FIRST word a name ("Swift guardrails ...")."""

    known = {spelling.casefold() for spelling in TERM_ALIASES}
    for term, aliases in alias_table().items():
        known |= {spelling.casefold() for spelling in (term, *aliases) if " " not in spelling}
    return frozenset(known)


def _opens_sentence(text: str, offset: int) -> bool:
    before = text[:offset].rstrip(" \t")
    return not before or before[-1] in _SENTENCE_OPENERS


def _is_name(word: str, *, opens: bool) -> bool:
    lowered = word.lower()
    if len(word) < 2 or lowered in TERM_STOP_WORDS or lowered in _NOT_NAMES or not word[0].isalpha():
        return False
    if _is_technical(word):
        return True
    if not word[0].isupper():
        return False
    return not opens or lowered in _known_technology()


def names_in(text: str) -> list[str]:
    """The technologies, products and organisations a line names, as written, each once (a rule of thumb: see the module text).

    A token that looks technical (a digit, ``+``, ``#``, an inner dot, an inner capital, all caps) or a
    Capitalized word that does not open a sentence; the first word of a sentence only when the shipped tables
    know it as a technology.  Each part of ``OpenAI/Anthropic`` and of ``Kafka-based`` is judged on its own."""

    found: dict[str, str] = {}
    for match in _TERM_TOKEN.finditer(text):
        token = _clean_token(match.group(0))
        if not token:
            continue
        opens = _opens_sentence(text, match.start())
        units = [token] if token.lower() in TERM_ALIASES or "/" not in token else [part for part in token.split("/") if part]
        for unit in units:
            pieces = [unit] if unit.lower() in TERM_ALIASES or "-" not in unit else [piece for piece in unit.split("-") if piece]
            for piece in pieces:
                piece = piece.strip(".")
                if _is_name(piece, opens=opens):
                    found.setdefault(piece.lower(), piece)
                opens = False
    return list(found.values())


# --- what a source states, clause by clause --------------------------------------------------------


def clauses(text: str) -> list[str]:
    """The sentences and clauses of ``text``: split at a sentence end, a ``;`` and a contrast word; never at a comma."""

    out: list[str] = []
    for sentence in _SENTENCE.split(text):
        out += [part for part in _CONTRAST.split(sentence) if part.strip()]
    return out


@dataclass(frozen=True)
class Stated:
    """What one source states, by word key: outright, only with a hedge, or only ever denied."""

    plain: frozenset[str]
    hedged: frozenset[str]
    denied: frozenset[str]
    #: Clause by clause, the keys it states outright and the ones it states with a hedge (a Skills part needs ONE clause).
    by_clause: tuple[tuple[frozenset[str], frozenset[str]], ...]
    #: The clauses that deny nothing, joined: where an ownership verb is read.
    affirming: str

    @property
    def says_nothing(self) -> bool:
        """True for an answer that only says no ("No, I have not used it"): it supports nothing."""

        return not any(len(key) >= 3 and key not in _FILLER for key in self.plain | self.hedged)


def stated(text: str, *, answer: bool = False, subject: str = "") -> Stated:
    """Read one source, clause by clause.

    A hedge word lowers what stands AFTER it in its clause ("some exposure to Terraform"), and the whole clause
    when the clause ends on it ("Kubernetes (basic)").  ``answer``: a "no" denies what stands after it in its
    clause, and the whole clause when the clause ends on it ("Temporal: no"); a master line denies nothing ("with
    no data loss" is a claim).  ``subject``: an answer's question id, whose words the FIRST clause is about: a
    clause with any "no" in it denies them ("I have not used it"), one with any hedge hedges them."""

    plain: set[str] = set()
    hedged: set[str] = set()
    denied: set[str] = set()
    by_clause: list[tuple[frozenset[str], frozenset[str]]] = []
    affirming: list[str] = []
    for index, clause in enumerate(clauses(text)):
        negations = [found.start() for found in _NEGATION.finditer(clause)] if answer else []
        all_no = bool(negations) and _ENDS_NEGATIVE.search(clause) is not None
        hedges = [found.start() for found in _HEDGE.finditer(clause)]
        all_hedged = bool(hedges) and _ENDS_HEDGE.search(clause) is not None
        yes: set[str] = set()
        low: set[str] = set()
        no: set[str] = set()
        for offset, word in _words(clause):
            if all_no or (negations and offset > negations[0]):
                no |= term_keys(word)
            elif all_hedged or (hedges and offset > hedges[0]):
                low |= term_keys(word)
            else:
                yes |= term_keys(word)
        if index == 0 and subject:
            about = {key for part in _ID_PARTS.split(subject) if part for key in term_keys(part)}
            (no if negations else low if hedges else yes).update(about)
        denied |= no
        hedged |= low
        plain |= yes
        by_clause.append((frozenset(yes), frozenset(low - yes)))
        if not negations:
            affirming.append(clause)
    return Stated(frozenset(plain), frozenset(hedged - plain), frozenset(denied - plain - hedged), tuple(by_clause), " ".join(affirming))


def _answer_stated(source: AnswerSource) -> Stated:
    return stated(source.answer, answer=True, subject=source.question_id)


def ownership_claims(text: str) -> dict[str, str]:
    """``family -> the word as written`` for every leadership verb ``text`` uses (``LEAD_FAMILIES``)."""

    flat = normalize(_NOT_OWNERSHIP.sub(" ", text))
    found: dict[str, str] = {}
    for name, pattern in _OWNERSHIP:
        match = pattern.search(flat) if name in LEAD_FAMILIES else None
        if match:
            found[name] = match.group(0).strip()
    return found


def _numbers_text(text: str) -> str:
    """``text`` without the names that carry a digit (``p95``, ``k8s``): the names rule owns them, as in ``tailor_no_loss``."""

    return _NAME_WITH_DIGITS.sub(" ", text)


# --- the check -------------------------------------------------------------------------------------


def _skill_words(part: str) -> list[str]:
    """The words of one skill, without function words; a hyphenated word stays whole (``fine-tuning``)."""

    words = [_clean_token(token) for token in _TERM_TOKEN.findall(part)]
    return [word for word in words if word and word.lower() not in _FUNCTION_WORDS]


def _skill_keys(part: str) -> set[tuple[str, ...]]:
    """One skill as it compares, by its words' stems: with a hyphenated word whole (``fine-tuning`` is ``fine-tuned``)
    and by its parts (``tool-calling`` is ``tool calling``)."""

    words = _skill_words(part)
    split = [piece for word in words for piece in ([word] if word.lower() in TERM_ALIASES else re.split(r"[-/]", word)) if piece]
    return {key for key in (tuple(_root(word) for word in words), tuple(_root(word) for word in split)) if key}


def _word_stated(word: str, have: Set[str]) -> bool:
    """True when ``have`` states one word of a skill: the word, or every part of a hyphenated one."""

    if _named(term_keys(word), have):
        return True
    pieces = [piece for piece in word.split("-") if piece]
    return len(pieces) > 1 and all(_named(term_keys(piece), have) for piece in pieces)


def _label_names(label: str) -> list[str]:
    """What a Skills line's label names: a label is a heading in Title Case ("Languages"), so only a token that
    looks technical or that the shipped tables know as a technology is a name there ("Rust and Go:")."""

    found: dict[str, str] = {}
    for _offset, word in _words(label):
        lowered = word.lower()
        if len(word) >= 2 and lowered not in TERM_STOP_WORDS and lowered not in _NOT_NAMES and (_is_technical(word) or lowered in _known_technology()):
            found.setdefault(lowered, word)
    return list(found.values())


class _Index:
    """The master as the check reads it: its lines by text, its entries by heading, what its Skills and each entry name."""

    def __init__(self, master: Master) -> None:
        self.master = master
        self.by_text: dict[str, list[MasterItem]] = {}
        for item in master.items.values():
            # As shown (markers and paired emphasis dropped) and as written: a hand-back may copy either.
            for text in dict.fromkeys((shown(item.text), " ".join(item.text.split()))):
                self.by_text.setdefault(text, []).append(item)
        self.by_heading: dict[str, list[MasterEntry]] = {}
        for entry in master.entries.values():
            self.by_heading.setdefault(shown(entry.heading), []).append(entry)
        #: every single skill of the master's Skills lines (``_skill_keys``) -> the line that lists it.
        self.skill_parts: dict[tuple[str, ...], str] = {}
        self.skill_keys: set[str] = set()
        self.all_keys: set[str] = set()
        for item in master.items.values():
            self.all_keys |= text_keys(item.text)
            if item.kind != KIND_SKILLS:
                continue
            self.skill_keys |= text_keys(item.text)
            for part in skill_parts(item.text)[1]:
                for key in _skill_keys(part):
                    self.skill_parts.setdefault(key, item.id)
        self._entry_keys: dict[str, set[str]] = {}
        heading_parts: dict[str, int] = {}
        for entry in master.entries.values():
            self.all_keys |= text_keys(" ".join((entry.heading, *entry.sublines)))
            for part in self._heading_parts(entry):
                heading_parts[part.casefold()] = heading_parts.get(part.casefold(), 0) + 1
        #: The parts of an entry's heading no other entry's heading has: what "names" the entry in an answer.
        self.entry_names: dict[str, tuple[str, ...]] = {
            entry.id: tuple(part for part in self._heading_parts(entry) if heading_parts[part.casefold()] == 1)
            for entry in master.entries.values()
        }

    @staticmethod
    def _heading_parts(entry: MasterEntry) -> list[str]:
        parts = [part.strip() for part in _HEADING_PARTS.split(shown(entry.heading))]
        return list(dict.fromkeys(part for part in parts if len(part) >= 3 and not any(char.isdigit() for char in part)))

    def entry_keys(self, entry_id: str) -> set[str]:
        """What the entry's own lines, heading and title/dates lines name."""

        if entry_id not in self._entry_keys:
            entry = self.master.entries[entry_id]
            texts = [entry.heading, *entry.sublines, *(self.master.items[item_id].text for item_id in entry.bullets)]
            self._entry_keys[entry_id] = text_keys(" ".join(texts))
        return self._entry_keys[entry_id]

    def entry_for(self, section: str, heading: HandbackLine, sublines: Sequence[HandbackLine]) -> MasterEntry | None:
        """The master entry a heading is: of the same section first, then the one whose title/dates lines these are."""

        found = self.by_heading.get(heading.text)
        if not found:
            return None
        wanted = {line.text for line in sublines}
        ranked = sorted(found, key=lambda entry: (entry.section != section, not wanted <= {shown(line) for line in entry.sublines}))
        return ranked[0]

    def paragraph(self, text: str) -> list[MasterItem] | None:
        """The master lines a paragraph is made of, one after the other, or ``None``.

        Plain lines with no blank line between them are one paragraph to the format's parser, so two master
        lines written that way arrive joined; each is still a master line, word for word."""

        found: list[MasterItem] = []
        rest = text
        while rest:
            known = max((known for known in self.by_text if rest == known or rest.startswith(known + " ")), key=len, default=None)
            if known is None:
                return None
            found.append(self.by_text[known][0])
            rest = rest[len(known):].lstrip()
        return found if len(found) > 1 else None

    def names_entry(self, text: str, entry: MasterEntry) -> bool:
        for name in self.entry_names.get(entry.id, ()):
            pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?![A-Za-z0-9])"
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False


class _Check:
    def __init__(self, master: Master, answers: Mapping[str, AnswerSource], stored: Set[tuple[str, str]]) -> None:
        self.index = _Index(master)
        self.master = master
        self.answers = answers
        self.stored = stored
        self.problems: list[Problem] = []
        self.lines: dict[int, LineSource] = {}
        self.headings: dict[int, tuple[str, int]] = {}
        self._answer_stated: dict[str, Stated] = {}

    def refuse(self, line: int | None, code: str, what: str) -> None:
        self.problems.append(problem(line, code, what))

    def answer(self, key: str) -> Stated:
        if key not in self._answer_stated:
            self._answer_stated[key] = _answer_stated(self.answers[key])
        return self._answer_stated[key]

    # -- headings and the whole resume

    def entry(self, section: str, entry: HandbackEntry) -> MasterEntry | None:
        if not entry.heading:
            return None
        heading, sublines = entry.heading[0], entry.heading[1:]
        found = self.index.entry_for(section, heading, sublines)
        if found is None:
            self.refuse(heading.number, "heading_not_master", "this entry heading is not an entry heading of your master resume")
            for line in sublines:
                if not any(line.text == shown(text) for other in self.master.entries.values() for text in other.sublines):
                    self.refuse(line.number, "heading_not_master", "this title or dates line is not a line of your master resume")
            return None
        if found.section != section:
            self.refuse(heading.number, "heading_not_master", f"your master resume has entry {found.id} under {found.section.capitalize()}, not under {section.capitalize()}")
        self.headings[heading.number] = (found.id, 0)
        own = [shown(text) for text in found.sublines]
        for line in sublines:
            if line.text in own:
                self.headings[line.number] = (found.id, own.index(line.text) + 1)
            else:
                self.refuse(line.number, "heading_not_master", f"this title or dates line is not the one your master resume has for entry {found.id}")
        if not entry.bullets and found.bullets:
            self.refuse(heading.number, "empty_entry", f"entry {found.id} has no line under it")
        return found

    def role_order(self, roles: Sequence[tuple[int, MasterEntry]]) -> None:
        """Roles newest first: by the year each ends (an ongoing one last ends never), then by the year it starts."""

        above: tuple[int, int] | None = None
        for number, entry in roles:
            if entry.start is None and not entry.ongoing:
                continue  # an entry that names no year has no place in time
            when = (9999 if entry.ongoing else (entry.end or 0), entry.start or 0)
            if above is not None and when > above:
                self.refuse(number, "roles_out_of_order", f"entry {entry.id} is newer than the role above it")
            above = when

    # -- one body line

    def body(self, line: HandbackLine, section: str, heading: str | None, entry: MasterEntry | None) -> None:
        """``heading``: the entry heading the line stands under (``None`` in a lines section); ``entry``: the master entry that is, when it is one."""

        if (place_of(section, heading), line.text) in self.stored:
            self.lines[line.number] = LineSource(STORED)
            return
        found = self.index.by_text.get(line.text)
        if found:
            item = next((item for item in found if entry is not None and item.entry_id == entry.id), found[0])
            if entry is not None and item.entry_id != entry.id:
                where = "belongs to another role or project" if item.entry_id else f"is a {item.section.capitalize()} line of your master resume"
                self.refuse(line.number, "line_under_wrong_entry", f"master line {item.id} {where}, not to entry {entry.id}")
                return
            self.lines[line.number] = LineSource(MASTER, (item.id,))
            return
        several = self.index.paragraph(line.text) if entry is None else None
        if several is not None:
            self.lines[line.number] = LineSource(MASTER, tuple(item.id for item in several))
            return
        personal = personal_info_found(line.text)
        # Two to four Capitalized words are name-shaped, and so is a Skills line such as "Apache Kafka": in Skills
        # the name shape alone decides nothing until the Skills rule has read the line.
        if personal and (section != "skills" or personal != ["name"]):
            self.refuse(line.number, "personal_info_refused", "a name or a contact detail")
            return
        before = len(self.problems)
        source = self.skills(line) if section == "skills" else self.cited(line, entry)
        if source is not None and len(self.problems) == before:
            self.lines[line.number] = source
        elif personal and len(self.problems) > before:
            # Name-shaped and no known skill: the name rule's refusal, so that a name is never repeated as "the skill ...".
            del self.problems[before:]
            self.refuse(line.number, "personal_info_refused", "a name or a contact detail")

    def _answer_key(self, ref: str) -> str | None:
        """The answer or story a citation ``A <id>`` names, as ``answers`` keys it; ``None`` when ``ref`` is not one or names none."""

        from .question_ids import normalize_question_id

        if ref[:2] not in ("A ", "a "):
            return None
        raw = ref[2:].strip()
        return next((key for key in (normalize_question_id(raw), raw) if key in self.answers), None)

    def _sources(self, line: HandbackLine) -> tuple[list[MasterItem], list[str]] | None:
        """The cited master lines and answer keys, or ``None`` (refused: no citation, or one that names nothing)."""

        if not line.cited:
            self.refuse(line.number, "no_source", "this line is not a line of your master resume and cites no source")
            return None
        items: list[MasterItem] = []
        keys: list[str] = []
        ok = True
        for ref in line.cited:
            if ref[:2] in ("A ", "a "):
                key = self._answer_key(ref)
                if key is not None:
                    keys.append(key)
                else:
                    ok = False
                    self.refuse(line.number, "unknown_source", f'"{ref}" is not an answer you gave or a story that matches this job')
                continue
            item = self.master.items.get(ref)
            if item is None:
                ok = False
                kind = "an entry, not a line" if ref in self.master.entries else "not a line id"
                self.refuse(line.number, "unknown_source", f'"{ref}" is {kind} of your master resume')
            else:
                items.append(item)
        # A citation that names nothing is fixed first: what the line may say depends on it.
        return (list(dict.fromkeys(items)), list(dict.fromkeys(keys))) if ok else None

    def cited(self, line: HandbackLine, entry: MasterEntry | None) -> LineSource | None:
        sources = self._sources(line)
        if sources is None:
            return None
        items, keys = sources
        labels = ", ".join([item.id for item in items] + [f"A {key}" for key in keys])
        answers = [self.answers[key] for key in keys]

        # G3: one line, one role or project, the one it stands under.
        owners = list(dict.fromkeys(item.entry_id for item in items if item.entry_id is not None))
        if len(owners) > 1:
            self.refuse(line.number, "cross_entry", f"this line cites lines of {len(owners)} roles or projects ({', '.join(owners)})")
        elif entry is not None and owners and owners[0] != entry.id:
            self.refuse(line.number, "line_under_wrong_entry", f"the cited lines belong to entry {owners[0]}, not to entry {entry.id}")
        elif entry is not None and not owners and not any(self.index.names_entry(answer.guard_text, entry) for answer in answers):
            if answers:
                self.refuse(line.number, "answer_names_no_role", f"no cited answer names entry {entry.id}'s company or project")
            else:
                self.refuse(line.number, "line_under_wrong_entry", f"none of the cited lines belongs to entry {entry.id}")

        # G2: every number is a cited source's.
        texts = [item.text for item in items] + [answer.guard_text for answer in answers]
        for mention in unsupported_numbers(_numbers_text(line.text), [_numbers_text(text) for text in texts]):
            self.refuse(line.number, "number_not_in_sources", f'the number "{mention.span}" is in none of the cited sources ({labels})')

        # G5, the per-claim answer rule and the level rule: what the line names.
        read = [stated(item.text) for item in items] + [self.answer(key) for key in keys]
        plain = set().union(*(source.plain for source in read))
        hedged = set().union(*(source.hedged for source in read)) - plain
        own = stated(line.text)
        nearby = set(self.index.skill_keys)
        for entry_id in ([entry.id] if entry is not None else owners):
            nearby |= self.index.entry_keys(entry_id)
        denying: set[str] = set()
        for name in names_in(line.text):
            wanted = term_keys(name)
            if _named(wanted, plain):
                continue
            if _named(wanted, hedged):
                if not wanted & own.hedged:
                    self.refuse(line.number, "level_upgrade", f'the cited sources state "{name}" only with a hedge (familiar, some, basic ...)')
                continue
            denier = next((key for key in keys if _named(wanted, self.answer(key).denied)), None)
            if denier is not None:
                denying.add(denier)
                self.refuse(line.number, "answer_says_no", f'"A {denier}" says no to "{name}"')
            elif not _named(wanted, nearby):
                self.refuse(line.number, "names_something_new", f'"{name}" is named by none of the cited sources ({labels}), by no other line of the entry and not by your master\'s Skills')
        for key in keys:
            if key not in denying and self.answer(key).says_nothing:
                about = _ID_PARTS.split(key)[-1] or key
                self.refuse(line.number, "answer_says_no", f'"A {key}" says no ("{about}"): it supports nothing')

        # G6: ownership as the sources state it.
        claimed = [ownership_claims(item.text) for item in items] + [ownership_claims(self.answer(key).affirming) for key in keys]
        for family, word in ownership_claims(line.text).items():
            if not any(family in source for source in claimed):
                self.refuse(line.number, "ownership_upgrade", f'"{word}" claims ownership that none of the cited sources state ({labels})')
        return LineSource(REWORDED, tuple(item.id for item in items), tuple(keys))

    def skills(self, line: HandbackLine) -> LineSource | None:
        """G8: every part of a Skills line is a part of a master skill, or stated by a clause of an answer that does not deny it."""

        label, parts = skill_parts(line.text)
        items: list[str] = []
        keys: list[str] = []
        # The answers the line cites are read first, then every other one: a Skills line needs no citation.
        cited = list(dict.fromkeys(key for key in map(self._answer_key, line.cited) if key is not None))
        order = cited + [key for key in self.answers if key not in cited]
        own_hedges = _HEDGE.search(line.text) is not None
        for name in _label_names(label):
            if not _named(term_keys(name), self.index.all_keys) and not any(_named(term_keys(name), self.answer(key).plain) for key in order):
                self.refuse(line.number, "skill_not_stated", f'the label names "{name}", which your master resume does not')
        for part in parts:
            words = _skill_words(part)
            if not words or all(_HEDGE.fullmatch(word) for word in words):
                continue  # "Rust (basic)": the level the line states is not a skill of its own
            listed = next((self.index.skill_parts[key] for key in _skill_keys(part) if key in self.index.skill_parts), None)
            if listed is not None:
                items.append(listed)
                continue
            # ONE clause states every word of the part: outright, or (the level rule) only with a hedge.
            plainly = next((a for a in order if any(all(_word_stated(word, yes) for word in words) for yes, _ in self.answer(a).by_clause)), None)
            if plainly is not None:
                keys.append(plainly)
                continue
            hedging = next((a for a in order if any(all(_word_stated(word, yes | low) for word in words) for yes, low in self.answer(a).by_clause)), None)
            if hedging is None:
                self.refuse(line.number, "skill_not_stated", f'the skill "{part}" is no part of your master\'s Skills and no answer states it')
            elif own_hedges:
                keys.append(hedging)
            else:
                self.refuse(line.number, "level_upgrade", f'"A {hedging}" states "{part}" only with a hedge (familiar, some, basic ...)')
        return LineSource(REWORDED, tuple(dict.fromkeys(items)), tuple(dict.fromkeys(keys)))


def check_handback(
    sections: Sequence[HandbackSection],
    master: Master,
    answers: Mapping[str, AnswerSource],
    *,
    stored: Set[tuple[str, str]] = frozenset(),
) -> Handback:
    """Check a handed-back resume against the master (see the module text).  A guard, not proof.  Pure.

    ``answers``: the user's answers and the stories that match the posting, by id (``tailor_sources``).
    ``stored``: ``(place_of(...), text)`` of every body line of the job's stored resume: such a line, unchanged
    and in the same place, keeps its stored sources and is not checked again.
    """

    check = _Check(master, answers, stored)
    for section in sections:
        if section.heading not in ENTRY_SECTIONS:
            for line in section.lines:
                check.body(line, section.heading, None, None)
            continue
        roles: list[tuple[int, MasterEntry]] = []
        for entry in section.entries:
            found = check.entry(section.heading, entry)
            heading = entry.heading[0].text if entry.heading else ""
            if found is not None and entry.heading:
                roles.append((entry.heading[0].number, found))
            for line in entry.bullets:
                if found is None:
                    # The heading is refused already; under an entry that is not the master's nothing can be placed.
                    if (place_of(section.heading, heading), line.text) in stored:
                        check.lines[line.number] = LineSource(STORED)
                    continue
                check.body(line, section.heading, heading, found)
        if section.heading == "experience":
            check.role_order(roles)
    problems = sorted(check.problems, key=lambda item: (item.line is None, item.line or 0))
    return Handback(tuple(problems), check.lines, check.headings)


__all__ = [
    "CODES",
    "HEDGES",
    "LEAD_FAMILIES",
    "MASTER",
    "REWORDED",
    "STORED",
    "WHAT_THE_CHECK_IS",
    "Handback",
    "HandbackEntry",
    "HandbackLine",
    "HandbackSection",
    "LineSource",
    "MasterLines",
    "Problem",
    "Stated",
    "check_handback",
    "cited_ids",
    "clauses",
    "master_lines",
    "names_in",
    "over_page_limit",
    "ownership_claims",
    "place_of",
    "problem",
    "refusal_text",
    "shown",
    "stated",
    "term_keys",
    "text_keys",
]
