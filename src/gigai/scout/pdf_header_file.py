"""0.1.11.3 item 13: the header file -- the user's own name and contact details for a PDF, in a file THEY keep.

GigAI stores no name or contact details (0110-046).  A person who does not want to type them into the
Generate PDF form every time keeps them in one JSON file of their own, outside GigAI's store::

    ~/Documents/GigAI/header.json      (a GigAI home other than ~/.gigai: <home>/header.json)

beside the resumes folder, never inside it (that folder never holds contact details).  The file::

    {
      "name": "Jane Example",
      "email": "jane@example.com",
      "phone": "555-0100",
      "location": "Springfield, IL",
      "github": "jane-example",
      "linkedin": "jane-example",
      "website": "jane.example.com",
      "links": [{"label": "Talks", "url": "example.com/talks"}],
      "work_authorization": "VISA: H1B"
    }

Every field is optional.  The PDF header is the name and ONE line (0.1.11.3 items 15/16): location,
``work_authorization`` as written, the links, email, phone.

SHORTHAND (item 16).  ``github`` and ``linkedin`` take just the id (``github.com/<id>``,
``linkedin.com/in/<id>``); the same thing written as ``github.com/<id>`` or as a full URL is accepted and
read as the id.  ``website`` takes a host or a URL.  Each prints without ``https://`` or ``www.`` and is a
clickable link to the full URL.  ``links`` keeps working beside them; a link both name prints once.  A
shorthand value that still starts with ``REPLACE`` is a placeholder like any other (item 14, below).

**GigAI only READS it, at PDF time.**  ``read_header_file`` is called from exactly two places: the
Generate PDF form's prefill (``api/pdf_header.py``, the browser page's own route) and ``gigai scout
resume pdf`` when it renders a PDF with a header.  The values go into that one form or that one PDF
and nowhere else: this module writes nothing, logs nothing, and its errors and warnings name a field
and a rule, never a value.  Nothing here is imported by the store, the journal, a record, a brief, a
suggestion or a model prompt (``tests/behaviors/scout_find_jobs/test_pdf_header_file_privacy.py``).

Precedence, wherever a header is put together: what the person edits in the form, then this file,
then the profile's sponsorship answer (the ``work_authorization`` line only, and only when the file
does not have that key at all: a key that is there and empty means "no line").

0.1.11.3 item 14: a value that starts with ``REPLACE`` is a template placeholder nobody filled in yet.  It
never fills the form and never prints in a PDF, and neither does an empty value: the real fields are used,
the placeholders are skipped and the answer names them (field names only).  A file whose name is missing
or a placeholder says "has no name yet": the form flags it and ``gigai scout resume pdf`` refuses.  The Generate PDF form may also WRITE this file, once, when the
person presses "Save these details to <path>": that is ``pdf_header_save``, not this module, which still
only reads.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .resume_display import HEADER_FIELDS, MAX_VALUE, WORK_AUTHORIZATION_FIELD, HeaderFormError, link_key, parse_header_form
from .resumes_folder import default_folder
from .target_resolution import _display_path

RESPONSE_SCHEMA = "scout-pdf-header-prefill:1"
FILE_NAME = "header.json"
#: The file's keys.  ``links`` is a list of ``{"label", "url"}``.
FILE_FIELDS: tuple[str, ...] = ("name", "email", "phone", "location", "github", "linkedin", "website", "links", "work_authorization")
#: Item 16: the shorthand link fields, in the order their links print.
SHORTHAND_FIELDS: tuple[str, ...] = ("github", "linkedin", "website")
LINK_FIELDS: tuple[str, ...] = ("label", "url")
MAX_LINKS = 6
#: The file is a handful of short lines; anything larger is not this file.
MAX_BYTES = 16 * 1024

STATE_FILLED = "filled"
STATE_MISSING = "missing"
STATE_INVALID = "invalid"
#: 0.1.11.3 item 14: the file is there and valid, and every value in it is still a template placeholder.
STATE_PLACEHOLDER = "placeholder"
#: A value that starts with this is a template's placeholder, not the person's own: never filled, never printed.
PLACEHOLDER_PREFIX = "REPLACE"

#: ``gigai scout resume pdf``: the error code of a file that cannot make the header, by its state.
FAILURE_CODES = {STATE_MISSING: "header_file_missing", STATE_INVALID: "header_file_invalid", STATE_PLACEHOLDER: "header_file_placeholders"}

#: What the ``work_authorization`` line starts as when the profile says sponsorship is needed and the header
#: file does not say otherwise.  The same sentence as the form's (``ui/src/generatePdfModel.js``).
SPONSORSHIP_DEFAULT = "Requires visa sponsorship"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PLAIN_KEY = re.compile(r"\A[A-Za-z_]{1,30}\Z")
_LINKEDIN = re.compile(r"(?:\A|[/.@])linkedin\.com(?:/|\Z)", re.IGNORECASE)
_EXAMPLE = (
    '{"name": "...", "email": "...", "phone": "...", "location": "...", "github": "your-id", "linkedin": "your-id", '
    '"links": [{"label": "Website", "url": "..."}], "work_authorization": "..."}'
)
_SCHEME = re.compile(r"\A[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_WWW = re.compile(r"\Awww\.", re.IGNORECASE)
_GITHUB_ID = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_LINKEDIN_ID = re.compile(r"\A[^\s/?#@]{1,100}\Z")
_HOST = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?:[/?#]\S*)?\Z")


def default_path(home_root: Path) -> Path:
    """``~/Documents/GigAI/header.json`` for the default GigAI home, ``<home>/header.json`` for any other.

    Beside the default resumes folder (``resumes_folder.default_folder``), never inside it."""

    return default_folder(home_root).parent / FILE_NAME


@dataclass(frozen=True)
class HeaderFile:
    """What one read of the header file found.  ``values`` never appears in a ``repr``."""

    path: Path
    state: str
    #: One plain sentence: what was read, or what is wrong and where the file goes.  Never a value.
    message: str
    #: Set when the file can be read by other users of the computer.  Never a value.
    warning: str | None = None
    #: The Generate PDF form's values (``resume_display.HEADER_FIELDS`` strings and ``links``); ``None`` unless filled.
    values: dict[str, object] | None = field(default=None, repr=False)
    #: Whether the file has a ``work_authorization`` key at all (an empty one means "no line").
    has_work_authorization: bool = False
    #: The fields skipped because their value is still a ``REPLACE`` placeholder (names of ``FILE_FIELDS`` only, never a value).
    placeholders: tuple[str, ...] = ()
    #: One plain sentence about those placeholders, or ``None``.  Never a value.
    notice: str | None = None
    #: "<path> has no name yet." when the file is valid and its name is missing, empty or a placeholder; else ``None``.
    name_note: str | None = None

    @property
    def shown(self) -> str:
        """The path the way the person types it (``~/Documents/GigAI/header.json``)."""

        return _display_path(self.path)

    @property
    def filled(self) -> bool:
        return self.state == STATE_FILLED


class _Invalid(ValueError):
    """The file's content is not usable; the message names a field and a rule, never a value."""


