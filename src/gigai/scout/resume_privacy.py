"""The one privacy boundary for resume text that leaves the machine (0110-003 P1).

Every model-bound prompt builder that carries resume text goes through here:
the assess prompt (assess node + quick assess), tailor, resume extract and
prep categories call ``model_resume``; the rank digest uses the shared
header/guard helpers below (``split_resume_header``, ``guard_private``,
``guard_name``), which moved here from ``find_jobs/rank_digest.py`` (it
re-exports them). Readers cannot strip: every reader re-hashes the raw
bytes for pinning, so the strip happens where the text leaves.

``model_resume`` (the full-text flows):

- The HEADER CANDIDATE BLOCK is the leading non-blank lines before the first
  section heading (``Summary``, ``## Experience``, ...), or before the first
  blank line when the resume has no heading; at most ``HEADER_MAX_LINES``
  lines, and a long non-contact sentence ends it (it is body text).
- A candidate line is WITHHELD when it is contact-like (email, phone, URL,
  street, PO box, city/state/zip, a work-authorization line) or when it is
  line 1 and strictly name-shaped (2-4 capitalised or all-caps words,
  letters and ``.'-`` only, no title words). The resume's own headline line
  is kept: it is professional content, not contact data.
- In the BODY a line made only of contact segments is withheld; every other
  line keeps its text with emails, phone numbers and links redacted. The
  link redaction is narrow (``scheme://``, ``www.``, ``linkedin.com``,
  ``github.com``, link shorteners) so tech names like ``socket.io`` survive.
  Job locations in the body stay.
- The withheld name line's words are removed from every kept line (whole
  word, case-insensitive).

Numbering matches ``tailored_resume.resume_lines`` (non-blank lines,
stripped, 1-based), so a tailor prompt can show kept lines under their
original ``R<n>`` numbers and reject the withheld ones.

Pure code: no model call, no network, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

# --- contact patterns (shared with the rank digest) ---------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<!\w)@[\w.]{2,}")
_URL = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b(?:[\w-]+\.)+(?:com|net|org|io|dev|me|ai|app|co|us|ca|uk|in|xyz|tech)\b(?:/\S*)?",
    re.I,
)
_PHONE = re.compile(
    r"(?<![\w.])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
    r"|(?<![\w.])\+\d[\d\s().-]{7,}\d",
)
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[\w.]+\s+){0,4}(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|"
    r"place|pl|parkway|pkwy|suite|apt)\b\.?",
    re.I,
)
_PO_BOX = re.compile(r"\bp\.?\s?o\.?\s?box\s+\d+", re.I)
_ZIP_LINE = re.compile(r"\b[A-Z][A-Za-z.\s]+,\s*[A-Z]{2}\b(?:\s+\d{5}(?:-\d{4})?)?|\b\d{5}(?:-\d{4})?\b")

#: Body redaction only: a real email address (never a bare ``@handle``, so
#: ``Engineer @Stripe`` survives) and a narrow link pattern (see the module
#: docstring).
_BODY_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BODY_URL = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+"
    r"|\b(?:[\w-]+\.)*(?:linkedin\.com|github\.com|gitlab\.com|bit\.ly|lnkd\.in|t\.co|tinyurl\.com|goo\.gl)\b(?:/\S*)?",
    re.I,
)
#: A header line that states work authorization (``VISA: H1B``, ``US citizen``).
_WORK_AUTH = re.compile(
    r"\b(?:visa|h-?1b|green\s+card|citizen(?:ship)?|work\s+authori[sz]ation|authori[sz]ed\s+to\s+work|sponsorship)\b",
    re.I,
)
#: Labels that may precede a contact value (``Email: ...``, ``LinkedIn - ...``).
_CONTACT_LABEL = re.compile(
    r"\b(?:e-?mail|phone|tel|telephone|mobile|cell|linkedin|github|gitlab|web(?:site)?|portfolio|blog|address|"
    r"contact|reach\s+me(?:\s+at)?|or)\b",
    re.I,
)
_SEGMENT_SPLIT = re.compile(r"\s*(?:[|•·]|\s[–—-]\s|\t|\s{2,})\s*")


def _is_clean(text: str) -> bool:
    return not any(p.search(text) for p in (_EMAIL, _URL, _PHONE, _STREET, _PO_BOX))


def _is_contact_line(line: str) -> bool:
    return not _is_clean(line) or bool(_ZIP_LINE.search(line))


def guard_private(text: str) -> str:
    """Strip emails, phones, URLs/handles and drop street-address lines from digest text."""

    out: list[str] = []
    for line in text.split("\n"):
        if _STREET.search(line) or _PO_BOX.search(line):
            continue
        for pattern in (_EMAIL, _URL, _PHONE):
            line = pattern.sub("", line)
        out.append(line)
    return "\n".join(out)


# --- header structure (shared with the rank digest) ---------------------------------

_RESUME_SECTION = re.compile(
    r"^(?:professional\s+|career\s+|executive\s+|technical\s+|work\s+)?"
    r"(?:summary|profile|objective|about(?:\s+me)?|experience|employment(?:\s+history)?|work\s+history|"
    r"skills|technical\s+skills|core\s+competencies|education|projects|certifications?|awards|publications|"
    r"languages|interests|references|contact|contact\s+(?:info|information|details))$",
    re.I,
)
_HEADER_MAX_WORDS = 12
_NAME_STOP = frozenset({
    "senior", "sr", "junior", "jr", "staff", "principal", "lead", "head", "chief", "associate", "intern", "software",
    "backend", "back", "end", "frontend", "front", "full", "stack", "fullstack", "data", "machine", "learning", "ml",
    "platform", "cloud", "site", "reliability", "devops", "mobile", "web", "systems", "security", "and", "of", "the",
    "engineer", "developer", "architect", "scientist", "analyst", "manager", "director", "consultant", "administrator",
    "designer", "programmer", "sre", "researcher", "specialist", "resume", "cv", "curriculum", "vitae",
})


def _is_resume_section(line: str) -> bool:
    return bool(_RESUME_SECTION.match(line.strip().strip("#*_-:= ").strip()))


def split_resume_header(resume_text: str) -> tuple[list[str], list[str]]:
    """``(header_lines, body_lines)``: the name/contact block dropped by structure (uat-bug-032).

    The RANK digest's rule. With a section heading (Summary, Experience,
    Skills, ...) the header is the leading block above the first heading, up
    to the first blank line, and only its short or contact-like lines (a long
    sentence is body text). With no heading the header is the first block (up
    to the first blank line). Contact-like lines (email, phone, URL, street,
    city/state/zip) are dropped from the body either way.
    """

    lines = resume_text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    heading = next((i for i, line in enumerate(lines) if _is_resume_section(line)), None)
    end = first
    while end < len(lines) and lines[end].strip() and (heading is None or end < heading):
        line = lines[end]
        if heading is not None and not _is_contact_line(line) and len(line.split()) > _HEADER_MAX_WORDS:
            break
        end += 1
    header, body = lines[first:end], lines[end:]
    return header, [line for line in body if not _is_contact_line(line)]


def _name_tokens(header: Sequence[str], name: str | None) -> set[str]:
    """The candidate's own name words: ``name`` when given, else the header's first line minus title vocabulary."""

    if name is not None and name.strip():
        words = re.findall(r"[^\W\d_]+", name)
    else:
        first = header[0] if header else ""
        if _is_contact_line(first) or any(c.isdigit() for c in first):
            return set()
        words = [w for w in re.findall(r"[^\W\d_]+", first) if w.lower() not in _NAME_STOP]
    return {w.lower() for w in words if len(w) > 1}


def guard_name(text: str, tokens: set[str]) -> str:
    """Remove each name token (case-insensitive, whole word) from ``text``."""

    if not tokens:
        return text
    pattern = re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r")(?![^\W_])", re.I)
    return "\n".join(re.sub(r"[ \t]{2,}", " ", pattern.sub("", line)) for line in text.split("\n"))


# --- the full-text strip ---------------------------------------------------------------

#: At most this many lines form the header candidate block.
HEADER_MAX_LINES = 8
_MARKDOWN_MARKERS = re.compile(r"\A[#>*_\s]+|[*_\s]+\Z")


def _unmarked(line: str) -> str:
    return _MARKDOWN_MARKERS.sub("", line.strip())


def is_name_line(line: str) -> bool:
    """Strictly name-shaped: 2-4 capitalised or all-caps words, letters and ``.'-`` only, no title words."""

    text = _unmarked(line)
    words = text.split()
    if not 2 <= len(words) <= 4 or text[-1:] in {",", ";", ":", "!", "?"}:
        return False
    return all(_is_name_word(word) for word in words) and any(len(word.strip(".'’-")) > 1 for word in words)


