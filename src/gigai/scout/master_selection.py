"""The master resume's selector (0.1.10.9 master P2): which lines a resume shows, by code alone.

``select`` picks the lines of the master (``master_resume.Master``) that one
resume shows, for a profile alone (its standing pick) or for one posting, and
fits the pick to ``LENGTH_RULE.max_pages`` pages. ``evidence_view`` picks the
lines most relevant to one posting under a character budget instead (what an
assessment would read). Nothing here calls a model, reads a file or writes:
the same master, profile, posting and day give the same ids.

The rules (0110-10-15, the pick's objective; ``SELECTOR_VERSION`` ``sel-5``).  For a posting the
selection maximises, in this order, and a lower term never buys back a higher one:

1. MANDATORY REQUIREMENT COVERAGE.  The posting's requirements are its list lines (``Requirement``; a
   line under a "nice to have" heading is not mandatory) and every must-have keyword no line names.  A
   master line SUPPORTS a requirement when it names a keyword the requirement names, or shares enough of
   its rarer words.  Every mandatory requirement the master can support keeps a supporting line.
2. EVIDENCE STRENGTH.  The line kept for a requirement is its strongest: among the lines that support it
   well, a backed line before a quantified one before a stated one.  That line is the requirement's
   EVIDENCE and the fit does not cut it while anything else can go.
3. MUST-KEEP LINES: the profile's pins (``SelectionProfile.pins``), cut only after everything else.
4. RECENCY only breaks ties between lines of equal coverage and strength.  An older role's line that is
   the evidence of a requirement stays while recent lines that support nothing are cut.

WHEN THE POSTING HAS BEEN ASSESSED (``SelectionPosting.cited``: the rows of the stored assessment of this
posting for this profile, each with the master lines its evidence cites, ``assess_master.cited_requirements``),
COVERAGE COMES FROM THE CITATIONS, not from words.  An assessment maps a requirement to lines BY MEANING: the
line it cites may share no word with the requirement, and a line that shares its words may not be what was
cited.  So a row that cites lines is a requirement whose supporters are exactly the lines it cites, the
strongest first (a line of ordinary length, then strength, then the line more rows cite, then recency); its
first line is its evidence.  Every row keeps one cited line: a met mandatory row's evidence is the last thing
any cut reaches (after every other requirement's evidence), a nice-to-have row's is kept like a nice-to-have's
first line.  Matching by words (1 above) only ADDS: it supports what the posting asks for that no cited row is
about (a row the assessment found no line for, a line of the posting it has no row for), and everything when
there is no assessment.  A posting line or keyword that a cited row is about is not matched by words at all.

WHAT IS LEFT OF THE PAGE (``sel-4``), once every requirement has its line and the pins are in, goes to the lines
that are ABOUT THE POSTING before the lines that are only strong or recent.  THE POSTING'S TITLE says what the job is
about (its words without rank, place or level: ``_title_subject``), and it NAMES a role or a project whose heading
or own title holds one of those words, or ``TITLE_LINES_SHARE`` of whose lines do.  Among the lines that add nothing
a requirement lacks, in this order: the lines of an entry the title names that hold a word of the title, whatever the
profile shows; then as before what the profile shows and what is of ordinary length; then, BEFORE strength and
recency, a line that supports BY WORDS a line of the posting that a cited row has answered (it covers nothing, the
assessment cited another line; it is still about the job).  Recency still only breaks ties.
AN ENTRY THE TITLE NAMES KEEPS ITS BEST LINE (``Selected.title_entries``): it is not dropped whole.  Its best line
is in the pick whatever a cap says and is cut only after every line that is not a requirement's evidence or a pin
(so every other old role and project goes whole first, and every recent role is down to its one line); when even
that cannot fit, the result carries a ``title_entry`` conflict.  It never costs a requirement its evidence, and the
Skills section is not cut for it.

PAGE FIT IS A CONSTRAINT, not a term: ``LENGTH_RULE.max_pages`` pages, measured with the shipped PDF
template (``measure_markdown``) WITH ROOM FOR THE HEADER (``sel-5``, 0.1.11.3 item 15: the estimate keeps the
header's block at its largest, ``resume_pdf.HEADER_RESERVE_LINES``, and never reads the header; ``sel-4`` kept two
lines in all, so a pick that filled its last page ran onto another once the name and contact line were printed), no role or project printed without a bullet, roles in date order, every
recent role present (``FLOORS``).  The steps:

- SCORE every line (``_Keys``): which requirements it supports and how well, its derived strength, the
  profile's prior, how recent its role is.  The master's Skills, split inside a group (``skill_atoms``:
  ``Python/Go`` is two skills), are the keyword vocabulary.  A line that says what a better line says
  (``NEAR_DUPLICATE``) is left out.
- PICK: one summary variant (the posting's title and the profile decide what kind of engineer it names),
  the best bullets per role under a cap (``PICK_CAPS``; an old role, by ``LENGTH_RULE``, gets
  ``old_role_bullets``), the best ``MAX_PROJECTS`` projects, ``MAX_OTHER`` Other lines, every degree; a
  requirement's evidence is in the pick whatever the caps say.  THE SKILLS SECTION IS KEPT WHOLE when it
  fits: what the posting asks for first (in the posting's order), then the rest by name.
- FIT: cuts are applied lowest value first until the pick fits, the fewest that fit found by binary
  search.  THE CUT ORDER (``_keys``): bullets that add nothing the posting asks for (the ones the
  profile does not show first, then unusually long ones, the weakest, the oldest), then the Other lines
  that name nothing it asks for; then a third line of a mandatory requirement, a second line of a
  nice-to-have, the first line of one, a second line of a mandatory requirement; then the Other lines
  that name a keyword or support a requirement; then the best line of an entry the title names (a conflict).
  Cutting the last line of an old role or of a project removes it whole.  SKILLS are cut last, what nothing
  asks for first, and only when no line is left to cut;
  except in a master whose Skills section is too long to print whole (more than ``SKILLS_WHOLE`` names),
  where the names past that count that nothing asks for and no picked line names are the first thing cut.  Every cut skill is recorded.  Last of all: pins, then requirements' evidence.
- CONFLICTS (``Selected.conflicts``): when a requirement's evidence, a pin or the one line of an entry the
  title names could not be shown within the page budget the result says which and why.  Nothing mandatory is
  dropped silently.
- FILL: room left on the last page goes back to the best lines cut, then to the best unshown bullets of
  the recent roles (up to ``HARD_CAPS``).
- Nothing is reworded: every shown line is a master line, by id, and every line of the master, shown or
  not, carries the reason (``LineReason``).  The same lines in another order give the same pick.

RE-MAKING A PICK (``compare_selections``): a stored selection and a new one are checked against the
same current master, requirements and page budget on separate checks; see that function.

Caps and the support thresholds are set by hand on the synthetic pick eval (``tests/evals/run_pick_eval.py``:
5 postings x 3 master sizes x 9 variations); ``SELECTOR_VERSION`` names them, so changing one is a
deliberate change of every stored selection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
import math
import re
from typing import TypeVar

from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SUMMARY, Master, MasterEntry, MasterItem
from .posting_keywords import PostingKeywords, alias_table, extract_keywords, mentions
from .tailor_no_loss import OWNERSHIP_FAMILIES, normalize
from .tailored_resume import LENGTH_RULE

#: Names the scoring weights, caps, floors and cut order below; stored with a selection.
SELECTOR_VERSION = "sel-5"
MAX_PAGES = LENGTH_RULE.max_pages
#: The page budget is "fits ``MAX_PAGES`` at this spacing or looser" (the renderer's own floor is 0.7).
FIT_SCALE = 0.9

#: Bullets per experience role in the first pick, by recency rank (0 = newest); an old role gets
#: ``LENGTH_RULE.old_role_bullets``. The fit cuts from here; the fill adds up to ``HARD_CAPS``.
PICK_CAPS = (9, 6, 6, 5)
HARD_CAPS = (11, 8, 8, 6)
#: A recent role is always present with at least this many bullets (the page constraint: no role without a bullet).
FLOORS = (1, 1, 1, 1)
MAX_PROJECTS = 3
PROJECT_BULLETS = 3
#: A shown project holds at most this many lines, and more than ``PROJECT_BULLETS`` only of lines the posting's requirements are about.
PROJECT_HARD_BULLETS = 6
MAX_OTHER = 4
#: The Skills section is kept whole. A master that lists more names than this has a section "too long": the names
#: past this count that no posting asks for and no picked line names are the first thing cut for length, before
#: any line (and they come back last, when room is left). Up to this count a skill is cut only when no line is left to cut.
SKILLS_WHOLE = 40
#: How many lines the fill tries to put into the room the last cut left.
FILL_TRIES = 12
#: A line supports a requirement when it holds this share of what the requirement says: its words, and (counted
#: ``TERM_WEIGHT`` times each) the keywords it names; and it names one of those keywords or shares ``SUPPORT_WORDS``
#: words.  Only the requirement and the line are read, so adding lines to a master never ends another line's support.
SUPPORT_SHARE = 0.3
SUPPORT_WORDS = 2
TERM_WEIGHT = 2
#: A requirement's evidence is chosen among the lines that support it at least this share as well as its best one.
EVIDENCE_SHARE = 0.8
#: A line longer than this (about four printed lines) costs the page what two or three lines do: of two lines that
#: support a requirement equally well the other one is its evidence, and among lines that add nothing it goes first.
LONG_LINE_CHARS = 400
#: Two lines are one (the weaker is left out) when this share of their words is common to both.
NEAR_DUPLICATE = 0.7
#: A cited row of the assessment is ABOUT a line of the posting when it shares this share of its own words with it,
#: and the rows about a line ANSWER it (the line is then not matched by words) when together they hold this share of
#: what the line says.
CITED_ABOUT_SHARE = 0.5
CITED_ANSWERED_SHARE = 0.6
#: The posting's title NAMES an entry when its heading or a subline (a role's own title) holds a word of the title,
#: or when this share of its lines do (two lines at least, unless it has one line only).
TITLE_LINES_SHARE = 0.5
#: The evidence view's budget: the assess prompt's own cap on resume text.
EVIDENCE_CAP = 12_000

#: The page count reported for a pick too large for the renderer to lay out at all (only ever before the fit).
UNMEASURED_PAGES = 99

#: ``(last page, fill of that page 0..1)`` for resume markdown.
Measure = Callable[[str], tuple[int, float]]
_N = TypeVar("_N", int, float)

_KEYWORD_WEIGHT = 1.5
_LEXICAL_WEIGHT = 2.0
_PRIOR_WEIGHT = 1.0
_STRENGTH = {"backed": 0.5, "quantified": 0.35, "stated": 0.0}
_OWNERSHIP_BONUS = 0.15
_RECENCY = (1.0, 0.9, 0.9, 0.8)
_OLD_RECENCY = 0.6
_PROJECT_RECENCY = 0.9
_OTHER_RECENCY = 0.8

_WORD = re.compile(r"[a-z][a-z0-9+#.-]*[a-z0-9+#]|[a-z]")
_TITLE_WORD = re.compile(r"[a-z0-9+#]+")
_STOP = frozenset(
    """a an and or of to in for with on at by from as is are be we you our your this that it its will can
    must should have has had not no all any more most other some such than then their they them what when
    where which who how about also into over under per plus experience experienced years year strong
    ability work working role using used use new across within build building built design
    designed own owned including least solid record several tight every one two three""".split()
)
#: Verb forms the suffix rule does not join.
_IRREGULAR = {"led": "lead", "ran": "run", "wrote": "write", "written": "write", "grew": "grow", "grown": "grow"}
#: Rank and filler words of a job title: they say nothing about what kind of engineer it names.
_GENERIC_TITLE = frozenset({"staff", "senior", "principal", "lead", "engineer", "software", "and", "the", "of", "with"})
_PRIOR_TITLE_STOP = frozenset({"staff", "principal", "senior", "engineer"})
#: Words of a job title that name no subject: where the job is, a level.
_TITLE_PLACE = frozenset({"remote", "hybrid", "onsite", "us", "usa", "uk", "eu", "emea", "apac", "ii", "iii", "iv", "jr", "sr"})
_OWNERSHIP = tuple(re.compile(pattern) for _name, pattern in OWNERSHIP_FAMILIES)
_POSTING_BULLET = re.compile(r"\A[-*•]\s+")
_NICE_HEADING = re.compile(r"\b(?:nice|bonus|preferred|plus|desirable|ideally|good to have)\b", re.IGNORECASE)
_MUST_CUE = re.compile(r"\b(?:required|requirements?|must|you have|you will have|you bring|minimum|essential|mandatory|qualifications)\b", re.IGNORECASE)
#: A list under one of these headings says what the employer gives, not what the job asks for.
_NOT_ASKED_HEADING = re.compile(
    r"\b(?:benefits?|perks?|we offer|what you(?:'ll| will) get|compensation|salary|pay range|about (?:us|the company)|why (?:join|us)|our values|equal opportunity)\b",
    re.IGNORECASE,
)
_HEADING_MAX_CHARS = 60
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_SKILL_PARTS = re.compile(r"[/()]")
_STRENGTH_RANK = {"backed": 3, "quantified": 2, "stated": 1}
_LABEL_CHARS = 80


@dataclass(frozen=True)
class SelectionProfile:
    """What a profile gives the selector: its titles and, optionally, focus tags or a stored selection.

    ``base_ids`` (the ids a profile's stored selection shows) is the prior when present; else the focus
    tags against each line's tags; else the titles' own words."""

    titles: tuple[str, ...] = ()
    focus_tags: tuple[str, ...] = ()
    base_ids: tuple[str, ...] | None = None
    profile_id: str | None = None
    label: str = ""
    #: Must-keep lines (the profile's stored pins): shown whatever they score, cut only when nothing else can go.
    pins: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "label": self.label, "titles": list(self.titles), "focus_tags": list(self.focus_tags)}


@dataclass(frozen=True)
class CitedRequirement:
    """One requirement row of the stored assessment of a posting, and the master lines its evidence cites.

    ``id`` is the row's place in the assessment (``r<n>``), ``text`` the assessment's own requirement words (the
    posting's), ``lines`` the ids of the master lines its evidence quotes trace to (never empty: a row that cites
    no line is not passed, it is matched by words as before).  ``met`` is the assessment's status for the row.
    """

    id: str
    text: str
    mandatory: bool
    lines: tuple[str, ...]
    met: bool = True

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "text": self.text, "mandatory": self.mandatory, "met": self.met, "lines": list(self.lines)}


@dataclass(frozen=True)
class SelectionPosting:
    """One posting as the selector reads it: public text, written by strangers; only ever matched against.

    ``cited``: when this posting has a stored assessment for the profile, its rows that cite master lines
    (``assess_master.cited_requirements``).  Coverage then comes from those lines (the module text)."""

    title: str
    text: str
    company: str = ""
    location: str = ""
    cited: tuple[CitedRequirement, ...] = ()


# --- text features ----------------------------------------------------------------------------


def _stem(word: str) -> str:
    """One form for the forms of a word: ``release``, ``releases``, ``released`` and ``releasing`` are ``releas``."""

    for suffix in ("ations", "ation", "ing", "ers", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word[:-1] if len(word) > 4 and word.endswith("e") else word


def _words(text: str) -> frozenset[str]:
    """The words of a line as they compare: stemmed, without filler; a compound (``multi-agent``) and its parts."""

    found: set[str] = set()
    for word in _WORD.findall(text.casefold()):
        for part in (word, *word.split("-")) if "-" in word else (word,):
            part = _IRREGULAR.get(part, part)
            if len(part) > 2 and part not in _STOP:
                found.add(_stem(part))
    return frozenset(found)


def _title_tokens(text: str) -> set[str]:
    """What a job title is about: its words (two-letter ones such as AI and ML included), minus rank words."""

    return {_stem(token) for token in _TITLE_WORD.findall(text.casefold()) if len(token) >= 2 and token not in _GENERIC_TITLE}


#: (``_title_tokens`` answers stems: "Remote" is ``remot``, "Engineering" is ``engineer``.)
_NO_SUBJECT = frozenset(_stem(word) for word in (*_GENERIC_TITLE, *_TITLE_PLACE))


def _title_subject(text: str) -> frozenset[str]:
    """The words of ``text`` that can say what a job is ABOUT: a title's words without rank, place, level or number."""

    return frozenset(token for token in _title_tokens(text) if token not in _NO_SUBJECT and not token.isdigit())


def _tag_tokens(tags: Iterable[str]) -> set[str]:
    return {_stem(tag) for tag in tags} | {part for tag in tags for part in tag.split("-")}


def _idf(master: Master, item_words: dict[str, frozenset[str]]) -> dict[str, float]:
    docs = [item_words[item.id] for item in master.items.values() if item.kind == KIND_BULLET]
    count: dict[str, int] = {}
    for doc in docs:
        for word in doc:
            count[word] = count.get(word, 0) + 1
    return {word: math.log(1 + len(docs) / n) for word, n in count.items()}


def skill_atoms(name: str) -> tuple[str, ...]:
    """The single skills inside one name of a Skills line: ``Python/Go/TypeScript`` is three, ``Model Context
    Protocol (MCP)`` two.  A name the keyword table knows whole (``CI/CD``) and one with a one-letter part
    (``A/B testing``, ``I/O``) stay whole."""

    if any(name.casefold() == known.casefold() for known in alias_table()):
        return (name,)
    parts = [part.strip() for part in _SKILL_PARTS.split(name) if part.strip()]
    if len(parts) < 2 or any(len(part) < 2 for part in parts):
        return (name,)
    return tuple(dict.fromkeys(parts))


def _vocabulary(master: Master) -> list[str]:
    """Every single skill the master lists, once: the keyword vocabulary (however the Skills lines group them)."""

    seen: set[str] = set()
    out: list[str] = []
    for name in master.skills():
        for atom in skill_atoms(name):
            if atom.casefold() not in seen:
                seen.add(atom.casefold())
                out.append(atom)
    return out


def _owns(text: str) -> bool:
    flat = normalize(text)
    return any(pattern.search(flat) for pattern in _OWNERSHIP)


# --- roles in time ----------------------------------------------------------------------------


def _end_year(entry: MasterEntry) -> int:
    return 9999 if entry.ongoing else (entry.end or 0)


def _entry_sort_key(entry: MasterEntry) -> tuple[int, int, int]:
    return (-_end_year(entry), -(entry.start or 0), entry.order)


def is_old_role(entry: MasterEntry, today: date) -> bool:
    """An OLD role (``LENGTH_RULE``): it ended more than ``old_role_years`` back; ongoing or undated is never old."""

    return entry.end is not None and today.year - entry.end > LENGTH_RULE.old_role_years


def _rank(index: int, table: tuple[_N, ...]) -> _N:
    return table[min(index, len(table) - 1)]


# --- scoring ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Asked:
    """One thing the posting asks for, as the selector reads it: a list line, or a keyword no line names."""

    id: str
    text: str
    mandatory: bool
    words: frozenset[str]
    terms: tuple[str, ...]


@dataclass(frozen=True)
class _JobTerms:
    keywords: PostingKeywords
    #: stemmed word -> weight (requirement lines 1.0, nice-to-have lines 0.5)
    lexical: dict[str, float]
    asked: tuple[_Asked, ...] = ()


def _job_terms(master: Master, posting: SelectionPosting) -> _JobTerms:
    keywords = extract_keywords(posting.text, title=posting.title, skills=_vocabulary(master))
    named = (*keywords.must, *keywords.nice)
    lines = [line.strip() for line in posting.text.splitlines() if line.strip()]
    # A posting written as lists: only its list lines say what the job asks for (the prose around them is
    # about the company). A posting with no list at all is read whole, a sentence at a time.
    bulleted = any(_POSTING_BULLET.match(line) for line in lines)
    lexical: dict[str, float] = {}
    asked: list[_Asked] = []
    weight, skipped = 1.0, False

    def ask(text: str, mandatory: bool) -> None:
        words, terms = _words(text), tuple(term for term in named if mentions(text, term))
        # A line that only lists keywords asks for nothing its keywords do not (each is asked for below).
        if words - set().union(*(_words(term) for term in terms)) if terms else words:
            asked.append(_Asked(f"q{len(asked) + 1}", text, mandatory, words, terms))

    for line in lines:
        is_bullet = _POSTING_BULLET.match(line) is not None
        if not is_bullet and len(line) <= _HEADING_MAX_CHARS and (bulleted or line.endswith(":")):
            weight = 0.5 if _NICE_HEADING.search(line) else 1.0
            skipped = _NOT_ASKED_HEADING.search(line) is not None
            continue
        if not is_bullet and bulleted:
            continue
        for word in _words(line):
            lexical[word] = max(lexical.get(word, 0.0), weight)
        if skipped:
            continue
        if is_bullet:
            text = _POSTING_BULLET.sub("", line).strip()
            ask(text, weight == 1.0 and (_NICE_HEADING.search(text) is None or _MUST_CUE.search(text) is not None))
        else:
            # Prose: a sentence is mandatory only when it says so or names a must-have; the rest is about the company.
            for sentence in _SENTENCE.split(line):
                must = any(mentions(sentence, term) for term in keywords.must) or _MUST_CUE.search(sentence) is not None
                ask(sentence.strip(), weight == 1.0 and must and _NICE_HEADING.search(sentence) is None)
    for word in _words(posting.title):
        lexical[word] = 1.0
    # Every keyword is asked for by itself too: a must-have keeps a line that names it, whatever its sentence says.
    for mandatory, terms in ((True, keywords.must), (False, keywords.nice)):
        for term in terms:
            asked.append(_Asked(f"k{len(asked) + 1}", term, mandatory, frozenset(), (term,)))
    return _JobTerms(keywords, lexical, tuple(asked))


@dataclass
class _Scores:
    """Per item: the keywords it names (by weight), a lexical overlap 0..1, the profile's prior 0..1, strength."""

    hits: dict[str, dict[str, float]] = field(default_factory=dict)
    lexical: dict[str, float] = field(default_factory=dict)
    prior: dict[str, float] = field(default_factory=dict)
    strength: dict[str, float] = field(default_factory=dict)


def _profile_prior(master: Master, profile: SelectionProfile, item_words: dict[str, frozenset[str]]) -> dict[str, float]:
    if profile.base_ids is not None:
        chosen = set(profile.base_ids)
        return {item_id: 1.0 if item_id in chosen else 0.0 for item_id in master.items}
    title_words = set().union(*(_words(title) for title in profile.titles)) - _PRIOR_TITLE_STOP
    focus = set(profile.focus_tags)
    # "A profile is just its titles": with no focus tags, what the titles are about stands in for them.
    from_titles = set().union(*(_title_tokens(title) for title in profile.titles)) if not focus else set()
    out: dict[str, float] = {}
    for item in master.items.values():
        if not item.tags:
            share = 0.0
        elif focus:
            share = len(focus & set(item.tags)) / len(item.tags)
        else:
            share = sum(bool(_tag_tokens((tag,)) & from_titles) for tag in item.tags) / len(item.tags)
        out[item.id] = min(1.0, share + 0.5 * bool(title_words & item_words[item.id]))
    return out


def _score_items(master: Master, terms: _JobTerms | None, profile: SelectionProfile) -> _Scores:
    item_words = {item.id: _words(item.text) for item in master.items.values()}
    scores = _Scores(prior=_profile_prior(master, profile, item_words))
    weights = _idf(master, item_words)
    top = 1e-9
    for item in master.items.values():
        hits: dict[str, float] = {}
        if terms is not None:
            for term in terms.keywords.must:
                if mentions(item.text, term):
                    hits[term] = 2.0
            for term in terms.keywords.nice:
                if mentions(item.text, term):
                    hits[term] = 1.0
            overlap = item_words[item.id] & terms.lexical.keys()
            scores.lexical[item.id] = sum(weights.get(word, 1.0) * terms.lexical[word] for word in overlap)
            top = max(top, scores.lexical[item.id])
        scores.hits[item.id] = hits
        scores.strength[item.id] = _STRENGTH[item.strength] + (_OWNERSHIP_BONUS if _owns(item.text) else 0.0)
    for item_id in scores.lexical:
        scores.lexical[item_id] /= top
    return scores


def _value(item: MasterItem, scores: _Scores, covered: dict[str, int], recency: float) -> float:
    """What showing this line is worth now: a keyword counts less once the pick already covers it."""

    keyword = sum(weight / (1 + covered.get(term, 0)) for term, weight in scores.hits.get(item.id, {}).items())
    return recency * (
        _KEYWORD_WEIGHT * keyword + _LEXICAL_WEIGHT * scores.lexical.get(item.id, 0.0)
        + _PRIOR_WEIGHT * scores.prior.get(item.id, 0.0) + scores.strength.get(item.id, 0.0)
    )


def _summary_choice(master: Master, scores: _Scores, posting: SelectionPosting | None) -> MasterItem | None:
    """The summary variant a resume shows.

    A summary says what KIND of engineer this is, so the posting's title and the profile decide first and
    keywords only break ties. (Chosen by keyword value alone, the infrastructure summary won for an AI agent
    posting, because it names more of the posting's listed tools: the spike's fault, section 9.2.)"""

    summaries = master.in_section("summary")
    if not summaries:
        return None
    title_tokens = _title_tokens(posting.title) if posting is not None else set()

    def rank(item: MasterItem) -> tuple[float, float, int]:
        own = _title_tokens(item.text) | _tag_tokens(item.tags)
        return (len(title_tokens & own) + scores.prior.get(item.id, 0.0), _value(item, scores, {}, 1.0), -item.order)

    return max(summaries, key=rank)


# --- a pick and its markdown ------------------------------------------------------------------


@dataclass
class _Pick:
    """Which master lines a resume shows. Everything is an id (skills: names); text is never copied here."""

    summary: list[str] = field(default_factory=list)
    entries: dict[str, list[str]] = field(default_factory=dict)  # entry id -> bullet ids, shown order
    skills: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def bullet_ids(self) -> list[str]:
        return [bullet for bullets in self.entries.values() for bullet in bullets]

    def copy(self) -> "_Pick":
        return _Pick(list(self.summary), {key: list(value) for key, value in self.entries.items()}, list(self.skills), list(self.other))


#: 0.1.11 N3: how a master line's note reaches the assess prompt's RESUME block, and nothing else: a line of its own,
#: labelled, under the line (or the entry heading) it belongs to. A comment, so no renderer would ever print it.
NOTE_LABEL = "private note"


def _note(master: Master, item_id: str) -> str | None:
    """The note of a line or an entry (0.1.11 N1b's ``MasterItem.note`` / ``MasterEntry.note``, what ``master_resume.note_of``
    answers); ``None`` without one, and for a master read by a tree that has no notes yet."""

    target = master.items.get(item_id) or master.entries.get(item_id)
    note = getattr(target, "note", None)
    return " ".join(str(note).split()) or None if note else None


def _render(master: Master, pick: _Pick, *, ids: bool, notes: bool = False) -> str:
    """Resume markdown in GigAI's format for a pick; ``ids`` keeps each line's master id in a trailing comment.

    ``notes`` (0.1.11 N3; the assess prompt's RESUME block ONLY, ``evidence_view(ids=True)``): a line or an entry
    that has a note is followed by a line of its own, ``<!-- private note: ... -->``. A note is the user's private
    guidance for choosing lines: it is never in a resume, a PDF, a candidate set or a stored selection, which are all
    rendered without ``notes``.
    """

    def mark(item_id: str) -> str:
        return f" <!-- id:{item_id} -->" if ids else ""

    def noted(item_id: str) -> list[str]:
        note = _note(master, item_id) if notes else None
        return [f"<!-- {NOTE_LABEL}: {note} -->"] if note else []

    def line(item_id: str) -> list[str]:
        return [f"- {master.items[item_id].text}{mark(item_id)}", *noted(item_id)]

    def entries(section: str) -> list[str]:
        out: list[str] = []
        for entry in sorted(master.entries_in(section), key=_entry_sort_key):
            if entry.id in pick.entries:
                out += [f"### {entry.heading}{mark(entry.id)}", *entry.sublines, *noted(entry.id), ""]
                out += [text for i in pick.entries[entry.id] for text in line(i)] + [""]
        return out

    out: list[str] = []
    if pick.summary:
        out += ["## Summary", ""] + [text for i in pick.summary for text in line(i)] + [""]
    for section in ("experience", "projects"):
        shown = entries(section)
        if shown:
            out += [f"## {section.capitalize()}", "", *shown]
    if pick.skills:
        out += ["## Skills", "", "- " + ", ".join(pick.skills), ""]
    education = entries("education")
    if education:
        out += ["## Education", "", *education]
    if pick.other:
        out += ["## Other", ""] + [text for i in pick.other for text in line(i)] + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def _shipped_measure(markdown: str) -> tuple[int, float]:
    from .resume_pdf import ResumeMarkdownError, measure_markdown  # lazy: the layout engine is a large native library

    try:
        return measure_markdown(markdown, spacing_scale=FIT_SCALE)
    except ResumeMarkdownError as exc:
        if exc.code != "resume_markdown_too_large":
            raise
        # More markdown than the renderer takes is far over any page budget; the fit cuts on from here.
        return UNMEASURED_PAGES, 1.0


# --- the result -------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineReason:
    """Why one master line is shown or left out: a stable ``code`` and the sentence a person reads."""

    id: str
    picked: bool
    code: str
    reason: str


@dataclass(frozen=True)
class SkillReason:
    name: str
    picked: bool
    code: str
    reason: str


@dataclass(frozen=True)
class Requirement:
    """One thing the posting asks for and the master lines that support it, strongest first.

    ``text`` is the posting's own line (public text) or, for a keyword no list line names, the keyword.
    ``supporters`` is empty when the master cannot support it; its first id is the requirement's EVIDENCE.
    ``cited``: the requirement is a row of the stored assessment and its supporters are the lines that row
    cites (found by meaning), not lines found by shared words.
    """

    id: str
    text: str
    mandatory: bool
    supporters: tuple[str, ...] = ()
    cited: bool = False

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "text": self.text, "mandatory": self.mandatory, "supporters": list(self.supporters), "cited": self.cited}