def _text(raw: dict[str, object], key: str, where: str = "") -> str:
    value = raw.get(key, "")
    name = f"{where}{key}"
    if not isinstance(value, str):
        raise _Invalid(f"{name} must be text in double quotes")
    if len(value) > MAX_VALUE:
        raise _Invalid(f"{name} is longer than {MAX_VALUE} characters")
    if _CONTROL.search(value):
        raise _Invalid(f"{name} must be one line")
    return value.strip()


def _unknown(keys: object, allowed: tuple[str, ...], where: str = "") -> None:
    for key in keys:  # type: ignore[attr-defined]
        if key not in allowed:
            # A key is named only when it reads as a field name: a value typed where a key goes is not echoed.
            named = f"{where}{key}" if isinstance(key, str) and _PLAIN_KEY.fullmatch(key) else "a field"
            raise _Invalid(f"{named} is not a field of this file (the fields: {', '.join(allowed)})")


def _is_placeholder(value: str) -> bool:
    return value.startswith(PLACEHOLDER_PREFIX)


def _bare(value: str) -> str:
    """``value`` without a scheme, ``www.``, a query, a fragment or a trailing slash: what an id is read from."""

    return re.split(r"[?#]", _WWW.sub("", _SCHEME.sub("", value)), maxsplit=1)[0].rstrip("/")


def _shorthand(key: str, value: str) -> str | None:
    """The link a shorthand value names, as ``host/path`` (no scheme: the header adds ``https://`` for the
    target); ``None`` for an empty value or a placeholder.  ``_Invalid`` names the field and the rule only."""

    if not value or _is_placeholder(value):
        return None
    bare = _bare(value)
    if key == "github":
        account = re.sub(r"\Agithub\.com/", "", bare, flags=re.IGNORECASE).lstrip("@")
        if not _GITHUB_ID.fullmatch(account):
            raise _Invalid('github must be your GitHub id alone, like "your-id" (github.com/your-id also works)')
        return f"github.com/{account}"
    if key == "linkedin":
        account = re.sub(r"\A(?:[a-z]{2,3}\.)?linkedin\.com/", "", bare, flags=re.IGNORECASE)
        account = re.sub(r"\Ain/", "", account, flags=re.IGNORECASE).lstrip("@")
        if not _LINKEDIN_ID.fullmatch(account):
            raise _Invalid('linkedin must be your LinkedIn id alone, like "your-id" (linkedin.com/in/your-id also works)')
        return f"linkedin.com/in/{account}"
    if not _HOST.fullmatch(_WWW.sub("", _SCHEME.sub("", value)).rstrip("/")):
        raise _Invalid('website must be a site address, like "example.com"')
    return _WWW.sub("", _SCHEME.sub("", value)).rstrip("/")


