"""The demo data behind every release screenshot. All of it is invented.

Pure data, standard library only: tests import this file without the `media` dependency group.

Rules (tests/behaviors/release_media/test_media_persona.py holds them):

* the persona is not a real person; the only contact-shaped values are the ones in
  ``ALLOWED_CONTACT_VALUES``: an ``example.test`` address and a 555-01xx number, both reserved
  for fiction, and they appear only in the Generate PDF form (GigAI itself stores no contact data);
* companies are invented names on the reserved ``example.test`` domain, each different;
* postings vary in role, place, work mode and pay.
"""

from __future__ import annotations

from dataclasses import dataclass

PERSONA_NAME = "Perrin Aldecott"
PERSONA_EMAIL = "perrin@example.test"
PERSONA_PHONE = "(303) 555-0142"
PERSONA_LOCATION = "Denver, CO"
PERSONA_LINK = "perrin.example.test"

#: What the Generate PDF screenshot types into the form (field key -> value).
PDF_HEADER: dict[str, str] = {
    "name": PERSONA_NAME, "email": PERSONA_EMAIL, "phone": PERSONA_PHONE, "location": PERSONA_LOCATION, "link": PERSONA_LINK,
}

#: The only email / phone / link shapes a frame may show (privacy_scan.py's allowlist).
ALLOWED_CONTACT_VALUES: tuple[str, ...] = (PERSONA_EMAIL, PERSONA_PHONE, PERSONA_LINK)

#: Headerless on purpose: GigAI stores no name or contact details, so the resume it imports has none.
#: The summary sentence is the first line (no `## Summary` heading above it): the fixture model
#: builds its tailored resume from the first line it is given.
RESUME_MARKDOWN = """\
Senior backend engineer with nine years building Python services, data pipelines and the platforms they run on.

## Experience
### Larkspur Freight
**Senior Software Engineer** | 2021 - present
- Built Python services that price and route 40,000 shipments a day.
- Led a Postgres migration of a 4 TB primary with no downtime.
- Cut CI time from 38 to 14 minutes by splitting the test suite and caching builds.

### Mossbank Analytics
**Software Engineer** | 2017 - 2021
- Built the ingestion pipeline behind the reporting product: Kafka, Python workers, Postgres.
- Ran the on-call rotation for a team of six and wrote its runbooks.

## Skills
Python, Postgres, Kubernetes, Kafka, Terraform, Docker, GitHub Actions

## Education
B.S. Computer Science, 2016
"""

ROLES_DEFAULT: tuple[str, ...] = ("software engineer", "backend engineer")
SECOND_PROFILE_LABEL = "Platform track"
SECOND_PROFILE_TITLES: tuple[str, ...] = ("platform engineer", "site reliability engineer", "infrastructure engineer")


@dataclass(frozen=True)
class Posting:
    title: str
    place: str
    mode: str  # Lever's workplaceType: remote | hybrid | on-site
    pay: tuple[int, int] | None
    about: str


@dataclass(frozen=True)
class Company:
    slug: str  # a made-up Lever board; the UI shows it as "Tallgrass Health"
    blurb: str
    age_days: int  # how long ago its postings were first seen
    wave: int  # 1 = already seen; 2 = arrives after "Mark all seen", so it is "new since last check"
    postings: tuple[Posting, ...]

    @property
    def name(self) -> str:
        return " ".join(part.capitalize() for part in self.slug.split("-"))


REMOTE_US = "Remote - United States"

