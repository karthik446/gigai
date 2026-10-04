"""0.1.10.7 C: the answers and stories routes -- user-level, written mainly by the user's agent.

    GET    /api/answers                  every answer (``q`` and ``tag`` narrow the list)      [answers.py]
    POST   /api/answers                  save an answer (optionally re-assess the job)        [answers.py]
    GET    /api/answers/match            the near match for one question ("We already know: ...")
    GET    /api/answers/{question_id}    one answer
    PUT    /api/answers/{question_id}    edit: answer, question words and/or tag
    DELETE /api/answers/{question_id}    remove it

    GET    /api/stories                  every story (``q`` and ``tag`` narrow the list)
    POST   /api/stories                  add a story
    GET    /api/stories/prep             ``answers_questions`` pooled across the stories
    GET    /api/stories/{story_id}       one story
    PUT    /api/stories/{story_id}       edit the given fields
    DELETE /api/stories/{story_id}       remove it

Every route is local and model-free (``gigai.scout.story_bank``, ``gigai.scout.stories``).
Answers and stories belong to the user, not to a profile: no route takes a ``profile_id``. The
reads return the user's text, so ``do_GET`` runs the Host check before them; the writes go
through ``_check_csrf``. Nothing here logs an answer or a story.

Two writers (the user's agent and the user) share them. A write says who it is with ``actor``
(``operator``, the default, or ``agent``; the body field, or the ``X-GigAI-Actor`` header), and
``PUT``/``DELETE`` name the ``revision`` they read: when the answer or story changed since, the
reply is ``409 revision_conflict`` with the current ``answer`` / ``story`` in the error. Every
write runs the contact-data check (shape-only: email, phone, links, a street address; GigAI
stores no name to look for) and refuses with ``422 personal_info_refused``.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ....private_records import PrivateRecordError
from ... import stories, story_bank
from ...pipeline import triggers as pipeline_triggers

ANSWERS_SCHEMA = "scout-answers-response:1"
STORIES_SCHEMA = "scout-stories-response:1"

ACTOR_HEADER = "X-GigAI-Actor"

_LIST_KEYS = frozenset({"q", "tag"})
_MATCH_KEYS = frozenset({"question_id", "question"})
_ANSWER_PUT_KEYS = frozenset({"answer", "question", "tag", "revision", "actor", "source"})
_DELETE_KEYS = frozenset({"revision", "actor"})
_STORY_POST_KEYS = frozenset({"story_id", "actor", *stories.STORY_FIELDS})
_STORY_PUT_KEYS = frozenset({"revision", "actor", *stories.STORY_FIELDS})

ERROR_STATUS: dict[str, HTTPStatus] = {
    "not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "personal_info_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unknown_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "answer_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "revision_conflict": HTTPStatus.CONFLICT,
    "story_exists": HTTPStatus.CONFLICT,
}


def _match_id(path: str, prefix: str) -> str | None:
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):]
    if not remainder or "/" in remainder:
        return None
    return unquote(remainder)


def _match_answer_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{question_id}`` from ``/api/answers/{question_id}``.

    The same shape as ``common._match_run_id`` / ``profiles._match_profile_id``
    (``server.py``'s dispatch and ``tests/api_e2e/route_inventory.py``'s scanner
    recognize the name). The id is percent-decoded; no embedded slash.
    """

    return None if suffix else _match_id(path, "/api/answers/")


def _match_story_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{story_id}`` from ``/api/stories/{story_id}`` (as ``_match_answer_id``)."""

    return None if suffix else _match_id(path, "/api/stories/")


def _narrowed(items: list[dict[str, object]], *, q: str | None, tag: str | None, tags_of, text_of) -> list[dict[str, object]]:
    if tag and tag.strip():
        wanted = tag.strip().lower()
        items = [item for item in items if wanted in tags_of(item)]
    if q and q.strip():
        needle = q.strip().casefold()
        items = [item for item in items if needle in text_of(item).casefold()]
    return items


def answers_response(home_root: Path, target: Path, *, q: str | None = None, tag: str | None = None) -> dict[str, object]:
    """The ``GET /api/answers`` body (also what ``gigai scout answers list --json`` prints)."""

    entries = [entry.to_json() for entry in story_bank.read_bank(home_root=home_root, target=target)]
    shown = _narrowed(
        entries, q=q, tag=tag,
        tags_of=lambda item: (item["tag"],),
        text_of=lambda item: " ".join(str(item[key]) for key in ("question_id", "question", "answer", "tag")),
    )
    return {"schema_version": ANSWERS_SCHEMA, "answers": shown, "total": len(entries), "tags": sorted({str(entry["tag"]) for entry in entries})}


def answer_response(entry: story_bank.BankEntry) -> dict[str, object]:
    """One answer's body: ``GET``/``PUT /api/answers/{question_id}`` and ``answers show|save --json``."""

    return {"schema_version": ANSWERS_SCHEMA, "answer": entry.to_json()}


