"""0110-10-05 C: the length rule (``tailor_length``) on the stored tailored resume, and the product's page count.

``tailor_length`` is the rule, pure.  Here is what touches the world: the
measurement (``measure_pages``: the PDF's own layout, ``resume_pdf.fewest_pages``)
and the one write path ``PUT /api/tailored-resumes/length`` and ``gigai scout
resume length`` share (``change_stored_length``: read under the store's write
lock, apply ``restore`` or ``cut``, store; neither measures).  No model call,
no network.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .tailor_length import LENGTH_USES, apply_length_use
from .tailored_resume import (
    TailoredResume,
    TailorError,
    TailorResponse,
    list_tailored_resumes,
    render_markdown,
    save_tailor_response,
    tailored_resume_path,
    tailored_resume_write_lock,
)


def measure_pages(result: TailoredResume) -> int | None:
    """The pages ``result`` prints on (``resume_pdf.fewest_pages``); ``None`` when the renderer cannot say."""

    try:
        from .resume_pdf import fewest_pages

        return fewest_pages(result)
    except Exception:  # noqa: BLE001 - no renderer (Typst missing or failing): the length is flagged, never guessed
        return None


def with_length_use(stored: TailorResponse, use: str) -> TailorResponse:
    """``stored`` after one length action, its markdown re-rendered; ``stored`` itself when nothing changes.

    ``updated_at`` is unchanged, as for a line choice: the resume is the same tailoring.
    """

    result = apply_length_use(stored.result, use)
    if result == stored.result:
        return stored
    return replace(stored, result=result, markdown=render_markdown(result))


def change_stored_length(
    home_root: Path, target: Path, *, profile_id: str | None, job_identity: str, use: str, updated_at: str | None = None
) -> TailorResponse:
    """Put back what one stored tailored resume left out for length (``restore``), or leave it out again (``cut``).

    ``profile_id`` ``None`` takes the job's newest tailored resume.
    ``updated_at`` (the API's revision check) must be the stored one, else
    ``tailored_resume_changed``.  The read, the check and the write happen
    under the store's write lock.  Raises ``TailorError``: ``invalid_value``,
    ``tailored_resume_not_found``, ``tailored_resume_changed``, or
    ``list_tailored_resumes``'s own codes.
    """

    if use not in LENGTH_USES:
        raise TailorError("invalid_value", "use must be restore or cut")

    def newest() -> TailorResponse:
        items = list_tailored_resumes(home_root, target, profile_id=profile_id, job_identity=job_identity)
        if not items:
            raise TailorError("tailored_resume_not_found", "no stored tailored resume for that job")
        return items[0]

    with tailored_resume_write_lock(tailored_resume_path(home_root, target, newest().resume.profile_id, job_identity)):
        stored = newest()  # read again under the lock: a tailoring that landed meanwhile is never written over
        if updated_at is not None and stored.updated_at != updated_at:
            raise TailorError("tailored_resume_changed", "a newer tailoring replaced this resume; reload it")
        updated = with_length_use(stored, use)
        if updated is not stored:
            save_tailor_response(updated)
    return updated


__all__ = ["change_stored_length", "measure_pages", "with_length_use"]
