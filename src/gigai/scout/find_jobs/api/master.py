"""0.1.10.9 master P5: the master resume's routes -- the Master page, the migration, the profiles' selections.

    GET  /api/master                    the master: every entry and line with its id, strength and who shows it
    GET  /api/master/history            the revisions (who, when, what changed) and what is retired
    POST /api/master/lines              add a line (under a role, or to Summary, Skills, Other)
    PUT  /api/master/lines              edit, retire or restore a line, by id
    POST /api/master/entries            add a role, a project or a school
    PUT  /api/master/entries            edit, retire or restore one, by id
    GET  /api/master/migration          what building the master from the profiles' resumes would do, and its questions
    POST /api/master/migration          build it, with the answers
    POST /api/master/sync               import master.md from the resumes folder (0.1.10.9 master P8), or write it
    GET  /api/master/selection          each profile's selection against the master ("3 new master lines: refresh?")
    POST /api/master/selection          refresh (select again) or sync (print again) one profile's selection
    PUT  /api/tailored-resumes/selection  Add or Remove one master line on a job's tailored resume

Every route is local and model-free (``master_store``, ``master_edit``,
``master_profiles``, ``tailor_selection_edit``).  The line and entry writes
call the functions ``gigai scout resume master add | edit | remove`` call
(``master_edit``), so the rules are the same for the page and for the agent:
a line the master already has in other words is asked about before anything
is written (``status: near_duplicate``; ``force`` adds it), a removed line is
retired and can be restored, and who wrote a line is kept.  The two PUT
routes also take ``note`` (0.1.11): the note of a line or an entry, a string
to set it and ``""`` to remove it; it is returned with every line and entry
and never printed in a resume.  This module
checks the shape of a body (``wrong_type``, ``invalid_value``) and maps the
refusals to a status.  The master belongs to the
user, not to a profile.  The reads return the user's text, so ``do_GET``
runs the Host check before them; the writes go through ``_check_csrf``.
Nothing here logs a line of the master.

Two writers share the master (the user in Scout, and the user's agent).  A
write says who it is with ``actor`` (the answers' rule,
``_story_bank_actor``: the body field or the ``X-GigAI-Actor`` header; with
neither, a browser page of this server is the operator and any other
loopback caller the agent) and names the ``revision`` it read: when the
master changed since, the reply is ``409 revision_conflict`` with the
revision it is at now (``error.current``).  Every write runs the contact
check and refuses with ``422 personal_info_refused``; every write ends with
``master_profiles_cli.after_master_write`` (``profiles`` in the reply).

A master that does not exist yet is a state, not an error: the reads answer
200 with ``master: null``.

P8: the master is also a file in the resumes folder (``master.md``), written
after every change.  ``GET /api/master`` says how that file stands (``file``:
``not_imported`` when the user changed it), every write says where its
revision went (``file``), and ``POST /api/master/sync`` is the explicit
import (``master_file.sync``): no route reads the file by itself.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
import subprocess
import threading

from ....private_records import PrivateRecordError
from ....workpad import WorkpadError, resolve_workpad
from ... import master_edit, master_file, master_profiles, profile_records, story_bank
from ...master_resume import MASTER_FORMAT, MasterResumeError, compare
from ...master_store import MasterStoreError, StoredMaster, load_master
from ...tailored_resume import TailorError
from .common import reads_committed

MASTER_SCHEMA = "scout-master:1"
MASTER_HISTORY_SCHEMA = "scout-master-history:1"
MASTER_MIGRATION_SCHEMA = "scout-master-migration:1"
MASTER_SELECTION_SCHEMA = "scout-master-selection:1"

LINE_USES: tuple[str, ...] = ("edit", "retire", "restore")
SELECTION_USES: tuple[str, ...] = ("refresh", "sync")

_GET_KEYS = frozenset({"revision"})
_SELECTION_GET_KEYS = frozenset({"profile_id"})
_LINE_POST_KEYS = frozenset({"revision", "actor", "text", "entry_id", "section", "tags", "backed", "force"})
_LINE_PUT_KEYS = frozenset({"revision", "actor", "id", "use", "text", "tags", "backed", "note"})
_ENTRY_POST_KEYS = frozenset({"revision", "actor", "section", "heading", "sublines"})
_ENTRY_PUT_KEYS = frozenset({"revision", "actor", "id", "use", "heading", "sublines", "note"})
_MIGRATION_POST_KEYS = frozenset({"answers", "revision", "actor"})
_SYNC_POST_KEYS = frozenset({"revision", "actor"})
_SELECTION_POST_KEYS = frozenset({"profile_id", "use", "dry_run"})
_JOB_SELECTION_KEYS = frozenset({"profile_id", "job_identity", "updated_at", "use", "item_id", "fit"})

ERROR_STATUS: dict[str, HTTPStatus] = {
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "master_not_found": HTTPStatus.NOT_FOUND,
    "master_line_not_found": HTTPStatus.NOT_FOUND,
    "master_item_not_found": HTTPStatus.NOT_FOUND,
    "master_entry_not_found": HTTPStatus.NOT_FOUND,
    "master_revision_not_found": HTTPStatus.NOT_FOUND,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "tailored_resume_not_found": HTTPStatus.NOT_FOUND,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unknown_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "revision_required": HTTPStatus.UNPROCESSABLE_ENTITY,
    "personal_info_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_text_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_tag_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_backed_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_note_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_place_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_edit_empty": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_edit_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_markdown_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_too_large": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_file_unreadable": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_file_binary": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_format_unsupported": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_contact_data": HTTPStatus.UNPROCESSABLE_ENTITY,
    "master_actor_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "migration_answer_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "migration_answer_unknown": HTTPStatus.UNPROCESSABLE_ENTITY,
    "selection_unavailable": HTTPStatus.UNPROCESSABLE_ENTITY,
    "selection_line_unsupported": HTTPStatus.UNPROCESSABLE_ENTITY,
    "revision_conflict": HTTPStatus.CONFLICT,
    "master_exists": HTTPStatus.CONFLICT,
    "master_line_exists": HTTPStatus.CONFLICT,
    "master_entry_exists": HTTPStatus.CONFLICT,
    "master_empty": HTTPStatus.CONFLICT,
    "master_file_changed": HTTPStatus.CONFLICT,
    "tailored_resume_changed": HTTPStatus.CONFLICT,
}
_ERRORS = (
    MasterStoreError, MasterResumeError, master_edit.MasterEditError, master_profiles.MasterProfileError, story_bank.StoryBankError,
    PrivateRecordError, WorkpadError, profile_records.ProfileRecordError,
)

# --- a new profile's first selection, made after the answer (see ``first_selection_later``) ------

_PENDING_LOCK = threading.Lock()
#: (home, target) -> the profiles whose first selection of the master is being made in the background.
_pending: dict[tuple[str, str], set[str]] = {}
_threads: list[threading.Thread] = []


def pending_first_selections(home_root: Path, target: Path) -> list[str]:
    with _PENDING_LOCK:
        return sorted(_pending.get((str(home_root), str(target)), ()))


def first_selection_later(home_root: Path, target: Path, profile_id: str, *, logger=None) -> threading.Thread:  # noqa: ANN001
    """Make a new profile's first selection of the master on a background thread; the thread is returned (tests join it).

    The selection reads the local index (2 to 3 s on a home with 290,000
    postings) and stores the view (4 s there): too long to hold ``POST
    /api/profiles`` for.  The profile is usable at once with the resume it
    was created with; when the selection lands its resume becomes its own
    view, exactly as a refresh does.  ``GET /api/master/selection`` lists the
    profile under ``pending`` meanwhile.  Best effort, as before: a selection
    that cannot be made leaves the profile as created.
    """

    key = (str(home_root), str(target))
    with _PENDING_LOCK:
        _pending.setdefault(key, set()).add(profile_id)

    def run() -> None:
        try:
            master_profiles.first_selection(home_root=home_root, target=target, profile_id=profile_id)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:  # every store, record and layout refusal is one of these
            if logger is not None:
                logger.info("master: the first selection of profile %s was not made (%s)", profile_id, getattr(exc, "code", type(exc).__name__))
        finally:
            with _PENDING_LOCK:
                _pending.get(key, set()).discard(profile_id)

    thread = threading.Thread(target=run, name=f"master-first-selection-{profile_id}", daemon=True)
    with _PENDING_LOCK:
        _threads[:] = [item for item in _threads if item.is_alive()] + [thread]
    thread.start()
    return thread


def wait_first_selections(timeout: float = 30.0) -> bool:
    """Wait for the first selections being made (a server that stops lets them finish their write); false when one is still running."""

    import time

    deadline = time.monotonic() + timeout
    with _PENDING_LOCK:
        running = list(_threads)
    for thread in running:
        thread.join(max(0.0, deadline - time.monotonic()))
    return not any(thread.is_alive() for thread in running)


# --- the bodies ---------------------------------------------------------------------------------


def revision_json(stored: StoredMaster) -> dict[str, object]:
    return {**stored.revision.to_json(), "record_id": stored.record_id, "revisions": stored.revisions}


def master_json(stored: StoredMaster, home_root: Path, target: Path) -> dict[str, object]:
    """The master as ``gigai scout resume master show --json`` prints it: the revision, then every entry and line,
    each with who wrote its text and where its evidence came from (null where that is not known)."""

    master = stored.master
    known = master_edit.provenance(home_root=home_root, target=target, master=master)
    return {
        **revision_json(stored), "format": MASTER_FORMAT, "sections": list(master.sections), "counts": master.counts(),
        "entries": [master_edit.entry_json(entry, known) for entry in master.entries.values()],
        "items": [master_edit.item_json(item, known) for item in master.items.values()],
    }


def _shown_by(home_root: Path, target: Path) -> tuple[list[dict[str, object]], dict[str, list[str]]]:
    """``(the profiles that hold a selection, line id -> the profiles whose selection shows it)``. Reads only."""

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        profiles = [item for item in profile_records.list_profiles(resolved) if item.state != "deleted" and item.master_selection is not None]
    except (WorkpadError, profile_records.ProfileRecordError, PrivateRecordError):
        return [], {}
    shown: dict[str, list[str]] = {}
    for profile in profiles:
        for item_id in profile.master_selection.item_ids:  # type: ignore[union-attr]
            shown.setdefault(item_id, []).append(profile.profile_id)
    return [{"profile_id": item.profile_id, "label": item.label, "state": item.state} for item in profiles], shown


def master_response(home_root: Path, target: Path, *, revision: int | None = None) -> dict[str, object]:
    """The ``GET /api/master`` body."""

    from ...tailor_master import stored_master

    current = stored_master(home_root, target)
    # P8: how master.md in the resumes folder stands (one small index file and the one file's digest; never its text).
    file = master_file.file_status(home_root, current)
    if current is None:
        return {"schema_version": MASTER_SCHEMA, "master": None, "current_revision": None, "profiles": [], "shown_by": {}, "file": file}
    stored = current
    if revision is not None and revision != current.revision.revision:
        older = load_master(home_root=home_root, target=target, revision=revision)
        assert older is not None
        stored = older
    profiles, shown = _shown_by(home_root, target)
    return {
        "schema_version": MASTER_SCHEMA, "master": master_json(stored, home_root, target), "current_revision": current.revision.revision,
        "profiles": profiles, "shown_by": shown, "file": file,
    }


def history_response(home_root: Path, target: Path) -> dict[str, object]:
    """The ``GET /api/master/history`` body: the revisions newest first, and what is retired."""

    chain = master_edit.revisions(home_root, target)
    entries: list[dict[str, object]] = []
    previous = None
    for revision, master in chain:
        entries.append({**revision.to_json(), "items": len(master.items), "entries": len(master.entries), **compare(previous, master).to_json()})
        previous = master
    return {
        "schema_version": MASTER_HISTORY_SCHEMA, "revision": chain[-1][0].revision if chain else None,
        "revisions": list(reversed(entries)),
        # What `master show --retired` lists, with what the page shows beside it; a line that left with its entry is under the entry.
        "retired": [
            {**gone.to_json(), "what": gone.what, "entry_heading": gone.entry_heading, "sublines": list(gone.sublines)}
            for gone in master_edit.retired(chain)
        ],
    }


def selection_response(home_root: Path, target: Path, *, profile_id: str | None = None) -> dict[str, object]:
    """The ``GET /api/master/selection`` body."""

    from ...tailor_master import stored_master

    stored = stored_master(home_root, target)
    pending = pending_first_selections(home_root, target)
    if stored is None:
        return {"schema_version": MASTER_SELECTION_SCHEMA, "master": None, "profiles": [], "pending": pending}
    statuses = master_profiles.selection_statuses(home_root=home_root, target=target, profile_id=profile_id)
    return {
        "schema_version": MASTER_SELECTION_SCHEMA, "master": {**revision_json(stored), "counts": stored.master.counts()},
        "profiles": [{**status.to_json(), "pending": status.profile_id in pending} for status in statuses], "pending": pending,
    }


def _revision(value: object) -> int:
    if type(value) is int and value >= 0:
        return value  # type: ignore[return-value]
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise master_edit.MasterEditError("invalid_value", "revision is required: the revision of the master you read (a whole number)")


def _string(body: dict[str, object], key: str, *, required: bool = False) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise master_edit.MasterEditError("wrong_type", f"{key} must be a string")
    return value


def _strings(body: dict[str, object], key: str) -> list[str] | None:
    value = body.get(key)
    if value is None:
        return None
    if type(value) is not list or any(type(item) is not str for item in value):
        raise master_edit.MasterEditError("wrong_type", f"{key} must be an array of strings")
    return value  # type: ignore[return-value]


class MasterRoutesMixin:
    """``Handler`` mixin: ``/api/master...`` and ``PUT /api/tailored-resumes/selection``."""

    def _master_fail(self, exc: Exception) -> None:
        code = getattr(exc, "code", "invalid_value")
        status = ERROR_STATUS.get(code, HTTPStatus.CONFLICT)
        current = getattr(exc, "current", None)
        if current is not None:
            self._error_with_extra(status, code, str(exc), {"current": current.to_json()})
            return
        self._error(status, code, str(exc))

    def _master_write(self, write, paths: tuple[Path, Path], *, created: bool = False) -> None:  # noqa: ANN001 - a master_edit.MasterEdit
        """The reply of a write: 201 for an add that was written; 200 otherwise (also when it was asked, or unchanged)."""

        status = HTTPStatus.CREATED if created and write.written else HTTPStatus.OK
        self._write_json(status, {"schema_version": MASTER_SCHEMA, **write.to_json(), "master": master_json(write.stored, *paths)})

    # --- reads ---------------------------------------------------------------------------------

    @reads_committed
    def _handle_get_master(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_GET_KEYS)
        if paths is None or query is None:
            return
        try:
            revision = _revision(query["revision"]) if query.get("revision") else None
            body = master_response(*paths, revision=revision)
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    @reads_committed
    def _handle_get_master_history(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(frozenset())
        if paths is None or query is None:
            return
        try:
            body = history_response(*paths)
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_master_selection(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(_SELECTION_GET_KEYS)
        if paths is None or query is None:
            return
        try:
            # Not under the read cache as a whole: resolving the profiles migrates the default one on a home that has none yet.
            body = selection_response(*paths, profile_id=query.get("profile_id") or None)
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_master_migration(self) -> None:
        paths = self._story_bank_paths()
        query = None if paths is None else self._story_bank_query(frozenset())
        if paths is None or query is None:
            return
        self._migration(paths, answers={}, revision=None, actor="operator", dry_run=True)

    # --- the migration -------------------------------------------------------------------------

    def _migration(self, paths: tuple[Path, Path], *, answers: dict[str, str], revision: int | None, actor: str, dry_run: bool) -> None:
        from ...master_profiles_cli import after_master_write, migration_payload

        home_root, target = paths
        try:
            result = master_profiles.migrate(home_root=home_root, target=target, answers=answers, actor=actor, revision=revision, dry_run=dry_run)
        except master_profiles.MasterProfileError as exc:
            if dry_run and exc.code in ("migration_no_profiles", "migration_resume_unreadable"):
                # Asked "what would it do": that it cannot is the answer, with why.
                self._write_json(HTTPStatus.OK, {
                    "schema_version": MASTER_MIGRATION_SCHEMA, "ok": True, "mode": "migration", "status": "blocked", "written": False,
                    "master": None, "migration": None, "questions": [], "profiles": [], "contact_removed": None, "file": None,
                    "blocked": {"code": exc.code, "message": str(exc)},
                })
                return
            self._master_fail(exc)
            return
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        payload = {"schema_version": MASTER_MIGRATION_SCHEMA, **migration_payload(result), "blocked": None}
        if dry_run and result.status == "dry_run":
            payload["status"] = "ready"
        wrote = not dry_run and result.written is not None and result.status in ("created", "revised")
        if wrote:
            payload["after"] = after_master_write(home_root, target)
        self._write_json(HTTPStatus.CREATED if wrote else HTTPStatus.OK, payload)

    def _handle_post_master_migration(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_MIGRATION_POST_KEYS)
        if paths is None or body is None:
            return
        answers = body.get("answers", {})
        if type(answers) is not dict or any(type(value) is not str for value in answers.values()):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "answers must be an object: {question_id: \"a\" | \"b\" | \"both\"}")
            return
        try:
            actor = self._story_bank_actor(body.get("actor"))
            revision = _revision(body["revision"]) if body.get("revision") is not None else None
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._migration(paths, answers={key: value.strip().lower() for key, value in answers.items()}, revision=revision, actor=actor, dry_run=False)

    # --- the visible file (P8) -------------------------------------------------------------------

    def _handle_post_master_sync(self) -> None:
        """``POST /api/master/sync``: import ``master.md`` from the resumes folder as the next revision, or write it when it is missing."""

        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_SYNC_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        try:
            revision = _revision(body["revision"]) if body.get("revision") is not None else None
            try:
                result = master_file.sync(home_root=home_root, target=target, actor=self._story_bank_actor(body.get("actor")), revision=revision)
            except MasterStoreError as exc:
                if exc.code != "master_contact_data":
                    raise
                # The code every other write of the master refuses contact data with.
                raise master_edit.MasterEditError("personal_info_refused", str(exc)) from None
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {"schema_version": MASTER_SCHEMA, **result.to_json(), "master": master_json(result.stored, *paths)})

    # --- lines and entries -----------------------------------------------------------------------

    def _handle_post_master_lines(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_LINE_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        try:
            section, force = _string(body, "section"), body.get("force", False)
            if type(force) is not bool:
                raise master_edit.MasterEditError("wrong_type", "force must be true or false")
            write = master_edit.add_line(
                home_root=home_root, target=target, revision=_revision(body.get("revision")), actor=self._story_bank_actor(body.get("actor")),
                text=_string(body, "text", required=True) or "", entry_id=_string(body, "entry_id"),
                section=section.strip().lower() if section is not None else None,
                tags=_strings(body, "tags") or (), backed=_strings(body, "backed") or (), force=force,
            )
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._master_write(write, paths, created=True)

    def _master_use(self, body: dict[str, object]) -> tuple[str, str]:
        item_id, use = body.get("id"), body.get("use", "edit")
        if not isinstance(item_id, str) or not item_id.strip():
            raise master_edit.MasterEditError("invalid_value", "id is required: the id of the line or entry (GET /api/master)")
        if use not in LINE_USES:
            raise master_edit.MasterEditError("invalid_value", "use must be edit, retire or restore")
        return item_id.strip(), str(use)

    def _master_put(self, keys: frozenset[str], fields: tuple[str, ...]) -> None:
        """``PUT /api/master/lines`` and ``/entries``: edit (``fields``), retire or restore one, by id."""

        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(keys)
        if paths is None or body is None:
            return
        home_root, target = paths
        try:
            item_id, use = self._master_use(body)
            common = {"home_root": home_root, "target": target, "revision": _revision(body.get("revision")), "actor": self._story_bank_actor(body.get("actor"))}
            if use == "edit":
                given: dict[str, object] = {key: _string(body, key) if key in ("text", "heading", "note") else _strings(body, key) for key in fields}
                note = given.pop("note")
                if isinstance(note, str) and not note.strip():
                    given["clear_note"] = True  # "" removes the note, as an empty tags array removes the tags
                else:
                    given["note"] = note
                write = master_edit.edit(item_id=item_id, **given, **common)  # type: ignore[arg-type]
            elif any(body.get(key) is not None for key in fields):
                raise master_edit.MasterEditError("invalid_value", f"use {use} takes no {', '.join(fields[:-1])} or {fields[-1]}")
            elif use == "retire":
                write = master_edit.remove(item_id=item_id, **common)  # type: ignore[arg-type]
            else:
                write = master_edit.restore(item_id=item_id, **common)  # type: ignore[arg-type]
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._master_write(write, paths)

    def _handle_put_master_lines(self) -> None:
        self._master_put(_LINE_PUT_KEYS, ("text", "tags", "backed", "note"))

    def _handle_post_master_entries(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_ENTRY_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        try:
            section = _string(body, "section", required=True) or ""
            write = master_edit.add_entry(
                home_root=home_root, target=target, revision=_revision(body.get("revision")), actor=self._story_bank_actor(body.get("actor")),
                section=section.strip().lower(), heading=_string(body, "heading", required=True) or "", sublines=_strings(body, "sublines") or (),
            )
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._master_write(write, paths, created=True)

    def _handle_put_master_entries(self) -> None:
        self._master_put(_ENTRY_PUT_KEYS, ("heading", "sublines", "note"))

    # --- the profiles' selections ------------------------------------------------------------------

    def _handle_post_master_selection(self) -> None:
        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_SELECTION_POST_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        profile_id, use, dry_run = body.get("profile_id"), body.get("use", "refresh"), body.get("dry_run", False)
        if not isinstance(profile_id, str) or not profile_id.strip() or use not in SELECTION_USES or type(dry_run) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id is required; use is refresh or sync; dry_run is true or false")
            return
        if use == "sync" and dry_run:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "use sync writes; it has no dry_run (see GET /api/master/selection)")
            return
        try:
            if use == "sync":
                changes = master_profiles.sync_views(home_root=home_root, target=target, profile_id=profile_id.strip())
            else:
                changes = [master_profiles.refresh_selection(home_root=home_root, target=target, profile_id=profile_id.strip(), dry_run=dry_run)]
            after = selection_response(home_root, target, profile_id=profile_id.strip())
        except _ERRORS as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {**after, "use": use, "dry_run": dry_run, "changes": [change.to_json() for change in changes]})

    # --- one job's selection -------------------------------------------------------------------------

    def _handle_put_tailored_resume_selection(self) -> None:
        """``PUT /api/tailored-resumes/selection``: Add or Remove one master line on a job's tailored resume."""

        from ...tailor_selection_edit import FIT_ASK, FITS, SELECTION_USES as JOB_USES, change_stored_selection

        paths = self._story_bank_paths()
        body = None if paths is None else self._story_bank_body(_JOB_SELECTION_KEYS)
        if paths is None or body is None:
            return
        home_root, target = paths
        # 0.1.11.9: a job has ONE resume. ``profile_id`` is still taken (callers of before send it) and picks nothing.
        required = ("job_identity", "updated_at", "use", "item_id")
        if not all(isinstance(body.get(key), str) and body[key] for key in required) or not isinstance(body.get("profile_id"), str | None):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "job_identity, updated_at, use and item_id are required, each a non-empty string")
            return
        fit = body.get("fit", FIT_ASK)
        if body["use"] not in JOB_USES or fit not in FITS:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "use must be add or remove; fit must be ask, cut or keep")
            return
        try:
            edit = change_stored_selection(
                home_root, target, profile_id=body.get("profile_id") or None, job_identity=body["job_identity"], use=body["use"],  # type: ignore[arg-type]
                item_id=body["item_id"], fit=fit, updated_at=body["updated_at"],  # type: ignore[arg-type]
            )
        except TailorError as exc:
            self._master_fail(exc)
            return
        self._write_json(HTTPStatus.OK, {**edit.response.to_json(), "selection_change": edit.to_json()})


__all__ = [
    "MASTER_HISTORY_SCHEMA",
    "MASTER_MIGRATION_SCHEMA",
    "MASTER_SCHEMA",
    "MASTER_SELECTION_SCHEMA",
    "MasterRoutesMixin",
    "first_selection_later",
    "history_response",
    "master_json",
    "master_response",
    "pending_first_selections",
    "selection_response",
    "wait_first_selections",
]
