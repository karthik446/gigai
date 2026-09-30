"""Resume display settings: the name, contact line and per-profile title printed on a PDF.

Stored once per home at ``<home>/scout/resume-display.json`` (``scout-resume-display:1``),
0600, display-only: nothing here ever feeds a model prompt or the network.  Reads are
tolerant (missing, symlinked or malformed means "not saved"; a file written before 0110-017
has no ``spacing_scale``/``auto_fit`` and reads as the defaults).  ``suggest`` is a local,
pure prefill parser: it never writes and never overrides saved values.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from gigai.scout.find_jobs.discovery.storage import atomic_write

SCHEMA_VERSION = "scout-resume-display:1"
KINDS: tuple[str, ...] = ("location", "work_authorization", "linkedin", "github", "link", "email", "phone")
MAX_CONTACT = 12
MAX_VALUE = 200
#: The PDF spacing unit's scale (0110-017): the Resume display slider's range and default.
SPACING_MIN, SPACING_MAX, SPACING_DEFAULT = 0.7, 1.4, 1.0
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ContactEntry:
    kind: str
    value: str


@dataclass(frozen=True)
class DisplaySettings:
    name: str = ""
    contact: tuple[ContactEntry, ...] = ()
    titles: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""
    spacing_scale: float = SPACING_DEFAULT
    auto_fit: bool = True


@dataclass(frozen=True)
class ContactItem:
    text: str
    url: str | None


@dataclass(frozen=True)
class PdfHeader:
    name: str = ""
    title: str = ""
    contact: tuple[ContactItem, ...] = ()


@dataclass(frozen=True)
class Suggestion:
    name: str = ""
    title: str = ""
    contact: tuple[ContactEntry, ...] = ()


def display_path(home_root: Path) -> Path:
    return home_root / "scout" / "resume-display.json"


def _clean(value: object) -> str:
    return _CONTROL.sub(" ", value).strip()[:MAX_VALUE] if isinstance(value, str) else ""


def valid_spacing(value: object) -> bool:
    """A real number (not a bool) inside the slider's range."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and SPACING_MIN <= value <= SPACING_MAX


def _spacing(value: object) -> float:
    return round(float(value), 2) if valid_spacing(value) else SPACING_DEFAULT


def normalize(settings: DisplaySettings) -> DisplaySettings:
    """Drop empty values, unknown kinds and duplicates of single-use kinds; keep order.  An out-of-range
    spacing scale reads as the default."""

    seen: set[str] = set()
    contact: list[ContactEntry] = []
    for entry in settings.contact:
        value = _clean(entry.value)
        if not value or entry.kind not in KINDS or (entry.kind != "link" and entry.kind in seen):
            continue
        seen.add(entry.kind)
        contact.append(ContactEntry(entry.kind, value))
        if len(contact) >= MAX_CONTACT:
            break
    titles = {key: title for key, value in settings.titles.items() if isinstance(key, str) and key and (title := _clean(value))}
    auto_fit = settings.auto_fit if type(settings.auto_fit) is bool else True
    return DisplaySettings(_clean(settings.name), tuple(contact), titles, settings.updated_at, _spacing(settings.spacing_scale), auto_fit)


def load_display(home_root: Path) -> DisplaySettings | None:
    """The saved settings, or ``None`` when nothing usable is saved."""

    path = display_path(home_root)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if type(raw) is not dict or raw.get("schema_version") != SCHEMA_VERSION:
        return None
    contact: list[ContactEntry] = []
    if type(raw.get("contact")) is list:
        for item in raw["contact"]:
            if type(item) is dict and isinstance(item.get("kind"), str) and isinstance(item.get("value"), str):
                contact.append(ContactEntry(item["kind"], item["value"]))
    titles = raw.get("titles") if type(raw.get("titles")) is dict else {}
    updated = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else ""
    spacing = raw.get("spacing_scale", SPACING_DEFAULT)
    auto_fit = raw.get("auto_fit", True)
    return normalize(DisplaySettings(_clean(raw.get("name")), tuple(contact), dict(titles), updated, spacing, auto_fit))


def save_display(home_root: Path, settings: DisplaySettings, *, now: datetime | None = None) -> DisplaySettings:
    """Normalize and write atomically, 0600 (directory 0700 when created)."""

    clean = normalize(settings)
    stamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    clean = replace(clean, updated_at=stamp)
    path = display_path(home_root)
    if path.is_symlink():
        raise OSError("resume display settings path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "name": clean.name,
        "contact": [{"kind": e.kind, "value": e.value} for e in clean.contact],
        "titles": clean.titles,
        "spacing_scale": clean.spacing_scale,
        "auto_fit": clean.auto_fit,
        "updated_at": clean.updated_at,
    }
    atomic_write(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)
    return clean


# --- header for the PDF --------------------------------------------------------------------

