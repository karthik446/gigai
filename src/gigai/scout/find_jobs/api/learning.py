"""0.1.11.10 Part A: the learning-pathway routes (the store is ``scout/learning_store.py``).

* ``GET /api/learning/pathways``: every pathway the user asked for or imported, the newest first.
* ``GET /api/learning/pathways/{pathway_id}``: one, with its whole record.
* ``GET`` / ``HEAD /learning/{pathway_id}/{path}``: one file of a ``done`` pathway's course (``index.html`` when no
  path is given). Not an ``/api/`` route: it answers the file's bytes, never JSON (an error is the usual JSON body).

All three only read files of the store. No model call, no network, no journal read.

0.1.11.10 Part B (G5), the GENERATION of a course (the job is ``scout/learning_job.py``):

* ``POST /api/learning/pathways`` ``{"role_text"}``: what generating a course for the role would take (calls, page
  fetches, minutes, the caps) and NOTHING is started. With ``"approve": true`` the request is stored (``queued``),
  the job starts on a thread of the server and the answer is ``202`` with the record; ``409 learning_running``
  while a course of this project is being generated.
* ``POST /api/learning/pathways/{pathway_id}/cancel``: ask the job to stop (no further model call or fetch).
* ``POST /api/learning/pathways/{pathway_id}/resume``: go on with a ``failed`` or ``interrupted`` job (``202``).

The two reads then also say how far a generation is: ``status`` may read ``interrupted`` (a job no live process
runs), with ``progress`` (steps, calls, fetches, caps, what was dropped) and ``error_code``. They still only read
files. No route here answers posting text or resume text.

A COURSE PAGE IS NOT SCOUT'S PAGE. It was generated from postings and web pages (untrusted text) and runs its own
scripts (diagrams, code highlighting), so every file of a course is sent with ``COURSE_CSP``:

* ``sandbox`` WITHOUT ``allow-same-origin``: the page gets an origin of its own that matches nothing. It cannot read
  Scout's API (a request from it is cross-origin and no route answers with CORS headers), nor Scout's storage or
  cookies, nor the page that opened it;
* ``connect-src 'none'``: its scripts cannot send a request at all (``fetch``, ``XMLHttpRequest``, a socket, a beacon);
* ``default-src 'none'`` with scripts, styles, images and fonts from this server only; no frame, no plugin, no
  form, no ``<base>``; only a page of this server may frame it;
* ``script-src 'self'`` has no ``'unsafe-inline'`` and no ``'unsafe-eval'``: an inline ``<script>`` block, an
  ``onclick=`` attribute and ``eval`` do not run. Styles may be inline (a diagram library sets them).

The file itself passes ``learning_store.resolve_course_file``: only a ``done`` pathway, only a plain file of an
allowed kind inside the course's own folder, never through a link, never a dotfile, never ``..``. Anything else is
404, the same answer as an unknown pathway. The request passes the server's own guards first (a loopback peer, and
a ``Host`` that is this server: ``do_GET`` / ``do_HEAD``).

0.1.11.10 SHIPPED LESSONS (slice 3), the courses that ship inside GigAI (``scout/learning_shipped_store.py``):

* ``GET /api/learning/pathways`` lists them FIRST, one row each with ``source: "shipped"`` (:func:`shipped_summary`);
  ``GET /api/learning/pathways/{role key}`` answers that row. A shipped course is not generated here: the cancel and
  resume routes answer 404 for a role key, as for an unknown pathway.
* ``GET`` / ``HEAD /learning/shipped/{role key}/pathway.html``: the ONE page of a shipped course this server serves,
  the user's own: the locally built file when there is one, else a plain page saying it is not built yet. The
  lessons and practice pages are on the public learning center (the catalog's ``site``): the row's ``url`` is the
  address of the course's pathway there, for the user's browser to open (this server never fetches it), and every
  other ``/learning/shipped/...`` path answers 404.
* ``GET`` / ``HEAD /learning/_assets/{file}``: the shared assets of the package, by an exact whitelist (the listing
  of the package's assets folder).

Every one of these files is sent with the SAME ``COURSE_HEADERS`` as a generated course's. A role key is never a
pathway id and ``_assets`` and ``shipped`` are not ids, so no address means two things.

0.1.11.10 SHIPPED LESSONS (slices 4b and 5b), the one page of a shipped course that is the user's own
(``scout/learning_pathway_build.py``):

* ``POST /api/learning/shipped/{role_key}/pathway`` ``{}``: what building that page would take and send (the
  estimate of the one model call that compares the course with the master resume) and NOTHING is sent or started.
  With ``"approve": true`` the build starts on a thread of the server and the answer is ``202`` with the course's
  row; ``409 learning_running`` while a course is generated or a page is built for this project.
* THE RESUME IS ALWAYS THE REFERENCE: there is no build without the comparison and no key that turns it off.
  ``approve`` is the usual consent to a model call. With no master resume line there is nothing to send, and the
  build makes no call.
* The row's ``local_page`` (:func:`local_page`) says ``not_built`` / ``built`` / ``stale`` with ``reasons``, and
  ``build``: a build that runs now, or the plain line of the last one that did not finish. This file reads no
  resume text: the state comes from small files and the master's revision id.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ... import learning_shipped_store, learning_store
from ...learning_shipped_store import ShippedCourse
from ...learning_store import LearningError, Pathway

LIST_SCHEMA = "scout-learning-pathways-response:1"
ONE_SCHEMA = "scout-learning-pathway-response:1"
COURSE_PREFIX = "/learning/"
#: The pages of a shipped course and the assets every shipped course shares (neither name is a pathway id).
SHIPPED_PREFIX = "/learning/shipped/"
ASSETS_PREFIX = "/learning/_assets/"
COURSE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"
)
#: Sent with every file of a course, whatever its kind.
COURSE_HEADERS: dict[str, str] = {
    "Content-Security-Policy": COURSE_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_COURSE_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
_PLAIN_TEXT = "text/plain; charset=utf-8"


def course_content_type(name: str) -> str:
    """The Content-Type of a course file, by its extension; a file with none (a licence notice) is plain text."""

    return _COURSE_CONTENT_TYPES.get(Path(name).suffix.lower(), _PLAIN_TEXT)


def course_url(pathway: Pathway) -> str | None:
    """Where the Scout server serves the pathway's course; ``None`` until it is ``done``."""

    if pathway.status != "done" or pathway.course is None:
        return None
    return f"{COURSE_PREFIX}{pathway.id}/{pathway.course.entry}"


