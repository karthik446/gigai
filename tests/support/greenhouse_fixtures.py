"""0.1.10.8 U2: a synthetic Greenhouse board for the description / digest tests. No request is made, nothing is read from ``~/.gigai``.

A board's list body (and, per posting, its cached single-job detail) is put straight into the board cache under the fixture home and
indexed from there, the way ``tests/support/posting_fixtures.py`` does for Lever.
"""

from __future__ import annotations

import html
import json

from gigai.scout.find_jobs.ats_board_clients import _GREENHOUSE_JOB_URL
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.watchlist import add_company_from_url

from tests.support.posting_fixtures import PostingsFixture, posting_text

UPDATED = "2026-10-02T09:00:00Z"
UPDATED_LATER = "2026-10-03T09:00:00Z"


def gh_url(token: str, job_id: int) -> str:
    return f"https://boards.greenhouse.io/{token}/jobs/{job_id}"


def gh_job(token: str, job_id: int, title: str, *, updated_at: str = UPDATED) -> dict[str, object]:
    return {
        "id": job_id, "title": title, "absolute_url": gh_url(token, job_id), "location": {"name": "Remote - United States"},
        "updated_at": updated_at,
    }


def gh_content(text: str) -> str:
    """Greenhouse's ``content`` is HTML-escaped HTML."""

    return html.escape(f"<p>{text}</p>")


def gh_detail(job: dict[str, object], text: str) -> dict[str, object]:
    return {**job, "content": gh_content(text), "company_name": "Acme"}


def seed_greenhouse(
    fx: PostingsFixture, token: str, jobs: list[dict[str, object]], *, seen_at, details: dict[int, tuple[str, str]] | None = None,
) -> None:
    """Cache ``token``'s list body and, for ``details`` ``{job id: (text, marker)}``, each posting's cached description; index the board.

    A detail's ``marker`` is the ``updated_at`` it was fetched at: when it differs from the list's, the strict read has no text.
    """

    add_company_from_url(f"https://boards.greenhouse.io/{token}", fx.home_root, fx.target)
    fx.cache.store("greenhouse", board_list_url("greenhouse", token), body=json.dumps({"jobs": jobs}).encode("utf-8"), etag=None, last_modified=None, marker=None)
    by_id = {job["id"]: job for job in jobs}
    for job_id, (text, marker) in (details or {}).items():
        fx.cache.store(
            "greenhouse", _GREENHOUSE_JOB_URL.format(token=token, job_id=job_id),
            body=json.dumps(gh_detail(by_id[job_id], text)).encode("utf-8"), etag=None, last_modified=None, marker=marker,
        )
    refresh_company(fx.index, fx.cache, ats="greenhouse", slug=token, observed_at=index_stamp(seen_at))


__all__ = ["UPDATED", "UPDATED_LATER", "gh_content", "gh_detail", "gh_job", "gh_url", "posting_text", "seed_greenhouse"]
