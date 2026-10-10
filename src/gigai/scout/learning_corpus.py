"""0.1.11.10 Part B, packet G1: the role corpus and what its postings ask for (pathway steps 2 to 5).

A learning pathway is a course for one role. Before anything is written, GigAI reads the postings it already
stores for that role and counts what they ask for. Four steps, in order:

2. TITLES (one model call, one retry): the role as typed becomes 3 to 8 title phrases plus deny words
   (``data/instructions/learning_titles.md``; :func:`parse_titles` validates the answer).
3. CORPUS (no model, no network): ``search_index.candidates`` over every live posting with those phrases, the
   title rule on each candidate (the index only narrows), the deny words, copies of one job as one posting, the
   newest :data:`CORPUS_LIMIT`. A course is about a ROLE, not about one person's job search: the only filter is
   the country of the setup (US only for a US setup: every posting but one clearly outside the US, so an unclear
   location stays), and the work mode and area of the Jobs search are never applied (:func:`corpus_filters`). A
   posting is "in the window" when it was posted in the last :data:`WINDOW_DAYS` days. The postings READ are
   those of the window with stored text; when fewer than :data:`WIDEN_BELOW` of them have text, every date is
   read instead, and the corpus note says which (:attr:`Corpus.basis`). Text comes from the board cache
   (``postings.posting_texts``: no request), HTML and boilerplate paragraphs stripped.
4. VOCABULARY (one model call, one retry, at most one follow-up call): up to :data:`SENTENCE_BUDGET` characters of
   requirement-cue sentences, sampled evenly across the postings read and fenced as untrusted, become concepts
   ``{id, display, category, phrases, technical}`` (``data/instructions/learning_vocabulary.md``;
   :func:`parse_vocabulary`). The fewest concepts an answer may hold scales with the postings read
   (:func:`concepts_minimum`); an answer with more than :data:`CONCEPTS_MAX` is not refused: the concepts the most
   postings name are kept (:func:`trim_vocabulary`). The phrases are PLAIN words: code builds the word-boundary matchers
   (:func:`phrase_pattern`). :func:`screen_vocabulary` drops a phrase more than 60% of the postings hold (a
   boilerplate word), but only for a concept whose ``technical`` is false: a technical concept (skill, tool or
   domain) is kept whatever its share of postings, since the count is meant to show what the role needs. Either
   way, a concept no posting names is dropped.
5. COUNTING (no model): per concept, in how many postings of the window and of the whole set it is named, three
   example phrasings, and the threshold ``count_in90d >= 3 or count_all >= 4`` (:func:`count_concepts`).

Everything but the model answers is deterministic, and every function here is pure given those answers.
:func:`run_corpus` returns plain data and stores nothing itself; given a ``store`` (the course job's work folder) it
hands it each step's output as it is made (``titles``, ``corpus-summary``, ``vocabulary-raw``, ``vocabulary``,
``counts``: numbers, concepts and the model's raw answers, never a posting's text) and does not ask again for a
step whose stored output still fits. No resume is read anywhere in this
module (the resume is the reference for a course's known/some/new marks, which the lesson-writing step makes).

The model is reached the way ``interview_prep/categories.py`` reaches it (the user's configured target, through
``proposal_execution.resolve_model_adapter``, tools off) and every call is metered as kind ``learning``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import html
from importlib import resources
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Protocol

from .learning_store import LearningError, clean_role_text
from .untrusted_text import fence_untrusted_posting

SCHEMA_VERSION = "scout-learning-corpus:1"

#: Step 3: the window, and how many postings (newest first) the corpus keeps.
WINDOW_DAYS = 90
CORPUS_LIMIT = 300
#: Fewer postings of the window with stored text than this: every date is read instead (and the note says so).
WIDEN_BELOW = 25
#: Below this many postings read the counts are directional, and the corpus says so.
THIN_CORPUS = 10
BASIS_WINDOW, BASIS_ANY_DATE = "last_90_days", "any_date"

#: Step 2 bounds.
TITLES_MIN, TITLES_MAX = 3, 8
TITLE_WORDS_MAX = 5
TITLE_CHARS_MAX = 60
DENY_MAX = 12
DENY_CHARS_MAX = 24

#: Step 4 bounds. The fewest concepts scales with the postings read (:func:`concepts_minimum`): a small corpus
#: cannot name 60 things. An answer may hold up to :data:`CONCEPTS_ACCEPTED`; past :data:`CONCEPTS_MAX` the
#: concepts the most postings name are kept (the first real answer held 162, and was refused twice for it).
CONCEPTS_MIN, CONCEPTS_MAX = 60, 150
CONCEPTS_MIN_FLOOR = 15
CONCEPTS_ACCEPTED = 300
FOLLOW_UP_MAX = 30
PHRASES_MAX = 8
PHRASE_WORDS_MAX = 4
PHRASE_CHARS_MAX = 60
ID_CHARS_MAX = 48
DISPLAY_CHARS_MAX = 80
CATEGORIES = ("skill", "tool", "responsibility", "seniority", "domain")
SENTENCE_BUDGET = 40_000
SENTENCE_CHARS_MAX = 300
#: A phrase in more than this share of the postings is a boilerplate word.
BOILERPLATE_SHARE = 0.6
#: The follow-up call is made only for word groups at least this many uncovered sentences hold.
UNCOVERED_MIN_COUNT = 3

#: Step 5.
EXAMPLES_PER_CONCEPT = 3
EXAMPLE_CHARS_MAX = 160
KEEP_IN_WINDOW, KEEP_ANY_DATE = 3, 4

#: The model calls of one corpus: titles, vocabulary, the follow-up; one retry each for the first two.
EXPECTED_CALLS = 3
MAX_CALLS = 5

_VALIDATION_ERROR_MAX = 300
_TITLES_RESOURCE = ("scout", "data", "instructions", "learning_titles.md")
_VOCABULARY_RESOURCE = ("scout", "data", "instructions", "learning_vocabulary.md")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


class LearningCorpusError(LearningError):
    """A corpus step could not be done; ``code`` is the API/CLI error code."""


class CorpusStore(Protocol):
    """Where a run keeps its step outputs (the course job's work folder): ``read`` answers ``None`` for a step not stored."""

    def read(self, step: str) -> object | None: ...

    def write(self, step: str, data: object) -> object: ...


#: The step outputs a store is handed, in order. None holds a posting's text.
STEP_TITLES, STEP_SUMMARY, STEP_RAW, STEP_VOCABULARY, STEP_COUNTS = "titles", "corpus-summary", "vocabulary-raw", "vocabulary", "counts"


class RoleModel(Protocol):
    """What the steps need of a model: an answer to a prompt, and a way to say the last answer was unusable.

    ``timeout_seconds`` (G7c) overrides the adapter's own default for this one call; ``None`` keeps it.
    """

    def ask(self, prompt: str, *, timeout_seconds: float | None = None) -> str: ...

    def invalid_output(self) -> None: ...


#: G7c: the vocabulary call (step 4) read ~40k characters of posting sentences and took ~2 minutes in the real
#: replay, past the adapter's 120 s default; titles (step 2) stays at the adapter's own default (a short answer).
VOCABULARY_TIMEOUT_SECONDS = 300.0


# ---------------------------------------------------------------------------
# Prompt templates (paragraph templates, as data/instructions/assess.md)
# ---------------------------------------------------------------------------


def _template(resource: tuple[str, ...]) -> str:
    text = resources.files("gigai").joinpath(*resource).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def _render(template: str, values: Mapping[str, str | None]) -> str:
    """``template`` with its placeholders filled. A paragraph that holds a placeholder with no value is left out.

    Every value is put in once: a ``{{name}}`` inside a value (posting text) stays those characters.
    """

    kept = []
    for paragraph in template.split("\n\n"):
        names = _PLACEHOLDER.findall(paragraph)
        if any(values.get(name) is None for name in names):
            continue
        kept.append(_PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), paragraph))
    return "\n\n".join(kept)


