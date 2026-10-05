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
from typing import Callable, Iterator, Sequence

# --- patterns found in linear time ---------------------------------------------------
#
# A pattern such as ``[\w.+-]+@...`` tried from every character of a long run
# reads the rest of the run each time: one 20,000-character line cost 4.5 s and
# 100,000 characters 111 s. ``_Linear`` finds the same matches without that.
#
# Each branch is one alternative of the pattern, in the pattern's own order,
# with the places it can start: ``starts`` finds them in one pass over the text
# (a run is read once, from its first character), and ``resume``, when a branch
# has one, gives the first place at or after the end of the match before. The
# alternative itself is then matched only at those places, by the unchanged
# expression. For every branch: where the alternative matches at some place, it
# matches at one of its listed places no later than that, so the leftmost match
# and which alternative makes it are what ``re`` would find.
# ``tests/behaviors/scout_find_jobs/test_resume_privacy_linear.py`` keeps the
# earlier patterns and compares.

_Resume = Callable[[str, int], "int | None"]


def _place(found: "re.Match[str] | None") -> int | None:
    """Where a ``starts`` match says its alternative can begin: its last group when it has one."""

    if found is None:
        return None
    return found.start(found.lastindex) if found.lastindex else found.start()


def _resume_at(expression: str, flags: int = 0) -> Callable[[], _Resume]:
    pattern = re.compile(expression, flags)
    return lambda: lambda text, position: _place(pattern.match(text, position))


class _Linear:
    """A pattern's ``finditer`` / ``search`` / ``sub`` in time linear in the text (see above)."""

    def __init__(self, *branches: tuple[str, str, "Callable[[], _Resume] | None"], flags: int = 0) -> None:
        self._branches = tuple((re.compile(match, flags), re.compile(starts, flags), resume) for match, starts, resume in branches)

    def finditer(self, text: str) -> Iterator["re.Match[str]"]:
        streams = [starts.finditer(text) for _match, starts, _resume in self._branches]
        heads = [_place(next(stream, None)) for stream in streams]
        resumes = [resume() if resume is not None else None for _match, _starts, resume in self._branches]
        position = 0
        while True:
            extra: list[int | None] = [None] * len(streams)
            for index, stream in enumerate(streams):
                head = heads[index]
                while head is not None and head < position:
                    head = _place(next(stream, None))
                heads[index] = head
                resume = resumes[index]
                if position and resume is not None:
                    extra[index] = resume(text, position)
            found = None
            while found is None:
                place = min((at for at in (*heads, *extra) if at is not None), default=None)
                if place is None:
                    return
                for index, (match, _starts, _resume) in enumerate(self._branches):
                    if heads[index] == place:
                        heads[index] = _place(next(streams[index], None))
                    elif extra[index] != place:
                        continue
                    if extra[index] == place:
                        extra[index] = None
                    if found is None:
                        found = match.match(text, place)
            yield found
            position = max(found.end(), found.start() + 1)

    def search(self, text: str) -> "re.Match[str] | None":
        return next(self.finditer(text), None)

    def sub(self, replacement: str, text: str) -> str:
        """``text`` with every match replaced by ``replacement``, taken literally."""

        out: list[str] = []
        position = 0
        for found in self.finditer(text):
            out.append(text[position:found.start()])
            out.append(replacement)
            position = found.end()
        if not out:
            return text
        out.append(text[position:])
        return "".join(out)


def _scheme_resume() -> _Resume:
    """Where a ``scheme://`` link can begin at or after a place inside a run of scheme characters.

    The run is read once however many matches end inside it: whether
    ``://`` and a character follow it is kept for the run.
    """

    held = [0, -1, False]  # the run was read from here, to here; whether a link's "://x" follows it

    def resume(text: str, position: int) -> int | None:
        if not held[0] <= position <= held[1]:
            end = _SCHEME_RUN.match(text, position).end()
            held[:] = [position, end, _SCHEME_FOLLOWS.match(text, end) is not None]
        if not held[2]:
            return None
        return _place(_SCHEME_LETTER.match(text, position, held[1]))

    return resume


_SCHEME_RUN = re.compile(r"[a-z0-9+.-]*", re.I)
_SCHEME_FOLLOWS = re.compile(r"://\S")
_SCHEME_LETTER = re.compile(r"[0-9+.-]*([a-z])", re.I)

