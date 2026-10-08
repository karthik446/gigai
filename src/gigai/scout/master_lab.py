"""0.1.11.7 T2: the lab honesty guard. A lab line is a line about something the user built to learn, never production.

``gigai scout resume master add --lab --when "Oct 2026"`` adds a line that ends ``(personal lab, Oct 2026)`` and
carries the backing ``lab:2026-10`` (``backed:lab:2026-10`` in the master file, ``backed`` of a skill in
``lines.json``). The rules, the same for every writer (the CLI and the Master page routes call ``master_edit``):

* a lab line that states a production word (``production_word``) is REFUSED, not warned;
* a skill added with ``--lab`` is listed as ``NAME (lab)``;
* ``stated_check.read_master`` leaves a lab line and a ``(lab)`` skill out, so ``settle_stated`` never settles a
  requirement row from one;
* the assess prompt lets a lab line support ``met`` only on familiarity or hands-on wording (``assess.md``).

Pure and model-free; this module imports nothing else of Scout, so every reader can use it.
"""

from __future__ import annotations

import re

LAB_REF = re.compile(r"\Alab:(\d{4})-(0[1-9]|1[0-2])\Z")
#: What a lab line's text carries, whatever its backing says: the label ``(personal lab, Oct 2026)``.
_LAB_TEXT = re.compile(r"\(personal lab(?:,[^()]*)?\)", re.IGNORECASE)
_LAB_SKILL = re.compile(r"\(lab\)\s*\Z", re.IGNORECASE)

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WHEN_WORDS = re.compile(r"\A([A-Za-z]{3,9})\.?,?\s+(\d{4})\Z")
_WHEN_ISO = re.compile(r"\A(\d{4})-(\d{1,2})\Z")
_WHEN_SLASH = re.compile(r"\A(\d{1,2})/(\d{4})\Z")

#: (pattern, the word to name). Rule 1 of the spike: operated, owned, production, at scale, led, years, team size, customers;
#: plus the verbs ``master_edit`` warns about elsewhere as ownership (managed, ran in prod).
_PRODUCTION: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), word) for pattern, word in (
        (r"\boperat(?:ed|es|ing|e|ion|ions)\b", "operated"),
        (r"\bown(?:ed|s|ing|ership)\b", "owned"),
        (r"\bproduction\b|\bprod\b", "production"),
        (r"\bat\s+scale\b", "at scale"),
        (r"\bled\b|\blead(?:s|ing)?\b", "led"),
        (r"\bmanag(?:ed|es|ing)\b", "managed"),
        (r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\+?\s*(?:-\s*)?(?:years?|yrs?)\b|\byears?\b", "years"),
        (r"\bteam\s+of\s+\d+|\b\d+\s*(?:engineers|developers|people|reports|teammates)\b", "a team size"),
        (r"\bcustomers?\b|\bclients?\b", "customers"),
    )
)


class LabError(ValueError):
    """A lab line the guard refuses; the message is one sentence and never quotes the line."""


def parse_when(value: object) -> tuple[int, int]:
    """``(year, month)`` of ``--when``: ``Oct 2026``, ``October 2026``, ``2026-10`` or ``10/2026``; else :class:`LabError`."""

    text = " ".join(value.split()) if isinstance(value, str) else ""
    year = month = 0
    if (found := _WHEN_WORDS.match(text)) is not None:
        name = found.group(1)[:3].lower()
        if name in _MONTHS:
            year, month = int(found.group(2)), _MONTHS.index(name) + 1
    elif (found := _WHEN_ISO.match(text)) is not None:
        year, month = int(found.group(1)), int(found.group(2))
    elif (found := _WHEN_SLASH.match(text)) is not None:
        year, month = int(found.group(2)), int(found.group(1))
    if not (1950 <= year <= 2099 and 1 <= month <= 12):
        raise LabError('--when is the month the lab was done, like "Oct 2026" (or 2026-10); that is not a month and a year')
    return year, month


def lab_ref(when: tuple[int, int]) -> str:
    return f"lab:{when[0]:04d}-{when[1]:02d}"


def label(when: tuple[int, int]) -> str:
    return f"(personal lab, {_NAMES[when[1] - 1]} {when[0]})"


def is_lab_ref(ref: str) -> bool:
    return LAB_REF.fullmatch(ref) is not None


def lab_refs(backed: object) -> tuple[str, ...]:
    return tuple(ref for ref in backed if isinstance(ref, str) and is_lab_ref(ref)) if isinstance(backed, (tuple, list)) else ()


def is_lab_text(text: str) -> bool:
    """A line whose words carry the lab label (what an assessment prompt shows, which has no backing)."""

    return _LAB_TEXT.search(text) is not None


def is_lab_skill(name: str) -> bool:
    """A skill written ``NAME (lab)``."""

    return _LAB_SKILL.search(name.strip()) is not None


def without_label(text: str) -> str:
    """``text`` without its lab label: the month and year of a label are a date, not a number the line states."""

    return _LAB_TEXT.sub(" ", text)


def shows_lab(resume_text: str) -> bool:
    """Whether a RESUME block holds a lab line or a ``(lab)`` skill (the assess prompt carries its lab rule only then)."""

    return is_lab_text(resume_text) or re.search(r"\(lab\)", resume_text, re.IGNORECASE) is not None


def production_word(text: str) -> str | None:
    """The first word of ``text`` that claims production work (the name to show), or ``None``. The lab label itself is ignored."""

    body = _LAB_TEXT.sub(" ", text)
    for pattern, word in _PRODUCTION:
        if pattern.search(body):
            return word
    return None


def lab_text(text: str, when: tuple[int, int]) -> str:
    """``text`` ending in the label: ``Built a resolver (personal lab, Oct 2026)``."""

    body = text.strip().rstrip(".").rstrip()
    return f"{body} {label(when)}"


def refuse_production(text: str) -> None:
    """Refuse a lab line that claims production work: :class:`LabError` naming the word, never quoting the line."""

    word = production_word(text)
    if word is not None:
        raise LabError(
            f'a lab line cannot say "{word}": a lab is not production. Say what you built and how you checked it '
            "(\"Built a resolver, verified with dig +trace\"), or add it without --lab if it was real work."
        )


__all__ = [
    "LAB_REF", "LabError", "is_lab_ref", "is_lab_skill", "is_lab_text", "shows_lab", "lab_ref", "lab_refs", "lab_text", "label", "parse_when",
    "production_word", "without_label", "refuse_production",
]