def _error_text(error: BaseException | str | None) -> str | None:
    return None if error is None else " ".join(str(error).split())[:_VALIDATION_ERROR_MAX]


def _json_object(raw: str, code: str) -> Mapping[str, object]:
    text = raw.strip() if isinstance(raw, str) else ""
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        decoded = json.loads(text[start : end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, Mapping):
        raise LearningCorpusError(code, "the answer holds no JSON object")
    return decoded


def _held(what: str, noun: str, count: int, minimum: int, maximum: int) -> str:
    """Which bound a list missed and by how many, in plain words (the retry prompt and the job's error both say it)."""

    held = f"{what} holds {count} {noun}{'' if count == 1 else 's'}"
    if count < minimum:
        return f"{held}: {minimum - count} fewer than the {minimum} needed (the bounds are {minimum} to {maximum})"
    return f"{held}: {count - maximum} more than the {maximum} allowed (the bounds are {minimum} to {maximum})"


def _plain_words(text: str) -> list[str]:
    from .find_jobs.search_index import plain_words

    return plain_words(text)


# ---------------------------------------------------------------------------
# Step 2: titles
# ---------------------------------------------------------------------------

#: Words of a typed role that do not make it that role: a phrase must share another word with it.
_FILLER = frozenset({"of", "the", "and", "a", "an", "for", "in", "to", "at", "with"})


@dataclass(frozen=True)
class Titles:
    """What the role is searched with: the title phrases, and the words that mark another job."""

    phrases: tuple[str, ...]
    deny: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {"phrases": list(self.phrases), "deny": list(self.deny)}


def titles_prompt(role_text: str, validation_error: BaseException | str | None = None) -> str:
    return _render(_template(_TITLES_RESOURCE), {"role": role_text, "validation_error": _error_text(validation_error)})


def parse_titles(raw: str, role_text: str) -> Titles:
    """The title phrases and deny words of a model answer, or ``titles_invalid`` naming the first thing wrong.

    Refused: no JSON object, a key other than ``titles`` and ``deny``, fewer than 3 or more than 8 phrases, a
    phrase that is not 1 to 5 words (or over 60 characters, or twice), no phrase that holds a word of the role as
    typed, a deny entry that is not one word of letters. A deny word that is a word of a phrase or of the role
    would take the role's own postings out: it is left out, not refused.
    """

    decoded = _json_object(raw, "titles_invalid")

    def bad(message: str) -> LearningCorpusError:
        return LearningCorpusError("titles_invalid", message)

    if set(decoded) - {"titles", "deny"}:
        raise bad("the answer may hold only the keys titles and deny")
    titles = decoded.get("titles")
    if not isinstance(titles, list):
        raise bad(f"titles must be a list of {TITLES_MIN} to {TITLES_MAX} phrases")
    if not TITLES_MIN <= len(titles) <= TITLES_MAX:
        raise bad(_held("titles", "phrase", len(titles), TITLES_MIN, TITLES_MAX))
    phrases: list[str] = []
    for item in titles:
        words = _plain_words(item) if isinstance(item, str) else []
        if not isinstance(item, str) or not 1 <= len(words) <= TITLE_WORDS_MAX or len(item.strip()) > TITLE_CHARS_MAX:
            raise bad(f"every title phrase is 1 to {TITLE_WORDS_MAX} words and at most {TITLE_CHARS_MAX} characters")
        phrase = " ".join(item.lower().split())
        if phrase in phrases:
            raise bad("no two title phrases may be the same")
        phrases.append(phrase)
    typed = set(_plain_words(role_text)) - _FILLER
    used = {word for phrase in phrases for word in _plain_words(phrase)}
    if not typed & used:
        raise bad("at least one title phrase must hold a word of the role as typed")
    deny_raw = decoded.get("deny", [])
    if not isinstance(deny_raw, list) or len(deny_raw) > DENY_MAX:
        raise bad(f"deny must be a list of at most {DENY_MAX} words")
    deny: list[str] = []
    for item in deny_raw:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z]{2,%d}" % DENY_CHARS_MAX, item.strip()):
            raise bad(f"every deny entry is one word of letters, at most {DENY_CHARS_MAX} characters")
        word = item.strip().lower()
        if word not in deny and word not in used and word not in typed:
            deny.append(word)
    return Titles(tuple(phrases), tuple(deny))


def propose_titles(model: RoleModel, role_text: str) -> Titles:
    """Step 2: one call, and one more when the first answer does not validate. ``titles_invalid`` after the second."""

    error: LearningCorpusError | None = None
    for _attempt in range(2):
        raw = model.ask(titles_prompt(role_text, error))
        try:
            return parse_titles(raw, role_text)
        except LearningCorpusError as exc:
            model.invalid_output()
            error = exc
    assert error is not None
    raise LearningCorpusError("titles_invalid", f"the model's title phrases for this role were not usable, twice (the last answer: {error})")


# ---------------------------------------------------------------------------
# Text: HTML, boilerplate, sentences (ported from the role-packet research scripts)
# ---------------------------------------------------------------------------


def strip_html(text: str | None) -> str:
    """Posting HTML as plain text: scripts and styles out, a line per block and per list item, entities decoded."""

    text = html.unescape(text or "")
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


#: A line that matches one of these is not a requirement: equal-opportunity, visa, pay, benefits.
_BOILERPLATE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bequal opportunity employer\b",
        r"\bEEO\b",
        r"\bdiscriminat(e|ion)\b.{0,80}\b(race|gender|religion|national origin|disability|veteran)\b",
        r"\breasonable accommodations?\b",
        r"\bvisa sponsorship\b",
        r"\bH1B\b|\bOPT\b|\bwork authorization\b",
        r"\bpay (range|transparency)\b",
        r"\bsalary range\b|\bbase salary\b|\btotal compensation\b",
        r"\bbenefits (include|package)\b|\bhealth insurance\b|\bdental\b.{0,20}\bvision\b",
        r"\b401\(?k\)?\b",
        r"\bpaid (leave|time off|parental)\b|\bPTO\b",
        r"\bequity\b.{0,20}\b(grant|package|compensation)\b",
        r"job applicant privacy policy",
        r"personal information about job applicants",
    )
)
#: A line matching this is a bare "about us"-style heading, dropped on its own; a heading line that ALSO carries
#: real sentences after a colon or dash (``"About us: we build widgets and sell them to..."``) is a line of
#: content with a label, not just a label, and is judged like any other line instead.
_BOILERPLATE_HEADING = re.compile(r"^(who we are|about us|about the company|our mission|why join|why .{1,40}\?|our benefits|perks)\s*[:?]?$", re.IGNORECASE)
#: "<Company> is the leading ...": a company describing itself. Case matters: the name starts with a capital.
_SELF_DESCRIPTION = re.compile(r"\b[A-Z][\w.&'-]{1,40} is (the|a|an) (trusted |leading |only |first |#1 )")