def pathway_summary(pathway: Pathway, *, status: str | None = None) -> dict[str, object]:
    """One row of ``GET /api/learning/pathways``. ``status``: what a reader sees (``learning_job.live_status``), else as stored."""

    return {
        "id": pathway.id,
        "role_text": pathway.role_text,
        "requested_at": pathway.requested_at,
        "status": status or pathway.status,
        "cost": dict(pathway.to_json()["cost"]),  # type: ignore[call-overload]
        "course": pathway.course.to_json() if pathway.course is not None else None,
        "url": course_url(pathway),
        "source": pathway.source,
        "imported_from": pathway.imported_from,
        "error": pathway.error,
        "error_code": pathway.error_code,
        "progress": pathway.to_json().get("progress"),
    }


def _live_status(home_root: Path, target: Path, pathway: Pathway) -> str:
    """``learning_job.live_status``: file reads only (an owner file, a file's age). Only a record a job may own is asked about."""

    if pathway.status not in ("queued", "running") or pathway.source != "requested":
        return pathway.status
    from ... import learning_job

    return learning_job.live_status(home_root, target, pathway)


def local_page(home_root: Path, target: Path, role_key: str, *, learning: Path | None = None) -> dict[str, object]:
    """``{"state", "built_at", "reasons", "build", "url"}`` of a shipped course's locally built pathway page.

    ``state`` / ``reasons`` follow ``learning_pathway_local.state``'s rule. Asked from a list it is cheap: the
    record of the build (one small file) says the GigAI version, the catalog digest and the master revision the
    page was built from. ``stale`` is said for another version or another catalog, and with the reason ``master``
    when the master resume is at another revision than the one the page was compared with, or when a page built
    with no resume now has one to be compared with. Of the master only its revision chain is read (ids, no resume
    text; an answer of this process up to ``learning_pathway_build.MASTER_REF_SECONDS`` old is reused). The
    postings are NOT counted here (that is the build itself, seconds): a page is not said to be stale for new
    postings by this read.

    ``build``: ``learning_pathway_build.build_state``: null, or ``{"status": "running", "started_at",
    "detail"}`` while a build of this page runs, or ``{"status": "failed", "error_code", "error",
    "finished_at"}`` after one that did not finish (the page that was there stays).
    """

    from ... import learning_pathway_build, learning_pathway_local

    master: Any = learning_pathway_local.NOT_GIVEN
    record = None
    try:
        folder: Path | None = Path(learning) if learning is not None else learning_store.learning_dir(home_root, target)
    except LearningError:
        folder = None
    if folder is not None:
        record = learning_pathway_local.read_local_record(folder, role_key)
        if record is not None:
            master = learning_pathway_build.current_master_ref(home_root, target, max_age=learning_pathway_build.MASTER_REF_SECONDS)
    found = learning_pathway_local.state(home_root, target, role_key, master=master, learning=folder)
    state, reasons = found["state"], list(found["reasons"])  # type: ignore[call-overload]
    if state != learning_shipped_store.PAGE_NOT_BUILT and record is not None and record.get("master") is None and isinstance(master, dict):
        # Built when there was no resume to compare with; there is one now.
        state, reasons = learning_shipped_store.PAGE_STALE, [*reasons, learning_pathway_local.STALE_MASTER]
    return {
        "state": state, "built_at": found["built_at"], "reasons": reasons,
        "build": learning_pathway_build.build_state(folder, role_key) if folder is not None else None,
        "url": learning_shipped_store.local_page_url(role_key),
    }


