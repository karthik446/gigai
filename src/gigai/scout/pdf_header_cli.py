"""0.1.11.4 C2: the header of a PDF a COMMAND makes, from the user's own header file.  One reading, two commands.

``gigai scout resume pdf`` and ``gigai scout cover-letter pdf`` put the same compact header (the name and ONE
contact line) on their PDF, from the same file, by the same rules.  This module is those rules, once:

- the flags: ``--header FILE`` needs ``--out FILE`` and does not go with ``--no-header`` (``check_flags``);
- the file: ``--header FILE``, else the default file (``pdf_header_file.default_path``) when ``--out`` is given and
  that file is there; ``--no-header`` reads nothing at all;
- the precedence: the file, then the profile's sponsorship answer for the ``work_authorization`` line when the file
  has no such key (nobody edits a form here);
- the refusals, each one plain sentence that names a field and a rule, never a value: a missing ``--header`` file,
  an invalid one, one whose values are all still ``REPLACE`` placeholders, one with no name.

``header_for_pdf`` is the ONE place a command reads the header file
(``tests/behaviors/scout_find_jobs/test_pdf_header_file_privacy.py``).  The values it returns go into one PDF and
nowhere else: nothing here writes, logs or prints, and ``PdfHeaderChoice`` keeps them out of its ``repr``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class PdfHeaderRefusal(ValueError):
    """The header cannot be made; ``code`` is the CLI error code.  The message never holds a value of the file."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PdfHeaderChoice:
    """What one command's PDF gets for a header."""

    #: The Generate PDF form's values for this ONE PDF, or ``None``: no header.  Never in a ``repr``.
    form: dict[str, object] | None = field(default=None, repr=False)
    #: The file the header came from, as the user types it; ``None`` when the PDF has no header.
    header_file: str | None = None
    #: One plain sentence about the file (other users can read it; placeholders were skipped; it was not used), or ``None``.
    header_note: str | None = None
    #: The default file is not there (and no ``--header`` named another): the one plain sentence that says where it goes.
    missing: str | None = None


def check_flags(header_value: Path | None, *, no_header: bool, out_file: Path | None) -> None:
    """``PdfHeaderRefusal`` (``invalid_value``) for ``--header`` with ``--no-header``, or without ``--out``."""

    if header_value is None:
        return
    if no_header:
        raise PdfHeaderRefusal("invalid_value", "--header and --no-header do not go together")
    if out_file is None:
        raise PdfHeaderRefusal("invalid_value", "--header needs --out FILE: a PDF with your name and contact details is never written to the resumes folder")


def header_for_pdf(header_value: Path | None, *, no_header: bool, out_file: Path | None, home_root: Path, target: Path | None) -> PdfHeaderChoice:
    """The header of the PDF this command is about to make; ``PdfHeaderRefusal`` when the file cannot make one.

    ``target`` is the Scout folder whose sponsorship answer may give the ``work_authorization`` line (``None``:
    no such answer is read).  Reads the header file once and only here; reads nothing with ``no_header``."""

    if no_header:
        return PdfHeaderChoice()
    from . import pdf_header_file
    from .find_jobs.resume_input import read_config_preferences

    found = pdf_header_file.read_header_file(header_value if header_value is not None else pdf_header_file.default_path(home_root))
    wanted = header_value is not None or (out_file is not None and found.state != pdf_header_file.STATE_MISSING)
    form: dict[str, object] | None = None
    problem = None if found.filled else found.message
    if found.filled and wanted:
        try:
            # Nobody edits a form here: the file, then the profile's sponsorship answer for the work authorization line.
            form = pdf_header_file.render_form(found, visa_required=read_config_preferences(target)[0] if target is not None else False)
        except pdf_header_file.HeaderFileError as exc:
            problem = str(exc)
    if wanted and problem is not None:
        hint = "" if header_value is not None else " (--no-header makes the PDF without a header)"
        raise PdfHeaderRefusal(pdf_header_file.FAILURE_CODES.get(found.state, "header_file_invalid"), problem + hint)
    if form is not None:
        # 0.1.11.3 item 14: REPLACE: placeholders are skipped, never printed; the note names their fields.
        return PdfHeaderChoice(form, found.shown, " ".join(note for note in (found.warning, found.notice) if note) or None)
    if found.state != pdf_header_file.STATE_MISSING:
        # The default file is there but this PDF goes to the resumes folder, which never holds contact details.
        return PdfHeaderChoice(header_note=f"Your header file ({found.shown}) was not used: pass --out FILE to make the PDF with your name and contact details.")
    return PdfHeaderChoice(missing=found.message)


__all__ = ["PdfHeaderChoice", "PdfHeaderRefusal", "check_flags", "header_for_pdf"]