def form_values(raw: object) -> tuple[dict[str, object], bool, tuple[str, ...]]:
    """``(the Generate PDF form's values, whether work_authorization is set at all, the placeholder fields)`` from the file's JSON; ``_Invalid`` otherwise.

    A link labelled LinkedIn (or to linkedin.com) fills the form's LinkedIn field, once; every other link is a row
    of ``links`` under its own label.  The shorthand fields (``_shorthand``, item 16) come first: ``linkedin`` fills
    that same field, ``github`` and ``website`` are rows labelled GitHub and Website; a ``links`` row that names a
    link already there is left out.  A value that starts with ``REPLACE`` is a placeholder: its field stays empty
    (a link whose label or url is one is left out; a shorthand that is one adds no link) and is named in the third
    item; a placeholder ``work_authorization`` counts as a key that is not there.  A link with an empty url is left
    out too.  Pure: no I/O, never logs."""

    if type(raw) is not dict:
        raise _Invalid("the file must hold one JSON object, like " + _EXAMPLE)
    _unknown(raw, FILE_FIELDS)
    values: dict[str, object] = {key: "" for key in HEADER_FIELDS}
    placeholders: list[str] = []
    for key in ("name", "email", "phone", "location", WORK_AUTHORIZATION_FIELD):
        values[key] = _text(raw, key)
        if _is_placeholder(str(values[key])):
            values[key] = ""
            placeholders.append(key)
    links = raw.get("links", [])
    if type(links) is not list:
        raise _Invalid('links must be a list, like [{"label": "LinkedIn", "url": "..."}]')
    if len(links) > MAX_LINKS:
        raise _Invalid(f"links holds more than {MAX_LINKS} links")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, label in (("github", "GitHub"), ("linkedin", "LinkedIn"), ("website", "Website")):
        given = _text(raw, key)
        if _is_placeholder(given):
            placeholders.append(key)
            continue
        url = _shorthand(key, given)
        if url is None or link_key(url) in seen:
            continue
        seen.add(link_key(url))
        if key == "linkedin":
            values["linkedin"] = url
        else:
            rows.append({"label": label, "url": url})
    for number, item in enumerate(links, 1):
        where = f"links[{number}]."
        if type(item) is not dict:
            raise _Invalid(f'links[{number}] must be an object, like {{"label": "LinkedIn", "url": "..."}}')
        _unknown(item, LINK_FIELDS, where)
        label, url = _text(item, "label", where), _text(item, "url", where)
        if not url:
            continue
        if _is_placeholder(url) or _is_placeholder(label):
            if "links" not in placeholders:
                placeholders.append("links")
            continue
        if link_key(url) in seen:
            continue  # the same link as a shorthand field or an earlier row: once
        seen.add(link_key(url))
        if not values["linkedin"] and (label.casefold() == "linkedin" or _LINKEDIN.search(url)):
            values["linkedin"] = url
        else:
            rows.append({"label": label or "Link", "url": url})
    if len(rows) > MAX_LINKS:
        raise _Invalid(f"the file names more than {MAX_LINKS} links besides LinkedIn")
    values["links"] = rows
    has_line = WORK_AUTHORIZATION_FIELD in raw and WORK_AUTHORIZATION_FIELD not in placeholders
    # In the file's own order, whatever order they were found in.
    return values, has_line, tuple(key for key in FILE_FIELDS if key in placeholders)


def placeholder_notice(shown: str, placeholders: tuple[str, ...], *, all_of_it: bool) -> str:
    """The one sentence about a file that still holds ``REPLACE`` placeholders: field names only, never a value."""

    sentence = f"{shown} still has placeholder values: replace the REPLACE: fields (or save your details here)."
    return sentence if all_of_it else f"{sentence} Skipped: {', '.join(placeholders)}."


def _where_it_goes(shown: str) -> str:
    return f"To fill the PDF header from a file, keep your details in {shown}: " + _EXAMPLE + ". GigAI only reads it when it makes a PDF."


def _others_can_read(mode: int) -> bool:
    return bool(stat.S_IMODE(mode) & (stat.S_IRGRP | stat.S_IROTH))


