"""0.1.11.3 Q2 (T1): a question the master already answers is not asked. Pure; no model call, no file read.

Measured on 100 real assessments (numbers only): 36 of 185 questions asked for something the resume states (a tool in
the skills line, a skill a bullet names, a number of years the role dates already show), and 68 of 100 jobs ended
"needs your answers". :func:`settle_stated` runs on the model's answer, after its rows are normalized and before the
question caps and the gate: each ``unclear`` row whose requirement is ONLY key terms the master states becomes ``met``
with the master's own line(s) as its evidence (and, where the line has an id, as its ``sources``), and the question on
it is dropped. The verdict is then settled by the gate as always. Nothing is ever added to a row but lines of the
master, word for word; a ``met`` or ``unmet`` row, an eligibility row and a sponsorship row are never touched.

THE RULE (:func:`stated`).  A requirement is read as words of three kinds:

* IGNORED words: what every requirement is made of ("experience with", "proficiency in", "building applications"), the
  strength and scale words the met standard says never make a question ("strong", "deep", "proven", "at scale",
  "large, complex", "high-volume"), and category nouns ("languages", "cloud platforms", "or similar").
* KEY TERMS: a name in the master's skills line (``Distributed Systems``), or a word written as a name (``React``,
  ``AWS``, ``Node.js``, ``C++``, ``CI/CD``). Neighbouring name words are one term (``Google Cloud``).
* anything else (``leading``, ``teams``, ``consumer-facing``, ``payments``, a bare number) is the requirement's own
  substance, which this check cannot read: THE ROW IS LEFT AS IT CAME. So a scope or domain word is never a key term,
  and a requirement that asks for more than its tools keeps its question.

A key term is STATED when one line of the master holds it as a whole word: ``Java`` is not in ``JavaScript``, ``C`` is
not in ``C++``, ``C#`` or ``C-level``, and ``go`` in lower case is a verb. A word that is only capitalised (``React``,
``Spring``) must be written the same way inside a sentence of the master (not as a line's first word, not before a
year: ``in Spring 2019``); a few spellings of one name are one name (:data:`_ALIASES`).
The line is a bullet, a summary or an Other line (cited by id) or the skills line; never a company heading, a role
title, a private note, or a line that weakens its own claim ("basic exposure to", "migrated off").

Every key term must be stated, each by one line (at most :data:`MAX_LINES`). When the requirement is a list of
alternatives (the row carries ``alternatives``, or it says "or" / "such as" / "e.g." with no "and"), one stated
alternative is enough.

YEARS.  ``8+ years on AWS`` needs the number as well, which is strict:

* a line that holds the term AND states at least that many years, or
* ROLE DATES: the Experience roles whose shown bullets hold the term cover that many years (unless a line states a
  smaller number of years of that term: the master's own number wins). A role's years are read by
  ``master_resume.MasterEntry`` (``start`` / ``end`` / ``ongoing``: the calculation the selector and the PDF use),
  refined to months when both ends name one; overlapping roles are counted once. The evidence is each counted role's
  own dated line and the bullet that holds the term.

``8+ years of software engineering experience`` (no key term; the requirement says software / engineering /
development and nothing else): a Summary line that states the years of an engineer, or the dated Experience roles with
an engineering title. This is also the ALTERNATIVE TRACK: ``a degree in X, or 8+ years of software engineering
experience`` is met by the years. A bare ``N years of experience`` does not say of what, and is left as it came; so is
a requirement with more than one "N years", or any other number.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import re

from .requirements_list import is_eligibility_id
from .resume_gate import is_authorization_row

#: Lines one row cites at most; a requirement that needs more is a composite this check does not settle.
MAX_LINES = 4
#: The longest evidence string the proposals validator takes (``proposals._MAX_TEXT``).
_MAX_EVIDENCE = 1_200
#: Evidence strings and sources one row carries at most (``proposals._MAX_ITEMS``, ``contracts.MAX_ROW_SOURCES``).
_MAX_ITEMS = 12

RULE_LINE = "master_line"
RULE_SKILLS = "skills_line"
RULE_YEARS_STATED = "years_stated"
RULE_YEARS_ROLES = "years_from_roles"

_ID_COMMENT = re.compile(r"<!--\s*id:\s*(\S+?)\s*-->")
_ANY_COMMENT = re.compile(r"\s*<!--.*?-->")
_EMPHASIS = re.compile(r"\A(?:\*\*|__|\*|_)(.*?)(?:\*\*|__|\*|_)\Z")

# --- the master as this check reads it ------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One line of the master: its id (``None`` for the skills line the view prints without one), text and section."""

    id: str | None
    text: str
    section: str