def shipped_summary(home_root: Path, target: Path, course: ShippedCourse, *, learning: Path | None = None) -> dict[str, object]:
    """The row of a course that ships inside GigAI: the keys of :func:`pathway_summary` plus ``label`` and ``local_page``.

    ``id`` is the role key. It is always ``done`` (its lessons are on the learning center), nobody asked for it
    (``requested_at`` null) and making it cost this user nothing (``cost`` null). ``local_page`` is the state of
    the locally built pathway page (:func:`local_page`): ``not_built``, ``built`` or ``stale`` with ``built_at``
    and the ``reasons`` it is stale for, the ``build`` that runs or failed, and ``url``, where it is served.
    The row's own ``url`` is the course's pathway on the learning center (``learning_shipped_store.public_url``),
    or ``None`` for a catalog that names no site; ``course.entry`` is ``None``: GigAI holds no pages of it.
    ``learning``: the project's learning folder when the caller already named it (one ``git`` process per naming).
    """

    key = course.role_key
    return {
        "id": key,
        "role_text": course.role,
        "requested_at": None,
        "status": "done",
        "cost": None,
        "course": {"entry": None, "lessons": course.lessons, "size_bytes": None, "generated_at": None},
        "url": learning_shipped_store.public_url(course),
        "source": learning_shipped_store.SOURCE_SHIPPED,
        "imported_from": None,
        "error": None,
        "error_code": None,
        "progress": None,
        "label": learning_shipped_store.SHIPPED_LABEL,
        "local_page": local_page(home_root, target, key, learning=learning),
    }


def shipped_response(home_root: Path, target: Path, course: ShippedCourse) -> dict[str, object]:
    """The ``GET /api/learning/pathways/{role key}`` body: the list's row (a shipped course has no stored record)."""

    return {"schema_version": ONE_SCHEMA, "pathway": shipped_summary(home_root, target, course)}


def pathways_response(home_root: Path, target: Path) -> dict[str, object]:
    """The ``GET /api/learning/pathways`` body (also what ``gigai scout learning list --json`` prints).

    The courses shipped with GigAI first (by role), then the user's own, the newest request first.
    """

    own = [
        pathway_summary(pathway, status=_live_status(home_root, target, pathway))
        for pathway in learning_store.list_pathways(home_root, target)
    ]
    shipped = learning_shipped_store.list_courses()
    # The project is named ONCE for every shipped row (and not at all when nothing ships).
    learning = learning_store.learning_dir(home_root, target) if shipped else None
    rows = [shipped_summary(home_root, target, course, learning=learning) for course in shipped] + own
    return {"schema_version": LIST_SCHEMA, "pathways": rows, "total": len(rows)}


