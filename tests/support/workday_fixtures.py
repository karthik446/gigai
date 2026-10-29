"""0.1.11.12 packet 8: Workday bodies RECORDED from two shipped tenants on 2026-10-29 (the only live data in the Workday tests).

Eight requests with the GigAI user-agent, at least 1.1 s apart, within each host's ``robots.txt`` (``Allow: /<site>/``): per tenant
one ``robots.txt``, one list page (``POST .../wday/cxs/<tenant>/<site>/jobs`` with ``{"limit": 20, "offset": 0, ...}``) and two
detail GETs (``.../wday/cxs/<tenant>/<site><externalPath>``). The bytes are the tenants' own, trimmed only for size (the list page
keeps 6 of its 20 postings and drops ``facets``; one description is cut at 8000 characters) so the six bodies total about 40 KB.
Both hosts answered ``application/json`` with ``cache-control: no-store`` and NO validator (no ``ETag``, no ``Last-Modified``): a
Workday board is never revalidated by header, only by the digest of its merged list body (``ats_feeds.workday_pages``).

Nothing here makes a request. ``tests/support/workday_recorded/`` holds the files; ``recorded(key)`` reads them.
"""

from __future__ import annotations

import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent / "workday_recorded"

# key -> (board token as stored, "<tenant>.<pod>.<site>")
TENANTS: dict[str, str] = {"f5": "ffive.wd5.f5jobs", "comcast": "comcast.wd115.Comcast_Careers"}


def recorded(key: str) -> dict[str, object]:
    """``{"token", "robots", "list", "details"}`` of one tenant: ``details`` maps each ``externalPath`` to its detail body."""

    details: dict[str, object] = {}
    for index in (0, 1):
        path = (DIRECTORY / f"{key}-detail{index}.path").read_text(encoding="utf-8")
        details[path] = json.loads((DIRECTORY / f"{key}-detail{index}.json").read_text(encoding="utf-8"))
    return {
        "token": TENANTS[key],
        "robots": (DIRECTORY / f"{key}-robots.txt").read_text(encoding="utf-8"),
        "list": json.loads((DIRECTORY / f"{key}-list.json").read_text(encoding="utf-8")),
        "details": details,
    }