def _is_name_word(word: str) -> bool:
    """``Jane``, ``O'Neil``, ``McDonald``, ``Q.``, ``JOSÉ``; never ``GraphQL``, ``iOS``, digits or a title word."""

    letters = word.strip(".'’-")
    if not letters or not letters[0].isupper() or letters.lower() in _NAME_STOP:
        return False
    if any(not (char.isalpha() or char in ".'’-") for char in word):
        return False
    if letters.isupper():
        return True
    caps = [index for index, char in enumerate(letters) if char.isupper()]
    return len(caps) <= 2 and all(b - a > 1 for a, b in zip(caps, caps[1:]))


#: Line 1 as ``<name> <separator> <headline>`` (``# Priya Natarajan — Staff ML Engineer``, ``Jane Doe | Engineer``).
_NAME_THEN_REST = re.compile(r"\A(?P<prefix>[#>*_\s]*)(?P<name>[^|—–·•,]+?)\s*(?:\s[—–-]\s|[|·•,])\s*(?P<rest>\S.*)\Z")


def _name_then_headline(line: str) -> tuple[str, str] | None:
    """``(name, headline)`` when line 1 opens with a strictly name-shaped segment followed by more text."""

    match = _NAME_THEN_REST.match(line.strip())
    if match is None or not is_name_line(match.group("name")):
        return None
    hashes = re.match(r"#+", match.group("prefix").strip())
    headline = _MARKDOWN_MARKERS.sub("", match.group("rest")) or match.group("rest")
    return _unmarked(match.group("name")), (hashes.group(0) + " " if hashes else "") + headline