def _boilerplate_line(line: str) -> bool:
    return bool(_BOILERPLATE_HEADING.match(line) or _SELF_DESCRIPTION.search(line) or any(rule.search(line) for rule in _BOILERPLATE))


def strip_boilerplate(text: str) -> str:
    """``text`` without its equal-opportunity, visa, pay, benefits, "about us" or self-description lines.

    Works line by line (a single ``\\n``), not on ``text`` as one unit: a poster's HTML can collapse to one block
    with no blank line anywhere (``ats_board_clients.html_to_text`` does this), and a whole posting must never be
    dropped because ONE line of it, anywhere, matched a boilerplate rule. A block survives, minus its dropped
    lines, as long as one line of it is not boilerplate.
    """

    kept_blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        kept_lines = [line for raw in block.split("\n") if (line := raw.strip()) and not _boilerplate_line(line)]
        if kept_lines:
            kept_blocks.append("\n".join(kept_lines))
    return "\n\n".join(kept_blocks)


def split_sentences(text: str) -> list[str]:
    """The sentences of ``text``; a bullet line is one (postings use bullets where prose uses full stops)."""

    sentences = []
    for line in (text or "").split("\n"):
        line = re.sub(r"^\s*[-*•]\s*", "", line)
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


_REQUIREMENT_CUES = re.compile(
    r"\b(experience (with|in)|proficien|familiar(ity)? with|knowledge of|expert(ise)? in|"
    r"strong (background|understanding)|hands[- ]on|track record|comfortable with|skilled? (in|with)|"
    r"you (have|are|will|can)|required?|must have|should have|minimum qualifications?|years of)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are be will you your our we have has had as at by from that this it its "
    "into if than then so such not no nor but can could should would may might must shall about across over under "
    "between within experience experienced working work team teams role roles years year ability able strong "
    "excellent solid deep broad good great high level including include includes etc using use used build built "
    "building design designed designing".split()
)


def requirement_sentences(text: str) -> list[str]:
    """The sentences of a posting that read like a requirement ("experience with", "must have", "you will")."""

    return [sentence for sentence in split_sentences(text) if _REQUIREMENT_CUES.search(sentence)]


# ---------------------------------------------------------------------------
# Step 3: the corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusPosting:
    """One posting of the corpus. ``text`` is the cleaned description (``None``: the board cache holds none)."""

    board: str
    posting_id: str
    company: str
    title: str
    url: str
    location: str
    posted: str
    in_window: bool
    text: str | None = None


@dataclass(frozen=True)
class Corpus:
    """The postings a role's counts are made from, newest first, and how the set was cut."""

    postings: tuple[CorpusPosting, ...]
    #: Candidates the index gave; those whose title passed the rule; those a deny word took out; copies of a kept job.
    candidates: int = 0
    title_matches: int = 0
    denied: int = 0
    copies: int = 0
    #: Postings past :data:`CORPUS_LIMIT` (older ones), left out.
    beyond_limit: int = 0
    filters: Mapping[str, object] = field(default_factory=dict)

    @property
    def with_text(self) -> tuple[CorpusPosting, ...]:
        return tuple(posting for posting in self.postings if posting.text)

    @property
    def basis(self) -> str:
        """Which postings are read: those of the window, or every date when fewer than :data:`WIDEN_BELOW` of the window have text."""

        in_window = sum(1 for posting in self.postings if posting.in_window and posting.text)
        return BASIS_WINDOW if in_window >= WIDEN_BELOW else BASIS_ANY_DATE

    @property
    def read(self) -> tuple[CorpusPosting, ...]:
        """The postings the vocabulary is named from: those with text, of the window or (:attr:`basis`) of any date."""

        window_only = self.basis == BASIS_WINDOW
        return tuple(posting for posting in self.postings if posting.text and (posting.in_window or not window_only))

    @property
    def note(self) -> str:
        """Which set was read and how many postings, in one line a course prints; a thin set says its counts are directional."""

        count, stored = len(self.read), len(self.postings)
        scope = f"of {stored} stored for this role's titles; {_filter_words(self.filters)}"
        if self.basis == BASIS_WINDOW:
            text = f"Read: {_postings(count)} of the last {WINDOW_DAYS} days with stored text ({scope})"
        else:
            in_window = sum(1 for posting in self.postings if posting.in_window and posting.text)
            text = (
                f"Fewer than {WIDEN_BELOW} postings of the last {WINDOW_DAYS} days have stored text ({in_window}), so every date is read: "
                f"{_postings(count)} with stored text ({scope})"
            )
        if count < THIN_CORPUS:
            text += f". Only {_postings(count)} match{'es' if count == 1 else ''} this role on your stored boards; counts are directional"
        return text

    def to_json(self) -> dict[str, object]:
        """The numbers only: no posting, no text."""

        with_text = self.with_text
        return {
            "candidates": self.candidates,
            "title_matches": self.title_matches,
            "denied": self.denied,
            "copies": self.copies,
            "beyond_limit": self.beyond_limit,
            "postings": len(self.postings),
            "postings_in_window": sum(1 for posting in self.postings if posting.in_window),
            "with_text": len(with_text),
            "with_text_in_window": sum(1 for posting in with_text if posting.in_window),
            "window_days": WINDOW_DAYS,
            "limit": CORPUS_LIMIT,
            "basis": self.basis,
            "read": len(self.read),
            "widen_below": WIDEN_BELOW,
            "filters": dict(self.filters),
            "note": self.note,
        }

    def fingerprint(self, titles: Titles) -> str:
        """A digest of the title phrases and the texts read: a stored vocabulary fits only the corpus it was named from."""

        digest = hashlib.sha256(json.dumps(titles.to_json(), sort_keys=True).encode("utf-8"))
        for posting in self.read:
            digest.update(b"\x00" + (posting.text or "").encode("utf-8"))
        return digest.hexdigest()


def _postings(count: int) -> str:
    return f"{count} posting{'' if count == 1 else 's'}"


def _filter_words(filters: Mapping[str, object]) -> str:
    """The filters of a corpus as a note says them: the country, then the work mode and area (``any`` when the Jobs search's are not applied)."""

    countries = [str(country) for country in filters.get("countries") or ()]  # type: ignore[union-attr]
    where = "US or unclear location" if filters.get("us_only") else (", ".join(countries) or "any country")
    mode = filters.get("work_mode") or "any"
    words = f"{where}, {'any work mode' if mode == 'any' else f'work mode {mode}'}"
    return words + (f", area {filters['area']}" if filters.get("area") else "")