@dataclass(frozen=True)
class Conflict:
    """Something the page budget kept out although the rules say it stays: shown to the user, never silent.

    ``kind``: ``mandatory_evidence`` (a mandatory requirement's evidence line is not shown; ``covered`` says
    whether a weaker line still covers the requirement), ``must_keep`` (a pinned line is not shown),
    ``title_entry`` (no line is shown of a role or project the posting's title names; ``ids``: the entry and
    its best line) or ``over_budget`` (every cut the rules allow was made and the resume is still over the page limit).
    """

    kind: str
    ids: tuple[str, ...]
    reason: str
    requirement_id: str = ""
    requirement: str = ""
    covered: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind, "ids": list(self.ids), "reason": self.reason, "requirement_id": self.requirement_id,
            "requirement": self.requirement, "covered": self.covered,
        }


@dataclass(frozen=True)
class LengthCut:
    """One cut the fit made, in the order it was made. ``kind`` is ``bullet``, ``role``, ``other`` or ``skill``."""

    id: str
    kind: str
    code: str
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class Selected:
    """One selection of the master: the ids shown, the markdown, and the reason for every line."""

    selector_version: str
    summary: tuple[str, ...]
    #: entry id -> its shown bullet ids, in the order the resume prints both.
    entries: dict[str, tuple[str, ...]]
    skills: tuple[str, ...]
    other: tuple[str, ...]
    #: The resume markdown (no id comments): what a renderer or an existing resume reader takes.
    markdown: str
    #: The same markdown with each line's master id in a trailing comment.
    markdown_with_ids: str
    #: Every line of the master except its Skills lines, in the master's order.
    lines: tuple[LineReason, ...]
    skill_reasons: tuple[SkillReason, ...]
    max_pages: int
    pages_before_fit: int
    pages: int
    last_page_fill: float
    layout_queries: int
    cut_for_length: tuple[LengthCut, ...]
    added_to_fill: tuple[str, ...]
    #: ``sel-1`` fields, always empty now (a requirement's evidence is in ``evidence_for``); kept for readers of the JSON.
    only_evidence: dict[str, tuple[str, ...]]
    shown_instead: dict[str, tuple[str, tuple[str, ...]]]
    roles_dropped: tuple[str, ...]
    keywords: PostingKeywords | None
    #: With a posting: which of its keywords the markdown names (``must_covered``, ``must_missing``, ...).
    coverage: dict[str, tuple[str, ...]]
    #: Every bullet and Other line of the master -> its place in the cut order (0 is cut first; a larger
    #: number stays longer). What the tailor path's fit reads (``tailor_master``).
    values: dict[str, float] = field(default_factory=dict)
    #: What the posting asks for, in its order, each with the master lines that support it (empty without a posting).
    requirements: tuple[Requirement, ...] = ()
    #: A shown line that is the evidence of a requirement -> the ids of those requirements.
    evidence_for: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: What the page budget kept out although the rules say it stays (empty: nothing).
    conflicts: tuple[Conflict, ...] = ()
    #: A line left out because a better line says the same: id -> that line's id.
    duplicates: dict[str, str] = field(default_factory=dict)
    #: The roles and projects the posting's title names -> the best line of each (the line that keeps it shown).
    title_entries: dict[str, str] = field(default_factory=dict)

    @property
    def fits(self) -> bool:
        return self.pages <= self.max_pages

    def item_ids(self) -> tuple[str, ...]:
        """The ids of the lines shown, in the order the resume prints them (entry headings are in ``entries``)."""

        return (*self.summary, *(bullet for bullets in self.entries.values() for bullet in bullets), *self.other)

    def to_json(self, master: Master) -> dict[str, object]:
        def line(reason: LineReason) -> dict[str, object]:
            item = master.items[reason.id]
            return {
                "id": item.id, "section": item.section, "kind": item.kind, "entry_id": item.entry_id, "text": item.text,
                "code": reason.code, "reason": reason.reason,
            }

        by_id = {reason.id: reason for reason in self.lines}
        shown_order = [by_id[item_id] for item_id in self.item_ids()]
        shown_ids = set(self.item_ids())
        left = [reason for reason in self.lines if not reason.picked]
        entries: list[dict[str, object]] = []
        for section in ("experience", "projects", "education"):
            for entry in sorted(master.entries_in(section), key=_entry_sort_key):
                picked = self.entries.get(entry.id)
                entries.append({
                    "id": entry.id, "section": entry.section, "heading": entry.heading, "sublines": list(entry.sublines),
                    "shown": picked is not None, "picked": list(picked or ()),
                    "left_out": [bullet for bullet in entry.bullets if bullet not in (picked or ())],
                })
        out: dict[str, object] = {
            "selector_version": self.selector_version,
            "max_pages": self.max_pages, "pages": self.pages, "pages_before_fit": self.pages_before_fit,
            "last_page_fill": self.last_page_fill, "fits": self.fits, "layout_queries": self.layout_queries,
            "counts": {
                "picked": len(shown_order), "left_out": len(left), "bullets": sum(len(bullets) for bullets in self.entries.values()),
                "skills": len(self.skills), "skills_left_out": sum(not skill.picked for skill in self.skill_reasons),
                "cut_for_length": len(self.cut_for_length), "conflicts": len(self.conflicts),
            },
            "summary": list(self.summary),
            "entries": entries,
            "picked": [line(reason) for reason in shown_order],
            "left_out": [line(reason) for reason in left],
            "skills": {
                "picked": [{"name": skill.name, "code": skill.code, "reason": skill.reason} for skill in self.skill_reasons if skill.picked],
                "left_out": [{"name": skill.name, "code": skill.code, "reason": skill.reason} for skill in self.skill_reasons if not skill.picked],
            },
            "cut_for_length": [cut.to_json() for cut in self.cut_for_length],
            "added_to_fill": list(self.added_to_fill),
            "only_evidence": [{"id": item_id, "terms": list(terms)} for item_id, terms in self.only_evidence.items()],
            "shown_instead": [{"id": item_id, "of": old, "terms": list(terms)} for item_id, (old, terms) in self.shown_instead.items()],
            "roles_dropped": list(self.roles_dropped),
            "requirements": [
                {**requirement.to_json(), "shown": [item for item in requirement.supporters if item in shown_ids]}
                for requirement in self.requirements
            ],
            "evidence_for": [{"id": item_id, "requirements": list(ids)} for item_id, ids in self.evidence_for.items()],
            "conflicts": [conflict.to_json() for conflict in self.conflicts],
            "title_entries": [{"id": entry_id, "line": item_id, "shown": entry_id in self.entries} for entry_id, item_id in self.title_entries.items()],
            "keywords": None,
        }
        if self.keywords is not None:
            out["keywords"] = {**self.keywords.to_json(), **{key: list(value) for key, value in self.coverage.items()}}
        return out


