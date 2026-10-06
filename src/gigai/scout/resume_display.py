"""Resume display settings: the per-profile title and the PDF layout (0110-046: no personal data).

Stored once per home at ``<home>/scout/resume-display.json`` (``scout-resume-display:1``),
0600, display-only: nothing here ever feeds a model prompt or the network.  The file holds the
per-profile ``titles``, ``spacing_scale`` and ``auto_fit``.  GigAI never stores the user's name or
contact details (0110-046): a file written before 0.1.10.7 may still carry ``name`` / ``contact``;
reads ignore them, any save drops them, and ``legacy_contact_fields`` counts them for the one-time
cleanup.  The PDF header's name and contact items come from the Generate PDF form for ONE render
(``parse_header_form`` + ``form_header``) and are never written anywhere.  Reads are tolerant
(missing, symlinked or malformed means "not saved"; a file written before 0110-017 has no
``spacing_scale``/``auto_fit`` and reads as the defaults).  Nothing is prefilled from the stored
resume any more (0110-046: its header is not kept).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from gigai.scout.find_jobs.discovery.storage import atomic_write

SCHEMA_VERSION = "scout-resume-display:1"
MAX_VALUE = 200
#: The PDF spacing unit's scale (0110-017): the Resume display slider's range and default.
SPACING_MIN, SPACING_MAX, SPACING_DEFAULT = 0.7, 1.4, 1.0
#: The keys a file written before 0.1.10.7 may still hold: never read, dropped by any save.
LEGACY_CONTACT_KEYS: tuple[str, ...] = ("name", "contact")
#: The Generate PDF form's fields (0110-046), in the order the contact line prints them after the name.
HEADER_FIELDS: tuple[str, ...] = ("name", "email", "phone", "location", "linkedin", "link", "work_authorization")
#: 0.1.11.3 item 6: the form's optional "Work authorization" line (e.g. "H-1B, requires sponsorship").  It prints as its
#: own line of this one PDF's header and, like the other form values, is never stored: not in the master, a job's resume
#: markdown or JSON, or the resumes folder.  Sponsorship stays a label for jobs; this is only what the user prints.
WORK_AUTHORIZATION_FIELD = "work_authorization"
#: 0.1.11.3 item 13: the form's optional extra links (the rows a header file's ``links`` fill): a list of
#: ``{"label", "url"}``.  The URL prints in the contact line like the form's own link fields; the label names the row.
LINKS_FIELD = "links"
MAX_LINKS = 6
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class DisplaySettings:
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
    work_authorization: str = ""


class HeaderFormError(ValueError):
    """The Generate PDF form's values are not usable; ``code`` is the API error code.

    Messages name a field and the rule, never a value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    """Drop empty titles; an out-of-range spacing scale reads as the default."""

    titles = {key: title for key, value in settings.titles.items() if isinstance(key, str) and key and (title := _clean(value))}
    auto_fit = settings.auto_fit if type(settings.auto_fit) is bool else True
    return DisplaySettings(titles, settings.updated_at, _spacing(settings.spacing_scale), auto_fit)


def _read_raw(home_root: Path) -> dict[str, object] | None:
    path = display_path(home_root)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if type(raw) is not dict or raw.get("schema_version") != SCHEMA_VERSION:
        return None
    return raw


def load_display(home_root: Path) -> DisplaySettings | None:
    """The saved settings, or ``None`` when nothing usable is saved.  A legacy name/contact is ignored."""

    raw = _read_raw(home_root)
    if raw is None:
        return None
    titles = raw.get("titles") if type(raw.get("titles")) is dict else {}
    updated = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else ""
    spacing = raw.get("spacing_scale", SPACING_DEFAULT)
    auto_fit = raw.get("auto_fit", True)
    return normalize(DisplaySettings(dict(titles), updated, spacing, auto_fit))


def legacy_contact_fields(home_root: Path) -> dict[str, int]:
    """How many personal values a pre-0.1.10.7 file still holds: ``{"name": 0|1, "contact": <items>}``.

    Counts only (the one-time cleanup's report); ``{}`` when the file holds none or cannot be read."""

    raw = _read_raw(home_root)
    if raw is None:
        return {}
    found: dict[str, int] = {}
    if isinstance(raw.get("name"), str) and raw["name"].strip():
        found["name"] = 1
    contact = raw.get("contact")
    items = [item for item in contact if type(item) is dict and isinstance(item.get("value"), str) and item["value"].strip()] if type(contact) is list else []
    if items:
        found["contact"] = len(items)
    if not found and any(key in raw for key in LEGACY_CONTACT_KEYS):
        found["empty_fields"] = sum(1 for key in LEGACY_CONTACT_KEYS if key in raw)
    return found