def _stamp(moment: datetime) -> str:
    """A UTC stamp as the search index writes ``posted`` (fixed width: compares as a string)."""

    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def corpus_filters(home_root: Path, target: Path, *, now: datetime | None = None):
    """The filters of a role corpus: the country of the setup, and nothing else of the Jobs search.

    A course is about a role, not about one person's job search: a remote-only search still reads the hybrid and
    on-site postings of the role (the first real course read 38 of 146 stored postings without this, 17 with
    text). The country is the shared ``find-jobs.json``'s, and US only is on for a US setup exactly as
    ``free_search`` decides it (every posting but one clearly outside the US: an unclear location stays). The
    work mode is ``any`` and there is no area. The window is not a filter here either: the corpus keeps every
    date and marks the postings of the last :data:`WINDOW_DAYS` days.
    """

    from .find_jobs import free_search
    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.filters import DEFAULT_COUNTRY
    from .find_jobs.job_copies import us_only_default
    from .find_jobs.search_index import IndexFilters

    try:
        config = free_search.default_config(Path(home_root), Path(target))
        filters = IndexFilters.from_config(config, now=now)
    except (free_search.FreeSearchError, FindJobsContractError) as exc:
        raise LearningCorpusError("config_unavailable", str(exc)) from exc
    if us_only_default(config.countries):
        filters = replace(filters, countries=(DEFAULT_COUNTRY,), us_only=True)
    return replace(filters, cutoff=None, work_mode="any", area=None)


def _filters_json(filters: object | None) -> dict[str, object]:
    if filters is None:
        return {"countries": [], "us_only": False, "work_mode": "any", "area": None}
    return {
        "countries": list(getattr(filters, "countries", ())),
        "us_only": bool(getattr(filters, "us_only", False)),
        "work_mode": getattr(filters, "work_mode", "any"),
        "area": getattr(filters, "area", None),
    }


def clean_posting_text(text: str | None) -> str | None:
    """A stored description as the counting reads it: HTML out, boilerplate paragraphs out; ``None`` when nothing is left."""

    cleaned = strip_boilerplate(strip_html(text))
    return cleaned or None


def build_corpus(home_root: Path, titles: Titles, filters: object | None = None, *, now: datetime | None = None) -> Corpus:
    """Step 3: the postings of the role, newest first, with their cleaned text. Reads the index and the board cache only.

    ``filters`` is an ``IndexFilters`` (:func:`corpus_filters`) or ``None`` for every stored posting; its window,
    if it has one, is not used. ``search_index_unavailable`` when the index cannot answer (it is never built here).
    """

    from . import postings as postings_module
    from .find_jobs import search_index
    from .find_jobs.contracts import FindJobsContractError, normalize_url
    from .find_jobs.job_copies import copy_key
    from .find_jobs.search_index import IndexQuery, strict_title_match

    home_root = Path(home_root)
    moment = now or datetime.now(UTC)
    if filters is not None:
        filters = replace(filters, cutoff=None)  # type: ignore[type-var]
    found = search_index.candidates(home_root, IndexQuery(titles=titles.phrases, strict=True, filters=filters))  # type: ignore[arg-type]
    if not found.available:
        raise LearningCorpusError(
            "search_index_unavailable",
            f"the search index did not answer ({found.reason}), and a role's postings are read through it. "
            "`gigai scout sources update` builds the index; then run this command again",
        )
    deny = set(titles.deny)
    verdicts: dict[str, bool] = {}
    title_matches = denied = copies = 0
    seen: set[object] = set()
    kept = []
    for row in found.rows:  # newest first
        fits = verdicts.get(row.title)
        if fits is None:
            fits = verdicts[row.title] = any(strict_title_match(row.title, phrase) for phrase in titles.phrases)
        if not fits:
            continue
        title_matches += 1
        if deny & set(_plain_words(row.title)):
            denied += 1
            continue
        key = copy_key(row.board, row.company, row.title, row.content, row.removed)
        if key is not None:
            if key in seen:
                copies += 1
                continue
            seen.add(key)
        kept.append(row)
    beyond = max(0, len(kept) - CORPUS_LIMIT)
    kept = kept[:CORPUS_LIMIT]

    jobs: dict[int, str] = {}
    for number, row in enumerate(kept):
        try:
            jobs[number] = normalize_url(row.url)
        except FindJobsContractError:
            continue  # a URL the board cache cannot be asked about: the posting has no text
    # ``posting_texts`` reads a row's board and job identity only.
    wanted = [SimpleNamespace(board=kept[number].board, job=job) for number, job in jobs.items()]
    texts = postings_module.posting_texts(home_root, wanted)  # type: ignore[arg-type]
    cutoff = _stamp(moment - timedelta(days=WINDOW_DAYS))
    postings = []
    for number, row in enumerate(kept):
        stored = texts.get(jobs.get(number, ""))
        postings.append(
            CorpusPosting(
                board=row.board, posting_id=row.posting_id, company=row.company, title=row.title, url=row.url,
                location=row.location, posted=row.posted, in_window=row.posted >= cutoff,
                text=clean_posting_text(stored.text) if stored is not None else None,
            )
        )
    return Corpus(
        postings=tuple(postings), candidates=len(found.rows), title_matches=title_matches, denied=denied, copies=copies,
        beyond_limit=beyond, filters=_filters_json(filters),
    )


# ---------------------------------------------------------------------------
# Step 4: the vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Concept:
    """One thing a posting can ask for; ``phrases`` are plain words, matched as whole words whatever their case."""

    id: str
    display: str
    category: str
    phrases: tuple[str, ...]
    technical: bool

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "display": self.display, "category": self.category, "phrases": list(self.phrases), "technical": self.technical}


_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PHRASE_WORD = re.compile(r"[A-Za-z0-9+#./&-]+")
_CONCEPT_KEYS = frozenset({"id", "display", "category", "phrases", "technical"})


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """The whole-word matcher of one plain phrase: any case, any run of spaces or a dash between its words.

    Built from the phrase's characters, escaped: a phrase is never read as a regular expression. A dash inside a
    word is optional ("fine-tuning" is "fine tuning" and "finetuning").
    """

    return _any_phrase((phrase,))


def _phrase_source(phrase: str) -> str:
    words = []
    for word in phrase.split():
        words.append(r"[\s\-]?".join(re.escape(part) for part in word.split("-") if part) or re.escape(word))
    return r"[\s\-]+".join(words)


def _any_phrase(phrases: Iterable[str]) -> re.Pattern[str]:
    """One matcher for any of ``phrases`` (each as :func:`phrase_pattern` reads it)."""

    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_phrase_source(phrase) for phrase in phrases) + r")(?![A-Za-z0-9])", re.IGNORECASE)


def concept_matches(concept: Concept, text: str) -> bool:
    return bool(concept.phrases) and _any_phrase(concept.phrases).search(text) is not None


def sample_sentences(texts: Sequence[str], budget: int = SENTENCE_BUDGET) -> list[str]:
    """Requirement-cue sentences of ``texts``, taken evenly: the first of every posting, then the second, ... up to ``budget`` characters.

    A sentence is cut at :data:`SENTENCE_CHARS_MAX` characters and given once (the same line in ten postings is
    boilerplate the paragraph rules missed). The order is the postings' order, so the same corpus gives the same lines.
    """

    queues = [requirement_sentences(text) for text in texts]
    chosen: list[str] = []
    seen: set[str] = set()
    used = 0
    depth = 0
    while any(depth < len(queue) for queue in queues):
        for queue in queues:
            if depth >= len(queue):
                continue
            sentence = queue[depth][:SENTENCE_CHARS_MAX].strip()
            key = sentence.casefold()
            if not sentence or key in seen:
                continue
            if used + len(sentence) + 3 > budget:
                return chosen
            seen.add(key)
            chosen.append(sentence)
            used += len(sentence) + 3
        depth += 1
    return chosen


