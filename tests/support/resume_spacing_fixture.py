"""0.1.11.5 (a): a synthetic 20-bullet job resume in the shape of a real pick, for the spacing scale's tests.

A header's room, a Summary, five roles with bullets (20 by default, most of them two printed lines), Projects, the
block of four earlier roles, two degrees and a long Skills line.  Invented: no real person, employer or product.
"""

from __future__ import annotations

SKILLS = (
    "Python, Go, TypeScript, Ruby, Java, Kotlin, PostgreSQL, MySQL, Redis, Kafka, RabbitMQ, Kubernetes, Docker, Terraform, Helm, AWS, GCP, "
    "Airflow, dbt, Snowflake, Spark, Prometheus, Grafana, OpenTelemetry, gRPC, GraphQL, REST, React, Node.js, FastAPI, Rails, CI/CD, "
    "Feature flags, Incident response, Capacity planning, Technical mentoring"
)
ROLES = (
    ("Larkspur Table Systems", "Staff Software Engineer", "2022 - Present"),
    ("Quillmere Logistics", "Senior Software Engineer", "2019 - 2022"),
    ("Brindlewood Health", "Senior Software Engineer", "2016 - 2019"),
    ("Okapi Payments", "Software Engineer", "2014 - 2016"),
    ("Fennel Street Labs", "Software Engineer", "2012 - 2014"),
)
EARLIER = (
    "Software Engineer, Marrow & Pine | 2010 - 2012",
    "Software Engineer, Tidewater Analytics | 2008 - 2010",
    "Junior Developer, Copperfield Media | 2006 - 2008",
    "Support Engineer, Halcyon Networks | 2004 - 2006",
)
#: What a two-line bullet reads like; ``{n}`` keeps every bullet's text its own.
BULLET = "- Led the redesign of scheduling service {n}, moving {m} million shift records a day onto an event-driven pipeline and cutting the nightly reconciliation from hours to minutes."


def bullet(number: int) -> str:
    return BULLET.format(n=number, m=number + 12)


def resume(bullets: int = 20, *, master: bool = False) -> str:
    """Resume markdown in GigAI's format with ``bullets`` role bullets, dealt to the five roles in turn.

    ``master``: the same person's master resume: each earlier role is an entry of its own with one bullet (a
    master has no "Earlier experience" block: that is how a job's resume prints the roles it shows no line of)."""

    per_role: list[list[str]] = [[] for _ in ROLES]
    for number in range(bullets):
        per_role[number % len(ROLES)].append(bullet(number))
    out = [
        "## Summary", "",
        "Staff engineer with fourteen years building scheduling, payments and data platforms for restaurants and clinics; leads small teams, "
        "writes the design, ships the first version and stays for the on-call.", "",
        "## Experience", "",
    ]
    for (company, title, dates), lines in zip(ROLES, per_role):
        if lines:
            out += [f"### {company}", f"{title} | {dates}", "", *lines, ""]
    if master:
        for number, role in enumerate(EARLIER):
            title, _, rest = role.partition(", ")
            company, _, dates = rest.partition(" | ")
            out += [f"### {company}", f"{title} | {dates}", "", f"- Kept the billing exports of customer group {number + 2} running through two data centre moves.", ""]
    else:
        out += ["### Earlier experience", *EARLIER, ""]
    out += [
        "## Projects", "",
        "### Shiftboard, an open scheduling toolkit", "",
        "- Wrote and maintain a small open source library that solves weekly staff rosters under labour rules; used by three community clinics.", "",
        "## Education", "",
        "### Example State University", "M.S., Information Systems | 2010 - 2012", "",
        "### Example Institute of Technology", "B.Tech., Computer Science | 2000 - 2004", "",
        "## Skills", "", f"- {SKILLS}", "",
    ]
    return "\n".join(out)