def pathway_response(pathway: Pathway, *, status: str | None = None) -> dict[str, object]:
    """The ``GET /api/learning/pathways/{pathway_id}`` body (also ``gigai scout learning show --json``)."""

    record = pathway.to_json()
    return {
        "schema_version": ONE_SCHEMA,
        "pathway": {
            **record, "status": status or pathway.status, "error_code": pathway.error_code, "progress": record.get("progress"),
            "url": course_url(pathway),
        },
    }


def live_pathway_response(home_root: Path, target: Path, pathway: Pathway) -> dict[str, object]:
    """:func:`pathway_response` with the status a reader sees."""

    return pathway_response(pathway, status=_live_status(home_root, target, pathway))


_GENERATE_KEYS = frozenset({"role_text", "approve"})
_JOB_ERROR_STATUS = {
    "learning_running": HTTPStatus.CONFLICT,
    "not_resumable": HTTPStatus.CONFLICT,
    "pathway_not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
}
SHIPPED_API_PREFIX = "/api/learning/shipped/"
_BUILD_KEYS = frozenset({"approve"})


def _match_shipped_role_key(path: str, *, suffix: str) -> str | None:
    """Extract ``{role_key}`` from ``/api/learning/shipped/{role_key}<suffix>`` (the shape of ``_match_pathway_id``)."""

    if not path.startswith(SHIPPED_API_PREFIX) or not suffix or not path.endswith(suffix):
        return None
    remainder = path[len(SHIPPED_API_PREFIX): len(path) - len(suffix)]
    if not remainder or "/" in remainder:
        return None
    return unquote(remainder)


