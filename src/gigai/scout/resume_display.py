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
HEADER_FIELDS: tuple[str, ...] = ("name", "email", "phone", "location", "github", "linkedin", "website", "link", "work_authorization")
#: 0.1.11.3 item 16: the link fields that take a SHORTHAND.  ``github`` and
#: ``linkedin`` hold the id alone, ``website`` a site address (``shorthand_value``); the header prints the link each
#: names (``shorthand_link``).  The same three keys of the header file (``pdf_header_file``) and of the form.
SHORTHAND_FIELDS: tuple[str, ...] = ("github", "linkedin", "website")
#: 0.1.11.3 item 6: the form's optional "Work authorization" text (e.g. "H-1B, requires sponsorship").  It prints as an
#: item of this one PDF's header's contact line (item 15: after the location) and, like the other form values, is
#: never stored: not in the master, a job's resume markdown or JSON, or the resumes folder.  Sponsorship stays a label
#: for jobs; this is only what the user prints.
WORK_AUTHORIZATION_FIELD = "work_authorization"
#: 0.1.11.3 item 13: the form's optional extra links (the rows a header file's ``links`` fill): a list of
#: ``{"label", "url"}``.  The URL prints in the contact line like the form's own link fields; the label names the row.
LINKS_FIELD = "links"
MAX_LINKS = 6
#: 0.1.11.3 item 15: the order of the PDF header's ONE contact line (``form_header``), its items joined with " | ".
CONTACT_ORDER: tuple[str, ...] = ("location", WORK_AUTHORIZATION_FIELD, "github", "website", LINKS_FIELD, "linkedin", "link", "email", "phone")
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
    #: 0.1.11.5 PH: not a person's header but the job page preview's stand-in for one (``resume_pdf.placeholder_header``):
    #: set in a lighter grey, and never printed in a PDF (``resume_pdf._render`` refuses it).
    placeholder: bool = False


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


