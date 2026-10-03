"""0.1.10-001: a local, no-model heads-up for contact details in a resume, and (0110-046) the
strip every resume import runs.

``detect_contact_details`` only *notices* the obvious shapes -- an email address, a phone
number, a linkedin.com or github.com link, a street address -- so the user can be told. An
empty result means "nothing obvious found", never "clean": callers must say nothing then.

``strip_contact_lines`` (0110-046) is what a resume import stores: GigAI never stores the
user's name or contact details. It removes exactly what ``resume_privacy.model_resume``
withholds from a model (the name line, the header's contact lines, contact-only lines, and
emails, phone numbers and links inside other lines), so the stored text is the text a model
may see, and counts what it removed by kind (counts only, never a value). It cannot catch
everything (a name inside a sentence, an unusual layout): the README says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RESUME_WARNING = (
    "Scout removes your name and contact lines (email, phone, address, links) before sending your resume to "
    "the model you pick (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider), and adds them back "
    "only in your PDF, on this machine. It can't catch personal details elsewhere in the text (a first line that holds both a title and your name, or contact details inside a sentence), so keep those out. "
    "Your contact line lives in Settings > Resume display."
)
#: Shown (UI, CLI, API ``contact_removed.message``) when an import removed contact lines.
REMOVED_MESSAGE = "We removed your contact lines; you'll add them when you make a PDF."

# Tried only from the first character of a run (a match from inside one is a match from its start), so a long run is read once.
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\d)"
    r"|(?<![\w.])\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}(?!\d)"
)
_LINK = re.compile(r"(?:linkedin|github)\.com/", re.IGNORECASE)
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][A-Za-z.']*\s+){1,3}"
    r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl)\b"
)

# Shown label -> pattern, in the order the heads-up lists them.
_CHECKS = (("email", _EMAIL), ("phone", _PHONE), ("links", _LINK), ("address", _ADDRESS))


def detect_contact_details(text: str) -> list[str]:
    """Labels of the contact-detail shapes found in ``text`` (``[]`` when none)."""

    return [label for label, pattern in _CHECKS if pattern.search(text)]


def heads_up(found: list[str]) -> str | None:
    """The heads-up line for ``found``; ``None`` when nothing was found."""

    if not found:
        return None
    return f"This resume seems to contain: {', '.join(found)}. Remove them before continuing?"


# --- the import strip (0110-046) -------------------------------------------------------------

#: The kinds ``strip_contact_lines`` counts, in the order a report lists them.
REMOVED_KINDS: tuple[str, ...] = ("name", "email", "phone", "links", "address", "work_authorization", "other")


@dataclass(frozen=True)
class ContactStrip:
    """A resume with its contact lines removed, and how many of each kind went (counts only)."""

    text: str
    removed: dict[str, int] = field(default_factory=dict)
    #: The removed name's words, lowercased: in memory only (the import keeps a file name holding
    #: them out of the stored label); never stored, logged or returned.
    name_words: frozenset[str] = frozenset()

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def _kinds(text: str) -> list[str]:
    from .resume_privacy import _BODY_URL, _EMAIL, _PHONE, _PO_BOX, _STREET, _URL, _WORK_AUTH, _ZIP_LINE

    found = []
    if _EMAIL.search(text):
        found.append("email")
    if _PHONE.search(text):
        found.append("phone")
    if _URL.search(text) or _BODY_URL.search(text):
        found.append("links")
    if _STREET.search(text) or _PO_BOX.search(text) or _ZIP_LINE.search(text):
        found.append("address")
    if _WORK_AUTH.search(text):
        found.append("work_authorization")
    return found


def strip_contact_lines(text: str) -> ContactStrip:
    """``text`` without the name and contact details ``model_resume`` withholds; unchanged text when none.

    Pure: no I/O, no model, never logs.  ``removed`` maps a kind (``REMOVED_KINDS``) to the number
    of lines that lost one: the name line, each withheld contact line, each kept line that had an
    email, phone number or link taken out."""

    from .resume_privacy import _name_then_headline, _name_tokens_of, is_name_line, model_resume

    stripped = model_resume(text)
    if stripped.text == text:
        return ContactStrip(text)
    numbered = [line.strip() for line in text.splitlines() if line.strip()]
    kept = dict(stripped.lines)
    removed: dict[str, int] = {}

    def count(kinds: list[str]) -> None:
        for kind in kinds:
            removed[kind] = removed.get(kind, 0) + 1

    for number, line in enumerate(numbered, 1):
        if number in stripped.withheld:
            # The name line, a contact line, or a line left empty once its contact details went.
            count(["name"] if number == 1 and is_name_line(line) else _kinds(line) or ["other"])
        elif kept.get(number, line) != line:
            # Inline contact details taken out, or the name's words (a "Name - Headline" first line).
            count(_kinds(line) or ["name"])
    ordered = {kind: removed[kind] for kind in REMOVED_KINDS if kind in removed}
    first = numbered[0] if numbered else ""
    split = _name_then_headline(first) if first else None
    words = _name_tokens_of(first) if first and is_name_line(first) else (_name_tokens_of(split[0]) if split else set())
    return ContactStrip(stripped.text, ordered or {"other": 1}, frozenset(words))


def removed_summary(removed: dict[str, int]) -> str:
    """``"name 1, email 1, phone 2"``: a count line, never a value."""

    return ", ".join(f"{kind.replace('_', ' ')} {count}" for kind, count in removed.items())