@dataclass(frozen=True)
class Role:
    """One Experience role: its dated line, word for word, the months it covers and the bullets shown under it."""

    dated: str
    titles: str
    start: int  # months since year 0
    end: int
    bullets: tuple[Line, ...]


@dataclass(frozen=True)
class MasterFacts:
    """``lines``: the lines that carry an id (no Education line); ``skills``: the skills line(s); ``skill_names``:
    each name of them, folded; ``lower_names``: those the master writes in lower case; ``roles``: the dated roles."""

    lines: tuple[Line, ...]
    skills: tuple[Line, ...]
    skill_names: frozenset[str]
    lower_names: frozenset[str]
    roles: tuple[Role, ...]


_MONTHS = {name: index + 1 for index, name in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DATE_POINT = re.compile(r"(?:\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+)?(?<!\d)(19[5-9]\d|20\d\d)(?!\d)", re.IGNORECASE)


def _months(sublines: Sequence[str], today: date) -> tuple[int, int] | None:
    """``(start, end)`` of a role in months since year 0, or ``None`` for a role that names no year.

    The years are ``MasterEntry``'s (``start``: the first year named; ``end``: the last, or today for an ongoing
    role). Months count only when both ends name one; otherwise the role covers whole years by subtraction.
    """

    from .master_resume import MasterEntry  # lazy: that module imports the assess core, which imports this one

    entry = MasterEntry(id="", section="experience", heading="", sublines=tuple(sublines))
    first = entry.start
    if first is None:
        return None
    points = [(int(year), _MONTHS.get((month or "")[:3].lower())) for month, year in _DATE_POINT.findall(" ".join(sublines))]
    start_month = next((month for year, month in points if year == first), None)
    if entry.ongoing:
        last, end_month = today.year, today.month
    else:
        last = entry.end if entry.end is not None else first
        end_month = next((month for year, month in reversed(points) if year == last), None)
    if start_month is None or end_month is None:
        return first * 12, last * 12
    return first * 12 + start_month - 1, last * 12 + end_month - 1


def _clean(raw: str) -> str:
    text = _ANY_COMMENT.sub("", raw).strip()
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()
    found = _EMPHASIS.match(text)
    return found.group(1).strip() if found else text


def read_master(resume_text: str, *, today: date | None = None) -> MasterFacts:
    """What the RESUME block of an assess prompt states, as this check reads it: lines, skills and dated roles.

    ``resume_text`` is the evidence view (``master_selection.evidence_view(ids=True)``) or a master in the same
    format. A note line (``<!-- private note: ... -->``) is the user's private guidance and is never read.
    """

    from .master_resume import skill_names  # lazy: that module imports the assess core, which imports this one

    today = today or date.today()
    lines: list[Line] = []
    skills: list[Line] = []
    roles: list[Role] = []
    section = ""
    in_entry = False
    sublines: list[str] = []
    bullets: list[Line] = []

    def close() -> None:
        nonlocal in_entry, sublines, bullets
        if in_entry and section == "experience":
            span = _months(sublines, today)
            dated = next((line for line in sublines if _DATE_POINT.search(line)), None)
            if span is not None and dated is not None and span[1] > span[0]:
                roles.append(Role(dated, " ".join(sublines), span[0], span[1], tuple(bullets)))
        in_entry, sublines, bullets = False, [], []

    for raw in resume_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            close()
            if stripped.startswith("###"):
                in_entry = True
            else:
                section = _ANY_COMMENT.sub("", stripped).lstrip("#").strip().lower()
            continue
        text = _clean(stripped)
        if not text:
            continue
        if not stripped.startswith(("- ", "* ")):
            if in_entry and not bullets:
                sublines.append(text)
            continue
        found = _ID_COMMENT.search(stripped)
        line = Line(found.group(1) if found else None, text, section)
        if section == "skills":
            skills.append(line)
        elif line.id is not None and section != "education":
            lines.append(line)
            if in_entry:
                bullets.append(line)
    close()
    names: set[str] = set()
    lower: set[str] = set()
    for line in skills:
        for name in skill_names(line.text)[1]:
            # ``AWS (EC2, S3)`` names AWS, EC2 and S3.
            for part in re.split(r"[(),]", name):
                if part.strip():
                    names.add(_fold(part))
                    if part.strip().islower():
                        lower.add(_fold(part))
    return MasterFacts(tuple(lines), tuple(skills), frozenset(names), frozenset(lower), tuple(roles))


# --- the words of a requirement ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    word: index + 1
    for index, word in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_NUMBER = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
#: ``8 years``, ``8+ years``, ``5-7 yrs``, ``ten years``, ``5 or more years``: the first number is the one required.
_YEARS = re.compile(
    r"\b" + _NUMBER + r"\s*\+?(?:\s*(?:-|–|to)\s*(?:\d{1,2})\s*\+?)?(?:\s+or\s+more)?[\s-]*(?:years?|yrs?)\b(?:['’]s?)?(?:\s+or\s+more)?",
    re.IGNORECASE,
)
_YEARS_MARK = "\x00"
_TOKEN = re.compile(r"\x00|\.?[A-Za-z0-9][A-Za-z0-9+#./'’_-]*|[,;:()&/]")

_OR = frozenset({"or", "and/or"})
_AND = frozenset({"and", "&", "plus"})
_BREAK = frozenset({",", ";", ":", "(", ")", "/"})
#: Words that say the terms after them are examples or alternatives: one is enough.
_ANY_OF = frozenset({"e.g", "eg", "such", "like", "similar", "either", "any"})

#: Words every requirement is made of. They are never a key term and never the requirement's substance.
_GRAMMAR = frozenset(
    "a an the of in on to for with at by as from is are be been being that this these those your you our we it its have has having had "
    "must should will can would may able into over via per than then so within across least more about around approximately total overall "
    "combined minimum min one both all each well etc i.e ie especially particularly ideally preferably preferred required requirement bonus "
    "nice need needs essential desired following".split()
)
_HAVING = frozenset(
    "experience experienced expertise expert proficiency proficient knowledge knowledgeable familiarity familiar understanding background "
    "skill skills skilled competency competence fluency fluent command mastery comfort comfortable capability track record exposure".split()
)
_DOING = frozenset(
    "use using used work working worked build building built develop developing developed development write writing written code coding "
    "program programming ship shipping shipped deploy deploying deployed implement implementing implemented create creating created "
    "maintain maintaining maintained".split()
)
#: Strength and scale: the met standard's words, which never make a question.
_STRENGTH = frozenset(
    "strong deep solid proven demonstrated demonstrable significant extensive excellent good great advanced thorough substantial hands-on "
    "practical professional commercial industry real-world production production-grade direct relevant prior previous recent high highly "
    "very in-depth level large large-scale complex scale scalable high-volume high-scale robust".split()
)
#: Category nouns and list words ("AWS, GCP or similar cloud platforms").
_CATEGORY = frozenset(
    "language languages framework frameworks library libraries tool tools tooling technology technologies tech stack stacks platform "
    "platforms provider providers database databases cloud application applications app apps software system systems service services "
    "product products solution solutions project projects environment environments codebase codebases other others equivalent comparable "
    "related modern popular major common public some several multiple various including include includes".split()
) | _ANY_OF
_IGNORED = _GRAMMAR | _HAVING | _DOING | _STRENGTH | _CATEGORY

#: With a number of years and no key term ("8+ years of software engineering experience"): the words that make the
#: years those of a career in engineering (counted on the roles with an engineering title). Any other category or doing
#: word is a subject the role dates do not show.
_FAMILY = frozenset({"software", "engineering", "engineer", "development", "developing", "developer", "programming", "programmer"})
_CAREER_WORDS = _GRAMMAR | _HAVING | _STRENGTH | _FAMILY | frozenset({"work", "working", "worked"})
_FAMILY_TITLE = re.compile(r"engineer|developer|programmer|architect|\bsde\b|\bswe\b", re.IGNORECASE)

#: A lower-case word this long is read as the skills-line name it spells (``kubernetes``); a shorter one may be a verb (``go``, ``react``).
_LOWER_NAME_CHARS = 8

#: A first word that may be a verb and not a name ("Go above and beyond", "React quickly to incidents").
_VERBS = frozenset({"go", "react", "express", "make", "spring", "swift", "lead", "drive", "dash", "flow", "shell"})
_VERB_FOLLOWS = frozenset({"to", "on", "above", "beyond", "deep", "deeper", "quickly", "fast", "the", "a", "an", "through", "into", "out", "up"})

#: Spellings of one name. Each group is closed: nothing here says two different things are the same.
_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("node", "node.js", "nodejs"),
    ("react", "react.js", "reactjs"),
    ("vue", "vue.js", "vuejs"),
    ("next.js", "nextjs"),
    ("postgres", "postgresql"),
    ("kubernetes", "k8s"),
    ("go", "golang"),
    ("javascript", "js"),
    ("typescript", "ts"),
    ("gcp", "google cloud", "google cloud platform"),
    ("aws", "amazon web services"),
    ("ci/cd", "cicd"),
)
_ALIASES: dict[str, tuple[str, ...]] = {name: group for group in _ALIAS_GROUPS for name in group}
#: Spellings that are also plain words: found in the master only as written here.
_EXACT_SPELLING = {"go": "Go", "node": "Node", "react": "React", "vue": "Vue", "js": "JS", "ts": "TS"}

