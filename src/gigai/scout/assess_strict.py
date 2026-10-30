"""0.1.11.12 F1: the strict-met post-check, deterministic, over a v9 matrix and the whole master.

A Scout ``met`` must mean what a strict ATS screener sees in the submitted resume. The model reads leniently (a
summary line, the skills line, a project, a role older than the window, or a yes answer made a row ``met`` in 9 of
28 gold rows: ``research/scout/SPIKE-assessment-strictness.md`` section 4.1, option B). This module runs after the
answer is normalized and the stated check settled it, and only ever lowers a must-have row (``hard``/``askable``),
turns a bare ``unmet`` into a question, or (T8) reads a declared language as ``met``:

T1  a row with named tools (``alternatives`` less the open entries, "other X") is ``met`` only if a CLEAR line
    (a bullet or heading of a role within the window, or of a project) names one of them: a tool's word on the
    line, lower-case, trailing ``s`` dropped ("Rails generators" names Ruby on Rails), generic words ignored.
    Tools listed after "including", "such as", "e.g." or "like" are examples: one is enough; tools joined by
    "and" with no "or" between them are all required.
T2  a level word in the tool clause (proficient, fluent, deep, expert, strong, hands-on, advanced, mastery) is
    ``met`` only if a named tool sits in the current role's lines, or in two roles within the window.
T3  "N+ years of X": the months of the roles within the window that name X are summed when X is a named tool in
    the years clause ("8 years of Java"); any role, in or out of the window, when X is generic ("8 years of
    back-end experience"). ``met`` with the ``r-`` ids as sources when they reach N, else ``unclear``.
T4  a degree row is ``met`` with the ``e-`` id when a degree's field matches, or the row allows a related field.
T5  any other ``met`` row resting only on buried lines (summary, skills, Other, an old role) is ``unclear``; a
    spoken-language or collaboration-style row ("Strong English communication", "written and verbal
    communication", "teamwork", "self-starter") is a claim no resume line settles and keeps the model's status.
T6  a ``met`` row resting only on answers or stories is ``answered``: the candidate said yes; no line shows it.
T7  an ``unmet`` row that cites nothing is ``unclear``: a silence is a question, not a gap.
T8  a programming language the candidate declares (a name of a skills line, or of a saved answer the row cites that
    does not deny it) is a fact about the candidate, not a dated one (operator decision 2026-10-30, dossier-A): a row
    that is about languages alone (``LANGUAGES``; one of an "or" list, all of an "and" or slashed list, no framework or
    infrastructure beside them, nothing but filler words around them: "payments systems in Go" is about payments) is
    ``met`` whether the model said ``met`` or ``unclear``, keeps its sources, and T1/T2 do not lower it. A years row
    stays T3, a degree row T4, an ``unmet`` row T7.

Every row the check touches gets a ``code_note`` naming the posting's term, the lines found and why they do not
count. ``elig-`` rows and the authorization row are never touched. Pure: the master is a
:class:`~gigai.scout.assess_master.StrictMaster`, the matrix the normalized rows (dicts, changed in place).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, MutableMapping, Sequence

from .assess_master import LINE_DEGREE, LINE_OTHER, LINE_ROLE, LINE_SKILLS, LINE_SUMMARY, StrictEntry, StrictLine, StrictMaster
from .find_jobs.contracts import MAX_ROW_SOURCES
from .requirements_list import is_eligibility_id
from .resume_gate import is_authorization_row, is_mandatory
from .stated_check import _NUMBER_WORDS, _YEARS  # noqa: SLF001 - the one reading of "N+ years" the stated check uses

MET = "met"
UNMET = "unmet"
UNCLEAR = "unclear"
ANSWERED = "answered"

#: How long a ``code_note`` may run.
MAX_CODE_NOTE_CHARS = 400
#: Words that name no tool: the posting's "systems", "tools", "experience", the joining words, and the English a tool's
#: name may carry ("New Relic", "Google Cloud Platform", "Spring Boot" are named by their other word).
GENERIC_TOKENS = frozenset(
    "system experience framework tool language technology technologies platform on and or in with of the a an to for using skill"
    " new modern cloud web server service data open source native standard stack based software engineering development"
    " programming application solution product enterprise management design next database api i".split()
)
#: An ``alternatives`` entry that is no tool: "other OO languages", "a comparable framework", "similar tools", "any OO language".
OPEN_WORDS = frozenset({"other", "similar", "comparable", "equivalent", "another", "related", "any", "etc"})
_ARTICLES = frozenset({"a", "an", "some", "or"})
#: An ``alternatives`` entry shaped like a tool's name (Go, C++, Node.js, Ruby on Rails, k8s, gRPC), not a practice ("payment
#: systems", "unified APIs", "backend", "Python backend and distributed systems"): opens with a capital, a digit or a symbol
#: and runs at most three words, or is one word with a capital or a digit inside.
_TOOL_SHAPED = re.compile(r"\A(?:[A-Z0-9#+.][\w+#.\-]*(?:\s+[\w+#.\-]+){0,2}|[a-z]+[A-Z0-9][\w+#.\-]*)\Z")
#: T2: a word that asks for depth, when it governs the tool clause.
_LEVEL = re.compile(r"\b(proficient|proficiency|fluent|fluency|deep|expert|expertise|strong|hands-on|advanced|mastery)\b", re.IGNORECASE)
#: ...unless it qualifies a skill ("strong communication skills and experience with Rust"), or an understanding of something
#: else, a phrase of its own before the tool's ("a deep understanding of distributed systems and experience with Kafka"; "strong
#: knowledge of AWS, Terraform and Postgres" governs the list).
_LEVEL_SKILLS = re.compile(r"\b(?:deep|strong|expert|advanced)\s+(?:\w+\s+)?\w+ skills\b", re.IGNORECASE)
_LEVEL_OF_NOUN = re.compile(
    r"\b(?:deep|strong|expert|advanced)\s+(?:\w+\s+)?(?:understanding|knowledge|grasp|background|foundation)\b"
    r"(?=.*\b(?:experience|familiarity|exposure|ability|comfort|proficiency|track record)\b)",
    re.IGNORECASE | re.DOTALL,
)
#: T2: the clause ends at a stop (";" or a sentence's "."; a ":" or an "e.g." opens the list it governs) or at a comma that
#: opens a new phrase ("Experience with X, and a strong Y"; ", and Postgres" continues the list).
_CLAUSE_BREAK = re.compile(r"[;.]|,\s*(?:with|plus|including|as well)\b|,\s*and\s+(?=[a-z])", re.IGNORECASE)
_ABBREVIATION = re.compile(r"\b([ei])\.\s?([ge])\.", re.IGNORECASE)
#: T1: the words that open a list of examples, so one of the tools is enough however the list is joined.
_EXAMPLES = re.compile(r"\b(?:including|such as|like|e\.?g\.?|i\.?e\.?|for example|for instance)\W*$", re.IGNORECASE)
#: T3: the years clause runs from "N+ years" to the next stop; a named tool in it is the X of "N years of X".
_YEARS_CLAUSE_END = re.compile(r"[,;:.()]|\b(?:including|with a track|and)\b", re.IGNORECASE)
#: T3: a tool is a name (Java, TypeScript, C#), not a phrase ("payment systems"), when it opens with a capital, a digit or a symbol.
_NAMED = re.compile(r"\A[A-Z0-9#+.]")
#: T4: a degree row ("a high degree of ownership" is none).
_DEGREE = re.compile(r"\b(?:degree(?!\s+of\b)|bachelor(?:'s|s)?|master(?:'s|s)|b\.?s\.?c?|b\.?a\.?|m\.?s\.?c?|ph\.?d|doctorate)\b", re.IGNORECASE)
_RELATED_FIELD = re.compile(r"\b(?:related|relevant|similar|equivalent|comparable)\b", re.IGNORECASE)
#: Words of a degree row that name no field (the "s" of "bachelor's", the row's "required"/"preferred").
_DEGREE_WORDS = frozenset(
    "degree bachelor master bs ba ms msc bsc phd doctorate field discipline related relevant equivalent similar comparable or in a an of the"
    " practical work s required preferred minimum plus strong".split()
)
#: T4: a field of engineering the spec names as counting for "engineering" (an Electronics and Communication degree).
_ENGINEERING_FIELDS = frozenset({"electronic", "electrical", "communication", "mechanical", "civil", "chemical", "software", "computer"})
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_ANSWER_PREFIX = "A "
#: Evidence that quotes no resume line: a story-bank entry or an answer the prompt showed (``suggestion_check``).
_EVIDENCE_NOTES = ("Story bank ", "Your answer:")
#: T7: a cited bank answer, the prompt's own form for a reused answer (``assess.md``: "Story bank <question_id>: <the answer>");
#: a story (``Story bank story:<slug>: ...``) is a narrative, not the candidate's yes or no.
_BANK_ANSWER = re.compile(r"\A\s*(?:Your answer:|Story bank (?!story:)\S+:)")
#: ``names_in``: a run of capitalised words ("New Relic", "Go", "SQS"), and the stop that opens a sentence before one.
_CAPS_RUN = re.compile(r"\b[A-Z][A-Za-z0-9+#]*(?:\.[A-Za-z0-9]+)*(?:\s+[A-Z][A-Za-z0-9+#]*(?:\.[A-Za-z0-9]+)*)*")
_SENTENCE_START = re.compile(r"[.;!?]\s*$")
_PARENTHESIS = re.compile(r"\([^)]*\)")
#: T5 does not apply to a row (with no named tool, years or degree) that claims a working language or a way of working.
_SOFT_CLAIM = re.compile(
    r"\b(?:english|spanish|french|german|japanese|language fluency|fluent in [a-z]+\b(?! (?:code|tool|framework|language))|communicat\w*"
    r"|collaborat\w*|teamwork|team player|self[- ]starter|interpersonal|written and verbal|verbal and written)\b",
    re.IGNORECASE,
)
#: T8: a programming language the candidate may declare on the skills line (casefolded). SQL is in (a query language the
#: skills line declares the same way); frameworks and runtimes (Node.js, React, Rails) are out, so "TypeScript and Node"
#: is no language row.
LANGUAGES = frozenset(
    "go golang rust java kotlin python typescript javascript c c++ c# ruby swift scala php elixir erlang haskell clojure dart r lua perl sql".split()
)
_LANGUAGE_ALIASES = {"golang": "go", "js": "javascript", "ts": "typescript"}
#: T8: an answer that takes something back ("no, I have not used Rust") declares no language.
_DENIAL = re.compile(r"\b(?:no|not|never|\w+n['’]t)\b", re.IGNORECASE)
#: T8: what separates the names of a skills line ("Languages: Go/Ruby/Python", "Python (FastAPI, Django)") or the words of an
#: answer; a hyphen stays, so "Objective-C" does not declare C.
_NAME_SPLIT = re.compile(r"[\s/,;:()\[\]·•]+")


@dataclass(frozen=True)
class StrictChange:
    """What the check did to one row: ``before`` to ``after`` (equal when the row kept its status and only gained sources)."""

    row_id: str | None
    requirement: str
    before: str
    after: str
    step: str
    note: str

    @property
    def lowered(self) -> bool:
        return self.before != self.after


# --- tokens -------------------------------------------------------------------------------------------------


def tokens(text: str) -> frozenset[str]:
    """The words of ``text`` as the check compares them: lower-case, ``.``-trimmed, trailing ``s`` dropped (``rails`` -> ``rail``), generic ones left out.

    A dotted name gives its stem and its dot-free form too (``Node.js`` -> ``node.js``, ``nodejs``, ``node``: "Node/Nest" names it).
    """

    found: set[str] = set()
    for word in _WORD.findall(text.casefold().replace("/", " ").replace("-", " ")):
        word = word.strip(".")
        if len(word) >= 4 and word.endswith("s") and not word.endswith(("ss", "js")):
            word = word[:-1]
        forms = {word, word.replace(".", ""), word.split(".", 1)[0]} if "." in word else {word}
        found.update(form for form in forms if form and form not in GENERIC_TOKENS)
    return frozenset(found)


def named_tools(row: Mapping[str, object]) -> tuple[str, ...]:
    """The row's ``alternatives`` that name a tool: an entry opening "other", "similar", "comparable", "equivalent", "any" or
    "another" is dropped, and so is one not shaped like a name (a practice, "payment systems", see ``_TOOL_SHAPED``).

    A row the model gave no ``alternatives`` names its tools itself: the capitalised names in the requirement
    ("... backend systems in Go", "eventing (Kafka, SNS, SQS)"), see :func:`names_in`.
    """

    value = row.get("alternatives")
    if not isinstance(value, (list, tuple)):
        return names_in(str(row.get("requirement") or ""))
    return tuple(item for item, kind in _alternatives(value) if kind == "tool")


def practice_alternatives(row: Mapping[str, object]) -> tuple[str, ...]:
    """The row's ``alternatives`` that word a practice ("production deployment pipelines"): neither open nor shaped like a name."""

    value = row.get("alternatives")
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item, kind in _alternatives(value) if kind == "practice")


