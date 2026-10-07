"""0.1.11.5 (c): a synthetic master in the shape of a real one, for the tests of the pick's cap.

The person of ``resume_spacing_fixture`` (the same five roles, four earlier roles, project, two degrees and Skills),
as a MASTER with far more lines than a resume shows: 45 role bullets by default (nine a role, each about two printed
lines and each with words of its own, so no line is a near duplicate of another), one line under each earlier role,
two project lines.  Every line has an id a person can read (``r1-03``: the fourth line of the first role).
Invented: no real person, employer or product.
"""

from __future__ import annotations

from tests.support.resume_spacing_fixture import ROLES, SKILLS

#: (heading, title, dates): the roles of the spacing fixture's "Earlier experience" block, as entries of a master.
EARLIER = (
    ("Marrow & Pine", "Software Engineer", "2010 - 2012"),
    ("Tidewater Analytics", "Software Engineer", "2008 - 2010"),
    ("Copperfield Media", "Junior Developer", "2006 - 2008"),
    ("Halcyon Networks", "Support Engineer", "2004 - 2006"),
)
EARLIER_LINES = (
    "Kept the billing exports of the wholesale customers running through two data centre moves.",
    "Wrote the nightly report loader that fed the regional sales dashboards.",
    "Maintained the newsroom publishing tool and its image resizing queue.",
    "Answered second-line tickets for the branch office routers and wrote their runbooks.",
)
_ACTIONS = (
    "Led the redesign of", "Rebuilt", "Took over and stabilised", "Designed and shipped", "Migrated", "Introduced", "Owned the roadmap for",
    "Replaced", "Hardened", "Scaled", "Wrote the first version of", "Untangled", "Automated", "Consolidated", "Instrumented",
)
_SYSTEMS = (
    "the shift scheduling service in Python", "the Kafka event pipeline behind table orders", "the PostgreSQL ledger of card settlements",
    "the Kubernetes clusters that run checkout", "the Terraform modules for every region", "the React console used by restaurant managers",
    "the GraphQL gateway for partner integrations", "the Redis cache in front of menu lookups", "the Airflow jobs that close the books nightly",
    "the gRPC contract between kitchens and couriers", "the Snowflake warehouse feeding finance", "the on-call rotation and its paging rules",
    "the feature flag service for staged rollouts", "the Go workers that print kitchen tickets", "the OpenTelemetry tracing across forty services",
)
_OUTCOMES = (
    "cutting the nightly reconciliation from five hours to twelve minutes", "so that a failed deploy rolls back by itself within ninety seconds",
    "which removed the weekly outage that cost support two hundred tickets", "lifting successful payments from 97.1% to 99.6% at dinner peak",
    "and trained six engineers to run it without me", "halving the cloud bill for that workload inside one quarter",
    "letting three product teams release daily instead of every fortnight", "after which page load at the till fell under one second",
    "with zero lost records across 14 million rows moved", "so auditors get their evidence from a query, not a spreadsheet",
    "retiring four cron servers nobody dared to restart", "and wrote the design review that the next two projects copied",
    "bringing median alert noise down from sixty pages a week to seven", "which let the company open two new countries on schedule",
    "ending the quarterly freeze that blocked every other team",
)
PROJECT = ("Shiftboard, an open scheduling toolkit", (
    "Wrote and maintain a small open source library that solves weekly staff rosters under labour rules; used by three community clinics.",
    "Added a constraint explainer that tells a manager which rule made a roster impossible.",
))
TITLE = "Staff Software Engineer, Scheduling Platform"
REQUIREMENTS = ("Python services in production", "Kafka event pipelines", "PostgreSQL at scale", "Kubernetes in production", "Terraform", "React")
POSTING = TITLE + "\n\nRequirements:\n" + "".join(f"- {text}\n" for text in REQUIREMENTS) + "\nNice to have:\n- GraphQL\n\nYou will own scheduling for restaurants.\n"


def line_id(role: int, line: int) -> str:
    return f"r{role + 1}-{line:02d}"


def line_text(role: int, line: int) -> str:
    number = role * 9 + line
    return f"{_ACTIONS[(number * 7 + 2) % 15]} {_SYSTEMS[(number * 4 + role) % 15]}, {_OUTCOMES[(number * 11 + 5) % 15]}."


def master_markdown(per_role: int = 9, *, roles: int = len(ROLES), earlier: bool = True, project: bool = True) -> str:
    """The master: ``roles`` roles with ``per_role`` lines each, then the earlier roles (one line each), a project, Skills, two degrees."""

    out = [
        "## Summary", "",
        "- Staff engineer with fourteen years building scheduling, payments and data platforms for restaurants and clinics. <!-- id:sum-1 -->", "",
        "## Experience", "",
    ]
    for role, (company, title, dates) in enumerate(ROLES[:roles]):
        out += [f"### {company} <!-- id:role-{role + 1} -->", f"{title} | {dates}", ""]
        out += [f"- {line_text(role, line)} <!-- id:{line_id(role, line)} -->" for line in range(per_role)] + [""]
    for number, (company, title, dates) in enumerate(EARLIER if earlier else ()):
        out += [f"### {company} <!-- id:early-{number + 1} -->", f"{title} | {dates}", "", f"- {EARLIER_LINES[number]} <!-- id:e{number + 1}-00 -->", ""]
    if project:
        out += ["## Projects", "", f"### {PROJECT[0]} <!-- id:proj-1 -->", ""]
        out += [f"- {text} <!-- id:p1-{number:02d} -->" for number, text in enumerate(PROJECT[1])] + [""]
    out += [
        "## Skills", "", f"- {SKILLS} <!-- id:skills-1 -->", "",
        "## Education", "",
        "### Example State University <!-- id:edu-1 -->", "M.S., Information Systems | 2010 - 2012", "",
        "### Example Institute of Technology <!-- id:edu-2 -->", "B.Tech., Computer Science | 2000 - 2004", "",
    ]
    return "\n".join(out)
