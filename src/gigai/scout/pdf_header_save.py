"""0.1.11.3 item 14: "Save these details to <path>" -- the Generate PDF form writes the user's own header file, on their click.

``pdf_header_file`` only reads the file.  This module is the one place GigAI WRITES it, and only when the person
presses the button in the Generate PDF form (``api/pdf_header.py``, Scout's own page only): one explicit write of
what is in the form at that moment, to the same path the form reads (``pdf_header_file.default_path``).

- **Only that file.**  The values go into ``header.json`` and nowhere else: this module logs nothing, keeps nothing,
  and its answers and errors name a field, a rule or the path, never a value.
- **Mode 0600, atomic.**  The content is written to a temporary file in the same folder, created 0600, flushed to
  disk, and then put in place in one step.  A failure at any point leaves the file that was there as it was and
  removes the temporary one.
- **Never over an existing file without a yes.**  Without ``replace`` an existing file answers ``exists`` and is
  not touched (the new file is put in place with a hard link, which cannot overwrite); the form then asks "Replace
  the existing header.json?" and sends ``replace``.
- **Checked by the reader's own rules** (``pdf_header_file.form_values``): the file's fields, one line of at most
  200 characters each, at most 6 links, at most 16 KB.
- **The shorthand** (0.1.11.3 item 16).  The form's GitHub and LinkedIn fields are saved as the id alone
  (``"github": "<id>"``, ``"linkedin": "<id>"``; an address pasted there is saved as its id) and Website as the
  site address; ``links`` holds only the other links.  What is written is what the reader reads back
  (``form_values``), so a saved file fills the form with the same values.  A field left empty is left out of the
  file; ``work_authorization`` alone is written when empty, because there an empty value means "no line".
- **The folder.**  ``~/Documents/GigAI`` (the default GigAI home's folder for this file) is created, 0700, when it
  is not there yet; ``~/Documents`` itself is not.  For any other GigAI home the folder is the home, which exists.
  A folder that is missing or cannot be written is one plain sentence.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .pdf_header_file import FILE_FIELDS, LINK_FIELDS, MAX_BYTES, PLACEHOLDER_PREFIX, WORK_AUTHORIZATION_FIELD, _Invalid, _unknown, form_values
from .target_resolution import _display_path

RESPONSE_SCHEMA = "scout-pdf-header-save:1"
STATE_SAVED = "saved"
STATE_EXISTS = "exists"
STATE_NOT_WRITABLE = "not_writable"
#: What the form asks before a second request that carries ``replace``.
REPLACE_QUESTION = "Replace the existing header.json?"
#: What Save writes: every field of the file, the link shorthand (``github`` / ``linkedin`` / ``website``, item 16)
#: among them.  Also the keys the save request may carry.
SAVED_FIELDS = FILE_FIELDS
_TEXT_FIELDS = tuple(key for key in SAVED_FIELDS if key != "links")


class HeaderSaveError(ValueError):
    """The details cannot be saved as they are; the message names a field and a rule, never a value."""


@dataclass(frozen=True)
class HeaderSave:
    """What one save did.  Holds no value of the file."""

    path: Path
    state: str
    #: One plain sentence: where it was saved, that a file is already there, or why the folder cannot be written.
    message: str

    @property
    def shown(self) -> str:
        return _display_path(self.path)

    def to_json(self) -> dict[str, object]:
        return {"schema_version": RESPONSE_SCHEMA, "state": self.state, "shown": self.shown, "message": self.message}


def file_content(details: object) -> bytes:
    """The bytes of ``header.json`` for the form's ``details``; ``HeaderSaveError`` when the reader would not take them.

    The content is the reader's own reading of ``details`` (``form_values``): the text fields trimmed, ``github`` and
    ``linkedin`` as the id alone and ``website`` as the site address (an address typed there, or a link that is one
    of them, is saved as the shorthand), ``links`` the other links that have a url.  A field left empty is left out;
    ``work_authorization`` is always written (empty means "no line").  Pure: no I/O, never logs."""

    try:
        if type(details) is dict:
            _unknown(details, SAVED_FIELDS)
        values, _, _ = form_values(details)
    except _Invalid as exc:
        raise HeaderSaveError(str(exc)) from None
    assert isinstance(details, dict)
    # What was typed, before the reader skips anything: a placeholder is refused here, never silently dropped.
    typed = [str(details.get(key, "")).strip() for key in _TEXT_FIELDS]
    typed += [str(item.get(name, "")).strip() for item in details.get("links", []) for name in LINK_FIELDS]
    if any(text.startswith(PLACEHOLDER_PREFIX) for text in typed):
        raise HeaderSaveError(f"a value still starts with {PLACEHOLDER_PREFIX}: type your own details first")
    content: dict[str, object] = {}
    for key in SAVED_FIELDS:
        held = values[key]
        if held or key == WORK_AUTHORIZATION_FIELD:
            content[key] = held
    if not any(content.values()):
        raise HeaderSaveError("there is nothing to save: type at least one of your details first")
    data = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_BYTES:
        raise HeaderSaveError(f"the details are larger than {MAX_BYTES} bytes")
    return data


def _put_in_place(temporary: Path, path: Path, *, replace: bool) -> bool:
    """Move the finished temporary file to ``path``; ``False`` (nothing changed) when a file is there and ``replace`` is not set."""

    if replace:
        os.replace(temporary, path)
        return True
    try:
        # A hard link never overwrites: a file that appeared since the check is kept.
        os.link(temporary, path)
    except FileExistsError:
        return False
    return True


def save_header_file(path: Path, details: object, *, replace: bool = False, create_folder: bool = False) -> HeaderSave:
    """Write ``details`` to ``path`` once.  ``HeaderSaveError`` for details the reader would not take; never logs.

    ``create_folder``: make ``path``'s folder (that one folder, 0700) when it is missing."""

    path = Path(path).expanduser()
    shown = _display_path(path)
    data = file_content(details)
    folder = path.parent
    if os.path.lexists(path):
        if path.is_dir():
            return HeaderSave(path, STATE_NOT_WRITABLE, f"{shown} is a folder, so your details were not saved. Move it away and save again.")
        if not replace:
            return HeaderSave(path, STATE_EXISTS, f"There is already a file at {shown}. It was not changed.")
    not_writable = HeaderSave(path, STATE_NOT_WRITABLE, f"Your details were not saved: GigAI cannot write to {_display_path(folder)}. Check that the folder is there and that you may write to it.")
    temporary: Path | None = None
    try:
        if not folder.is_dir():
            if not create_folder:
                return HeaderSave(path, STATE_NOT_WRITABLE, f"Your details were not saved: there is no folder {_display_path(folder)}. Create it and save again.")
            folder.mkdir(mode=0o700)
        # mkstemp creates the file 0600, so the details are never readable by another user, not even for a moment.
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=folder)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        placed = _put_in_place(temporary, path, replace=replace)
    except OSError:
        # An OS error's own text can hold nothing of the file, but it is not plain: say what to check.
        return not_writable
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    if not placed:
        return HeaderSave(path, STATE_EXISTS, f"There is already a file at {shown}. It was not changed.")
    return HeaderSave(path, STATE_SAVED, f"Saved your details to {shown}. GigAI keeps no other copy.")


__all__ = [
    "REPLACE_QUESTION",
    "RESPONSE_SCHEMA",
    "SAVED_FIELDS",
    "STATE_EXISTS",
    "STATE_NOT_WRITABLE",
    "STATE_SAVED",
    "HeaderSave",
    "HeaderSaveError",
    "file_content",
    "save_header_file",
]
