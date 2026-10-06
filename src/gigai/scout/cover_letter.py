"""0.1.11.4 C2: a cover letter's markdown to a ONE-PAGE PDF, locally and deterministically.

``gigai scout cover-letter pdf --in FILE --out FILE``.  The letter is the agent's (or the user's) own file: plain
paragraphs.  GigAI writes no letter and calls no model for one; this module only sets the text on a page, in the
resume's template family (``data/resume/letter.typ``: the same page, fonts, colours and compact header as
``resume.typ``), through the resume renderer's own Typst calls (``resume_pdf._compile`` / ``_end``).

THE LETTER FILE (``parse_letter``):

* a blank line ends a paragraph;
* lines with no blank line between them are one paragraph: a hard-wrapped paragraph is joined into one, and a
  paragraph whose every line is short (at most ``KEEP_LINES_MAX`` characters: a greeting, a sign-off with the
  name under it) keeps its lines;
* ``- `` lines are bullets; a ``# `` line prints as a paragraph without its hashes;
* never printed: a front-matter block (``---`` ... ``---`` at the top), ``<!-- ... -->`` comments, a rule line;
* text prints literally (inline markdown is not interpreted).

Only the file named with ``--in`` is read: a claims trace beside it (``<name>.claims.md``) is never opened and
never printed, and a claims trace given AS the letter is refused (``looks_like_claims_trace``).

ONE PAGE (``render_letter_pdf``).  The letter is set at spacing 1.0.  When it runs past one page the spacing goes
down a step at a time to ``LETTER_FLOOR`` (the gaps between paragraphs and the line height; never the type size).
A letter that needs a second page even there is rendered as its layout says, and ``RenderedPdf.note`` is one plain
sentence telling the user to shorten it: a second page is never silent.

Nothing here calls a model, the network or a logger, and nothing here writes: the header's values live only in
this call's arguments.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from datetime import datetime
from importlib import resources
from pathlib import Path

from .resume_pdf import HEADERLESS_LINES, MAX_MARKDOWN_BYTES, MAX_MARKDOWN_LINES, SPACING_MIN, RenderedPdf, _GRID, _compile, _data, _end, pdf_header

TEMPLATE = "letter.typ"
#: The spacing a letter is set at when it fits, and the tightest the fit may choose (the resume's own floor).
LETTER_SCALE = 1.0
LETTER_FLOOR = SPACING_MIN
#: A paragraph whose every line is at most this long keeps its line breaks (a greeting, a sign-off, an address block).
KEEP_LINES_MAX = 60
#: The file name a claims trace ends with (the cover-letter skill writes it beside the letter).
CLAIMS_SUFFIX = ".claims.md"
DOC_TITLE = "Cover letter"

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HASHES = re.compile(r"\A#{1,6}\s+")
_BULLET = re.compile(r"\A[-*•]\s+(.*)\Z")
_RULE = re.compile(r"\A(?:-{3,}|\*{3,}|_{3,})\Z")
_FRONT_MATTER_LINES = 40


class CoverLetterError(ValueError):
    """The letter cannot be made into a PDF; ``code`` is the CLI error code.

    Messages name a line number and the rule, never the line's text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def looks_like_claims_trace(name: str) -> bool:
    """Whether a file name is a claims trace's (``<company>-<role>-<date>.claims.md``), which is never printed."""

    return name.casefold().endswith(CLAIMS_SUFFIX)


def _without_front_matter(lines: list[str]) -> list[str]:
    if not lines or lines[0].strip() != "---":
        return lines
    for index, line in enumerate(lines[1:_FRONT_MATTER_LINES], 1):
        if line.strip() == "---":
            return lines[index + 1:]
    return lines


