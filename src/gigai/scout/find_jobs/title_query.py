"""ONE title matcher for search: the role rule, the profile's tag query (0110-024b, P2) and its titles to avoid.

``index_search.read_indexed_boards`` and ``market_acquisition._role_match``
both decide "does this posting's title fit the profile's roles" here, so the
two can never disagree.

A posting matches when EITHER

* its title passes the committed role rule (``ats_board_clients.matches_roles``:
  the role's whole words, standing together as that role). It always does:
  tags only ADD matches, recall never drops below the rule's; or
* the tag store holds a tag for its normalized title whose level equals the
  level of one of the profile's roles and whose function equals that role's
  function. Each role is tagged by ``posting_tags.tag_title`` on the role text
  itself ("Director of Engineering" -> director + software). Level is the
  SAME level only: "VP Engineering" and "Head of Engineering" stay out of a
  "Director of Engineering" role, while "Dir. of Engineering" and "Director,
  Software Development" come in.

A role whose tag has no function (the rules found no family) takes no part in
the tag query and keeps the plain rule only. So does (0.1.11.5, TITLE-01)

* a role of a WIDE family (``WIDE_FUNCTIONS``: every family but ``software``).
  A (level, function) pair says "the same job" only where the family is one
  job. "Forward Deployed Engineer" is mid + solutions, and so are "Solutions
  Architect", "Implementation Consultant" and "Field Architect": the pair
  listed them all for that role;
* a role with a QUALIFIER (``role_qualifier``: "Staff Software Engineer, Agent
  Infrastructure", "Software Engineer (Backend)"). The tag knows nothing of the
  qualifier, so the pair staff + software listed every "Staff Software
  Engineer" there is. Rule only, the qualifier's words must be in the title.

Both still say which FUNCTIONS the profile wants (the generic-title veto
below reads the tag of every role). A title the rule REJECTED (the
role's words are all there, as another role: "Staff Training Engineer" for
"Staff Engineer", ``role_fit`` is ``False``) is never matched by a tag: the
rules tag it staff + software like the role itself, and the tag must not bring
it back. A posting whose function tag is
missing (not in the store, or ``function`` is ``None``) is judged by the rule
alone: the untagged fallback. With no tag store, or an unreadable one, the
matcher is exactly the rule and never raises.

0110-8-05, the ONE exception to "the rule always wins": a GENERIC title. A role
is generic when, level and seniority words aside, it holds only a bare role
noun (``is_generic_role``: "Staff Engineer", "Engineering Manager", "Director of
Engineering"): its rule needs no word that says which kind of engineer, so it
passes "Staff Security Engineer" and "Staff Technical Program Manager,
Engineering Onboarding". When the profile ALSO names a function-specific title
("Staff Software Engineer", "Staff AI Engineer"), those say which functions it
wants, and a posting matched ONLY by a generic title is then decided by its
stored function tag:

* a known function that is one of the profile's functions: matched (by rule);
* a known function that is not: NOT matched (``vetoed_by_tag``), whatever the
  words say;
* no known function yet (not in the store, or no family found): matched by the
  generic rule as before, and flagged ``tag_pending`` so the grid can say the
  match is by title words alone until the tag arrives.

A title a function-specific role matches is never vetoed ("Staff Software
Engineer, Product Experiences" stays, though the rules tag it ``product``). A
profile with ONLY generic titles names no function of its own, so nothing is
vetoed and it matches exactly as before: every title with its words
(``generic_title_warnings`` is what tells the user how wide that is).

0.1.11.3 (packet 14), TITLES TO AVOID: a matcher built with the profile's
``titles_to_avoid`` matches no title that holds one of them
(``title_avoided``), whatever the rule or a tag says. An entry is a word or a
phrase: the title is read as its lower-cased words (letters, digits, ``+``
and ``#``; punctuation and spacing only separate words), and an entry matches
when its own words are words of the title, in a row and in order. Whole words
only and no stemming: "Trainer" does not match "Training" or "Trainers",
"intern" does not match "Internal". The WHOLE title is read, the part after a
comma too: avoiding "training" also takes out "Staff Software Engineer, ML
Training Infrastructure". An entry with no letter or digit avoids nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from .ats_board_clients import _LEVEL_WORDS, _SENIORITY_WORDS, _WORD_RE, _words, matches_roles, role_fit
from .posting_tags import _LEVEL_RES, FUNCTIONS, default_store, normalize_title, tag_title
from .tag_store import TagStore

#: The bare role nouns (as the rule stems them: "engineering" is "engineer"): a role that is only one of these plus level
#: words says no function of its own.
GENERIC_ROLE_WORDS = frozenset({"engineer", "developer", "programmer"})


#: The ONE family whose (level, function) pair is one job, so a role of it takes part in the tag query. The tag table
#: (``posting_tags._FUNCTION_RULES``) tries every look-alike family first, and ``software`` is what is left: an
#: engineering noun with no other family's word ("Dir. of Engineering", "Director, Software Development").
TAG_QUERY_FUNCTIONS = frozenset({"software"})
#: The WIDE families (0.1.11.5, TITLE-01): a role of one takes NO part in the tag query, it keeps the rule only. Each is
#: either several jobs under one name (``solutions``: sales / solutions / customer / field / implementation / forward
#: deployed x engineer / architect / consultant; ``customer``, ``hardware``, ``operations``, ``sales``, ``marketing``,
#: ``people``, ``finance``, ``legal``, ``healthcare``, ``research``, ``design``) or keyed on a TOPIC word that any job
#: may carry (``ai_ml``: "ai" or "ml" anywhere, so an AI security lead, designer or account executive; ``data``,
#: ``security_it``, ``product``).
WIDE_FUNCTIONS = frozenset(FUNCTIONS) - TAG_QUERY_FUNCTIONS

#: Where a role's qualifier starts: , ; : | @, a bracket, a dash or slash of its own (" - ", " / "), an en or em dash.
_QUALIFIER_RE = re.compile(r"[,;:|()\[\]{}@]|\s[-/]+\s|[\u2013\u2014]")


def _is_level_word(word: str) -> bool:
    return any(pattern.fullmatch(word) for _name, pattern in _LEVEL_RES)


def role_qualifier(role: str) -> tuple[str, ...]:
    """The words of ``role``'s QUALIFIER (as the rule stems them), ``()`` when it has none.

    The qualifier is what follows the first comma, colon, bracket, pipe or dash of its own: ", Agent Infrastructure",
    " - Payments", "(Backend)". Level words there are no qualifier ("Software Engineer, Staff"), and a separator with
    nothing in front of it starts none ("(Senior) Software Engineer").
    """

    if type(role) is not str:
        return ()
    head, *rest = _QUALIFIER_RE.split(role, maxsplit=1)
    if not rest or not _words(head):
        return ()
    return tuple(
        word for word in _words(rest[0]) if word not in _SENIORITY_WORDS and word not in _LEVEL_WORDS and not _is_level_word(word)
    )


def is_generic_role(role: str) -> bool:
    """Whether ``role`` is a generic title: only level/seniority words and a bare role noun ("Staff Engineer")."""

    if type(role) is not str or tag_title(role).function is None:
        return False
    rest = [word for word in _words(role) if word not in _SENIORITY_WORDS and not _is_level_word(word)]
    return bool(rest) and all(word in GENERIC_ROLE_WORDS for word in rest)


def avoid_phrases(titles_to_avoid: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Each entry of a profile's titles to avoid as its lower-cased words; an entry with none is left out."""

    phrases = []
    for entry in titles_to_avoid:
        words = tuple(_WORD_RE.findall(entry.lower())) if type(entry) is str else ()
        if words and words not in phrases:
            phrases.append(words)
    return tuple(phrases)