# --- the selection ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cut:
    kind: str  # bullet | other | skill
    id: str
    #: The cut is made although the rules say the line stays (a pin, a requirement's evidence): a conflict.
    forced: bool = False


def _names(terms: Iterable[str]) -> str:
    return ", ".join(terms)


def _label(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _LABEL_CHARS else flat[: _LABEL_CHARS - 1].rstrip() + "…"


@dataclass
class _Keys:
    """Every selectable bullet and Other line against what the posting asks for: what each supports, and its place.

    ``key`` orders the lines: a larger key stays longer.  ``(tier, class, place)``: the tier is 4 for the
    evidence of a met mandatory row of the assessment (the line it cites), 3 for the evidence of any other
    mandatory requirement, 2 for a pin and 1 for the best line of an entry the posting's title names
    (``title_floor``); the class is 5 for evidence and pins, 4 for a second line of a
    mandatory requirement, 3 for the first line of a nice-to-have, 2 for a second line of one, 1 for a line
    that adds no line a requirement lacks but still supports a listed mandatory requirement, 0 for a line
    that supports nothing asked for; the place is its turn in the keep order (``_keys``).  Ties end on the
    line's id (the smaller id stays longer), so the master's own order never decides."""

    requirements: tuple[Requirement, ...] = ()
    #: line id -> {requirement id: how well it supports it}
    supports: dict[str, dict[str, float]] = field(default_factory=dict)
    #: a requirement's evidence line -> the mandatory requirements it is the evidence of
    evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    nice_evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: the evidence lines that are the line a met mandatory row of the assessment cites (cut last of all)
    cited_evidence: frozenset[str] = frozenset()
    duplicates: dict[str, str] = field(default_factory=dict)
    #: a line of an entry the posting's title names -> how many words of the title it holds
    titled: dict[str, int] = field(default_factory=dict)
    #: line -> how well it supports BY WORDS the lines of the posting that a cited row has answered (it covers nothing there)
    wording: dict[str, float] = field(default_factory=dict)
    #: an entry the posting's title names -> its best line
    title_floor: dict[str, str] = field(default_factory=dict)
    key: dict[str, tuple] = field(default_factory=dict)
    rank: dict[str, int] = field(default_factory=dict)

    def klass(self, item_id: str) -> int:
        return self.key[item_id][1] if item_id in self.key else 0

    def protected(self, item_id: str) -> bool:
        return item_id in self.evidence


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _near_duplicates(items: list[MasterItem], words: dict[str, frozenset[str]], better: Callable[[MasterItem], tuple]) -> dict[str, str]:
    """``line -> the better line that says the same``: the same numbers, and the same words but for a few
    (``NEAR_DUPLICATE`` of both).  Two lines that state different numbers are two facts, however alike they read."""

    kept: list[MasterItem] = []
    out: dict[str, str] = {}
    numbers = {item.id: frozenset(_NUMBER.findall(item.text)) for item in items}
    for item in sorted(items, key=better, reverse=True):
        mine = words[item.id]
        twin = None
        if len(mine) >= 3:
            for other in kept:
                if numbers[other.id] != numbers[item.id]:
                    continue
                theirs = words[other.id]
                if min(len(mine), len(theirs)) < NEAR_DUPLICATE * max(len(mine), len(theirs)):
                    continue
                if len(mine & theirs) >= NEAR_DUPLICATE * len(mine | theirs):
                    twin = other
                    break
        if twin is None:
            kept.append(item)
        else:
            out[item.id] = twin.id
    return out


def _keys(
    master: Master, terms: _JobTerms | None, scores: _Scores, recency: Callable[[MasterItem], float], pins: Iterable[str],
    cited: tuple[CitedRequirement, ...] = (), summary_id: str | None = None, title: str = "",
) -> _Keys:
    lines = [item for item in master.items.values() if item.kind in (KIND_BULLET, KIND_OTHER)]
    words = {item.id: _words(item.text) for item in lines}
    strength = {item.id: _STRENGTH_RANK[item.strength] for item in lines}
    brief = {item.id: len(item.text) <= LONG_LINE_CHARS for item in lines}
    # The last word in every tie: the smaller id stays longer. Ids do not move when the master's lines do, so the
    # pick does not depend on their order; ids written by hand usually follow it, so a role's first lines stay.
    tie = {item_id: -place for place, item_id in enumerate(sorted(item.id for item in lines))}
    out = _Keys()
    out.duplicates = _near_duplicates(lines, words, lambda item: (strength[item.id], recency(item), scores.prior.get(item.id, 0.0), tie[item.id]))
    lines = [item for item in lines if item.id not in out.duplicates]
    by_id = {item.id: item for item in lines}
    naming: dict[str, list[str]] = {}  # keyword -> the lines that name it
    for item in lines:
        for term in scores.hits.get(item.id, {}):
            naming.setdefault(term, []).append(item.id)

    requirements: list[Requirement] = []
    listed_evidence: set[str] = set()  # the evidence lines of the mandatory requirements the posting lists
    topic: dict[str, float] = {}  # line -> how well it supports those listed requirements, summed
    # THE ASSESSMENT'S ROWS FIRST (the module text): a row that cites lines is supported by exactly those lines.
    # A cited line that a better line repeats is stood for by that line; one the master no longer holds cites nothing.
    held: dict[str, tuple[str, ...]] = {}
    citing: dict[str, int] = {}  # line -> how many rows cite it
    for row in cited:
        held[row.id] = tuple(dict.fromkeys(twin for item_id in row.lines if (twin := out.duplicates.get(item_id, item_id)) in by_id))
        for item_id in held[row.id]:
            citing[item_id] = citing.get(item_id, 0) + 1
    cited_evidence: set[str] = set()
    by_summary: set[str] = set()  # rows the shown summary covers by itself
    row_words: list[tuple[frozenset[str], str]] = []
    for row in cited:
        own = held[row.id]
        summaries = tuple(dict.fromkeys(item_id for item_id in row.lines if item_id in master.items and master.items[item_id].kind == KIND_SUMMARY))
        if not own and not summaries:
            continue  # nothing it cites is a line of this master now: it is matched by words, like a row that cites nothing
        # The strongest cited line first: a line of ordinary length, its strength, the line more rows cite, recency.
        ordered = sorted(own, key=lambda item_id: (brief[item_id], strength[item_id], citing[item_id], recency(by_id[item_id]), tie[item_id]), reverse=True)
        # The summary the resume shows covers the row by itself; another summary variant cannot be shown beside it.
        shown_summary = tuple(item_id for item_id in summaries if item_id == summary_id)
        requirements.append(Requirement(row.id, row.text, row.mandatory, (*shown_summary, *ordered, *(item_id for item_id in summaries if item_id != summary_id)), cited=True))
        row_words.append((_words(row.text), row.text))
        for item_id in own:
            out.supports.setdefault(item_id, {})[row.id] = 1.0
            if row.mandatory:
                topic[item_id] = topic.get(item_id, 0.0) + 1.0
        if shown_summary:
            by_summary.add(row.id)
        elif row.mandatory and ordered:
            listed_evidence.add(ordered[0])
            if row.met:
                cited_evidence.add(ordered[0])
    out.cited_evidence = frozenset(cited_evidence)

    def answered(asked: _Asked) -> bool:
        """A cited row is about this line or keyword of the posting: the assessment has said which lines support it."""

        if not asked.words:
            return any(mentions(text, asked.terms[0]) for _words_of, text in row_words)
        said: set[str] = set()
        for words_of, _text in row_words:
            shared = words_of & asked.words
            if shared and len(shared) >= CITED_ABOUT_SHARE * len(words_of):
                said |= shared
        return len(said) >= CITED_ANSWERED_SHARE * len(asked.words)

    # Then what the posting asks for that no cited row is about, matched by words. The listed lines first, then the
    # keywords: a keyword's evidence is a line that is evidence already when one names it, else the line most about
    # what the posting lists.
    for asked in sorted(terms.asked if terms is not None else (), key=lambda asked: not asked.words):
        found: dict[str, float] = {}
        if asked.words:
            total = len(asked.words) + TERM_WEIGHT * len(asked.terms)
            for item in lines:
                named = sum(1 for term in asked.terms if term in scores.hits.get(item.id, {}))
                shared = len(asked.words & words[item.id])
                share = (shared + TERM_WEIGHT * named) / total
                if share >= SUPPORT_SHARE and (named or shared >= SUPPORT_WORDS):
                    found[item.id] = share
        if row_words and answered(asked):
            # A cited row has answered this line of the posting: a line that supports it by words covers nothing
            # (the assessment cited another one) and is still ABOUT the posting.
            for item_id, value in found.items():
                out.wording[item_id] = round(out.wording.get(item_id, 0.0) + (value if asked.mandatory else value / 2), 6)
            continue
        if asked.words:
            # "Well" is measured against the best line of ordinary length: a very long line shares more words with
            # any requirement only because it holds more words.
            best = max((value for item_id, value in found.items() if brief[item_id]), default=max(found.values(), default=0.0))

            def strongest(item_id: str) -> tuple:
                # Among the lines that support it well: a line of ordinary length, strength, then how well, then recency.
                well = found[item_id] >= EVIDENCE_SHARE * best
                return (well, brief[item_id] if well else False, strength[item_id] if well else 0, round(found[item_id], 6), recency(by_id[item_id]), tie[item_id])

            supporters = tuple(sorted(found, key=strongest, reverse=True))
            if asked.mandatory:
                listed_evidence.update(supporters[:1])
                for item_id, value in found.items():
                    topic[item_id] = topic.get(item_id, 0.0) + value
        else:
            found = {item_id: 1.0 for item_id in naming.get(asked.terms[0], ())}

            def strongest(item_id: str) -> tuple:
                return (item_id in listed_evidence, brief[item_id], round(topic.get(item_id, 0.0), 6) > 0, strength[item_id], round(topic.get(item_id, 0.0), 6), recency(by_id[item_id]), tie[item_id])

            supporters = tuple(sorted(found, key=strongest, reverse=True))
        requirements.append(Requirement(asked.id, asked.text, asked.mandatory, supporters))
        for item_id, value in found.items():
            out.supports.setdefault(item_id, {})[asked.id] = value
    # The assessment's rows in its order, then the posting's in its own.
    place = {row.id: index for index, row in enumerate(cited)}
    place.update({asked.id: len(cited) + index for index, asked in enumerate(terms.asked if terms is not None else ())})
    requirements.sort(key=lambda requirement: place[requirement.id])
    out.requirements = tuple(requirements)
    mandatory = {requirement.id for requirement in requirements if requirement.mandatory}
    evidence: dict[str, list[str]] = {}
    for requirement in requirements:
        # (A row the shown summary covers has no evidence line: its cited bullets are further lines for it.)
        if requirement.mandatory and requirement.supporters and requirement.supporters[0] in by_id:
            evidence.setdefault(requirement.supporters[0], []).append(requirement.id)
    out.evidence = {item_id: tuple(ids) for item_id, ids in evidence.items()}
    pinned = set(pins)

    def static(item: MasterItem) -> tuple:
        """A line's worth with nothing more to add: the profile shows it, it is of ordinary length, its strength, then recency."""

        prior = scores.prior.get(item.id, 0.0)
        return (prior >= 0.5, brief[item.id], strength[item.id], round(prior, 6), recency(item), tie[item.id])

    # The keep order, best first. A requirement's evidence, then pins; then, one line at a time, the line that
    # adds most: a SECOND line for a mandatory requirement the posting lists, the first line for a
    # nice-to-have, a second for a listed one. A line that adds nothing more (its requirements have their
    # lines) is worth its ``static`` only.
    shown: dict[str, int] = {req: 1 for req in by_summary}
    order: list[str] = []
    klass: dict[str, int] = {}

    def take(item_id: str, kind: int) -> None:
        order.append(item_id)
        klass[item_id] = kind
        for req in out.supports.get(item_id, {}):
            shown[req] = shown.get(req, 0) + 1

    for item_id in sorted(out.evidence, key=lambda item_id: (item_id in cited_evidence, len(out.evidence[item_id]), static(by_id[item_id])), reverse=True):
        take(item_id, 5)
    for item_id in sorted((item_id for item_id in pinned if item_id in by_id and item_id not in klass), key=lambda item_id: static(by_id[item_id]), reverse=True):
        take(item_id, 5)
    pool = {item_id for item_id in out.supports if item_id not in klass}
    listed = {asked.id for asked in (terms.asked if terms is not None else ()) if asked.words} | {requirement.id for requirement in requirements if requirement.cited}
    nice_evidence: dict[str, list[str]] = {}
    while pool:
        def gain(item_id: str) -> tuple:
            supported = out.supports[item_id]
            # A second line counts for what the posting LISTS; a keyword asked for by itself needs its one line only.
            second = [req for req in supported if req in mandatory and req in listed and shown.get(req, 0) == 1]
            first_nice = [req for req in supported if req not in mandatory and shown.get(req, 0) == 0]
            second_nice = [req for req in supported if req not in mandatory and req in listed and shown.get(req, 0) == 1]
            well = round(sum(supported[req] for req in (*second, *first_nice, *second_nice)), 6)
            # A further line is chosen for what it adds, a line of ordinary length before a very long one (which
            # supports more only because it holds more words), then for how well it supports it; its strength only then.
            fits, short, strong, *rest = static(by_id[item_id])
            adds = (len(second), len(first_nice), len(second_nice))
            return (any(adds), short, *adds, well, strong, fits, *rest)

        best_id = max(pool, key=gain)
        found = gain(best_id)
        if not found[0]:
            break  # nothing left adds a line to a requirement that lacks one
        if found[2] == 0 and found[3]:
            for req in out.supports[best_id]:
                if req not in mandatory and shown.get(req, 0) == 0:
                    nice_evidence.setdefault(best_id, []).append(req)
        take(best_id, 4 if found[2] else (3 if found[3] else 2))
        pool.discard(best_id)
    out.nice_evidence = {item_id: tuple(ids) for item_id, ids in nice_evidence.items()}
    def on_topic(item: MasterItem) -> float:
        """How well a line supports the mandatory things the posting LISTS (naming a keyword alone is not a topic)."""

        return round(topic.get(item.id, 0.0), 6)

    # WHAT THE POSTING'S TITLE NAMES (the module text): the roles and projects, and the lines of them that hold a
    # word of the title. Only the entry and the title are read, so a line added elsewhere changes neither.
    subject = _title_subject(title)
    named: set[str] = set()
    for entry in master.entries.values() if subject else ():
        if entry.section not in ("experience", "projects") or not entry.bullets:
            continue
        naming_lines = sum(1 for bullet in entry.bullets if subject & _title_subject(master.items[bullet].text))
        by_lines = naming_lines >= TITLE_LINES_SHARE * len(entry.bullets) and (naming_lines >= 2 or len(entry.bullets) == 1)
        if subject & _title_subject(" ".join((entry.heading, *entry.sublines))) or by_lines:
            named.add(entry.id)
    # (A word of the title in a line of any OTHER entry counts for nothing: "40 support agents" is not agent work.)
    out.titled = {item.id: count for item in lines if item.entry_id in named and (count := len(subject & _title_subject(item.text)))}

    # What is left adds no line a requirement lacks. A line that still supports a listed mandatory requirement
    # (a third line for it) stays longer than one that supports none, however recent that one is. Then the lines
    # of an entry the title names that hold a word of the title (the more of its words the longer), whatever the
    # profile shows. Then as the profile shows and by length; and BEFORE strength and recency, how well a line
    # supports by words a line of the posting that a cited row has answered.
    rest = sorted(
        (item for item in lines if item.id not in klass),
        key=lambda item: (on_topic(item) > 0, out.titled.get(item.id, 0), *static(item)[:2], on_topic(item), out.wording.get(item.id, 0.0), *static(item)[2:]),
        reverse=True,
    )
    for item in rest:
        klass[item.id] = 1 if on_topic(item) > 0 else 0
        order.append(item.id)
    tier = {item_id: (4 if item_id in cited_evidence else (3 if item_id in out.evidence else (2 if item_id in pinned else 0))) for item_id in order}
    # AN ENTRY THE TITLE NAMES KEEPS ITS BEST LINE (the module text). An entry whose best line is evidence or a pin
    # is kept by that line already.
    turn = {item_id: place for place, item_id in enumerate(order)}
    for entry in master.entries.values():
        held = [bullet for bullet in entry.bullets if bullet in tier]
        if entry.id not in named or not held:
            continue
        best = max(held, key=lambda item_id: (tier[item_id], klass[item_id], -turn[item_id]))
        out.title_floor[entry.id] = best
        tier[best] = max(tier[best], 1)
    for place, item_id in enumerate(order):
        # (the tier the fit reads, the class, the place in the keep order): a larger key stays longer.
        out.key[item_id] = (tier[item_id], klass[item_id], len(order) - place)
    for place, item_id in enumerate(sorted(out.key, key=out.key.__getitem__)):
        out.rank[item_id] = place
    return out


def select(
    master: Master,
    profile: SelectionProfile,
    posting: SelectionPosting | None = None,
    *,
    today: date | None = None,
    measure: Measure | None = None,
    max_pages: int = MAX_PAGES,
    fill: bool = True,
    title_floor: bool = True,
) -> Selected:
    """One selection: ``posting`` ``None`` gives the profile's standing pick, else the pick for that job.

    ``title_floor`` (0.1.11): ``False`` leaves out the 0.1.10.11 rule that an entry the posting's TITLE names keeps its
    best line (and the title's weight in the order of the rest). It is for the base of a MODEL's pick
    (``pick.settle``): a valid pick is not overridden by a word-match of the title ("Agent Platform" in a job about
    endpoint agents); the code selector's own selection keeps the rule.

    ``measure`` lays out resume markdown and answers ``(last page, fill)``; the default is the shipped
    template at ``FIT_SCALE``. ``today`` decides which roles are old (default: today's date).
    """

    today = today or date.today()
    terms = _job_terms(master, posting) if posting is not None else None
    scores = _score_items(master, terms, profile)
    subject = "this posting" if posting is not None else "this profile"
    roles = sorted(master.entries_in("experience"), key=_entry_sort_key)
    old = {entry.id for entry in roles if is_old_role(entry, today)}
    role_recency = {entry.id: (_OLD_RECENCY if entry.id in old else _rank(index, _RECENCY)) for index, entry in enumerate(roles)}

    def recency(item: MasterItem) -> float:
        if item.kind == KIND_OTHER:
            return _OTHER_RECENCY
        return role_recency.get(item.entry_id or "", _PROJECT_RECENCY)

    pins = tuple(item_id for item_id in profile.pins if item_id in master.items and master.items[item_id].kind in (KIND_BULLET, KIND_OTHER))
    # The summary first: a row of the assessment that cites the summary shown is covered by it.
    summary = _summary_choice(master, scores, posting)
    keys = _keys(
        master, terms, scores, recency, pins, posting.cited if posting is not None else (), summary.id if summary is not None else None,
        posting.title if posting is not None and title_floor else "",
    )
    cited_rows = {requirement.id for requirement in keys.requirements if requirement.cited}
    key = keys.key
    requirement_text = {requirement.id: requirement.text for requirement in keys.requirements}
    #: In the pick whatever a cap says: a requirement's evidence (mandatory or nice-to-have), a pin, and the best
    #: line of an entry the posting's title names.
    title_lines = set(keys.title_floor.values())
    forced = set(keys.evidence) | set(keys.nice_evidence) | {item_id for item_id in pins if item_id in key} | title_lines
    pick = _Pick()
    out: dict[str, tuple[str, str]] = {}  # a left-out line's (code, reason)
    for item_id, twin in keys.duplicates.items():
        out[item_id] = ("near_duplicate", f"says what the line {twin} says")

    def best_first(ids: Iterable[str]) -> list[str]:
        return sorted(ids, key=key.__getitem__, reverse=True)

    def reason_for(item: MasterItem) -> tuple[str, str]:
        if item.id in keys.evidence:
            first, *more = (requirement_text[req] for req in keys.evidence[item.id])
            what = "the line your assessment cites for" if keys.evidence[item.id][0] in cited_rows else "the strongest evidence for"
            return "requirement_evidence", f"{what}: {_label(first)}" + (f" (and {len(more)} more)" if more else "")
        if item.id in pins:
            return "pinned", "pinned on this profile"
        if item.id in title_lines:
            return "title_entry", "the best line of a role or project the posting's title names"
        if item.id in keys.nice_evidence:
            return "posting_wording", f"the best line for: {_label(requirement_text[keys.nice_evidence[item.id][0]])}"
        hits = scores.hits.get(item.id, {})
        if hits:
            return "names_keywords", "names " + _names(sorted(hits, key=lambda term: (-hits[term], term)))
        if keys.supports.get(item.id) or keys.wording.get(item.id):
            return "posting_wording", "matches the posting's wording"
        if keys.titled.get(item.id):
            return "posting_title", "names what the posting's title names"
        if scores.prior.get(item.id, 0.0) >= 0.5:
            return "profile_focus", "fits this profile"
        if posting is None:
            return "strongest_remaining", "strongest remaining line of its role"
        return "general", "supports no requirement of this posting; shown because there is room"

    # 1. Summary: one variant.
    if summary is not None:
        pick.summary = [summary.id]
        for item in master.in_section("summary"):
            if item.id != summary.id:
                out[item.id] = ("summary_other_variant", f"another summary fits {subject} better")

    # 2. Experience: the best lines of each role under its cap; a requirement's evidence whatever the cap.
    caps = {entry.id: (LENGTH_RULE.old_role_bullets if entry.id in old else _rank(index, PICK_CAPS)) for index, entry in enumerate(roles)}
    hard = {entry.id: (LENGTH_RULE.old_role_bullets if entry.id in old else _rank(index, HARD_CAPS)) for index, entry in enumerate(roles)}

    def about(item_id: str) -> bool:
        """The line supports something the posting asks for: a cap for general lines does not leave it out."""

        return keys.klass(item_id) > 0

    for entry in roles:
        ranked = best_first(bullet for bullet in entry.bullets if bullet in key)
        chosen = [bullet for place, bullet in enumerate(ranked) if place < caps[entry.id] or bullet in forced or (about(bullet) and place < hard[entry.id])]
        pick.entries[entry.id] = chosen
        for bullet in ranked:
            if bullet not in chosen:
                out[bullet] = ("role_limit", f"over the limit of {caps[entry.id]} lines for this role; the lines shown score higher for {subject}")

    # 3. Projects: the best few, a few bullets each; a project that holds a requirement's evidence is one of them.
    projects = master.entries_in("projects")
    project_lines = {entry.id: best_first(bullet for bullet in entry.bullets if bullet in key) for entry in projects}
    by_worth = sorted(
        (entry for entry in projects if project_lines[entry.id]),
        key=lambda entry: ([key[bullet] for bullet in project_lines[entry.id][:PROJECT_BULLETS]], entry.id), reverse=True,
    )
    shown_projects = {entry.id for entry in by_worth[:MAX_PROJECTS]} | {entry.id for entry in projects if forced & set(project_lines[entry.id])}
    for entry in projects:
        ranked = project_lines[entry.id]
        chosen = [
            bullet for place, bullet in enumerate(ranked)
            if entry.id in shown_projects and (place < PROJECT_BULLETS or bullet in forced or (about(bullet) and place < PROJECT_HARD_BULLETS))
        ]
        if chosen:
            pick.entries[entry.id] = sorted(chosen, key=lambda bullet: master.items[bullet].order)
        for bullet in ranked:
            if bullet not in chosen:
                out[bullet] = (
                    ("project_limit", f"over the limit of {PROJECT_BULLETS} lines per project") if entry.id in shown_projects
                    else ("project_not_shown", f"its project is not among the {MAX_PROJECTS} most relevant to {subject}")
                )

    # 4. Education: every degree; a line under it only when it names a posting keyword.
    for entry in master.entries_in("education"):
        pick.entries[entry.id] = [bullet for bullet in entry.bullets if (scores.hits.get(bullet) or bullet in forced) and bullet in key]
        for bullet in entry.bullets:
            if bullet not in pick.entries[entry.id] and bullet not in out:
                out[bullet] = ("education_detail", "a detail under a degree is shown only when it names something the posting asks for")

    # 5. Other: the best few.
    others = best_first(item.id for item in master.in_section("other") if item.id in key)
    chosen_other = [item_id for place, item_id in enumerate(others) if place < MAX_OTHER or item_id in forced]
    pick.other = sorted(chosen_other, key=lambda item_id: master.items[item_id].order)
    for item_id in others:
        if item_id not in chosen_other:
            out[item_id] = ("other_limit", f"over the limit of {MAX_OTHER} Other lines; the lines shown score higher for {subject}")

    # 6. Skills: the whole section. What the posting asks for first (matched inside a group), then what the
    #    picked lines name, then the rest; the order is settled here, before the fit, so a cut never moves a skill.
    all_skills = master.skills()
    atoms = {skill: skill_atoms(skill) for skill in all_skills}
    asked_kind: dict[str, str] = {}
    if terms is not None:
        for kind, names in (("must", terms.keywords.must), ("nice", terms.keywords.nice)):
            for term in names:
                for skill in all_skills:
                    if skill not in asked_kind and any(mentions(atom, term) for atom in atoms[skill]):
                        asked_kind[skill] = kind
    picked_text = " ".join(master.items[i].text for i in pick.bullet_ids())
    named_by_pick = {skill for skill in all_skills if skill not in asked_kind and any(mentions(picked_text, atom) for atom in atoms[skill])}
    # What the posting asks for in the posting's own order, then by name: never in the order the master lists
    # its skills, so the same skills listed or grouped another way print in the same places.
    asked_at: dict[str, tuple[int, int]] = {}
    if terms is not None:
        for kind_place, names in enumerate((terms.keywords.must, terms.keywords.nice)):
            for place, term in enumerate(names):
                for skill in all_skills:
                    if skill not in asked_at and skill in asked_kind and any(mentions(atom, term) for atom in atoms[skill]):
                        asked_at[skill] = (kind_place, place)
    skill_order = sorted(asked_kind, key=lambda skill: (asked_at[skill], skill.casefold()))
    skill_order += sorted((skill for skill in all_skills if skill not in asked_kind), key=str.casefold)
    pick.skills = list(skill_order)

    def view(trial: _Pick) -> _Pick:
        """``trial`` as it prints: a role's bullets best first (the master's order inside a tie)."""

        shown = trial.copy()
        for entry_id, bullets in shown.entries.items():
            if master.entries[entry_id].section == "experience":
                shown.entries[entry_id] = sorted(bullets, key=key.__getitem__, reverse=True)
        return shown

    measured: dict[str, tuple[int, float]] = {}
    lay_out = measure or _shipped_measure

    def end_of(trial: _Pick) -> tuple[int, float]:
        markdown = _render(master, view(trial), ids=False)
        if markdown not in measured:
            measured[markdown] = lay_out(markdown)
        return measured[markdown]

    # 7. FIT: the cuts in the one order they may be applied (the module text): lowest value first.
    recent = {entry.id for entry in roles if entry.id not in old}
    floors = {entry.id: _rank(index, FLOORS) for index, entry in enumerate(roles) if entry.id in recent}
    remaining = {entry_id: len(bullets) for entry_id, bullets in pick.entries.items()}
    cuts: list[_Cut] = []

    def cut_lines(ids: Iterable[str], *, forced_cut: bool) -> None:
        for item_id in sorted(ids, key=key.__getitem__):
            item = master.items[item_id]
            if item.kind == KIND_OTHER:
                cuts.append(_Cut("other", item_id, forced_cut))
                continue
            entry_id = item.entry_id or ""
            if remaining[entry_id] <= floors.get(entry_id, 0):
                continue  # a recent role keeps its best line
            remaining[entry_id] -= 1
            cuts.append(_Cut("bullet", item_id, forced_cut))

    cuttable = [*(bullet for entry_id, bullets in pick.entries.items() if master.entries[entry_id].section != "education" for bullet in bullets), *pick.other]
    kept_for_last = set(keys.evidence) | set(pins) | title_lines
    # What adds nothing the posting asks for goes first: bullets, then the Other lines that name nothing it asks
    # for. Then the rest by value; an Other line that names a keyword or supports a requirement (a certification
    # the posting asks for, a talk on its subject: one short line each) goes after the bullets.
    free = [item_id for item_id in cuttable if item_id not in kept_for_last]
    other_about = {item_id for item_id in free if master.items[item_id].kind == KIND_OTHER and keys.supports.get(item_id)}
    # A Skills section too long to print whole (``SKILLS_WHOLE``): the names past that count that nothing asks for
    # and no picked line names are worth less than any line, and go first.
    plain = [skill for skill in skill_order if skill not in asked_kind and skill not in named_by_pick]
    surplus = plain[max(0, SKILLS_WHOLE - (len(skill_order) - len(plain))):]
    cuts += [_Cut("skill", skill) for skill in reversed(surplus)]
    cut_lines((item_id for item_id in free if keys.klass(item_id) == 0 and master.items[item_id].kind == KIND_BULLET), forced_cut=False)
    cut_lines((item_id for item_id in free if master.items[item_id].kind == KIND_OTHER and item_id not in other_about), forced_cut=False)
    cut_lines((item_id for item_id in free if keys.klass(item_id) > 0 and master.items[item_id].kind == KIND_BULLET), forced_cut=False)
    cut_lines(other_about, forced_cut=False)
    # Then the one line of an entry the posting's title names: every line that is not evidence or a pin went first.
    cut_lines((item_id for item_id in cuttable if item_id in title_lines and item_id not in keys.evidence and item_id not in pins), forced_cut=True)
    gone = set(surplus)
    # The rest of the Skills section only when no line is left to cut: what nothing asks for and no picked line
    # names first, what the posting asks for last.
    kept_skills = [skill for skill in skill_order if skill not in gone]
    cuts += [_Cut("skill", skill) for skill in sorted(kept_skills, key=lambda skill: (skill in asked_kind, skill in named_by_pick, -skill_order.index(skill)))]
    cut_lines((item_id for item_id in cuttable if item_id in pins and item_id not in keys.evidence), forced_cut=True)
    cut_lines((item_id for item_id in cuttable if item_id in keys.evidence), forced_cut=True)

    def without(gone: Iterable[_Cut]) -> _Pick:
        trial = pick.copy()
        for cut in gone:
            if cut.kind == "bullet":
                entry_id = master.items[cut.id].entry_id or ""
                trial.entries[entry_id] = [b for b in trial.entries[entry_id] if b != cut.id]
            elif cut.kind == "other":
                trial.other = [i for i in trial.other if i != cut.id]
            else:
                trial.skills = [name for name in trial.skills if name != cut.id]
        # No role or project is printed without a bullet: an old role or a project whose lines all went goes whole.
        for entry_id in [entry_id for entry_id, bullets in trial.entries.items() if not bullets and master.entries[entry_id].section != "education"]:
            del trial.entries[entry_id]
        return trial

    before = end_of(pick)
    applied: list[_Cut] = []
    if before[0] > max_pages:
        low, high = 0, len(cuts)  # the fewest cuts that fit; every cut applied is the most the rules allow
        while low < high:
            mid = (low + high) // 2
            if end_of(without(cuts[:mid]))[0] <= max_pages:
                high = mid
            else:
                low = mid + 1
        applied = list(cuts[:low])
    fitted = without(applied)

    # 8. FILL: room left on the last page goes to the best lines not shown: the ones just cut, and the
    #    bullets of the recent roles and of the shown projects that a cap left out. Best first; an old role's
    #    line that supports nothing never comes back.
    added_to_fill: list[str] = []

    def with_fill(gone: Iterable[_Cut], added: Iterable[str]) -> _Pick:
        trial = without(gone)
        for item_id in added:
            entry_id = master.items[item_id].entry_id or ""
            trial.entries.setdefault(entry_id, []).append(item_id)
            if master.entries[entry_id].section == "projects":
                trial.entries[entry_id].sort(key=lambda bullet: master.items[bullet].order)
        return trial

    if fill and end_of(fitted)[0] <= max_pages:
        def skills_back(names: set[str]) -> None:
            nonlocal fitted
            for cut in [cut for cut in reversed(applied) if cut.kind == "skill" and cut.id in names]:
                trial = with_fill([item for item in applied if item != cut], added_to_fill)
                if end_of(trial)[0] <= max_pages:
                    applied.remove(cut)
                    fitted = trial

        skills_back(set(skill_order) - gone)  # the Skills section proper first: it is kept whole when it fits
        cut_back = {cut.id: cut for cut in applied if cut.kind != "skill"}
        in_pick = set(cuttable)
        limits = {**{entry.id: hard[entry.id] for entry in roles if entry.id in recent}, **{entry_id: PROJECT_HARD_BULLETS for entry_id in shown_projects}}
        extras = [bullet for entry_id in limits for bullet in master.entries[entry_id].bullets if bullet in key and bullet not in in_pick]
        tries = 0
        for item_id in best_first([*cut_back, *extras]):
            if tries == FILL_TRIES:
                break
            item = master.items[item_id]
            if item.kind == KIND_BULLET and item.entry_id in old and keys.klass(item_id) == 0:
                continue
            if item_id in cut_back:
                trial = with_fill([cut for cut in applied if cut is not cut_back[item_id]], added_to_fill)
            else:
                if len(fitted.entries.get(item.entry_id or "", ())) >= limits[item.entry_id or ""]:
                    continue
                trial = with_fill(applied, [*added_to_fill, item_id])
            tries += 1
            if end_of(trial)[0] > max_pages:
                continue
            fitted = trial
            if item_id in cut_back:
                applied.remove(cut_back[item_id])
            else:
                added_to_fill.append(item_id)
        skills_back(gone)  # last, the names of a too-long Skills section that nothing asks for

    final = view(fitted)
    pages, last_fill = end_of(fitted)

    # The reasons: every line of the master, shown or not.
    shown_ids = set(final.summary) | set(final.bullet_ids()) | set(final.other)
    dropped = [entry for entry in roles if entry.id not in final.entries]
    dropped_ids = {entry.id for entry in dropped}
    why: dict[str, tuple[str, str]] = {}
    for item_id in shown_ids:
        why[item_id] = reason_for(master.items[item_id])
    if summary is not None:
        why[summary.id] = reason_for(summary) if scores.hits.get(summary.id) else ("summary_variant", f"the summary that fits {subject} best")
    length_cuts: list[LengthCut] = []
    role_cut: set[str] = set()
    for cut in applied:
        if cut.kind == "skill":
            length_cuts.append(LengthCut(cut.id, "skill", "cut_for_length", "cut for length: the Skills section did not fit"))
            continue
        entry_id = master.items[cut.id].entry_id or ""
        if cut.forced:
            code, reason = "cut_conflict", "cut for length although the rules say it stays: it does not fit the page limit (see conflicts)"
        elif entry_id in dropped_ids:
            code, reason = "cut_role_dropped", f"cut for length: no line of this older role is evidence for {subject}"
        else:
            code, reason = "cut_lowest_value", f"cut for length: lowest value for {subject}"
        length_cuts.append(LengthCut(cut.id, cut.kind, code, reason))
        out[cut.id] = (code, reason)
        if entry_id in dropped_ids and entry_id not in role_cut and not any(b in shown_ids for b in master.entries[entry_id].bullets):
            if all(b in {c.id for c in applied[: applied.index(cut) + 1]} for b in pick.entries[entry_id]):
                role_cut.add(entry_id)
                length_cuts.append(LengthCut(entry_id, "role", "cut_role_dropped", f"cut for length: no line of this older role is evidence for {subject}"))
    for entry in dropped:
        for bullet in entry.bullets:
            if out.get(bullet, ("role_limit",))[0] == "role_limit":
                out[bullet] = ("role_dropped", "its role was cut for length")
    for item_id in added_to_fill:
        code, reason = reason_for(master.items[item_id])
        why[item_id] = ("room_left", f"{reason} (room left on the page)")

    lines = tuple(
        LineReason(item.id, item.id in shown_ids, *(why[item.id] if item.id in shown_ids else out.get(item.id, ("not_picked", f"scores lower for {subject} than the lines shown"))))
        for item in master.items.values() if item.kind in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER)
    )
    shown_skills = set(final.skills)
    final_text = " ".join(master.items[i].text for i in fitted.bullet_ids())
    skill_reasons: list[SkillReason] = []
    for skill in final.skills:
        if asked_kind.get(skill) == "must":
            skill_reasons.append(SkillReason(skill, True, "posting_must", "the posting asks for it"))
        elif asked_kind.get(skill) == "nice":
            skill_reasons.append(SkillReason(skill, True, "posting_nice", "the posting lists it as a plus"))
        elif any(mentions(final_text, atom) for atom in atoms[skill]):
            skill_reasons.append(SkillReason(skill, True, "named_by_line", "a shown line names it"))
        else:
            skill_reasons.append(SkillReason(skill, True, "listed", "your master lists it; the Skills section is kept whole"))
    skill_reasons += [
        SkillReason(skill, False, "cut_for_length", "cut for length: the Skills section did not fit") for skill in all_skills if skill not in shown_skills
    ]

    # What the page budget kept out although the rules say it stays.
    conflicts: list[Conflict] = []
    for requirement in keys.requirements:
        if not requirement.mandatory or not requirement.supporters or requirement.supporters[0] in shown_ids:
            continue
        covered = any(item_id in shown_ids for item_id in requirement.supporters)
        if not requirement.cited:
            why = "the strongest evidence for this requirement does not fit the page limit beside the other requirements' evidence" + (
                "; a weaker line still covers it" if covered else "; no line covers it now")
        elif master.items[requirement.supporters[0]].kind == KIND_SUMMARY:
            why = "the assessment cites a summary this resume does not show; no line it cites is shown"
        else:
            why = "the line the assessment cites for this requirement does not fit the page limit beside the other requirements' evidence" + (
                "; another line it cites is shown" if covered else "; no line it cites is shown now")
        conflicts.append(Conflict("mandatory_evidence", (requirement.supporters[0],), why, requirement.id, requirement.text, covered))
    for entry_id, item_id in keys.title_floor.items():
        if entry_id not in final.entries:
            conflicts.append(Conflict(
                "title_entry", (entry_id, item_id),
                "the posting's title names this role or project, and its best line does not fit the page limit beside the requirements' evidence",
            ))
    missing_pins = tuple(item_id for item_id in pins if item_id in key and item_id not in shown_ids)
    if missing_pins:
        conflicts.append(Conflict("must_keep", missing_pins, "these pinned lines do not fit the page limit beside the requirements' evidence"))
    if pages > max_pages:
        conflicts.append(Conflict("over_budget", (), f"{pages} pages after every cut the rules allow; the limit is {max_pages}"))

    markdown = _render(master, final, ids=False)
    coverage: dict[str, tuple[str, ...]] = {}
    if terms is not None:
        coverage = {
            "must_covered": tuple(term for term in terms.keywords.must if mentions(markdown, term)),
            "must_missing": tuple(term for term in terms.keywords.must if not mentions(markdown, term)),
            "nice_covered": tuple(term for term in terms.keywords.nice if mentions(markdown, term)),
            "nice_missing": tuple(term for term in terms.keywords.nice if not mentions(markdown, term)),
            "must_missing_in_master": tuple(
                term for term in terms.keywords.must if not any(mentions(item.text, term) for item in master.items.values())
            ),
        }
    printed = [entry for section in ("experience", "projects", "education") for entry in sorted(master.entries_in(section), key=_entry_sort_key)]
    return Selected(
        selector_version=SELECTOR_VERSION,
        summary=tuple(final.summary),
        entries={entry.id: tuple(final.entries[entry.id]) for entry in printed if entry.id in final.entries},
        skills=tuple(final.skills),
        other=tuple(final.other),
        markdown=markdown,
        markdown_with_ids=_render(master, final, ids=True),
        lines=lines,
        skill_reasons=tuple(skill_reasons),
        max_pages=max_pages,
        pages_before_fit=before[0],
        pages=pages,
        last_page_fill=round(last_fill, 2),
        layout_queries=len(measured),
        cut_for_length=tuple(length_cuts),
        added_to_fill=tuple(added_to_fill),
        only_evidence={},
        shown_instead={},
        roles_dropped=tuple(entry.id for entry in dropped),
        keywords=terms.keywords if terms is not None else None,
        coverage=coverage,
        values={item_id: float(place) for item_id, place in keys.rank.items()},
        requirements=keys.requirements,
        evidence_for={item_id: ids for item_id, ids in keys.evidence.items() if item_id in shown_ids},
        conflicts=tuple(conflicts),
        duplicates=dict(keys.duplicates),
        title_entries=dict(keys.title_floor),
    )


