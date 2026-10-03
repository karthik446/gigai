"""P3 (agent-security spike section 8): the outbound check on what the Scout API returns.

``redact_payload`` is called once, in ``api/server.py``'s ``_write_json``: every JSON
response passes through it before it leaves. It walks the payload's STRING values and
replaces anything contact-shaped with a fixed token (``TOKENS``), counting by kind
(counts only, never a value). The kinds are ``resume_pii``'s: ``email``, ``phone``,
``links`` (a linkedin.com profile, a github.com or gitlab.com path), ``address`` (a street
address). On top of ``resume_pii``'s shapes it reads the usual obfuscations: ``name at
domain dot com``, ``name [at] domain.com``, ``n a m e @ d o m a i n . c o m``, spaced or
bracketed digits, and lookalike characters (a fullwidth ``@``, digits of another script).

It is a backstop, not a guarantee: a spelled-out phone number, a name, or a zero-width
character inside an address pass.

NOT SCANNED, by structure (the key a string sits under):

- ids, URLs, digests, timestamps and versions (``_STRUCTURAL_KEY``), and everything under
  ``links``: a job URL, a posting identity or a ``sha256:...`` is never mangled;
- posting text (``_POSTING_TEXT_KEYS``, and ``text`` directly inside a posting-shaped
  object: one that holds ``normalized_url`` or ``job_url``). A posting is public, and its
  recruiter and office contact lines are what the user needs to apply.

A dict's keys are never scanned. Everything else is.

LINEAR TIME. No pattern here is tried against an unbounded run from more than one place:
the email shapes are anchored on the ``@`` (or the ``at`` word) and read a bounded window
on each side, and every other quantifier is bounded. ``tests/behaviors/scout_find_jobs/
test_outbound_check.py`` times 100,000- and 200,000-character pathological inputs.

Pure code: no model call, no network, no I/O, no logging.
"""

from __future__ import annotations

import re
from typing import Any
import unicodedata

#: What replaces a match, by kind (``resume_pii``'s kinds).
TOKENS: dict[str, str] = {
    "email": "[removed: email]",
    "phone": "[removed: phone]",
    "links": "[removed: link]",
    "address": "[removed: address]",
}
#: The kinds, in the order ``_redactions`` lists them.
KINDS: tuple[str, ...] = ("email", "phone", "links", "address")
#: The key a response carries when (and only when) something was redacted: ``{kind: count}``.
REDACTIONS_KEY = "_redactions"

# --- which strings are structural ------------------------------------------------------

_STRUCTURAL_KEY = re.compile(
    r"^(?:id|ids|url|urls|uri|href|path|paths|digest|sha256|etag|at|as_of|since|schema_version|version|method|"
    r"job_identity|identity|source|ref|refs|openapi|llms|self)$"
    r"|(?:_id|_ids|_url|_urls|_uri|_path|_paths|_digest|_sha256|_hash|_at|_ref|_refs|_version|_identity|_key)$"
)
#: Whole subtrees of action links: ``{method, path, body}`` built from ids and URLs.
_STRUCTURAL_SUBTREES = frozenset({"links"})
#: Posting text, wherever it sits.
_POSTING_TEXT_KEYS = frozenset({"posting_text", "job_text", "description", "job_description"})
#: An object holding one of these is a posting: its own ``text`` is the posting's text.
_POSTING_IDENTITY_KEYS = ("normalized_url", "job_url")


def _is_structural(key: str | None) -> bool:
    return key is not None and (key in _POSTING_TEXT_KEYS or _STRUCTURAL_KEY.search(key) is not None)


# --- lookalike characters ----------------------------------------------------------------

_HYPHENS = frozenset("‐‑‒–—―−﹣－")


class _Fold(dict):  # type: ignore[type-arg]
    """``str.translate`` table: each character to the ASCII one it stands for, one for one.

    Filled on demand, so a text costs one lookup per character and the result has the
    text's own length (a span found in the folded text is the same span in the text).
    """

    def __missing__(self, code: int) -> int:
        char = chr(code)
        value = code
        digit = unicodedata.decimal(char, None)
        if digit is not None:
            value = ord(str(digit))
        elif char in _HYPHENS:
            value = ord("-")
        elif char.isspace():
            value = code if char in "\n\r" else ord(" ")
        else:
            folded = unicodedata.normalize("NFKC", char)
            if len(folded) == 1 and folded.isascii():
                value = ord(folded)
        self[code] = value
        return value


_FOLD = _Fold()


def _folded(text: str) -> str:
    return text if text.isascii() else text.translate(_FOLD)


# --- links -------------------------------------------------------------------------------

#: Any ``scheme://`` or ``www.`` link: one token. A phone or address shape inside it is not one.
_ANY_URL = re.compile(r"(?<![a-z0-9+.-])[a-z][a-z0-9+.-]{0,15}://\S{1,2048}|(?<![\w.-])www\.\S{1,2048}", re.I)
#: The personal ones: a linkedin.com profile, anything under github.com or gitlab.com.
_PERSONAL_LINK = re.compile(
    r"(?<![\w@.-])(?:https?://)?(?:www\.|[a-z]{2}\.)?"
    r"(?:linkedin\.com/(?:in|pub)/|(?:github|gitlab)\.com/(?=[\w~]))\S{1,2048}",
    re.I,
)
_TRAILING = ".,;:!?)]}>'\""


