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

0.1.10.11: an import (``headings=True``) first takes the links out of the lines that name an
entry and keeps their words (``resume_privacy.heading_links``, the one rule for a link in a
heading); the strip then runs on what is left, unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RESUME_WARNING = (
    "Scout removes your name and contact lines (email, phone, address, links) before sending your resume to "
    "the model you pick (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider), and never stores them. "
    "It can't catch personal details elsewhere in the text (a first line that holds both a title and your name, or contact details inside a sentence), so keep those out. "
    "You type your name and contact details only when you make a PDF."
)
#: Shown (UI, CLI, API ``contact_removed.message``) when an import removed contact lines.
REMOVED_MESSAGE = "We removed your contact lines; you'll add them when you make a PDF."
#: The end of the heads-up for a resume that is about to be imported (0.1.10.7 K: the import removes them).
HEADS_UP_REMOVED = "Scout removes contact lines when it stores the resume. Check that nothing else personal is in the text."

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
    return f"This resume seems to contain: {', '.join(found)}. {HEADS_UP_REMOVED}"


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
    #: 0.1.10.11: a link taken out of a heading that is KEPT: ``(file line, the heading's words as they are
    #: stored, whether the link stood on a line under the heading)``; never the address.
    headings: tuple[tuple[int, str, bool], ...] = ()

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


def strip_contact_lines(text: str, *, headings: bool = False) -> ContactStrip:
    """``text`` without the name and contact details ``model_resume`` withholds; unchanged text when none.

    Pure: no I/O, no model, never logs.  ``removed`` maps a kind (``REMOVED_KINDS``) to the number
    of lines that lost one: the name line, each withheld contact line, each kept line that had an
    email, phone number or link taken out.

    ``headings`` (0.1.10.11, what an import passes): a link in a line that names an entry goes and the
    line's words stay (``resume_privacy.heading_links``; ``ContactStrip.headings`` says where), before
    the strip runs on the rest. ``resume_privacy.HeadingOnlyLink`` when a heading is only a link."""

    from .resume_privacy import _name_then_headline, _name_tokens_of, heading_links, heading_words, is_name_line, model_resume

    links = ()
    if headings:
        text, links = heading_links(text)
    stripped = model_resume(text)
    if stripped.text == text and not links:
        return ContactStrip(text)
    removed: dict[str, int] = {}
    lost_a_link: set[int] = set()
    for number, kind in _removed_lines(text, stripped):
        removed[kind] = removed.get(kind, 0) + 1
        if kind == "links":
            lost_a_link.add(number)
    numbered = [line.strip() for line in text.splitlines() if line.strip()]
    said: list[tuple[int, str, bool]] = []
    if links:
        number_of = {index: number for number, index in enumerate((index for index, line in enumerate(text.splitlines(), 1) if line.strip()), 1)}
        kept = dict(stripped.lines)
        for link in links:
            if number_of[link.line] not in lost_a_link:
                removed["links"] = removed.get("links", 0) + 1  # the line lost a link, as it always counted
            if number_of[link.line] in kept:
                # The words as they are stored: what the strip took from the heading (an email, the name's words) is not said.
                said.append((link.line, heading_words(kept.get(number_of[link.head], "")), link.under))
    ordered = {kind: removed[kind] for kind in REMOVED_KINDS if kind in removed}
    first = numbered[0] if numbered else ""
    split = _name_then_headline(first) if first else None
    words = _name_tokens_of(first) if first and is_name_line(first) else (_name_tokens_of(split[0]) if split else set())
    return ContactStrip(stripped.text, ordered or {"other": 1}, frozenset(words), tuple(said))


def _removed_lines(text: str, stripped) -> list[tuple[int, str]]:  # noqa: ANN001 - a ModelResume, imported lazily
    """``(number, kind)`` for every line ``stripped`` (``model_resume(text)``) took something from.

    ``number`` counts the non-empty lines, 1-based, as ``model_resume`` does; a line that lost
    several kinds appears once per kind."""

    from .resume_privacy import is_name_line

    numbered = [line.strip() for line in text.splitlines() if line.strip()]
    kept = dict(stripped.lines)
    found: list[tuple[int, str]] = []
    for number, line in enumerate(numbered, 1):
        if number in stripped.withheld:
            # The name line, a contact line, or a line left empty once its contact details went.
            kinds = ["name"] if number == 1 and is_name_line(line) else _kinds(line) or ["other"]
        elif kept.get(number, line) != line:
            # Inline contact details taken out, or the name's words (a "Name - Headline" first line).
            kinds = _kinds(line) or ["name"]
        else:
            continue
        found.extend((number, kind) for kind in kinds)
    return found


#: The kind names ``contact_findings`` reports, in this order within a line.
CHECK_KINDS: tuple[str, ...] = ("name", "email", "phone", "address", "link", "work_authorization", "other")


@dataclass(frozen=True)
class ContactFinding:
    """One kind of contact detail on one line of a file (never the value)."""

    kind: str
    line: int


def contact_findings(text: str) -> list[ContactFinding]:
    """The contact details in ``text`` by kind and 1-based FILE line number; ``[]`` when none are noticed.

    The import strip's own detector (what ``strip_contact_lines`` removes) plus the heads-up's
    ``detect_contact_details`` shapes, line by line. Pattern-based: ``[]`` is not proof of a clean file."""

    from .resume_privacy import model_resume

    file_lines = text.splitlines()
    file_line_of = [index for index, line in enumerate(file_lines, 1) if line.strip()]
    found: set[tuple[int, str]] = set()
    for number, kind in _removed_lines(text, model_resume(text)):
        found.add((file_line_of[number - 1], "link" if kind == "links" else kind))
    for index in file_line_of:
        for label in detect_contact_details(file_lines[index - 1]):
            found.add((index, "link" if label == "links" else label))
    order = {kind: n for n, kind in enumerate(CHECK_KINDS)}
    return [ContactFinding(kind, line) for line, kind in sorted(found, key=lambda f: (f[0], order[f[1]]))]


def clean_contact_text(text: str) -> str:
    """``text`` that ``contact_findings`` finds nothing in: the import strip, then any line it still flags.

    Pure; the strip is ``strip_contact_lines`` (the same one the import runs)."""

    cleaned = strip_contact_lines(text).text
    for _ in range(3):
        flagged = {finding.line for finding in contact_findings(cleaned)}
        if not flagged:
            break
        cleaned = "\n".join(line for index, line in enumerate(cleaned.splitlines(), 1) if index not in flagged)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def removed_summary(removed: dict[str, int]) -> str:
    """``"name 1, email 1, phone 2"``: a count line, never a value."""

    return ", ".join(f"{kind.replace('_', ' ')} {count}" for kind, count in removed.items())