def stories_response(home_root: Path, target: Path, *, q: str | None = None, tag: str | None = None) -> dict[str, object]:
    """The ``GET /api/stories`` body (also what ``gigai scout story list --json`` prints)."""

    found = [story.to_json() for story in stories.list_stories(home_root=home_root, target=target)]
    shown = _narrowed(
        found, q=q, tag=tag,
        tags_of=lambda item: item["tags"],
        text_of=lambda item: " ".join(
            [str(item[key]) for key in ("story_id", "title", "company", "role", "period", "raw")]
            + [str(part) for part in item["narrative"].values()]  # type: ignore[union-attr]
            + [str(part) for key in ("tags", "answers_questions") for part in item[key]]  # type: ignore[union-attr]
        ),
    )
    return {"schema_version": STORIES_SCHEMA, "stories": shown, "total": len(found), "tags": sorted({str(tag) for item in found for tag in item["tags"]})}  # type: ignore[union-attr]


def story_response(story: stories.Story) -> dict[str, object]:
    """One story's body: ``GET``/``POST``/``PUT /api/stories[/{story_id}]`` and ``story show|save --json``."""

    return {"schema_version": STORIES_SCHEMA, "story": story.to_json()}


def prep_response(home_root: Path, target: Path) -> dict[str, object]:
    """The ``GET /api/stories/prep`` body (also ``gigai scout story prep --json``)."""

    return {"schema_version": STORIES_SCHEMA, "questions": stories.prep_questions(stories.list_stories(home_root=home_root, target=target))}


def error_extra(exc: Exception) -> dict[str, object] | None:
    """What a stale or duplicate write carries beside the error: the answer or story as it is now."""

    entry = getattr(exc, "entry", None)
    if entry is None:
        return None
    return {"story" if isinstance(entry, stories.Story) else "answer": entry.to_json()}