def _trimmed_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _TRAILING:
        end -= 1
    return end


# --- email -------------------------------------------------------------------------------

_LOCAL_WINDOW = 200
_BRACKET_AT = r"[(\[{<]\s?at\s?[)\]}>]"
_AT_ANCHOR = re.compile(r"@|" + _BRACKET_AT + r"|(?<=\s)at(?=\s)", re.I)
_DOT_WORD = r"(?:\s{1,2}dot\s{1,2}|\s{0,2}[(\[{<]\s?dot\s?[)\]}>]\s{0,2})"
_DOT_ANY = r"(?:\.|\s{1,2}\.\s{1,2}|" + _DOT_WORD + r")"
_LABEL = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
_TLD = r"[^\W\d_]{2,24}"
_KNOWN_TLD = r"(?:com|net|org|io|dev|me|ai|app|co|us|ca|uk|in|de|fr|eu|au|nz|edu|gov|info|xyz|tech)"
#: ``domain.tld``, as written or with any dot spelled or spaced (``example dot com``, ``example . com``, ``example(dot)com``).
_DOMAIN_LOOSE = re.compile(rf"{_LABEL}(?:{_DOT_ANY}{_LABEL}){{0,8}}{_DOT_ANY}{_TLD}(?![\w-])", re.I)
#: After the word ``at``: every dot spelled, and a known ending (``looked at the dot product`` is not an address).
_DOMAIN_SPELLED = re.compile(rf"{_LABEL}(?:{_DOT_WORD}{_LABEL}){{0,8}}{_DOT_WORD}{_KNOWN_TLD}(?![\w-])", re.I)
#: ``d o m a i n . c o m``: single characters, one space apart.
_DOMAIN_LETTERS = re.compile(r"((?:[\w.-] ){3,80}[\w.-])(?![\w.-])")
_DOMAIN_FULL = re.compile(rf"{_LABEL}(?:\.{_LABEL}){{0,8}}\.{_TLD}", re.I)
# The local part, read backwards from the anchor (so each pattern below matches reversed text).
#: ``jane``, ``jane.doe``, and ``jane dot doe`` / ``jane (dot) doe``.
_LOCAL_SPELLED = re.compile(r"[\w.%+-]{1,64}(?:\s{1,2}tod\s{1,2}[\w%+-]{1,64}|\s{0,2}[)\]}>]\s?tod\s?[(\[{<]\s{0,2}[\w%+-]{1,64}){0,3}", re.I)
_LOCAL_LETTERS = re.compile(r"(?:[\w.%+-] ){2,63}[\w.%+-](?![\w.%+-])")
_GAP = re.compile(r"\s{0,2}")


def _local_start(shadow: str, end: int) -> tuple[int, str] | None:
    """Where the local part ending at ``end`` (spaces before the anchor allowed) begins, and how it is written."""

    window = shadow[max(0, end - _LOCAL_WINDOW):end][::-1]
    gap = _GAP.match(window).end()  # type: ignore[union-attr]
    letters = _LOCAL_LETTERS.match(window, gap)
    if letters is not None:
        return end - letters.end(), "letters"
    spelled = _LOCAL_SPELLED.match(window, gap)
    if spelled is None:
        return None
    plain = gap == 0 and not any(char.isspace() for char in spelled.group())
    return end - spelled.end(), "tight" if plain else "spaced"


def _email_spans(shadow: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    done = 0
    for anchor in _AT_ANCHOR.finditer(shadow):
        if anchor.start() < done:
            continue
        symbol = anchor.group() == "@"
        word = not symbol and anchor.group()[0] in "aA"
        local = _local_start(shadow, anchor.start())
        if local is None:
            continue
        start, written = local
        after = _GAP.match(shadow, anchor.end()).end()  # type: ignore[union-attr]
        spaced = written != "tight" or after != anchor.end()
        end: int | None = None
        if word:
            # ``jane at example dot com``: the plain word needs a spelled dot (``Engineer at acme.com`` is not one).
            found = _DOMAIN_SPELLED.match(shadow, after)
            end = found.end() if found is not None else None
        else:
            letters = _DOMAIN_LETTERS.match(shadow, after) if spaced else None
            token = shadow[start:anchor.start()].strip()
            if letters is not None and _DOMAIN_FULL.fullmatch(letters.group(1).replace(" ", "")):
                end = letters.end(1)
            elif not (symbol and spaced and written != "letters" and token.isalpha() and token[0].isupper()):
                # ``Engineer @ Booking.com`` is a title, ``jane @ example.com`` an address: a spaced ``@``
                # after one capitalised word is left alone.
                found = _DOMAIN_LOOSE.match(shadow, after)
                end = found.end() if found is not None else None
        if end is not None:
            spans.append((start, end, "email"))
            done = end
    return spans


# --- phone and street address ------------------------------------------------------------

_SEP = r"[ \t.\-]"
_PHONE = re.compile(
    # 3-3-4 with separators, an optional country code, the area code plain or bracketed.
    rf"(?<![\w.+\-/#=])(?:\+?\d{{1,3}}{_SEP}{{0,2}})?(?:[(\[{{] ?\d{{3}} ?[)\]}}]{_SEP}{{0,3}}|\d{{3}}{_SEP}{{1,3}})\d{{3}}{_SEP}{{1,3}}\d{{4}}(?!\w)"
    # +country then groups.
    rf"|(?<![\w.+\-/#=])\+\d{{1,3}}(?:{_SEP}{{0,2}}\(?\d{{1,5}}\)?){{2,5}}(?!\w)"
    # Digits written one by one: 4 1 5 5 5 5 0 1 2 3.
    rf"|(?<![\w.+\-/#=])(?:\d{_SEP}{{1,2}}){{9,14}}\d(?!\w)"
)
_STREET = re.compile(
    r"\b\d{1,5}[ \t]{1,3}(?:[A-Z][A-Za-z.']{0,30}[ \t]{1,3}){1,3}"
    r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl)\b"
    # ``2024 Best Place to work`` is a sentence; an address ends, or goes on with a unit or a capitalised place.
    r"(?![ \t]+(?!apt\b|unit\b|suite\b|ste\b)[a-z])"
)


