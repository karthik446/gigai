"""ONE title matcher for search: the whole-word rule plus the profile's tag query (0110-024b, P2).

``index_search.read_indexed_boards`` and ``market_acquisition._role_match``
both decide "does this posting's title fit the profile's roles" here, so the
two can never disagree.

A posting matches when EITHER

* its title passes the committed whole-word rule (``ats_board_clients.matches_roles``;
  the 021 rule). It always does: tags only ADD matches, recall never drops
  below the rule's; or
* the tag store holds a tag for its normalized title whose level equals the
  level of one of the profile's roles and whose function equals that role's
  function. Each role is tagged by ``posting_tags.tag_title`` on the role text
  itself ("Director of Engineering" -> director + software). Level is the
  SAME level only: "VP Engineering" and "Head of Engineering" stay out of a
  "Director of Engineering" role, while "Dir. of Engineering" and "Director,
  Software Development" come in.

A role whose tag has no function (the rules found no family) takes no part in
the tag query and keeps the plain rule only. A posting whose function tag is
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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .ats_board_clients import _SENIORITY_WORDS, _words, matches_roles
from .posting_tags import _LEVEL_RES, default_store, normalize_title, tag_title
from .tag_store import TagStore

#: The bare role nouns (as the rule stems them: "engineering" is "engineer"): a role that is only one of these plus level
#: words says no function of its own.
GENERIC_ROLE_WORDS = frozenset({"engineer", "developer", "programmer"})


def _is_level_word(word: str) -> bool:
    return any(pattern.fullmatch(word) for _name, pattern in _LEVEL_RES)


def is_generic_role(role: str) -> bool:
    """Whether ``role`` is a generic title: only level/seniority words and a bare role noun ("Staff Engineer")."""

    if type(role) is not str or tag_title(role).function is None:
        return False
    rest = [word for word in _words(role) if word not in _SENIORITY_WORDS and not _is_level_word(word)]
    return bool(rest) and all(word in GENERIC_ROLE_WORDS for word in rest)


@dataclass(frozen=True, slots=True)
class TagQuery:
    """The ``(level, function)`` pairs the profile's roles ask for."""

    pairs: frozenset[tuple[str, str]]

    def __bool__(self) -> bool:
        return bool(self.pairs)


def tag_query_for_roles(roles: Sequence[str]) -> TagQuery:
    """Tag each role phrase; a role with no function contributes nothing."""

    pairs: set[tuple[str, str]] = set()
    for role in roles:
        if type(role) is not str or not role.strip():
            continue
        tag = tag_title(role)
        if tag.function is not None:
            pairs.add((tag.level, tag.function))
    return TagQuery(frozenset(pairs))


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
    """What the matcher decided for one title, and how (``by``: ``rule`` / ``tag`` / ``vetoed`` / ``None``)."""

    matched: bool
    by: str | None = None
    #: Matched by a generic title's rule alone, the posting's function tag is not known yet.
    tag_pending: bool = False


_NO_MATCH = TitleDecision(False)
_BY_RULE = TitleDecision(True, "rule")
_BY_TAG = TitleDecision(True, "tag")
_VETOED = TitleDecision(False, "vetoed")
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
    """

    def __init__(self, roles: Sequence[str], store: TagStore | None = None) -> None:
        self.roles = tuple(str(role) for role in roles)
        self.query = tag_query_for_roles(self.roles) if store is not None else TagQuery(frozenset())
        self._store = store if self.query else None
        self.counts = TitleMatchCounts()
        generic = tuple(role for role in self.roles if is_generic_role(role))
        names_a_function = any(role not in generic and tag_title(role).function is not None for role in self.roles)
        #: The generic titles a known function tag can veto: only with a store, and only next to a function-specific title.
        self.generic_roles = generic if self._store is not None and names_a_function else ()
        self._specific_roles = tuple(role for role in self.roles if role not in self.generic_roles)
        self._functions = frozenset(function for _level, function in self.query.pairs)

    def _rule_only(self) -> None:
        """The store cannot be read: from here on exactly the rule (no tag match, no veto)."""

        self._store = None
        self.generic_roles, self._specific_roles = (), self.roles

    def decide(self, title: str) -> TitleDecision:
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
        if (tag.level, tag.function) in self.query.pairs:
            self.counts.matched_by_tag += 1
            return _BY_TAG
        return _NO_MATCH

    def matches(self, title: str) -> bool:
        return self.decide(title).matched


def title_matches(title: str, roles: Sequence[str], store: TagStore | None = None) -> bool:
    """One-shot form of ``TitleMatcher`` (what ``_role_match`` calls)."""

    return TitleMatcher(roles, store).matches(title)


__all__ = [
    "GENERIC_ROLE_WORDS",
    "TagQuery",
    "TitleDecision",
    "TitleMatchCounts",
    "TitleMatcher",
    "is_generic_role",
    "open_tag_store",
    "tag_query_for_roles",
    "title_matches",
]