def _match_pathway_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{pathway_id}`` from ``/api/learning/pathways/{pathway_id}<suffix>`` (the shape of ``_match_run_id``).

    ``suffix`` is ``""`` (the read), ``"/cancel"`` or ``"/resume"`` (G5).
    """

    prefix = "/api/learning/pathways/"
    if not path.startswith(prefix) or (suffix and not path.endswith(suffix)):
        return None
    remainder = path[len(prefix): len(path) - len(suffix)] if suffix else path[len(prefix):]
    if not remainder or "/" in remainder:
        return None
    return unquote(remainder)


def split_course_path(path: str) -> tuple[str, str] | None:
    """``(pathway id, path below the course)`` of a ``/learning/...`` request path, percent-decoded ONCE; else ``None``.

    The path below the course is ``""`` for ``/learning/<id>/`` (the entry page). What it names is judged by
    ``learning_store.resolve_course_file``: after the one decoding a ``..``, an empty part or a ``%`` left in a
    name is refused there.
    """

    if not path.startswith(COURSE_PREFIX):
        return None
    pathway_id, slash, rest = path[len(COURSE_PREFIX):].partition("/")
    if not slash or not learning_store.is_pathway_id(pathway_id):
        return None
    try:
        return pathway_id, unquote(rest, errors="strict")
    except UnicodeDecodeError:
        return None


def split_shipped_path(path: str) -> tuple[str, str] | None:
    """``(role key, page)`` of a ``/learning/shipped/...`` request path, percent-decoded ONCE; else ``None``.

    The page is ``""`` for ``/learning/shipped/<role key>/`` (the entry page). What it names is judged by
    the served page: only ``pathway.html`` is one, so any other name (a ``/``, a ``..`` or a ``%`` left after the
    one decoding included) is refused.
    """

    if not path.startswith(SHIPPED_PREFIX):
        return None
    role_key, slash, rest = path[len(SHIPPED_PREFIX):].partition("/")
    if not slash or not learning_shipped_store.is_role_key(role_key):
        return None
    try:
        return role_key, unquote(rest, errors="strict")
    except UnicodeDecodeError:
        return None


def pathway_not_built_page(course: ShippedCourse) -> bytes:
    """The page served as a shipped course's ``pathway.html`` while the local page is not built.

    Plain: the course's own stylesheet and theme script (so it reads like the pages around it), nothing else.
    """

    from html import escape

    role = escape(course.role)
    public = learning_shipped_store.public_url(course)
    opening = "" if public is None else f'<p><a href="{escape(public)}" target="_blank" rel="noopener">Open this course on the GigAI learning center</a></p>\n'
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Your pathway - {role}</title>\n"
        f'<script src="{learning_shipped_store.ASSET_BASE}/theme-init.js"></script>\n'
        f'<link rel="stylesheet" href="{learning_shipped_store.ASSET_BASE}/course.css">\n'
        '</head>\n<body>\n<main class="content" data-role="pathway-not-built">\n'
        "<h1>Your pathway page is not built yet</h1>\n"
        f"<p>This page is yours alone: for the course {role}, it shows what your resume already covers and what is new "
        "to you. It is built on this computer from your own resume and stored postings, and it has not been built yet.</p>\n"
        "<p>To build it: in Scout, open Learning pathways and press Build your pathway page on this course, or run "
        f"<code>gigai scout learning pathway {escape(course.role_key)}</code>. Building it calls no model.</p>\n"
        "<p>The lessons and practice pages of this course are the same for everyone and are on the GigAI learning center.</p>\n"
        f"{opening}"
        "</main>\n</body>\n</html>\n"
    ).encode("utf-8")


class LearningRoutesMixin:
    """``Handler`` mixin: the ``/api/learning/pathways`` reads, the generation routes (``POST``) and ``GET`` / ``HEAD /learning/...``."""

    def _learning_paths(self) -> tuple[Path, Path] | None:
        home_root = getattr(self._backend, "home_root", None)
        target = getattr(self._backend, "target", None)
        if home_root is None or target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return Path(home_root), Path(target)

    def _learning_no_query(self) -> bool:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        if query:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {sorted(query)[0]}; allowed: none")
            return False
        return True

    def _handle_get_learning_pathways(self) -> None:
        paths = self._learning_paths()
        if paths is None or not self._learning_no_query():
            return
        try:
            body = pathways_response(*paths)
        except LearningError as exc:
            self._error(HTTPStatus.NOT_FOUND, exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_learning_pathway(self, pathway_id: str) -> None:
        paths = self._learning_paths()
        if paths is None or not self._learning_no_query():
            return
        try:
            pathway = learning_store.get_pathway(*paths, pathway_id)
        except LearningError as exc:
            self._error(HTTPStatus.NOT_FOUND, exc.code, str(exc))
            return
        if pathway is None:
            shipped = learning_shipped_store.get_course(pathway_id)
            if shipped is not None:
                self._write_json(HTTPStatus.OK, shipped_response(*paths, shipped))
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "there is no such learning pathway")
            return
        self._write_json(HTTPStatus.OK, live_pathway_response(*paths, pathway))

    def _learning_job_error(self, exc: LearningError) -> None:
        code = "not_found" if exc.code == "pathway_not_found" else exc.code
        message = "there is no such learning pathway" if code == "not_found" else str(exc)
        self._error(_JOB_ERROR_STATUS.get(exc.code, HTTPStatus.UNPROCESSABLE_ENTITY), code, message)

    def _handle_post_learning_pathways(self) -> None:
        """Without ``approve``: the estimate, nothing started. With it: the request is stored and its job starts (202)."""

        from ... import learning_job

        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - _GENERATE_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}; allowed: approve, role_text")
            return
        role_text, approve = body.get("role_text"), body.get("approve", False)
        if type(role_text) is not str or type(approve) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "role_text must be a string and approve true or false")
            return
        paths = self._learning_paths()
        if paths is None:
            return
        try:
            if not approve:
                self._write_json(HTTPStatus.OK, learning_job.ask_response(*paths, role_text))
                return
            pathway = learning_job.start(*paths, role_text)
        except LearningError as exc:
            self._learning_job_error(exc)
            return
        self._write_json(HTTPStatus.ACCEPTED, live_pathway_response(*paths, pathway))

    def _handle_post_learning_pathway_action(self, pathway_id: str, action: str) -> None:
        """``cancel``: ask the job to stop (200, ``cancel_requested``). ``resume``: go on with a stopped job (202)."""

        from ... import learning_job

        body = self._read_json_body()
        if body is None:
            return
        if not (isinstance(body, dict) and not body):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no body keys")
            return
        paths = self._learning_paths()
        if paths is None:
            return
        try:
            if not learning_store.is_pathway_id(pathway_id):
                raise LearningError("pathway_not_found", "there is no such learning pathway")
            if action == "resume":
                learning_job.resume(*paths, pathway_id)
                asked = None
            else:
                asked = learning_job.cancel(*paths, pathway_id)
            pathway = learning_store.get_pathway(*paths, pathway_id)
            if pathway is None:
                raise LearningError("pathway_not_found", "there is no such learning pathway")
        except LearningError as exc:
            self._learning_job_error(exc)
            return
        response = live_pathway_response(*paths, pathway)
        if asked is None:
            self._write_json(HTTPStatus.ACCEPTED, response)
            return
        self._write_json(HTTPStatus.OK, {**response, "cancel_requested": asked})

    def _handle_post_learning_shipped_pathway(self, role_key: str) -> None:
        """Without ``approve``: what the build takes and sends, nothing sent. With it: the build starts (202, the course's row)."""

        from ... import learning_pathway_build

        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - _BUILD_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}; allowed: approve")
            return
        approve = body.get("approve", False)
        if type(approve) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "approve must be true or false")
            return
        paths = self._learning_paths()
        if paths is None:
            return
        course = learning_shipped_store.get_course(role_key)
        if course is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "there is no such course shipped with GigAI")
            return
        try:
            if not approve:
                self._write_json(HTTPStatus.OK, learning_pathway_build.ask_response(*paths, course))
                return
            learning_pathway_build.start(*paths, course)
        except LearningError as exc:
            self._error(_JOB_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.ACCEPTED, shipped_response(*paths, course))

    def _learning_course_file(self, path: str) -> Path | None:
        """The stored file a ``/learning/...`` request names, or ``None`` (whatever the reason: the answer is one 404)."""

        home_root = getattr(self._backend, "home_root", None)
        target = getattr(self._backend, "target", None)
        named = split_course_path(path)
        if home_root is None or target is None or named is None:
            return None
        try:
            return learning_store.resolve_course_file(Path(home_root), Path(target), named[0], named[1])
        except LearningError:
            return None

    def _learning_served(self, path: str) -> tuple[str, Path | bytes] | None:
        """What a ``/learning/...`` request is answered with: ``(the name its kind is read from, a file or the bytes)``.

        ``None`` is the one 404. A shared asset, the ONE page of a shipped course (its ``pathway.html``: the user's own
        file, or the not-built page), else a file of a generated course.
        """

        if path.startswith(ASSETS_PREFIX):
            try:
                asset = learning_shipped_store.asset_file(unquote(path[len(ASSETS_PREFIX):], errors="strict"))
            except UnicodeDecodeError:
                return None
            return None if asset is None else (asset.name, asset)
        if path.startswith(SHIPPED_PREFIX):
            home_root = getattr(self._backend, "home_root", None)
            target = getattr(self._backend, "target", None)
            named = split_shipped_path(path)
            if home_root is None or named is None:
                return None
            role_key, page = named
            if page != learning_shipped_store.PATHWAY_PAGE:
                return None  # the lessons are on the learning center: this server serves no other page of a shipped course
            course = learning_shipped_store.get_course(role_key)
            if course is None:
                return None
            built = None if target is None else learning_shipped_store.pathway_page(Path(home_root), Path(target), role_key)
            return (page, built if built is not None else pathway_not_built_page(course))
        resource = self._learning_course_file(path)
        return None if resource is None else (resource.name, resource)

    def _handle_get_learning_file(self, path: str) -> None:
        served = self._learning_served(path)
        if served is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            return
        name, resource = served
        body = resource if isinstance(resource, bytes) else resource.read_bytes()
        self._write_bytes(HTTPStatus.OK, course_content_type(name), body, COURSE_HEADERS)

    def _handle_head_learning_file(self, path: str) -> None:
        """``HEAD``: the headers ``GET`` would send (the file's size included) and no body; 404 has no body either."""

        served = self._learning_served(path)
        if served is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name, resource = served
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", course_content_type(name))
        self.send_header("Content-Length", str(len(resource) if isinstance(resource, bytes) else resource.stat().st_size))
        for name, value in COURSE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()


__all__ = [
    "ASSETS_PREFIX",
    "COURSE_CSP",
    "COURSE_HEADERS",
    "COURSE_PREFIX",
    "LearningRoutesMixin",
    "course_content_type",
    "course_url",
    "local_page",
    "live_pathway_response",
    "pathway_response",
    "pathway_summary",
    "pathway_not_built_page",
    "pathways_response",
    "shipped_response",
    "shipped_summary",
    "SHIPPED_PREFIX",
    "split_course_path",
    "split_shipped_path",
]
