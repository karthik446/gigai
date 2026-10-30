"""The model-free fact guard over a tailored resume (0.1.11.12, dossier-B).

``unverified_facts`` lists the words and numbers a tailored resume states that NEITHER the master resume NOR the
posting states: a tool or product name (``Rust``, ``HIPAA``), a place (``Washington``), a number (``73%``,
``12``), a credential word (``Certified``, ``patented``). Each flag is the word as the resume prints it, once,
in order of appearance. Flags are for the user to read: this module never rewrites, drops or blocks a line, never
stops a save, an apply or a PDF, and is never a model call. The pick and the tailor are not changed by it; the
view that reads a stored resume (``job_actions.pick_view``) computes the list on the read and stores nothing.

What is checked: the bullets and plain lines of the resume's sections (a section opens at a ``## `` heading; ``###``
and ``####`` head an entry inside it). What is never checked, so never flagged: everything before the first ``## ``
heading (name, contact, city, a legacy ``# Name`` line), every heading, the title/dates lines under an entry heading
(``Staff Engineer | 2023 - Present``: the stale check's business), a bare four-digit year (``2021``; ``2,000`` and
``2048 shards`` are numbers), the lines of a Skills or Languages section (a skills line is the tailor's own ``answers'
skills`` rule), the trailing ``<!-- R12 -->`` ref comments, stop words (``TERM_STOP_WORDS``: months, weekdays,
``I``, ``OK``, cities), and a Capitalized word at a sentence start (the line's first word, or right after ``.``,
``!``, ``?``: ``Built``, ``Delivered``) unless it is technical on its face (digits, ``+``, ``#``, an inner dot,
inner capitals, all caps). A bracket or a quote opens no sentence: ``(Rust)``, ``[Zig]``, ``"Go"`` are claims.

Known limits, by design: a Capitalized invented name as a bullet's FIRST word (``Rust services for …``) reads as
sentence-initial and is not flagged unless technical on its face; an invented company in a ``### `` heading is not
flagged (headings are excluded); ``;`` and ``:`` are not sentence breaks (``Stack: Rust`` flags ``Rust``, and a
Capitalized verb after ``;`` would flag); a ``Languages:`` line outside a Skills/Languages section is checked.

Sources: the master's text and the posting's text, matched as the tailor's own guards match them (``text_terms``
/ ``source_terms``: case-folded, aliases such as ``Postgres`` = ``PostgreSQL``, naive plurals, the parts of a
hyphenated or slash-joined token; numbers by value, so ``4,000`` meets ``4000`` and ``forty`` meets ``40``; a
credential word by its family, so ``certification`` supports ``certified``). A pasted job has no posting text (it
is never kept): the master alone is the source then, a posting-only word is flagged, and the caller says so
(``job_actions`` ``fact_checks_posting``).
"""

from __future__ import annotations

import re
from decimal import Decimal

from .tailored_resume import (
    TERM_ALIASES,
    TERM_STOP_WORDS,
    _is_technical,
    _term_variants,
    _tokens_with_parts,
    numeric_values,
    source_terms,
)

#: Words that claim a credential (the spec's list plus patent/award), each with the family that supports it: a source
#: with any word of the family (``certification`` for ``certified``, ``licensed`` for ``licence``, ``Chartered`` for
#: ``chartered``) supports the claim.
_CREDENTIAL_FAMILIES: tuple[str, ...] = ("certif", "licen", "accredit", "charter", "patent", "award")
_CREDENTIAL_WORDS = frozenset(
    "certified certification certifications certificate certificates licensed license licence accredited accreditation "
    "chartered patent patents patented award awards awarded".split()
)
#: A section whose lines are never checked (the heading's words, folded).
_UNCHECKED_SECTIONS = frozenset({"skills", "skill", "languages", "language"})
_REF_COMMENT = re.compile(r"\s*<!--.*?-->")
_BULLET = re.compile(r"^[ \t]*(?:[-*•–—·]|\d+[.)])[ \t]+")
_WORD = re.compile(r"[A-Za-z]+")
_YEAR = re.compile(r"(?:19|20)\d\d")
#: What ends a sentence on a resume line, for the "Capitalized only at a sentence start" rule. The guard's own set: a
#: bracket or a quote opens no sentence here (``(Rust)``, ``[Zig]``, ``"Go"`` are claims), unlike the posting-side rule.
_SENTENCE_END = frozenset(".!?")
#: The text a number sits in, shown whole: ``73%``, ``$1.2M``, ``p99``, ``5+``.
_NUMBER_RUN = frozenset("%$+")