class StoryBankRoutesMixin:
    """``Handler`` mixin: ``/api/answers/...`` (one answer, the match) and ``/api/stories...``."""

    def _story_bank_fail(self, exc: Exception) -> None:
        code = getattr(exc, "code", "invalid_value")
        status = ERROR_STATUS.get(code, HTTPStatus.CONFLICT)
        extra = error_extra(exc)
        if extra is not None:
            self._error_with_extra(status, code, str(exc), extra)
            return
        self._error(status, code, str(exc))

    def _story_bank_paths(self) -> tuple[Path, Path] | None:
        home_root = getattr(self._backend, "home_root", None)
        target = getattr(self._backend, "target", None)
        if home_root is None or target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return home_root, target

    def _story_bank_query(self, allowed: frozenset[str]) -> dict[str, str] | None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = sorted(set(query) - allowed)
        if unknown:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key",
                f"unknown query key: {unknown[0]}; allowed: {', '.join(sorted(allowed)) or 'none'}",
            )
            return None
        return {key: values[0] for key, values in query.items()}

    def _story_bank_body(self, allowed: frozenset[str]) -> dict[str, object] | None:
        body = self._read_json_body()
        if body is None:
            return None
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return None
        unknown = sorted(set(body) - allowed)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}; allowed: {', '.join(sorted(allowed))}")
            return None
        return body

    def _story_bank_actor(self, given: object) -> str:
        """``actor`` from the body/query, else the ``X-GigAI-Actor`` header, else who the request is from.

        0110-10-04: a write that names no writer is the operator's only when
        it is the Scout UI's: a browser page sends ``Origin`` on every write,
        and ``_check_csrf`` has already refused any origin but this server's
        own. Everything else on loopback (curl, a script, the user's agent) is
        recorded as ``agent``: an answer an agent wrote is never stored as the
        user's own because the agent did not say who it was.
        """

        if given is not None and not isinstance(given, str):
            raise story_bank.StoryBankError("wrong_type", "actor must be a string")
        said = given if isinstance(given, str) and given.strip() else self.headers.get(ACTOR_HEADER)
        if said is None or not str(said).strip():
            return story_bank.ACTOR_OPERATOR if self.headers.get("Origin") else story_bank.ACTOR_AGENT
        return story_bank.actor_value(said)

    # --- answers ---------------------------------------------------------------------------

    def _handle_get_answer(self, question_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(frozenset())
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            entry = story_bank.get_answer(home_root=home_root, target=target, question_id=question_id)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        if entry is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", f"there is no answer for {question_id!r}")
            return
        self._write_json(HTTPStatus.OK, answer_response(entry))

    def _handle_get_answers_match(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_MATCH_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        question_id = (query.get("question_id") or "").strip()
        if not question_id:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "question_id is required")
            return
        try:
            entries = story_bank.read_bank(home_root=home_root, target=target, with_jobs=False)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        match = story_bank.near_match(entries, question_id=question_id, question=query.get("question") or "")
        self._write_json(HTTPStatus.OK, {"schema_version": ANSWERS_SCHEMA, "question_id": question_id, "match": None if match is None else match.to_json()})

    def _handle_put_answer(self, question_id: str) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_ANSWER_PUT_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        for key in ("answer", "question", "tag", "source"):
            if body.get(key) is not None and not isinstance(body[key], str):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", f"{key} must be a string")
                return
        try:
            entry = story_bank.edit_answer(
                home_root=home_root, target=target, question_id=question_id,
                answer=body.get("answer"), question=body.get("question"), tag=body.get("tag"),  # type: ignore[arg-type]
                source=body.get("source"),  # type: ignore[arg-type]
                actor=self._story_bank_actor(body.get("actor")),
                expected_revision=story_bank.revision_value(body.get("revision"), required=True),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        if body.get("answer") is not None or body.get("question") is not None:  # a tag alone answers nothing new
            self._pipeline_fire(pipeline_triggers.pending_answer(home_root, target, entry))
        self._write_json(HTTPStatus.OK, answer_response(entry))

    def _handle_delete_answer(self, question_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_DELETE_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            self._story_bank_actor(query.get("actor"))
            deleted = story_bank.delete_answer(
                home_root=home_root, target=target, question_id=question_id,
                expected_revision=story_bank.revision_value(query.get("revision"), required=True),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": ANSWERS_SCHEMA, "deleted": deleted})

    # --- stories ---------------------------------------------------------------------------

    def _handle_get_stories(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_LIST_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            body = stories_response(home_root, target, q=query.get("q"), tag=query.get("tag"))
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_stories_prep(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(frozenset())
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            body = prep_response(home_root, target)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_story(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(frozenset())
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            story = stories.get_story(home_root=home_root, target=target, story_id=story_id)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        if story is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", f"there is no story {story_id!r}")
            return
        self._write_json(HTTPStatus.OK, story_response(story))

    def _handle_post_stories(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_STORY_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        story_id = body.get("story_id")
        if story_id is not None and not isinstance(story_id, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "story_id must be a string")
            return
        try:
            story = stories.save_story(
                home_root=home_root, target=target, story_id=story_id,
                fields={key: body[key] for key in stories.STORY_FIELDS if key in body},
                actor=self._story_bank_actor(body.get("actor")),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._pipeline_fire(pipeline_triggers.pending_story(home_root, target, story))
        self._write_json(HTTPStatus.CREATED, story_response(story))

    def _handle_put_story(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_STORY_PUT_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        try:
            story = stories.edit_story(
                home_root=home_root, target=target, story_id=story_id,
                fields={key: body[key] for key in stories.STORY_FIELDS if key in body},
                actor=self._story_bank_actor(body.get("actor")),
                expected_revision=story_bank.revision_value(body.get("revision"), required=True),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._pipeline_fire(pipeline_triggers.pending_story(home_root, target, story))
        self._write_json(HTTPStatus.OK, story_response(story))

    def _handle_delete_story(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_DELETE_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            self._story_bank_actor(query.get("actor"))
            deleted = stories.delete_story(
                home_root=home_root, target=target, story_id=story_id,
                expected_revision=story_bank.revision_value(query.get("revision"), required=True),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": STORIES_SCHEMA, "deleted": deleted})


__all__ = [
    "ACTOR_HEADER",
    "ANSWERS_SCHEMA",
    "ERROR_STATUS",
    "STORIES_SCHEMA",
    "StoryBankRoutesMixin",
    "answer_response",
    "answers_response",
    "error_extra",
    "prep_response",
    "stories_response",
    "story_response",
]
