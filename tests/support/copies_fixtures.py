"""0.1.11.8 N1 + N2: one job posted once per country, beside what must never merge into it. Synthetic only.

On top of ``posting_fixtures`` (made-up Lever boards). The postings (``point-example`` unless said):

- 1-7: "Staff Engineer" posted once per country, seven countries ("Remote Estonia" ...), all with ONE description:
  ONE job, seven copies, none in the US;
- 8, 9: "Staff Engineer, Payments" at "Remote - United States" and "Austin, TX", one description: one job, two
  cities of one country;
- 10: "Staff Engineer, Search", in the US;
- 11: "STAFF  Engineer." at "Remote Portugal", the first job's description under a title written another way: a row
  of its own (the stored digest is of the title as written);
- 12: "Staff Engineer" at "Remote Spain" with ANOTHER description (another team): a row of its own;
- 13: "Staff Engineer, Platform" at "Remote" alone: nobody says where it is ("unclear location");
- and "Staff Engineer" with the first job's description on ANOTHER board (``other-example``), in the US.

Fourteen postings, seven jobs; five postings (four jobs) are not clearly outside the US. The fixture's own profiles
are US profiles (their lists hold the US postings only); :func:`anywhere_profile` adds a profile with NO country
setting, whose list holds all fourteen.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gigai.scout import profile_records
from gigai.scout.profile_records import ProfileSearchSettings

from tests.support.posting_fixtures import PostingsFixture, lever_job
from tests.support.scout_profile_fixtures import uuids

SLUG, OTHER = "point-example", "other-example"
COUNTRIES = ("Estonia", "Lithuania", "Latvia", "Bulgaria", "Romania", "Ukraine", "Poland")
#: The postings of the one job posted once per country.
ONE_JOB = list(range(1, 8))
#: Postings clearly outside the US, and the ones that are not (in the US, or nobody says where).
ABROAD = [*ONE_JOB, 11, 12]
NOT_ABROAD = [8, 9, 10, 13]
POSTINGS, JOBS = 14, 7
ANYWHERE_LABEL = "Anywhere"
ROLE = "Own the platform services. Requirements: Python in production; Kubernetes."
OTHER_ROLE = "Own the data pipelines. Requirements: SQL; Spark."


def copy_job(
    slug: str, n: int, title: str, location: str, country: str | None, newest: datetime | None = None, *, description: str = ROLE,
) -> dict[str, object]:
    """One posting. ``newest``: posting ``n`` went up ``n`` minutes before it, so posting 1 is the newest (else all at once)."""

    more = {} if newest is None else {"created": newest - timedelta(minutes=n)}
    job = lever_job(slug, n, title=title, text=description, **more)  # type: ignore[arg-type]
    job["categories"] = {"location": location}
    job["country"] = country
    return job


def board(leave_out: tuple[int, ...] = (), newest: datetime | None = None) -> list[dict[str, object]]:
    """``point-example``'s postings (``leave_out``: the ones the board no longer lists)."""

    jobs = [copy_job(SLUG, n, "Staff Engineer", f"Remote {country}", None, newest) for n, country in enumerate(COUNTRIES, start=1)]
    jobs += [
        copy_job(SLUG, 8, "Staff Engineer, Payments", "Remote - United States", "US", newest),
        copy_job(SLUG, 9, "Staff Engineer, Payments", "Austin, TX", "US", newest),
        copy_job(SLUG, 10, "Staff Engineer, Search", "Remote - United States", "US", newest),
        copy_job(SLUG, 11, "STAFF  Engineer.", "Remote Portugal", None, newest),
        copy_job(SLUG, 12, "Staff Engineer", "Remote Spain", None, newest, description=OTHER_ROLE),
        copy_job(SLUG, 13, "Staff Engineer, Platform", "Remote", None, newest),
    ]
    return [job for n, job in enumerate(jobs, start=1) if n not in leave_out]


def seed_copies(fx: PostingsFixture, *, seen_at: datetime, newest: datetime | None = None) -> None:
    fx.seed(SLUG, board(newest=newest), seen_at=seen_at)
    fx.seed(OTHER, [copy_job(OTHER, 1, "Staff Engineer", "Remote - United States", "US", newest)], seen_at=seen_at)


def anywhere_profile(fx: PostingsFixture) -> str:
    """A profile with NO country setting: its list holds the postings of every country. Returns its id."""

    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    record = profile_records.create_profile(
        resolved, label=ANYWHERE_LABEL, titles=("staff engineer",), titles_to_avoid=(), queries=("staff engineer",), resume_ref=default.resume_ref,
        search_settings=ProfileSearchSettings(countries=(), location=None, max_age_days=None, work_mode="any"), uuid_factory=uuids(70),
    )
    return record.profile_id