def vocabulary_prompt(
    role_text: str, lines: Sequence[str], *, minimum: int = CONCEPTS_MIN, maximum: int = CONCEPTS_MAX,
    known_ids: Sequence[str] | None = None, validation_error: BaseException | str | None = None,
) -> str:
    """The vocabulary prompt: ``lines`` are requirement sentences, or (``known_ids`` given: the follow-up) uncovered word groups."""

    follow_up = known_ids is not None
    return _render(
        _template(_VOCABULARY_RESOURCE),
        {
            "role": role_text,
            "min_concepts": str(minimum),
            "max_concepts": str(maximum),
            "known_ids": ", ".join(known_ids) if follow_up else None,  # type: ignore[arg-type]
            "block_title": "UNCOVERED WORD GROUPS" if follow_up else "REQUIREMENT SENTENCES",
            "sentences": fence_untrusted_posting("\n".join(f"- {line}" for line in lines)),
            "validation_error": _error_text(validation_error),
        },
    )


def parse_vocabulary(raw: str, *, minimum: int = CONCEPTS_MIN, maximum: int = CONCEPTS_MAX, accepted: int | None = None) -> tuple[Concept, ...]:
    """The concepts of a model answer, or ``vocabulary_invalid`` naming the first thing wrong.

    Refused: no JSON object, fewer than ``minimum`` or more than ``maximum`` concepts (the message says how many
    the answer held and by how many it missed the bound; with ``accepted``, a longer list up to that many is let
    through for the caller to cut), a concept with other keys than the five, an id that is not lowercase kebab-case or is used twice, a category that is not one of the
    five, no phrase or more than eight, a phrase that is not 1 to 4 plain words, a ``technical`` that is not a
    boolean.
    """

    decoded = _json_object(raw, "vocabulary_invalid")

    def bad(message: str) -> LearningCorpusError:
        return LearningCorpusError("vocabulary_invalid", message)

    items = decoded.get("concepts")
    if not isinstance(items, list):
        raise bad(f"concepts must be a list of {minimum} to {maximum} objects")
    if not minimum <= len(items) <= max(maximum, accepted or 0):
        raise bad(_held("concepts", "object", len(items), minimum, maximum))
    concepts: list[Concept] = []
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != _CONCEPT_KEYS:
            raise bad("every concept has exactly the keys id, display, category, phrases, technical")
        concept_id, display, category, phrases, technical = (item[key] for key in ("id", "display", "category", "phrases", "technical"))
        if not isinstance(concept_id, str) or len(concept_id) > ID_CHARS_MAX or not _ID.fullmatch(concept_id):
            raise bad(f"an id is lowercase kebab-case, at most {ID_CHARS_MAX} characters")
        if concept_id in ids:
            raise bad(f"the id {concept_id} is used twice")
        if not isinstance(display, str) or not display.strip() or len(display.strip()) > DISPLAY_CHARS_MAX or "\n" in display:
            raise bad(f"display of {concept_id} must be 1 to {DISPLAY_CHARS_MAX} characters on one line")
        if category not in CATEGORIES:
            raise bad(f"category of {concept_id} must be one of: {', '.join(CATEGORIES)}")
        if not isinstance(phrases, list) or not 1 <= len(phrases) <= PHRASES_MAX:
            raise bad(f"phrases of {concept_id} must hold 1 to {PHRASES_MAX} phrases")
        plain: list[str] = []
        for phrase in phrases:
            words = phrase.split() if isinstance(phrase, str) else []
            if (
                not 1 <= len(words) <= PHRASE_WORDS_MAX or len(" ".join(words)) > PHRASE_CHARS_MAX
                or not all(_PHRASE_WORD.fullmatch(word) and re.search(r"[A-Za-z0-9]", word) for word in words)
            ):
                raise bad(f"phrases of {concept_id}: a phrase is 1 to {PHRASE_WORDS_MAX} plain words, at most {PHRASE_CHARS_MAX} characters")
            text = " ".join(words).lower()
            if text not in plain:
                plain.append(text)
        if not isinstance(technical, bool):
            raise bad(f"technical of {concept_id} must be true or false")
        ids.add(concept_id)
        concepts.append(Concept(concept_id, display.strip(), str(category), tuple(plain), technical))
    return tuple(concepts)


def screen_vocabulary(concepts: Sequence[Concept], texts: Sequence[str]) -> tuple[tuple[Concept, ...], list[dict[str, object]]]:
    """``(the concepts the postings name, what was dropped and why)``.

    The 60% boilerplate rule answers "is this generic filler, not a role skill", so it applies only to a concept
    whose ``technical`` is false (a seniority or responsibility signal, not a named skill, tool or domain): a
    phrase more than 60% of ``texts`` hold is taken out of such a concept, and a concept whose other phrases then
    name no posting is dropped (``boilerplate``). A technical concept (``technical`` true) is kept whatever its
    share of postings: the whole point of the count is to show what the role needs, and a skill most postings
    name is the strongest signal, not boilerplate. Either way, a concept no posting names at all is dropped
    (``no_match``). The dropped list says, for a boilerplate concept, in how many postings its most common phrase
    stood.
    """

    total = len(texts)
    kept: list[Concept] = []
    dropped: list[dict[str, object]] = []
    for concept in concepts:
        phrases = []
        most = named = 0
        for phrase in concept.phrases:
            pattern = phrase_pattern(phrase)
            count = sum(1 for text in texts if pattern.search(text))
            most = max(most, count)
            if concept.technical or not total or count / total <= BOILERPLATE_SHARE:
                phrases.append(phrase)
                named += count
        screened = replace(concept, phrases=tuple(phrases))
        if not named:
            # Boilerplate when a phrase of it was too common (what is left names nothing); else no posting names it.
            common = len(phrases) < len(concept.phrases)
            dropped.append({
                "id": concept.id, "display": concept.display, "reason": "boilerplate" if common else "no_match",
                "postings": most if common else 0, "of": total,
            })
            continue
        kept.append(screened)
    return tuple(kept), dropped


