"""0.1.11.5 (PE): a synthetic job resume with the SIZE of a real 20-bullet pick, for the tests of the auto fit.

The earlier fixtures (``resume_spacing_fixture``, ``pick_cap_fixture``) hold one bullet text of two short lines
twenty times over, 36 skills and short degree names; a real pick is longer in every block.  This one has:

- a Summary paragraph of four printed lines;
- 20 role bullets under SIX employers, every one with words of its own: twelve of two printed lines and eight of
  three (``BULLETS``);
- the block of four earlier roles (one line a role, under its title; ``EARLIER`` holds a fifth);
- two projects, each with a line of its technologies under its title and a one-line bullet (``PROJECTS`` holds a third
  for a longer resume);
- 60 skills;
- two degrees with LONG degree names and their years ("Bachelors, Electronics and Communication Engineering");
- the section order of a master pick: Summary, Experience, Projects, Skills, Education (Education ends the resume).

``resume(split=True)`` (0.1.11.5 PB5) is the same person with the shape whose page breaks wasted room: a role of two
bullets right after the longest role, and two projects of three and two bullets under two printed lines of technologies.

Invented: no real person, employer, school or product.
"""

from __future__ import annotations

import itertools

SUMMARY = (
    "Staff applied AI engineer with fifteen years across retrieval, ranking and the data platforms under them; takes a model from a notebook to a "
    "service that pages nobody, sets the evaluation before the first experiment, and leaves each team with runbooks, dashboards and two engineers "
    "who can run the system without the person who built it."
)
#: (employer, title, dates, number of bullets): the six roles that show lines, newest first.
ROLES = (
    ("Larkspur Table Systems", "Staff Applied AI Engineer", "Mar 2022 - Present", 5),
    ("Quillmere Logistics", "Senior Machine Learning Engineer", "Jan 2019 - Feb 2022", 4),
    ("Brindlewood Health", "Senior Software Engineer, Search", "Jun 2016 - Dec 2018", 4),
    ("Okapi Payments", "Software Engineer, Risk", "Aug 2014 - May 2016", 3),
    ("Fennel Street Labs", "Software Engineer", "Jul 2012 - Jul 2014", 2),
    ("Marrow & Pine", "Software Engineer", "Sep 2010 - Jun 2012", 2),
)
EARLIER = (
    "Software Engineer, Tidewater Analytics | 2008 - 2010",
    "Associate Software Engineer, Copperfield Media | 2007 - 2008",
    "Junior Developer, Halcyon Networks | 2006 - 2007",
    "Support Engineer, Pemberton Freight | 2005 - 2006",
    "Intern, Saltmarsh Instruments | 2004 - 2005",
)
#: Twenty bullets, each with its own words.  ``3`` marks the ones written to take three printed lines.
BULLETS = (
    (3, "Designed and shipped the retrieval service behind the kitchen assistant: a hybrid of BM25 and dense vectors over 40 million menu and "
        "order documents, reranked by a distilled cross-encoder, which lifted answer accuracy from 71% to 89% while p95 latency "
        "stayed under 400 ms at dinner peak."),
    (2, "Built the offline evaluation harness every model change goes through: 2,300 labelled questions, a judge calibrated against "
        "three human raters, and a report that names each regression."),
    (2, "Cut the monthly inference bill by 58% by moving the long tail of requests to a fine-tuned small model behind a router that falls "
        "back to the large one only when its own confidence is low."),
    (3, "Led four engineers through the migration of feature computation from nightly Spark jobs to a streaming pipeline on Kafka and Flink, "
        "so that a restaurant's menu change reaches the ranking model in ninety seconds instead of the next morning, checked daily "
        "against the warehouse."),
    (2, "Wrote the guardrail layer that keeps allergen answers grounded in the restaurant's own records, and the red-team suite of 600 "
        "prompts that it has to pass before each release."),
    (3, "Owned the demand forecasting models for 1,100 delivery routes: replaced a hand-tuned regression per depot with one gradient-boosted "
        "model with hierarchical reconciliation, bringing the weekly error from 18% to 9% and letting planners commit trucks two days earlier "
        "than they could before."),
    (2, "Put the training pipelines on Kubeflow with reproducible data snapshots, so a model from eight months ago can be rebuilt bit for "
        "bit when an auditor or a customer asks how a decision was made."),
    (2, "Introduced shadow deployments and a two-week holdback for every ranking change, which caught three launches that looked good "
        "offline and would have cost about 2% of completed orders."),
    (2, "Mentored five engineers from other teams through their first production models, and wrote the internal course on evaluation "
        "that forty people have since taken."),
    (3, "Rebuilt clinical search for 9 million patient documents on Elasticsearch with a learned ranking stage trained on click and dwell "
        "signals, which took the share of searches answered on the first page from 62% to 84% and removed the weekly synonym list that two "
        "nurses used to maintain by hand."),
    (2, "Designed the de-identification step every document passes before it is indexed, reviewed by the privacy office and tested "
        "against 50,000 synthetic records with planted names and dates."),
    (2, "Moved the search cluster to a blue-green rollout with automated relevance checks, ending the Saturday maintenance windows "
        "and the two-hour read-only periods that came with them."),
    (3, "Took over an indexing pipeline that lost about one document in four thousand, traced the loss to a retry that acknowledged a "
        "message before the write had committed, and fixed it with an idempotent writer and a reconciliation job that has reported zero "
        "missing documents in every run since."),
    (3, "Built the real-time fraud scoring service that sits in the card authorisation path: 140 features from a Redis feature store, a "
        "model served in 12 ms at p99, and rules the risk analysts can change without a deploy, which together cut chargebacks by 31% in "
        "the first two quarters."),
    (2, "Wrote the labelling queue and the weekly retraining job that keep the fraud model current, with a drift monitor that pages "
        "when the score distribution moves further than it did in any week of the last year."),
    (2, "Replaced a nightly batch of 300 SQL reports with a small set of dbt models and tests, so finance closes the month from tables "
        "checked every hour instead of spreadsheets checked by nobody."),
    (3, "Shipped the first recommendation feature of the mobile app, a collaborative filter trained on eleven months of listening history, "
        "then replaced it a year later with a two-tower model whose embeddings three other teams now use for search, notifications and the "
        "weekly email."),
    (2, "Set up the experiment platform the company still runs on: assignment by hashed user id, guardrail metrics that stop a test on "
        "their own, and a results page a product manager can read."),
    (3, "Wrote the ingestion service for retail point-of-sale feeds from 70 store chains in nine formats, with a schema registry and a "
        "quarantine for files that fail validation, which took the share of feeds loaded without a person touching them from about half "
        "to 97% inside a year."),
    (2, "Tuned the PostgreSQL cluster behind the reporting product through a tenfold growth in rows, with partitioning, covering "
        "indexes and a read replica, keeping report time under two seconds."),
)
#: (title, the technologies' line, one bullet).
PROJECTS = (
    ("Shiftboard, an open scheduling toolkit", "Python, OR-Tools, FastAPI, PostgreSQL, Docker",
     "Wrote and maintain an open source library that solves weekly staff rosters under labour rules."),
    ("Ledgerlint, a linter for evaluation sets", "Rust, Python bindings, GitHub Actions",
     "Finds leaked answers and duplicated questions in an evaluation set before a model is scored."),
    ("Tallybird", "TypeScript, React, SQLite, WebAssembly",
     "A browser tool that explains a ranking model's score for one result, feature by feature, without the data leaving the page."),
)
SKILLS = (
    "Python, PyTorch, TensorFlow, scikit-learn, XGBoost, LightGBM, Hugging Face Transformers, LangChain, LlamaIndex, Retrieval-augmented generation, "
    "Prompt engineering, Fine-tuning, Model evaluation, Vector search, FAISS, Elasticsearch, OpenSearch, Learning to rank, Recommender systems, "
    "Time series forecasting, Feature stores, MLflow, Kubeflow, Airflow, dbt, Spark, Flink, Kafka, Snowflake, BigQuery, PostgreSQL, MySQL, Redis, "
    "DynamoDB, Go, TypeScript, Java, Scala, SQL, Rust, FastAPI, gRPC, GraphQL, REST, React, Node.js, Kubernetes, Docker, Terraform, Helm, AWS, GCP, "
    "Prometheus, Grafana, OpenTelemetry, CI/CD, A/B testing, Incident response, Capacity planning, Technical mentoring"
)
#: (school, degree, years).  The degree names are as long as real ones: the first is too long for one line even at the
#: smallest size (its years go to a second line), the second fits one line only a step smaller than the body's type.
DEGREES = (
    ("Example Institute of Technology", "Master of Science, Computer Science and Information Systems", "2008 - 2010"),
    ("Example State University", "Bachelors, Electronics and Communication Engineering", "2000 - 2004"),
)
#: The sections after Experience, in a master pick's order (Education ends the resume).
ORDER = ("projects", "skills", "education")