COMPANIES: tuple[Company, ...] = (
    Company(
        slug="tallgrass-health",
        blurb="Tallgrass Health builds scheduling and records software for rural clinics.",
        age_days=6,  # inside the rank lane's 7-day window: older postings are not ranked, and every row shows a Rank chip
        wave=1,
        postings=(
            Posting("Senior Software Engineer, Scheduling", REMOTE_US, "remote", (172_000, 205_000),
                    "Own the Python services behind appointment scheduling for 900 clinics."),
            Posting("Platform Engineer", "Denver, CO", "hybrid", (158_000, 190_000),
                    "Run the Kubernetes platform our product teams deploy to, and the pipelines that get code there."),
            Posting("Backend Engineer, Records", "Minneapolis, MN", "on-site", None,
                    "Build the records API that clinics and labs exchange results through."),
        ),
    ),
    Company(
        slug="quillon-robotics",
        blurb="Quillon Robotics makes warehouse robots and the fleet software that directs them.",
        age_days=5,
        wave=1,
        postings=(
            Posting("Staff Software Engineer, Fleet Services", "Boulder, CO", "hybrid", (198_000, 240_000),
                    "Lead the services that plan routes for 3,000 robots across 40 warehouses."),
            Posting("Site Reliability Engineer", REMOTE_US, "remote", (165_000, 195_000),
                    "Keep fleet control available: capacity, incident response and the tooling behind both."),
        ),
    ),
    Company(
        slug="brightwater-ledger",
        blurb="Brightwater Ledger is an accounting platform for water and power utilities.",
        age_days=6,
        wave=1,
        postings=(
            Posting("Software Engineer, Billing", REMOTE_US, "remote", (148_000, 176_000),
                    "Build the billing engine that turns meter reads into invoices for two million accounts."),
            Posting("Senior Backend Engineer, Payments", "Austin, TX", "hybrid", (175_000, 210_000),
                    "Own payment processing and reconciliation, in Python and Postgres."),
            Posting("Infrastructure Engineer", "Austin, TX", "on-site", None,
                    "Run the cloud accounts, networks and databases the product lives on."),
        ),
    ),
    Company(
        slug="halcyon-grid",
        blurb="Halcyon Grid forecasts demand for regional power grids.",
        age_days=4,
        wave=1,
        postings=(
            Posting("Senior Software Engineer, Data Platform", REMOTE_US, "remote", (180_000, 215_000),
                    "Build the pipelines that turn sensor data from 60 substations into hourly forecasts."),
            Posting("Platform Engineer, Developer Experience", "Chicago, IL", "hybrid", (160_000, 188_000),
                    "Make builds, tests and deploys fast for 70 engineers."),
        ),
    ),
    Company(
        slug="osprey-lane",
        blurb="Osprey Lane is a booking marketplace for independent outdoor guides.",
        age_days=0,
        wave=2,
        postings=(
            Posting("Software Engineer, Marketplace", REMOTE_US, "remote", (150_000, 182_000),
                    "Build search and booking for 12,000 guides and the people who hire them."),
            Posting("Senior Backend Engineer, Search", "Salt Lake City, UT", "hybrid", (170_000, 200_000),
                    "Own the search service: indexing, ranking and the API in front of it."),
            Posting("Site Reliability Engineer, Platform", REMOTE_US, "remote", None,
                    "Run the platform through the summer peak, when traffic is six times winter's."),
        ),
    ),
)

REQUIREMENTS = (
    "Requirements: five or more years of Python in production; Postgres, including schema changes on live tables; "
    "hands-on experience with Google Cloud Platform (GCP); clear written communication."
)

#: Saved before the screenshots; the fixture model asks `cloud:gcp` of every posting until it is answered.
ANSWERS: tuple[dict[str, str], ...] = (
    {
        "question_id": "cloud:gcp",
        "question": "Do you have hands-on Google Cloud Platform experience?",
        "answer": "Yes. Two years running batch workloads and Postgres on GCP: GKE, Cloud SQL and Pub/Sub.",
    },
    {
        "question_id": "oncall:rotation",
        "question": "Have you been on call for a production service?",
        "answer": "Yes. Four years on a weekly rotation; I ran it for a team of six and wrote the runbooks.",
    },
    {
        "question_id": "work:authorization",
        "question": "Are you authorized to work in the United States without sponsorship?",
        "answer": "Yes.",
    },
)