def word_supporters(master: Master, requirement: str) -> tuple[str, ...]:
    """The lines of ``master`` that support one requirement BY WORDS: the selector's own rule, with no citation.

    ``requirement`` is read as a posting that lists that one line, so the answer is what ``select`` counts as
    its support when no assessment cites a line for it (the line's words and the keywords it names).  Strongest
    first.  What a report reads to tell a cited line that is not shown with nothing in its place from one whose
    requirement another shown line supports.  Pure.
    """

    terms = _job_terms(master, SelectionPosting("", f"Requirements:\n- {requirement}\n"))
    keys = _keys(master, terms, _score_items(master, terms, SelectionProfile()), lambda _item: 1.0, ())
    return tuple(dict.fromkeys(item_id for found in keys.requirements for item_id in found.supporters))


def render_selection(master: Master, item_ids: Iterable[str], skills: Iterable[str], *, ids: bool = False) -> str:
    """The resume markdown of a STORED selection: the entries and lines ``item_ids`` names, and ``skills``.

    What the master no longer has is left out (a retired line, a skill it no longer lists), and an edited
    line prints as the master words it now. An entry prints when ``item_ids`` names it or one of its lines.
    For the ids and skills of a ``Selected`` this is its ``markdown``; ``ids`` keeps each line's master id in
    a trailing comment (its ``markdown_with_ids``).
    """

    wanted = list(dict.fromkeys(item_ids))
    pick = _Pick()
    for item_id in wanted:
        entry = master.entries.get(item_id)
        if entry is not None:
            pick.entries.setdefault(entry.id, [])
            continue
        item = master.items.get(item_id)
        if item is None:
            continue
        if item.kind == KIND_SUMMARY:
            pick.summary.append(item.id)
        elif item.kind == KIND_OTHER:
            pick.other.append(item.id)
        elif item.kind == KIND_BULLET:
            pick.entries.setdefault(item.entry_id or "", []).append(item.id)
    listed = {name.casefold(): name for name in master.skills()}
    pick.skills = list(dict.fromkeys(listed[name.casefold()] for name in skills if name.casefold() in listed))
    return _render(master, pick, ids=ids)


