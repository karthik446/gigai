"""The Scout ATS score: GigAI's own local check of how well a rendered resume PDF reads and matches a posting.

``score(pdf_bytes, source, keywords)`` is pure and local: ``pypdf`` is the one reader, no model, no network.
Three parts, 100 points in all:

* **Parse fidelity (40)**: what the extracted text keeps of the source the PDF was rendered from (words,
  section headings, date ranges, an email, and role lines whose title and dates stay adjacent).  Scout holds
  the source, so this is a diff against known truth, never a guess at fields.
* **Keyword coverage (40)**: posting must-haves (weight 2), nice-to-haves and title words (weight 1), matched
  on word boundaries with the shipped alias table against the EXTRACTED text, i.e. what a reader sees.
* **Format rules (20)**: text layer, single column, no tables, no images, fonts embedded, plain characters,
  standard headings, 1-2 pages, file name.  A failed rule also caps the total (``CAPS``): the posture is "a
  real ATS may misread this", never a prediction of any vendor's score.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from pypdf import PdfReader
from pypdf.generic import ContentStream

from gigai.scout.posting_keywords import PostingKeywords, mentions
from gigai.scout.tailored_resume import TailoredResume, render_markdown

#: The wording that always accompanies the score: ``ATS_WORDING``, one source string (``gigai.scout.wording``).
from gigai.scout.wording import ATS_WORDING

STANDARD_HEADINGS = frozenset({"SUMMARY", "EXPERIENCE", "SKILLS", "EDUCATION", "PROJECTS"})
#: A failed rule caps the total.
CAPS = {"text layer": 10, "single column": 75, "no tables": 80, "plain characters": 85, "standard headings": 85}
_WEIGHTS = {
    "text layer": 5, "single column": 3, "no tables": 2, "no images": 2, "fonts embedded": 1,
    "plain characters": 2, "standard headings": 3, "1-2 pages": 1, "file name": 1,
}
_FIDELITY_CLEAN = 36.0
_TEXT_LAYER_CHARS = 200
_RECALL_CLEAN = 0.95  # below this share of the source's words, the line names "text lost"
_DATE_RANGE = re.compile(r"\b(?:[A-Z][a-z]{2} )?(?:19|20)\d\d\s*[-–]\s*(?:(?:[A-Z][a-z]{2} )?(?:19|20)\d\d|Present)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_FILE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}\.pdf$")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]")
_ROLE_LINE = re.compile(r"(?m)^(.+?) \| ((?:[A-Z][a-z]{2} )?\d{4} - .+)$")
_LETTER_SPACED = re.compile(r"(?:\b\S {1,2}){5,}\S\b")  # "J O R D A N" (tracking can double the gaps)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SECOND_COLUMN_START = 0.30  # a row whose FIRST fragment starts past this share of the page width
_SECOND_COLUMN_ROWS = 0.15
_STROKE_OPS = (b"S", b"s", b"B", b"b")
_MAX_RULE_STROKES = 2
#: The space width (1/1000 em) ``pypdf`` assumes for a font whose subset has NO space glyph.  Its own default
#: (200) makes a 0.1 em tracking read as a space between letters, so the same heading extracted as ``SUMMARY``
#: or ``S U M M A RY`` depending on whether any heading on the page held a space.  A typical text face's space
#: is about a quarter em (Inter: 252-281), which is also what a subset WITH a space glyph gives.
_SPACE_WIDTH = 250.0


@dataclass(frozen=True)
class AtsScore:
    score: int
    line: str
    fidelity: float
    coverage: float
    format: float
    breakdown: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {"score": self.score, "line": self.line, "fidelity": self.fidelity, "coverage": self.coverage, "format": self.format, "breakdown": self.breakdown}


@dataclass
class _Read:
    text: str
    frags: list[tuple[int, int, float, str, float]]  # (page, y, x, text, page width)
    images: int
    pages: int
    unembedded_fonts: list[str]
    strokes: int


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _words(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def _read(pdf: bytes) -> _Read:
    reader = PdfReader(io.BytesIO(pdf))
    text: list[str] = []
    frags: list[tuple[int, int, float, str, float]] = []
    images = strokes = 0
    unembedded: set[str] = set()
    for number, page in enumerate(reader.pages):
        width = float(page.mediabox.width)

        def visit(t: str, cm: list[float], tm: list[float], _font: object, _size: float, _n: int = number, _w: float = width) -> None:
            if t.strip():
                frags.append((_n, round(tm[5] * cm[3] + cm[5]), tm[4] * cm[0] + cm[4], t, _w))

        text.append(page.extract_text(visitor_text=visit, space_width=_SPACE_WIDTH) or "")
        images += len(page.images)
        resources = page.get("/Resources")
        for font in ((resources.get_object() if resources is not None else {}).get("/Font") or {}).values():
            font = font.get_object()
            descriptor = font.get("/FontDescriptor") or next((d.get_object().get("/FontDescriptor") for d in font.get("/DescendantFonts", [])), None)
            if descriptor is not None and not any(key in descriptor.get_object() for key in ("/FontFile", "/FontFile2", "/FontFile3")):
                unembedded.add(str(font.get("/BaseFont")))
        contents = page.get_contents()
        if contents is not None:
            strokes += sum(1 for _operands, op in ContentStream(contents, reader).operations if op in _STROKE_OPS)
    return _Read("\n".join(text), frags, images, len(reader.pages), sorted(unembedded), strokes)


def _source_markdown(source: str | TailoredResume) -> str:
    markdown = render_markdown(source) if isinstance(source, TailoredResume) else source
    # A stored resume's lines end in a source comment: without it and the blank it leaves, a role line reads the
    # same as in plain markdown ("Title | 2012 - 2016", not "... 2016 ").
    # A leading "# Name" is a hint the renderer prints nowhere (the header comes from the form, or there is none).
    return "\n".join(line.rstrip() for line in _COMMENT.sub("", markdown).splitlines() if not line.startswith("# "))


def _first_column_share(frags: list[tuple[int, int, float, str, float]]) -> float:
    """Share of text rows whose FIRST fragment starts past 30% of the page width: a second column's lines.

    0 for one column: Scout's right-margin dates and inline chips are never a row's first fragment."""
    first: dict[tuple[int, int], tuple[float, float]] = {}
    for page, y, x, _t, width in frags:
        if (page, y) not in first or x < first[(page, y)][0]:
            first[(page, y)] = (x, width)
    return sum(1 for x, width in first.values() if x > _SECOND_COLUMN_START * width) / max(1, len(first))


def _row_order_text(frags: list[tuple[int, int, float, str, float]]) -> str:
    """What a column-unaware line reader sees: fragments sorted top to bottom, then left to right."""
    rows: dict[tuple[int, int], list[tuple[float, str]]] = defaultdict(list)
    for page, y, x, t, _w in frags:
        rows[(page, y)].append((x, t))
    return "\n".join(" ".join(t.strip() for _x, t in sorted(rows[key])) for key in sorted(rows, key=lambda k: (k[0], -k[1])))


def _entries_intact(text: str, markdown: str) -> float:
    """Share of source role lines ("Title | dates") that come out with title and dates adjacent.

    1 when the source has no such line: nothing to split."""
    roles = _ROLE_LINE.findall(markdown)
    if not roles:
        return 1.0
    ok = sum(1 for title, dates in roles if re.search(re.escape(title) + r"\s+" + re.escape(dates).replace("\\ ", r"\s+"), text))
    return ok / len(roles)


def _fidelity(markdown: str, read: _Read) -> tuple[float, dict[str, object]]:
    source_words = [w for w in _words(re.sub(r"(?m)^#+\s*|^- ", "", markdown)) if len(w) > 2]
    got_words = set(_words(read.text))
    recall = sum(w in got_words for w in source_words) / max(1, len(source_words))
    lines = {line.strip().upper() for line in read.text.splitlines()}
    wanted = {h.strip().upper() for h in re.findall(r"(?m)^## (.+)$", markdown)}
    found = wanted & lines
    source_dates, got_dates = len(_DATE_RANGE.findall(markdown)), len(_DATE_RANGE.findall(read.text))
    # Kept when the source has none to lose: Scout's markdown never holds one and an agent's PDF has no header.
    email = _EMAIL.search(read.text) is not None or _EMAIL.search(markdown) is None
    intact_stream = _entries_intact(read.text, markdown)
    intact_rows = _entries_intact(_row_order_text(read.frags), markdown)
    # A part with nothing in the source (no heading, no date range) has nothing to lose: it earns its points.
    sections = len(found) / len(wanted) if wanted else 1.0
    dates = min(1.0, got_dates / source_dates) if source_dates else 1.0
    points = 20 * recall + 10 * sections + 7 * dates + 3 * email
    points = max(0.0, points - 5 * (1 - min(intact_stream, intact_rows)))
    lost = [
        *(f"{h.lower()} heading" for h in sorted(wanted - found)),
        *(["text lost"] if recall < _RECALL_CLEAN else []),
        *(["dates lost"] if got_dates < source_dates else []),
        *(["email lost"] if not email else []),
        *(["role lines split"] if min(intact_stream, intact_rows) < 1 else []),
    ]
    return round(points, 1), {
        "lost": lost,
        "word_recall": round(recall, 3), "sections": f"{len(found)}/{len(wanted)}", "sections_missing": sorted(wanted - found),
        "dates": f"{got_dates}/{source_dates}", "email": email, "role_lines_intact_stream": round(intact_stream, 2), "role_lines_intact_rows": round(intact_rows, 2),
    }


def _coverage(keywords: PostingKeywords, text: str) -> tuple[float, dict[str, object]]:
    must = [(t, mentions(text, t)) for t in keywords.must]
    nice = [(t, mentions(text, t)) for t in keywords.nice]
    title = [(t, mentions(text, t)) for t in keywords.title]
    got = 2 * sum(h for _t, h in must) + sum(h for _t, h in nice) + sum(h for _t, h in title)
    total = 2 * len(must) + len(nice) + len(title)
    points = 40.0 if total == 0 else (40 * got / total if text.strip() else 0.0)  # no keywords given: nothing to miss
    return round(points, 1), {
        "key_skills": f"{sum(h for _t, h in must + nice)}/{len(must) + len(nice)}",
        "missing_must": [t for t, h in must if not h], "missing_nice": [t for t, h in nice if not h], "missing_title": [t for t, h in title if not h],
    }


def _format(read: _Read, file_name: str | None, markdown: str) -> tuple[float, dict[str, object]]:
    odd = sum(1 for ch in read.text if 0x1D400 <= ord(ch) <= 0x1D7FF or 0xE000 <= ord(ch) <= 0xF8FF)
    heads = {line.strip().upper() for line in read.text.splitlines()}
    share = _first_column_share(read.frags)
    rules = {
        # Most of a page's worth of text, or of the source's when the resume itself is shorter than that.
        "text layer": len(read.text.strip()) > min(_TEXT_LAYER_CHARS, len(markdown.strip()) // 2),
        "single column": share < _SECOND_COLUMN_ROWS,
        "no tables": read.strokes <= _MAX_RULE_STROKES,
        "no images": read.images == 0,
        "fonts embedded": not read.unembedded_fonts,
        "plain characters": odd == 0 and not _LETTER_SPACED.search(read.text),
        "standard headings": len(heads & STANDARD_HEADINGS) >= 3,
        "1-2 pages": read.pages <= 2,
        "file name": file_name is None or bool(_FILE_NAME.match(file_name)),  # no name given: nothing to fault
    }
    failed = [name for name, ok in rules.items() if not ok]
    return float(sum(w for name, w in _WEIGHTS.items() if rules[name])), {
        "failed": failed, "column_share": round(share, 3), "images": read.images, "strokes": read.strokes, "pages": read.pages,
        "unembedded_fonts": read.unembedded_fonts,
    }


def _unreadable() -> AtsScore:
    return AtsScore(0, "Scout ATS 0: does not parse: unreadable PDF", 0.0, 0.0, 0.0, {"format": {"failed": ["text layer"]}})


def score(pdf_bytes: bytes, source: str | TailoredResume, keywords: PostingKeywords, *, file_name: str | None = None) -> AtsScore:
    """Score one rendered resume PDF against the markdown (or ``TailoredResume``) it came from and a posting's keywords.

    ``file_name`` is the name the PDF is offered under (the PDF download name); ``None`` skips that rule."""
    try:
        read = _read(pdf_bytes)
    except Exception:  # noqa: BLE001 - pypdf raises many types on damaged or encrypted input; all mean "no readable text layer"
        return _unreadable()
    markdown = _source_markdown(source)
    fidelity, fidelity_detail = _fidelity(markdown, read)
    coverage, coverage_detail = _coverage(keywords, read.text)
    fmt, format_detail = _format(read, file_name, markdown)
    failed = format_detail["failed"]
    assert isinstance(failed, list)
    total = min([round(fidelity + coverage + fmt), *(CAPS[rule] for rule in failed if rule in CAPS)])
    if not read.text.strip():
        parse = "does not parse: no text layer"
    elif fidelity >= _FIDELITY_CLEAN and not failed:
        parse = "parses cleanly"
    else:
        # Never an unnamed issue: a failed rule, what the text lost, or (neither) that it differs from the source.
        parse = "parse issues: " + ", ".join([*failed, *fidelity_detail["lost"]] or ["text differs from the source"])  # type: ignore[misc]
    missing = [*coverage_detail["missing_must"], *coverage_detail["missing_nice"]]  # type: ignore[misc]
    skills = coverage_detail["key_skills"]
    line = f"Scout ATS {total}: {parse}" + ("" if skills == "0/0" else f" · {skills} key skills") + (f" · missing: {', '.join(missing)}" if missing else "")
    return AtsScore(total, line, fidelity, coverage, fmt, {"fidelity": fidelity_detail, "coverage": coverage_detail, "format": format_detail, "wording": ATS_WORDING})