def concepts_minimum(postings: int) -> int:
    """The fewest concepts a vocabulary answer may hold for ``postings`` postings read: three for every two postings,
    never under :data:`CONCEPTS_MIN_FLOOR`, never over :data:`CONCEPTS_MIN` (40 postings or more).

    A small corpus names fewer things, and a course's field-standard lessons fill what the postings do not name.
    """

    return min(CONCEPTS_MIN, max(CONCEPTS_MIN_FLOOR, 3 * postings // 2))


def trim_vocabulary(concepts: Sequence[Concept], texts: Sequence[str], *, limit: int = CONCEPTS_MAX) -> tuple[tuple[Concept, ...], list[dict[str, object]]]:
    """``(at most limit concepts, what was left out)``: past ``limit`` the concepts the most postings name are kept.

    The order of the answer is kept, and of two concepts as many postings name the earlier one stays. What is
    left out is listed with the reason ``over_limit`` and its count, as the screen lists its drops.
    """

    if len(concepts) <= limit:
        return tuple(concepts), []
    named = [sum(1 for text in texts if _any_phrase(concept.phrases).search(text)) for concept in concepts]
    keep = set(sorted(range(len(concepts)), key=lambda number: (-named[number], number))[:limit])
    dropped = [
        {"id": concept.id, "display": concept.display, "reason": "over_limit", "postings": named[number], "of": len(texts)}
        for number, concept in enumerate(concepts) if number not in keep
    ]
    return tuple(concept for number, concept in enumerate(concepts) if number in keep), dropped


def uncovered_ngrams(texts: Sequence[str], concepts: Sequence[Concept], *, top: int = FOLLOW_UP_MAX) -> list[tuple[str, int]]:
    """The 2- and 3-word groups of the requirement sentences no concept matches, the most frequent first.

    ``(word group, sentences that hold it)``, at most ``top``, only those in :data:`UNCOVERED_MIN_COUNT` sentences
    or more; ties in alphabetical order, so the same corpus gives the same list.
    """

    phrases = [phrase for concept in concepts for phrase in concept.phrases]
    covered = _any_phrase(phrases) if phrases else None
    counts: Counter[str] = Counter()
    for text in texts:
        for sentence in requirement_sentences(text):
            if covered is not None and covered.search(sentence):
                continue
            found = (word.rstrip("./-") for word in re.findall(r"[a-zA-Z][a-zA-Z0-9+/.#-]*", sentence.lower()))
            words = [word for word in found if word not in _STOPWORDS and len(word) > 2]
            for size in (2, 3):
                for group in {" ".join(words[start : start + size]) for start in range(len(words) - size + 1)}:
                    counts[group] += 1
    ordered = sorted(((group, count) for group, count in counts.items() if count >= UNCOVERED_MIN_COUNT), key=lambda item: (-item[1], item[0]))
    return ordered[:top]


@dataclass(frozen=True)
class Vocabulary:
    """Step 4's answer: the concepts kept, and what happened on the way."""

    concepts: tuple[Concept, ...]
    returned: int = 0
    dropped: tuple[Mapping[str, object], ...] = ()
    sentences: int = 0
    #: ``not_needed`` (nothing uncovered), ``added`` (with ``follow_up_added``), ``invalid`` (the answer was not usable and is ignored).
    follow_up: str = "not_needed"
    follow_up_added: int = 0
    #: The fewest concepts the answer had to hold (:func:`concepts_minimum` of the postings read).
    minimum: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "returned": self.returned, "kept": len(self.concepts), "dropped": [dict(item) for item in self.dropped],
            "sentences_sent": self.sentences, "follow_up": self.follow_up, "follow_up_added": self.follow_up_added,
            "minimum": self.minimum, "maximum": CONCEPTS_MAX,
        }


def propose_vocabulary(
    model: RoleModel, role_text: str, texts: Sequence[str], *, on_answer: Callable[[str, str, str | None], object] | None = None,
) -> Vocabulary:
    """Step 4: one call (one more when the answer does not validate), the screen, and at most one follow-up call.

    With no posting text there is nothing to name: no call is made. The answer holds :func:`concepts_minimum` of
    ``texts`` to :data:`CONCEPTS_MAX` concepts; the retry prompt says how many the refused answer held and by how
    many it missed. An answer with more than :data:`CONCEPTS_MAX` (up to :data:`CONCEPTS_ACCEPTED`) is not
    refused: after the screen, the concepts the most postings name are kept (:func:`trim_vocabulary`). A
    follow-up answer that does not validate is ignored (it is never asked for again); a follow-up id the
    vocabulary already has is left out. ``on_answer(call, raw answer, what was wrong or None)`` is told every
    answer as it arrives (``call`` is ``first``, ``retry`` or ``follow_up``), so a caller can keep it.
    """

    if not texts:
        return Vocabulary(())
    lines = sample_sentences(texts)
    if not lines:
        return Vocabulary(())
    heard = on_answer or (lambda _call, _raw, _error: None)
    minimum = concepts_minimum(len(texts))
    error: LearningCorpusError | None = None
    proposed: tuple[Concept, ...] | None = None
    for call in ("first", "retry"):
        raw = model.ask(
            vocabulary_prompt(role_text, lines, minimum=minimum, validation_error=error),
            timeout_seconds=VOCABULARY_TIMEOUT_SECONDS,
        )
        try:
            proposed = parse_vocabulary(raw, minimum=minimum, accepted=CONCEPTS_ACCEPTED)
        except LearningCorpusError as exc:
            model.invalid_output()
            error = exc
            heard(call, raw, str(exc))
        else:
            heard(call, raw, None)
            break
    if proposed is None:
        raise LearningCorpusError(
            "vocabulary_invalid", f"the model's vocabulary for this role was not usable, twice (the last answer: {error})"
        )
    screened, dropped = screen_vocabulary(proposed, texts)
    concepts, over = trim_vocabulary(screened, texts)
    dropped = [*dropped, *over]
    returned = len(proposed)
    follow_up, added = "not_needed", 0
    room = CONCEPTS_MAX - len(concepts)
    groups = uncovered_ngrams(texts, concepts) if room > 0 else []
    if groups:
        prompt = vocabulary_prompt(
            role_text, [f"{group} ({count} sentences)" for group, count in groups], minimum=0, maximum=min(FOLLOW_UP_MAX, room),
            known_ids=[concept.id for concept in concepts],
        )
        raw = model.ask(prompt, timeout_seconds=VOCABULARY_TIMEOUT_SECONDS)
        try:
            extra = parse_vocabulary(raw, minimum=0, maximum=min(FOLLOW_UP_MAX, room))
        except LearningCorpusError as exc:
            model.invalid_output()
            follow_up = "invalid"
            heard("follow_up", raw, str(exc))
        else:
            heard("follow_up", raw, None)
            known = {concept.id for concept in proposed}
            fresh, more_dropped = screen_vocabulary([concept for concept in extra if concept.id not in known], texts)
            returned += len(extra)
            concepts, dropped, added, follow_up = (*concepts, *fresh), [*dropped, *more_dropped], len(fresh), "added"
    return Vocabulary(
        concepts, returned=returned, dropped=tuple(dropped), sentences=len(lines), follow_up=follow_up, follow_up_added=added, minimum=minimum,
    )


# ---------------------------------------------------------------------------
# Step 5: counting
# ---------------------------------------------------------------------------


def _percent(count: int, total: int) -> float:
    return round(100 * count / total, 1) if total else 0.0


def count_concepts(postings: Sequence[CorpusPosting], concepts: Sequence[Concept]) -> list[dict[str, object]]:
    """Step 5: per concept, the postings that name it in the window and in the whole set, and three examples.

    A posting counts once per concept however often it names it; postings with no text are in neither total.
    ``kept`` is the threshold ``count_in90d >= 3 or count_all >= 4``. An example is one sentence of at most 160
    characters with its company, title and URL, from a posting of the window first. Concepts no posting names
    are left out; the order is the window count, then the any-date count, then the id.
    """

    with_text = [posting for posting in postings if posting.text]
    ordered = [posting for posting in with_text if posting.in_window] + [posting for posting in with_text if not posting.in_window]
    n_window = sum(1 for posting in with_text if posting.in_window)
    n_all = len(with_text)
    sentences = [split_sentences(posting.text or "") for posting in ordered]
    counted = []
    for concept in concepts:
        pattern = _any_phrase(concept.phrases)
        count_window = count_all = 0
        examples: list[dict[str, object]] = []
        for posting, lines in zip(ordered, sentences):
            if not pattern.search(posting.text or ""):
                continue
            count_all += 1
            count_window += int(posting.in_window)
            if len(examples) < EXAMPLES_PER_CONCEPT:
                line = next((line for line in lines if len(line) <= EXAMPLE_CHARS_MAX and pattern.search(line)), None)
                if line is not None:
                    examples.append({"phrase": line, "company": posting.company, "title": posting.title, "url": posting.url})
        if not count_all:
            continue
        counted.append({
            **concept.to_json(),
            "count_in90d": count_window, "n_in90d": n_window, "percent_in90d": _percent(count_window, n_window),
            "count_all": count_all, "n_all": n_all, "percent_all": _percent(count_all, n_all),
            "kept": count_window >= KEEP_IN_WINDOW or count_all >= KEEP_ANY_DATE,
            "examples": examples,
        })
    counted.sort(key=lambda item: (-int(item["count_in90d"]), -int(item["count_all"]), str(item["id"])))  # type: ignore[call-overload]
    return counted


# ---------------------------------------------------------------------------
# The model, the estimate, the whole run
# ---------------------------------------------------------------------------


class MeteredRoleModel:
    """The user's configured model target, one call per ``ask``, each recorded as kind ``learning``."""

    def __init__(self, home_root: Path, target: Path) -> None:
        from ..adapters.factory import AdapterFactoryError
        from ..adapters.port import ModelInvocationError
        from ..config import ConfigurationError, load_config
        from ..model_targets import ModelTargetResolutionError
        from . import proposal_execution
        from .call_metrics import KIND_LEARNING, CallMeter
        from .proposal_execution import ScoutProposalExecutionError, _resolve_configured_target_name_for_adapter
        from .quick_assess import _default_model_target

        self.model_target = _default_model_target(Path(target)).value
        self.calls = 0
        try:
            config = load_config(Path(home_root))
            adapter_target = _resolve_configured_target_name_for_adapter(config, self.model_target)
            # Module-attribute lookup, so the test transport and the payload tests reach this call too.
            self._binding = proposal_execution.resolve_model_adapter(config, adapter_target, home_root=Path(home_root))
        except (AdapterFactoryError, ModelTargetResolutionError, ScoutProposalExecutionError, ModelInvocationError, ConfigurationError) as exc:
            raise LearningCorpusError("model_unavailable", str(exc)) from exc
        self._meter = CallMeter(KIND_LEARNING, self.model_target, Path(home_root), Path(target))

    def ask(self, prompt: str, *, timeout_seconds: float | None = None) -> str:
        from ..adapters.port import ModelInvocationError

        self.calls += 1
        try:
            result = self._meter.invoke(
                self._binding.port, self._binding.request(role="reviewer", prompt=prompt, timeout_seconds=timeout_seconds)
            )
        except ModelInvocationError as exc:
            raise LearningCorpusError("model_unavailable", str(exc)) from exc
        return str(getattr(result, "output_text", "") or "")

    def invalid_output(self) -> None:
        self._meter.invalid_output()

    def close(self) -> None:
        self._binding.close()


def corpus_estimate(home_root: Path, target: Path) -> tuple[str, dict[str, object]]:
    """``(model target, estimate)`` of one corpus, from the recorded ``learning`` calls (``call_metrics.estimate``).

    ``calls`` is always a number (:data:`EXPECTED_CALLS`); tokens, seconds and cost are ``None`` and
    ``basis_calls`` 0 while no ``learning`` call is recorded on this model target.
    """

    from .call_metrics import KIND_LEARNING, CallMetricsError, estimate
    from .pipeline.store import PipelineStoreError
    from .quick_assess import _default_model_target

    model = _default_model_target(Path(target)).value
    try:
        found = estimate(KIND_LEARNING, model, EXPECTED_CALLS, home_root=Path(home_root), target=Path(target))
    except (CallMetricsError, PipelineStoreError):
        found = {"calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    calls = found["calls"] if isinstance(found["calls"], int) else EXPECTED_CALLS
    return model, {
        "calls": calls, "max_calls": MAX_CALLS, "tokens": found["tokens"], "seconds": found["seconds"], "cost": found["cost"],
        "basis_calls": found["basis_calls"],
    }


def estimate_words(estimate: Mapping[str, object]) -> str:
    """The estimate as the question says it: calls, and tokens and minutes when the history can say."""

    words = f"~{estimate['calls']} model calls (at most {estimate['max_calls']})"
    if not estimate.get("basis_calls"):
        return words + " (no recorded learning calls yet to estimate tokens or time from)"
    tokens, seconds = estimate.get("tokens"), estimate.get("seconds")
    if isinstance(tokens, (int, float)) and tokens >= 1000:
        words += f", ~{tokens / 1000:.0f}k tokens"
    if isinstance(seconds, (int, float)) and seconds > 0:
        words += f", ~{max(1, round(seconds / 60))} min"
    return words


def ask_response(home_root: Path, target: Path, role_text: str) -> dict[str, object]:
    """What is asked before any model call: the estimate, what the calls send, and the question. Makes no call."""

    role = clean_role_text(role_text)
    model, estimate = corpus_estimate(home_root, target)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ask",
        "role_text": role,
        "model_target": model,
        "estimate": estimate,
        "sends": (
            "the role as typed, and up to 40,000 characters of requirement sentences from the job postings stored on this "
            "computer for that role. No resume, no answer and no story is read or sent."
        ),
        "question": {"text": f"Read the stored postings for '{role}' and count what they ask for? {estimate_words(estimate)}"},
    }


def _stored_titles(store: CorpusStore | None, role: str) -> Titles | None:
    stored = store.read(STEP_TITLES) if store is not None else None
    if not isinstance(stored, Mapping) or stored.get("role_text") != role:
        return None
    phrases, deny = stored.get("phrases"), stored.get("deny")
    if not isinstance(phrases, list) or not isinstance(deny, list) or not phrases or not all(isinstance(item, str) for item in (*phrases, *deny)):
        return None
    return Titles(tuple(phrases), tuple(deny))


def _stored_vocabulary(store: CorpusStore | None, role: str, fingerprint: str) -> Vocabulary | None:
    """The vocabulary a previous run stored, when it was named for this role from these very texts; else ``None``."""

    stored = store.read(STEP_VOCABULARY) if store is not None else None
    if not isinstance(stored, Mapping) or stored.get("role_text") != role or stored.get("fingerprint") != fingerprint:
        return None
    try:
        concepts = parse_vocabulary(json.dumps({"concepts": stored.get("concepts")}), minimum=0, maximum=CONCEPTS_MAX)
        return Vocabulary(
            concepts, returned=int(stored["returned"]), dropped=tuple(dict(item) for item in stored["dropped"]),
            sentences=int(stored["sentences_sent"]), follow_up=str(stored["follow_up"]), follow_up_added=int(stored["follow_up_added"]),
            minimum=int(stored["minimum"]),
        )
    except (LearningCorpusError, KeyError, TypeError, ValueError):
        return None


def run_corpus(
    home_root: Path, role_text: str, *, model: RoleModel, filters: object | None = None,
    now: datetime | None = None, progress: Callable[[str], None] | None = None, store: CorpusStore | None = None,
) -> dict[str, object]:
    """Steps 2 to 5 for one role, as plain data. The only model calls are ``model``'s.

    ``filters`` is :func:`corpus_filters`'s answer (``None``: every stored posting). The result holds counts and,
    per concept, at most three example sentences of at most 160 characters: never a posting's text.

    Without a ``store`` nothing is stored. With one (the course job's work folder) every step's output is handed
    to it as it is made, so a run that failed can be read: ``titles``, ``corpus-summary`` (the counts and the
    filters), ``vocabulary-raw`` (every answer of the model as it arrived, with what was wrong with it),
    ``vocabulary`` (the concepts kept) and ``counts``. A later run on the same store does not ask again for the
    titles of the same role, nor for a vocabulary named from the same texts.
    """

    from .data_labels import UNTRUSTED_TEXT_RULE

    say = progress or (lambda _text: None)
    role = clean_role_text(role_text)
    titles = _stored_titles(store, role)
    if titles is None:
        say("Asking for the title phrases of this role...")
        titles = propose_titles(model, role)
        if store is not None:
            store.write(STEP_TITLES, {"role_text": role, **titles.to_json()})
    say("Reading the stored postings...")
    corpus = build_corpus(Path(home_root), titles, filters, now=now)
    texts = [posting.text for posting in corpus.read if posting.text]
    fingerprint = corpus.fingerprint(titles)
    if store is not None:
        store.write(STEP_SUMMARY, {"role_text": role, "fingerprint": fingerprint, **corpus.to_json()})
    vocabulary = _stored_vocabulary(store, role, fingerprint)
    if vocabulary is None:
        if texts:
            say(f"Asking what {_postings(len(texts))} ask for...")
        answers: list[dict[str, object]] = []

        def keep(call: str, raw: str, error: str | None) -> None:
            answers.append({"call": call, "usable": error is None, "error": error, "answer": raw})
            if store is not None:
                store.write(STEP_RAW, {"role_text": role, "fingerprint": fingerprint, "answers": answers})

        vocabulary = propose_vocabulary(model, role, texts, on_answer=keep)
        if store is not None:
            store.write(
                STEP_VOCABULARY,
                {"role_text": role, "fingerprint": fingerprint, "concepts": [concept.to_json() for concept in vocabulary.concepts], **vocabulary.to_json()},
            )
    counted = count_concepts(corpus.postings, vocabulary.concepts)
    if store is not None:
        store.write(STEP_COUNTS, {"role_text": role, "fingerprint": fingerprint, "concepts": counted})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "done",
        "role_text": role,
        "titles": titles.to_json(),
        "corpus": corpus.to_json(),
        "vocabulary": vocabulary.to_json(),
        "threshold": {"count_in90d": KEEP_IN_WINDOW, "count_all": KEEP_ANY_DATE},
        "rule": UNTRUSTED_TEXT_RULE,
        "concepts": [item for item in counted if item["kept"]],
        "below_threshold": sum(1 for item in counted if not item["kept"]),
        "calls": getattr(model, "calls", None),
    }