# --- re-making a pick: the previous selection and the new one, on the same current sources ----------


@dataclass(frozen=True)
class SelectionChecks:
    """One selection (the ids and skills it shows) against the CURRENT master, requirements and page budget.

    Separate checks, never one score: ``lost`` (mandatory requirements the master supports with no line
    shown), ``weak`` (those whose evidence, the strongest line, is not shown), ``pins_missing``, and the page
    constraint (``pages``, ``empty_entries``).  ``invalid`` names the ids that are not current lines of the
    master: a retired line, or one corrected since the selection was made."""

    invalid: tuple[str, ...]
    covered: tuple[str, ...]
    lost: tuple[str, ...]
    strongest: tuple[str, ...]
    weak: tuple[str, ...]
    pins_shown: tuple[str, ...]
    pins_missing: tuple[str, ...]
    pages: int
    max_pages: int
    empty_entries: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.invalid

    @property
    def fits(self) -> bool:
        return self.pages <= self.max_pages and not self.empty_entries

    def to_json(self) -> dict[str, object]:
        return {
            "valid": self.valid, "invalid": list(self.invalid), "covered": list(self.covered), "lost": list(self.lost),
            "strongest": list(self.strongest), "weak": list(self.weak), "pins_missing": list(self.pins_missing),
            "pages": self.pages, "max_pages": self.max_pages, "fits": self.fits, "empty_entries": list(self.empty_entries),
        }