def read_header_file(path: Path) -> HeaderFile:
    """Read ``path`` once.  Never raises, never writes, never logs; every message is free of the file's values."""

    path = Path(path).expanduser()
    shown = _display_path(path)
    try:
        if not path.exists():
            return HeaderFile(path, STATE_MISSING, f"There is no header file at {shown}. " + _where_it_goes(shown))
        if not path.is_file():
            return HeaderFile(path, STATE_INVALID, f"{shown} is not a file. " + _where_it_goes(shown))
        info = path.stat()
        if info.st_size > MAX_BYTES:
            return HeaderFile(path, STATE_INVALID, f"{shown} is larger than {MAX_BYTES} bytes, so it was not read. " + _where_it_goes(shown))
        data = path.read_bytes()
    except OSError:
        return HeaderFile(path, STATE_INVALID, f"{shown} could not be read (check that you may open it). " + _where_it_goes(shown))
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        # A JSON error's own text can quote the file: say only that it is not JSON.
        return HeaderFile(path, STATE_INVALID, f"{shown} is not valid JSON, so it was not used. It should look like " + _EXAMPLE + ".")
    try:
        values, has_line, placeholders = form_values(raw)
    except _Invalid as exc:
        return HeaderFile(path, STATE_INVALID, f"{shown} was not used: {exc}.")
    real = any(values[key] for key in values)
    if placeholders and not real:
        # Nothing of the person's own in it yet: the form is not filled and no PDF is made from it.
        notice = placeholder_notice(shown, placeholders, all_of_it=True)
        return HeaderFile(path, STATE_PLACEHOLDER, notice, placeholders=placeholders, notice=notice, name_note=f"{shown} has no name yet.")
    warning = None
    if _others_can_read(info.st_mode):
        warning = f"{shown} can be read by other users of this computer. To keep it to yourself: chmod 600 {shown}"
    notice = placeholder_notice(shown, placeholders, all_of_it=False) if placeholders else None
    name_note = None if values["name"] else f"{shown} has no name yet."
    return HeaderFile(path, STATE_FILLED, f"Filled from {shown}", warning, values, has_line, placeholders, notice, name_note)


def with_sponsorship_default(values: dict[str, object], *, has_work_authorization: bool, visa_required: bool) -> dict[str, object]:
    """``values`` with the ``work_authorization`` line from the profile's sponsorship answer when the file has no such key."""

    if has_work_authorization or not visa_required:
        return values
    return {**values, WORK_AUTHORIZATION_FIELD: SPONSORSHIP_DEFAULT}


class HeaderFileError(ValueError):
    """The file was read but cannot make a PDF header; the message names a field and a rule, never a value."""


def render_form(found: HeaderFile, *, visa_required: bool) -> dict[str, object]:
    """The Generate PDF form's values for a PDF made WITHOUT the form (``gigai scout resume pdf``), from a filled file.

    Nobody edits them here, so: the file, then the profile's sponsorship answer.  ``HeaderFileError`` when the
    file has no name (the form's own rule: a header needs one)."""

    values = found.values or {}
    if not values.get("name"):
        problem = f"{found.shown} has no name yet; a PDF header needs one"
        raise HeaderFileError(f"{problem}. {found.notice.rstrip('.')}" if found.notice else problem)
    try:
        return parse_header_form(with_sponsorship_default(values, has_work_authorization=found.has_work_authorization, visa_required=visa_required))
    except HeaderFormError as exc:
        raise HeaderFileError(f"{found.shown} was not used: {exc}") from None


def prefill_response(found: HeaderFile) -> dict[str, object]:
    """What the Generate PDF form's prefill route answers (``api/pdf_header.py``): the browser page's own, nobody else's."""

    return {
        "schema_version": RESPONSE_SCHEMA,
        "state": found.state,
        "shown": found.shown,
        "message": found.message,
        "warning": found.warning,
        "has_work_authorization": found.has_work_authorization,
        "values": found.values,
        "placeholders": list(found.placeholders),
        "notice": found.notice,
        "name_note": found.name_note,
    }


__all__ = [
    "FAILURE_CODES",
    "FILE_FIELDS",
    "FILE_NAME",
    "HeaderFile",
    "HeaderFileError",
    "LINK_FIELDS",
    "MAX_BYTES",
    "MAX_LINKS",
    "PLACEHOLDER_PREFIX",
    "RESPONSE_SCHEMA",
    "SHORTHAND_FIELDS",
    "SPONSORSHIP_DEFAULT",
    "STATE_FILLED",
    "STATE_INVALID",
    "STATE_MISSING",
    "STATE_PLACEHOLDER",
    "default_path",
    "form_values",
    "placeholder_notice",
    "prefill_response",
    "read_header_file",
    "render_form",
    "with_sponsorship_default",
]
