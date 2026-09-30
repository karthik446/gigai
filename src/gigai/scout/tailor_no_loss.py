"""0110-006: the deterministic no-loss check for one tailored rewrite.

A rewrite may reorder or re-lead a resume line; it may never make it weaker.
``lost_items(rewrite, sources)`` lists every item a cited source states that
the rewrite no longer states, by rule (an empty dict means the rewrite
passes):

- ``numbers``: every number of the sources (``tailored_resume.numeric_values``:
  digits, number words, ``5+``, ``%``, ``$1.2M`` = ``1.2 million``, both ends
  of a range) must be stated by the rewrite; the digits inside a name
  (``k8s``, ``EC2``) are the entities rule's, not numbers.
- ``entities``: named technologies and entities -- ``posting_terms`` of the
  source (technical tokens and Capitalized words not at a sentence start)
  plus every all-caps token of 2-6 letters, stop word or not (``CO``, ``US``,
  ``EU``).  One survives when it, or one of its slash/hyphen parts, is among
  the rewrite's ``source_terms`` (aliases and naive plurals included).
- ``ownership``: ownership-verb FAMILIES (``owns`` -> ``owned`` passes,
  ``owning`` -> ``with`` fails).  "drive/drove" is deliberately not one
  (it is filler far more often than ownership).
- ``scope``: scope and qualifier phrases ("end to end", "cross functional",
  "production", "highly available", ...), with a small equivalence table
  ("org wide" = "across the organization", "every day" = "daily").
- ``skills`` (only with ``skills=True``, the skills-line rule, Q3): every
  comma/semicolon/pipe-separated item of a source skills line stays; a
  skills rewrite may reorder, never drop.

Text is compared lowercased with every Unicode dash turned into a space, so
"end-to-end" = "end to end".  Length is not a rule: dropping facts is what
is caught, a pure reorder is fine.  Pure code: no I/O, no model call.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

from .tailored_resume import (
    TERM_STOP_WORDS,
    _term_variants,
    canonical_term,
    numeric_values,
    posting_terms,
    source_terms,
    text_terms,
)

#: Every Unicode dash or hyphen (and the ASCII one) becomes a space.
_DASHES = re.compile(r"[\-‐-―−﹘﹣－]")
_SPACES = re.compile(r"\s+")
#: A name with a digit after its first letter (``k8s``, ``p99``, ``EC2``, ``SOC2``):
#: the entities rule owns it, so the numbers rule never reads its digits as
#: a number ("k8s" -> "Kubernetes" drops no "8").  ``5k``, ``3rd``, ``$1.2M``
#: start with a digit and stay numbers.
_NAME_WITH_DIGITS = re.compile(r"\b[A-Za-z]+\d[A-Za-z0-9]*\b")
#: An all-caps token of 2-6 letters standing alone (``CO``, ``DSAR``; not ``SOC2``).
_ALL_CAPS = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,6}(?![A-Za-z0-9])")

#: (family, pattern) over the normalized text.  ``manage`` counts people
#: management only: "fully managed" (a service) is skipped.
OWNERSHIP_FAMILIES: tuple[tuple[str, str], ...] = (
    ("own", r"\bown(?:s|ed|ing|er|ers|ership)?\b"),
    ("lead", r"\b(?:lead|leads|leading|led)\b"),
    ("architect", r"\barchitect(?:s|ed|ing)?\b"),  # the verb; "architecture" is a noun
    ("found", r"\b(?:co ?)?found(?:ed|er|ers|ing)\b"),
    ("spearhead", r"\bspearhead\w*"),
    ("head", r"\bheaded\b"),
    ("manage", r"(?<!fully )\bmanag(?:ed|es|ing)\b"),
    ("direct", r"\bdirect(?:ed|s|ing)\b"),
    ("establish", r"\bestablish\w*"),
    ("pioneer", r"\bpioneer\w*"),
    ("initiate", r"\binitiat\w*"),
    ("launch", r"\blaunch\w*"),
    ("design", r"\bdesign\w*"),
    ("build", r"\b(?:built|build\w*)"),
    ("create", r"\bcreat\w*"),
    ("mentor", r"\bmentor\w*"),
    ("hire", r"\bhir(?:e|ed|es|ing)\b"),
    ("sole", r"\bsole(?:ly)?\b"),
)

#: (phrase, pattern) over the normalized text; an alternative in a pattern
#: is an accepted equivalent ("high availability" keeps "highly available").
SCOPE_PHRASES: tuple[tuple[str, str], ...] = (
    ("end to end", r"\bend to end\b"),
    ("cross functional", r"\bcross functional\b"),
    ("org wide", r"\b(?:company|org|organization|organisation|platform|team) wide\b|\bacross the (?:company|org|organization|organisation)\b"),
    ("across", r"\bacross\b|\b(?:company|org|organization|organisation) wide\b"),
    ("multi", r"\bmulti ?(?:team|region|account|tenant|cloud|domain)s?\b"),
    ("global", r"\bglobal(?:ly)?\b"),
    ("entire", r"\bentire\b"),
    ("every", r"\bevery\b(?! ?day\b)"),
    ("daily", r"\bevery ?day\b|\bdaily\b"),
    ("from scratch", r"\bfrom scratch\b"),
    ("greenfield", r"\bgreen ?field\b"),
    ("zero to one", r"\b(?:0|zero) to (?:1|one)\b"),
    ("production", r"\bproduction\b"),
    ("at scale", r"\bat scale\b"),
    ("fault tolerant", r"\bfault toleran(?:t|ce)\b"),
    ("highly available", r"\bhighly available\b|\bhigh availability\b"),
    ("mission critical", r"\bmission critical\b"),
    ("real time", r"\breal ?time\b"),
    ("distributed", r"\bdistributed\b"),
)

_OWNERSHIP = tuple((name, re.compile(pattern)) for name, pattern in OWNERSHIP_FAMILIES)
_SCOPE = tuple((name, re.compile(pattern)) for name, pattern in SCOPE_PHRASES)

#: Skills-line parsing: a leading ``Label:`` and the item separators.
_SKILLS_LABEL = re.compile(r"\A[^:,;|]{1,40}:\s*")
_SKILLS_SEPARATORS = re.compile(r"[,;|•·]")
_SKILLS_MARKERS = re.compile(r"\A(?:[#>*\-•–—]+\s*)+")
_FUNCTION_WORDS = frozenset({"a", "an", "and", "or", "of", "the", "with", "in", "on", "for", "to"})


def normalize(text: str) -> str:
    """Lowercase, every dash a space, whitespace collapsed."""

    return _SPACES.sub(" ", _DASHES.sub(" ", text.lower())).strip()


def _entities(text: str) -> set[str]:
    found = {term for term in posting_terms(text) if not term[:1].isdigit()}  # "$1.2M" is the numbers rule's
    found |= {canonical_term(token) for token in _ALL_CAPS.findall(text)}
    return found


def _survives(entity: str, rewrite_terms: set[str]) -> bool:
    parts = [entity, *(part for part in re.split(r"[/-]", entity) if part)]
    return any(_term_variants(part) & rewrite_terms for part in parts)


def _skill_items(source: str) -> list[str]:
    body = _SKILLS_MARKERS.sub("", source.strip())
    body = _SKILLS_LABEL.sub("", body)
    return [item.strip(" .*_()[]") for item in _SKILLS_SEPARATORS.split(body) if item.strip(" .*_()[]")]


def lost_items(rewrite: str, sources: Iterable[str], *, skills: bool = False) -> dict[str, list[str]]:
    """What ``sources`` state that ``rewrite`` drops, by rule; ``{}`` means no loss.

    Keys appear only when something is lost; every list is sorted.
    """

    sources = [source for source in sources if source]
    lost: dict[str, list[str]] = {}

    kept_values = {mention.value for mention in numeric_values(_NAME_WITH_DIGITS.sub(" ", rewrite))}
    numbers: dict[object, str] = {}
    for source in sources:
        for mention in numeric_values(_NAME_WITH_DIGITS.sub(" ", source)):
            if mention.value not in kept_values:
                numbers.setdefault(mention.value, mention.span)
    if numbers:
        lost["numbers"] = sorted(set(numbers.values()))

    rewrite_terms = source_terms(rewrite) | {canonical_term(token) for token in _ALL_CAPS.findall(rewrite)}
    entities = {entity for source in sources for entity in _entities(source) if not _survives(entity, rewrite_terms)}
    if entities:
        lost["entities"] = sorted(entities)

    flat_rewrite = normalize(rewrite)
    flat_sources = [normalize(source) for source in sources]
    ownership = {
        name for name, pattern in _OWNERSHIP
        if any(pattern.search(source) for source in flat_sources) and not pattern.search(flat_rewrite)
    }
    if ownership:
        lost["ownership"] = sorted(ownership)
    scope = {
        name for name, pattern in _SCOPE
        if any(pattern.search(source) for source in flat_sources) and not pattern.search(flat_rewrite)
    }
    if scope:
        lost["scope"] = sorted(scope)

    if skills:
        dropped: set[str] = set()
        for source in sources:
            for item in _skill_items(source):
                wanted = {term for term in text_terms(item) if term not in _FUNCTION_WORDS}
                if wanted and not all(_term_variants(term) & rewrite_terms for term in wanted):
                    dropped.add(normalize(item))
        if dropped:
            lost["skills"] = sorted(dropped)
    return lost


def anchor_terms(text: str) -> set[str]:
    """The words of a requirement name or posting phrase that can anchor a rewrite's reason."""

    return {term for term in source_terms(text) if len(term) >= 2 and term not in TERM_STOP_WORDS and not term.isdigit()}


__all__ = ["OWNERSHIP_FAMILIES", "SCOPE_PHRASES", "anchor_terms", "lost_items", "normalize"]
