"""0.1.11.12 dossier-C (gigai-6po): up to three red flags beside the fit score, derived from the stored matrix when it is read.

A flag names one thing the posting asks for that the resume does not show, in the posting's own words, so a 60% that
hides "hybrid in San Francisco" or "Java and Kotlin" is read for what it is. Three kinds, one flag per row, in the order
location, language, level, at most :data:`MAX_RED_FLAGS`:

- ``location``: the eligibility row for the place or the work mode (``elig-location`` / ``elig-work-mode``) that is unmet,
  or unclear on a hard row. The text is the row's requirement.
- ``language``: a must-have row (hard, askable or unclassed; never an eligibility row, never one of a list) that is not met
  and names a programming language (``assess_strict.LANGUAGES``, aliases folded) the candidate's skills line does not
  declare, read as the strict check reads a list: an "and" list or a slashed one needs every name (``requires_all`` /
  ``slash_joined``), any other list is met by any one declared name ("Python or Java" with Python declared raises nothing).
  ``Asks for Java, Kotlin``.
- ``level``: a hard row that is not met and asks for "N+ years" (the strict check's reading) or a seniority title (staff,
  principal, tech / team / engineering lead, lead engineer, director of, head of, manager of managers); "lead" the verb,
  a director-level stakeholder or the VP one reports to is not an ask, and a row naming a degree is a credential, not a
  level. ``Asks for 8+ years of back-end experience in …``.

Nothing here is stored and nothing here moves the fit, its cap, the verdict or the questions: the flags are a reading
of the stored rows. Only ``unmet`` and ``unclear`` rows flag: ``met``, ``answered`` (the user said yes) and any other
status do not, and ``list_item`` / ``nice_to_have`` rows (minor gaps, said elsewhere) never do.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .assess_strict import language_of, named_tools, requires_all, slash_joined, years_required
from .resume_gate import is_mandatory
from .requirements_list import is_eligibility_id

MAX_RED_FLAGS = 3
LOCATION = "location"
LANGUAGE = "language"
LEVEL = "level"

#: The eligibility rows that say where the work is and how (contracts ``ELIGIBILITY_ROW_IDS``); sponsorship and region are not a place.
_PLACE_ROW_IDS = frozenset({"elig-location", "elig-work-mode"})
_SENIORITY = re.compile(
    r"\b(?:staff|principal|(?:tech|technical|team|engineering)[ -]lead|lead[ -](?:engineer|developer|architect)|director of|head of|manager of managers)\b",
    re.IGNORECASE,
)
#: A row naming a degree asks for a credential; its years ("or 4 years equivalent experience") are not a level ask.
_DEGREE = re.compile(r"\b(?:bachelor|master|degree|bs|ms|phd|b\.s|m\.s|ph\.d)\b", re.IGNORECASE)
_HARD = "hard"
_UNMET = "unmet"
_UNCLEAR = "unclear"
#: A flag's text is one short line: the requirement cut at a word.
_TEXT_CHARS = 60


@dataclass(frozen=True)
class RedFlag:
    kind: str
    text: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


def _short(text: str) -> str:
    words = " ".join(text.split())
    if len(words) <= _TEXT_CHARS:
        return words
    head = words[: _TEXT_CHARS - 1]
    if " " in head:
        head = head[: head.rfind(" ")]
    return head.rstrip(" ,;:-") + "…"


def _as_row(row: object) -> Mapping[str, object]:
    """A stored row as its JSON (``RequirementMatrixRow.to_json``), or the mapping it already is."""

    if isinstance(row, Mapping):
        return row
    to_json = getattr(row, "to_json", None)
    return to_json() if callable(to_json) else {}


def _missing_languages(row: Mapping[str, object], requirement: str, declared: frozenset[str]) -> list[str]:
    """The languages the row needs that ``declared`` lacks, in the row's names, read as T8 reads a list (the module text)."""

    named = [(tool, language) for tool in named_tools(row) if (language := language_of(tool)) is not None]
    if not named:
        return []
    names = [tool for tool, _language in named]
    required = requires_all(requirement, names) or (tuple(names) if slash_joined(requirement, names) else ())
    if required:
        return [tool for tool, language in named if tool in required and language not in declared]
    if any(language in declared for _tool, language in named):
        return []
    return names


def _flag(row: Mapping[str, object], declared: frozenset[str]) -> RedFlag | None:
    status = row.get("status")
    if status not in (_UNMET, _UNCLEAR):
        return None
    requirement = str(row.get("requirement") or "")
    klass = row.get("class")
    row_id = row.get("id") if isinstance(row.get("id"), str) else None
    if row_id in _PLACE_ROW_IDS:
        return RedFlag(LOCATION, _short(requirement)) if status == _UNMET or klass == _HARD else None
    if row_id is not None and is_eligibility_id(row_id):
        return None
    if not is_mandatory(row):
        return None
    missing = _missing_languages(row, requirement, declared)
    if missing:
        return RedFlag(LANGUAGE, "Asks for " + ", ".join(missing))
    if klass != _HARD or _DEGREE.search(requirement):
        return None
    if years_required(requirement) is not None or _SENIORITY.search(requirement):
        return RedFlag(LEVEL, "Asks for " + _short(requirement))
    return None


def red_flags(matrix: Iterable[object], declared: frozenset[str]) -> tuple[RedFlag, ...]:
    """At most :data:`MAX_RED_FLAGS` flags of ``matrix`` (stored rows or their JSON), location first, then language, then level.

    ``declared`` is the languages the candidate's skills line declares (``assess_strict.declared_languages``, casefolded);
    empty when there is no master. Pure: the rows are read, never changed.
    """

    found = [flag for flag in (_flag(_as_row(row), declared) for row in matrix) if flag is not None]
    ordered = [flag for kind in (LOCATION, LANGUAGE, LEVEL) for flag in found if flag.kind == kind]
    return tuple(ordered[:MAX_RED_FLAGS])


def red_flags_json(matrix: Iterable[object], declared: frozenset[str]) -> list[dict[str, str]]:
    """:func:`red_flags` as the responses carry it: ``[{"kind": ..., "text": ...}]``, ``[]`` for a clean matrix."""

    return [flag.to_json() for flag in red_flags(matrix, declared)]


def stored_declared(home_root: Path, target: Path) -> frozenset[str]:
    """The languages the stored master's skills line declares, read once for a command or a request; empty without a master.

    The same reading the strict post-check makes of the master (``assess_master.strict_master`` over
    ``Master.markdown(ids=True)``), so a flag and the check agree on what "declared" means. ``tailor_master.stored_master``
    keeps the read per journal head: a second call on an unchanged home reads nothing.
    """

    from .assess_master import strict_master
    from .assess_strict import declared_languages
    from .tailor_master import stored_master

    stored = stored_master(home_root, target)
    if stored is None:
        return frozenset()
    return declared_languages(strict_master(stored.master.markdown(ids=True)))


__all__ = ["LANGUAGE", "LEVEL", "LOCATION", "MAX_RED_FLAGS", "RedFlag", "red_flags", "red_flags_json", "stored_declared"]