#: A line that takes its own claim back is not evidence.
_WEAK = re.compile(
    r"\b(?:no|not|never|without|instead of|basic|beginner|exposure to|learning|studying|migrat\w+ (?:off|away)|"
    r"mov\w+ (?:off|away)|replac\w+|deprecat\w+|retir\w+|sunset\w*)\b",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class _Term:
    """A key term of a requirement: its words as written, and whether the master must write them the same way."""

    text: str
    exact: bool = False


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and word[-2].isalpha() and (word[-2].isupper() or word.islower()) else word


def _named(word: str) -> tuple[bool, bool]:
    """``(a name, only capitalised)``: ``AWS``, ``Node.js``, ``C++`` and ``k8s`` are names; ``React`` is one the master must write the same way."""

    if word.isdigit() or not any(char.isalpha() for char in word):
        return False, False
    if any(char.isupper() for char in word[1:]) or any(char.isdigit() for char in word) or any(char in "+#" for char in word) or word.startswith("."):
        return True, False
    if "." in word.strip("."):
        return True, False
    return (True, True) if word[0].isupper() else (False, False)


def _pattern(text: str, *, exact: bool) -> re.Pattern[str]:
    """``text`` as a whole word: no letter, digit, ``+ # _ -`` or ``.`` joins it to a longer name on either side."""

    body = r"\s+".join(re.escape(part) for part in text.split())
    plural = "s?" if text[-1].isalpha() and not text.endswith("s") and len(text) > 2 else ""
    return re.compile(
        r"(?<![A-Za-z0-9+#_.\-])" + body + plural + r"(?![A-Za-z0-9+#_\-])(?!\.[A-Za-z0-9])(?!\s+(?:19|20)\d\d\b)",
        0 if exact else re.IGNORECASE,
    )


def _spellings(term: _Term) -> list[re.Pattern[str]]:
    folded = _fold(term.text)
    out = [_pattern(term.text, exact=term.exact)]
    for other in _ALIASES.get(folded, ()):
        if other != folded:
            written = _EXACT_SPELLING.get(other)
            out.append(_pattern(written or other, exact=written is not None))
    return out


def _holds(line: Line, term: _Term) -> bool:
    """Whether ``line`` holds ``term`` as a whole word.

    A word that is only capitalised must stand INSIDE a sentence of the master (``... in React and ...``): the first
    word of a line is capitalised whatever it is (``Ownership of ...``), so it does not show a name.
    """

    for pattern in _spellings(term):
        for found in pattern.finditer(line.text):
            if not (term.exact and line.section != "skills" and found.start() == 0):
                return True
    return False


def _line_for(term: _Term, facts: MasterFacts) -> Line | None:
    """The master line that states ``term``: a bullet of a role first (the strongest evidence), the skills line last."""

    order = {"experience": 0, "projects": 1, "summary": 2}
    for line in sorted(facts.lines, key=lambda item: order.get(item.section, 3)):
        if len(line.text) <= _MAX_EVIDENCE and not _WEAK.search(line.text) and _holds(line, term):
            return line
    return next((line for line in facts.skills if len(line.text) <= _MAX_EVIDENCE and _holds(line, term)), None)


# --- years ------------------------------------------------------------------------------------------------

_STATED_YEARS = re.compile(r"\b" + _NUMBER + r"\s*\+?[\s-]*(?:years?|yrs?)\b", re.IGNORECASE)


def _number(text: str) -> int:
    return int(text) if text.isdigit() else _NUMBER_WORDS[text.lower()]


def _years_in(text: str) -> int | None:
    """The years a line states: the smallest number when it states several (``ten years, two of them on AWS``)."""

    found = [_number(item) for item in _STATED_YEARS.findall(text)]
    return min(found) if found else None


def _covered(roles: Sequence[Role], needed: int) -> list[Role] | None:
    """The most recent of ``roles`` that together cover ``needed`` months (overlaps counted once), or ``None``."""

    taken: list[Role] = []
    for role in sorted(roles, key=lambda item: (-item.end, -item.start)):
        taken.append(role)
        total, reach = 0, None
        for start, end in sorted((item.start, item.end) for item in taken):
            if reach is None or start > reach:
                total += end - start
                reach = end
            elif end > reach:
                total += end - reach
                reach = end
        if total >= needed:
            return taken
    return None


@dataclass(frozen=True)
class Stated:
    """What settles a row: the master's lines, word for word, the ids of those that have one, and the rule that found them."""

    evidence: tuple[str, ...]
    sources: tuple[str, ...]
    rule: str


def _cite(lines: Iterable[Line | str], rule: str) -> Stated | None:
    evidence: list[str] = []
    sources: list[str] = []
    for line in lines:
        text = line if isinstance(line, str) else line.text
        if not text or len(text) > _MAX_EVIDENCE:
            return None
        if text not in evidence:
            evidence.append(text)
        if isinstance(line, Line) and line.id is not None and line.id not in sources:
            sources.append(line.id)
    if not evidence or len(evidence) > _MAX_ITEMS or len(sources) > _MAX_ITEMS:
        return None
    return Stated(tuple(evidence), tuple(sources), rule)


def _says_fewer(line: Line, term: _Term, years: int) -> bool:
    """Whether ``line`` states fewer years OF THE TERM ("two years of Postgres tuning"): the master's own number wins over the role dates."""

    for found in _STATED_YEARS.finditer(line.text):
        if _number(found.group(1)) < years and any(pattern.search(line.text[found.end(): found.end() + 40]) for pattern in _spellings(term)):
            return True
    return False


def _term_years(term: _Term, years: int, facts: MasterFacts) -> tuple[list[Line | str], str] | None:
    """Years of ``term``: a line that holds it and states that many, else the dated roles whose shown bullets hold it."""

    for line in facts.lines:
        said = _years_in(line.text)
        if said is not None and said >= years and not _WEAK.search(line.text) and _holds(line, term):
            return [line], RULE_YEARS_STATED
    if any(_says_fewer(line, term, years) for line in facts.lines):
        return None
    held = {id(role): next((line for line in role.bullets if not _WEAK.search(line.text) and _holds(line, term)), None) for role in facts.roles}
    counted = _covered([role for role in facts.roles if held[id(role)] is not None], years * 12)
    if counted is None:
        return None
    return [item for role in counted for item in (role.dated, held[id(role)])], RULE_YEARS_ROLES  # type: ignore[misc]


def _career_years(years: int, facts: MasterFacts) -> tuple[list[Line | str], str] | None:
    """Years as an engineer: a Summary line that states them of an engineer, else the dated roles with an engineering title."""

    for line in facts.lines:
        said = _years_in(line.text)
        if line.section == "summary" and said is not None and said >= years and _FAMILY_TITLE.search(line.text):
            return [line], RULE_YEARS_STATED
    counted = _covered([role for role in facts.roles if _FAMILY_TITLE.search(role.titles)], years * 12)
    return None if counted is None else ([role.dated for role in counted], RULE_YEARS_ROLES)


# --- the rule ---------------------------------------------------------------------------------------------


def _skill(words: Sequence[str], facts: MasterFacts) -> bool:
    """Whether ``words`` are a name of the master's skills line, as a requirement may write it."""

    joined = " ".join(words)
    low = _fold(joined)
    if len(words) == 1:
        if low in _IGNORED or low in _FAMILY or not any(char.isalpha() for char in joined):
            return False
        # ``go`` and ``react`` in lower case are verbs; a long lower-case word (``kubernetes``) is the name, and so is
        # a short one the master itself writes in lower case (``dbt``).
        if joined.islower() and len(joined) < _LOWER_NAME_CHARS and low not in facts.lower_names:
            return False
    return low in facts.skill_names or _fold(_singular(joined)) in facts.skill_names


def stated(requirement: str, facts: MasterFacts, *, alternatives: bool = False) -> Stated | None:
    """The master lines that settle ``requirement``, or ``None`` when the row is to be left as it came (the module text)."""

    text = " ".join(requirement.split()).rstrip(". ")
    spans = list(_YEARS.finditer(text))
    if len(spans) > 1:
        return None
    years: int | None = None
    if spans:
        years = _number(spans[0].group(1))
        text = f"{text[: spans[0].start()]} {_YEARS_MARK} {text[spans[0].end():]}"
    tokens = [token.rstrip(".'’") or token for token in _TOKEN.findall(text)]
    folded = [token.casefold() for token in tokens]
    joins = _OR | _AND | _BREAK
    any_of = (alternatives or any(token in _OR or token in _ANY_OF for token in folded)) and not any(token in _AND for token in folded)

    # THE ALTERNATIVE TRACK ("a degree in X, or 8+ years of software engineering experience"): the part that is only the
    # years of a career is one way to meet the row, whatever the other parts ask for.
    if years is not None and any(token in _OR for token in folded):
        part: list[str] = []
        parts: list[list[str]] = [part]
        for token in folded:
            if token in _OR:
                part = []
                parts.append(part)
            else:
                part.append(token)
        mine = next(item for item in parts if _YEARS_MARK in item)
        words = [token for token in mine if token != _YEARS_MARK and token not in _BREAK]
        # The part must be whole: it ends the requirement or closes on "experience" ("5+ years developing or
        # operating X" is one phrase about X, not a track).
        whole = mine is parts[-1] or "experience" in words
        if whole and all(token in _CAREER_WORDS for token in words) and any(token in _FAMILY for token in words):
            found = _career_years(years, facts)
            if found is not None:
                return _cite(*found)

    # One segment per alternative (one in all when every term is required): its key terms, and whether "N years" stood in it.
    segments: list[tuple[list[_Term], bool]] = []
    terms: list[_Term] = []
    run: list[str] = []
    run_exact = False
    marked = False
    family = False
    career_only = True  # nothing but the words a plain "N years of experience" is made of
    seen_term = False

    def end_run() -> None:
        nonlocal run, run_exact, seen_term
        if run:
            terms.append(_Term(" ".join(run), run_exact and len(run) == 1))
            seen_term = True
        run, run_exact = [], False

    def end_segment() -> None:
        nonlocal terms, marked
        end_run()
        segments.append((terms, marked))
        terms, marked = [], False

    index = 0
    while index < len(tokens):
        token, low = tokens[index], folded[index]
        if token == _YEARS_MARK:
            end_run()
            marked = True
        elif low in _OR or low in _BREAK:
            end_segment() if any_of else end_run()
        elif low in _AND:
            end_run()
        else:
            # A name of the skills line, longest first: it may hold ignored words ("Distributed Systems").
            size = next(
                (
                    count for count in (4, 3, 2, 1)
                    if index + count <= len(tokens)
                    and all(folded[at] not in joins and tokens[at] != _YEARS_MARK for at in range(index, index + count))
                    and _skill(tokens[index: index + count], facts)
                ),
                0,
            )
            if size:
                end_run()
                terms.append(_Term(" ".join(tokens[index: index + size])))
                seen_term = True
                index += size
                continue
            if low in _FAMILY and (years is not None or low in _IGNORED):
                end_run()
                family = family or years is not None
            elif low in _IGNORED:
                end_run()
                if low not in _CAREER_WORDS:
                    career_only = False
                if seen_term and (low in _HAVING or low in _DOING):
                    any_of = False  # a second clause ("Python, with exposure to Go or Rust"): every term is required
            else:
                name, capitalised = _named(token)
                verb = index == 0 and capitalised and low in _VERBS and index + 1 < len(tokens) and folded[index + 1] in _VERB_FOLLOWS
                if not name or verb:
                    return None  # the requirement's own substance: not this check's to read
                run.append(_singular(token))
                run_exact = capitalised
        index += 1
    end_segment()

    def settle(wanted: Sequence[_Term]) -> Stated | None:
        if not wanted or len(wanted) > MAX_LINES:
            return None
        cited: list[Line | str] = []
        rules: list[str] = []
        for term in wanted:
            if years is not None:
                found = _term_years(term, years, facts)
                if found is None:
                    return None
                cited += found[0]
                rules.append(found[1])
            else:
                line = _line_for(term, facts)
                if line is None:
                    return None
                cited.append(line)
                rules.append(RULE_SKILLS if line.id is None else RULE_LINE)
        return _cite(cited, rules[0])

    def career() -> Stated | None:
        # Only "N years of software engineering experience": bare "N years of experience" does not say of what.
        if years is None or not career_only or not family:
            return None
        found = _career_years(years, facts)
        return None if found is None else _cite(*found)

    every = [term for found, _in in segments for term in found]
    if not any_of:
        return settle(every) if every else career()
    for found, in_segment in segments:
        # "a degree or 8 years of experience": the years are one of the alternatives.
        result = settle(found) if found else career() if in_segment else None
        if result is not None:
            return result
    return None


# --- the answer -------------------------------------------------------------------------------------------


def _key(text: object) -> str:
    return " ".join(str(text).split()).casefold() if isinstance(text, str) else ""


def settle_stated(
    matrix: Iterable[dict[str, object]], questions: Sequence[object], facts: MasterFacts,
) -> tuple[list[object], list[object], list[tuple[dict[str, object], str]]]:
    """``(questions kept, questions dropped, [(row, rule)])``: the ``unclear`` rows the master settles are made ``met``.

    IN PLACE, like ``suggestion_check.with_verbatim_evidence``: a settled row's ``status`` is ``met``, its
    ``resume_evidence`` the master's line(s) and its ``sources`` the ids of those that have one. A question is dropped
    when it names a settled row. Every other row and question is returned as it came.
    """

    settled: list[tuple[dict[str, object], str]] = []
    for row in matrix:
        requirement = row.get("requirement")
        if row.get("status") != "unclear" or not isinstance(requirement, str) or not requirement.strip():
            continue
        if is_eligibility_id(row.get("id")) or is_authorization_row(row):
            continue  # the setup's rows; sponsorship is a label
        found = stated(requirement, facts, alternatives=bool(row.get("alternatives")))
        if found is None:
            continue
        row["status"] = "met"
        row["resume_evidence"] = list(found.evidence)
        if found.sources:
            row["sources"] = list(found.sources)
        else:
            row.pop("sources", None)
        settled.append((row, found.rule))
    names = {_key(row.get("requirement")) for row, _ in settled}
    kept: list[object] = []
    dropped: list[object] = []
    for question in questions:
        named = _key(question.get("requirement")) if isinstance(question, Mapping) else ""
        (dropped if named and named in names else kept).append(question)
    return kept, dropped, settled


__all__ = [
    "MAX_LINES",
    "RULE_LINE",
    "RULE_SKILLS",
    "RULE_YEARS_ROLES",
    "RULE_YEARS_STATED",
    "Line",
    "MasterFacts",
    "Role",
    "Stated",
    "read_master",
    "settle_stated",
    "stated",
]