_SCHEME = re.compile(r"\A[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _contact_item(entry: ContactEntry) -> ContactItem:
    value = entry.value
    if entry.kind == "email":
        return ContactItem(value, "mailto:" + value)
    if entry.kind in {"linkedin", "github", "link"}:
        if _SCHEME.match(value):
            return ContactItem(_SCHEME.sub("", value).rstrip("/"), value)
        return ContactItem(value.rstrip("/"), "https://" + value)
    return ContactItem(value, None)


def pdf_header(settings: DisplaySettings | None, profile_id: str | None, fallback_name: str = "") -> PdfHeader:
    """Saved values only; unsaved contact is never printed.  Name falls back to ``fallback_name``."""

    if settings is None:
        return PdfHeader(name=_clean(fallback_name))
    name = settings.name or _clean(fallback_name)
    title = settings.titles.get(profile_id or "", "")
    return PdfHeader(name, title, tuple(_contact_item(entry) for entry in settings.contact))


# --- local prefill parser ------------------------------------------------------------------

_MAX_HEADER_LINES = 8
_HEADING = re.compile(r"\A\s*(?:#{1,6}\s+\S|(?:summary|profile|experience|work experience|skills|education|projects|objective)\s*:?\s*\Z)", re.IGNORECASE)
_EMAIL = re.compile(r"\A[^\s@|]+@[^\s@|]+\.[A-Za-z]{2,}\Z")
_PHONE = re.compile(r"\A\+?\(?\d[\d\s().-]{6,}\d\Z")
_URLISH = re.compile(r"\A(?:https?://|www\.)\S+\Z|\A[\w-]+(?:\.[\w-]+)*\.(?:com|io|dev|me|net|org|co|app|ai)(?:/\S*)?\Z", re.IGNORECASE)
_WORKAUTH = re.compile(r"\b(?:visa|h-?1b|green card|citizen|authori[sz]ed|authori[sz]ation|sponsor\w*|ead|opt|tn)\b", re.IGNORECASE)
_LOCATION = re.compile(r"\A[A-Z][\w.'’ -]+,\s*(?:[A-Z]{2}|[A-Z][\w.'’ -]+)(?:,\s*[A-Z][\w.'’ -]+)?\Z")
_TITLE_WORD = re.compile(
    r"\b(?:engineer|developer|manager|architect|scientist|analyst|designer|lead|director|consultant|administrator|specialist|programmer|founder|head|principal|staff|intern)\b",
    re.IGNORECASE,
)
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_SPLIT = re.compile(r"\s*[|•·]\s*|\s+[–—-]\s+|\t+|\s{2,}")
_NAME_WORD = re.compile(r"\A[A-Za-z][A-Za-z.'’-]*\Z")


def _strip_markers(line: str) -> str:
    return re.sub(r"\A[#>\s]+", "", line).replace("**", "").replace("__", "").strip()


def _header_block(resume_text: str) -> list[str]:
    lines: list[str] = []
    started = False
    has_heading = any(_HEADING.match(line) and not line.lstrip().startswith("# ") for line in resume_text.splitlines()[1:])
    for raw in resume_text.splitlines():
        if not raw.strip():
            if started and not has_heading:
                break
            continue
        if started and _HEADING.match(raw):
            break
        started = True
        lines.append(_strip_markers(raw) if not lines else raw.strip())
        if len(lines) >= _MAX_HEADER_LINES:
            break
    return lines


def _looks_like_name(line: str) -> bool:
    words = line.split()
    if not 2 <= len(words) <= 4 or _TITLE_WORD.search(line) or any(ch.isdigit() for ch in line) or line.endswith((".", ":", ",")):
        return False
    return all(_NAME_WORD.match(word) and (word[0].isupper()) for word in words)


def _classify(segment: str) -> ContactEntry | None:
    text = segment.strip().strip("*_").strip()
    if not text:
        return None
    if _EMAIL.match(text):
        return ContactEntry("email", text)
    if _PHONE.match(text):
        return ContactEntry("phone", text)
    lowered = text.lower()
    if "linkedin.com/" in lowered and _URLISH.match(text):
        return ContactEntry("linkedin", text)
    if "github.com/" in lowered and _URLISH.match(text):
        return ContactEntry("github", text)
    if _URLISH.match(text):
        return ContactEntry("link", text)
    if _WORKAUTH.search(text) and len(text.split()) <= 8:
        return ContactEntry("work_authorization", text)
    if _LOCATION.match(text) and not _TITLE_WORD.search(text):
        return ContactEntry("location", text)
    return None


def suggest(resume_text: str) -> Suggestion:
    """A local prefill from the resume's header block only.  Pure: no model, no network, no writes.

    Segments that match nothing are dropped, never guessed."""

    block = _header_block(resume_text)
    if not block:
        return Suggestion()
    name = block[0] if _looks_like_name(block[0]) else ""
    title = ""
    contact: list[ContactEntry] = []
    seen: set[str] = set()
    for line in block[1 if name else 0 :]:
        line = _MD_LINK.sub(lambda m: m.group(2), line)
        segments = [s for s in _SPLIT.split(line) if s.strip()]
        if not title and len(segments) == 1 and len(line.split()) <= 8 and not line.rstrip().endswith(".") and _TITLE_WORD.search(line) and _classify(line) is None:
            title = _strip_markers(line)
            continue
        for segment in segments:
            entry = _classify(segment)
            if entry is None or (entry.kind != "link" and entry.kind in seen) or (entry.kind, entry.value) in {(c.kind, c.value) for c in contact}:
                continue
            seen.add(entry.kind)
            contact.append(entry)
    order = {kind: index for index, kind in enumerate(KINDS)}
    contact.sort(key=lambda entry: order[entry.kind])
    return Suggestion(name, title, tuple(contact))


__all__ = [
    "ContactEntry",
    "ContactItem",
    "DisplaySettings",
    "KINDS",
    "PdfHeader",
    "SCHEMA_VERSION",
    "SPACING_DEFAULT",
    "SPACING_MAX",
    "SPACING_MIN",
    "Suggestion",
    "display_path",
    "load_display",
    "normalize",
    "pdf_header",
    "save_display",
    "suggest",
    "valid_spacing",
]