def _holds_avoided(title: str, phrases: Sequence[tuple[str, ...]]) -> bool:
    words = _WORD_RE.findall(title.lower())
    for phrase in phrases:
        size = len(phrase)
        if size == 1:
            if phrase[0] in words:
                return True
            continue
        first = phrase[0]
        for at in range(len(words) - size + 1):
            if words[at] == first and tuple(words[at:at + size]) == phrase:
                return True
    return False


def title_avoided(title: str, titles_to_avoid: Sequence[str]) -> bool:
    """Whether ``title`` holds one of ``titles_to_avoid`` as whole words in a row (see the module text)."""

    return type(title) is str and _holds_avoided(title, avoid_phrases(titles_to_avoid))


@dataclass(frozen=True, slots=True)
class TagQuery:
    """The ``(level, function)`` pairs the profile's roles ask for."""

    pairs: frozenset[tuple[str, str]]

    def __bool__(self) -> bool:
        return bool(self.pairs)


def tag_query_for_roles(roles: Sequence[str]) -> TagQuery:
    """Tag each role phrase; a role with no function, of a wide family or with a qualifier contributes nothing."""

    pairs: set[tuple[str, str]] = set()
    for role in roles:
        if type(role) is not str or not role.strip():
            continue
        tag = tag_title(role)
        if tag.function in TAG_QUERY_FUNCTIONS and not role_qualifier(role):
            pairs.add((tag.level, tag.function))
    return TagQuery(frozenset(pairs))


def function_levels_for_roles(roles: Sequence[str]) -> tuple[str, ...]:
    """The levels of the roles that name a function: where a posting's function tag can decide a match.

    Wider than the tag query on purpose: a wide or qualified role adds no tag match, but its function still decides
    the generic-title veto, so a title of its level with no function yet is still worth a model's tag.
    """

    tags = (tag_title(role) for role in roles if type(role) is str and role.strip())
    return tuple(sorted({tag.level for tag in tags if tag.function is not None}))


@dataclass(slots=True)
class TitleMatchCounts:
    """What one pass decided: by the rule, by a tag only, or by the rule alone because untagged."""

    matched_by_rule: int = 0
    matched_by_tag: int = 0
    untagged_fallback: int = 0
    #: 0110-8-05: a generic title's rule matched, the posting's known function is not one of the profile's: not matched.
    vetoed_by_tag: int = 0
    #: 0110-8-05: matched by a generic title's rule alone because the posting has no known function tag yet.
    tag_pending: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "matched_by_rule": self.matched_by_rule,
            "matched_by_tag": self.matched_by_tag,
            "untagged_fallback": self.untagged_fallback,
            "vetoed_by_tag": self.vetoed_by_tag,
            "tag_pending": self.tag_pending,
        }