def _digits(text: str) -> int:
    return sum(char.isdigit() for char in text)


# --- one string --------------------------------------------------------------------------

#: An ASCII text with none of these holds no contact shape (most strings: a status, a kind, a label).
_MAYBE = re.compile(r"[@\d]|\bat\b|linkedin|github|gitlab", re.I)


def redact_text(text: str, counts: dict[str, int] | None = None) -> str:
    """``text`` with every contact shape replaced by its token; ``counts`` gains one per replacement."""

    if text.isascii() and _MAYBE.search(text) is None:
        return text
    shadow = _folded(text)
    spans = _email_spans(shadow)
    kept_links: list[tuple[int, int]] = []
    for found in _PERSONAL_LINK.finditer(shadow):
        spans.append((found.start(), _trimmed_end(shadow, found.start(), found.end()), "links"))
    for found in _ANY_URL.finditer(shadow):
        kept_links.append((found.start(), found.end()))
    for found in _PHONE.finditer(shadow):
        if 8 <= _digits(found.group()) <= 15 and not _inside(found.start(), found.end(), kept_links):
            spans.append((found.start(), found.end(), "phone"))
    for found in _STREET.finditer(shadow):
        if not _inside(found.start(), found.end(), kept_links):
            spans.append((found.start(), found.end(), "address"))
    if not spans:
        return text
    spans.sort(key=lambda span: (span[0], span[0] - span[1]))
    out: list[str] = []
    position = 0
    for start, end, kind in spans:
        if start < position:
            continue
        out.append(text[position:start])
        out.append(TOKENS[kind])
        if counts is not None:
            counts[kind] = counts.get(kind, 0) + 1
        position = end
    out.append(text[position:])
    return "".join(out)


def _inside(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """Whether ``[start, end)`` touches one of ``ranges`` (sorted, disjoint): a binary search."""

    low, high = 0, len(ranges)
    while low < high:
        middle = (low + high) // 2
        if ranges[middle][1] <= start:
            low = middle + 1
        else:
            high = middle
    return low < len(ranges) and ranges[low][0] < end


# --- a payload ---------------------------------------------------------------------------


def _walk(node: object, key: str | None, counts: dict[str, int]) -> object:
    if isinstance(node, str):
        return node if _is_structural(key) else redact_text(node, counts)
    if isinstance(node, dict):
        posting = any(name in node for name in _POSTING_IDENTITY_KEYS)
        changed: dict[object, object] | None = None
        for name, value in node.items():
            if name in _STRUCTURAL_SUBTREES or (posting and name == "text" and isinstance(value, str)):
                continue
            new = _walk(value, name if isinstance(name, str) else None, counts)
            if new is not value:
                if changed is None:
                    changed = dict(node)
                changed[name] = new
        return node if changed is None else changed
    if isinstance(node, (list, tuple)):
        items: list[object] | None = None
        for index, value in enumerate(node):
            new = _walk(value, key, counts)
            if new is not value:
                if items is None:
                    items = list(node)
                items[index] = new
        return node if items is None else items
    return node


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """``payload`` as it may leave: contact shapes in its strings replaced, ``_redactions`` added when any was.

    The same object when nothing was found (nothing is copied); otherwise a copy of the
    changed branches with ``_redactions: {kind: count}``. Never raises on a JSON value.
    """

    counts: dict[str, int] = {}
    out = _walk(payload, None, counts)
    if not counts or not isinstance(out, dict):
        return payload
    return {**out, REDACTIONS_KEY: {kind: counts[kind] for kind in KINDS if kind in counts}}


__all__ = ["KINDS", "REDACTIONS_KEY", "TOKENS", "redact_payload", "redact_text"]
