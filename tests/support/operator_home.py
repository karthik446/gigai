"""0110-9-01: an OPERATOR-SIZED synthetic Scout home, for the read model's timing gate. Synthetic only.

The 0.1.10.7 PL6 gate was 5,000 postings over 10 boards. The operator's home
is ~290,000 postings over ~10,350 companies with 2 active profiles and ~600
matched postings, and what was slow there scales with the COMPANIES (index
files, watched boards) and the postings NOT matched, which the old gate had
none of. This builds that shape:

* ``companies`` Lever boards with a heavy-tailed size (a few boards with
  thousands of postings, most with a handful), ``postings`` in all, each
  board's body in the board cache and indexed by the product's own
  ``refresh_company`` (so the index files are the real format);
* every board on the watchlist, in ONE journal transition
  (``seed_watchlist_from_catalog`` with a made-up catalog);
* 2 active profiles with Staff titles, one of them the generic "Staff
  Engineer" next to function-specific ones (0110-8-05's veto reads the tag
  store per title), about 1 posting in ``match_every`` matched;
* a title-tag store with a rules tag for every distinct title;
* a few assessments (the fixture model, no network) and a "new since" anchor.

Nothing is requested and no real home is read: ``build`` refuses a root that
is not under the system's temporary directory, and every path is given.

Run it by hand (prints the build time and the counts as JSON)::

    uv run python -m tests.support.operator_home <root under $TMPDIR> [--postings N --companies N]

Build time on the worker's laptop (14 cores, 2026-10-03): see ``BUILD_NOTES``.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import time

OPERATOR_POSTINGS = 290_000
OPERATOR_COMPANIES = 10_350
#: About one posting in this many has a title a profile matches (290,000 / 480 = ~600 matched postings).
MATCH_EVERY = 480
#: The share of boards first observed inside the "new since" window.
RECENT_BOARD_EVERY = 10
ASSESSED = 40
#: A posting's description is about this many copies of ``_ABOUT`` (0.55 KB each): a real board body is several KB a posting.
ABOUT_COPIES = 7

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
OLD_SEEN = NOW - timedelta(days=20)
RECENT_SEEN = NOW - timedelta(days=2)
ANCHOR = NOW - timedelta(days=5)

DEFAULT_LABEL = "default"
SECOND_LABEL = "Platform track"
#: The default profile: two function-specific titles and the generic one (0110-8-05: a known function tag vetoes it).
DEFAULT_TITLES = ("Staff Software Engineer", "Staff AI Engineer", "Staff Engineer")
SECOND_TITLES = ("Staff Platform Engineer", "Staff Backend Engineer")

BUILD_NOTES = "290,000 postings x 10,350 companies: about 2 minutes on 14 cores (12 worker processes); 29,000 x 1,035: about 15 s."

SEAM_ENV = {
    "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP": "1",
    "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1",
    "GIGAI_SCOUT_AUTO_REFRESH": "0",
}

_MATCHED_TITLES = (
    "Staff Software Engineer, {team}",
    "Staff AI Engineer",
    "Staff Platform Engineer",
    "Staff Engineer",
    "Staff Backend Engineer, {team}",
    "Staff Software Engineer",
    "Staff AI Engineer, {team}",
    "Staff Engineer, {team}",
)
#: Titles the generic "Staff Engineer" rule passes and a known function tag vetoes, and near misses of every kind.
_LEVELS = ("", "Senior", "Lead", "Principal", "Junior", "Associate", "Distinguished", "Head of", "Director of", "VP of", "Sr.", "Founding")
#: One posting in ``VETO_EVERY``: a title the generic "Staff Engineer" rule passes and whose known function tag vetoes it.
VETO_EVERY = 97
_VETOED_TITLES = ("Staff Security Engineer", "Staff Security Engineer, {team}", "Staff Hardware Engineer", "Staff Hardware Engineer, {team}")
_ROLES = (
    "Account Executive", "Product Manager", "Software Engineer", "Data Scientist", "Security Engineer", "Recruiter",
    "Customer Success Manager", "Sales Development Representative", "Data Engineer", "Product Designer", "Financial Analyst",
    "Solutions Architect", "Technical Program Manager", "Marketing Manager", "Site Reliability Engineer", "Support Specialist",
    "Machine Learning Engineer", "Engineering Manager", "Business Analyst", "Operations Manager", "Legal Counsel",
    "Frontend Engineer", "DevOps Engineer", "Hardware Engineer", "Controller", "Content Strategist", "QA Engineer",
    "Research Scientist", "Implementation Consultant", "People Partner", "Accountant", "Network Engineer", "UX Researcher",
    "Partnerships Manager", "Field Engineer", "Mobile Engineer", "Revenue Operations Analyst", "Program Manager",
    "Clinical Specialist", "Warehouse Associate",
)
_TEAMS = tuple(
    f"{area} {kind}" for area in (
        "Payments", "Growth", "Core", "Identity", "Billing", "Search", "Risk", "Ads", "Maps", "Checkout", "Ledger", "Mobile",
        "Trust", "Supply", "Fleet", "Catalog", "Insights", "Messaging", "Storage", "Compute", "Network", "Pricing", "Onboarding",
        "Marketplace", "Compliance",
    ) for kind in ("Foundations", "Experience", "Infrastructure", "Tools", "Services", "Operations", "Systems", "Team")
)
_PLACES = (
    ("Remote - United States", "US", "remote"), ("New York, NY", "US", "hybrid"), ("San Francisco, CA", "US", "on-site"),
    ("Austin, TX", "US", "hybrid"), ("Remote - United States", "US", "remote"), ("London, United Kingdom", "GB", "hybrid"),
    ("Seattle, WA", "US", "on-site"), ("Berlin, Germany", "DE", "on-site"),
)
_ABOUT = (
    "About the role. You will own services end to end with a small team, from the first design note to the pager. "
    "What you will do: write and review Python and Go, run the services on Kubernetes, keep the Terraform honest, and "
    "work with product and design on what ships next. Requirements: 5+ years of Python in production; Kubernetes; "
    "Terraform; GCP experience is a plus; clear writing. Benefits: medical, dental and vision; a learning budget; "
    "flexible time off. We are an equal opportunity employer and consider every qualified applicant. "
)


class OperatorHomeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperatorHome:
    root: str
    home: str
    target: str
    postings: int
    companies: int
    matched_titles: int
    distinct_titles: int
    assessed: int
    profiles: dict[str, str]  # label -> profile id
    build_seconds: float

    @property
    def home_root(self) -> Path:
        return Path(self.home)

    @property
    def target_path(self) -> Path:
        return Path(self.target)

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def assert_synthetic_root(root: Path) -> None:
    """Refuse to build anywhere near real data: ``root`` must be under the system's temporary directory."""

    resolved = Path(root).resolve()
    allowed = [Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), Path("/private/tmp").resolve()]
    if not any(resolved.is_relative_to(base) for base in allowed):
        raise OperatorHomeError(f"the operator-sized home must be built under a temporary directory, not {resolved}")
    if resolved.name == ".gigai" or (resolved / ".gigai").exists():
        raise OperatorHomeError("refusing to build over a .gigai folder")