def _alternatives(value: Sequence[object]) -> list[tuple[str, str]]:
    """Each non-empty entry with its kind: ``open``, ``tool`` or ``practice``."""

    found: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        words = [word for word in item.casefold().split() if word not in _ARTICLES]
        if not words or words[0].strip(",") in OPEN_WORDS:
            kind = "open"
        elif tokens(item) and _TOOL_SHAPED.match(item.strip()):
            kind = "tool"
        else:
            kind = "practice"
        found.append((item.strip(), kind))
    return found


def names_in(requirement: str) -> tuple[str, ...]:
    """The capitalised names a requirement carries, each run of capitalised words as one ("New Relic", "Go", "Node.js"): never the
    word that opens a sentence (unless the run is the whole requirement, a row the model split down to "Postgres", or a level
    word opens the run, "Strong Datadog experience" names Datadog; a ":" or an "e.g." opens a list, "Nice to have: Go" names Go),
    never an acronym ("CS", "AI", "SRE", "LLM", "PMs": a term the row may word a practice with, not a tool; the acronyms that are
    tools, "AWS", "SQS", come through the model's ``alternatives``; "C#", "C++" and "C" are languages), never a generic word."""

    found: list[str] = []
    requirement = _ABBREVIATION.sub(r"\1\2", requirement)
    whole = requirement.strip()
    for run in _CAPS_RUN.finditer(requirement):
        words = run.group(0).split()
        if run.group(0) != whole and (run.start() == 0 or _SENTENCE_START.search(requirement[: run.start()])):
            if len(words) < 2 or not _LEVEL.fullmatch(words[0]):
                continue
            words = words[1:]
        name = " ".join(words)
        if _SOFT_CLAIM.fullmatch(name):
            continue  # "Strong English communication": a spoken language is no tool
        if any(tokens(word) and not _is_acronym(word) for word in words) and name not in found:
            found.append(name)
    return tuple(found)