def _table(rows: Iterable[Mapping[str, object]]) -> list[str]:
    lines = []
    for item in rows:
        lines.append(
            f"{int(item['count_in90d']):>4} {float(item['percent_in90d']):>5.1f}%  {int(item['count_all']):>4} {float(item['percent_all']):>5.1f}%  "  # type: ignore[call-overload,arg-type]
            f"{str(item['category']):<14} {item['display']}"
        )
    return lines


def render(response: Mapping[str, object]) -> str:
    """A finished corpus as text: the numbers and the concept counts. No posting text, no example sentence."""

    titles, corpus, vocabulary = response["titles"], response["corpus"], response["vocabulary"]
    assert isinstance(titles, Mapping) and isinstance(corpus, Mapping) and isinstance(vocabulary, Mapping)
    filters = corpus["filters"]
    assert isinstance(filters, Mapping)
    where = "US only" if filters.get("us_only") else (", ".join(filters.get("countries") or ()) or "any country")  # type: ignore[arg-type]
    lines = [
        f"Role: {response['role_text']}",
        "Title phrases: " + ", ".join(titles["phrases"]) + (f"  (not: {', '.join(titles['deny'])})" if titles["deny"] else ""),  # type: ignore[arg-type]
        f"Filters: {where}; work mode {filters.get('work_mode')}" + (f"; area {filters.get('area')}" if filters.get("area") else ""),
        (
            f"Postings: {corpus['postings']} ({corpus['postings_in_window']} posted in the last {corpus['window_days']} days); "
            f"{corpus['with_text']} with stored text ({corpus['with_text_in_window']} in the last {corpus['window_days']} days)."
        ),
    ]
    left_out = [f"{corpus[key]} {label}" for key, label in (("denied", "taken out by a deny word"), ("copies", "copies of a kept job"), ("beyond_limit", "older than the newest " + str(corpus["limit"]))) if corpus[key]]
    if left_out:
        lines.append("Left out: " + "; ".join(left_out) + ".")
    if corpus.get("note"):
        lines.append(f"{corpus['note']}.")
    concepts = response["concepts"]
    assert isinstance(concepts, list)
    threshold = response["threshold"]
    assert isinstance(threshold, Mapping)
    lines.append(
        f"Concepts: {len(concepts)} named by at least {threshold['count_in90d']} postings of the last {corpus['window_days']} days or "
        f"{threshold['count_all']} of any date ({response['below_threshold']} below that; the model named {vocabulary['returned']})."
    )
    if concepts:
        lines.append(f" {corpus['window_days']}d      %   any      %  kind           concept")
        lines.extend(_table(concepts))
    boilerplate = [item for item in vocabulary["dropped"] if item["reason"] == "boilerplate"]  # type: ignore[union-attr,index]
    if boilerplate:
        lines.append(
            "Dropped as too common (in more than 60% of the postings): "
            + ", ".join(f"{item['display']} ({item['postings']} of {item['of']})" for item in boilerplate) + "."
        )
    over = sum(1 for item in vocabulary["dropped"] if item["reason"] == "over_limit")  # type: ignore[union-attr,index]
    if over:
        lines.append(f"The model named more than {vocabulary['maximum']} concepts the postings hold: the {over} the fewest postings name were left out.")
    if vocabulary["follow_up"] == "invalid":
        lines.append("The follow-up answer for uncovered wording was not usable and was ignored.")
    if response.get("calls") is not None:
        lines.append(f"Model calls: {response['calls']}. Nothing was stored.")
    return "\n".join(lines)


