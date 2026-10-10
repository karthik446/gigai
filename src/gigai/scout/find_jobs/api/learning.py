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
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ... import learning_store
from ...learning_store import LearningError, Pathway

LIST_SCHEMA = "scout-learning-pathways-response:1"
ONE_SCHEMA = "scout-learning-pathway-response:1"
COURSE_PREFIX = "/learning/"
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


def pathways_response(home_root: Path, target: Path) -> dict[str, object]:
    """The ``GET /api/learning/pathways`` body (also what ``gigai scout learning list --json`` prints)."""

    rows = [
        pathway_summary(pathway, status=_live_status(home_root, target, pathway))
        for pathway in learning_store.list_pathways(home_root, target)
    ]
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

    def _handle_get_learning_file(self, path: str) -> None:
        resource = self._learning_course_file(path)
        if resource is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            return
        self._write_bytes(HTTPStatus.OK, course_content_type(resource.name), resource.read_bytes(), COURSE_HEADERS)

    def _handle_head_learning_file(self, path: str) -> None:
        """``HEAD``: the headers ``GET`` would send (the file's size included) and no body; 404 has no body either."""

        resource = self._learning_course_file(path)
        if resource is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", course_content_type(resource.name))
        self.send_header("Content-Length", str(resource.stat().st_size))
        for name, value in COURSE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()


__all__ = [
    "COURSE_CSP",
    "COURSE_HEADERS",
    "COURSE_PREFIX",
    "LearningRoutesMixin",
    "course_content_type",
    "course_url",
    "live_pathway_response",
    "pathway_response",
    "pathway_summary",
    "pathways_response",
    "split_course_path",
]
