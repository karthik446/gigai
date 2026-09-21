"""Bounded, deterministic checks for Scout application documents.

The checker deliberately operates on caller-supplied bytes only.  It does not
read a workpad, infer candidate facts, contact a provider, persist a result, or
claim that lexical presence establishes competence.  Its report is a plain
JSON-shaped mapping so a caller can bind it to the exact document revision it
already owns without introducing a new schema family here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re
import unicodedata

from .canonical import canonical_json_bytes, digest_imported_bytes


RULE_ID = "scout.document_checks"
RULE_VERSION = "1.0"
MAX_DOCUMENT_BYTES = 256_000
MAX_FINDINGS = 128
MAX_POSTING_TERMS = 128
MAX_TERM_LENGTH = 160
MAX_HEADING_REQUIREMENTS = 32
MAX_CONTACT_REQUIREMENTS = 16
MAX_EVIDENCE_REFS = 16

DOCUMENT_KINDS = frozenset({"resume", "cover_letter"})
SEVERITIES = ("info", "low", "medium", "high")

_WORD_RE = re.compile(r"\S+", re.UNICODE)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]+(?:\.[\w-]{1,63})+(?![\w.-])", re.UNICODE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d().\-\s]{6,}\d)(?!\d)")
_ATX_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>\S(?:.*?\S)?)\s*#*\s*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^\s*(?:=+|-+)\s*$")
_PLACEHOLDER_RE = re.compile(
    r"(?:\[\s*(?:your\s+)?(?:name|email|phone|company|employer|job(?:\s+title)?|title|date|address)\s*\]"
    r"|\{\{[^{}\r\n]{1,80}\}\}"
    r"|\b(?:TODO|TBD|TBA|LOREM\s+IPSUM|INSERT\s+(?:NAME|COMPANY|TITLE|TEXT))\b)",
    re.IGNORECASE,
)
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_KNOWN_CONTACTS = frozenset({"email", "phone", "name", "location", "address", "linkedin", "website"})


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _refs(text: str, offsets: Iterable[int]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    for offset in offsets:
        line = _line_for_offset(text, offset)
        if line in seen:
            continue
        seen.add(line)
        result.append({"kind": "document", "line": line})
        if len(result) >= MAX_EVIDENCE_REFS:
            break
    return result


def _first_offset(text: str, needle: str, *, case_sensitive: bool = False) -> int:
    if not needle:
        return 0
    if case_sensitive:
        return text.find(needle)
    return text.casefold().find(needle.casefold())


def _normalise_heading(value: str) -> str:
    return " ".join(value.strip().rstrip("#").split()).casefold()


def _supported_headings(text: str) -> tuple[set[str], dict[str, int]]:
    """Return exact supported Markdown/plain-text section headings.

    Supported forms are conservative: ATX Markdown headings (up to three
    leading spaces and one to six ``#`` characters), Setext headings whose
    following line is all ``=`` or ``-``, and standalone plain-text heading
    lines surrounded by a blank line (or document boundary).  A prose line is
    not a heading merely because it contains the requested words.
    """

    lines = text.splitlines()
    headings: set[str] = set()
    locations: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = _ATX_HEADING_RE.match(line)
        title: str | None = match.group("title") if match else None
        if title is None and index + 1 < len(lines) and line.strip() and _SETEXT_UNDERLINE_RE.match(lines[index + 1]):
            title = line.strip()
        if title is None:
            stripped = line.strip()
            previous_blank = index == 0 or not lines[index - 1].strip()
            next_blank = index == len(lines) - 1 or not lines[index + 1].strip()
            # Plain headings are intentionally conservative: standalone,
            # short, non-sentence lines only.  This admits ``Experience`` but
            # rejects ``Experience mentioned in prose.``.
            if (
                stripped
                and len(stripped) <= MAX_TERM_LENGTH
                and previous_blank
                and next_blank
                and not stripped.endswith((".", ",", ";", ":"))
                and any(char.isalpha() for char in stripped)
                and (
                    len(stripped.split()) == 1
                    or all(not char.isalpha() or char.isupper() for char in stripped)
                    or all(word[:1].isupper() for word in stripped.split() if word[:1].isalpha())
                )
            ):
                title = stripped
        if title is not None:
            key = _normalise_heading(title)
            if key:
                headings.add(key)
                locations.setdefault(key, index + 1)
    return headings, locations


def _normalise_terms(value: object) -> tuple[list[str], list[tuple[str, int]]]:
    """Return bounded unique terms and (invalid reason, index) diagnostics."""

    if value is None:
        return [], []
    raw: list[object]
    overflow = False
    if isinstance(value, Mapping):
        iterator = iter(value.keys())
        raw = []
        for _ in range(MAX_POSTING_TERMS + 1):
            try:
                raw.append(next(iterator))
            except StopIteration:
                break
        overflow = len(raw) > MAX_POSTING_TERMS
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        iterator = iter(value)
        raw = []
        for _ in range(MAX_POSTING_TERMS + 1):
            try:
                raw.append(next(iterator))
            except StopIteration:
                break
        overflow = len(raw) > MAX_POSTING_TERMS
    else:
        return [], [("posting_terms_not_iterable", 0)]

    terms: list[str] = []
    invalid: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(raw[:MAX_POSTING_TERMS]):
        if not isinstance(candidate, str):
            invalid.append(("posting_term_not_text", index))
            continue
        if len(candidate) > MAX_TERM_LENGTH:
            invalid.append(("posting_term_oversize", index))
            continue
        term = " ".join(candidate.split())
        if not term:
            invalid.append(("posting_term_empty", index))
            continue
        if len(term) > MAX_TERM_LENGTH:
            invalid.append(("posting_term_oversize", index))
            continue
        folded = term.casefold()
        if folded not in seen:
            terms.append(term)
            seen.add(folded)
    if overflow:
        invalid.append(("posting_terms_bounded", MAX_POSTING_TERMS))
    terms.sort(key=str.casefold)
    return terms, invalid


def _normalise_sequence(value: object, *, limit: int, code: str) -> tuple[list[str], list[str]]:
    if value is None:
        return [], []
    if isinstance(value, str):
        raw: list[object] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw = list(value[: limit + 1])
    else:
        return [], [code]
    result: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in raw[:limit]:
        if not isinstance(item, str) or not item.strip():
            errors.append(code)
            continue
        if len(item) > MAX_TERM_LENGTH:
            errors.append(code)
            continue
        item = " ".join(item.split())
        folded = item.casefold()
        if folded not in seen:
            result.append(item)
            seen.add(folded)
    if len(raw) > limit:
        errors.append(f"{code}_bounded")
    result.sort(key=str.casefold)
    return result, errors


def _normalise_requirements(value: object, *, limit: int, code: str) -> tuple[dict[str, object], list[str]]:
    if value is None:
        return {}, []
    if not isinstance(value, Mapping):
        return {}, [code]
    result: dict[str, object] = {}
    errors: list[str] = []
    duplicates: set[str] = set()
    for index, (key, item) in enumerate(value.items()):
        if index >= limit:
            errors.append(f"{code}_bounded")
            break
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_TERM_LENGTH:
            errors.append(code)
            continue
        normalized_key = key.strip().casefold()
        if normalized_key in result or normalized_key in duplicates:
            duplicates.add(normalized_key)
            result.pop(normalized_key, None)
            errors.append(f"{code}_duplicate")
            continue
        result[normalized_key] = item
    return dict(sorted(result.items())), errors


def _finding(code: str, severity: str, message: str, refs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity if severity in SEVERITIES else "medium",
        "message": message,
        "evidence_refs": list(refs or []),
    }


def _add(findings: list[dict[str, object]], item: dict[str, object]) -> None:
    # Reserve one slot for a visible truncation marker.  A bounded report must
    # never look complete when a caller supplied enough inputs to overflow it.
    if len(findings) < MAX_FINDINGS - 1:
        findings.append(item)
    elif not any(existing.get("code") == "findings_truncated" for existing in findings):
        findings.append(_finding("findings_truncated", "high", "Findings were truncated; not all checks are represented"))


def _digest_terms(terms: list[str]) -> str | None:
    if not terms:
        return None
    return digest_imported_bytes(canonical_json_bytes(terms))


def _safe_setting_value(value: object) -> dict[str, object]:
    """Bind a setting without exposing a supplied contact or private value."""

    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > MAX_TERM_LENGTH:
            return {"type": "str", "valid": False, "reason": "oversize"}
        try:
            return {
                "type": type(value).__name__,
                "sha256": digest_imported_bytes(canonical_json_bytes(value)),
            }
        except (TypeError, ValueError, OverflowError):
            return {"type": type(value).__name__, "valid": False}
    return {"type": type(value).__name__, "valid": False}


def _requirements_digest(
    *,
    document_kind: str,
    terms: list[str],
    term_errors: list[tuple[str, int]],
    lengths: Mapping[str, object],
    length_errors: list[str],
    contacts: Mapping[str, object],
    contact_errors: list[str],
    headings: list[str],
    heading_errors: list[str],
    cover: Mapping[str, object],
    cover_errors: list[str],
) -> str:
    payload = {
        "version": RULE_VERSION,
        "document_kind": document_kind,
        "posting_terms": terms,
        "posting_term_errors": [[code, index] for code, index in term_errors],
        "length_requirements": {key: _safe_setting_value(value) for key, value in lengths.items()},
        "length_requirement_errors": length_errors,
        "contact_requirements": {key: _safe_setting_value(value) for key, value in contacts.items()},
        "contact_requirement_errors": contact_errors,
        "heading_requirements": headings,
        "heading_requirement_errors": heading_errors,
        "cover_letter_requirements": {key: _safe_setting_value(value) for key, value in cover.items()},
        "cover_letter_requirement_errors": cover_errors,
    }
    return digest_imported_bytes(canonical_json_bytes(payload))


def _contact_present(text: str, field: str, expected: object) -> bool:
    if isinstance(expected, str):
        return bool(expected.strip()) and expected.casefold() in text.casefold()
    if not expected:
        return True
    folded = field.casefold()
    if folded == "email":
        return bool(_EMAIL_RE.search(text))
    if folded == "phone":
        return bool(_PHONE_RE.search(text))
    if folded in {"linkedin", "website"}:
        return bool(re.search(r"(?:https?://|www\.)\S+", text, re.IGNORECASE))
    if folded == "name":
        lines = [line.strip(" #\t") for line in text.splitlines() if line.strip()]
        return bool(lines and any(ch.isalpha() for ch in lines[0]))
    # Location and address cannot be safely inferred without echoing or
    # inventing a value.  Require a caller-supplied string for those fields.
    return False


def _length_findings(
    text: str,
    requirements: Mapping[str, object],
    findings: list[dict[str, object]],
) -> dict[str, int]:
    words = len(_WORD_RE.findall(text))
    chars = len(text)
    measured = {"word_count": words, "character_count": chars}
    aliases = {"min": "min_words", "max": "max_words"}
    bounds: dict[str, int] = {}
    for key, value in requirements.items():
        canonical = aliases.get(key, key)
        if canonical not in {"min_words", "max_words", "min_chars", "max_chars"}:
            _add(findings, _finding("length_requirement_invalid", "medium", "Unsupported declared length bound"))
            continue
        if type(value) is not int or value < 0 or value > MAX_DOCUMENT_BYTES * 4:
            _add(findings, _finding("length_requirement_invalid", "medium", "Declared length bound is invalid"))
            continue
        bounds[canonical] = value
    if "min_words" in bounds and "max_words" in bounds and bounds["min_words"] > bounds["max_words"]:
        _add(findings, _finding("length_requirement_invalid", "medium", "Declared word bounds are reversed"))
    if "min_chars" in bounds and "max_chars" in bounds and bounds["min_chars"] > bounds["max_chars"]:
        _add(findings, _finding("length_requirement_invalid", "medium", "Declared character bounds are reversed"))
    for bound, actual, direction, message in (
        ("min_words", words, "below", "Document is shorter than the declared minimum word length"),
        ("max_words", words, "above", "Document exceeds the declared maximum word length"),
        ("min_chars", chars, "below", "Document is shorter than the declared minimum character length"),
        ("max_chars", chars, "above", "Document exceeds the declared maximum character length"),
    ):
        if bound not in bounds:
            continue
        violated = actual < bounds[bound] if direction == "below" else actual > bounds[bound]
        if violated:
            _add(findings, _finding("length_out_of_bounds", "medium", message))
    return measured


def check_document(
    document_bytes: bytes,
    document_kind: str,
    *,
    posting_terms: Iterable[str] | Mapping[str, object] | None = None,
    length_requirements: Mapping[str, object] | None = None,
    contact_requirements: Mapping[str, object] | None = None,
    heading_requirements: Sequence[str] | str | None = None,
    cover_letter_requirements: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Inspect exact UTF-8 Markdown bytes with bounded deterministic rules.

    ``length_requirements`` accepts ``min_words``, ``max_words``, ``min_chars``
    and ``max_chars`` (``min``/``max`` are word aliases).  Contact requirement
    values may be ``True`` for a conservative field heuristic or a supplied
    string, which is matched without ever being copied to the report.  Cover
    letter requirements use ``job_title`` and ``company_name`` string values.
    """

    findings: list[dict[str, object]] = []
    document_digest: str | None = None
    document_size = 0
    text = ""
    decoded = False
    if type(document_bytes) is bytes:
        document_size = len(document_bytes)
        document_digest = digest_imported_bytes(document_bytes)
        if document_size > MAX_DOCUMENT_BYTES:
            _add(findings, _finding("document_oversize", "high", "Document exceeds the deterministic input byte limit"))
        else:
            try:
                text = document_bytes.decode("utf-8")
                decoded = True
            except UnicodeDecodeError:
                _add(findings, _finding("document_not_utf8", "high", "Document is not valid UTF-8"))
    else:
        _add(findings, _finding("document_bytes_required", "high", "Document input must be exact UTF-8 bytes"))

    kind_valid = isinstance(document_kind, str) and document_kind in DOCUMENT_KINDS
    kind = document_kind if kind_valid else "invalid"
    if not kind_valid:
        _add(findings, _finding("document_kind_invalid", "high", "Document kind is not supported"))

    terms, term_errors = _normalise_terms(posting_terms)
    for code, _index in term_errors:
        _add(findings, _finding(code, "medium", "Posting terms were malformed or bounded"))
    lengths, length_errors = _normalise_requirements(length_requirements, limit=8, code="length_requirements_invalid")
    for code in length_errors:
        _add(findings, _finding(code, "medium", "Declared length requirements are malformed or bounded"))
    contacts, contact_errors = _normalise_requirements(contact_requirements, limit=MAX_CONTACT_REQUIREMENTS, code="contact_requirements_invalid")
    for code in contact_errors:
        _add(findings, _finding(code, "medium", "Declared contact requirements are malformed or bounded"))
    cover, cover_errors = _normalise_requirements(cover_letter_requirements, limit=4, code="cover_letter_requirements_invalid")
    for code in cover_errors:
        _add(findings, _finding(code, "medium", "Cover-letter specificity requirements are malformed or bounded"))
    headings, heading_errors = _normalise_sequence(heading_requirements, limit=MAX_HEADING_REQUIREMENTS, code="heading_requirements_invalid")
    for code in heading_errors:
        _add(findings, _finding(code, "medium", "Declared heading requirements are malformed or bounded"))

    measurements = {"word_count": 0, "character_count": 0, "line_count": 0, "heading_count": 0}
    if decoded:
        if not text.strip():
            _add(findings, _finding("document_empty", "high", "Document has no readable text"))
        measurements["line_count"] = max(1, text.count("\n") + 1)
        supported_headings, _heading_locations = _supported_headings(text)
        measurements["heading_count"] = len(supported_headings)
        measurements.update(_length_findings(text, lengths, findings))
        if not any(ch.isalnum() for ch in text):
            _add(findings, _finding("document_unreadable", "high", "Document has no readable letters or numbers"))
        control_offsets = [index for index, char in enumerate(text) if unicodedata.category(char) in _CONTROL_CATEGORIES and char not in "\t\r\n"]
        if control_offsets:
            _add(findings, _finding("control_text_present", "high", "Document contains disallowed control or formatting characters", _refs(text, control_offsets)))

        if measurements["heading_count"] == 0:
            _add(findings, _finding("headings_missing", "low", "Document contains no Markdown section headings"))
        for required in headings:
            if _normalise_heading(required) not in supported_headings:
                _add(findings, _finding("heading_missing", "medium", "A declared section heading is missing"))

        for field, expected in sorted(contacts.items()):
            if field not in _KNOWN_CONTACTS:
                _add(findings, _finding("contact_requirement_invalid", "medium", "Unsupported contact requirement"))
            elif not isinstance(expected, (bool, str)):
                _add(findings, _finding("contact_requirement_invalid", "medium", "Contact requirement value is invalid"))
            elif isinstance(expected, str) and len(expected) > MAX_TERM_LENGTH:
                _add(findings, _finding("contact_requirement_invalid", "medium", "Contact requirement is oversize"))
            elif expected and not _contact_present(text, field, expected):
                _add(findings, _finding("contact_missing", "medium", "A declared contact field is not present"))

        for match in _PLACEHOLDER_RE.finditer(text):
            _add(findings, _finding("placeholder_present", "medium", "Document contains an obvious placeholder", _refs(text, [match.start()])))

        for field, supplied in cover.items():
            if field not in {"job_title", "company_name"}:
                _add(findings, _finding("cover_letter_requirement_invalid", "medium", "Unsupported cover-letter specificity requirement"))
            elif not isinstance(supplied, str) or not supplied.strip() or len(supplied) > MAX_TERM_LENGTH:
                _add(findings, _finding("cover_letter_requirement_invalid", "medium", "Cover-letter specificity value is invalid or oversize"))

        if kind == "cover_letter":
            for field in ("job_title", "company_name"):
                expected = cover.get(field)
                if expected is None:
                    continue
                if isinstance(expected, str) and expected.strip() and len(expected) <= MAX_TERM_LENGTH and expected.casefold() not in text.casefold():
                    _add(findings, _finding("cover_letter_specificity_missing", "medium", "Cover letter does not name the declared role or company"))

        for term in terms:
            match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE | re.UNICODE)
            if match:
                _add(findings, _finding("term_present", "info", "Lexical posting coverage: term_present", _refs(text, [match.start()])))
            else:
                _add(findings, _finding("term_absent", "low", "A supplied posting term was not found lexically"))

    # Keep the explicit abstention inspectable even when malformed input stops
    # all document-level checks.  These are limits, not quality scores.
    limits = ["semantic_factuality_not_assessed", "requirement_to_evidence_judgment_not_assessed"]
    _add(findings, _finding("semantic_factuality_not_assessed", "info", "Semantic factuality is not assessed by this deterministic service"))
    _add(findings, _finding("requirement_to_evidence_not_assessed", "info", "Requirement-to-evidence judgment is not assessed by this deterministic service"))

    requirements_digest = _requirements_digest(
        document_kind=kind,
        terms=terms,
        term_errors=term_errors,
        lengths=lengths,
        length_errors=length_errors,
        contacts=contacts,
        contact_errors=contact_errors,
        headings=headings,
        heading_errors=heading_errors,
        cover=cover,
        cover_errors=cover_errors,
    )

    # Sort only on bounded, non-sensitive fields to make equivalent calls byte
    # stable even when callers supplied mapping keys in different orders.
    findings.sort(key=lambda item: (str(item["code"]), str(item["severity"]), str(item["message"]), repr(item["evidence_refs"])))
    body: dict[str, object] = {
        "rule": RULE_ID,
        "rule_version": RULE_VERSION,
        "document_kind": kind or None,
        "inputs": {
            "document_sha256": document_digest,
            "document_size_bytes": document_size,
            "posting_terms_sha256": _digest_terms(terms),
            "posting_terms_count": len(terms),
            "requirements_sha256": requirements_digest,
        },
        "measurements": measurements,
        "findings": findings[:MAX_FINDINGS],
        "assessment_limits": limits,
        "bounded": {
            "max_document_bytes": MAX_DOCUMENT_BYTES,
            "max_findings": MAX_FINDINGS,
            "max_posting_terms": MAX_POSTING_TERMS,
        },
    }
    body["output"] = {
        "finding_count": len(body["findings"]),
        "digest_scope": "canonical report bytes with output.sha256 omitted",
        "checks_complete": (
            decoded
            and document_size <= MAX_DOCUMENT_BYTES
            and kind_valid
            and not term_errors
            and not length_errors
            and not contact_errors
            and not heading_errors
            and not cover_errors
            and not any(item["code"] == "findings_truncated" for item in findings)
        ),
    }
    # Hash the complete output-shaped report while omitting only the digest
    # member itself.  Consumers can reconstruct exactly this preimage from the
    # returned report; finding_count, scope, and completion state are covered.
    output_digest = digest_imported_bytes(canonical_json_bytes(body))
    body["output"]["sha256"] = output_digest  # type: ignore[index]
    return body


inspect_document = check_document


__all__ = [
    "DOCUMENT_KINDS",
    "MAX_DOCUMENT_BYTES",
    "RULE_ID",
    "RULE_VERSION",
    "check_document",
    "inspect_document",
]
