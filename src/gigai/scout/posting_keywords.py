"""Deterministic posting keywords for the Scout ATS score: ``{must, nice, title}`` from posting text.

Vocabulary: the resume's own Skills tags plus the shipped alias table (``gigai/data/ats-aliases-v1.json``).
A term counts as *must* or *nice* by the sentence it appears in ("required / must / you have" against
"nice / plus / bonus / preferred"); a heading line such as "Nice to have:" sets the cue for the lines under
it until the next heading.  A term with no cue anywhere is *must*.  Matching is on word boundaries, so
``Go`` is never found inside ``Google`` or ``good``; terms of two characters or fewer match case-sensitively.
No model, no network.  How accurate this is on real postings is checked by hand at UAT.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

ALIAS_RESOURCE = "ats-aliases-v1.json"

_NICE_CUE = re.compile(r"\b(?:nice[- ]to[- ]have|nice|plus|bonus|preferred|a plus|desirable|ideally|good to have)\b", re.IGNORECASE)
_MUST_CUE = re.compile(r"\b(?:required|requirements?|must|you have|you will have|you bring|minimum|essential|mandatory|qualifications)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")
_TITLE_STOP = frozenset({"and", "the", "for", "of", "with", "at", "in", "to", "a", "an", "ii", "iii", "iv", "i"})
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]")


@dataclass(frozen=True)
class PostingKeywords:
    """Canonical term names, in order of first appearance in the posting; ``must`` and ``nice`` never overlap."""

    must: tuple[str, ...] = ()
    nice: tuple[str, ...] = ()
    title: tuple[str, ...] = ()

    def to_json(self) -> dict[str, list[str]]:
        return {"must": list(self.must), "nice": list(self.nice), "title": list(self.title)}


@lru_cache(maxsize=1)
def alias_table() -> dict[str, tuple[str, ...]]:
    """``canonical term -> aliases`` from the shipped table."""
    data = json.loads(resources.files("gigai.data").joinpath(ALIAS_RESOURCE).read_text(encoding="utf-8"))
    return {entry["term"]: tuple(entry["aliases"]) for entry in data["terms"]}


def aliases_for(term: str) -> tuple[str, ...]:
    """The spellings that count as ``term``: the term itself first, then its table aliases (none for an unknown term)."""
    table = alias_table()
    key = next((name for name in table if name.casefold() == term.casefold()), None)
    return (term, *(table[key] if key else ()))


def term_regex(spelling: str) -> re.Pattern[str]:
    """Word-boundary pattern for one spelling.  Two characters or fewer ("Go", "R", "C") match case-sensitively."""
    body = r"\s+".join(re.escape(part) for part in spelling.split())
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", 0 if len(spelling.strip()) <= 2 else re.IGNORECASE)


def mentions(text: str, term: str) -> bool:
    """True when ``text`` names ``term`` under any of its spellings."""
    return any(term_regex(spelling).search(text) for spelling in aliases_for(term))


def skills_from_markdown(markdown: str) -> tuple[str, ...]:
    """The resume's Skills tags (the chips the PDF prints); empty when the markdown has no readable Skills section."""
    from gigai.scout.resume_pdf import ResumeMarkdownError, parse_resume_markdown

    try:
        _name, sections = parse_resume_markdown(markdown)
    except ResumeMarkdownError:
        return ()
    return tuple(str(tag) for section in sections for tag in section["tags"])  # type: ignore[attr-defined]


def _canonical(tag: str) -> str:
    """The table's name for a resume tag (``K8s`` -> ``Kubernetes``), or the tag as written."""
    for name, aliases in alias_table().items():
        if tag.casefold() == name.casefold() or tag.casefold() in (a.casefold() for a in aliases):
            return name
    return tag.strip()


def title_words(title: str) -> tuple[str, ...]:
    seen: list[str] = []
    for word in _WORD.findall(title.casefold()):
        if len(word) > 2 and word not in _TITLE_STOP and word not in seen:
            seen.append(word)
    return tuple(seen)


def extract_keywords(posting_text: str, *, title: str = "", skills: Iterable[str] = ()) -> PostingKeywords:
    """Must / nice / title keywords of one posting (``skills``: the resume's Skills tags, extra vocabulary)."""
    vocabulary = list(alias_table())
    known = {name.casefold() for name in vocabulary}
    for tag in skills:
        name = _canonical(tag)
        if name and name.casefold() not in known:
            known.add(name.casefold())
            vocabulary.append(name)
    patterns = {name: [term_regex(s) for s in aliases_for(name)] for name in vocabulary}

    first_seen: dict[str, int] = {}
    cue: dict[str, str] = {}
    mode = ""
    position = 0
    for line in posting_text.splitlines():
        stripped = line.strip()
        heading = bool(stripped) and len(stripped) <= 60 and stripped.endswith(":")
        if heading:
            mode = "nice" if _NICE_CUE.search(stripped) else ("must" if _MUST_CUE.search(stripped) else "")
        for sentence in _SENTENCES.split(line):
            nice, must = _NICE_CUE.search(sentence), _MUST_CUE.search(sentence)
            kind = "nice" if nice and not must else ("must" if must and not nice else (mode if not (nice and must) else "nice"))
            for name, regexes in patterns.items():
                if any(rx.search(sentence) for rx in regexes):
                    first_seen.setdefault(name, position)
                    position += 1
                    if cue.get(name) != "must":  # one required mention outranks any number of nice ones
                        cue[name] = kind or "must"
    ordered = sorted(first_seen, key=first_seen.__getitem__)
    return PostingKeywords(
        must=tuple(n for n in ordered if cue[n] == "must"),
        nice=tuple(n for n in ordered if cue[n] == "nice"),
        title=title_words(title),
    )
