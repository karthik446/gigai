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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .ats_board_clients import matches_roles
from .posting_tags import default_store, normalize_title, tag_title
from .tag_store import TagStore


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

    def to_json(self) -> dict[str, int]:
        return {
            "matched_by_rule": self.matched_by_rule,
            "matched_by_tag": self.matched_by_tag,
            "untagged_fallback": self.untagged_fallback,
        }


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
    was the only judge.
    """

    def __init__(self, roles: Sequence[str], store: TagStore | None = None) -> None:
        self.roles = tuple(str(role) for role in roles)
        self.query = tag_query_for_roles(self.roles) if store is not None else TagQuery(frozenset())
        self._store = store if self.query else None
        self.counts = TitleMatchCounts()

    def matches(self, title: str) -> bool:
        if matches_roles(title, self.roles):
            self.counts.matched_by_rule += 1
            return True
        if self._store is None or type(title) is not str:
            return False
        try:
            tag = self._store.get(normalize_title(title))
        except (OSError, sqlite3.Error):
            self._store = None  # unreadable: from here on exactly the rule
            return False
        if tag is None or tag.function is None:
            self.counts.untagged_fallback += 1
            return False
        if (tag.level, tag.function) in self.query.pairs:
            self.counts.matched_by_tag += 1
            return True
        return False


def title_matches(title: str, roles: Sequence[str], store: TagStore | None = None) -> bool:
    """One-shot form of ``TitleMatcher`` (what ``_role_match`` calls)."""

    return TitleMatcher(roles, store).matches(title)


__all__ = [
    "TagQuery",
    "TitleMatchCounts",
    "TitleMatcher",
    "open_tag_store",
    "tag_query_for_roles",
    "title_matches",
]