#: The first word character of a chain of dotted labels (``[\w-]+`` joined by
#: single dots): of a run of ``[\w.-]``, and after each ``..`` inside one.
_CHAIN_STARTS = r"(?<![\w.-])(?=[.-]*(\w))|(?<=\.\.)(?=(?:-+\.)*-*(\w))"
#: A run of address characters that a ``@`` follows, from its first character
#: (``_ADDRESS_RESUME``: or from where the match before ended).
_ADDRESS_STARTS = r"(?<![\w.+-])(?=[\w.+-]+@)"
_ADDRESS_RESUME = _resume_at(r"(?=[\w.+-]+@)")

# --- contact patterns (shared with the rank digest) ---------------------------------

_ADDRESS = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_EMAIL = _Linear(
    (_ADDRESS, _ADDRESS_STARTS, _ADDRESS_RESUME),
    (r"(?<!\w)@[\w.]{2,}", r"@", None),
)
_URL = _Linear(
    (r"(?:https?://|www\.)\S+", r"https?://|www\.", None),
    (r"\b(?:[\w-]+\.)+(?:com|net|org|io|dev|me|ai|app|co|us|ca|uk|in|xyz|tech)\b(?:/\S*)?", _CHAIN_STARTS, None),
    flags=re.I,
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
#: ``City, ST`` begins at the first capital on a word boundary in a run of ``[A-Za-z.\s]`` that a comma ends.
_CITY_START = r"(?=[A-Za-z.\s]+,)(?=[A-Za-z.\s]*?\b([A-Z]))"
_ZIP_LINE = _Linear(
    (r"\b[A-Z][A-Za-z.\s]+,\s*[A-Z]{2}\b(?:\s+\d{5}(?:-\d{4})?)?", r"(?<![A-Za-z.\s])" + _CITY_START, _resume_at(_CITY_START)),
    (r"\b\d{5}(?:-\d{4})?\b", r"\b(?=\d{5})", None),
)

#: Body redaction only: a real email address (never a bare ``@handle``, so
#: ``Engineer @Stripe`` survives) and a narrow link pattern (see the module
#: docstring).
_BODY_EMAIL = _Linear((_ADDRESS, _ADDRESS_STARTS, _ADDRESS_RESUME))
_LINK_HOSTS = r"(?:linkedin\.com|github\.com|gitlab\.com|bit\.ly|lnkd\.in|t\.co|tinyurl\.com|goo\.gl)\b"
# ``(?:scheme://|www\.)\S+`` and ``\b(?:[\w-]+\.)*host\b(?:/\S*)?``, each as two branches: the
# host with labels before it begins at its chain's first word character, the host alone where it stands.
_BODY_URL = _Linear(
    (r"[a-z][a-z0-9+.-]*://\S+", r"(?<![a-z0-9+.-])(?=[0-9+.-]*([a-z])[a-z0-9+.-]*://)", _scheme_resume),
    (r"www\.\S+", r"www\.", None),
    (r"\b(?:[\w-]+\.)*" + _LINK_HOSTS + r"(?:/\S*)?", _CHAIN_STARTS, None),
    (r"\b" + _LINK_HOSTS + r"(?:/\S*)?", r"\b" + _LINK_HOSTS, None),
    flags=re.I,
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
_LEADING_MARKERS = re.compile(r"[#>*_\s]*")
_TRAILING_MARKERS = re.compile(r"[*_\s]*")


def _without_markers(text: str) -> str:
    """``text`` less its leading ``[#>*_\\s]`` and then its trailing ``[*_\\s]``, each run read once."""

    text = text[_LEADING_MARKERS.match(text).end():]
    return text[: len(text) - _TRAILING_MARKERS.match(text[::-1]).end()]


def _unmarked(line: str) -> str:
    return _without_markers(line.strip())


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


#: Line 1 as ``<name> <separator> <headline>`` (``# Priya Natarajan — Staff ML Engineer``, ``Jane Doe | Engineer``):
#: markers, then the shortest name that a separator follows, then the rest. The first separator in the
#: line is the only one a name can end at (no name holds ``|—–·•,``).
_NAME_SEPARATOR = re.compile(r"\s[—–-]\s|[|·•,]")


def _name_then_headline(line: str) -> tuple[str, str] | None:
    """``(name, headline)`` when line 1 opens with a strictly name-shaped segment followed by more text."""

    line = line.strip()
    if "\n" in line:
        return None
    prefix = line[: _LEADING_MARKERS.match(line).end()]
    after = line[len(prefix):]
    separator = _NAME_SEPARATOR.search(after)
    if separator is None:
        return None
    name = after[: separator.start()]
    rest = after[separator.end():].lstrip()
    if not rest or "—" in name or "–" in name or not is_name_line(name):
        return None
    hashes = re.match(r"#+", prefix.strip())
    headline = _without_markers(rest) or rest
    return _unmarked(name), (hashes.group(0) + " " if hashes else "") + headline


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


# --- a link in a heading (0.1.10.11) ---------------------------------------------------
#
# The strip above takes a link out of a line it keeps and leaves what stood around it:
# ``### [Driftwatch](https://github.com/x/driftwatch)`` became ``### [Driftwatch](``, and
# where a flagged line is not imported at all (the master) the project lost its name and
# its bullets went to the entry above. ``heading_links`` runs BEFORE the strip, on the
# lines that name an entry, and takes only the link: the words stay. What it leaves goes
# through the unchanged strip, so an email, a phone number or the name's words in a
# heading are handled exactly as they were, and no link is stored either way.

#: A heading's words are said back at most this long.
HEADING_WORDS_SHOWN = 80
#: At most this many plain lines right under a heading are its role lines (``tailored_resume.MAX_HEADING_LINES`` less the heading).
_ROLE_LINES = 3
#: A longer line is not a heading: it is left to the strip as it is.
_TITLE_MAX_CHARS = 2000
_TITLE_HEADING = re.compile(r"#{2,6}[ \t]+(?=\S)")
_TITLE_BULLET = re.compile(r"(?:[-*+•▪▫◦‣⁃○●■□◆◇►▸➢➤✓✔→–—·>]|\d{1,2}[.)])\s")
#: A line that opens in bold or italics (``**Driftwatch** — a drift detector``, ``*Staff Engineer* | 2021``, ``[**Driftwatch**](...)``).
_TITLE_EMPHASIS = re.compile(r"\[?(\*\*|__|\*|_)(?=[^\s*_])")
_CONTACT_SECTION = re.compile(r"contact(?:\s+(?:info|information|details))?|references", re.I)
#: ``[Words](target)`` and ``![alt](target)``: the words hold no bracket, the target no space or bracket; a quoted title may follow it.
_MD_LINK = re.compile(r"(!?)\[([^\[\]]*)\]\(\s*<?([^()\s<>]*)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
#: What an import before 0.1.10.11 left of a link in a line it kept: the words, and a bracket where the address was.
_MD_LINK_LEFT = re.compile(r"\[([^\[\]]*)\]\((?:\s*\))?(?=[\s)|,;:.]|\Z)")
#: A link whose words are contact data like its address: a mail or phone link, a person's profile page.
_CONTACT_TARGET = re.compile(r"(?:mailto|tel|sms):|(?:[a-z][a-z0-9+.-]*://)?(?:[\w-]+\.)*(?:linkedin\.com/(?:in|pub)/|lnkd\.in/)", re.I)
_TARGET_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.I)
#: The closers and the punctuation that follow an address in a sentence or in markup are not part of it.
_AFTER_URL = ")]}>*_.,;:!?'\""


class HeadingOnlyLink(ValueError):
    """An entry heading that holds nothing but a link: there is no name to keep. ``line`` is its 1-based file line.

    The message names the line and never holds its text."""

    def __init__(self, line: int) -> None:
        super().__init__(f"line {line}: this heading is only a link: give the project a name")
        self.line = line


@dataclass(frozen=True)
class HeadingLink:
    """A link ``heading_links`` took out of a line it kept (never the address)."""

    #: The 1-based file line the link stood on.
    line: int
    #: The file line of the entry's heading: ``line`` itself, or the heading the line stands under.
    head: int

    @property
    def under(self) -> bool:
        """The link stood on a line under the heading (a role line), not in the heading."""

        return self.head != self.line


def heading_words(line: str) -> str:
    """The words of a heading line as they are said back: no ``#``, no emphasis marks, no trailing comment."""

    core = _split_title(line.strip())[1]
    words = re.sub(r"\*+|(?<![^\W_])_+|_+(?![^\W_])", "", core)
    return " ".join(words.split())[:HEADING_WORDS_SHOWN].rstrip()


def heading_link_message(words: str, under: bool = False) -> str:
    """What a report says about one ``HeadingLink``: the heading's words, never the address."""

    where = "a line under the heading" if under else "the heading"
    return f"link removed from {where} {words}" if words else f"link removed from {'a line under a heading' if under else 'a heading'}"


def _split_title(line: str) -> tuple[str, str, str]:
    """``(the '#' marks, the text, the trailing comments)`` of a stripped line."""

    end = len(line)
    while line[:end].rstrip().endswith("-->"):
        start = line.rfind("<!--", 0, end)
        if start < 0:
            break
        end = start
    body = line[:end]
    marks = _TITLE_HEADING.match(body)
    lead = marks.end() if marks else 0
    return body[:lead], body[lead:].rstrip(), line[len(body.rstrip()):]


def _is_label_line(line: str, mark: str) -> bool:
    """``**Languages:** Go`` and ``**Languages**: Go``: a labelled line, not a title."""

    close = line.find(mark, len(mark) + (1 if line.startswith("[") else 0))
    if close < 0:
        return True  # an emphasis mark that is never closed: not a title either
    return line[:close].rstrip().endswith(":") or line[close + len(mark):].lstrip()[:1] == ":"


def _link_address(target: str) -> tuple[str, list[str]]:
    """``(host, path segments)`` of a link's target, lower case, without scheme, ``www.``, query or fragment."""

    scheme = _TARGET_SCHEME.match(target)
    rest = target[scheme.end():] if scheme else target
    rest = re.split(r"[?#]", rest, maxsplit=1)[0].casefold()
    host, _slash, path = rest.partition("/")
    return host.removeprefix("www."), [segment for segment in path.split("/") if segment]


def _kept_words(found: "re.Match[str]") -> str:
    """What stays of one markdown link: its words, or nothing when the words are the link itself.

    Nothing stays of an image, of a mail or phone link, of a link to a person's profile page, of a link
    whose words are its own address, and of a handle (``[jordan-example](github.com/jordan-example)``:
    the words are the one path segment)."""

    image, words, target = found.group(1), found.group(2), found.group(3)
    if image or _CONTACT_TARGET.match(target):
        return ""
    said = re.sub(r"[*_`\s]+", "", words).casefold().lstrip("@")
    host, path = _link_address(target)
    said_host, said_path = _link_address(said)
    if (said_host, said_path) == (host, path) or (len(path) == 1 and said == path[0]):
        return ""
    return words


def _without_links(core: str) -> tuple[str, list[str]]:
    """``core`` without its links: a markdown link keeps its words, a bare address goes. ``(text, the addresses that went)``."""

    text = core
    addresses: list[str] = []

    def kept(found: "re.Match[str]") -> str:
        addresses.append(found.group(3))
        return _kept_words(found)

    for _ in range(3):  # a link around an image is found once the image went
        text, count = _MD_LINK.subn(kept, text)
        if not count:
            break
    parts: list[str] = []
    position = 0
    for found in _BODY_URL.finditer(text):
        end = found.end()
        opened = "(" in found.group(0)
        while end > found.start() and text[end - 1] in _AFTER_URL and not (text[end - 1] == ")" and opened):
            end -= 1
        if end > found.start():
            parts.append(text[position:found.start()])
            addresses.append(text[found.start():end])
            position = end
    if parts:
        text = "".join(parts) + text[position:]
    text = _MD_LINK_LEFT.sub(lambda found: found.group(1), text)
    if text == core:
        return core, []
    # Tidy what stood around a link: empty brackets, two separators in a row, a separator the line now ends with.
    text = re.sub(r"[ \t]+", " ", text)
    for _ in range(2):
        text = re.sub(r"\( ?\)|\[ ?\]|< ?>|(\*\*|__) ?\1", "", text)
    text = re.sub(r" ?([|·•])(?: ?[|·•])+ ?", r" \1 ", text)
    text = re.sub(r"\( ", "(", re.sub(r" ([),;])", r"\1", re.sub(r" {2,}", " ", text)))
    closing = len(text) - len(re.sub(r"(?:\*\*|__|\*|_)\Z", "", text))
    text = text[: len(text) - closing].rstrip(" |·•,;:([–—-") + text[len(text) - closing:]
    return re.sub(r"\A((?:\*\*|__|\*|_)?)[ |·•,;:–—]+", r"\1", text).strip(), addresses


def _only_a_link(marks: str, core: str, went: bool) -> bool:
    """A link stood on the line and nothing but a link is left: no letter and no digit, or a line the strip takes whole for its link alone.

    ``went``: the rule took a link out. A dotted name alone (``driftwatch.ai``) is the other case: the strip
    takes such a line whole, so there is no name to keep either. A line with no link at all is never this."""

    line = marks + core
    if not re.search(r"[^\W_]", core):
        return went
    if not (went or _URL.search(line)):
        return False
    return _is_contact_only(line) and not any(pattern.search(line) for pattern in (_EMAIL, _PHONE, _STREET, _PO_BOX, _ZIP_LINE))


def heading_links(text: str) -> tuple[str, tuple[HeadingLink, ...]]:
    """``text`` with the links taken out of its headings, the words kept, and where they stood.

    THE rule for a link in a line that names an entry, for every resume import (``resume add``,
    ``master init`` from the profiles' resumes or from a file, ``master sync``):

    * WHERE: from the first section heading on (``Summary``, ``## Experience``, ...; the name and
      contact lines above it are the strip's, as they were), outside a Contact or References
      section, and only in a TITLE line: a ``##`` to ``######`` heading, a line that opens in bold
      or italics (not ``**Label:** value``), and up to ``_ROLE_LINES`` plain lines right under
      one of those (its role lines). Never a bullet, never a paragraph.
    * WHAT: ``[Words](address)`` keeps ``Words``; an address written out (``scheme://``,
      ``www.``, the known hosts of ``_LINK_HOSTS``) goes and the rest of the line stays. Any other
      dotted name (``driftwatch.ai``) is not touched here: it is a project's name as often as a
      link. The words of a mail or phone link, of a person's profile page and a handle go with
      the link (``_kept_words``).
    * NOTHING LEFT: a ``#`` heading, or a bold title right above bullets, that is only a link
      raises ``HeadingOnlyLink`` (its bullets would have no entry): no word is left, or only what
      the strip takes whole (a dotted name alone, ``### driftwatch.ai``; a contact word alone,
      ``### [GitHub](...)``). A role line that is only a link is the strip's: it goes whole, as
      before, and is counted by the strip as a link.

    Line numbers are kept (a line is rewritten in place). Text with no such link comes back as it is.
    """

    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if _is_resume_section(line)), None)
    if start is None:
        return text, ()
    out = list(lines)
    found: list[HeadingLink] = []
    head: int | None = None  # the heading the line above belongs to, while role lines may follow
    below = 0
    contact_section = False

    def heads_bullets(index: int) -> bool:
        later = next((lines[at].strip() for at in range(index + 1, len(lines)) if lines[at].strip()), "")
        return _TITLE_BULLET.match(later) is not None

    for index in range(start, len(lines)):
        line = lines[index].strip()
        if _is_resume_section(line):
            contact_section, head = _CONTACT_SECTION.fullmatch(line.strip("#*_-:= ").strip()) is not None, None
            continue
        if not line or contact_section or len(line) > _TITLE_MAX_CHARS or _TITLE_BULLET.match(line):
            head = None
            continue
        emphasis = _TITLE_EMPHASIS.match(line)
        if _TITLE_HEADING.match(line):
            kind = "heading"
        elif emphasis and not _is_label_line(line, emphasis.group(1)):
            kind = "role" if head is not None else "title"
        elif head is not None and below < _ROLE_LINES and not line.startswith("#"):
            kind = "role"
        else:
            head = None
            continue
        marks, core, comments = _split_title(line)
        rewritten, addresses = _without_links(core)
        if _only_a_link(marks, rewritten, bool(addresses)):
            if kind == "heading" or (kind == "title" and heads_bullets(index)):
                raise HeadingOnlyLink(index + 1)
            alone = " ".join(addresses)
            if addresses and _is_contact_only(alone):
                # A role line that is only a link: the strip gets the address alone and takes the line whole, as it
                # takes a line that is only an address (nothing of the link's own words is left behind).
                out[index] = alone
            below += kind == "role"
            continue
        if kind == "role":
            below += 1
        else:
            head, below = index, 0
        if addresses or rewritten != core:
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            out[index] = f"{indent}{marks}{rewritten}{comments}"
            if addresses:
                found.append(HeadingLink(index + 1, (head if head is not None else index) + 1))
    if out == lines:
        return text, ()
    ends = [kept[len(line):] for kept, line in zip(text.splitlines(keepends=True), lines)]
    return "".join(line + end for line, end in zip(out, ends)), tuple(found)


__all__ = [
    "HEADER_MAX_LINES",
    "HEADING_WORDS_SHOWN",
    "HeadingLink",
    "HeadingOnlyLink",
    "ModelResume",
    "guard_name",
    "guard_private",
    "heading_link_message",
    "heading_links",
    "heading_words",
    "is_name_line",
    "model_resume",
    "redact_inline",
    "split_resume_header",
]