_WWW = re.compile(r"\Awww\.", re.IGNORECASE)
_GITHUB_ID = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_LINKEDIN_ID = re.compile(r"\A[^\s/?#@]{1,100}\Z")
_HOST = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?:[/?#]\S*)?\Z")


class ShorthandError(ValueError):
    """A shorthand link field does not hold an id (or a site address); the message names the field and the rule, never the value."""


def _bare(value: str) -> str:
    """``value`` without a scheme, ``www.``, a query, a fragment or a trailing slash: what an id is read from."""

    return re.split(r"[?#]", _WWW.sub("", _SCHEME.sub("", value)), maxsplit=1)[0].rstrip("/")


def shorthand_value(key: str, value: str) -> str:
    """What a ``SHORTHAND_FIELDS`` field holds, normalised (0.1.11.3 item 16): the id alone for ``github`` and
    ``linkedin`` (``github.com/<id>``, ``linkedin.com/in/<id>`` or the full URL is read as the id), the site address
    without a scheme or ``www.`` for ``website``.  An empty value stays empty.  ``ShorthandError`` otherwise.

    The ONE reader of the shorthand: the header file, the Generate PDF form's values and Save all go through it."""

    value = value.strip()
    if not value:
        return ""
    bare = _bare(value)
    if key == "github":
        account = re.sub(r"\Agithub\.com/", "", bare, flags=re.IGNORECASE).lstrip("@")
        if not _GITHUB_ID.fullmatch(account):
            raise ShorthandError('github must be your GitHub id alone, like "your-id" (your GitHub profile address also works)')
        return account
    if key == "linkedin":
        account = re.sub(r"\A(?:[a-z]{2,3}\.)?linkedin\.com/", "", bare, flags=re.IGNORECASE)
        account = re.sub(r"\Ain/", "", account, flags=re.IGNORECASE).lstrip("@")
        if not _LINKEDIN_ID.fullmatch(account):
            raise ShorthandError('linkedin must be your LinkedIn id alone, like "your-id" (your LinkedIn profile address also works)')
        return account
    site = _WWW.sub("", _SCHEME.sub("", value)).rstrip("/")
    if not _HOST.fullmatch(site):
        raise ShorthandError('website must be a site address, like "example.com"')
    return site


def shorthand_link(key: str, value: str) -> str:
    """The link a ``SHORTHAND_FIELDS`` value names, as ``host/path`` (no scheme: the header adds ``https://`` for
    the target): ``github.com/<id>``, ``linkedin.com/in/<id>``, the site.  Empty for an empty value."""

    held = shorthand_value(key, value)
    if not held:
        return ""
    return {"github": "github.com/", "linkedin": "linkedin.com/in/"}.get(key, "") + held


def parse_header_form(raw: object) -> dict[str, object]:
    """The form's values, trimmed, or ``HeaderFormError``.  Pure: no I/O, never logs.

    ``raw`` is an object of strings keyed by ``HEADER_FIELDS`` (each optional, at most ``MAX_VALUE``
    characters, one line), plus the optional ``links``: at most ``MAX_LINKS`` objects ``{"label", "url"}``
    (0.1.11.3 item 13; a row with an empty url is dropped).  ``github`` and ``linkedin`` are an id, ``website`` a
    site address (item 16; an address pasted there is read as the id: ``shorthand_value``).  A value there that is
    not one is kept as typed and prints as the plain link it was before item 16 (``form_header``): the form never
    refuses a PDF over it.  Errors name the field and the rule, never the value."""

    if type(raw) is not dict:
        raise HeaderFormError("wrong_type", "header must be an object of strings: " + ", ".join(HEADER_FIELDS))
    if any(key not in HEADER_FIELDS and key != LINKS_FIELD for key in raw):
        raise HeaderFormError("unknown_key", "header has an unknown field (allowed: " + ", ".join((*HEADER_FIELDS, LINKS_FIELD)) + ")")
    values: dict[str, object] = {key: _form_text(raw, key, f"header.{key}") for key in HEADER_FIELDS}
    for key in SHORTHAND_FIELDS:
        try:
            values[key] = shorthand_value(key, str(values[key]))
        except ShorthandError:
            pass  # not an id: kept as typed, printed as a plain link
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
    """A link as the header prints it (0.1.11.3 item 16): the text has no ``https://`` and no ``www.``; the
    target is the full URL (``https://`` added when none was given), so the printed text is clickable."""

    bare = _SCHEME.sub("", value)
    return ContactItem(_WWW.sub("", bare).rstrip("/"), value if _SCHEME.match(value) else "https://" + value)


def link_key(value: str) -> str:
    """What two spellings of one link share: no scheme, no ``www.``, no trailing slash, lower case."""

    return _WWW.sub("", _SCHEME.sub("", value.strip())).rstrip("/").casefold()


def form_header(values: Mapping[str, object], title: str = "") -> PdfHeader:
    """The PDF header from the form's values (``parse_header_form``) and the saved per-profile title.

    COMPACT (0.1.11.3 item 15): every value but the name is one item of ONE contact line, in ``CONTACT_ORDER``
    (location, work authorization, the links, email, phone).  Item 16: a link prints without ``https://`` or
    ``www.`` and is clickable, and the same link given twice prints once.  A field left empty adds no item, so no
    separator stands for it."""

    def text(key: str) -> str:
        value = values.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    contact: list[ContactItem] = []
    linked: set[str] = set()

    def link(value: str) -> None:
        if value.strip() and link_key(value) not in linked:  # one link named twice (a shorthand and a links row) prints once
            linked.add(link_key(value))
            contact.append(_link_item(value.strip()))

    for key in CONTACT_ORDER:
        if key == LINKS_FIELD:
            for row in values.get(LINKS_FIELD, ()):  # type: ignore[union-attr]
                link(row["url"])
        elif not text(key):
            continue
        elif key == "email":
            contact.append(ContactItem(text(key), "mailto:" + text(key)))
        elif key in SHORTHAND_FIELDS:
            try:
                link(shorthand_link(key, text(key)))
            except ShorthandError:
                link(text(key))  # not an id: the address as typed
        elif key == "link":
            link(text(key))
        else:
            contact.append(ContactItem(text(key), None))
    return PdfHeader(values.get("name", ""), _clean(title), tuple(contact))  # type: ignore[arg-type]


def profile_title(settings: DisplaySettings | None, profile_id: str | None) -> str:
    return settings.titles.get(profile_id or "", "") if settings is not None else ""


__all__ = [
    "CONTACT_ORDER",
    "ContactItem",
    "DisplaySettings",
    "HEADER_FIELDS",
    "SHORTHAND_FIELDS",
    "ShorthandError",
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
    "link_key",
    "shorthand_link",
    "shorthand_value",
    "load_display",
    "normalize",
    "parse_header_form",
    "profile_title",
    "save_display",
    "valid_spacing",
]