def check_selection(
    master: Master, requirements: Iterable[Requirement], item_ids: Iterable[str], skills: Iterable[str], *, stale: Iterable[str] = (),
    pins: Iterable[str] = (), measure: Measure | None = None, max_pages: int = MAX_PAGES,
) -> SelectionChecks:
    """``item_ids`` and ``skills`` (a stored selection, or a ``Selected``'s) checked against the current sources.

    ``requirements`` are the CURRENT ones (a ``Selected.requirements`` made on this master); ``stale`` the ids
    the master has corrected since the selection was made (the caller knows the revision it was made from).
    """

    wanted = list(dict.fromkeys(item_ids))
    gone = set(stale)
    invalid = tuple(item_id for item_id in wanted if (item_id not in master.items and item_id not in master.entries) or item_id in gone)
    shown = {item_id for item_id in wanted if item_id in master.items}
    covered: list[str] = []
    lost: list[str] = []
    strongest: list[str] = []
    weak: list[str] = []
    for requirement in requirements:
        if not requirement.mandatory or not requirement.supporters:
            continue
        (covered if shown & set(requirement.supporters) else lost).append(requirement.id)
        (strongest if requirement.supporters[0] in shown else weak).append(requirement.id)
    pinned = [item_id for item_id in dict.fromkeys(pins) if item_id in master.items]
    markdown = render_selection(master, wanted, skills)
    named = {item_id for item_id in wanted if item_id in master.entries} | {master.items[item_id].entry_id or "" for item_id in shown}
    empty = tuple(
        entry_id for entry_id in named
        if entry_id in master.entries and master.entries[entry_id].section in ("experience", "projects")
        and not any(bullet in shown for bullet in master.entries[entry_id].bullets)
    )
    return SelectionChecks(
        invalid=invalid, covered=tuple(covered), lost=tuple(lost), strongest=tuple(strongest), weak=tuple(weak),
        pins_shown=tuple(item_id for item_id in pinned if item_id in shown), pins_missing=tuple(item_id for item_id in pinned if item_id not in shown),
        pages=(measure or _shipped_measure)(markdown)[0], max_pages=max_pages, empty_entries=tuple(sorted(empty)),
    )