def parse_letter(markdown: str) -> list[dict[str, object]]:
    """The letter's blocks, ``{"lines": [text, ...], "bullet": bool}`` each, in order; ``CoverLetterError`` otherwise.

    The format is the module text's.  A block with more than one line prints them as separate lines."""

    if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise CoverLetterError("letter_too_large", f"the letter is larger than {MAX_MARKDOWN_BYTES} bytes")
    raw_lines = markdown.splitlines()
    if len(raw_lines) > MAX_MARKDOWN_LINES:
        raise CoverLetterError("letter_too_large", f"the letter has more than {MAX_MARKDOWN_LINES} lines")
    for number, raw in enumerate(raw_lines, 1):
        if _CONTROL.search(raw):
            raise CoverLetterError("letter_invalid", f"line {number}: control characters are not allowed")
    # Comments go first (one may span lines); a line that held only a comment then reads as text that is not there.
    kept = _COMMENT.sub("", "\n".join(_without_front_matter(raw_lines))).splitlines()
    blocks: list[dict[str, object]] = []
    open_lines: list[str] = []

    def close() -> None:
        if not open_lines:
            return
        if len(open_lines) > 1 and all(len(line) <= KEEP_LINES_MAX for line in open_lines):
            blocks.append({"lines": list(open_lines), "bullet": False})
        else:
            blocks.append({"lines": [" ".join(open_lines)], "bullet": False})
        open_lines.clear()

    last_bullet = False
    for raw in kept:
        line = " ".join(raw.split())
        if not line or _RULE.match(line):
            close()
            last_bullet = False
            continue
        bullet = _BULLET.match(line)
        if bullet:
            close()
            if bullet.group(1):
                blocks.append({"lines": [bullet.group(1)], "bullet": True})
                last_bullet = True
            continue
        if last_bullet:
            # A plain line right under a bullet continues it (a hard wrap).
            held: list[str] = blocks[-1]["lines"]  # type: ignore[assignment]
            held[0] = f"{held[0]} {line}"
            continue
        text = _HASHES.sub("", line)
        if text:
            open_lines.append(text)
    close()
    if not blocks:
        raise CoverLetterError("letter_invalid", "the letter is empty: write its paragraphs as plain text, with a blank line between them")
    return blocks


def word_count(blocks: list[dict[str, object]]) -> int:
    """The words the PDF prints."""

    return sum(len(str(line).split()) for block in blocks for line in block["lines"])  # type: ignore[union-attr]


def too_long_note(pages: int) -> str:
    """What a person reads when the letter does not fit one page: plain words, ASCII."""

    return (
        f"This letter takes {pages} pages even with the tightest spacing; a cover letter is one page. "
        "Shorten it (about 330-380 words fit), then make the PDF again."
    )


def render_letter_pdf(markdown: str, form: dict[str, object] | None, *, timestamp: datetime) -> RenderedPdf:
    """The letter's PDF, its page count and the spacing used.

    ``form`` is the header's values for this ONE PDF (``pdf_header_cli.header_for_pdf``: the Generate PDF form's
    fields, from the user's own file); ``None``: no header, its block kept blank.  The header is the resume's
    compact one, with no title line.  ``RenderedPdf.note`` is ``too_long_note`` when no spacing puts the letter on
    one page; else ``None``."""

    blocks = parse_letter(markdown)
    header = pdf_header(None, None, form)
    root = resources.files("gigai.scout").joinpath("data", "resume")
    with ExitStack() as stack:
        directory = str(stack.enter_context(resources.as_file(root)))
        template = (Path(directory) / TEMPLATE).read_bytes()
        # The header's data is the resume's own (the one contact line, empty items left out, the work authorization's place).
        data = {**_data([], header, "", blank_lines=HEADERLESS_LINES), "doc_title": DOC_TITLE, "blocks": blocks}
        del data["sections"]
        scale, pages = LETTER_SCALE, 0
        for candidate in (item for item in _GRID if LETTER_FLOOR <= item <= LETTER_SCALE):  # loosest first
            scale, pages = candidate, _end(template, directory, data, candidate)[0]
            if pages <= 1:
                break
        note = too_long_note(pages) if pages > 1 else None
        return RenderedPdf(_compile(template, directory, data, scale, timestamp), pages, scale, note)


__all__ = [
    "CLAIMS_SUFFIX",
    "DOC_TITLE",
    "KEEP_LINES_MAX",
    "LETTER_FLOOR",
    "LETTER_SCALE",
    "TEMPLATE",
    "CoverLetterError",
    "looks_like_claims_trace",
    "parse_letter",
    "render_letter_pdf",
    "too_long_note",
    "word_count",
]