# --- 0.1.11.5 PB5: the shape that wasted the foot of a page (``resume(split=True)``) ------------------------------------
#: The bullets of the six roles, in turn: a role of TWO bullets right after the longest one (a block of two or three
#: bullets was kept whole, so it left the page it did not fit on), 20 in all.
SPLIT_COUNTS = (6, 2, 4, 3, 3, 2)
#: A project's line of technologies long enough for TWO printed lines (it is never joined to the title's line).
LONG_TECH = (
    "Python, OR-Tools, FastAPI, PostgreSQL, SQLAlchemy, Alembic, Celery, Redis, Docker, Kubernetes, Helm, Terraform, GitHub Actions, "
    "OpenTelemetry, Prometheus, Grafana, React, TypeScript",
    "Rust, PyO3, Python bindings, Polars, Apache Arrow, Parquet, DuckDB, GitHub Actions, pre-commit, Hugging Face Datasets, sentence-transformers, "
    "FAISS, MinHash, WebAssembly, mdBook",
)
#: The projects' further bullets (two printed lines each): three bullets under the first project, two under the second.
PROJECT_BULLETS = (
    (
        "Wrote and maintain an open source library that solves weekly staff rosters under labour rules, used by eleven clinics and two food "
        "banks that publish their schedules from it every Friday.",
        "Added a rule language that a scheduler without a programmer can read, with forty worked examples and an explainer that names "
        "the rule behind every shift the solver refused.",
        "Cut the solve time of a 300-person roster from nine minutes to forty seconds by seeding the search with last week's roster and "
        "pruning the swaps that a rest rule already forbids.",
    ),
    (
        "Finds leaked answers and duplicated questions in an evaluation set before a model is scored, by exact and near-duplicate matching "
        "against the training corpus and the set's own earlier versions.",
        "Runs as a pre-commit hook and a continuous integration step in six public benchmark repositories, where it has stopped four "
        "releases that held questions copied from their own training split.",
    ),
)


