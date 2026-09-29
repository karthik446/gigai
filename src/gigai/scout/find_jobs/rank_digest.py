"""Digest v2: the one-line posting digest and the compact resume digest a model ranks on.

SCOPE-ADD-3 (v0.1.9), ported from the ranking spike's ``digest.py`` +
``flags_v2.py`` (research/ranking-spike, REPORT Q1). Deterministic, no model
calls, no I/O.

A posting digest is one line, about 55 tokens against ~1,200 for the full
text::

    p12 | Senior Software Engineer @ acme | lvl=senior | loc=Remote - US [US] | yrs=5+ | req=Python, AWS | flags=no_sponsor

- ``req=`` lists the skills/tech found in the posting's REQUIREMENTS section
  only (found by heading; when no section is found the whole text is used
  and the field says ``(req=full)``), so a company blurb or a benefits list
  does not pad the skills.
- ``flags=`` are hard-constraint hints read from the WHOLE text, because they
  are exactly what the ranker must see as blockers: ``no_sponsor`` (a
  sponsorship/visa mention with a negation within 160 characters and no
  positive "we sponsor"), ``citizen_or_gc``, ``clearance`` and ``onsite``.
  v1's single tight regex caught 3 of the spike's 9 sponsorship/citizenship/
  clearance not-a-matches; this window-based v2 catches all 9. It also flags
  postings the full assessment only ASKS about (a "U.S. citizen or permanent
  resident" line), which is why a blocker demotes and never hides.

The resume digest is a few lines: years, a short summary, the skills in the
same vocabulary as ``req=``, and the candidate's constraints (target titles,
countries, visa need, location). ``level=`` comes from the target titles
(the spike hard-coded the operator's current title).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, Sequence

DIGEST_VERSION = "digest-v2"

# canonical label -> regex (case-insensitive). Kept to terms that separate one
# engineering posting from another (the spike's vocabulary, unchanged).
TECH: dict[str, str] = {
    "Python": r"python", "Go": r"golang|\bgo\b(?=[\s,/)]|$)", "Java": r"\bjava\b(?!script)", "Kotlin": r"kotlin",
    "Scala": r"scala\b", "Rust": r"\brust\b", "C++": r"c\+\+", "C#": r"c#|\.net\b|dotnet", "C": r"\bc\b(?=[,/ ]+(?:c\+\+|rust|go))",
    "TypeScript": r"typescript", "JavaScript": r"javascript|\bjs\b", "Node.js": r"node\.?js|\bnode\b", "Ruby": r"\bruby\b",
    "Rails": r"\brails\b", "PHP": r"\bphp\b", "Elixir": r"elixir", "Swift": r"\bswift\b", "Objective-C": r"objective-c",
    "React": r"\breact\b(?! native)", "React Native": r"react native", "Vue": r"\bvue", "Angular": r"angular",
    "GraphQL": r"graphql", "gRPC": r"grpc", "REST": r"\brest(?:ful)?\b", "Django": r"django", "Flask": r"flask",
    "FastAPI": r"fastapi", "Spring": r"\bspring\b", "AWS": r"\baws\b|amazon web services", "GCP": r"\bgcp\b|google cloud",
    "Azure": r"\bazure\b", "Kubernetes": r"kubernetes|\bk8s\b", "Docker": r"docker|container", "Terraform": r"terraform",
    "Pulumi": r"pulumi", "Kafka": r"kafka", "Kinesis": r"kinesis", "RabbitMQ": r"rabbitmq", "SQS": r"\bsqs\b",
    "Postgres": r"postgres", "MySQL": r"mysql", "SQL": r"\bsql\b", "NoSQL": r"nosql", "MongoDB": r"mongo",
    "DynamoDB": r"dynamo", "Cassandra": r"cassandra", "Redis": r"redis", "Elasticsearch": r"elasticsearch|opensearch",
    "Snowflake": r"snowflake", "BigQuery": r"bigquery", "Spark": r"\bspark\b", "Flink": r"flink", "Airflow": r"airflow",
    "dbt": r"\bdbt\b", "Hadoop": r"hadoop", "ClickHouse": r"clickhouse", "Databricks": r"databricks",
    "LLM": r"\bllms?\b|large language model|genai|generative ai", "RAG": r"\brag\b|retrieval", "ML": r"machine learning|\bml\b",
    "PyTorch": r"pytorch", "TensorFlow": r"tensorflow", "CUDA": r"\bcuda\b", "computer vision": r"computer vision",
    "iOS": r"\bios\b", "Android": r"android", "Linux": r"linux", "CI/CD": r"ci/cd|continuous integration",
    "observability": r"observability|datadog|prometheus|opentelemetry|grafana", "microservices": r"microservice",
    "distributed systems": r"distributed system", "event-driven": r"event[- ]driven", "serverless": r"serverless|lambda",
    "data pipelines": r"data pipeline|etl\b|elt\b", "security": r"\bsecurity\b|appsec", "embedded": r"embedded|firmware",
    "robotics": r"robotic|\bros\b", "networking": r"networking|tcp/ip", "frontend": r"front[- ]end", "backend": r"back[- ]end",
    "full-stack": r"full[- ]stack", "mobile": r"\bmobile\b", "infra": r"infrastructure", "SRE": r"\bsre\b|reliability engineering",
    "payments": r"payment", "fintech": r"fintech|financial services|banking", "healthcare": r"healthcare|clinical|hipaa",
    "blockchain": r"blockchain|crypto|web3|solidity", "search": r"\bsearch\b", "compilers": r"compiler",
    "HPC/GPU": r"\bgpu|\bhpc\b",
}
_TECH_RE = {label: re.compile(pattern, re.I) for label, pattern in TECH.items()}

_REQ_HEAD = re.compile(
    r"(requirement|qualification|who you are|what you(?:'|’)?ll? (?:bring|need)|what you bring|you have|you(?:'|’)ll have|"
    r"what we(?:'|’)?re? look(?:ing)? for|what we look for|your expertise|skills|about you|you should apply|"
    r"must[- ]have|ideal candidate|you might be|you may be a (?:good )?fit|experience (?:&|and) skills|you bring|"
    r"we(?:'|’)d love|you are|what makes you|to be successful|minimum|preferred|bonus|nice to have)",
    re.I,
)
_STOP_HEAD = re.compile(
    r"(responsibilit|what you(?:'|’)?ll do|what you will do|in this role|the role|about (?:us|the (?:team|company))|"
    r"benefit|compensation|salary|pay range|perks|equal (?:employment|opportunity)|who we are|your location|"
    r"what we offer|how we(?:'|’)ll take care|our commitment|a typical day|why join|life at|the difference you)",
    re.I,
)
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:\+\s*)?(?:years|yrs)", re.I)
_LEVELS = (
    ("principal", r"principal|distinguished|fellow"), ("senior staff", r"senior staff|sr\.? staff"), ("staff", r"\bstaff\b"),
    ("lead", r"\blead\b|\btech lead\b"), ("manager", r"manager|head of|director|\bvp\b"),
    ("senior", r"senior|\bsr\.?\b|\biii\b"), ("junior", r"junior|\bjr\b|associate|new grad|intern|\bi\b$"),
)

# Hard-constraint flags (v2): window-based sponsorship negation plus
# citizenship/clearance, read from the whole text.
_SPONSOR = re.compile(r"sponsor|work visa|visa support|immigration support|employment visa", re.I)
_NEG = re.compile(r"\b(not|unable|cannot|can't|cannot|no|without|won't|will not|doesn't|don't|isn't|ineligible|n't)\b", re.I)
_POS = re.compile(
    r"(will|can|able to|happy to|do|does) (?:provide |offer )?(?:visa )?sponsor(?:ship)? (?:is )?(?:available|for)"
    r"|sponsorship (?:is )?available|we sponsor",
    re.I,
)
_CITIZEN = re.compile(
    r"u\.?s\.? citizen|united states citizen|citizenship (?:is )?required|green card|permanent resident|must be a citizen|\bus persons?\b",
    re.I,
)
_CLEARANCE = re.compile(r"security clearance|\bts/sci\b|secret clearance|clearance required|active clearance", re.I)
_ONSITE = re.compile(r"\bon-?site\b|in[- ]office|\bhybrid\b", re.I)
_SPONSOR_WINDOW = 160

_MAX_TECHS = 14
_MAX_LOCATION = 70
_MAX_TITLE = 80


class DigestPosting(Protocol):
    """The posting fields a digest reads (``PostingRow`` satisfies it)."""

    @property
    def title(self) -> str: ...
    @property
    def company(self) -> str: ...
    @property
    def location(self) -> str: ...
    @property
    def text(self) -> str | None: ...
    @property
    def countries(self) -> tuple[str, ...] | None: ...


@dataclass(frozen=True)
class CandidatePrefs:
    """The candidate constraints the resume digest states (never the full config)."""

    titles: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    visa_sponsorship_required: bool = False
    location: str = ""
    remote_preferred: bool = False


def constraint_flags(text: str) -> list[str]:
    """``no_sponsor`` / ``citizen_or_gc`` / ``clearance`` / ``onsite``, in that order."""

    out: list[str] = []
    for match in _SPONSOR.finditer(text):
        window = text[max(0, match.start() - _SPONSOR_WINDOW): match.end() + _SPONSOR_WINDOW]
        if _NEG.search(window) and not _POS.search(window):
            out.append("no_sponsor")
            break
    if _CITIZEN.search(text):
        out.append("citizen_or_gc")
    if _CLEARANCE.search(text):
        out.append("clearance")
    if _ONSITE.search(text):
        out.append("onsite")
    return out


def level_of(title: str) -> str:
    for label, pattern in _LEVELS:
        if re.search(pattern, title or "", re.I):
            return label
    return "mid"


def _is_heading(line: str) -> bool:
    stripped = line.strip().strip("#*_ ").strip()
    return 2 < len(stripped) <= 70 and not stripped.startswith(("-", "•", "*", "·")) and not stripped.endswith((".", ";", ","))


def requirements_section(text: str) -> tuple[str, bool]:
    """The requirements section by heading, or ``(text, False)`` when none is found."""

    out: list[str] = []
    inside = False
    for line in text.split("\n"):
        if _is_heading(line):
            if _REQ_HEAD.search(line):
                inside = True
                continue
            if inside and _STOP_HEAD.search(line):
                inside = False
                continue
        if inside:
            out.append(line)
    section = "\n".join(out).strip()
    if len(section) < 80:
        return text, False
    return section, True


def techs_in(text: str) -> list[str]:
    return [label for label, pattern in _TECH_RE.items() if pattern.search(text)]


def _field(value: str | None, limit: int) -> str:
    return (value or "?").replace("|", "/").replace("\n", " ")[:limit]


def posting_digest(posting: DigestPosting, posting_id: str) -> str:
    """The one-line digest the ranker scores (see the module docstring)."""

    text = posting.text or ""
    section, found = requirements_section(text)
    years = [int(m.group(1)) for m in _YEARS_RE.finditer(section) if 0 < int(m.group(1)) <= 25]
    techs = techs_in(section)[:_MAX_TECHS]
    flags = constraint_flags(text)
    countries = ",".join(posting.countries or ()) or "?"
    title = _field(posting.title, _MAX_TITLE)
    parts = [
        posting_id,
        f"{title} @ {_field(posting.company, _MAX_TITLE)}",
        f"lvl={level_of(title)}",
        f"loc={_field(posting.location, _MAX_LOCATION)} [{countries}]",
        f"yrs={min(years)}+" if years else "yrs=?",
        "req=" + (", ".join(techs) if techs else "-") + ("" if found else " (req=full)"),
    ]
    if flags:
        parts.append("flags=" + ",".join(flags))
    return " | ".join(parts)


def resume_digest(resume_text: str, prefs: CandidatePrefs) -> str:
    """The compact candidate block the rank prompt carries instead of the resume."""

    techs = techs_in(resume_text)
    years = max((int(m.group(1)) for m in _YEARS_RE.finditer(resume_text[:600])), default=None)
    levels = list(dict.fromkeys(level_of(title) for title in prefs.titles))
    head = resume_text.split("\n\n", 3)
    summary = next((block for block in head if len(block) > 80), "")[:300].replace("\n", " ")
    location = (prefs.location or "").strip() or "unknown"
    return "\n".join([
        f"CANDIDATE: level={'/'.join(levels) or '?'}; {years or '?'}+ yrs; " + summary.strip(),
        "skills: " + (", ".join(techs) or "-"),
        "targets: " + ("; ".join(prefs.titles) or "unspecified"),
        "countries: " + (",".join(prefs.countries) or "any"),
        "needs visa sponsorship: " + ("yes" if prefs.visa_sponsorship_required else "no"),
        "location: " + location + (" (remote preferred)" if prefs.remote_preferred else ""),
    ])


def batch_lines(postings: Sequence[tuple[str, DigestPosting]]) -> list[str]:
    return [posting_digest(posting, posting_id) for posting_id, posting in postings]


__all__ = [
    "CandidatePrefs",
    "DIGEST_VERSION",
    "DigestPosting",
    "TECH",
    "batch_lines",
    "constraint_flags",
    "level_of",
    "posting_digest",
    "requirements_section",
    "resume_digest",
    "techs_in",
]