def _name_tokens_of(name: str) -> set[str]:
    return {word.strip(".'’-").lower() for word in _unmarked(name).split() if len(word.strip(".'’-")) > 1}


def _is_heading_boundary(line: str) -> bool:
    return _is_resume_section(line) or re.match(r"#{2,}\s", line.strip()) is not None


def _is_contact_segment(segment: str) -> bool:
    """A segment that is nothing but a contact value (and maybe its label)."""

    rest = segment
    for pattern in (_EMAIL, _URL, _BODY_URL, _PHONE, _STREET, _PO_BOX, _ZIP_LINE):
        rest = pattern.sub(" ", rest)
    rest = _CONTACT_LABEL.sub(" ", rest)
    return rest != segment and not re.search(r"[^\W_]", rest)


def _is_contact_only(line: str) -> bool:
    segments = [segment for segment in _SEGMENT_SPLIT.split(_unmarked(line)) if segment.strip()]
    return bool(segments) and all(_is_contact_segment(segment) for segment in segments)


def _is_header_contact(line: str) -> bool:
    return _is_contact_line(line) or _WORK_AUTH.search(line) is not None


def redact_inline(line: str) -> str:
    """``line`` with emails, phone numbers and links removed (the narrow body rule)."""

    out = line
    for pattern in (_BODY_EMAIL, _BODY_URL, _PHONE):
        out = pattern.sub("", out)
    if out == line:
        return line
    return re.sub(r"[ \t]{2,}", " ", out).strip()


@dataclass(frozen=True)
class ModelResume:
    """What a model may see of one resume (see the module docstring)."""

    #: Header/contact lines removed, inline contact redacted, name tokens removed.
    text: str
    #: ``(original resume line number, redacted text)`` for every kept line.
    lines: tuple[tuple[int, str], ...]
    #: Original resume line numbers never shown to a model.
    withheld: frozenset[int]


def _header_block(numbered: Sequence[str], blank_after: set[int]) -> list[int]:
    """The header candidate block as 1-based numbers (see the module docstring)."""

    heading = next((n for n, line in enumerate(numbered, 1) if _is_heading_boundary(line)), None)
    block: list[int] = []
    for number, line in enumerate(numbered, 1):
        if heading is not None and number >= heading:
            break
        if len(block) >= HEADER_MAX_LINES:
            break
        if not _is_header_contact(line) and len(line.split()) > _HEADER_MAX_WORDS:
            break
        block.append(number)
        if heading is None and number in blank_after:
            break
    return block


def model_resume(resume_text: str) -> ModelResume:
    """The resume as a model may see it: the one strip every model-bound builder uses."""

    raw_lines = resume_text.splitlines()
    numbered: list[str] = []
    number_of_raw: dict[int, int] = {}
    blank_after: set[int] = set()
    for index, raw in enumerate(raw_lines):
        line = raw.strip()
        if line:
            numbered.append(line)
            number_of_raw[index] = len(numbered)
        elif numbered:
            blank_after.add(len(numbered))

    withheld: set[int] = set()
    tokens: set[str] = set()
    first_line: str | None = None
    for number in _header_block(numbered, blank_after):
        line = numbered[number - 1]
        if _is_header_contact(line):
            withheld.add(number)
        elif number == 1 and is_name_line(line):
            withheld.add(number)
            tokens = _name_tokens_of(line)
        elif number == 1 and (split := _name_then_headline(line)) is not None:
            # "Name — Headline": the name goes, the headline stays (Q1: the title is professional content).
            tokens = _name_tokens_of(split[0])
            first_line = split[1]
    for number, line in enumerate(numbered, 1):
        if number not in withheld and _is_contact_only(line):
            withheld.add(number)

    def shown(line: str) -> str:
        return guard_name(redact_inline(line), tokens) if tokens else redact_inline(line)

    kept: list[tuple[int, str]] = []
    out: list[str] = []
    for index, raw in enumerate(raw_lines):
        number = number_of_raw.get(index)
        if number is None:
            if out and out[-1] != "":
                out.append("")
            continue
        if number in withheld:
            continue
        text = shown(first_line if number == 1 and first_line is not None else raw.rstrip())
        if not text.strip():
            withheld.add(number)
            continue
        kept.append((number, text.strip()))
        out.append(text)
    while out and out[-1] == "":
        out.pop()
    unchanged = not withheld and first_line is None and all(text == numbered[number - 1] for number, text in kept)
    # Nothing to strip: the text goes out byte for byte as before (prompt goldens stay stable).
    text = resume_text if unchanged else "\n".join(out)
    return ModelResume(text=text, lines=tuple(kept), withheld=frozenset(withheld))


__all__ = [
    "HEADER_MAX_LINES",
    "ModelResume",
    "guard_name",
    "guard_private",
    "is_name_line",
    "model_resume",
    "redact_inline",
    "split_resume_header",
]
