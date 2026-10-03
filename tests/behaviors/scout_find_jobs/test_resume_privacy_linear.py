"""The resume privacy patterns run in linear time and find what they always found.

Found by the 0110-046 worker: one 20,000-character line took ``model_resume``
4.5 s and a 100,000-character line 111 s. Six patterns in
``resume_privacy.py`` (and ``resume_pii._EMAIL``) tried a match from every
character of a long run and read the run again each time; the name/headline
split was worse (cubic on a run of spaces).

Two things are pinned here:

* **time**: on one long line of spaces, digits, ``@``, dots, dashes and the
  shapes built from them, ten times the input costs less than twenty times
  the time, and 100,000 characters stay under a generous ceiling. Measured
  as this process's CPU time (``time.process_time``), so a busy machine
  cannot fail it;
* **equivalence**: the patterns find exactly the matches the earlier ones
  did. The earlier patterns are kept below, verbatim, as the reference, and
  compared on a corpus of synthetic resume lines (emails, phones in several
  shapes, links, addresses, name and headline lines, body text) and on
  seeded random lines built from the characters the patterns care about.

Synthetic fixtures only.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from gigai.scout import resume_pii, resume_privacy
from tests.support.latency import latency_bound

# --- the patterns as they were before the fix (the reference; never edit) --------------

OLD = {
    "_EMAIL": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<!\w)@[\w.]{2,}"),
    "_URL": re.compile(
        r"(?:https?://|www\.)\S+"
        r"|\b(?:[\w-]+\.)+(?:com|net|org|io|dev|me|ai|app|co|us|ca|uk|in|xyz|tech)\b(?:/\S*)?",
        re.I,
    ),
    "_PHONE": re.compile(
        r"(?<![\w.])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
        r"|(?<![\w.])\+\d[\d\s().-]{7,}\d",
    ),
    "_STREET": re.compile(
        r"\b\d{1,6}\s+(?:[\w.]+\s+){0,4}(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|"
        r"place|pl|parkway|pkwy|suite|apt)\b\.?",
        re.I,
    ),
    "_PO_BOX": re.compile(r"\bp\.?\s?o\.?\s?box\s+\d+", re.I),
    "_ZIP_LINE": re.compile(r"\b[A-Z][A-Za-z.\s]+,\s*[A-Z]{2}\b(?:\s+\d{5}(?:-\d{4})?)?|\b\d{5}(?:-\d{4})?\b"),
    "_BODY_EMAIL": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "_BODY_URL": re.compile(
        r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+"
        r"|\b(?:[\w-]+\.)*(?:linkedin\.com|github\.com|gitlab\.com|bit\.ly|lnkd\.in|t\.co|tinyurl\.com|goo\.gl)\b(?:/\S*)?",
        re.I,
    ),
}
OLD_PII_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
OLD_MARKDOWN_MARKERS = re.compile(r"\A[#>*_\s]+|[*_\s]+\Z")
OLD_NAME_THEN_REST = re.compile(r"\A(?P<prefix>[#>*_\s]*)(?P<name>[^|—–·•,]+?)\s*(?:\s[—–-]\s|[|·•,])\s*(?P<rest>\S.*)\Z")


def _old_unmarked(line: str) -> str:
    return OLD_MARKDOWN_MARKERS.sub("", line.strip())


def _old_name_then_headline(line: str) -> tuple[str, str] | None:
    match = OLD_NAME_THEN_REST.match(line.strip())
    if match is None or not resume_privacy.is_name_line(match.group("name")):
        return None
    hashes = re.match(r"#+", match.group("prefix").strip())
    headline = OLD_MARKDOWN_MARKERS.sub("", match.group("rest")) or match.group("rest")
    return _old_unmarked(match.group("name")), (hashes.group(0) + " " if hashes else "") + headline


# --- equivalence -----------------------------------------------------------------------

CORPUS = (
    "Jane Doe",
    "JANE Q. DOE",
    "# Priya Natarajan — Staff ML Engineer",
    "Jane Doe | Senior Backend Engineer",
    "**Jane Doe** · Platform Engineer",
    "Jane O'Neil-McDonald, Staff Engineer",
    "## Jane Doe - Data Scientist - Denver",
    "Senior Software Engineer",
    "jane.doe@example.com",
    "Email: jane.doe+jobs@mail.example.co.uk",
    "jane_doe-99@sub-domain.example.io | (303) 555-0142 | linkedin.com/in/jane-doe",
    "Reach me at jane@example.com or +1 303 555 0142",
    "Contact: jane@example.com.+backup@example.org",
    "@janedoe",
    "Twitter @jane.doe, GitHub @janedoe-dev",
    "Engineer @Stripe, then @ Acme",
    "a@b",
    "x@y.z+q@w.e",
    "303-555-0142",
    "(303) 555-0142",
    "303.555.0142",
    "+1 (303) 555-0142",
    "+44 20 7946 0958",
    "+1-303-555-0142 ext 12",
    "Phone 3035550142, mobile +13035550142",
    "Version 3.11.4 and 2019-2023, 1,200,000 rows, 555-0142",
    "https://janedoe.dev/portfolio?x=1#top",
    "http://example.com",
    "www.janedoe.dev",
    "janedoe.dev",
    "portfolio: jane-doe.github.io/projects and blog.jane.me",
    "linkedin.com/in/jane-doe",
    "LinkedIn - https://www.linkedin.com/in/jane-doe/",
    "github.com/janedoe · gitlab.com/janedoe · bit.ly/jd-cv",
    "my-github.com/x and foo-github.com, 1.github.com.x://y, a_b.gitlab.com.z://q",
    "t.co/abc lnkd.in/xyz tinyurl.com/jd goo.gl/maps",
    "ftp://files.example.net/cv.pdf and custom+scheme.v2://host/path",
    "Built with socket.io, node.js, ASP.NET and Three.js; e.g. i.e. etc.",
    "Deployed api.internal.example.com-backed services to us.east.example.co",
    "a..b.com and .hidden.dev and -dash.io and x.-.y.org",
    "1234 Main Street",
    "742 Evergreen Terrace Dr.",
    "12 N. Oak Ave Suite 400",
    "Apt 4B, 99 West 3rd St",
    "P.O. Box 1234",
    "PO Box 77, Denver, CO 80202",
    "Denver, CO 80202",
    "Denver, CO",
    "San Francisco, CA 94107-1234",
    "St. Louis, MO. Kansas City, MO 64101",
    "Boulder, Colorado 80301",
    "Acme Corp, Denver, CO | 2019 - 2023",
    "80202",
    "Staff Engineer, Acme Corp, Remote",
    "Led a team of 12 engineers; cut p99 latency 40% (300 ms to 180 ms).",
    "Python, Go, Kubernetes, PostgreSQL, Kafka",
    "B.S. Computer Science, University of Colorado, Boulder, CO",
    "US citizen. No visa sponsorship required. H-1B not needed.",
    "Summary",
    "## Experience",
    "- Shipped search ranking (BM25 + embeddings) for 2M users",
    "Jane Doe\tDenver, CO\tjane@example.com",
    "Jane Doe  —  Engineer  —  jane@example.com  —  303-555-0142",
    "",
    "   ",
)

_FUZZ_ATOMS = (
    "a", "b", "A", "Z", "é", "1", "5", " ", " ", ".", "-", "@", "+", "/", ":", ",", "_", "|", "—", "–", "·", "#", "*", "(", ")", "\t",
    "www.", "http://", "https://", "://", "github.com", "linkedin.com", "t.co", ".com", ".io", "com", "x@y.co",
    "12345", "303", "5550142", "CA", "St", "Jane", "Doe", "Box", "p.o.", " - ", "..",
)


def _fuzz_lines(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(_FUZZ_ATOMS) for _ in range(rng.randint(1, 14))) for _ in range(count)]


def _spans(pattern, text: str) -> list[tuple[int, int]]:
    return [match.span() for match in pattern.finditer(text)]


def _lines() -> list[str]:
    joined = [f"{a} | {b}" for a in CORPUS[::5] for b in CORPUS[2::7]] + [f"{a}, {b}" for a in CORPUS[1::6] for b in CORPUS[3::8]]
    return [*CORPUS, *joined, *(line.upper() for line in CORPUS), *(f"  {line}.  " for line in CORPUS)]


@pytest.mark.parametrize("name", sorted(OLD))
def test_each_pattern_finds_exactly_what_it_found_before(name: str) -> None:
    old, new = OLD[name], getattr(resume_privacy, name)
    for line in [*_lines(), *_fuzz_lines(seed=1045, count=6000)]:
        assert _spans(new, line) == _spans(old, line), (name, line)
        found_new, found_old = new.search(line), old.search(line)
        assert (found_new and found_new.span()) == (found_old and found_old.span()), (name, line)
        assert new.sub("", line) == old.sub("", line), (name, line)
        assert new.sub(" ", line) == old.sub(" ", line), (name, line)


def test_the_corpus_exercises_every_pattern() -> None:
    """The comparison above means nothing for a pattern the corpus never matches."""

    lines = _lines()
    for name, old in OLD.items():
        assert sum(1 for line in lines if old.search(line)) >= 5, name
    assert sum(1 for line in lines if _old_name_then_headline(line)) >= 5


def test_the_name_headline_split_and_marker_strip_are_unchanged() -> None:
    for line in [*_lines(), *_fuzz_lines(seed=2045, count=20000)]:
        assert resume_privacy._unmarked(line) == _old_unmarked(line), line
        assert resume_privacy._name_then_headline(line) == _old_name_then_headline(line), line


def test_the_import_heads_up_email_check_is_unchanged() -> None:
    for line in [*_lines(), *_fuzz_lines(seed=3045, count=6000)]:
        assert bool(resume_pii._EMAIL.search(line)) == bool(OLD_PII_EMAIL.search(line)), line


# --- time ------------------------------------------------------------------------------

#: One long line of each: what the patterns scan, alone and in the mixes that
#: made them read a run again from every character.
_PATHOLOGICAL_ATOMS = (
    " ", "1", "@", ".", "-", "a", "A", "+", "#", "*", "_", ",", "/",
    "a.", "a-", "a ", "A ", "1 ", "1-", "1.", "a@", "+1 ", "A.", "a1", "a..", "-.", " - ", "a, ", " ,",
    "1 . - @", "x@y.z+", "github.com-", "a.github.com-", "a.com-", "a.com.", "www.", "a://", "Aa, BB ", "Jane ", "(303) ",
)
_WRAPS = (("", ""), ("x", "y"), ("Jane Doe ", " z"), ("# ", " | x"))

#: CPU seconds, each scaled by ``latency_bound`` for CI. Measured after the
#: fix: 100,000 characters cost ``model_resume`` 0.02 to 0.3 s. Before it the
#: same line cost 111 s, and 10,000 characters over a second.
_FLOOR_SECONDS = 0.15
_CEILING_100K_SECONDS = 3.0


def _the_functions():
    return {
        "model_resume": resume_privacy.model_resume,
        "redact_inline": resume_privacy.redact_inline,
        "guard_private": resume_privacy.guard_private,
        "split_resume_header": resume_privacy.split_resume_header,
        "name_then_headline": resume_privacy._name_then_headline,
        "is_contact_only": resume_privacy._is_contact_only,
        "strip_contact_lines": resume_pii.strip_contact_lines,
        "detect_contact_details": resume_pii.detect_contact_details,
    }


def _cpu(function, text: str) -> float:
    started = time.process_time()
    function(text)
    return time.process_time() - started


def _line(atom: str, wrap: tuple[str, str], size: int) -> str:
    return wrap[0] + atom * (size // len(atom)) + wrap[1]


@pytest.mark.parametrize("function_name", sorted(_the_functions()))
def test_one_long_line_costs_time_in_proportion_to_its_length(function_name: str) -> None:
    function = _the_functions()[function_name]
    floor = latency_bound(_FLOOR_SECONDS)
    ceiling = latency_bound(_CEILING_100K_SECONDS)
    for atom in _PATHOLOGICAL_ATOMS:
        for wrap in _WRAPS:
            what = f"{function_name} on {wrap[0]!r} + {atom!r} * n + {wrap[1]!r}"
            thousand = _cpu(function, _line(atom, wrap, 1_000))
            ten_thousand = _cpu(function, _line(atom, wrap, 10_000))
            # Checked before the 100,000-character line is tried: the earlier patterns took minutes on it.
            assert ten_thousand < max(20 * thousand, floor), (
                f"{what}: 10,000 characters cost {ten_thousand:.3f}s, 1,000 cost {thousand:.4f}s"
            )
            hundred_thousand = _cpu(function, _line(atom, wrap, 100_000))
            assert hundred_thousand < max(20 * ten_thousand, floor), (
                f"{what}: 100,000 characters cost {hundred_thousand:.3f}s, 10,000 cost {ten_thousand:.4f}s"
            )
            assert hundred_thousand < ceiling, f"{what}: 100,000 characters cost {hundred_thousand:.3f}s"
