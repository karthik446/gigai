"""The master resume's selector (0.1.10.9 master P2): which lines a resume shows, by code alone.

``select`` picks the lines of the master (``master_resume.Master``) that one
resume shows, for a profile alone (its standing pick) or for one posting, and
fits the pick to ``LENGTH_RULE.max_pages`` pages. ``evidence_view`` picks the
lines most relevant to one posting under a character budget instead (what an
assessment would read). Nothing here calls a model, reads a file or writes:
the same master, profile, posting and day give the same ids.

The rules (the operator's decisions 1 and 2):

1. SCORE every line against the posting: the posting's keywords it names
   (``posting_keywords``: a must-have counts 2, a nice-to-have 1, and less
   each time the pick already covers the term), word overlap with the
   posting's requirement lines weighted by how rare the word is in the
   master, the profile's prior, the line's derived strength, and how recent
   its role is. The master's whole Skills list is the keyword vocabulary.
2. PICK: one summary variant (the posting's title and the profile decide what
   kind of engineer the summary names; keywords only break ties), bullets per
   role under a cap that falls with age (``PICK_CAPS``; an old role, by
   ``LENGTH_RULE``, gets ``old_role_bullets``), the best ``MAX_PROJECTS``
   projects, ``MAX_OTHER`` Other lines, and up to ``MAX_SKILLS`` skills: the
   posting's must-haves the master lists, then its nice-to-haves, then what
   the shown bullets name.
3. FIT by measuring, never estimating: the pick's markdown is laid out with
   the shipped PDF template (``measure_markdown``) and cuts are applied in one
   fixed order until it fits; the fewest cuts that fit are found by binary
   search. THE CUT ORDER: the oldest role's bullets, then that role; the next
   oldest; only then the lowest-value line anywhere above the floors, then
   Other lines. ``FLOORS`` keep every recent role present.
4. THE ONLY EVIDENCE of a must-have is never cut. When an old role's line is
   the last shown line naming a must-have, a recent role's unshown line that
   names the same thing is shown in its place first; when the master has no
   such line, the old line stays and its role is shortened to it, not dropped.
5. FILL: room left on the last page goes to the best unshown bullets of the
   recent roles (up to ``HARD_CAPS``).
6. Nothing is reworded: every shown line is a master line, by id, and every
   line of the master, shown or not, carries the reason (``LineReason``).

Weights, caps and floors are the spike's (``research/master-resume-spike``),
set by hand on two profiles and three postings; ``SELECTOR_VERSION`` names
them, so changing one is a deliberate change of every stored selection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
import math
import re
from typing import TypeVar

from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SUMMARY, Master, MasterEntry, MasterItem
from .posting_keywords import PostingKeywords, extract_keywords, mentions
from .tailor_no_loss import OWNERSHIP_FAMILIES, normalize
from .tailored_resume import LENGTH_RULE

#: Names the scoring weights, caps, floors and cut order below; stored with a selection.
SELECTOR_VERSION = "sel-1"
MAX_PAGES = LENGTH_RULE.max_pages
#: The page budget is "fits ``MAX_PAGES`` at this spacing or looser" (the renderer's own floor is 0.7).
FIT_SCALE = 0.9

#: Bullets per experience role in the first pick, by recency rank (0 = newest); an old role gets
#: ``LENGTH_RULE.old_role_bullets``. The fit cuts from here; the fill adds up to ``HARD_CAPS``.
PICK_CAPS = (9, 6, 6, 5)
HARD_CAPS = (11, 8, 8, 6)
#: A recent role is always present with at least this many bullets.
FLOORS = (3, 2, 2, 2)
MAX_PROJECTS = 3
PROJECT_BULLETS = 3
MAX_OTHER = 4
MAX_SKILLS = 28
#: How many unshown bullets the fill tries.
FILL_TRIES = 12
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
_LEXICAL_REASON = 0.25

_WORD = re.compile(r"[a-z][a-z0-9+#.-]*[a-z0-9+#]|[a-z]")
_TITLE_WORD = re.compile(r"[a-z0-9+#]+")
_STOP = frozenset(
    """a an and or of to in for with on at by from as is are be we you our your this that it its will can
    must should have has had not no all any more most other some such than then their they them what when
    where which who how about also into over under per plus experience experienced years year strong
    ability work working team teams role using used use new across within build building built design
    designed lead led own owned including least solid record several tight every one two three""".split()
)
#: Rank and filler words of a job title: they say nothing about what kind of engineer it names.
_GENERIC_TITLE = frozenset({"staff", "senior", "principal", "lead", "engineer", "software", "and", "the", "of", "with"})
_PRIOR_TITLE_STOP = frozenset({"staff", "principal", "senior", "engineer"})
_OWNERSHIP = tuple(re.compile(pattern) for _name, pattern in OWNERSHIP_FAMILIES)
_POSTING_BULLET = re.compile(r"\A[-*•]\s+")
_NICE_HEADING = re.compile(r"\b(?:nice|bonus|preferred|plus|desirable|ideally|good to have)\b", re.IGNORECASE)
_HEADING_MAX_CHARS = 60


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

    def to_json(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "label": self.label, "titles": list(self.titles), "focus_tags": list(self.focus_tags)}


@dataclass(frozen=True)
class SelectionPosting:
    """One posting as the selector reads it: public text, written by strangers; only ever matched against."""

    title: str
    text: str
    company: str = ""
    location: str = ""


# --- text features ----------------------------------------------------------------------------


def _stem(word: str) -> str:
    for suffix in ("ations", "ation", "ing", "ers", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _words(text: str) -> frozenset[str]:
    return frozenset(_stem(word) for word in _WORD.findall(text.casefold()) if len(word) > 2 and word not in _STOP)


def _title_tokens(text: str) -> set[str]:
    """What a job title is about: its words (two-letter ones such as AI and ML included), minus rank words."""

    return {_stem(token) for token in _TITLE_WORD.findall(text.casefold()) if len(token) >= 2 and token not in _GENERIC_TITLE}


def _tag_tokens(tags: Iterable[str]) -> set[str]:
    return {_stem(tag) for tag in tags} | {part for tag in tags for part in tag.split("-")}


def _idf(master: Master, item_words: dict[str, frozenset[str]]) -> dict[str, float]:
    docs = [item_words[item.id] for item in master.items.values() if item.kind == KIND_BULLET]
    count: dict[str, int] = {}
    for doc in docs:
        for word in doc:
            count[word] = count.get(word, 0) + 1
    return {word: math.log(1 + len(docs) / n) for word, n in count.items()}


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
class _JobTerms:
    keywords: PostingKeywords
    #: stemmed word -> weight (requirement lines 1.0, nice-to-have lines 0.5)
    lexical: dict[str, float]


def _job_terms(master: Master, posting: SelectionPosting) -> _JobTerms:
    keywords = extract_keywords(posting.text, title=posting.title, skills=master.skills())
    lines = [line.strip() for line in posting.text.splitlines() if line.strip()]
    # A posting written as lists: only its list lines say what the job asks for (the prose around them is
    # about the company). A posting with no list at all is read whole.
    bulleted = any(_POSTING_BULLET.match(line) for line in lines)
    lexical: dict[str, float] = {}
    weight = 1.0
    for line in lines:
        is_bullet = _POSTING_BULLET.match(line) is not None
        if not is_bullet and len(line) <= _HEADING_MAX_CHARS and (bulleted or line.endswith(":")):
            weight = 0.5 if _NICE_HEADING.search(line) else 1.0
            continue
        if not is_bullet and bulleted:
            continue
        for word in _words(line):
            lexical[word] = max(lexical.get(word, 0.0), weight)
    for word in _words(posting.title):
        lexical[word] = 1.0
    return _JobTerms(keywords, lexical)


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


def _render(master: Master, pick: _Pick, *, ids: bool) -> str:
    """Resume markdown in GigAI's format for a pick; ``ids`` keeps each line's master id in a trailing comment."""

    def mark(item_id: str) -> str:
        return f" <!-- id:{item_id} -->" if ids else ""

    def entries(section: str) -> list[str]:
        out: list[str] = []
        for entry in sorted(master.entries_in(section), key=_entry_sort_key):
            if entry.id in pick.entries:
                out += [f"### {entry.heading}{mark(entry.id)}", *entry.sublines, ""]
                out += [f"- {master.items[i].text}{mark(i)}" for i in pick.entries[entry.id]] + [""]
        return out

    out: list[str] = []
    if pick.summary:
        out += ["## Summary", ""] + [f"- {master.items[i].text}{mark(i)}" for i in pick.summary] + [""]
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
        out += ["## Other", ""] + [f"- {master.items[i].text}{mark(i)}" for i in pick.other] + [""]
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
class LengthCut:
    """One cut the fit made, in the order it was made. ``kind`` is ``bullet``, ``role`` or ``other``."""

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
    #: A line kept because it is the only shown line naming a must-have: id -> the must-haves.
    only_evidence: dict[str, tuple[str, ...]]
    #: A recent role's line shown in place of an old role's only-evidence line: id -> (the old line's id, the must-haves).
    shown_instead: dict[str, tuple[str, tuple[str, ...]]]
    roles_dropped: tuple[str, ...]
    keywords: PostingKeywords | None
    #: With a posting: which of its keywords the markdown names (``must_covered``, ``must_missing``, ...).
    coverage: dict[str, tuple[str, ...]]

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
                "cut_for_length": len(self.cut_for_length),
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
            "keywords": None,
        }
        if self.keywords is not None:
            out["keywords"] = {**self.keywords.to_json(), **{key: list(value) for key, value in self.coverage.items()}}
        return out


# --- the selection ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cut:
    kind: str  # bullet | role | other
    id: str
    #: Lines of recent roles shown in this line's place (it was the only evidence of a must-have).
    add: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()


def _names(terms: Iterable[str]) -> str:
    return ", ".join(terms)


def select(
    master: Master,
    profile: SelectionProfile,
    posting: SelectionPosting | None = None,
    *,
    today: date | None = None,
    measure: Measure | None = None,
    max_pages: int = MAX_PAGES,
    fill: bool = True,
) -> Selected:
    """One selection: ``posting`` ``None`` gives the profile's standing pick, else the pick for that job.

    ``measure`` lays out resume markdown and answers ``(last page, fill)``; the default is the shipped
    template at ``FIT_SCALE``. ``today`` decides which roles are old (default: today's date).
    """

    today = today or date.today()
    terms = _job_terms(master, posting) if posting is not None else None
    scores = _score_items(master, terms, profile)
    subject = "this posting" if posting is not None else "this profile"
    roles = sorted(master.entries_in("experience"), key=_entry_sort_key)
    old = {entry.id for entry in roles if is_old_role(entry, today)}
    recency = {entry.id: (_OLD_RECENCY if entry.id in old else _rank(index, _RECENCY)) for index, entry in enumerate(roles)}
    covered: dict[str, int] = {}
    pick = _Pick()
    why: dict[str, tuple[str, str]] = {}  # a shown line's (code, reason)
    out: dict[str, tuple[str, str]] = {}  # a left-out line's (code, reason)

    def reason_for(item: MasterItem) -> tuple[str, str]:
        hits = scores.hits.get(item.id, {})
        if hits:
            return "names_keywords", "names " + _names(sorted(hits, key=lambda term: (-hits[term], term)))
        if scores.lexical.get(item.id, 0.0) > _LEXICAL_REASON:
            return "posting_wording", "matches the posting's wording"
        if scores.prior.get(item.id, 0.0) >= 0.5:
            return "profile_focus", "fits this profile"
        return "strongest_remaining", "strongest remaining line of its role"

    def take(item: MasterItem) -> None:
        for term in scores.hits.get(item.id, {}):
            covered[term] = covered.get(term, 0) + 1
        why[item.id] = reason_for(item)

    def worth(item_id: str) -> float:
        item = master.items[item_id]
        return _value(item, scores, {}, recency.get(item.entry_id or "", _PROJECT_RECENCY))

    # 1. Summary: one variant.
    summary = _summary_choice(master, scores, posting)
    if summary is not None:
        pick.summary = [summary.id]
        take(summary)
        for item in master.in_section("summary"):
            if item.id != summary.id:
                out[item.id] = ("summary_other_variant", f"another summary fits {subject} better")

    # 2. Experience: greedy by marginal value under per-role caps.
    caps = {entry.id: (LENGTH_RULE.old_role_bullets if entry.id in old else _rank(index, PICK_CAPS)) for index, entry in enumerate(roles)}
    picked: dict[str, list[str]] = {entry.id: [] for entry in roles}
    pool = [master.items[bullet] for entry in roles for bullet in entry.bullets]
    while pool:
        open_pool = [item for item in pool if len(picked[item.entry_id or ""]) < caps[item.entry_id or ""]]
        if not open_pool:
            break
        best = max(open_pool, key=lambda item: (_value(item, scores, covered, recency[item.entry_id or ""]), -item.order))
        picked[best.entry_id or ""].append(best.id)
        take(best)
        pool.remove(best)
    for entry in roles:
        pick.entries[entry.id] = picked[entry.id]
    for item in pool:
        limit = caps[item.entry_id or ""]
        out[item.id] = ("role_limit", f"over the limit of {limit} lines for this role; the lines shown score higher for {subject}")

    # 3. Projects: the best few, a few bullets each.
    projects = master.entries_in("projects")
    project_best = {
        entry.id: sorted(((_value(master.items[b], scores, covered, _PROJECT_RECENCY), b) for b in entry.bullets), reverse=True)[:PROJECT_BULLETS]
        for entry in projects
    }
    shown_projects = sorted(projects, key=lambda entry: -sum(value for value, _bullet in project_best[entry.id]))[:MAX_PROJECTS]
    for entry in projects:
        chosen = [bullet for _value_, bullet in project_best[entry.id]] if entry in shown_projects else []
        if entry in shown_projects:
            pick.entries[entry.id] = sorted(chosen, key=lambda bullet: master.items[bullet].order)
            for bullet in chosen:
                take(master.items[bullet])
        for bullet in entry.bullets:
            if bullet not in chosen:
                out[bullet] = (
                    ("project_limit", f"over the limit of {PROJECT_BULLETS} lines per project") if entry in shown_projects
                    else ("project_not_shown", f"its project is not among the {MAX_PROJECTS} most relevant to {subject}")
                )

    # 4. Education: every degree; a line under it only when it names a posting keyword.
    for entry in master.entries_in("education"):
        pick.entries[entry.id] = [bullet for bullet in entry.bullets if scores.hits.get(bullet)]
        for bullet in entry.bullets:
            if bullet in pick.entries[entry.id]:
                take(master.items[bullet])
            else:
                out[bullet] = ("education_detail", "a detail under a degree is shown only when it names something the posting asks for")

    # 5. Other: the best few.
    others = sorted(master.in_section("other"), key=lambda item: -_value(item, scores, covered, _OTHER_RECENCY))
    pick.other = [item.id for item in sorted(others[:MAX_OTHER], key=lambda item: item.order)]
    for item in others[:MAX_OTHER]:
        take(item)
    for item in others[MAX_OTHER:]:
        out[item.id] = ("other_limit", f"over the limit of {MAX_OTHER} Other lines; the lines shown score higher for {subject}")

    # 6. Skills: the posting's must-haves the master lists, then its nice-to-haves, then what the shown bullets name.
    all_skills = master.skills()
    asked: list[tuple[str, str]] = []  # (skill, must | nice), in the posting's order
    if terms is not None:
        for kind, names in (("must", terms.keywords.must), ("nice", terms.keywords.nice)):
            for term in names:
                for skill in all_skills:
                    if mentions(skill, term) and skill not in (name for name, _kind in asked):
                        asked.append((skill, kind))

    def skills_for(trial: _Pick) -> list[tuple[str, str]]:
        shown_text = " ".join(master.items[i].text for i in trial.bullet_ids())
        ordered = list(asked)
        ordered += [(skill, "line") for skill in all_skills if skill not in (name for name, _kind in asked) and mentions(shown_text, skill)]
        return ordered[:MAX_SKILLS]

    def view(trial: _Pick) -> _Pick:
        """``trial`` as it prints: a role's bullets best first, and the skills its own lines give."""

        shown = trial.copy()
        for entry_id, bullets in shown.entries.items():
            if master.entries[entry_id].section == "experience":
                shown.entries[entry_id] = sorted(bullets, key=lambda bullet: (-worth(bullet), master.items[bullet].order))
        shown.skills = [name for name, _kind in skills_for(trial)]
        return shown

    measured: dict[str, tuple[int, float]] = {}
    lay_out = measure or _shipped_measure

    def end_of(trial: _Pick) -> tuple[int, float]:
        markdown = _render(master, view(trial), ids=False)
        if markdown not in measured:
            measured[markdown] = lay_out(markdown)
        return measured[markdown]

    # 7. FIT: the cuts in the one order they may be applied (the docstring's rules 3 and 4).
    must = set(terms.keywords.must) if terms is not None else set()

    def must_named(item_id: str) -> list[str]:
        return [term for term in scores.hits.get(item_id, {}) if term in must]

    evidence: dict[str, int] = {}
    for bullet in pick.bullet_ids():
        for term in must_named(bullet):
            evidence[term] = evidence.get(term, 0) + 1
    in_pick = set(pick.bullet_ids())
    protected: dict[str, tuple[int, tuple[str, ...]]] = {}  # a line the cut order skipped: (where, the must-haves)
    cuts: list[_Cut] = []

    def last_evidence(bullet: str) -> tuple[str, ...]:
        return tuple(term for term in must_named(bullet) if evidence.get(term, 0) <= 1)

    def release(bullet: str) -> None:
        for term in must_named(bullet):
            evidence[term] -= 1

    def stand_ins(needed: tuple[str, ...]) -> tuple[str, ...] | None:
        """Unshown lines of recent roles that between them name every term of ``needed``; ``None`` when the master has none."""

        left, chosen = set(needed), []
        candidates = [master.items[b] for entry in roles if entry.id not in old for b in entry.bullets if b not in in_pick]
        while left:
            naming = [item for item in candidates if item.id not in chosen and left & set(must_named(item.id))]
            if not naming:
                return None
            best = max(naming, key=lambda item: (len(left & set(must_named(item.id))), worth(item.id), -item.order))
            chosen.append(best.id)
            left -= set(must_named(best.id))
        return tuple(chosen)

    for entry in (entry for entry in reversed(roles) if entry.id in old):  # oldest first
        kept = 0
        for bullet in sorted(pick.entries[entry.id], key=lambda b: (worth(b), -master.items[b].order)):
            only = last_evidence(bullet)
            if not only:
                release(bullet)
                cuts.append(_Cut("bullet", bullet))
                continue
            instead = stand_ins(only)
            if instead is None:
                protected[bullet] = (len(cuts), only)
                kept += 1
                continue
            release(bullet)
            for added in instead:
                in_pick.add(added)
                for term in must_named(added):
                    evidence[term] = evidence.get(term, 0) + 1
            cuts.append(_Cut("bullet", bullet, add=instead, terms=only))
        if not kept:
            cuts.append(_Cut("role", entry.id))
    floors = {entry.id: (0 if entry.id in old else _rank(index, FLOORS)) for index, entry in enumerate(roles)}
    rest = [b for entry in roles if entry.id not in old for b in pick.entries[entry.id]]
    rest += [b for entry in projects if entry.id in pick.entries for b in pick.entries[entry.id]]
    remaining = {entry_id: len(bullets) for entry_id, bullets in pick.entries.items()}
    for bullet in sorted(rest, key=lambda b: (worth(b), -master.items[b].order)):
        entry_id = master.items[bullet].entry_id or ""
        if remaining[entry_id] <= floors.get(entry_id, 1):
            continue
        only = last_evidence(bullet)
        if only:
            protected[bullet] = (len(cuts), only)
            continue
        release(bullet)
        cuts.append(_Cut("bullet", bullet))
        remaining[entry_id] -= 1
    cuts += [_Cut("other", item_id) for item_id in sorted(pick.other, key=lambda i: (_value(master.items[i], scores, {}, _OTHER_RECENCY), -master.items[i].order))]

    def apply(count: int) -> _Pick:
        trial = pick.copy()
        for cut in cuts[:count]:
            if cut.kind == "bullet":
                entry_id = master.items[cut.id].entry_id or ""
                if entry_id in trial.entries:
                    trial.entries[entry_id] = [b for b in trial.entries[entry_id] if b != cut.id]
                    if master.entries[entry_id].section == "projects" and not trial.entries[entry_id]:
                        del trial.entries[entry_id]
                for added in cut.add:
                    trial.entries[master.items[added].entry_id or ""].append(added)
            elif cut.kind == "role":
                trial.entries.pop(cut.id, None)
            else:
                trial.other = [i for i in trial.other if i != cut.id]
        return trial

    before = end_of(pick)
    applied = 0
    if before[0] > max_pages:
        low, high = 0, len(cuts)  # the fewest cuts that fit; every cut applied is the most the rules allow
        while low < high:
            mid = (low + high) // 2
            if end_of(apply(mid))[0] <= max_pages:
                high = mid
            else:
                low = mid + 1
        applied = low
    fitted = apply(applied)

    # 8. FILL: room left on the last page goes to the best unshown bullets of the recent roles.
    added_to_fill: list[str] = []
    gone = {cut.id for cut in cuts[:applied]}
    if fill and end_of(fitted)[0] <= max_pages:
        shown = set(fitted.bullet_ids())
        hard = {entry.id: _rank(index, HARD_CAPS) for index, entry in enumerate(roles) if entry.id not in old}
        extras = sorted(
            (master.items[b] for entry in roles if entry.id in hard and entry.id in fitted.entries for b in entry.bullets if b not in shown and b not in gone),
            key=lambda item: (-_value(item, scores, covered, recency[item.entry_id or ""]), item.order),
        )
        for item in extras[:FILL_TRIES]:
            entry_id = item.entry_id or ""
            if len(fitted.entries[entry_id]) >= hard[entry_id]:
                continue
            trial = fitted.copy()
            trial.entries[entry_id].append(item.id)
            if end_of(trial)[0] > max_pages:
                break
            fitted = trial
            added_to_fill.append(item.id)

    final = view(fitted)
    pages, last_fill = end_of(fitted)

    # The reasons: every line of the master, shown or not.
    shown_ids = set(final.summary) | set(final.bullet_ids()) | set(final.other)
    dropped = [entry for entry in roles if entry.id not in final.entries]
    length_cuts: list[LengthCut] = []
    shown_instead: dict[str, tuple[str, tuple[str, ...]]] = {}
    for cut in cuts[:applied]:
        if cut.kind == "role":
            code, reason = "cut_oldest_role_dropped", "cut for length: oldest role dropped"
        elif cut.kind == "other":
            code, reason = "cut_lowest_value", f"cut for length: lowest value for {subject}"
        else:
            entry_id = master.items[cut.id].entry_id or ""
            if entry_id in old and entry_id not in final.entries:
                code, reason = "cut_oldest_role_dropped", "cut for length: oldest role dropped"
            elif entry_id in old:
                code, reason = "cut_oldest_role_shortened", "cut for length: oldest role shortened"
            else:
                code, reason = "cut_lowest_value", f"cut for length: lowest value for {subject}"
            if cut.add:
                code, reason = "cut_shown_by_recent_role", f"{reason}; a recent role's line now shows {_names(cut.terms)}"
                for added in cut.add:
                    named = tuple(term for term in cut.terms if term in must_named(added))
                    shown_instead[added] = (cut.id, named)
                    why[added] = ("shown_instead", f"names {_names(named)}; shown in place of an older role's line")
        length_cuts.append(LengthCut(cut.id, cut.kind, code, reason))
        if cut.kind != "role":
            out[cut.id] = (code, reason)
    for entry in dropped:
        for bullet in entry.bullets:
            if out.get(bullet, ("role_limit",))[0] == "role_limit":
                out[bullet] = ("role_dropped", "its role was cut for length: the oldest roles go first")
    for item_id in added_to_fill:
        code, reason = reason_for(master.items[item_id])
        why[item_id] = ("room_left", f"{reason} (room left on the page)")
    only_evidence = {
        item_id: named for item_id, (position, named) in protected.items() if position < applied and item_id in shown_ids
    }
    for item_id, named in only_evidence.items():
        why[item_id] = ("only_evidence", f"kept: the only line shown that names {_names(named)}")

    lines = tuple(
        LineReason(item.id, item.id in shown_ids, *(why[item.id] if item.id in shown_ids else out.get(item.id, ("not_picked", f"scores lower for {subject} than the lines shown"))))
        for item in master.items.values() if item.kind in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER)
    )
    final_skills = skills_for(fitted)
    shown_skills = {name for name, _kind in final_skills}
    skill_text = {
        "must": ("posting_must", "the posting asks for it"),
        "nice": ("posting_nice", "the posting lists it as a plus"),
        "line": ("named_by_line", "a shown line names it"),
    }
    asked_names = {name for name, _kind in asked}
    final_text = " ".join(master.items[i].text for i in fitted.bullet_ids())
    skill_reasons = [SkillReason(name, True, *skill_text[kind]) for name, kind in final_skills]
    for skill in all_skills:
        if skill in shown_skills:
            continue
        if skill in asked_names or mentions(final_text, skill):
            skill_reasons.append(SkillReason(skill, False, "skills_limit", f"over the limit of {MAX_SKILLS} skills"))
        else:
            skill_reasons.append(SkillReason(skill, False, "not_asked", f"not asked for by {subject} and not named by a shown line"))

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
        only_evidence=only_evidence,
        shown_instead=shown_instead,
        roles_dropped=tuple(entry.id for entry in dropped),
        keywords=terms.keywords if terms is not None else None,
        coverage=coverage,
    )


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
) -> EvidenceView:
    """What an assessment of ``posting`` would read of the master, within ``cap`` characters.

    Every role and degree heading, one summary (chosen as ``select`` chooses it), every skill and every
    Other line, then bullets by their value for the posting until the next one would pass ``cap``. No page
    fit: this view is for a model to read, not to print. Bullets print in the master's order.
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
        if len(_render(master, trial, ids=False)) > cap:
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
        markdown=_render(master, pick, ids=False),
        cap=cap,
        bullets=len(pick.bullet_ids()),
        bullets_total=len(bullets),
    )


__all__ = [
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
    "MAX_SKILLS",
    "Measure",
    "PICK_CAPS",
    "PROJECT_BULLETS",
    "SELECTOR_VERSION",
    "Selected",
    "SelectionPosting",
    "SelectionProfile",
    "SkillReason",
    "UNMEASURED_PAGES",
    "evidence_view",
    "is_old_role",
    "select",
]
