"""0110-034: the story bank routes -- a profile's answered questions and stories, kept and reused.

    GET    /api/story-bank             the bank a profile sees (own entries, then the shared
                                       profile's), its tags and its sharing setting; ``q`` and
                                       ``tag`` narrow the list
    GET    /api/story-bank/match       the near match for one question ("We already know: ...")
    GET    /api/story-bank/{id}        one entry
    POST   /api/story-bank             add a new entry: a story, or an answer nobody asked yet
    PUT    /api/story-bank/sharing     which other profile's bank this profile also reads
    PUT    /api/story-bank/{id}        edit an own entry: answer, question words and/or tag
    DELETE /api/story-bank/{id}        remove an own entry

``{id}`` is the entry's ``question_id`` (``cloud:gcp``, ``story:led_migration``).

Every route is local and model-free (``gigai.scout.story_bank``). ``profile_id`` is optional
everywhere: omitted, the gig's selected profile is used. The reads return answers, so
``do_GET`` runs the Host check before them; the writes go through ``_check_csrf``. Nothing
here logs an answer.

Two writers (the user and an agent) share the bank. A write says who it is with ``actor``
(``operator``, the default, or ``agent``; the body field, or the ``X-GigAI-Actor`` header),
and ``PUT``/``DELETE`` name the ``updated_at`` of the entry they read: when the entry changed
since, the answer is ``409 story_bank_changed`` with the current ``entry`` in the error (the
``PUT /api/tailored-resumes/lines`` pattern). Every write runs the personal-info check.

``known_names`` is the one place the saved PDF-header name is read for the personal-info
check; ``api/answers.py`` and the CLI use it too, so ``story_bank.py`` itself never imports
the display settings.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ....private_records import PrivateRecordError
from ... import story_bank
from ...resume_display import load_display
from ..assess_contracts import AssessResumeInput
from ..contracts import FindJobsContractError

RESPONSE_SCHEMA = "scout-story-bank-response:1"

ACTOR_HEADER = "X-GigAI-Actor"

_GET_KEYS = frozenset({"profile_id", "q", "tag"})
_ENTRY_KEYS = frozenset({"profile_id"})
_MATCH_KEYS = frozenset({"profile_id", "question_id", "question"})
_POST_KEYS = frozenset({"profile_id", "question_id", "question", "answer", "tag", "actor"})
_PUT_KEYS = frozenset({"profile_id", "answer", "question", "tag", "updated_at", "actor"})
_SHARING_KEYS = frozenset({"profile_id", "share_with", "actor"})
_DELETE_KEYS = frozenset({"profile_id", "updated_at", "actor"})

_ERROR_STATUS: dict[str, HTTPStatus] = {
    "not_found": HTTPStatus.NOT_FOUND,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "profile_unavailable": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "personal_info_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "answer_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "story_bank_changed": HTTPStatus.CONFLICT,
    "story_exists": HTTPStatus.CONFLICT,
}


def _match_story_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{story_id}`` from ``/api/story-bank/{story_id}<suffix>``.

    The same shape as ``common._match_run_id`` / ``profiles._match_profile_id``
    (``server.py``'s dispatch and ``tests/api_e2e/route_inventory.py``'s scanner
    recognize the name). The id is percent-decoded; no embedded slash.
    """

    from urllib.parse import unquote

    prefix = "/api/story-bank/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        remainder = remainder[: -len(suffix)]
    if not remainder or "/" in remainder:
        return None
    return unquote(remainder)


def known_names(home_root: Path | None) -> tuple[str, ...]:
    """The name saved for the PDF header, for the local personal-info check; ``()`` when none."""

    if home_root is None:
        return ()
    try:
        settings = load_display(home_root)
    except Exception:  # noqa: BLE001 - no readable settings means no known name
        return ()
    return (settings.name,) if settings is not None and settings.name.strip() else ()


def resolve_profile_id(home_root: Path, target: Path, profile_id: str | None) -> str:
    """``profile_id`` when it is a profile of this gig, else the selected profile's id.

    Raises ``StoryBankError``: ``profile_not_found`` / ``profile_unavailable`` / ``target_unavailable``.
    """

    from ....workpad import resolve_workpad
    from ..resume_input import resolve_profile

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:  # noqa: BLE001 - any failure to resolve the gig is one typed refusal
        raise story_bank.StoryBankError("target_unavailable", "no Scout gig is available for this folder") from exc
    try:
        profile = resolve_profile(AssessResumeInput(profile_id=profile_id or None), resolved=resolved, home_root=home_root, target=target)
    except FindJobsContractError as exc:
        raise story_bank.StoryBankError(exc.code, str(exc)) from exc
    assert profile is not None
    return profile.profile_id


def bank_response(home_root: Path, target: Path, profile_id: str, *, q: str | None = None, tag: str | None = None, question_id: str | None = None) -> dict[str, object]:
    """The ``GET /api/story-bank`` body (also what ``gigai scout story-bank list --json`` prints)."""

    from ...question_ids import normalize_question_id

    entries = story_bank.read_bank(home_root=home_root, target=target, profile_id=profile_id)
    tags = sorted({entry.tag for entry in entries})
    total = len(entries)
    if question_id:
        wanted = normalize_question_id(question_id)
        entries = tuple(entry for entry in entries if entry.question_id == wanted)
    if tag:
        entries = tuple(entry for entry in entries if entry.tag == tag.strip().lower())
    if q and q.strip():
        needle = q.strip().casefold()
        entries = tuple(
            entry
            for entry in entries
            if needle in entry.question_id.casefold() or needle in entry.question.casefold() or needle in entry.answer.casefold() or needle in entry.tag
        )
    return {
        "schema_version": RESPONSE_SCHEMA,
        "profile_id": profile_id,
        "entries": [entry.to_json() for entry in entries],
        "total": total,
        "tags": tags,
        "sharing": story_bank.sharing(home_root=home_root, target=target, profile_id=profile_id),
    }


