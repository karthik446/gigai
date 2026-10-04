"""0110-10-05 A: ``GET`` / ``PUT /api/resumes-folder`` -- the visible resumes folder.

``gigai.scout.resumes_folder`` is the one module that knows the folder; this is only the HTTP
wiring.  ``GET`` answers where it is (``path``, ``shown`` as the user types it, ``source``
``default`` or ``setting``, the ``default``, whether it ``exists``); with ``profile_id`` and
``job_identity`` it adds ``files``: the names of that job's markdown and PDF GigAI wrote there
(``null`` when there is none).  ``PUT`` ``{"path": "..."}`` saves another folder (created when
missing; ``""`` or ``null`` goes back to the default).  Files already written are not moved.

System data only: a folder path and file names (``<company>-<role>-<date>``), never a resume's
text.  The folder itself never holds contact details (``resumes_folder.save``).  ``PUT`` goes
through the Handler's loopback + CSRF guards like every write.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ... import resumes_folder
from ...quick_assess import QuickAssessError
from ...tailored_resume import tailored_resume_path
from ..contracts import FindJobsContractError
from ..job_state import normalize_job_identity

_ERROR_STATUS = {
    "folder_unwritable": HTTPStatus.CONFLICT,
    "setting_write_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class ResumesFolderRoutesMixin:
    """``Handler`` mixin: ``GET`` and ``PUT /api/resumes-folder``."""

    def _resumes_folder_home(self):
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
        return home_root

    def _handle_get_resumes_folder(self) -> None:
        home_root = self._resumes_folder_home()
        if home_root is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - {"profile_id", "job_identity"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        profile_id, job = (query.get("profile_id") or [""])[0].strip(), (query.get("job_identity") or [""])[0].strip()
        body = resumes_folder.resumes_folder(home_root).to_json()
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
            body["files"] = resumes_folder.job_files(home_root, resumes_folder.job_key(home_root, stored))
        self._write_json(HTTPStatus.OK, body)

    def _handle_put_resumes_folder(self) -> None:
        home_root = self._resumes_folder_home()
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
            folder = resumes_folder.set_resumes_folder(home_root, body["path"])
        except resumes_folder.ResumesFolderError as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.UNPROCESSABLE_ENTITY), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, folder.to_json())


__all__ = ["ResumesFolderRoutesMixin"]