def board_sizes(postings: int, companies: int) -> list[int]:
    """A heavy tail that sums to ``postings``: board ``i`` has about ``k / (i + 1) ** 0.6`` postings, never fewer than 1."""

    weights = [(position + 1) ** -0.6 for position in range(companies)]
    spare = max(0, postings - companies)
    scale = spare / sum(weights)
    sizes = [1 + int(weight * scale) for weight in weights]
    short = postings - sum(sizes)
    for position in range(max(0, short)):
        sizes[position % companies] += 1
    return sizes


def slug_of(board: int) -> str:
    return f"op{board:05d}co"


def title_of(number: int, match_every: int = MATCH_EVERY) -> str:
    """Posting ``number``'s title (a global counter): one in ``match_every`` is a title a profile matches."""

    team = _TEAMS[(number // 7) % len(_TEAMS)]
    if number % match_every == 0:
        return _MATCHED_TITLES[(number // match_every) % len(_MATCHED_TITLES)].format(team=team)
    if number % VETO_EVERY == 0:
        return _VETOED_TITLES[(number // VETO_EVERY) % len(_VETOED_TITLES)].format(team=team)
    level = _LEVELS[(number // 3) % len(_LEVELS)]
    role = _ROLES[(number * 7 + number // 11) % len(_ROLES)]
    base = f"{level} {role}".strip()
    return f"{base}, {team}" if number % 5 else base


def _job(slug: str, position: int, number: int, seen: datetime, match_every: int) -> dict[str, object]:
    place, country, mode = _PLACES[number % len(_PLACES)]
    title = title_of(number, match_every)
    if number % match_every == 0:
        place, country, mode = _PLACES[0]  # a matched title is never dropped by the country or work-mode rule
    posting_id = f"{slug}-{position:05d}"
    return {
        "id": posting_id,
        "text": title,
        "hostedUrl": f"https://jobs.lever.co/{slug}/{posting_id}",
        "categories": {"location": place},
        "country": country,
        "workplaceType": mode,
        "descriptionPlain": f"Posting {number} at {slug}. " + _ABOUT * ABOUT_COPIES,
        "createdAt": int((seen - timedelta(days=number % 5)).timestamp() * 1000),
    }


def _seen_at(board: int) -> datetime:
    """When board ``board`` was first observed: one in ``RECENT_BOARD_EVERY`` inside the "new" window, each at its own minute
    (so a page of the newest postings is spread over many companies, as it is on a real home)."""

    base = RECENT_SEEN if board % RECENT_BOARD_EVERY == 0 else OLD_SEEN
    return base - timedelta(minutes=(board * 37) % 1440)


def _build_boards(args: tuple[str, int, int, int, list[tuple[int, int, int]]]) -> tuple[int, list[str]]:
    """One worker's share: each board's body into the cache, then indexed by the product's own ``refresh_company``."""

    home, match_every, _companies, _postings, boards = args
    from gigai.scout.find_jobs.ats_board_clients import BoardCache
    from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company

    home_root = Path(home)
    cache = BoardCache(home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    index = CompanyIndex.for_home(home_root)
    titles: set[str] = set()
    written = 0
    for board, first_number, size in boards:
        slug = slug_of(board)
        seen = _seen_at(board)
        jobs = [_job(slug, position, first_number + position, seen, match_every) for position in range(size)]
        titles.update(str(job["text"]) for job in jobs)
        cache.store("lever", board_list_url("lever", slug), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
        refresh_company(index, cache, ats="lever", slug=slug, observed_at=index_stamp(seen))
        written += size
    return written, sorted(titles)


@dataclass(frozen=True)
class _Catalog:
    records: tuple[object, ...]
    revision: str = "operator-home-1"
    digest: str = "sha256:" + "0" * 64


def build(
    root: Path, *, postings: int = OPERATOR_POSTINGS, companies: int = OPERATOR_COMPANIES, match_every: int = MATCH_EVERY,
    assessed: int = ASSESSED, workers: int | None = None, log=lambda _line: None,
) -> OperatorHome:
    """Create the home under ``root`` (must not exist yet, under a temporary directory) and fill it. No request, no real model."""

    root = Path(root)
    assert_synthetic_root(root)
    if root.exists() and any(root.iterdir()):
        raise OperatorHomeError(f"{root} is not empty")
    started = time.monotonic()
    os.environ.update(SEAM_ENV)

    from click.testing import CliRunner

    from gigai.cli import cli
    from gigai.scout import postings as read_model, profile_records
    from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME
    from gigai.scout.find_jobs.company_catalog import CompanyRecord
    from gigai.scout.find_jobs.contracts import ATSProvider
    from gigai.scout.find_jobs.posting_tags import default_store, tag_new_titles
    from gigai.scout.find_jobs.watchlist import seed_watchlist_from_catalog
    from gigai.workpad import resolve_workpad

    home, target = root / "home", root / "home" / "scout"
    target.mkdir(parents=True)
    runner = CliRunner()

    def gigai(*args: str) -> None:
        result = runner.invoke(cli, list(args))
        if result.exit_code != 0:
            raise OperatorHomeError(f"gigai {' '.join(args[:3])} failed: {result.output[-600:]}")

    gigai(
        "setup", "--non-interactive", "--home", str(home), "--workpad-root", str(root / "workpads"), "--editor", "/usr/bin/true",
        "--endpoint", "ollama_local=ollama_local:http://127.0.0.1:11434",
        "--model-target", f"ollama_local=ollama_local:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
        "--create-model-target", "ollama_local", "--json",
    )
    gigai("init", "--home", str(home), "--target", str(target), "--username", "operator-sized", "--json")
    resume = root / "resume.md"
    resume.write_text(
        "# Rowan Vale\n\nStaff engineer. Python services on Kubernetes for nine years; Terraform; platform and ML infrastructure.\n\n"
        "## Experience\n\n- Led the inference platform team at a logistics company.\n- Ran the Kubernetes migration for 40 services.\n",
        encoding="utf-8",
    )
    gigai("scout", "resume", "add", str(resume), "--home", str(home), "--target", str(target), "--json")
    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(roles=list(DEFAULT_TITLES), merged_queries=list(DEFAULT_TITLES), sources={"exa": False, "ats": False, "hiringcafe": False})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    log(f"gig ready: {time.monotonic() - started:.1f} s")

    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    default = profile_records.selected_profile(resolved, home_root=home, target=target)
    second = profile_records.create_profile(
        resolved, label=SECOND_LABEL, titles=SECOND_TITLES, titles_to_avoid=(), queries=SECOND_TITLES[:1], resume_ref=default.resume_ref,
    )

    sizes = board_sizes(postings, companies)
    plan: list[tuple[int, int, int]] = []
    number = 0
    for board, size in enumerate(sizes):
        plan.append((board, number, size))
        number += size
    count = max(1, min(workers or (os.cpu_count() or 2) - 2, 12))
    # Round-robin, so every worker gets big and small boards alike.
    shares = [(str(home), match_every, companies, postings, plan[offset::count]) for offset in range(count)]
    titles: set[str] = set()
    if count == 1:
        results = [_build_boards(shares[0])]
    else:
        with ProcessPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(_build_boards, shares))
    for _written, found in results:
        titles.update(found)
    log(f"{sum(written for written, _ in results)} postings over {companies} boards indexed: {time.monotonic() - started:.1f} s")

    records = tuple(
        CompanyRecord(
            name=f"Operator Company {board:05d}", provider=ATSProvider.LEVER, board_token=slug_of(board),
            board_url=f"https://jobs.lever.co/{slug_of(board)}", hq_country="US",
        )
        for board in range(companies)
    )

    class _Prefs:
        countries = ()
        exclude_companies = ()
        watch_companies = ()

    seeded = seed_watchlist_from_catalog(home, target, prefs=_Prefs(), catalog=_Catalog(records))
    if seeded.added != companies:
        raise OperatorHomeError(f"the watchlist took {seeded.added} of {companies} boards")
    log(f"watchlist seeded: {time.monotonic() - started:.1f} s")

    store = default_store(home)
    try:
        tag_new_titles(store, sorted(titles))
    finally:
        store.close()
    log(f"{len(titles)} distinct titles tagged: {time.monotonic() - started:.1f} s")

    # The setup preferences, as the wizard saves them (without them the UI opens the setup wizard, not Jobs). Saved AFTER
    # the watchlist is in place and never followed by an update here: an update with preferences seeds the bundled catalog.
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend

    ScoutFindJobsBackend(home_root=home, target=target).write_setup({
        "roles": list(DEFAULT_TITLES), "titles_to_avoid": [], "countries": ["US"], "work_mode": "any", "city": None,
        "visa_sponsorship_required": False, "exclude_companies": [], "watch_companies": [], "company_stage_size": None,
        "industries_include": [], "industries_exclude": [], "must_have_stack": [], "dealbreaker_stack": [], "cadence_days": 7,
        "budget_usd_per_session": 0.5,
    })
    log(f"setup preferences saved: {time.monotonic() - started:.1f} s")

    done = _assess_some(home, target, plan, match_every, default.profile_id, assessed) if assessed else 0
    anchor_store = read_model.open_store(home, target)
    try:
        anchor_store.advance_anchor(read_model.stamp(ANCHOR), set_by="scout_new")
    finally:
        anchor_store.close()
    matched = sum(1 for value in range(0, postings, match_every))
    return OperatorHome(
        root=str(root), home=str(home), target=str(target), postings=postings, companies=companies, matched_titles=matched,
        distinct_titles=len(titles), assessed=done, profiles={DEFAULT_LABEL: default.profile_id, SECOND_LABEL: second.profile_id},
        build_seconds=round(time.monotonic() - started, 1),
    )


def _assess_some(home: Path, target: Path, plan: list[tuple[int, int, int]], match_every: int, profile_id: str, wanted: int) -> int:
    """Assess the first ``wanted`` matched postings for the default profile through the job page's own path (the fixture model)."""

    from gigai.scout.postings import PostingText
    from gigai.scout.scout_new import _assess

    pairs: list[tuple[str, str]] = []
    texts: dict[str, PostingText] = {}
    for board, first_number, size in plan:
        first = -(-first_number // match_every) * match_every
        for number in range(first, first_number + size, match_every):
            title = title_of(number, match_every)
            if "Platform" in title.split(",")[0] or "Backend" in title.split(",")[0]:
                continue  # the second profile's titles
            slug = slug_of(board)
            job = _job(slug, number - first_number, number, _seen_at(board), match_every)
            url = str(job["hostedUrl"])
            texts[url] = PostingText(title, slug, "Remote - United States", url, str(job["descriptionPlain"]), "remote", None)
            pairs.append((url, profile_id))
            break  # one posting a board: the assessed rows are spread over the companies
        if len(pairs) >= wanted:
            break
    result = _assess(pairs, texts, home_root=home, target=target, config=None)
    return int(result["assessed"])  # type: ignore[call-overload]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--postings", type=int, default=OPERATOR_POSTINGS)
    parser.add_argument("--companies", type=int, default=OPERATOR_COMPANIES)
    parser.add_argument("--assessed", type=int, default=ASSESSED)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    assert_synthetic_root(args.root)
    os.environ["HOME"] = str(args.root)  # nothing under the real home is ever the default
    built = build(
        args.root, postings=args.postings, companies=args.companies, assessed=args.assessed, workers=args.workers,
        log=lambda line: print(line, file=sys.stderr),
    )
    (args.root / "operator-home.json").write_text(json.dumps(built.to_json(), indent=2), encoding="utf-8")
    print(json.dumps(built.to_json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