class StoryBankRoutesMixin:
    """``Handler`` mixin: the ``/api/story-bank`` routes."""

    def _story_bank_fail(self, exc: Exception) -> None:
        code = getattr(exc, "code", "invalid_value")
        status = _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)
        entry = getattr(exc, "entry", None)
        if entry is not None:
            # A stale or duplicate write: the caller gets the entry as it is now.
            self._error_with_extra(status, code, str(exc), {"entry": entry.to_json()})
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
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}; allowed: {', '.join(sorted(allowed))}")
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
        for key, value in body.items():
            if value is not None and not isinstance(value, str):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", f"{key} must be a string")
                return None
        return body

    def _story_bank_actor(self, given: object) -> str:
        """``actor`` from the body/query, else the ``X-GigAI-Actor`` header, else ``operator``."""

        return story_bank.actor_value(given if isinstance(given, str) and given.strip() else self.headers.get(ACTOR_HEADER))

    def _handle_get_story_bank(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_GET_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            profile_id = resolve_profile_id(home_root, target, query.get("profile_id"))
            body = bank_response(home_root, target, profile_id, q=query.get("q"), tag=query.get("tag"))
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_story_bank_entry(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_ENTRY_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        try:
            profile_id = resolve_profile_id(home_root, target, query.get("profile_id"))
            body = bank_response(home_root, target, profile_id, question_id=story_id)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        entries = body["entries"]
        if not entries:
            self._error(HTTPStatus.NOT_FOUND, "not_found", f"this profile's story bank has no entry {story_id!r}")
            return
        self._write_json(HTTPStatus.OK, {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "entry": entries[0]})  # type: ignore[index]

    def _handle_get_story_bank_match(self) -> None:
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
            profile_id = resolve_profile_id(home_root, target, query.get("profile_id"))
            entries = story_bank.read_bank(home_root=home_root, target=target, profile_id=profile_id, with_postings=False)
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        match = story_bank.near_match(entries, question_id=question_id, question=query.get("question") or "")
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "question_id": question_id, "match": None if match is None else match.to_json()},
        )

    def _handle_post_story_bank(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        question, answer = body.get("question"), body.get("answer")
        if not isinstance(question, str) or not question.strip() or not isinstance(answer, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "question (what the story answers) and answer (the story) are required strings")
            return
        try:
            profile_id = resolve_profile_id(home_root, target, body.get("profile_id"))  # type: ignore[arg-type]
            entry = story_bank.add_story(
                home_root=home_root, target=target, profile_id=profile_id, question=question, answer=answer,
                question_id=body.get("question_id"), tag=body.get("tag"),  # type: ignore[arg-type]
                names=known_names(home_root), actor=self._story_bank_actor(body.get("actor")),
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.CREATED, {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "entry": entry.to_json()})

    def _handle_put_story_bank_entry(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_PUT_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        updated_at = body.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at.strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "updated_at is required: the updated_at of the entry you read")
            return
        try:
            profile_id = resolve_profile_id(home_root, target, body.get("profile_id"))  # type: ignore[arg-type]
            entry = story_bank.edit_entry(
                home_root=home_root, target=target, profile_id=profile_id, question_id=story_id,
                answer=body.get("answer"), question=body.get("question"), tag=body.get("tag"),  # type: ignore[arg-type]
                names=known_names(home_root), actor=self._story_bank_actor(body.get("actor")), expected_updated_at=updated_at,
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "entry": entry.to_json()})

    def _handle_put_story_bank_sharing(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_SHARING_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        if "share_with" not in body:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "share_with is required: a profile id, or null to read only this profile's own bank")
            return
        try:
            profile_id = resolve_profile_id(home_root, target, body.get("profile_id"))  # type: ignore[arg-type]
            result = story_bank.set_sharing(home_root=home_root, target=target, profile_id=profile_id, share_with=body["share_with"] or None)  # type: ignore[arg-type]
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "sharing": result})

    def _handle_delete_story_bank_entry(self, story_id: str) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_DELETE_KEYS)
        if paths is None or query is None:
            return
        home_root, target = paths
        updated_at = (query.get("updated_at") or "").strip()
        if not updated_at:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "updated_at is required: the updated_at of the entry you read")
            return
        try:
            self._story_bank_actor(query.get("actor"))
            profile_id = resolve_profile_id(home_root, target, query.get("profile_id"))
            deleted = story_bank.delete_entry(
                home_root=home_root, target=target, profile_id=profile_id, question_id=story_id, expected_updated_at=updated_at
            )
        except (story_bank.StoryBankError, PrivateRecordError) as exc:
            self._story_bank_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": RESPONSE_SCHEMA, "profile_id": profile_id, "deleted": deleted})


__all__ = ["ACTOR_HEADER", "RESPONSE_SCHEMA", "StoryBankRoutesMixin", "bank_response", "known_names", "resolve_profile_id"]