__all__ = [
    "BOILERPLATE_SHARE",
    "CATEGORIES",
    "CONCEPTS_MAX",
    "CONCEPTS_ACCEPTED",
    "CONCEPTS_MIN",
    "CONCEPTS_MIN_FLOOR",
    "CORPUS_LIMIT",
    "EXPECTED_CALLS",
    "MAX_CALLS",
    "SCHEMA_VERSION",
    "SENTENCE_BUDGET",
    "THIN_CORPUS",
    "WIDEN_BELOW",
    "WINDOW_DAYS",
    "Concept",
    "Corpus",
    "CorpusStore",
    "CorpusPosting",
    "LearningCorpusError",
    "MeteredRoleModel",
    "RoleModel",
    "Titles",
    "Vocabulary",
    "ask_response",
    "build_corpus",
    "clean_posting_text",
    "concept_matches",
    "concepts_minimum",
    "corpus_estimate",
    "corpus_filters",
    "count_concepts",
    "estimate_words",
    "parse_titles",
    "parse_vocabulary",
    "phrase_pattern",
    "propose_titles",
    "propose_vocabulary",
    "render",
    "requirement_sentences",
    "run_corpus",
    "sample_sentences",
    "screen_vocabulary",
    "split_sentences",
    "strip_boilerplate",
    "strip_html",
    "titles_prompt",
    "trim_vocabulary",
    "uncovered_ngrams",
    "vocabulary_prompt",
]
