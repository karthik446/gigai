"""0.1.11.4 J1: ``GET`` / ``PUT /api/jobs-folder`` (J3: and ``POST /api/jobs-folder/open``) -- the jobs folder, one folder per application.

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

J3: ``POST /api/jobs-folder/open`` is the page's "Open folder" button: Scout's own page only (403
``forbidden_origin`` without this server's ``Origin``), the folder found server-side, never a client path.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ... import jobs_folder, open_folder
from ...quick_assess import QuickAssessError
from ...tailored_resume import tailored_resume_path
from ..contracts import FindJobsContractError
from ..job_state import normalize_job_identity

_ERROR_STATUS = {
    "folder_unwritable": HTTPStatus.CONFLICT,
    "setting_write_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class JobsFolderRoutesMixin:
    """``Handler`` mixin: ``GET`` and ``PUT /api/jobs-folder``, ``POST /api/jobs-folder/open``."""

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

    def _handle_post_jobs_folder_open(self) -> None:
        """``POST /api/jobs-folder/open``: show the jobs folder (``{}``) or one job's folder in the file manager.

        Scout's own page only (this server's own ``Origin``, like ``/api/pdf-header``): an agent never opens
        windows on the person's computer.  The folder is found HERE, from the stored job (or the setting); the
        request never carries a path, and a folder that is not inside the jobs folder is refused.
        """

        if not self.headers.get("Origin"):
            self._log_rejection("forbidden_origin: a folder is opened for Scout's own page only")
            self._error(HTTPStatus.FORBIDDEN, "forbidden_origin", "this route answers Scout's own browser page only")
            return
        home_root = self._jobs_folder_home()
        if home_root is None:
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return
        unknown = sorted(set(body) - {"profile_id", "job_identity"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        root = jobs_folder.jobs_folder(home_root)
        folder, shown = root.path, root.shown
        if body:
            profile_id, job = body.get("profile_id"), body.get("job_identity")
            target = getattr(self._backend, "target", None)
            if type(profile_id) is not str or type(job) is not str or not profile_id.strip() or not job.strip() or target is None:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id and job_identity go together, as text")
                return
            try:
                stored = tailored_resume_path(home_root, target, profile_id.strip(), normalize_job_identity(job.strip()))
            except (FindJobsContractError, QuickAssessError, ValueError) as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", str(exc))
                return
            found = jobs_folder.stored_job_folder(home_root, stored)
            if found is None:
                self._error(HTTPStatus.NOT_FOUND, "folder_missing", "GigAI has not made a folder for this job yet")
                return
            folder, shown = found.path, found.shown
        resolved = folder.resolve()
        if folder.is_symlink() or not resolved.is_dir():
            self._error(HTTPStatus.NOT_FOUND, "folder_missing", f"{shown} is not there yet")
            return
        if not resolved.is_relative_to(root.path.resolve()):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "outside_jobs_folder", "that folder is not inside your jobs folder")
            return
        result = open_folder.open_in_file_manager(resolved, shown, home_root=home_root)
        self._write_json(HTTPStatus.OK, {"schema_version": "scout-jobs-folder-open:1", "opened": result.opened, "shown": shown, "message": result.message})

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
