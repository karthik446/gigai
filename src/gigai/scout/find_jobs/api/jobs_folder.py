"""0.1.11.4 J1: ``GET`` / ``PUT /api/jobs-folder`` -- the jobs folder, one folder per application.

``gigai.scout.jobs_folder`` is the one module that knows the folder; this is only the HTTP
wiring, the shape of ``/api/resumes-folder``.  ``GET`` answers where it is (``path``, ``shown``
as the user types it, ``source`` ``default`` or ``setting``, the ``default``, whether it
``exists``); with ``profile_id`` and ``job_identity`` it adds ``job``: that job's own folder
(``path``, ``shown``, ``relative`` = ``<company>/<role>``, ``files`` = ``{resume}``: a file name
or ``null``), or ``null`` when GigAI has not made one.  ``PUT`` ``{"path": "..."}`` saves another
folder (created when missing; ``""`` or ``null`` goes back to the default).  Folders already
written are not moved.

System data only: folder paths and file names (the posting's company and role as slugs), never a
file's contents.  A read is one index lookup: the folder is never scanned.  The folder itself
never holds contact details (``jobs_folder.save_resume``).  ``PUT`` goes through the Handler's
loopback + CSRF guards like every write.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ... import jobs_folder
from ...quick_assess import QuickAssessError
from ...tailored_resume import tailored_resume_path
from ..contracts import FindJobsContractError
from ..job_state import normalize_job_identity

_ERROR_STATUS = {
    "folder_unwritable": HTTPStatus.CONFLICT,
    "setting_write_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class JobsFolderRoutesMixin:
    """``Handler`` mixin: ``GET`` and ``PUT /api/jobs-folder``."""

    def _jobs_folder_home(self):
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
        return home_root

    def _handle_get_jobs_folder(self) -> None:
        home_root = self._jobs_folder_home()
        if home_root is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - {"profile_id", "job_identity"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        profile_id, job = (query.get("profile_id") or [""])[0].strip(), (query.get("job_identity") or [""])[0].strip()
        body = jobs_folder.jobs_folder(home_root).to_json()
        if profile_id or job:
            target = getattr(self._backend, "target", None)
            if not profile_id or not job or target is None:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id and job_identity go together")
                return
            try:
                stored = tailored_resume_path(home_root, target, profile_id, normalize_job_identity(job))
            except (FindJobsContractError, QuickAssessError, ValueError) as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", str(exc))
                return
            folder = jobs_folder.stored_job_folder(home_root, stored)
            body["job"] = None if folder is None else folder.to_json()
        self._write_json(HTTPStatus.OK, body)

    def _handle_put_jobs_folder(self) -> None:
        home_root = self._jobs_folder_home()
        if home_root is None:
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return
        unknown = sorted(set(body) - {"path"})
        if unknown or "path" not in body:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key" if unknown else "invalid_value", "body must be exactly path (a folder; empty for the default)")
            return
        try:
            folder = jobs_folder.set_jobs_folder(home_root, body["path"])
        except jobs_folder.JobsFolderError as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.UNPROCESSABLE_ENTITY), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, folder.to_json())


__all__ = ["JobsFolderRoutesMixin"]