@dataclass(frozen=True, slots=True)
class TitleDecision:
    """What the matcher decided for one title, and how (``by``: ``rule`` / ``tag`` / ``vetoed`` / ``avoided`` / ``None``)."""

    matched: bool
    by: str | None = None
    #: Matched by a generic title's rule alone, the posting's function tag is not known yet.
    tag_pending: bool = False


_NO_MATCH = TitleDecision(False)
_BY_RULE = TitleDecision(True, "rule")
_BY_TAG = TitleDecision(True, "tag")
_VETOED = TitleDecision(False, "vetoed")
_AVOIDED = TitleDecision(False, "avoided")
_PENDING = TitleDecision(True, "rule", tag_pending=True)


def open_tag_store(home_root: Path | str | None) -> TagStore | None:
    """The product tag store if its file exists; ``None`` otherwise. Never creates one, never raises."""

    if home_root is None:
        return None
    try:
        store = default_store(Path(home_root))
        return store if store.path.is_file() else None
    except (OSError, sqlite3.Error):
        return None


class TitleMatcher:
    """The shared matcher. Build once per pass, call ``matches(title)`` per posting.

    ``counts`` accumulates across calls. ``untagged_fallback`` counts the
    postings the rule rejected and that carry no function tag, so the rule
    was the only judge. ``decide`` is ``matches`` with the reason (a generic
    title's veto and the tag-pending flag, 0110-8-05: see the module text).
    ``avoid`` is the profile's titles to avoid: a title holding one is never
    matched (and counted nowhere).
    """

    def __init__(self, roles: Sequence[str], store: TagStore | None = None, avoid: Sequence[str] = ()) -> None:
        self.roles = tuple(str(role) for role in roles)
        self._avoid = avoid_phrases(avoid)
        self.query = tag_query_for_roles(self.roles) if store is not None else TagQuery(frozenset())
        #: The functions the profile's roles name: of EVERY role, also one that takes no part in the tag query.
        self._functions = frozenset(
            function for function in (tag_title(role).function for role in self.roles if role.strip()) if function is not None
        )
        self._store = store if self._functions else None
        self.counts = TitleMatchCounts()
        generic = tuple(role for role in self.roles if is_generic_role(role))
        names_a_function = any(role not in generic and tag_title(role).function is not None for role in self.roles)
        #: The generic titles a known function tag can veto: only with a store, and only next to a function-specific title.
        self.generic_roles = generic if self._store is not None and names_a_function else ()
        self._specific_roles = tuple(role for role in self.roles if role not in self.generic_roles)

    def _rule_only(self) -> None:
        """The store cannot be read: from here on exactly the rule (no tag match, no veto)."""

        self._store = None
        self.generic_roles, self._specific_roles = (), self.roles

    def decide(self, title: str) -> TitleDecision:
        if self._avoid and type(title) is str and _holds_avoided(title, self._avoid):
            return _AVOIDED
        if matches_roles(title, self._specific_roles):
            self.counts.matched_by_rule += 1
            return _BY_RULE
        if self._store is None or type(title) is not str:
            return _NO_MATCH
        generic = bool(self.generic_roles) and matches_roles(title, self.generic_roles)
        try:
            tag = self._store.get(normalize_title(title))
        except (OSError, sqlite3.Error):
            self._rule_only()
            if generic:
                self.counts.matched_by_rule += 1
                return _BY_RULE
            return _NO_MATCH
        if tag is None or tag.function is None:
            if generic:
                self.counts.matched_by_rule += 1
                self.counts.tag_pending += 1
                return _PENDING
            self.counts.untagged_fallback += 1
            return _NO_MATCH
        if generic:
            if tag.function in self._functions:
                self.counts.matched_by_rule += 1
                return _BY_RULE
            self.counts.vetoed_by_tag += 1
            return _VETOED
        if (tag.level, tag.function) in self.query.pairs and role_fit(title, self.roles) is None:
            self.counts.matched_by_tag += 1
            return _BY_TAG
        return _NO_MATCH

    def matches(self, title: str) -> bool:
        return self.decide(title).matched


def title_matches(title: str, roles: Sequence[str], store: TagStore | None = None, avoid: Sequence[str] = ()) -> bool:
    """One-shot form of ``TitleMatcher`` (what ``_role_match`` calls)."""

    return TitleMatcher(roles, store, avoid).matches(title)


__all__ = [
    "GENERIC_ROLE_WORDS",
    "TAG_QUERY_FUNCTIONS",
    "TagQuery",
    "TitleDecision",
    "TitleMatchCounts",
    "TitleMatcher",
    "WIDE_FUNCTIONS",
    "avoid_phrases",
    "function_levels_for_roles",
    "is_generic_role",
    "open_tag_store",
    "role_qualifier",
    "tag_query_for_roles",
    "title_avoided",
    "title_matches",
]
