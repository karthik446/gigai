"""0.1.11 C2 + C3 (orchestrator #51): what code guarantees about an assessment's evidence and suggestions. Pure; no model call.

C2, EVIDENCE IS VERBATIM.  For a ``met`` row with sources, the evidence GigAI stores and shows is the cited master
line(s) themselves, taken by id (:func:`verbatim_evidence`), in the order the row cites them, never the model's
paraphrase of them. An answer a row cites is shown as the answer's own text (a story as ``Story bank <id>: <summary>``).
A row that is not ``met``, one without a usable source, and a prompt that showed no ids keep what the model wrote.

C3, THE SUGGESTION CHECK (:func:`check_suggestions`).  A dropped suggestion costs nothing and a stretched one fails the
job, so the check keeps only what its own premises allow:

* ``reword`` / ``keyword`` / ``order`` name a line of the master, and the row they name is one that line is a source of;
* ``reword`` / ``keyword``: the ``posting_phrase`` is made of words the cited line (or an answer the user gave) already
  contains; a phrase that brings a word the line lacks would write a new claim, so the suggestion is dropped;
* ``order`` moves a line inside its own role: one that asks for a line of a later role to lead the whole Experience
  section, or a line that is in no role (the summary, the skills), is dropped;
* ``master_line`` is for a fact an answer or a story holds: the row it names cites one. Without that it is a ``gap``
  (an invitation to answer) on a row that is not met, and dropped on a met row (nothing is missing there);
* a ``gap`` that carries a ``posting_phrase`` names a requirement the phrase belongs to: a phrase of other words than the
  row's own is dropped.

* 0.1.11.3 Q1, NO FALSE "SILENT" (:func:`silent_but_stated`): a suggestion whose ``why`` says the resume is silent on,
  does not mention or does not show a tool, a skill or a number that a line of the master states is dropped
  (``silent_but_in_master``). Only a claim that is about named terms alone is judged: one that also names something
  else ("does not show leading a Kafka migration") is left as it is.
* 0.1.11.4 Q4, EVERY TERM AND EVERY PLACE OF THE CLAIM (:func:`false_claim`). (1) A claim that names several terms
  ("does not mention Kafka or Flink") is false as soon as the master states ONE of them, and the suggestion is dropped
  whole (code does not rewrite the sentence); a name of several words ("Kafka Streams") is one term. A claim with a
  number is read as before (the number and its tool in one line). (2) A claim about the roles ("Kafka is only in
  Skills", "is only listed", "is not shown in a role", "no bullet mentions Kafka") is false when a bullet of a role or
  a project states the term (``only_skills_but_in_role``). (3) A suggestion that names a city no text the prompt
  showed names (:func:`names_unknown_place`) is dropped (``place_not_in_posting``); the same check drops a question
  (:func:`without_unknown_places`).

An empty list is a good answer.

0.1.11.3 Q3, A CITATION IS ONE LINE (:func:`clean_citations`).  Evidence that code did not write (a row with no usable
source, a row that is not ``met``) is the model's own quote. Each item is made ONE line of the master as it is written
(or one answer / story the prompt listed), or it goes:

* the item is a line, or a piece of exactly one line (a quote cut short, a partial named set, a label or a role title
  put in front of it): it becomes that whole line (``trimmed_to_line``);
* the item joins pieces of several lines with ``;`` or ``...``: each line is cited on its own, in the order joined
  (``split_join``);
* no line holds any piece of it (a paraphrase, a sentence about the resume): the item is removed, and the row cites
  nothing rather than a text no line says (``no_line``).

Code only removes text or replaces it by the candidate's own line: a row's status, class and sources are never touched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
import re

from .find_jobs.assess_contracts import AssessmentSuggestion
from .posting_keywords import alias_table, term_regex

_ID_COMMENT = re.compile(r"<!--\s*id:\s*(\S+?)\s*-->")
_ANY_COMMENT = re.compile(r"\s*<!--.*?-->")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_ROLE_ID = re.compile(r"\Ar-")

#: The longest evidence string the proposals validator takes (``proposals._MAX_TEXT``).
_MAX_EVIDENCE = 1_200

_STOP = frozenset(
    "a an the and or of in on to for with across at by as from is are be being been that this these those our your you we it its "
    "into over per via using use used their them they than then so such".split()
)

#: An order suggestion that asks for a line to lead the whole Experience section / the resume (only the first role can).
_WHOLE_SECTION = re.compile(r"\b(?:experience section|top of (?:the )?(?:resume|experience|page)|whole resume|page one|first page)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MasterLine:
    """One bullet of the master: its id, its text (no id comment) and the role or project header it is under."""

    id: str
    text: str
    role: str | None = None
    first_role: bool = False


def parse_master_lines(resume_text: str) -> dict[str, MasterLine]:
    """The bullets of ``resume_text`` that carry an id comment, by id. Role and project headers are not lines.

    ``role`` is the id of the ``###`` header above the bullet (``None`` for the summary and the skills);
    ``first_role`` is true for the first role header of the Experience section (the current role).
    """

    lines: dict[str, MasterLine] = {}
    role: str | None = None
    seen_role = False
    first: str | None = None
    for raw in resume_text.splitlines():
        stripped = raw.strip()
        found = _ID_COMMENT.search(stripped)
        if stripped.startswith("#"):
            if stripped.startswith("###"):
                role = found.group(1) if found else None
                if role is not None and _ROLE_ID.match(role) and not seen_role:
                    seen_role, first = True, role
            else:
                role = None
            continue
        if not found:
            continue
        text = _ANY_COMMENT.sub("", stripped).strip()
        if text.startswith(("- ", "* ")):
            text = text[2:].strip()
        if text:
            lines[found.group(1)] = MasterLine(found.group(1), text, role, role is not None and role == first)
    return lines


# --- words ----------------------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [word.strip(".") for word in _WORD.findall(text.lower().replace("-", " ").replace("/", " ")) if word.strip(".") and word.strip(".") not in _STOP]


def _same(left: str, right: str) -> bool:
    """Two words are one word when equal or when they share a stem: a common prefix of at least four letters, all but two of the shorter."""

    if left == right:
        return True
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    return common >= max(4, min(len(left), len(right)) - 2)


def phrase_in(phrase: str, *texts: str) -> bool:
    """Whether every content word of ``phrase`` is in one of ``texts`` (stemmed). A phrase with no content word is in any text."""

    pool = [word for text in texts for word in _words(text)]
    return all(any(_same(word, other) for other in pool) for word in _words(phrase))


# --- C2: the evidence is the line -----------------------------------------------------------------------


def verbatim_evidence(
    sources: Sequence[str], lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str],
) -> list[str] | None:
    """The cited master line(s) of a row by id (then the cited answers, then the cited stories), or ``None`` when no source is known.

    ``answers`` / ``stories``: ``question_id`` -> the answer's text / the story's one-line summary.
    """

    out: list[str] = []
    for source in sources:
        if source in lines:
            text = lines[source].text[:_MAX_EVIDENCE]
        elif source.startswith("A "):
            question_id = source[2:].strip()
            if question_id in stories:
                text = f"Story bank {question_id}: {stories[question_id]}"[:_MAX_EVIDENCE]
            elif question_id in answers:
                text = f"Your answer: {answers[question_id]}"[:_MAX_EVIDENCE]
            else:
                continue
        else:
            continue
        if text not in out:
            out.append(text)
    return out or None


def with_verbatim_evidence(
    matrix: Iterable[dict[str, object]], lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str],
) -> None:
    """Each ``met`` row of ``matrix`` that cites sources shows the cited lines as its evidence. Other rows are untouched.

    IN PLACE: the rows are the boundary's own fresh dicts, and the places that already hold them (the question and
    suggestion row lookups) must keep seeing the same objects.
    """

    for row in matrix:
        sources = row.get("sources")
        if row.get("status") == "met" and isinstance(sources, list) and sources:
            taken = verbatim_evidence([str(source) for source in sources], lines, answers, stories)
            if taken is not None:
                row["resume_evidence"] = taken


# --- C3: the suggestion check ---------------------------------------------------------------------------


def _lookup(rows: Iterable[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    return {str(row["id"]): row for row in rows if row.get("id")}


def _row_sources(row: Mapping[str, object]) -> list[str]:
    sources = row.get("sources")
    return [str(source) for source in sources] if isinstance(sources, list) else []


def _row_text(row: Mapping[str, object]) -> str:
    alternatives = row.get("alternatives")
    extra = " ".join(str(item) for item in alternatives) if isinstance(alternatives, list) else ""
    return f"{row.get('requirement') or ''} {row.get('class_basis') or ''} {extra}"


def check_suggestion(
    item: AssessmentSuggestion, rows: Mapping[str, Mapping[str, object]], lines: Mapping[str, MasterLine], answers: Mapping[str, str],
    known: str = "",
) -> tuple[AssessmentSuggestion | None, str]:
    """``(kept, why)``: the suggestion as it may stay (possibly turned into a ``gap``), or ``None`` and the reason it goes."""

    row = rows.get(item.requirement) if item.requirement else None
    sources = _row_sources(row) if row is not None else []
    line = lines.get(item.line) if item.line else None
    cited_answers = " ".join(answers.values())  # the user's own words: any answer the prompt listed may back a phrase

    # Q1: a gap asks the user about the thing, so another spelling of it in the master ("ReactJS") already answers it;
    # a reword / keyword / order may be about the posting's own spelling, so only that exact spelling counts there.
    untrue = false_claim(item.why, lines, phrase=item.posting_phrase, variants=item.kind in ("gap", "master_line"))
    if untrue is not None:
        return None, untrue
    # Q4: a city that no text the prompt showed names is not the posting's and not the candidate's.
    if known and names_unknown_place(item.why, known):
        return None, PLACE_NOT_IN_POSTING
    if item.kind in ("reword", "keyword", "order"):
        if item.line is None or line is None:
            return None, "no_master_line"
        if row is not None and item.line not in sources:
            return None, "line_not_a_source_of_row"
    if item.kind in ("reword", "keyword") and item.posting_phrase and not phrase_in(item.posting_phrase, line.text, cited_answers):  # type: ignore[union-attr]
        return None, "phrase_not_in_line"
    if item.kind == "order" and line is not None:
        if line.role is None:
            return None, "order_line_in_no_role"
        if not line.first_role and _WHOLE_SECTION.search(item.why):
            return None, "order_across_roles"
    if item.kind == "master_line":
        if any(source.startswith("A ") for source in sources):
            return item, "ok"
        if row is None or row.get("status") == "met" or item.requirement is None:
            return None, "master_line_without_answer"
        return replace(item, kind="gap", line=None), "master_line_to_gap"
    if item.kind == "gap" and item.posting_phrase and row is not None and not phrase_in(item.posting_phrase, _row_text(row)):
        return None, "gap_phrase_not_of_row"
    return item, "ok"


def check_suggestions(
    suggestions: Sequence[AssessmentSuggestion], matrix: Iterable[Mapping[str, object]], lines: Mapping[str, MasterLine],
    answers: Mapping[str, str], known: str = "",
) -> tuple[list[AssessmentSuggestion], list[tuple[AssessmentSuggestion, str]]]:
    """``(kept, dropped)``: the suggestions that pass :func:`check_suggestion`, and each other one with its reason."""

    rows = _lookup(matrix)
    kept: list[AssessmentSuggestion] = []
    dropped: list[tuple[AssessmentSuggestion, str]] = []
    for item in suggestions:
        result, why = check_suggestion(item, rows, lines, answers, known)
        if result is None:
            dropped.append((item, why))
        else:
            kept.append(result)
    return kept, dropped


# --- Q1: no "the resume is silent on X" when a line states X ----------------------------------------------

_SEEN = r"(?:mention|show|name|list|state|include|reference|cite|cover|contain|demonstrate|indicate)"
#: The claim, with what it is about AFTER it ("does not mention Kafka") ... A bare "do not mention Kafka" is advice, not a claim.
_SILENT_THEN_TERM = re.compile(
    r"\b(?:silent\s+(?:on|about|regarding)"
    r"|(?:does\s+not|doesn['’]t|did\s+not|didn['’]t)\s+(?:\w+ly\s+)?" + _SEEN
    + r"|(?:lines|bullets|roles|positions|projects|they|you)\s+(?:do\s+not|don['’]t|never)\s+(?:\w+ly\s+)?" + _SEEN
    + r"|(?:no|none\s+of\s+(?:the|your|these|those))\s+(?:lines?|bullets?|roles?|positions?|projects?)\s+(?:\w+ly\s+)?" + _SEEN + r"s?"
    + r"|never\s+" + _SEEN + r"s"
    + r"|(?:no|without(?:\s+any)?)\s+(?:\w+\s+)?(?:mention|evidence|sign|reference|trace)\s+of"
    + r"|nothing\s+(?:on|about)"
    + r"|(?:lacks|lacking|omits|(?:is|are)\s+missing)(?:\s+(?:any\s+)?(?:mention|evidence)\s+of)?"
    + r")\s+",
    re.IGNORECASE,
)
_UNSEEN = r"(?:mentioned|shown|named|listed|stated|present|included|covered|visible|evidenced|demonstrated|cited|referenced|there)"
#: ... or BEFORE it ("Kafka is not mentioned").
_TERM_THEN_SILENT = re.compile(
    r"\s+(?:(?:is|are|was|were)\s+(?:not|never)\s+(?:\w+ly\s+)?" + _UNSEEN
    + r"|(?:isn['’]t|aren['’]t|wasn['’]t|weren['’]t)\s+(?:\w+ly\s+)?" + _UNSEEN
    + r"|(?:is|are)\s+(?:missing|absent)"
    + r"|(?:does\s+not|doesn['’]t|do\s+not|don['’]t|never)\s+appears?)\b",
    re.IGNORECASE,
)
#: Where the clause a claim is in ends: a full stop (not the one inside ``Node.js`` or ``99.9``), other punctuation, a conjunction.
_CLAUSE_END = re.compile(
    r"[;:!?]|\.(?=\s|\Z)|,?\s+(?:but|so|though|although|while|if|unless|because|since|yet|however|whereas|which|even)\b", re.IGNORECASE,
)
#: The claim is about the resume AS A WHOLE: the rest of its clause names the resume and nothing else. "The summary
#: does not mention Kafka" and "Kafka is not mentioned in the first role" are about one place, and may well be true.
_WHOLE_RESUME = frozenset(
    "the your this their a an resume master cv candidate candidate's experience background history work it there is are was has have "
    "shows show gives contains currently also still simply just and in on from anywhere at all explicitly by name itself yet".split()
)
#: Q4. The claim is about the ROLES: beside the words above its clause names roles, bullets, positions or projects in
#: general ("in a role", "in any of your roles", "in your experience bullets"). "In the first role" names one place and
#: is not judged.
_ROLE_WORDS = frozenset("role roles bullet bullets position positions job jobs project projects".split())
_ROLE_GLUE = frozenset("any of none no one under within across inside among section sections line lines entry entries listed".split())
#: ... written after the term: "does not show Kafka in any role".
_ROLE_TAIL = re.compile(r"\s+(?:in|under|within|across|inside|among)\s+((?:[a-z'’]+\s+){0,4}?(?:roles?|bullets?|positions?|jobs?|projects?|section))\s*\Z", re.IGNORECASE)
_SKILLS = r"(?:(?:the|your|a|an)\s+)?(?:skills?|keywords?)(?:\s+(?:line|section|list|block|entry|item|keywords?|tags?))?"
_LISTED = r"(?:listed|mentioned|named|shown|present|stated|included|found|seen|visible)"
_ONLY = r"(?:only|solely|merely)"
#: Q4, "X is only in Skills": the term BEFORE the claim. "X is the only skill ..." is another sentence.
_TERM_THEN_ONLY_SKILLS = re.compile(
    r"(?:(?<!\bthe)\s+(?:(?:is|are|was|were|appears?|shows?\s+up|sits?|lives?|occurs?)\s+)?(?:"
    + _LISTED + r"\s+)?" + _ONLY + r"\s+(?:(?:" + _LISTED + r"|appears?)\s+)*(?:(?:in|on|under|as|within|among)\s+" + _SKILLS
    + r"|an?\s+(?:skills?\s+(?:keyword|entry|item|tag)|keyword|skill)\b)"
    + r"|\s+(?:(?:is|are|was|were|appears?|shows?\s+up|sits?|lives?|occurs?)\s+)?(?:" + _LISTED + r"\s+)?(?:in|on|under|within|among)\s+" + _SKILLS + r"\s+" + _ONLY
    + r"|\s+(?:is|are)\s+(?:a\s+)?skills?[\s-]only)\b",
    re.IGNORECASE,
)
#: ... "X is only listed" with nothing after it but what it is not ("X is only listed, not shown in use").
_TERM_THEN_ONLY_LISTED = re.compile(r"\s+(?:is|are)\s+(?:" + _ONLY + r"\s+(?:listed|named)|(?:listed|named)\s+" + _ONLY + r")\b", re.IGNORECASE)
_ONLY_LISTED_TAIL = re.compile(r"\A\s*(?:,?\s*(?:and\s+)?(?:not|never|rather\s+than|without|with\s+no)\b.*)?\Z", re.IGNORECASE | re.DOTALL)
#: ... and the term AFTER it: "Only the Skills line mentions X".
_ONLY_SKILLS_THEN_TERM = re.compile(
    r"\b" + _ONLY + r"\s+(?:in\s+)?" + _SKILLS + r"\s+(?:\w+ly\s+)?(?:" + _SEEN + r"s?|has|have|carr(?:y|ies))\s+", re.IGNORECASE,
)
#: "Your resume lists X only in Skills": who lists it is no part of what is listed.
_SUBJECT_LISTS = re.compile(
    r"\A\s*(?:(?:(?:the|your|this)\s+)?(?:resume|master|cv)|you)\s+(?:currently\s+)?(?:lists?|mentions?|names?|shows?|has|have|includes?|states?|carr(?:y|ies))\s+",
    re.IGNORECASE,
)
ALL, ROLES = "all", "roles"
SILENT_BUT_IN_MASTER, ONLY_SKILLS_BUT_IN_ROLE, PLACE_NOT_IN_POSTING = "silent_but_in_master", "only_skills_but_in_role", "place_not_in_posting"
_SPAN_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.'’]*[A-Za-z0-9+#%]|[A-Za-z0-9]")
_NUMBER = re.compile(r"\A\d+(?:[.,]\d+)*\+?%?\Z")
_UNITS = {"year": r"\+?\s*(?:years?|yrs?)", "yr": r"\+?\s*(?:years?|yrs?)", "month": r"\+?\s*months?"}
#: Words of a claim that name nothing: "does not mention any hands-on Kafka experience at scale" is a claim about Kafka.
#: The scope and strength words are the reasonable reader's (docs/v0.1.11/MET-STANDARD.md): they never make a gap.
_FILLER = _STOP | frozenset(
    "experience experiences work working usage used using skill skills knowledge exposure any explicit explicitly name named names "
    "hands hand production tool tools tooling framework frameworks language languages technology technologies tech stack like e.g eg "
    "specific specifically all anywhere directly itself word words keyword keywords term terms posting posting's job requirement "
    "required requires resume master cv candidate candidate's either both nor neither etc particular also "
    "currently professional commercial prior previous direct year years yr yrs month months plus least minimum more "
    "scale large complex deep strong high volume significant proven extensive solid advanced expert expertise proficiency proficient level "
    # Q4: "doing it" words name nothing either ("does not mention building with React" is a claim about React).
    "build building built develop developing developed development program programming code coding write writing written".split()
)
_POINTERS = frozenset("this it them these those that requirement one".split())
#: The longest tail by which two spellings of one table entry are still one word ("React" / "ReactJS", "Go" / "Golang").
_VARIANT_TAIL = 4


@lru_cache(maxsize=1)
def _table() -> tuple[tuple[str, ...], ...]:
    """The spellings of each entry of the shipped alias table (``posting_keywords``), read once per process."""

    return tuple((term, *aliases) for term, aliases in alias_table().items())


@lru_cache(maxsize=1)
def _table_spellings() -> tuple[str, ...]:
    return tuple(sorted({spelling for entry in _table() for spelling in entry if spelling.strip()}, key=lambda spelling: (-len(spelling), spelling)))


@lru_cache(maxsize=2048)
def _names(spelling: str) -> re.Pattern[str]:
    return term_regex(spelling)


def _squash(spelling: str) -> str:
    return re.sub(r"[\s.\-]", "", spelling.casefold())


@lru_cache(maxsize=2048)
def _spellings(term: str) -> tuple[str, ...]:
    """``term`` and the other spellings of the SAME WORD in its table entry: one is the other plus a short tail.

    ``React`` / ``ReactJS`` / ``React.js``, ``Go`` / ``Golang``, ``PostgreSQL`` / ``Postgres``. The table's looser
    entries (``Kubernetes`` / ``EKS``, ``CI/CD`` / ``Jenkins``, ``Ruby`` / ``Ruby on Rails``) are another thing, not
    another spelling: a line that names one does not state the other.
    """

    mine = _squash(term)
    for entry in _table():
        if any(_squash(spelling) == mine for spelling in entry):
            same = [
                spelling for spelling in entry
                if (_squash(spelling).startswith(mine) or mine.startswith(_squash(spelling))) and abs(len(_squash(spelling)) - len(mine)) <= _VARIANT_TAIL
            ]
            return (term, *(spelling for spelling in same if spelling != term))
    return (term,)


def _whole_resume(text: str) -> bool:
    return all(token.casefold() in _WHOLE_RESUME for token in _SPAN_TOKEN.findall(text))


def _scope(text: str) -> str | None:
    """What the words around a claim say it is about: the whole resume (``ALL``), its roles (``ROLES``), or one place (``None``)."""

    tokens = [token.casefold() for token in _SPAN_TOKEN.findall(text)]
    if all(token in _WHOLE_RESUME for token in tokens):
        return ALL
    about_roles = any(token in _ROLE_WORDS for token in tokens) or ("section" in tokens and "experience" in tokens)
    if about_roles and all(token in _WHOLE_RESUME or token in _ROLE_WORDS or token in _ROLE_GLUE for token in tokens):
        return ROLES
    return None


def _rest_of_clause(text: str) -> str:
    end = _CLAUSE_END.search(text)
    return text[: end.start()] if end else text


def _silent_spans(text: str) -> list[tuple[str, str]]:
    """``(what it names, ALL | ROLES)`` for each claim of ``text`` that the resume, or its roles, do not show something.

    ``ALL``: "silent on / does not mention / is not shown" about the whole resume. ``ROLES`` (Q4): the same about
    the roles in general ("is not shown in a role", "no bullet mentions"), and "is only in Skills" / "is only listed".
    """

    spans: list[tuple[str, str]] = []
    for found in _SILENT_THEN_TERM.finditer(text):
        subject = " ".join(token for token in _SPAN_TOKEN.findall(found.group(0)) if token.casefold() in _ROLE_WORDS)
        scope = _scope(f"{_clause_before(text[: found.start()])} {subject}")
        span = _rest_of_clause(text[found.end():])
        tail = _ROLE_TAIL.search(span)
        if tail is not None and _scope(tail.group(1)) == ROLES:
            span, scope = span[: tail.start()], ROLES if scope is not None else None
        if scope is not None:
            spans.append((span, scope))
    for found in _TERM_THEN_SILENT.finditer(text):
        scope = _scope(_rest_of_clause(text[found.end():]))
        if scope is not None:
            spans.append((_clause_before(text[: found.start()]), scope))
    for found in _TERM_THEN_ONLY_SKILLS.finditer(text):
        spans.append((_SUBJECT_LISTS.sub("", _clause_before(text[: found.start()])), ROLES))
    for found in _TERM_THEN_ONLY_LISTED.finditer(text):
        if _ONLY_LISTED_TAIL.match(_rest_of_clause(text[found.end():])):
            spans.append((_SUBJECT_LISTS.sub("", _clause_before(text[: found.start()])), ROLES))
    for found in _ONLY_SKILLS_THEN_TERM.finditer(text):
        spans.append((_rest_of_clause(text[found.end():]), ROLES))
    return [(span, scope) for span, scope in spans if span.strip()]


def _clause_before(text: str) -> str:
    starts = [end.end() for end in _CLAUSE_END.finditer(text)]
    return text[starts[-1]:] if starts else text


def _claimed(span: str) -> tuple[list[str], list[tuple[str, str]]] | None:
    """``(tools and skills, numbers)`` a claim's span names, or ``None`` when it is also about something that is not a term.

    A tool or skill: a spelling of the alias table, or a word written as a name (a capital, a digit, ``+`` or ``#`` in
    it). Names written one after the other are ONE term ("Kafka Streams", "React Native"): a line that says Kafka
    does not state Kafka Streams. A number: ``(the digits, its unit pattern)``. Any other word that is not filler
    means the claim is about more than named terms, and code does not judge it.
    """

    names: list[tuple[int, int, str]] = []  # (start, end, the name) in the span
    rest = span
    for spelling in _table_spellings():  # the longest first: "React.js" before "js"
        for found in list(_names(spelling).finditer(rest)):
            # A table word in plain lower case ("the rest of the stack", "excel at") is read as a word, not as the tool.
            if " " in spelling or any(char.isupper() or char.isdigit() or char in "+#./" for char in found.group(0)):
                names.append((found.start(), found.end(), spelling))
                rest = rest[: found.start()] + " " * (found.end() - found.start()) + rest[found.end():]
    found_tokens = [(found.start(), found.group(0).rstrip(".'’")) for found in _SPAN_TOKEN.finditer(rest.replace("/", " ").replace("-", " "))]
    numbers: list[tuple[str, str]] = []
    for place, (start, token) in enumerate(found_tokens):
        low = token.casefold()
        if _NUMBER.match(token):
            unit = found_tokens[place + 1][1].casefold().rstrip("s") if place + 1 < len(found_tokens) else ""
            numbers.append((token.rstrip("+%"), r"\+?\s*%" if token.endswith("%") else _UNITS.get(unit, "")))
        elif low in _FILLER or low in _POINTERS or not low:
            continue
        elif any(char.isupper() or char.isdigit() or char in "+#" for char in token):
            names.append((start, start + len(token), token))
        else:
            return None
    tools: list[str] = []
    last_end: int | None = None
    for start, end, name in sorted(names):
        if last_end is not None and span[last_end:start].strip() == "" and span[last_end:start]:
            tools[-1] = f"{tools[-1]} {name}"
        else:
            tools.append(name)
        last_end = end
    return list(dict.fromkeys(tools)), numbers


def _states(texts: Sequence[str], tools: Sequence[str], numbers: Sequence[tuple[str, str]], variants: bool) -> bool:
    """Whether the master lines ``texts`` make a "does not show" claim about ``tools`` and ``numbers`` false.

    No number: ONE named tool that a line states is enough (Q4: "does not mention Kafka or Flink" is false about
    Kafka). With a number: every tool (each in some line) and every number (all in one line, with a tool when one is
    named), so "does not state 6 years of React" stays when the master says 4.
    """

    def has(text: str, tool: str) -> bool:
        return any(_names(spelling).search(text) for spelling in (_spellings(tool) if variants else (tool,)))

    if not numbers:
        return any(has(text, tool) for tool in tools for text in texts)
    if not all(any(has(text, tool) for text in texts) for tool in tools):
        return False
    patterns = [re.compile(r"(?<![\d.,])" + re.escape(digits) + (unit or r"(?![\d]|[.,]\d)"), re.IGNORECASE) for digits, unit in numbers]
    return any(all(pattern.search(text) for pattern in patterns) and (not tools or any(has(text, tool) for tool in tools)) for text in texts)


def _in_a_role(line: MasterLine) -> bool:
    """A bullet of a role or a project (not the summary, the skills, an Education entry)."""

    return line.role is not None and not line.role.startswith("e-")


def false_claim(why: str, lines: Mapping[str, MasterLine], *, phrase: str | None = None, variants: bool = True) -> str | None:
    """The reason ``why`` makes a claim the master's own lines contradict, or ``None``.

    ``silent_but_in_master``: it says the resume is silent on (does not mention, does not show) a term a line states.
    ``only_skills_but_in_role``: it says a term is only in Skills, is only listed, or is not shown in a role, and a
    bullet of a role or a project states it. Only a claim that names tools, skills or numbers and nothing else is
    judged (word boundaries, case aside; ``variants``: another spelling of the same word in the shipped alias table
    counts). A claim that only points ("the resume is silent on this") is about ``phrase``, the suggestion's posting phrase.
    """

    if not lines:
        return None
    texts = {ALL: [line.text for line in lines.values()], ROLES: [line.text for line in lines.values() if _in_a_role(line)]}
    for span, scope in _silent_spans(why):
        claimed = _claimed(span)
        if claimed is not None and not claimed[0] and not claimed[1] and phrase:
            claimed = _claimed(phrase)
        if claimed is None or not (claimed[0] or claimed[1]):
            continue
        if _states(texts[scope], claimed[0], claimed[1], variants):
            return SILENT_BUT_IN_MASTER if scope == ALL else ONLY_SKILLS_BUT_IN_ROLE
    return None


def silent_but_stated(why: str, lines: Mapping[str, MasterLine], *, phrase: str | None = None, variants: bool = True) -> bool:
    """Whether :func:`false_claim` finds a claim of ``why`` that lines of the master contradict."""

    return false_claim(why, lines, phrase=phrase, variants=variants) is not None


# --- Q4: a city nobody named ----------------------------------------------------------------------------------
#
# WHAT IS CHECKED. A capitalised name (one to four capitalised words) in one of five location wordings:
#   1. after "based / located / relocate / commute / onsite / in-office / in-person / hybrid / reside / live / office /
#      headquartered" + "in / to / at / near / around / from / within / outside / of"  ("based in Denver");
#   2. after "work(ing) from / out of"                                                  ("work from Lisbon");
#   3. before "office / area / metro / headquarters / HQ / campus" in lower case         ("the Denver office");
#   4. before "-based" / "-area" inside a sentence                                      ("are you Denver-based");
#   5. after "in / to / from / near / at" and before ", XX"                             ("in Denver, CO").
# Each word of the name is looked up, whole word and case aside, in the text the prompt showed (role, company,
# location header, posting text, the master, the setup's location, the answers and stories). One word found nowhere:
# the text names a place nobody gave. NEVER JUDGED: a country, a continent or region, a US state or Canadian province,
# a word of up to four capitals (NYC, SF, US), a part of many names (New, San, City, Bay, Area ...), a month, a day, a
# time zone, a work-mode word, a spelling of the alias table (a tool), a word under three letters.
# FALSE-POSITIVE RISK: a city the posting names another way (posting "NYC" or "Manhattan", question "Brooklyn"; a
# suburb or a metro name), a country or region missing from the list below, and a capitalised word that is no place
# in wording 1 or 3 ("based in Finance") when the posting never uses that word.

_CAP = r"[A-Z][A-Za-z'’]*(?:\s+[A-Z][A-Za-z'’]*){0,3}"
_THE = r"(?i:(?:the|our|their|its|a|your)\s+)?(?i:greater\s+)?"
_PLACE_WORDINGS = (
    re.compile(
        r"\b(?i:based|located|relocat(?:e|es|ed|ing|ion)|commut(?:e|es|ing)|onsite|on-site|in-office|in-person|hybrid|reside[sd]?|residing"
        r"|live[sd]?|living|offices?|headquartered)\s+(?i:(?:\w+ly|out)\s+)?(?i:in|to|at|near|around|from|within|outside|of)\s+" + _THE + "(" + _CAP + ")"
    ),
    re.compile(r"\b(?i:work(?:s|ing)?)\s+(?i:\w+ly\s+)?(?i:from|out\s+of)\s+" + _THE + "(" + _CAP + ")"),
    re.compile(r"(?<![A-Za-z'’])(" + _CAP + r")\s+(?:offices?|area|metro|metropolitan|headquarters|HQ|campus)\b"),
    re.compile(r"(?<=[a-z,]\s)(" + _CAP + r")-(?i:based|area)\b"),
    re.compile(r"\b(?i:in|to|from|near|at)\s+(" + _CAP + r"),\s+[A-Z]{2}\b"),
)
_PLACE_WORD = re.compile(r"[A-Za-z]+")
_NOT_A_CITY = frozenset(
    # parts of many names, and words that open a sentence or a question
    "new san santa los las saint st fort port mount lake north south east west northern southern eastern western central upper lower "
    "city town area bay greater metro downtown county state province region district valley island islands beach springs falls park "
    "are is was were do does did can could would will should have has had what which where when who how why if please you your our their its the a an this that "
    # work mode, calendar, time zones
    "remote remotely hybrid onsite office offices home hq headquarters campus anywhere worldwide global globally "
    "january february march april may june july august september october november december monday tuesday wednesday thursday friday saturday sunday "
    "pacific eastern mountain atlantic time zone standard daylight "
    # continents and regions
    "america americas europe asia africa oceania australasia emea apac latam nordics scandinavia benelux dach caribbean balkans baltics "
    "european asian african american north american latin middle "
    # countries (and the words of their names)
    "united states usa kingdom britain england scotland wales ireland canada mexico brazil argentina chile colombia peru uruguay costa rica panama "
    "germany france spain portugal italy netherlands holland belgium luxembourg switzerland austria denmark sweden norway finland iceland "
    "poland czechia czech republic slovakia hungary romania bulgaria greece turkey ukraine estonia latvia lithuania croatia serbia slovenia cyprus malta "
    "israel egypt nigeria kenya ghana morocco uae emirates saudi arabia qatar india pakistan bangladesh sri lanka nepal china japan korea taiwan "
    "singapore malaysia indonesia thailand vietnam philippines australia zealand hong kong "
    # US states and Canadian provinces (the words of their names)
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky "
    "louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada hampshire jersey york carolina dakota "
    "ohio oklahoma oregon pennsylvania rhode tennessee texas utah vermont virginia washington wisconsin wyoming columbia "
    "ontario quebec british alberta manitoba saskatchewan nova scotia brunswick newfoundland labrador yukon nunavut".split()
)


@lru_cache(maxsize=1)
def _tool_words() -> frozenset[str]:
    return frozenset(word.casefold() for spelling in _table_spellings() for word in _PLACE_WORD.findall(spelling))


def _judged_words(name: str) -> list[str]:
    """The words of a capitalised ``name`` that can only be a city's: no part of many names, no country, state, region, short capitals or tool."""

    out: list[str] = []
    for word in _PLACE_WORD.findall(re.sub(r"['’]s?\b", " ", name)):
        low = word.casefold()
        if len(word) < 3 or (word.isupper() and len(word) <= 4) or low in _NOT_A_CITY or low in _STOP or low in _tool_words():
            continue
        out.append(word)
    return out