def unverified_facts(markdown: str, master_text: str, posting_text: str | None = None) -> tuple[str, ...]:
    """The words and numbers ``markdown`` states that neither ``master_text`` nor ``posting_text`` states (the module
    text): each as the resume prints it, once (case-folded), in order of appearance. Pure; never raises on text."""

    sources = (master_text or "", posting_text or "")
    known_terms: set[str] = set()
    known_values: set[Decimal] = set()
    known_words: set[str] = set()
    for source in sources:
        known_terms |= source_terms(source)
        known_values |= {mention.value for _offset, mention in _numbers(source)}
        known_words |= {word.lower() for word in _WORD.findall(source)}
    known_families = tuple(family for family in _CREDENTIAL_FAMILIES if any(word.startswith(family) for word in known_words))

    flags: list[str] = []
    seen: set[str] = set()
    for line in _checked_lines(markdown):
        for _offset, surface in sorted(_line_claims(line, known_terms, known_values, known_families)):
            key = surface.casefold()
            if key not in seen:
                seen.add(key)
                flags.append(surface)
    return tuple(flags)


def _checked_lines(markdown: str) -> list[str]:
    """The lines the guard reads, bullet markers and ref comments off (the module text's exclusions)."""

    out: list[str] = []
    in_sections = False
    unchecked_section = False
    after_entry_heading = False
    for raw in markdown.splitlines():
        line = _REF_COMMENT.sub("", raw).strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("## "):  # a section opens at a level-2 heading only; `###`/`####` head an entry inside it
                in_sections = True
                after_entry_heading = False
                unchecked_section = any(word in _UNCHECKED_SECTIONS for word in _WORD.findall(line.lower()))
            elif in_sections:
                after_entry_heading = True
            continue
        if not in_sections or unchecked_section:
            continue
        bullet = _BULLET.match(line)
        if bullet is None and after_entry_heading:
            continue  # a title/dates line under the entry's heading
        after_entry_heading = False
        out.append(line[bullet.end():] if bullet else line)
    return out


def _line_claims(line: str, known_terms: set[str], known_values: set[Decimal], known_families: tuple[str, ...]) -> list[tuple[int, str]]:
    """``(offset, surface)`` of every claim on one checked line that no source supports."""

    claims: list[tuple[int, str]] = []
    for offset, mention in _numbers(line):
        if mention.value not in known_values and _YEAR.fullmatch(mention.span) is None:
            claims.append((offset, _number_shown(line, offset, len(mention.span))))
    for token, offset in _tokens_with_parts(line):
        if "/" in token and token.lower() not in TERM_ALIASES:
            continue  # its parts follow it in the same list
        parts = [token] if "-" not in token or token.lower() in TERM_ALIASES else [part for part in token.split("-") if part]
        for part in parts:
            if _is_claim(line, part, offset) and not _supported(part, known_terms, known_families):
                claims.append((offset, part.strip(".")))
            offset += len(part) + 1
    return claims


def _numbers(text: str):
    """``(offset, mention)`` of each number ``numeric_values`` reads in ``text``, at the mention's own place (a cursor: the
    same digits may sit inside an earlier term, ``p99 … 99``). A digit run preceded by a letter (``p99``, ``k8s``) is a
    term, the term rule's: not a number here, on either side."""

    cursor = 0
    for mention in numeric_values(text):
        offset = text.find(mention.span, cursor)
        if offset < 0:
            continue
        cursor = offset + len(mention.span)
        if offset > 0 and text[offset - 1].isalpha():
            continue
        yield offset, mention


def _is_claim(line: str, token: str, offset: int) -> bool:
    lowered = token.strip(".").lower()
    if lowered in TERM_STOP_WORDS or len(lowered) < 2 or not any(char.isalpha() for char in token):
        return False
    if lowered in _CREDENTIAL_WORDS or _is_technical(token):
        return True
    return token[0].isupper() and not _at_sentence_start(line, offset)


def _at_sentence_start(line: str, offset: int) -> bool:
    """A Capitalized word at the line's start or right after ``.``/``!``/``?`` (``_SENTENCE_END``); an opening bracket
    or quote before it is skipped, so ``(Rust)`` mid-line is a claim and ``(Built`` at the start is not."""

    before = line[:offset].rstrip(" \t").rstrip("(\"'[“‘").rstrip(" \t")
    return not before or before[-1] in _SENTENCE_END


def _supported(token: str, known_terms: set[str], known_families: tuple[str, ...]) -> bool:
    """A credential word is supported by any word of its family in a source (``certified`` by ``certification``);
    any other token by one of its canonical variants (``text_terms``' matching)."""

    lowered = token.strip(".").lower()
    if lowered in _CREDENTIAL_WORDS:
        return any(lowered.startswith(family) for family in known_families)
    return bool(_term_variants(token) & known_terms)


def _number_shown(line: str, start: int, length: int) -> str:
    end = start + length
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in _NUMBER_RUN):
        start -= 1
    while end < len(line) and (line[end].isalnum() or line[end] in _NUMBER_RUN):
        end += 1
    return line[start:end]


__all__ = ["unverified_facts"]
