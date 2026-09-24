"""Role research derived from the posting text alone (packet CHANGE 2b: no search).

Deliberately not a model call: the posting text Scout already captured
(``PostingRow.text``) is the only input, split into responsibility-shaped
and requirement-shaped lines by a bounded, static heuristic -- consistent
with "no search" and with keeping this section free (no cost, no key
dependency, always available when a posting has captured text).
"""

from __future__ import annotations

import re

from .types import RoleResearch

_MAX_LINES = 20
_REQUIREMENT_MARKERS = re.compile(
    r"\b(require|requirement|must have|must-have|need to|years? of experience|proficien|degree|qualif)\w*",
    re.IGNORECASE,
)
_RESPONSIBILITY_MARKERS = re.compile(
    r"\b(you will|you'll|responsib|own|build|lead|design|maintain|collaborat|drive|manage)\w*",
    re.IGNORECASE,
)
_BULLET_PREFIX = re.compile(r"^[\-\*•‣◦⁃∙\d\.\)\s]+")


def _lines(text: str) -> list[str]:
    result = []
    for raw_line in text.splitlines():
        line = _BULLET_PREFIX.sub("", raw_line).strip()
        if len(line) >= 8:
            result.append(line)
    return result


def research_role(posting_text: str) -> RoleResearch:
    """Extract responsibility-shaped and requirement-shaped lines from posting text."""

    responsibilities: list[str] = []
    requirements: list[str] = []
    for line in _lines(posting_text):
        if _REQUIREMENT_MARKERS.search(line) and len(requirements) < _MAX_LINES:
            requirements.append(line)
        elif _RESPONSIBILITY_MARKERS.search(line) and len(responsibilities) < _MAX_LINES:
            responsibilities.append(line)
    return RoleResearch(responsibilities=tuple(responsibilities), requirements=tuple(requirements))


__all__ = ["research_role"]
