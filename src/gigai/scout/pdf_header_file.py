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
      "links": [{"label": "LinkedIn", "url": "linkedin.com/in/jane-example"}],
      "work_authorization": "VISA: H1B"
    }

Every field is optional.  ``work_authorization`` prints as the header's own last line, as written.

**GigAI only READS it, at PDF time.**  ``read_header_file`` is called from exactly two places: the
Generate PDF form's prefill (``api/pdf_header.py``, the browser page's own route) and ``gigai scout
resume pdf`` when it renders a PDF with a header.  The values go into that one form or that one PDF
and nowhere else: this module writes nothing, logs nothing, and its errors and warnings name a field
and a rule, never a value.  Nothing here is imported by the store, the journal, a record, a brief, a
suggestion or a model prompt (``tests/behaviors/scout_find_jobs/test_pdf_header_file_privacy.py``).

Precedence, wherever a header is put together: what the person edits in the form, then this file,
then the profile's sponsorship answer (the ``work_authorization`` line only, and only when the file
does not have that key at all: a key that is there and empty means "no line").
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .resume_display import HEADER_FIELDS, MAX_VALUE, WORK_AUTHORIZATION_FIELD, HeaderFormError, parse_header_form
from .resumes_folder import default_folder
from .target_resolution import _display_path

RESPONSE_SCHEMA = "scout-pdf-header-prefill:1"
FILE_NAME = "header.json"
#: The file's keys.  ``links`` is a list of ``{"label", "url"}``.
FILE_FIELDS: tuple[str, ...] = ("name", "email", "phone", "location", "links", "work_authorization")
LINK_FIELDS: tuple[str, ...] = ("label", "url")
MAX_LINKS = 6
#: The file is a handful of short lines; anything larger is not this file.
MAX_BYTES = 16 * 1024

STATE_FILLED = "filled"
STATE_MISSING = "missing"
STATE_INVALID = "invalid"

#: What the ``work_authorization`` line starts as when the profile says sponsorship is needed and the header
#: file does not say otherwise.  The same sentence as the form's (``ui/src/generatePdfModel.js``).
SPONSORSHIP_DEFAULT = "Requires visa sponsorship"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PLAIN_KEY = re.compile(r"\A[A-Za-z_]{1,30}\Z")
_LINKEDIN = re.compile(r"(?:\A|[/.@])linkedin\.com(?:/|\Z)", re.IGNORECASE)
_EXAMPLE = '{"name": "...", "email": "...", "phone": "...", "location": "...", "links": [{"label": "LinkedIn", "url": "..."}], "work_authorization": "..."}'


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


def form_values(raw: object) -> tuple[dict[str, object], bool]:
    """``(the Generate PDF form's values, whether work_authorization is set at all)`` from the file's JSON; ``_Invalid`` otherwise.

    A link labelled LinkedIn (or to linkedin.com) fills the form's LinkedIn field, once; every other link is a row
    of ``links`` under its own label.  Pure: no I/O, never logs."""

    if type(raw) is not dict:
        raise _Invalid("the file must hold one JSON object, like " + _EXAMPLE)
    _unknown(raw, FILE_FIELDS)
    values: dict[str, object] = {key: "" for key in HEADER_FIELDS}
    for key in ("name", "email", "phone", "location", WORK_AUTHORIZATION_FIELD):
        values[key] = _text(raw, key)
    links = raw.get("links", [])
    if type(links) is not list:
        raise _Invalid('links must be a list, like [{"label": "LinkedIn", "url": "..."}]')
    if len(links) > MAX_LINKS:
        raise _Invalid(f"links holds more than {MAX_LINKS} links")
    rows: list[dict[str, str]] = []
    for number, item in enumerate(links, 1):
        where = f"links[{number}]."
        if type(item) is not dict:
            raise _Invalid(f'links[{number}] must be an object, like {{"label": "LinkedIn", "url": "..."}}')
        _unknown(item, LINK_FIELDS, where)
        label, url = _text(item, "label", where), _text(item, "url", where)
        if not url:
            raise _Invalid(f"{where}url is empty")
        if not values["linkedin"] and (label.casefold() == "linkedin" or _LINKEDIN.search(url)):
            values["linkedin"] = url
        else:
            rows.append({"label": label or "Link", "url": url})
    values["links"] = rows
    return values, WORK_AUTHORIZATION_FIELD in raw


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
        values, has_line = form_values(raw)
    except _Invalid as exc:
        return HeaderFile(path, STATE_INVALID, f"{shown} was not used: {exc}.")
    warning = None
    if _others_can_read(info.st_mode):
        warning = f"{shown} can be read by other users of this computer. To keep it to yourself: chmod 600 {shown}"
    return HeaderFile(path, STATE_FILLED, f"Filled from {shown}", warning, values, has_line)


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
        raise HeaderFileError(f"{found.shown} has no name; a PDF header needs one")
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
    }


__all__ = [
    "FILE_FIELDS",
    "FILE_NAME",
    "HeaderFile",
    "HeaderFileError",
    "LINK_FIELDS",
    "MAX_BYTES",
    "MAX_LINKS",
    "RESPONSE_SCHEMA",
    "SPONSORSHIP_DEFAULT",
    "STATE_FILLED",
    "STATE_INVALID",
    "STATE_MISSING",
    "default_path",
    "form_values",
    "prefill_response",
    "read_header_file",
    "render_form",
    "with_sponsorship_default",
]