def resume(
    *, bullets: int = len(BULLETS), projects: int = 2, earlier: int = 4, order: tuple[str, ...] = ORDER, master: bool = False, split: bool = False,
) -> str:
    """Resume markdown in GigAI's format: the first ``bullets`` of ``BULLETS`` under the six roles in turn of their counts.

    ``split`` (0.1.11.5 PB5): the shape whose page breaks wasted room: the roles hold ``SPLIT_COUNTS`` bullets (two
    right after the longest role) and the two projects hold three and two bullets of two printed lines under a line
    of technologies that is two printed lines (``LONG_TECH``, ``PROJECT_BULLETS``).

    ``order``: the sections after Experience (``()``: the resume ends with the earlier roles).  ``master``: the same
    person's master resume: each earlier role is an entry of its own with one line (a master has no "Earlier
    experience" block: that is how a job's resume prints the roles it shows no line of)."""

    out = ["## Summary", "", SUMMARY, "", "## Experience", ""]
    texts = iter(pair[1] for pair in BULLETS[:bullets])
    for (company, title, dates, count), split_count in zip(ROLES, SPLIT_COUNTS):
        count = split_count if split else count
        lines = [f"- {text}" for text in itertools.islice(texts, count)]
        if lines:
            out += [f"### {company}", f"{title} | {dates}", "", *lines, ""]
    if master:
        for number, role in enumerate(EARLIER):
            title, _, rest = role.partition(", ")
            company, _, dates = rest.partition(" | ")
            out += [f"### {company}", f"{title} | {dates}", "", f"- Kept the nightly exports of customer group {number + 2} running through a data centre move.", ""]
    elif earlier:
        out += ["### Earlier experience", *EARLIER[:earlier], ""]
    shown = [(title, tech, (text,)) for title, tech, text in PROJECTS[:projects]]
    if split:
        shown = [(title, tech, points) for (title, _tech, _text), tech, points in zip(PROJECTS[:projects], LONG_TECH, PROJECT_BULLETS)]
    sections = {
        "projects": ["## Projects", "", *(line for title, tech, points in shown for line in (f"### {title}", tech, "", *(f"- {text}" for text in points), ""))],
        "skills": ["## Skills", "", f"- {SKILLS}", ""],
        "education": ["## Education", "", *(line for school, degree, years in DEGREES for line in (f"### {school}", f"{degree} | {years}", ""))],
    }
    for name in order:
        if name != "projects" or projects:
            out += sections[name]
    return "\n".join(out)