def names_unknown_place(text: str, known: str) -> bool:
    """Whether ``text`` names, in a location wording, a city no word of ``known`` names (see the block comment above for exactly what is read)."""

    if not known.strip():
        return False
    for wording in _PLACE_WORDINGS:
        for found in wording.finditer(text):
            for word in _judged_words(found.group(1)):
                if not re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", known, re.IGNORECASE):
                    return True
    return False


def without_unknown_places(items: Sequence[object], key: str, known: str) -> tuple[list[object], int]:
    """``(kept, dropped count)``: ``items`` (plain strings, or objects whose text is under ``key``) without those :func:`names_unknown_place` refuses."""

    def text_of(item: object) -> str:
        if isinstance(item, str):
            return item
        value = item.get(key) if isinstance(item, Mapping) else None
        return value if isinstance(value, str) else ""

    kept = [item for item in items if not names_unknown_place(text_of(item), known)]
    return kept, len(items) - len(kept)


def truthful_notes(notes: Sequence[object], lines: Mapping[str, MasterLine], known: str = "") -> tuple[list[object], int]:
    """``(kept, dropped count)``: the plain-string suggestions without those :func:`false_claim` or :func:`names_unknown_place` refuses."""

    kept = [note for note in notes if not (isinstance(note, str) and (silent_but_stated(note, lines) or names_unknown_place(note, known)))]
    return kept, len(notes) - len(kept)