STORIES: tuple[dict[str, object], ...] = (
    {
        "title": "Moved a 4 TB Postgres primary with no downtime",
        "company": "Larkspur Freight",
        "role": "Senior Software Engineer",
        "period": "2023",
        "raw": "Our main Postgres primary was at 4 TB and close to its disk limit. I led three engineers "
               "through a dual-write cut-over to a new cluster. We lost no writes and nobody was paged.",
        "narrative": {
            "situation": "The 4 TB Postgres primary behind shipment pricing was near its disk limit.",
            "task": "Move it to a new cluster with no downtime.",
            "action": "Led three engineers through a dual-write cut-over, with a rehearsed rollback.",
            "result": "No lost writes and no pages; the old cluster was retired a week later.",
        },
        "tags": ["postgres", "migration", "leadership"],
        "answers_questions": ["Tell me about a migration you led", "Tell me about a project with a lot of risk"],
    },
    {
        "title": "Cut CI time from 38 to 14 minutes",
        "company": "Larkspur Freight",
        "role": "Senior Software Engineer",
        "period": "2022",
        "raw": "CI took 38 minutes and people stopped waiting for it. I split the suite by timing and "
               "cached the builds. It now takes 14 minutes.",
        "narrative": {
            "situation": "CI took 38 minutes, so engineers merged without waiting for it.",
            "task": "Make it fast enough that people wait for green.",
            "action": "Split the suite into timed shards and cached dependency builds.",
            "result": "14 minutes; merges without a green run dropped to zero.",
        },
        "tags": ["ci", "developer-experience"],
        "answers_questions": ["Tell me about a time you improved a process"],
    },
)

#: What the scripted agent says and the user replies in the terminal frames (tools/media/terminal.py).
#: The commands between these lines are REAL `gigai` calls against the demo home.
TERMINAL_ANSWER = {
    "question_id": "kafka:operations",
    "question": "Have you operated Kafka in production?",
    "answer": "Yes. Three years running a 12-broker cluster behind the ingestion pipeline at Mossbank Analytics.",
}
TERMINAL_STORY = {
    "title": "Kept ingestion running through a Kafka broker loss",
    "raw_text": "We lost two of twelve brokers during the quarter-end load. I moved the partitions by hand "
                "and we kept ingesting; no reports were late.",
    "situation": "Two of twelve Kafka brokers failed during the quarter-end load.",
    "task": "Keep ingestion running so reports were on time.",
    "action": "Reassigned the partitions by hand and throttled the backfill.",
    "result": "Ingestion never stopped; no report was late.",
    "tag": "kafka",
    "answers": "Tell me about an incident you handled",
}

#: The master resume (made from RESUME_MARKDOWN). Each of these stories backs the master line that
#: tells the same thing, so the Master page marks that line "backed" (story title -> the line).
MASTER_BACKED: dict[str, str] = {
    "Moved a 4 TB Postgres primary with no downtime": "Led a Postgres migration of a 4 TB primary with no downtime.",
    "Cut CI time from 38 to 14 minutes": "Cut CI time from 38 to 14 minutes by splitting the test suite and caching builds.",
}
#: The master lines "you" add to the hero job's resume with the page's own Add. The fixture model
#: shows none of the master's lines, so without these Picked would be empty.
MASTER_ADDED: tuple[str, ...] = (
    "Senior backend engineer with nine years building Python services, data pipelines and the platforms they run on.",
    "Built Python services that price and route 40,000 shipments a day.",
    "Led a Postgres migration of a 4 TB primary with no downtime.",
    "Cut CI time from 38 to 14 minutes by splitting the test suite and caching builds.",
)
#: The terminal's master frames: the agent adds TERMINAL_STORY to the master as one line, under this role.
TERMINAL_MASTER = {
    "entry": "Mossbank Analytics",
    "text": "Kept ingestion running through the loss of two of twelve Kafka brokers at quarter-end; no report was late.",
}


def posting_count(wave: int | None = None) -> int:
    return sum(len(company.postings) for company in COMPANIES if wave is None or company.wave == wave)


def all_text() -> str:
    """Every string in this file a frame can show, for the persona test and the privacy gate's self-check."""

    parts: list[str] = [PERSONA_NAME, PERSONA_EMAIL, PERSONA_PHONE, PERSONA_LOCATION, PERSONA_LINK, RESUME_MARKDOWN, REQUIREMENTS]
    for company in COMPANIES:
        parts += [company.slug, company.name, company.blurb]
        for posting in company.postings:
            parts += [posting.title, posting.place, posting.about]
    for answer in (*ANSWERS, TERMINAL_ANSWER):
        parts += list(answer.values())
    parts += [*MASTER_BACKED, *MASTER_BACKED.values(), *MASTER_ADDED, *TERMINAL_MASTER.values()]
    for story in (*STORIES, TERMINAL_STORY):
        for value in story.values():
            if isinstance(value, dict):
                parts += [str(item) for item in value.values()]
            else:
                parts += [str(item) for item in value] if isinstance(value, list) else [str(value)]
    return "\n".join(parts)