def save_display(home_root: Path, settings: DisplaySettings, *, now: datetime | None = None) -> DisplaySettings:
    """Normalize and write atomically, 0600 (directory 0700 when created).  Only layout and titles are written."""

    clean = normalize(settings)
    stamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    clean = replace(clean, updated_at=stamp)
    path = display_path(home_root)
    if path.is_symlink():
        raise OSError("resume display settings path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "titles": clean.titles,
        "spacing_scale": clean.spacing_scale,
        "auto_fit": clean.auto_fit,
        "updated_at": clean.updated_at,
    }
    atomic_write(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)
    return clean


# --- the Generate PDF form (0110-046): one render's header, never stored ------------------

_SCHEME = re.compile(r"\A[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _form_text(raw: Mapping[str, object], key: str, name: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise HeaderFormError("wrong_type", f"{name} must be a string")
    if len(value) > MAX_VALUE:
        raise HeaderFormError("invalid_value", f"{name} is longer than {MAX_VALUE} characters")
    if _CONTROL.search(value):
        raise HeaderFormError("invalid_value", f"{name} must be one line")
    return value.strip()


def parse_header_form(raw: object) -> dict[str, object]:
    """The form's values, trimmed, or ``HeaderFormError``.  Pure: no I/O, never logs.

    ``raw`` is an object of strings keyed by ``HEADER_FIELDS`` (each optional, at most ``MAX_VALUE``
    characters, one line), plus the optional ``links``: at most ``MAX_LINKS`` objects ``{"label", "url"}``
    (0.1.11.3 item 13; a row with an empty url is dropped).  Errors name the field and the rule, never the value."""

    if type(raw) is not dict:
        raise HeaderFormError("wrong_type", "header must be an object of strings: " + ", ".join(HEADER_FIELDS))
    if any(key not in HEADER_FIELDS and key != LINKS_FIELD for key in raw):
        raise HeaderFormError("unknown_key", "header has an unknown field (allowed: " + ", ".join((*HEADER_FIELDS, LINKS_FIELD)) + ")")
    values: dict[str, object] = {key: _form_text(raw, key, f"header.{key}") for key in HEADER_FIELDS}
    if LINKS_FIELD in raw:
        links = raw[LINKS_FIELD]
        if type(links) is not list or any(type(item) is not dict for item in links):
            raise HeaderFormError("wrong_type", "header.links must be a list of {label, url} objects")
        if len(links) > MAX_LINKS:
            raise HeaderFormError("invalid_value", f"header.links holds more than {MAX_LINKS} links")
        rows: list[dict[str, str]] = []
        for number, item in enumerate(links, 1):
            if any(key not in ("label", "url") for key in item):
                raise HeaderFormError("unknown_key", f"header.links[{number}] has an unknown field (allowed: label, url)")
            label, url = (_form_text(item, key, f"header.links[{number}].{key}") for key in ("label", "url"))
            if url:
                rows.append({"label": label, "url": url})
        if rows:
            values[LINKS_FIELD] = rows
    return values


def _link_item(value: str) -> ContactItem:
    if _SCHEME.match(value):
        return ContactItem(_SCHEME.sub("", value).rstrip("/"), value)
    return ContactItem(value.rstrip("/"), "https://" + value)


def form_header(values: Mapping[str, object], title: str = "") -> PdfHeader:
    """The PDF header from the form's values (``parse_header_form``) and the saved per-profile title."""

    contact: list[ContactItem] = []
    for key in HEADER_FIELDS[1:]:
        value = values.get(key, "")
        if not value or key == WORK_AUTHORIZATION_FIELD or not isinstance(value, str):
            continue
        if key == "email":
            contact.append(ContactItem(value, "mailto:" + value))
        elif key in ("linkedin", "link"):
            contact.append(_link_item(value))
        else:
            contact.append(ContactItem(value, None))
    contact.extend(_link_item(row["url"]) for row in values.get(LINKS_FIELD, ()))  # type: ignore[union-attr]
    return PdfHeader(values.get("name", ""), _clean(title), tuple(contact), values.get(WORK_AUTHORIZATION_FIELD, ""))  # type: ignore[arg-type]


def profile_title(settings: DisplaySettings | None, profile_id: str | None) -> str:
    return settings.titles.get(profile_id or "", "") if settings is not None else ""


__all__ = [
    "ContactItem",
    "DisplaySettings",
    "HEADER_FIELDS",
    "HeaderFormError",
    "LEGACY_CONTACT_KEYS",
    "LINKS_FIELD",
    "MAX_LINKS",
    "PdfHeader",
    "SCHEMA_VERSION",
    "SPACING_DEFAULT",
    "WORK_AUTHORIZATION_FIELD",
    "SPACING_MAX",
    "SPACING_MIN",
    "display_path",
    "form_header",
    "legacy_contact_fields",
    "load_display",
    "normalize",
    "parse_header_form",
    "profile_title",
    "save_display",
    "valid_spacing",
]