REMAKE_NEW = "new"
REMAKE_PREVIOUS = "previous"
REMAKE_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Remake:
    """A pick made again, beside the one it would replace (``compare_selections``).

    ``decision``: ``new`` (the new selection regresses on no check: it replaces the previous one),
    ``previous`` (the previous selection is still valid against the current sources and the new one
    regresses on a check: the previous one is kept) or ``unresolved`` (the new one regresses and the
    previous one cannot be kept: neither is chosen, the problem is shown).  ``regressions`` names the
    checks the new selection is worse on; ``problems`` why the previous one cannot be kept."""

    decision: str
    previous: SelectionChecks
    new: SelectionChecks
    regressions: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "decision": self.decision, "regressions": list(self.regressions), "problems": list(self.problems),
            "previous": self.previous.to_json(), "new": self.new.to_json(),
        }


def compare_selections(
    master: Master, new: Selected, previous_ids: Iterable[str], previous_skills: Iterable[str], *, stale: Iterable[str] = (),
    pins: Iterable[str] = (), measure: Measure | None = None,
) -> Remake:
    """The re-make rule (0110-10-15): which of a stored selection and the one just made stands.

    Both are checked against the SAME current sources (the master as it is, ``new``'s requirements, the page
    budget) on the separate checks of ``SelectionChecks``; nothing is added up, so a lost requirement is
    never offset by more skills or lines.  The previous selection is kept only when it is still valid (it
    shows no retired line and none in ``stale``, the ids corrected since it was made), still meets the page
    constraint, AND the new one regresses on a check.  When the new one regresses and the previous one
    cannot be kept, the answer is ``unresolved``: the caller shows the problem and picks neither silently.
    """

    pins = tuple(pins)
    was = check_selection(master, new.requirements, previous_ids, previous_skills, stale=stale, pins=pins, measure=measure, max_pages=new.max_pages)
    now = check_selection(
        master, new.requirements, (*new.summary, *(item for entry_id, bullets in new.entries.items() for item in (entry_id, *bullets)), *new.other),
        new.skills, pins=pins, measure=measure, max_pages=new.max_pages,
    )
    by_id = {requirement.id: requirement.text for requirement in new.requirements}
    regressions: list[str] = []
    gone = [req for req in was.covered if req not in now.covered]
    if gone:
        regressions.append("mandatory coverage: no line now for " + "; ".join(_label(by_id[req]) for req in gone))
    weaker = [req for req in was.strongest if req not in now.strongest and req not in gone]
    if weaker:
        regressions.append("evidence strength: the strongest line is no longer shown for " + "; ".join(_label(by_id[req]) for req in weaker))
    unpinned = [item_id for item_id in was.pins_shown if item_id not in now.pins_shown]
    if unpinned:
        regressions.append("must-keep lines no longer shown: " + ", ".join(unpinned))
    if was.fits and not now.fits:
        regressions.append(f"page fit: {now.pages} pages" + (f", no bullet under {', '.join(now.empty_entries)}" if now.empty_entries else ""))
    if not regressions:
        return Remake(REMAKE_NEW, was, now)
    problems: list[str] = []
    if was.invalid:
        problems.append("the previous selection shows lines the master has retired or corrected: " + ", ".join(was.invalid))
    if not was.fits:
        problems.append(f"the previous selection no longer meets the page limit ({was.pages} pages)")
    if not problems:
        return Remake(REMAKE_PREVIOUS, was, now, tuple(regressions))
    return Remake(REMAKE_UNRESOLVED, was, now, tuple(regressions), tuple(problems))


