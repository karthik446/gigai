"""0.1.10.7 C: the user's stories -- experiences worth telling, kept once and offered where they fit.

A story is longer than an answer (``story_bank.py``): a project, a problem,
an outcome. The user's agent writes it from the user's own words in a chat
("Want me to make this a story?"); Scout has no form for it. USER-LEVEL, like
the answers: one set for the whole Scout project, read by every profile.

    <home>/scout/<project_id>/story_bank/stories.json     (0600, plain JSON)

    {"schema_version": "scout-stories:1",
     "stories": {"<story_id>": {<the story below>, "history": [...]}}}

A STORY, as every surface returns it (``Story.to_json``,
``story-bank-contract.md``):

- ``story_id``: ``story:<the title's first words>`` unless the writer names one
- ``title``: "Cut CI time 60% at Acme"
- ``company``, ``role``, ``period`` (rough is fine)
- ``raw``: the user's own words, kept as said
- ``narrative``: ``{"situation", "task", "action", "result"}``, loosely STAR;
  every part an optional string
- ``tags``: a list of tags
- ``answers_questions``: the interview questions it answers
- ``sources``: ``[{"question_id", "job_identity"}]``, the job question that
  triggered it
- ``jobs``: the postings whose assessment cited it (``kind: used``)
- ``written_by`` (``operator`` | ``agent``), ``created_at``, ``updated_at``,
  ``revision``, ``history``

WRITES.  ``save_story`` (new), ``edit_story``, ``delete_story``. Every write
runs the contact-data check on every text it holds
(``story_bank.refuse_personal_info``), bumps ``revision`` and records the
writer. An edit or a delete names the ``revision`` it read; a stale one is
refused with ``revision_conflict`` and the story as it is now.

USE.  ``relevant_stories`` searches the stories for ONE job, locally: an
in-memory SQLite FTS5 table (the tokenizer of ``find_jobs/text_index.py``)
holding the stories, queried with the posting's skill keywords
(``posting_keywords.extract_keywords``) and title words. Only the few that
match go into the assess prompt as evidence (``prompt_line``,
``story_bank.AssessBank.for_job``). The table lives in memory for that one
search: story text is never written to a SQLite file. ``prep_questions``
pools ``answers_questions`` across the stories: the basic interview prep list.

Local and model-free: nothing here calls a model or the network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import threading

from ..canonical import digest_imported_bytes
from . import story_bank
from .assessment_core import BankAnswer
from .find_jobs.discovery.storage import atomic_write
from .question_ids import is_valid_question_id, normalize_question_id
from .story_bank import ACTOR_OPERATOR, ACTORS, JOB_USED, BankJob, StoryBankError

SCHEMA_VERSION = "scout-stories:1"

#: The stories one assessment's prompt is offered, at most.
MAX_PROMPT_STORIES = 3
#: One story's line in the prompt, at most.
MAX_STORY_LINE_CHARS = 420

NARRATIVE_PARTS: tuple[str, ...] = ("situation", "task", "action", "result")

MAX_TITLE_CHARS = 200
_MAX_SHORT_CHARS = 200
MAX_RAW_CHARS = 16_000
MAX_PART_CHARS = 4_000
_MAX_LIST = 12
_MAX_QUESTION_CHARS = 300
_MAX_SOURCES = 20
_SLUG_WORDS = 6

_FILE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Story:
    """One story, as every surface returns it (``to_json`` is the contract's Story)."""

    story_id: str
    title: str
    company: str = ""
    role: str = ""
    period: str = ""
    raw: str = ""
    narrative: Mapping[str, str] | None = None
    tags: tuple[str, ...] = ()
    answers_questions: tuple[str, ...] = ()
    sources: tuple[Mapping[str, str], ...] = ()
    jobs: tuple[BankJob, ...] = ()
    written_by: str = ACTOR_OPERATOR
    created_at: str | None = None
    updated_at: str | None = None
    revision: int = 0
    history: tuple[Mapping[str, str], ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "story_id": self.story_id,
            "title": self.title,
            "company": self.company,
            "role": self.role,
            "period": self.period,
            "raw": self.raw,
            "narrative": dict(self.narrative or {}),
            "tags": list(self.tags),
            "answers_questions": list(self.answers_questions),
            "sources": [dict(item) for item in self.sources],
            "jobs": [job.to_json() for job in self.jobs],
            "written_by": self.written_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "history": [dict(item) for item in self.history],
        }

    def text(self) -> str:
        """Everything the story says, for the search and the staleness rule."""

        narrative = self.narrative or {}
        return "\n".join(
            part
            for part in (self.company, self.role, *self.tags, *self.answers_questions, *(narrative.get(key, "") for key in NARRATIVE_PARTS), self.raw)
            if part
        )


# --- the file --------------------------------------------------------------------------------


def _path(home_root: Path, target: Path) -> Path:
    try:
        return story_bank.stories_path(Path(home_root), Path(target))
    except Exception as exc:  # noqa: BLE001 - any failure to name the project is one typed refusal
        raise StoryBankError("target_unavailable", "this folder is not bound to a GigAI project") from exc


def _read(home_root: Path, target: Path) -> dict[str, dict[str, object]]:
    """``story_id -> stored story``, tolerantly: missing, unreadable or another schema -> none."""

    path = _path(home_root, target)
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return {}
    stored = value.get("stories")
    return {str(key): item for key, item in stored.items() if isinstance(item, dict)} if isinstance(stored, dict) else {}


def _write(home_root: Path, target: Path, stored: Mapping[str, Mapping[str, object]]) -> None:
    atomic_write(
        _path(home_root, target),
        json.dumps({"schema_version": SCHEMA_VERSION, "stories": stored}, indent=2, sort_keys=True).encode("utf-8"),
    )


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _story(story_id: str, item: Mapping[str, object]) -> Story:
    narrative = item.get("narrative")
    sources = item.get("sources")
    return Story(
        story_id=story_id,
        title=str(item.get("title") or ""),
        company=str(item.get("company") or ""),
        role=str(item.get("role") or ""),
        period=str(item.get("period") or ""),
        raw=str(item.get("raw") or ""),
        narrative={key: str(narrative[key]) for key in NARRATIVE_PARTS if isinstance(narrative.get(key), str)} if isinstance(narrative, dict) else {},
        tags=_strings(item.get("tags")),
        answers_questions=_strings(item.get("answers_questions")),
        sources=tuple(
            {key: str(source[key]) for key in ("question_id", "job_identity") if isinstance(source.get(key), str)}
            for source in (sources if isinstance(sources, list) else ())
            if isinstance(source, dict)
        ),
        jobs=tuple(story_bank.stored_jobs(item.get("jobs"))),
        written_by=item["written_by"] if item.get("written_by") in ACTORS else ACTOR_OPERATOR,  # type: ignore[arg-type]
        created_at=item.get("created_at") if isinstance(item.get("created_at"), str) else None,  # type: ignore[arg-type]
        updated_at=item.get("updated_at") if isinstance(item.get("updated_at"), str) else None,  # type: ignore[arg-type]
        revision=item["revision"] if type(item.get("revision")) is int else 0,  # type: ignore[arg-type]
        history=story_bank.stored_history(item.get("history")),
    )


def list_stories(*, home_root: Path, target: Path) -> tuple[Story, ...]:
    """Every story of the user, sorted by id."""

    stored = _read(Path(home_root), Path(target))
    return tuple(_story(story_id, stored[story_id]) for story_id in sorted(stored))


def get_story(*, home_root: Path, target: Path, story_id: str) -> Story | None:
    wanted = _normal_id(story_id)
    return next((story for story in list_stories(home_root=home_root, target=target) if story.story_id == wanted), None)


def mark(story: Story) -> str:
    """An opaque mark that changes when THIS story is written again; no story text."""

    return digest_imported_bytes(f"story\n{story.story_id}\n{story.revision}\n{story.updated_at or ''}".encode("utf-8"))[len("sha256:"):][:16]


# --- validation ------------------------------------------------------------------------------


def _normal_id(story_id: str) -> str:
    return normalize_question_id(str(story_id))


def story_id_for(title: str) -> str:
    """The id a new story gets when the writer names none: ``story:<the title's first words>``."""

    words = [word.strip(".-+#") for word in story_bank._WORD.findall(title.lower())]  # noqa: SLF001
    kept = [word for word in words if word and word not in story_bank._STOPWORDS][:_SLUG_WORDS]  # noqa: SLF001
    slug = re.sub(r"[^a-z0-9._-]+", "_", "_".join(kept))[:100].strip("_")
    if not slug:
        raise StoryBankError("invalid_value", "title must hold at least one word to name the story; or pass story_id")
    return _normal_id(f"story:{slug}")


def _line(value: object, *, name: str, limit: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise StoryBankError("wrong_type", f"{name} must be a string")
    clean = " ".join(value.split())
    if required and not clean:
        raise StoryBankError("invalid_value", f"{name} must not be empty")
    if len(clean) > limit:
        raise StoryBankError("invalid_value", f"{name} is longer than {limit} characters")
    if clean:
        story_bank.refuse_personal_info(clean, what=f"this story's {name}")
    return clean


def _block(value: object, *, name: str, limit: int) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise StoryBankError("wrong_type", f"{name} must be a string")
    clean = value.strip()
    if len(clean) > limit:
        raise StoryBankError("invalid_value", f"{name} is longer than {limit} characters")
    if clean:
        story_bank.refuse_personal_info(clean, what=f"this story's {name}")
    return clean


def _narrative(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StoryBankError("wrong_type", "narrative must be an object with any of: " + ", ".join(NARRATIVE_PARTS))
    unknown = sorted(set(value) - set(NARRATIVE_PARTS))
    if unknown:
        raise StoryBankError("unknown_key", f"unknown narrative part: {unknown[0]}; allowed: {', '.join(NARRATIVE_PARTS)}")
    out: dict[str, str] = {}
    for key in NARRATIVE_PARTS:
        part = _block(value.get(key), name=f"narrative.{key}", limit=MAX_PART_CHARS)
        if part:
            out[key] = part
    return out


def _list(value: object, *, name: str, limit: int, tag: bool = False) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise StoryBankError("wrong_type", f"{name} must be a list of strings")
    out: list[str] = []
    for item in value:
        clean = story_bank.clean_tag(item) if tag and isinstance(item, str) else _line(item, name=name, limit=limit)
        if clean and clean not in out:
            out.append(clean)
    if len(out) > _MAX_LIST:
        raise StoryBankError("invalid_value", f"{name} holds more than {_MAX_LIST} items")
    return out


def _sources(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise StoryBankError("wrong_type", "sources must be a list of {question_id, job_identity}")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"question_id", "job_identity"}:
            raise StoryBankError("invalid_value", "a source is {question_id, job_identity}: the job question that triggered the story")
        source: dict[str, str] = {}
        question_id = item.get("question_id")
        if isinstance(question_id, str) and question_id.strip():
            normalized = normalize_question_id(question_id)
            if not is_valid_question_id(normalized):
                raise StoryBankError("invalid_value", "a source's question_id must match ^[A-Za-z0-9._:-]{1,128}$")
            source["question_id"] = normalized
        job_identity = item.get("job_identity")
        if isinstance(job_identity, str) and job_identity.strip():
            source["job_identity"] = job_identity.strip()[:2000]
        if source and source not in out:
            out.append(source)
    if len(out) > _MAX_SOURCES:
        raise StoryBankError("invalid_value", f"sources holds more than {_MAX_SOURCES} items")
    return out


#: field -> how it is cleaned (the contact-data check runs inside each).
_FIELDS = {
    "title": lambda value: _line(value, name="title", limit=MAX_TITLE_CHARS, required=True),
    "company": lambda value: _line(value, name="company", limit=_MAX_SHORT_CHARS),
    "role": lambda value: _line(value, name="role", limit=_MAX_SHORT_CHARS),
    "period": lambda value: _line(value, name="period", limit=_MAX_SHORT_CHARS),
    "raw": lambda value: _block(value, name="raw", limit=MAX_RAW_CHARS),
    "narrative": _narrative,
    "tags": lambda value: _list(value, name="tags", limit=story_bank.MAX_TAG_CHARS, tag=True),
    "answers_questions": lambda value: _list(value, name="answers_questions", limit=_MAX_QUESTION_CHARS),
    "sources": _sources,
}
#: What a writer may set (everything else is Scout's: ids, dates, revision, jobs).
STORY_FIELDS: tuple[str, ...] = tuple(_FIELDS)


def _cleaned(fields: Mapping[str, object]) -> dict[str, object]:
    unknown = sorted(set(fields) - set(_FIELDS))
    if unknown:
        raise StoryBankError("unknown_key", f"unknown story field: {unknown[0]}; allowed: {', '.join(STORY_FIELDS)}")
    return {key: _FIELDS[key](value) for key, value in fields.items()}


# --- writes ----------------------------------------------------------------------------------


def save_story(*, home_root: Path, target: Path, fields: Mapping[str, object], story_id: str | None = None, actor: str | None = None) -> Story:
    """Add a NEW story; the story as saved.

    ``fields``: ``title`` (required) and any of ``STORY_FIELDS``. ``story_id``
    is optional (``story:<the title's first words>`` otherwise).
    ``story_exists`` (with the story) when that id is taken, by a story or by
    an answer: change it with ``edit_story``. ``personal_info_refused`` when
    any text holds a contact shape.
    """

    writer = story_bank.actor_value(actor)
    clean = _cleaned({"title": None, **fields})
    chosen = _normal_id(story_id) if story_id and story_id.strip() else story_id_for(str(clean["title"]))
    if not is_valid_question_id(chosen) or ":" not in chosen:
        raise StoryBankError("invalid_value", "story_id must look like story:<words> (^[A-Za-z0-9._:-]{1,128}$)")
    with story_bank._WRITE_LOCK:  # noqa: SLF001 - one lock for the answers and the stories
        existing = get_story(home_root=home_root, target=target, story_id=chosen)
        if existing is not None:
            raise StoryBankError("story_exists", f"there is already a story {chosen!r}; edit that story instead", entry=existing)
        try:
            taken = story_bank.get_answer(home_root=home_root, target=target, question_id=chosen, with_jobs=False)
        except (StoryBankError, story_bank.PrivateRecordError):
            taken = None
        if taken is not None:
            raise StoryBankError("story_exists", f"{chosen!r} is already the id of an answer; pass another story_id")
        at = story_bank._now()  # noqa: SLF001
        item: dict[str, object] = {**clean, "created_at": at}
        story_bank.stamp(item, at=at, actor=writer, action="added")
        with _FILE_LOCK:
            stored = _read(Path(home_root), Path(target))
            stored[chosen] = item
            _write(Path(home_root), Path(target), stored)
    return _story(chosen, item)


def edit_story(
    *, home_root: Path, target: Path, story_id: str, fields: Mapping[str, object], actor: str | None = None, expected_revision: int | None = None
) -> Story:
    """Change the given fields of a story; the updated story. ``expected_revision``: the ``revision`` the caller read."""

    writer = story_bank.actor_value(actor)
    if not fields:
        raise StoryBankError("invalid_value", "give at least one of: " + ", ".join(STORY_FIELDS))
    clean = _cleaned(fields)
    wanted = _normal_id(story_id)
    with story_bank._WRITE_LOCK, _FILE_LOCK:  # noqa: SLF001
        stored = _read(Path(home_root), Path(target))
        item = stored.get(wanted)
        if item is None:
            raise StoryBankError("not_found", f"there is no story {wanted!r}")
        story_bank.refuse_stale(_story(wanted, item), expected_revision, what="story")
        item.update(clean)
        story_bank.stamp(item, at=story_bank._now(), actor=writer, action="changed: " + ", ".join(sorted(clean)))  # noqa: SLF001
        _write(Path(home_root), Path(target), stored)
        return _story(wanted, item)


def delete_story(*, home_root: Path, target: Path, story_id: str, expected_revision: int | None = None) -> str:
    """Remove a story: it is never listed, searched or sent again. The id removed."""

    wanted = _normal_id(story_id)
    with story_bank._WRITE_LOCK, _FILE_LOCK:  # noqa: SLF001
        stored = _read(Path(home_root), Path(target))
        item = stored.get(wanted)
        if item is None:
            raise StoryBankError("not_found", f"there is no story {wanted!r}")
        story_bank.refuse_stale(_story(wanted, item), expected_revision, what="story")
        del stored[wanted]
        _write(Path(home_root), Path(target), stored)
    return wanted


def record_use(*, home_root: Path, target: Path, story_ids: Iterable[str], posting: Mapping[str, object]) -> None:
    """Note the posting whose assessment cited these stories (``jobs``, ``kind: used``). Not a new revision."""

    at = story_bank._now()  # noqa: SLF001
    with _FILE_LOCK:
        stored = _read(Path(home_root), Path(target))
        changed = False
        for story_id in story_ids:
            item = stored.get(story_id)
            if item is not None:
                story_bank.add_job(item, story_bank.job_json(posting, JOB_USED, at))
                changed = True
        if changed:
            _write(Path(home_root), Path(target), stored)


# --- use: the per-job search, the prompt line, the staleness rule, the prep list -------------


def job_terms(*, title: str, text: str) -> tuple[str, ...]:
    """What a job is searched by: its skill keywords (every spelling), else its title words."""

    from .posting_keywords import aliases_for, extract_keywords

    keywords = extract_keywords(text, title=title)
    terms: list[str] = []
    for name in (*keywords.must, *keywords.nice):
        for spelling in aliases_for(name):
            if spelling and spelling.casefold() not in (term.casefold() for term in terms):
                terms.append(spelling)
    if not terms:
        terms = [word for word in keywords.title if word not in story_bank._STOPWORDS]  # noqa: SLF001
    return tuple(terms)


_TERM = re.compile(r"\w+", re.UNICODE)


def _fts_query(terms: Sequence[str]) -> str:
    """The terms as FTS5 phrases joined by OR: always valid, no operator from the posting."""

    phrases = [" ".join(_TERM.findall(term)) for term in terms]
    # A one-letter phrase ("C", "R") would match any stray letter of a story.
    return " OR ".join(f'"{phrase}"' for phrase in dict.fromkeys(phrases) if len(phrase) > 1)


def relevant_stories(stories: Sequence[Story], *, title: str = "", text: str = "", limit: int = MAX_PROMPT_STORIES) -> tuple[Story, ...]:
    """The few stories that match ONE job, best first; none when nothing matches.

    An in-memory FTS5 table (never a file) holds each story's title and text;
    the query is the posting's skill keywords, else its title words
    (``job_terms``), ranked by bm25 with the title weighted. A SQLite without
    FTS5 falls back to counting the terms each story names.
    """

    if not stories or limit <= 0:
        return ()
    terms = job_terms(title=title, text=text)
    query = _fts_query(terms)
    if not query:
        return ()
    try:
        from .find_jobs.text_index import _TOKENIZE

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(f"CREATE VIRTUAL TABLE story USING fts5(title, body, tokenize='{_TOKENIZE}')")
            conn.executemany("INSERT INTO story(rowid, title, body) VALUES (?, ?, ?)", [(index, story.title, story.text()) for index, story in enumerate(stories)])
            rows = conn.execute("SELECT rowid FROM story WHERE story MATCH ? ORDER BY bm25(story, 4.0, 1.0), rowid LIMIT ?", (query, limit)).fetchall()
        finally:
            conn.close()
        return tuple(stories[row[0]] for row in rows)
    except sqlite3.Error:
        from .posting_keywords import term_regex

        patterns = [term_regex(term) for term in terms]
        scored = [(sum(bool(pattern.search(f"{story.title}\n{story.text()}")) for pattern in patterns), -index, story) for index, story in enumerate(stories)]
        return tuple(story for hits, _order, story in sorted((item for item in scored if item[0]), key=lambda item: (-item[0], -item[1]))[:limit])


def prompt_line(story: Story, *, names: Iterable[str] = ()) -> BankAnswer | None:
    """One story as a STORY BANK line: its id, the question it answers (else its title) and a short telling.

    Contact details are redacted and the words of ``names`` (the resume's own
    name line, when the caller knows it) removed, as for an answer; a story
    whose line still shows a contact detail is left out (``None``).
    """

    names = tuple(names)

    narrative = story.narrative or {}
    told = " ".join(narrative[key] for key in NARRATIVE_PARTS if narrative.get(key)) or story.raw
    where = ", ".join(part for part in (story.role, story.company, story.period) if part)
    summary = story_bank.one_line(story_bank._redacted(f"{story.title}{' (' + where + ')' if where else ''}: {told}" if told else story.title, names), MAX_STORY_LINE_CHARS)  # noqa: SLF001
    question = story_bank.one_line(story_bank._redacted(story.answers_questions[0] if story.answers_questions else story.title, names), 160)  # noqa: SLF001
    if not summary or story_bank.personal_info_in_answer(f"{question} {summary}"):
        return None
    return BankAnswer(question_id=story.story_id, question=question, summary=summary)


def answers_question(story: Story, *, question_id: str, question: str = "") -> bool:
    """Whether a story is about an assessment's open question (model-free; the staleness rule).

    True when the question's subject (its id's value: ``tooling:kubernetes``
    -> kubernetes) is named by the story, or when the question's words are
    close to one of the questions the story says it answers.
    """

    tokens = story_bank._tokens  # noqa: SLF001
    normalized = normalize_question_id(question_id)
    _category, _, value = normalized.partition(":")
    subject = tokens(value or normalized)
    if subject and subject <= tokens(f"{story.title}\n{story.text()}"):
        return True
    asked = subject | tokens(question)
    return any(story_bank._jaccard(asked, tokens(item)) >= story_bank.NEAR_MATCH_THRESHOLD for item in story.answers_questions)  # noqa: SLF001


def prep_questions(stories: Iterable[Story]) -> list[dict[str, object]]:
    """``answers_questions`` pooled across the stories: the basic interview prep list.

    One row per question (compared without case or spacing), in first-seen
    order, with the stories that answer it: ``{"question", "stories":
    [{"story_id", "title"}]}``.
    """

    pooled: dict[str, dict[str, object]] = {}
    for story in stories:
        for question in story.answers_questions:
            key = " ".join(question.split()).casefold()
            row = pooled.setdefault(key, {"question": question, "stories": []})
            row["stories"].append({"story_id": story.story_id, "title": story.title})  # type: ignore[union-attr]
    return list(pooled.values())


__all__ = [
    "MAX_PROMPT_STORIES",
    "NARRATIVE_PARTS",
    "SCHEMA_VERSION",
    "STORY_FIELDS",
    "Story",
    "answers_question",
    "delete_story",
    "edit_story",
    "get_story",
    "job_terms",
    "list_stories",
    "mark",
    "prep_questions",
    "prompt_line",
    "record_use",
    "relevant_stories",
    "save_story",
    "story_id_for",
]