def _is_acronym(word: str) -> bool:
    """"CS", "LLM", "PMs": all capitals, more than one letter once the symbols of a language ("C#", "C++") are off."""

    letters = word.rstrip("s").strip("#+")
    return len(letters) > 1 and letters.isupper()


class _Matcher:
    """Where each named tool stands in the master: which lines name it (any non-generic word of the tool, whole, on the line).

    A role's heading line is read by its sublines (dates, location, a stack line), not its title: a company named like a tool
    ("Datadog") is no line that names the tool.
    """

    def __init__(self, master: StrictMaster) -> None:
        self.master = master
        entries = {entry.id: entry for entry in master.entries}
        self.line_tokens = {
            line.id: tokens(" ".join(entries[line.id].sublines) if line.heading and line.kind == LINE_ROLE and line.id in entries else line.text)
            for line in master.lines.values()
        }

    def lines_naming(self, tool: str) -> tuple[StrictLine, ...]:
        own = tokens(tool)
        return tuple(line for line in self.master.lines.values() if own & self.line_tokens[line.id])

    def names_any(self, text_tokens: frozenset[str], tool: str) -> bool:
        return bool(tokens(tool) & text_tokens)


# --- the words of a note --------------------------------------------------------------------------------------


def _year(months: int | None) -> str:
    return "?" if months is None else str(months // 12)


def _where(master: StrictMaster, line: StrictLine) -> str:
    """How a note names a line: its id and, for a role, the role and when it ended."""

    if line.kind == LINE_SUMMARY:
        return f"{line.id} (summary)"
    if line.kind == LINE_SKILLS:
        return f"{line.id} (skills line)"
    if line.kind == LINE_OTHER:
        return f"{line.id} (Other)"
    entry = master.entry_of(line.id)
    if entry is None:
        return line.id
    if entry.kind == LINE_DEGREE:
        return f"{line.id} (education)"
    if entry.kind != "role":
        return f"{line.id} (project)"
    if entry.current:
        return f"{line.id} (current role)"
    when = "undated" if entry.end is None else f"ended {_year(entry.end)}"
    return f"{line.id} ({'old role' if entry.old else 'role'}, {when})"


def _join(parts: Sequence[str]) -> str:
    parts = list(parts)
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _note(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_CODE_NOTE_CHARS else text[: MAX_CODE_NOTE_CHARS - 1].rstrip() + "…"


# --- the steps ------------------------------------------------------------------------------------------------


def _requires_all(requirement: str, tools: Sequence[str]) -> tuple[str, ...]:
    """The tools the row words as all required: joined by "and" with no "or" between the first and the last, not opened as
    examples, and not in a parenthesis ("TypeScript (and modern JavaScript) plus Node.js" requires TypeScript and Node.js). Empty when any one is enough."""

    text = _PARENTHESIS.sub(" ", requirement.casefold())
    places = {tool: text.find(tool.casefold()) for tool in tools}
    core = tuple(tool for tool, place in places.items() if place >= 0)
    if len(core) < 2 or _EXAMPLES.search(text[: min(places[tool] for tool in core)]):
        return ()
    between = text[min(places[tool] for tool in core) : max(places[tool] for tool in core) + 1]
    if re.search(r"\bor\b|/", between):
        return ()
    return core if re.search(r"\band\b|&|\bplus\b", between) else ()


def _level_word(requirement: str, tools: Sequence[str]) -> str | None:
    """T2: the level word that governs the tool clause of ``requirement``, or ``None``."""

    text = requirement
    places = [text.casefold().find(tool.casefold()) for tool in tools]
    places = [place for place in places if place >= 0]
    if not places:
        return None
    clause = _ABBREVIATION.sub(r"\1\2", text[: min(places)])
    breaks = list(_CLAUSE_BREAK.finditer(clause))
    if breaks:
        clause = clause[breaks[-1].end() :]
    clause = _LEVEL_OF_NOUN.sub(" ", _LEVEL_SKILLS.sub(" ", clause))
    found = _LEVEL.search(clause)
    return None if found is None else found.group(1).casefold()


def _years_required(requirement: str) -> tuple[int, str] | None:
    """T3: ``(N, the years clause)`` of "N+ years of X", or ``None``."""

    found = _YEARS.search(requirement)
    if found is None:
        return None
    number = found.group(1).casefold()
    years = int(number) if number.isdigit() else _NUMBER_WORDS.get(number)
    if years is None:
        return None
    rest = requirement[found.end() :]
    stop = _YEARS_CLAUSE_END.search(rest)
    return years, rest[: stop.start()] if stop else rest


def _years_is_alternative(requirement: str) -> bool:
    """T4: whether "N+ years" of a degree row is a track the posting offers instead of the degree ("A master's degree, or 8+ years of experience")."""

    found = _YEARS.search(requirement)
    return found is not None and re.search(r"\bor\b", requirement[: found.start()], re.IGNORECASE) is not None


def _months_of(roles: Sequence[StrictEntry]) -> int:
    """The months the roles cover, overlaps counted once."""

    spans = sorted((role.start, role.end) for role in roles if role.start is not None and role.end is not None)
    total = 0
    reach: int | None = None
    for start, end in spans:
        if reach is None or start > reach:
            total += end - start
            reach = end
        elif end > reach:
            total += end - reach
            reach = end
    return total


def _sources_of(row: Mapping[str, object]) -> tuple[str, ...]:
    value = row.get("sources")
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, (list, tuple)) else ()


def _set_sources(row: MutableMapping[str, object], sources: Sequence[str]) -> None:
    kept: list[str] = []
    for item in sources:
        if item not in kept:
            kept.append(item)
    if kept:
        row["sources"] = kept[:MAX_ROW_SOURCES]
    else:
        row.pop("sources", None)


def _lower(row: MutableMapping[str, object], status: str, note: str) -> None:
    row["status"] = status
    row["code_note"] = _note(note)


def _step_tools(row: MutableMapping[str, object], requirement: str, tools: Sequence[str], matcher: _Matcher, *, practices: Sequence[str] = ()) -> tuple[str, str] | None:
    """T1 and T2 over a row with named tools: ``(step, note)``; a row that keeps ``met`` gets the found lines appended to its sources.

    T2's level word is read from the requirement, else from the row's ``class_basis`` (the posting's words: a model that
    split "strong knowledge of AWS, Terraform and Postgres" into three rows keeps the level word only there).

    ``practices``: the row's practice alternatives (``practice_alternatives``). An "or" list none of whose named tools is on a
    line ("MLOps, LLMOps, or production deployment pipelines") rests on the practice, read by T5 over the row's sources.
    """

    master = matcher.master
    clear: dict[str, list[StrictLine]] = {}
    buried: dict[str, list[StrictLine]] = {}
    for tool in tools:
        for line in matcher.lines_naming(tool):
            (clear if master.is_clear(line.id) else buried).setdefault(tool, []).append(line)
    required_all = _requires_all(requirement, tools)
    shown = [tool for tool in tools if tool in clear]
    if not shown and practices and not required_all:
        return _step_sources(row, master)
    if not shown or any(tool not in clear for tool in required_all):
        parts: list[str] = []
        for tool in tools:
            if tool in clear:
                parts.append(f"{tool} on {_join([_where(master, line) for line in clear[tool]])}")
            elif tool in buried:
                parts.append(f"{tool} only on {_join([_where(master, line) for line in buried[tool]])}")
            else:
                parts.append(f"{tool} on no line of your master")
        rule = "every tool is required" if required_all else "a met row needs one of them on a line of a role within the window or a project"
        _lower(row, UNCLEAR, f"Strict read (T1): {'; '.join(parts)}; {rule}.")
        return ("T1", str(row["code_note"]))
    found_lines = [line for tool in shown for line in clear[tool]]
    level = _level_word(requirement, tools)
    if level is None and isinstance(row.get("class_basis"), str):
        level = _level_word(str(row["class_basis"]), tools)
    if level is not None and practices and not required_all and any(master.is_clear(item) and item not in {line.id for line in found_lines} for item in _sources_of(row)):
        level = None  # "Deep experience with Kafka, Pulsar, or event streaming": a clear cited line beyond the tool's own carries the practice
    if level is not None:
        in_current = [line for line in found_lines if (entry := master.entry_of(line.id)) is not None and entry.current]
        roles = {entry.id for line in found_lines if (entry := master.entry_of(line.id)) is not None and entry.kind == "role"}
        if not in_current and len(roles) < 2:
            places = _join([f"{tool} on {_join([_where(master, line) for line in clear[tool]])}" for tool in shown])
            _lower(row, UNCLEAR, f"Strict read (T2): '{level}' asks for depth; {places}; a row with a level word needs the tool in your current role or in two roles within the window.")
            return ("T2", str(row["code_note"]))
    _set_sources(row, [*_sources_of(row), *(line.id for line in found_lines)])
    places = _join([f"{tool} on {_join([_where(master, line) for line in clear[tool]])}" for tool in shown])
    row["code_note"] = _note(f"Strict read ({'T2' if level is not None else 'T1'}): {places}.")
    return ("T2" if level is not None else "T1", str(row["code_note"]))


def _step_years(row: MutableMapping[str, object], years: int, clause: str, tools: Sequence[str], matcher: _Matcher) -> tuple[str, str]:
    """T3: the roles' months against "N+ years"; the X of "N years of X" is the row's named tools when the years clause names one
    ("3+ years in Scala, Ruby or C++": the roles naming any of the three)."""

    master = matcher.master
    names = [tool for tool in tools if _NAMED.match(tool)]
    named = names if any(tool.casefold() in clause.casefold() for tool in names) else []
    if named:
        roles = [role for role in master.roles if role.in_window and any(matcher.names_any(tokens(line.text), tool) for tool in named for line in master.lines_of(role))]
        subject = _join(named)
    else:
        roles = list(master.roles)
        subject = "any role"
    months = _months_of(roles)
    span = f"{months // 12} years {months % 12} months"
    ids = _join([role.id for role in roles]) or "no dated role"
    if months >= years * 12:
        _set_sources(row, [role.id for role in roles])
        row["code_note"] = _note(f"Strict read (T3): {span} with {subject} across {ids}; the posting asks {years}+.")
        return ("T3", str(row["code_note"]))
    _lower(row, UNCLEAR, f"Strict read (T3): your roles within the window sum to {span} with {subject} ({ids}); the posting asks {years}+ years.")
    return ("T3", str(row["code_note"]))


def _step_degree(row: MutableMapping[str, object], requirement: str, master: StrictMaster) -> tuple[str, str]:
    """T4: a degree entry whose field matches, or the row's related-field allowance."""

    degrees = master.degrees
    fields = frozenset(word for word in tokens(requirement) if word not in _DEGREE_WORDS)
    if not degrees:
        _lower(row, UNCLEAR, "Strict read (T4): the posting asks a degree; your master has no education entry.")
        return ("T4", str(row["code_note"]))
    matched: StrictEntry | None = None
    for degree in degrees:
        own = tokens(degree.text)
        if not fields or fields & own or ("engineering" in fields and own & _ENGINEERING_FIELDS):
            matched = degree
            break
    if matched is None and _RELATED_FIELD.search(requirement):
        matched = degrees[0]
    held = _join([f"{degree.heading} ({degree.id})" for degree in degrees])
    if matched is None:
        _lower(row, UNCLEAR, f"Strict read (T4): the posting asks a degree in {' '.join(sorted(fields))}; you hold {held}; the row allows no related field.")
        return ("T4", str(row["code_note"]))
    _set_sources(row, [matched.id])
    row["code_note"] = _note(f"Strict read (T4): {matched.heading} ({matched.id}) meets the degree row.")
    return ("T4", str(row["code_note"]))


def _only_answers(sources: Sequence[str]) -> bool:
    return bool(sources) and all(item.startswith(_ANSWER_PREFIX) for item in sources)


def _step_sources(row: MutableMapping[str, object], master: StrictMaster) -> tuple[str, str] | None:
    """T5 over a met row's sources; ``None`` when a clear line is among them."""

    sources = _sources_of(row)
    if any(master.is_clear(item) for item in sources):
        return None
    lines = [master.lines[item] for item in sources if item in master.lines]
    if not sources:
        why = "the row cites no line of your master"
    elif lines:
        why = f"found only on {_join([_where(master, line) for line in lines])}"
    else:
        why = f"the cited sources ({_join(sources)}) are no line of your master"
    _lower(row, UNCLEAR, f"Strict read (T5): {why}; a met row needs a line of a role within the window or a project.")
    return ("T5", str(row["code_note"]))


def _cites_resume(row: Mapping[str, object]) -> bool:
    """T7: whether an unmet row's evidence quotes the resume at all (a bank note, ``Story bank <id>: ...``, is none)."""

    value = row.get("resume_evidence")
    if not isinstance(value, (list, tuple)):
        return False
    return any(isinstance(item, str) and item.strip() and not item.lstrip().startswith(_EVIDENCE_NOTES) for item in value)


def _answered(row: Mapping[str, object]) -> bool:
    """T7: whether an unmet row rests on the candidate's own answer (``Your answer: ...`` or a cited bank answer in the evidence,
    ``_BANK_ANSWER``, or an ``A ...`` source): an explicit no is a contradicting fact, a true gap, not a silence."""

    evidence = row.get("resume_evidence")
    quoted = isinstance(evidence, (list, tuple)) and any(isinstance(item, str) and _BANK_ANSWER.match(item) for item in evidence)
    return quoted or any(item.startswith(_ANSWER_PREFIX) for item in _sources_of(row))


# --- T8: declared languages ---------------------------------------------------------------------------------


def _language(name: str) -> str | None:
    """The language ``name`` names, casefolded with the aliases folded ("Golang" -> "go"), or ``None`` when it is none."""

    word = name.strip().casefold().rstrip(".")
    word = _LANGUAGE_ALIASES.get(word, word)
    return word if word in LANGUAGES else None


def _languages_in(text: str) -> frozenset[str]:
    return frozenset(language for language in map(_language, _NAME_SPLIT.split(text)) if language is not None)


def declared_languages(master: StrictMaster) -> frozenset[str]:
    """T8: the languages the master's skills lines declare (casefolded)."""

    return frozenset().union(*(_languages_in(text) for text in master.skills))


def _answers_of(row: Mapping[str, object]) -> tuple[str, ...]:
    """T8: the candidate's answers the row cites: ``Your answer: ...`` / ``Story bank <id>: ...`` evidence, and the id of an ``A ...`` source."""

    evidence = row.get("resume_evidence")
    quoted = [item for item in evidence if isinstance(item, str) and _BANK_ANSWER.match(item)] if isinstance(evidence, (list, tuple)) else []
    return (*quoted, *(item[len(_ANSWER_PREFIX) :] for item in _sources_of(row) if item.startswith(_ANSWER_PREFIX)))


#: T8: the words a language row may carry beside its language names and still be about the language alone ("Strong experience
#: building and operating production backend systems in Go, our primary backend language"). A word outside this set
#: ("payments", "reporting", "teams", "trading") makes the row about something more, which T8 does not read.
_LANGUAGE_FILLER = frozenset(
    """
    a an the our your you we have has is are be must should with in of on for to at as and or plus also
    experience experienced expertise expert knowledge skill skills skilled familiarity familiar comfortable proficiency proficient
    fluency fluent strong deep solid excellent good great advanced extensive significant demonstrated proven hands-on
    working write writing code coding develop developing development build building operate operating ship shipping
    production production-quality production-grade high-quality well-tested clean maintainable scalable reliable modern
    backend back-end frontend front-end full-stack server-side systems system services software applications programs programming
    language languages fundamentals concepts primary main one more such like similar other another equivalent comparable any
    including especially ideally preferably required preferred nice level senior using use ability able
    e.g i.e etc
    """.split()
)


def _slash_joined(requirement: str, tools: Sequence[str]) -> bool:
    """T8: whether a tool is slash-joined to a neighbour in the requirement ("Java/JavaScript"): as the stated check reads it, every
    name of such a list is required ("and/or" joins no name)."""

    text = requirement.casefold()
    return any(re.search(rf"{re.escape(tool.casefold())}\s*/|/\s*{re.escape(tool.casefold())}", text) for tool in tools)


def _tool_pattern(tool: str) -> re.Pattern[str]:
    """The tool's name as a whole word in casefolded text ("c" is not the "c" of "experience", "c++" keeps its pluses)."""

    return re.compile(rf"(?<![\w#+]){re.escape(tool.casefold())}(?![\w#+])")


def _only_language_words(requirement: str, tools: Sequence[str]) -> bool:
    """T8: whether nothing but the tool names, ``_LANGUAGE_FILLER`` and punctuation make up the requirement (a parenthesis is dropped)."""

    text = _PARENTHESIS.sub(" ", requirement.casefold())
    for tool in sorted(tools, key=len, reverse=True):
        text = _tool_pattern(tool).sub(" ", text)
    for word in re.split(r"[\s/,;:!?]+", text):
        word = word.strip("'’\"").rstrip(".")
        if word and not re.fullmatch(r"[\W_]+", word) and word not in _LANGUAGE_FILLER:
            return False
    return True


def _step_declared(row: MutableMapping[str, object], requirement: str, tools: Sequence[str], on_skills: frozenset[str]) -> tuple[str, str] | None:
    """T8: ``("T8", note)`` with the row ``met`` when it needs only languages the candidate declares (the module text), else ``None``.

    A language row names nothing but languages (a framework or infrastructure beside them: T1/T2 as before), names at least one
    in its own words (a language only the model's ``alternatives`` guessed, "Backend development experience", is not the row's),
    and says nothing beside them but filler (``_only_language_words``: "payments systems in Go" is about payments). It needs
    every tool of an "and" list (``_requires_all``) or a slashed list ("Java/JavaScript", ``_slash_joined``) and any one language
    of any other list. A cited answer that denies ("no", "not", "never", "n't") leaves the row as it came.
    """

    if not tools or not all(_language(tool) for tool in tools):
        return None
    text = requirement.casefold()
    tools = [tool for tool in tools if _tool_pattern(tool).search(text)]
    if not tools or not _only_language_words(requirement, tools):
        return None
    required = _requires_all(requirement, tools) or (tuple(tools) if _slash_joined(requirement, tools) else ())
    named = [(tool, _language(tool)) for tool in (required or tools)]
    answers = _answers_of(row)
    if any(_DENIAL.search(text) for text in answers):
        return None
    in_answer = frozenset().union(*(_languages_in(text) for text in answers))
    skills_tools = [tool for tool, language in named if language in on_skills]
    answer_tools = [tool for tool, language in named if language in in_answer and language not in on_skills]
    found = len(skills_tools) + len(answer_tools)
    if not found or (required and found < len(named)):
        return None
    parts = []
    if skills_tools:
        parts.append(f"{_join(skills_tools)} {'is' if len(skills_tools) == 1 else 'are'} on your skills line")
    if answer_tools:
        parts.append(f"{_join(answer_tools)} {'is' if len(answer_tools) == 1 else 'are'} in your answer")
    row["status"] = MET
    row["code_note"] = _note(f"Strict read (T8): {' and '.join(parts)}.")
    return ("T8", str(row["code_note"]))


# --- the check ------------------------------------------------------------------------------------------------


def strict_rows(matrix: Sequence[MutableMapping[str, object]], master: StrictMaster, id_of_row: Mapping[int, str] | None = None) -> tuple[StrictChange, ...]:
    """Run the check over ``matrix`` (changed in place) against ``master``; what it did, per touched row, in matrix order.

    ``id_of_row``: a row's id by ``id(row)`` when the row does not carry one (the live path names a row that way).
    """

    matcher = _Matcher(master)
    declared = declared_languages(master)
    changes: list[StrictChange] = []
    for row in matrix:
        row_id = row.get("id") if isinstance(row.get("id"), str) else (id_of_row or {}).get(id(row))
        if is_eligibility_id(row_id) or is_authorization_row(row) or not is_mandatory(row):
            continue
        requirement = str(row.get("requirement") or "")
        before = str(row.get("status") or "")
        step_note: tuple[str, str] | None = None
        tools = named_tools(row)
        years = _years_required(requirement)
        if before in (MET, UNCLEAR) and tools and years is None and not _DEGREE.search(requirement):
            step_note = _step_declared(row, requirement, tools, declared)
        if step_note is not None:
            pass
        elif before == MET:
            sources = _sources_of(row)
            if _DEGREE.search(requirement):
                step_note = _step_degree(row, requirement, master)  # a degree row's "alternatives" name degrees, not tools
                if row["status"] != MET and years is not None and _years_is_alternative(requirement):
                    # "A master's degree, or 8+ years of experience": the degree does not hold, so the years track is read (T3) over the whole career.
                    row["status"] = MET
                    step_note = _step_years(row, years[0], years[1], (), matcher)
            elif tools:
                step_note = _step_tools(row, requirement, tools, matcher, practices=practice_alternatives(row))
            if row["status"] != MET or _DEGREE.search(requirement):
                pass
            elif years is not None:
                step_note = _step_years(row, years[0], years[1], tools, matcher)
            elif not tools and not _SOFT_CLAIM.search(requirement):
                step_note = _step_sources(row, master)
            if _only_answers(sources) and (row["status"] == UNCLEAR or step_note is None):
                # T6: the row rested on a yes answer alone (no line of the master showed it): the candidate said so.
                _lower(row, ANSWERED, "Strict read (T6): you said yes, and no resume line shows it; add a line to your resume.")
                step_note = ("T6", str(row["code_note"]))
        elif before == UNMET and not _cites_resume(row) and not _answered(row):
            _lower(row, UNCLEAR, "Strict read (T7): the row says unmet and cites no resume line; a silence is a question, not a gap.")
            step_note = ("T7", str(row["code_note"]))
        if step_note is not None:
            changes.append(StrictChange(row_id, requirement, before, str(row["status"]), *step_note))
    return tuple(changes)


__all__ = [
    "ANSWERED",
    "GENERIC_TOKENS",
    "LANGUAGES",
    "MAX_CODE_NOTE_CHARS",
    "OPEN_WORDS",
    "StrictChange",
    "declared_languages",
    "named_tools",
    "names_in",
    "practice_alternatives",
    "strict_rows",
    "tokens",
]