# --- the evidence view ------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceView:
    """The lines of the master most relevant to one posting, as many as fit in ``cap`` characters."""

    selector_version: str
    summary: tuple[str, ...]
    entries: dict[str, tuple[str, ...]]
    skills: tuple[str, ...]
    other: tuple[str, ...]
    markdown: str
    cap: int
    #: Bullets shown, and how many the master has.
    bullets: int
    bullets_total: int
    #: 0.1.11 N3: ``markdown`` carries each line's and each entry's master id in a trailing comment, and each note on a labelled line.
    ids: bool = False
    #: How many of the lines and entries shown are followed by a private note line (only with ``ids``).
    notes: int = 0

    @property
    def chars(self) -> int:
        return len(self.markdown)

    @property
    def within_cap(self) -> bool:
        return self.chars <= self.cap

    def item_ids(self) -> tuple[str, ...]:
        return (*self.summary, *(bullet for bullets in self.entries.values() for bullet in bullets), *self.other)

    def to_json(self) -> dict[str, object]:
        return {
            "selector_version": self.selector_version, "cap": self.cap, "chars": self.chars, "within_cap": self.within_cap,
            "bullets": self.bullets, "bullets_total": self.bullets_total, "summary": list(self.summary),
            "entries": [{"id": entry_id, "picked": list(bullets)} for entry_id, bullets in self.entries.items()],
            "skills": list(self.skills), "other": list(self.other), "markdown": self.markdown,
        }


def evidence_view(
    master: Master, profile: SelectionProfile, posting: SelectionPosting, *, today: date | None = None, cap: int = EVIDENCE_CAP,
    ids: bool = False,
) -> EvidenceView:
    """What an assessment of ``posting`` would read of the master, within ``cap`` characters.

    Every role and degree heading, one summary (chosen as ``select`` chooses it), every skill and every
    Other line, then bullets by their value for the posting until the next one would pass ``cap``. No page
    fit: this view is for a model to read, not to print. Bullets print in the master's order.

    ``ids`` (0.1.11 N3, SPEC 1.1): each selectable line and each entry carries its master id in the trailing
    comment the master itself uses (``<!-- id:b-23b6dc -->``): a model cannot pick ids it does not see. A line or
    entry that has a note is followed by a line of its own, labelled (``<!-- private note: ... -->``, ``_render``):
    this view, for the assess prompt, is the only text a note is ever rendered into. The cap is then measured on
    that text, so a view with ids shows the same lines or fewer, never more than ``cap`` characters.
    """

    today = today or date.today()
    scores = _score_items(master, _job_terms(master, posting), profile)
    roles = sorted(master.entries_in("experience"), key=_entry_sort_key)
    recency = {entry.id: (_OLD_RECENCY if is_old_role(entry, today) else _rank(index, _RECENCY)) for index, entry in enumerate(roles)}
    bullets = [item for item in master.items.values() if item.kind == KIND_BULLET]
    ranked = sorted(bullets, key=lambda item: (-_value(item, scores, {}, recency.get(item.entry_id or "", _PROJECT_RECENCY)), item.order))
    summary = _summary_choice(master, scores, posting)
    pick = _Pick(
        summary=[summary.id] if summary is not None else [],
        entries={entry.id: [] for entry in master.entries.values() if entry.section in ("experience", "education")},
        skills=master.skills(),
        other=[item.id for item in master.in_section("other")],
    )
    for item in ranked:
        trial = pick.copy()
        trial.entries.setdefault(item.entry_id or "", []).append(item.id)
        if len(_render(master, trial, ids=ids, notes=ids)) > cap:
            break
        pick = trial
    for entry_id in pick.entries:
        pick.entries[entry_id] = sorted(pick.entries[entry_id], key=lambda bullet: master.items[bullet].order)
    printed = [entry for section in ("experience", "projects", "education") for entry in sorted(master.entries_in(section), key=_entry_sort_key)]
    return EvidenceView(
        selector_version=SELECTOR_VERSION,
        summary=tuple(pick.summary),
        entries={entry.id: tuple(pick.entries[entry.id]) for entry in printed if entry.id in pick.entries},
        skills=tuple(pick.skills),
        other=tuple(pick.other),
        markdown=_render(master, pick, ids=ids, notes=ids),
        cap=cap,
        bullets=len(pick.bullet_ids()),
        bullets_total=len(bullets),
        ids=ids,
        notes=sum(1 for item_id in (*pick.summary, *pick.entries, *pick.bullet_ids(), *pick.other) if _note(master, item_id)) if ids else 0,
    )


__all__ = [
    "CITED_ABOUT_SHARE",
    "CITED_ANSWERED_SHARE",
    "CitedRequirement",
    "Conflict",
    "EVIDENCE_CAP",
    "EvidenceView",
    "FIT_SCALE",
    "FLOORS",
    "HARD_CAPS",
    "LengthCut",
    "LineReason",
    "MAX_OTHER",
    "MAX_PAGES",
    "MAX_PROJECTS",
    "Measure",
    "NOTE_LABEL",
    "PICK_CAPS",
    "PROJECT_BULLETS",
    "PROJECT_HARD_BULLETS",
    "REMAKE_NEW",
    "REMAKE_PREVIOUS",
    "REMAKE_UNRESOLVED",
    "Remake",
    "Requirement",
    "SELECTOR_VERSION",
    "SKILLS_WHOLE",
    "Selected",
    "SelectionChecks",
    "SelectionPosting",
    "SelectionProfile",
    "SkillReason",
    "TITLE_LINES_SHARE",
    "UNMEASURED_PAGES",
    "check_selection",
    "compare_selections",
    "evidence_view",
    "is_old_role",
    "render_selection",
    "select",
    "skill_atoms",
    "word_supporters",
]