# --- Q3: a citation is one line of the master ----------------------------------------------------------------

_JOIN = re.compile(r";|\.\.\.|…")
_LABEL = re.compile(r"\A[^:]{1,80}:\s+(?=\S)")
#: The most lines one joined quote is split into (a row cites at most three sources, ``MAX_ROW_SOURCES``).
_MAX_SPLIT = 3
#: The shortest piece that can stand for a line: a single word of three letters ("SQL") names its one line.
_MIN_PIECE = 3


def _flat(text: str) -> str:
    """``text`` as it compares: no emphasis marks or quotes, one space, case folded, no closing stop."""

    return " ".join(re.sub(r"[*_`\"'“”‘’]", "", text).split()).casefold().strip(" .…")


class _Citable:
    """What one answer's evidence may cite: each master line, and each answer or story the prompt listed, as it is shown."""

    def __init__(self, lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str]) -> None:
        shown = [line.text[:_MAX_EVIDENCE] for line in lines.values()]
        shown += [f"Story bank {key}: {text}"[:_MAX_EVIDENCE] for key, text in stories.items()]
        shown += [f"Your answer: {text}"[:_MAX_EVIDENCE] for key, text in answers.items() if key not in stories]
        self.flat: dict[str, str] = {}
        for text in shown:
            self.flat.setdefault(_flat(text), text)
        self.shown = frozenset(shown)

    def one(self, piece: str) -> str | None:
        """The ONE citable text that is ``piece`` or holds it whole (word boundaries); ``None`` when none or several do."""

        if piece in self.flat:
            return self.flat[piece]
        if len(piece) < _MIN_PIECE:
            return None
        inside = re.compile(r"(?<![a-z0-9])" + re.escape(piece) + r"(?![a-z0-9])")
        held = [shown for flat, shown in self.flat.items() if piece in flat and inside.search(flat)]
        return held[0] if len(held) == 1 else None

    def of(self, piece: str) -> str | None:
        """:meth:`one`, also for a piece with a label or a role title in front of it ("Skills: Go, Node")."""

        piece = _flat(piece)
        found = self.one(piece) if piece else None
        while found is None and piece and _LABEL.match(piece):
            piece = _LABEL.sub("", piece, count=1).strip(" .…")
            found = self.one(piece) if piece else None
        return found


