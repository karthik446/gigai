"""0110-8-05: the warning for a GENERIC profile title, with its live match count.

A generic title ("Staff Engineer": only level words and a bare role noun,
``title_query.is_generic_role``) names no function, so its whole-word rule
matches every title that has its words: "Staff Security Engineer", "Staff
Technical Program Manager, Engineering Onboarding". ``gigai scout profile
list`` and ``profile update`` say so, with how many postings of the stored
company index that title ALONE matches under the profile's own search
settings (its countries, posted window and work mode): the search's own read
(``index_search.read_indexed_boards``) with that one title and no tag store,
so the number is what the words alone bring in. No request is made, no model
is called, nothing is written.

What the matcher then does with such a title is ``title_query``'s: next to a
function-specific title a posting's known function tag decides; alone, it
matches as it always did.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .title_query import is_generic_role

WARNING_CODE = "generic_title"


def generic_titles(titles: Sequence[str]) -> tuple[str, ...]:
    """The generic ones among ``titles``, in their order, each once."""

    return tuple(dict.fromkeys(title for title in titles if is_generic_role(title)))


def _warning_text(title: str, matches: int, has_specific: bool) -> str:
    noun = "posting" if matches == 1 else "postings"
    rest = (
        "next to this profile's other titles, a posting whose function tag is known must be one of their functions"
        if has_specific
        else "it names no function, so every title with these words matches; add a title that names one (like 'Staff AI Engineer')"
    )
    return f"title '{title}' alone matches {matches} {noun}: {rest}"


def generic_title_warnings(home_root: Path, target: Path, views: Sequence[object], *, now: datetime | None = None) -> dict[str, list[dict[str, object]]]:
    """``profile_id -> [{code, title, matches, text}]`` for every profile view that has a generic title.

    ``views`` are the read model's ``ProfileView``s (``postings.active_profiles``).
    The index is read only when some profile has a generic title, and each
    company file once however many titles are counted.
    """

    wanted = {view.profile_id: generic_titles(view.config.roles) for view in views}  # type: ignore[attr-defined]
    if not any(wanted.values()):
        return {}

    from ..postings import _IndexOnce, _watched
    from .ats_board_clients import BoardCache
    from .company_index import CompanyIndex
    from .index_search import read_indexed_boards
    from .posting_tags import tag_title

    home_root, target = Path(home_root), Path(target)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    boards = _watched(home_root, target)
    index = _IndexOnce(CompanyIndex.for_home(home_root))
    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    found: dict[str, list[dict[str, object]]] = {}
    for view in views:
        titles = wanted[view.profile_id]  # type: ignore[attr-defined]
        if not titles:
            continue
        roles = view.config.roles  # type: ignore[attr-defined]
        has_specific = any(role not in titles and tag_title(role).function is not None for role in roles)
        for title in titles:
            _rows, _failures, summary = read_indexed_boards(
                boards, index=index, cache=cache, config=replace(view.config, roles=(title,)), now=moment,  # type: ignore[arg-type,type-var]
                remember_search=False, tags=None, home_root=home_root,
            )
            matches = int(summary["matched"])  # type: ignore[call-overload]
            found.setdefault(view.profile_id, []).append(  # type: ignore[attr-defined]
                {"code": WARNING_CODE, "title": title, "matches": matches, "text": _warning_text(title, matches, has_specific)}
            )
    return found


__all__ = ["WARNING_CODE", "generic_title_warnings", "generic_titles"]
