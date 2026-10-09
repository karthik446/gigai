"""0.1.10.7 M3a: what the posting read model and ``scout new`` tests share. Synthetic only.

A gig with a resume (``pipeline_fixtures``: the scripted model is installed),
a second active profile, optionally a deleted one, and a company index of
Lever boards put straight into the board cache under the fixture home and
indexed from there: no request is made, nothing is read from ``~/.gigai``.

The default profile's titles are the fixture config's (``staff ai engineer``,
...); the second profile's are ``staff engineer``. So a "Staff AI Engineer"
posting matches both profiles and a "Staff Engineer" posting only the second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from gigai.scout import profile_records
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.watchlist import add_company_from_url

from tests.support.pipeline_fixtures import PipelineFixture, build_pipeline_fixture
from tests.support.scout_profile_fixtures import uuids

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
SECOND_LABEL = "Platform track"
SECOND_TITLES = ("staff engineer",)
TITLE_BOTH = "Staff AI Engineer"
TITLE_SECOND_ONLY = "Staff Engineer"
RECRUITER_EMAIL = "talent.rowan@acme-hiring.example"


def posting_text(n: int, *, extra: str = "") -> str:
    return (
        f"Posting {n}: own the Python inference services. Requirements: 5+ years of Python in production; Kubernetes; "
        f"Terraform; GCP experience is a plus. Remote within the United States.{extra}"
    )


def lever_job(slug: str, n: int, *, title: str = TITLE_BOTH, text: str | None = None, created: datetime = NOW, salary: bool = False) -> dict[str, object]:
    job: dict[str, object] = {
        "id": f"{slug}-{n:05d}",
        "text": title,
        "hostedUrl": f"https://jobs.lever.co/{slug}/{slug}-{n:05d}",
        "categories": {"location": "Remote - United States"},
        "country": "US",
        "workplaceType": "remote",
        "descriptionPlain": text if text is not None else posting_text(n),
        "createdAt": int(created.timestamp() * 1000),
    }
    if salary:
        job["salaryRange"] = {"min": 180000, "max": 220000, "currency": "USD", "interval": "per-year-salary"}
    return job


def job_url(slug: str, n: int) -> str:
    return f"https://jobs.lever.co/{slug}/{slug}-{n:05d}"


@dataclass(frozen=True)
class PostingsFixture:
    base: PipelineFixture
    second_profile_id: str
    deleted_profile_id: str | None

    @property
    def home_root(self) -> Path:
        return self.base.home_root

    @property
    def target(self) -> Path:
        return self.base.target

    @property
    def default_profile_id(self) -> str:
        return self.base.profile_id

    @property
    def index(self) -> CompanyIndex:
        return CompanyIndex.for_home(self.home_root)

    @property
    def cache(self) -> BoardCache:
        return BoardCache(self.home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)

    def watch(self, slug: str) -> None:
        add_company_from_url(f"https://jobs.lever.co/{slug}", self.home_root, self.target)

    def seed(self, slug: str, jobs: list[dict[str, object]], *, seen_at: datetime, watch: bool = True) -> None:
        """Put ``jobs`` in ``slug``'s cached board body and index it as observed at ``seen_at`` (a new id is first seen then)."""

        if watch:
            self.watch(slug)
        self.cache.store("lever", board_list_url("lever", slug), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
        refresh_company(self.index, self.cache, ats="lever", slug=slug, observed_at=index_stamp(seen_at))

    def cli(self, *args: str) -> list[str]:
        return ["scout", "new", *args, "--home", str(self.home_root), "--target", str(self.target)]


class _FixtureClock(datetime):
    """``datetime`` whose ``now`` is the fixture's :data:`NOW`."""

    @classmethod
    def now(cls, tz: object = None) -> "datetime":  # type: ignore[override]
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)  # type: ignore[arg-type]


def freeze_scout_new_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gigai scout new`` reads the clock itself when a caller gives it no ``now`` (the command line never does).

    The postings here are seeded relative to :data:`NOW` (``days_ago``), and a first ``scout new`` looks back seven
    days: run against the real clock, a "new" posting stops being new a week after :data:`NOW` and a test of the
    command goes red on a date (it did, on 2026-10-09). A test that runs the command over postings seeded with
    ``days_ago`` calls this first: ``scout_new``'s own ``datetime.now`` then answers :data:`NOW`, as every test that
    calls the function with ``now=NOW`` already has it. (A test that seeds relative to the real clock does not.)
    """

    from gigai.scout import scout_new

    monkeypatch.setattr(scout_new, "datetime", _FixtureClock)


def build_postings_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, deleted: bool = True) -> PostingsFixture:
    """The gig, a second active profile and (``deleted``) a deleted third one whose titles match everything the others do."""

    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False)
    resolved = base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == base.profile_id)
    second = profile_records.create_profile(
        resolved, label=SECOND_LABEL, titles=SECOND_TITLES, titles_to_avoid=(), queries=SECOND_TITLES,
        resume_ref=default.resume_ref, uuid_factory=uuids(40),
    )
    deleted_id = None
    if deleted:
        gone = profile_records.create_profile(
            resolved, label="Old Search", titles=("staff engineer", "staff ai engineer"), titles_to_avoid=(),
            queries=("staff engineer",), resume_ref=default.resume_ref, uuid_factory=uuids(41),
        )
        profile_records.write_profile(resolved, profile_id=gone.profile_id, state="deleted", uuid_factory=uuids(42))
        deleted_id = gone.profile_id
    return PostingsFixture(base, second.profile_id, deleted_id)


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


__all__ = [
    "NOW",
    "RECRUITER_EMAIL",
    "SECOND_LABEL",
    "TITLE_BOTH",
    "TITLE_SECOND_ONLY",
    "PostingsFixture",
    "build_postings_fixture",
    "days_ago",
    "freeze_scout_new_clock",
    "job_url",
    "lever_job",
    "posting_text",
]