def single_line_cites(item: str, citable: _Citable) -> tuple[list[str], str]:
    """``(the citations, what was done)`` for one evidence item: ``verbatim`` | ``trimmed_to_line`` | ``split_join`` | ``no_line``."""

    if item in citable.shown:
        return [item], "verbatim"
    whole = citable.of(item)
    if whole is not None:
        return [whole], "trimmed_to_line"
    found = [citable.of(piece) for piece in _JOIN.split(item)]
    cites = list(dict.fromkeys(text for text in found if text is not None))[:_MAX_SPLIT]
    if not cites:
        return [], "no_line"
    return cites, "split_join" if len(cites) > 1 else "trimmed_to_line"


def clean_citations(
    matrix: Iterable[dict[str, object]], lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str],
) -> list[str]:
    """Every evidence item of every row is ONE citable text as it is written, or is gone. Returns what was done to each item that changed.

    IN PLACE, like :func:`with_verbatim_evidence` (run after it: what it wrote is verbatim already). An ``elig-`` row's
    evidence is the setup's fixed wording, written by code, and is not a citation: it is left alone. Only
    ``resume_evidence`` is written: never a status, a class or the sources.
    """

    citable = _Citable(lines, answers, stories)
    done: list[str] = []
    for row in matrix:
        evidence = row.get("resume_evidence")
        if not isinstance(evidence, list) or str(row.get("id") or "").startswith("elig-"):
            continue
        out: list[str] = []
        for item in evidence:
            cites, what = single_line_cites(str(item), citable)
            if what != "verbatim":
                done.append(what)
            out.extend(text for text in cites if text not in out)
        row["resume_evidence"] = out
    return done


__all__ = [
    "MasterLine",
    "check_suggestion",
    "check_suggestions",
    "clean_citations",
    "false_claim",
    "names_unknown_place",
    "parse_master_lines",
    "phrase_in",
    "silent_but_stated",
    "single_line_cites",
    "truthful_notes",
    "verbatim_evidence",
    "with_verbatim_evidence",
    "without_unknown_places",
]
