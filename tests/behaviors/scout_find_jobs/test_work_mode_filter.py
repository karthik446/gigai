"""uat-bug-028: work mode + location are filters (N37).

The config stored only ``remote: bool`` and the starter placeholder
location; the wizard's four modes collapsed into ``remote``; nothing in the
search read either. Seen: a Roku San Jose posting (hybrid, per its text)
under Remote-only.

Pinned here, on real-shaped posting rows (location strings from the
company-index sample in the uat-bug-028 worker notes):

* a posting's work mode: the board field (``board``), else the location
  text (``derived``), else ``unknown``;
* the operator's semantics per mode: Remote-only, Hybrid + area, Onsite +
  area, Any -- the Roku San Jose posting is out under Remote-only; the
  Reddit "Remote - United States" Greenhouse posting is in, as a derived
  remote; an unknown mode is kept and labelled;
* area: the metro alias table (Denver finds Boulder, not Aurora, IL), the
  state as the fallback for a city the table does not know;
* the index search drops before ranking/the import cap and counts it;
  acquire's drop loop buckets it as ``work_mode_mismatch``;
* config: ``work_mode`` round-trips additively (an old file digests as
  before); a file without it takes the setup's saved answer (prefs.json),
  else Any -- ``remote: true`` alone never filters (the old wizard saved it
  for Any too); an explicit ``work_mode`` beats the saved answer; the
  placeholder location reads as none and is refused on save;
  ``PUT /api/setup`` stores all four modes;
* the results read carries each row's ``work_mode_fit``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api.server import SetupValidationError
from gigai.scout.find_jobs.api.setup import _validate_setup_body
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsConfig,
    FindJobsContractError,
    PostingRow,
    SourceKind,
    SourceToggles,
    WorkMode,
    WorkModePreference,
    normalize_url,
)
from gigai.scout.find_jobs.effective_config import with_saved_work_mode
from gigai.scout.find_jobs.work_mode import derive_work_mode, in_area, parse_area, work_mode_fit
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG

from tests.behaviors.scout_find_jobs import test_index_search as index_harness
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

PLACEHOLDER = "REPLACE_WITH_YOUR_LOCATION (e.g. Denver, CO, or null for any)"


def _config(work_mode: str | None, location: str | None = None, *, remote: bool = False) -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location=location,
        remote=remote,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
        countries=("US",),
        work_mode=None if work_mode is None else WorkModePreference(work_mode),
    )


def _row(company: str, location: str, *, provider: ATSProvider = ATSProvider.GREENHOUSE, board: WorkMode | None = None, job: str = "1") -> PostingRow:
    url = f"https://boards.greenhouse.io/{company}/jobs/{job}" if provider is ATSProvider.GREENHOUSE else f"https://jobs.{provider.value}.co/{company}/{job}"
    return PostingRow(
        url=url,
        normalized_url=normalize_url(url),
        provider=provider,
        board_token=company,
        company=company,
        title="Senior Software Engineer",
        location=location,
        published_at="2026-09-20T00:00:00Z",
        content_sha256=None,
        source_kind=SourceKind.ATS,
        query_key=f"ats:{provider.value}:{company}",
        work_mode=board,
    )


# Real-shaped rows. Roku's San Jose postings say "Hybrid" only in the text;
# the Greenhouse location is the plain city.
ROKU_SAN_JOSE = _row("roku", "San Jose, California", job="7931239")
ROKU_SAN_JOSE_HYBRID = _row("roku", "San Jose, California (Hybrid)", job="7931240")
REDDIT_REMOTE_US = _row("reddit", "Remote - United States", job="7243312")
G2I_ASHBY_REMOTE = _row("g2i", "United States", provider=ATSProvider.ASHBY, board=WorkMode.REMOTE)
LEVER_DENVER_HYBRID = _row("initech", "Denver, CO", provider=ATSProvider.LEVER, board=WorkMode.HYBRID)
LEVER_BOULDER_ONSITE = _row("initech", "Boulder, CO", provider=ATSProvider.LEVER, board=WorkMode.ONSITE, job="2")
GH_BOULDER_PLAIN = _row("acme", "Boulder, Colorado", job="3")
GH_AURORA_IL_HYBRID = _row("acme", "Aurora, IL - Hybrid", job="4")
GH_AUSTIN_HYBRID = _row("acme", "Austin, TX (Hybrid)", job="5")
GH_UNITED_STATES = _row("acme", "United States", job="6")
GH_EMPTY = _row("acme", "", job="7")


@pytest.mark.parametrize(
    ("location", "board", "mode", "source"),
    [
        ("Remote", None, "remote", "derived"),
        ("Remote - US", None, "remote", "derived"),
        ("Remote - United States", None, "remote", "derived"),
        ("Remote Anywhere in the World", None, "remote", "derived"),
        ("NY, SF or Remote ", None, "remote", "derived"),
        ("San Francisco, US (Hybrid)", None, "hybrid", "derived"),
        ("Denver, CO - Hybrid; New York, NY - Hybrid", None, "hybrid", "derived"),
        ("Burlington, MA | Hybrid", None, "hybrid", "derived"),
        ("Los Angeles, CA (On-site)", None, "onsite", "derived"),
        ("Berlin Office", None, "onsite", "derived"),
        ("Emeryville, California, United States; On-site", None, "onsite", "derived"),
        ("San Jose, California", None, "in_person", "derived"),
        ("Washington, DC", None, "in_person", "derived"),
        ("Gaithersburg, MD 20877 | 39.143095973 | -77.189152539", None, "in_person", "derived"),
        ("United States", None, "unknown", "none"),
        ("AMER", None, "unknown", "none"),
        ("North Carolina", None, "unknown", "none"),
        ("", None, "unknown", "none"),
        # The board's own field always wins over the text.
        ("Remote", WorkMode.HYBRID, "hybrid", "board"),
        ("Denver, CO", WorkMode.REMOTE, "remote", "board"),
    ],
)
def test_a_postings_work_mode_is_the_board_field_else_the_location_text(location, board, mode, source) -> None:
    derived = derive_work_mode(location, board)
    assert (derived.mode, derived.source) == (mode, source)


def _passing(config: FindJobsConfig, rows: list[PostingRow]) -> list[PostingRow]:
    return [row for row in rows if work_mode_fit(row, config).passes]


ALL_ROWS = [
    ROKU_SAN_JOSE,
    ROKU_SAN_JOSE_HYBRID,
    REDDIT_REMOTE_US,
    G2I_ASHBY_REMOTE,
    LEVER_DENVER_HYBRID,
    LEVER_BOULDER_ONSITE,
    GH_BOULDER_PLAIN,
    GH_AURORA_IL_HYBRID,
    GH_AUSTIN_HYBRID,
    GH_UNITED_STATES,
    GH_EMPTY,
]


def test_remote_only_keeps_remote_postings_and_the_unknown_ones_labelled() -> None:
    config = _config("remote")
    assert _passing(config, ALL_ROWS) == [REDDIT_REMOTE_US, G2I_ASHBY_REMOTE, GH_UNITED_STATES, GH_EMPTY]
    roku = work_mode_fit(ROKU_SAN_JOSE, config)
    assert roku.passes is False and roku.mode == "in_person"
    assert work_mode_fit(ROKU_SAN_JOSE_HYBRID, config).passes is False
    unknown = work_mode_fit(GH_UNITED_STATES, config)
    assert unknown.to_json() == {"mode": "unknown", "source": "none", "preference": "remote", "area": None, "in_area": None, "passes": True}


def test_the_reddit_remote_us_posting_is_a_derived_remote_and_passes_remote_only() -> None:
    fit = work_mode_fit(REDDIT_REMOTE_US, _config("remote"))
    assert fit.to_json() == {"mode": "remote", "source": "derived", "preference": "remote", "area": None, "in_area": None, "passes": True}
    # The row itself is untouched: the derived mode is never a board field.
    assert REDDIT_REMOTE_US.work_mode is None and "work_mode" not in REDDIT_REMOTE_US.to_json()
    assert work_mode_fit(G2I_ASHBY_REMOTE, _config("remote")).source == "board"


def test_hybrid_with_an_area_keeps_remote_plus_hybrid_in_that_area() -> None:
    config = _config("hybrid", "Denver, CO")
    assert _passing(config, ALL_ROWS) == [
        REDDIT_REMOTE_US,
        G2I_ASHBY_REMOTE,
        LEVER_DENVER_HYBRID,
        GH_BOULDER_PLAIN,
        GH_UNITED_STATES,
        GH_EMPTY,
    ]
    # An on-site posting in the area is out under Hybrid (in under Onsite).
    assert work_mode_fit(LEVER_BOULDER_ONSITE, config).passes is False
    assert work_mode_fit(LEVER_DENVER_HYBRID, config).to_json() == {
        "mode": "hybrid", "source": "board", "preference": "hybrid", "area": "Denver", "in_area": True, "passes": True,
    }
    boulder = work_mode_fit(GH_BOULDER_PLAIN, config)
    assert (boulder.mode, boulder.area, boulder.in_area) == ("in_person", "Denver", True)
    assert work_mode_fit(GH_AUSTIN_HYBRID, config).in_area is False


def test_onsite_with_an_area_keeps_remote_hybrid_and_onsite_in_that_area() -> None:
    config = _config("onsite", "Denver, CO")
    assert _passing(config, ALL_ROWS) == [
        REDDIT_REMOTE_US,
        G2I_ASHBY_REMOTE,
        LEVER_DENVER_HYBRID,
        LEVER_BOULDER_ONSITE,
        GH_BOULDER_PLAIN,
        GH_UNITED_STATES,
        GH_EMPTY,
    ]


def test_hybrid_or_onsite_without_an_area_filters_on_the_mode_alone() -> None:
    hybrid = _passing(_config("hybrid"), ALL_ROWS)
    assert LEVER_BOULDER_ONSITE not in hybrid and GH_AUSTIN_HYBRID in hybrid and ROKU_SAN_JOSE in hybrid
    assert _passing(_config("onsite"), ALL_ROWS) == ALL_ROWS


def test_any_keeps_everything_in_the_countries() -> None:
    assert _passing(_config("any", "Denver, CO"), ALL_ROWS) == ALL_ROWS
    assert work_mode_fit(ROKU_SAN_JOSE, _config("any")).area is None


def test_a_hybrid_posting_with_no_place_is_kept_and_labelled_area_not_stated() -> None:
    fit = work_mode_fit(_row("acme", "Hybrid", job="9"), _config("hybrid", "Denver, CO"))
    assert (fit.passes, fit.area, fit.in_area) == (True, "Denver", None)


def test_the_metro_alias_table_matches_nearby_cities_and_checks_the_state() -> None:
    denver = parse_area("Denver, CO")
    assert denver is not None and denver.label == "Denver"
    assert in_area("Boulder, CO", denver) is True
    assert in_area("Aurora, Colorado", denver) is True
    assert in_area("Aurora, IL", denver) is False
    assert in_area("Colorado Springs, CO", denver) is False
    bay = parse_area("San Jose, CA")
    assert in_area("Palo Alto, California", bay) is True
    assert in_area("San Jose, Costa Rica", bay) is False
    dc = parse_area("Washington, DC")
    assert in_area("Reston, VA", dc) is True and in_area("Seattle, Washington", dc) is False
    assert in_area("London, United Kingdom", parse_area("London")) is True
    assert in_area("United States", denver) is None


def test_the_state_is_the_fallback_for_a_city_the_table_does_not_know() -> None:
    pueblo = parse_area("Pueblo, CO")
    assert pueblo is not None and pueblo.state_fallback == "CO"
    assert in_area("Pueblo, CO", pueblo) is True
    assert in_area("Colorado Springs, CO", pueblo) is True
    assert in_area("Austin, TX", pueblo) is False
    colorado = parse_area("Colorado")
    assert colorado is not None and in_area("Grand Junction, CO", colorado) is True
    fit = work_mode_fit(_row("acme", "Colorado Springs, CO - Hybrid", job="10"), _config("hybrid", "Pueblo, CO"))
    assert (fit.passes, fit.in_area, fit.area) == (True, True, "Pueblo")


# --- config ---------------------------------------------------------------------


def test_work_mode_is_an_additive_config_key() -> None:
    old = _config(None, remote=True)
    assert "work_mode" not in old.to_json()
    assert FindJobsConfig.from_json(old.to_json()).digest() == old.digest()
    for mode in ("remote", "hybrid", "onsite", "any"):
        config = _config(mode, "Denver, CO")
        assert config.to_json()["work_mode"] == mode
        assert FindJobsConfig.from_json(config.to_json()) == config
    with pytest.raises(FindJobsContractError):
        FindJobsConfig.from_json({**_config(None).to_json(), "work_mode": "sometimes"})


def test_an_old_remote_flag_alone_never_filters() -> None:
    assert _config(None, remote=True).effective_work_mode is WorkModePreference.ANY
    assert _config(None, remote=False).effective_work_mode is WorkModePreference.ANY
    assert _config("hybrid", remote=True).effective_work_mode is WorkModePreference.HYBRID
    assert work_mode_fit(ROKU_SAN_JOSE, _config(None, remote=True)).passes is True
    # The saved answer fills a missing work_mode; a file's own always wins.
    assert with_saved_work_mode(_config(None, remote=True), WorkModePreference.REMOTE).work_mode is WorkModePreference.REMOTE
    assert with_saved_work_mode(_config("hybrid"), WorkModePreference.REMOTE).work_mode is WorkModePreference.HYBRID
    assert with_saved_work_mode(_config(None), None).work_mode is None


def test_the_placeholder_location_reads_as_none() -> None:
    stored = {**_config(None, remote=True).to_json(), "location": PLACEHOLDER}
    config = FindJobsConfig.from_json(stored)
    assert config.location is None
    assert parse_area(PLACEHOLDER) is None
    # The starter no longer writes it.
    assert STARTER_FIND_JOBS_CONFIG.location is None


def test_the_placeholder_city_is_refused_on_save() -> None:
    body = _setup_body("hybrid", PLACEHOLDER)
    with pytest.raises(SetupValidationError) as refused:
        _validate_setup_body(body)
    assert "placeholder" in refused.value.field_errors["city"]
    assert _validate_setup_body(_setup_body("hybrid", "  "))["city"] is None


# --- index search + acquire drop loop ---------------------------------------------


class _ModeBoards(index_harness._Boards):
    """The index harness's boards with real-shaped locations and Lever workplaceType."""

    def __init__(self) -> None:
        super().__init__()
        self.greenhouse = [
            {"id": 21, "title": "Senior Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/21", "location": {"name": "San Jose, California"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 22, "title": "Senior Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/22", "location": {"name": "Remote - United States"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 23, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/23", "location": {"name": "United States"}, "updated_at": "2026-09-20T00:00:00Z"},
        ]
        self.lever = [
            {"id": "lev-h", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-h", "categories": {"location": "Boulder, CO"}, "country": "US", "workplaceType": "hybrid", "createdAt": 1790000000000, "descriptionPlain": "Build things."},
            {"id": "lev-o", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-o", "categories": {"location": "Denver, CO"}, "country": "US", "workplaceType": "onsite", "createdAt": 1790000000000, "descriptionPlain": "Build things."},
            {"id": "lev-r", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-r", "categories": {"location": "Austin, TX"}, "country": "US", "workplaceType": "remote", "createdAt": 1790000000000, "descriptionPlain": "Build things."},
        ]


def _search_urls(tmp_path: Path, work_mode: str, location: str | None = None) -> tuple[list[str], dict[str, object]]:
    boards = _ModeBoards()
    base = index_harness._us_config()
    index_harness._update(tmp_path, boards, config=base)
    config = replace(base, work_mode=WorkModePreference(work_mode), location=location)
    rows, failures, summary = index_harness._search(tmp_path, config=config, remember_search=False)
    assert failures == []
    return sorted(row.url for row in rows), summary


def test_the_index_search_filters_remote_only_before_ranking(tmp_path: Path) -> None:
    urls, summary = _search_urls(tmp_path, "remote")
    assert urls == [
        "https://boards.greenhouse.io/acme/jobs/22",  # Remote - United States (derived)
        "https://boards.greenhouse.io/acme/jobs/23",  # United States: unknown, kept
        "https://jobs.lever.co/initech/lev-r",  # the board says remote
    ]
    assert summary["work_mode_filtered_out"] == 3
    assert summary["matched"] == 3


def test_the_index_search_filters_hybrid_and_onsite_in_the_area(tmp_path: Path) -> None:
    urls, summary = _search_urls(tmp_path, "hybrid", "Denver, CO")
    assert "https://jobs.lever.co/initech/lev-h" in urls  # hybrid, Boulder is Denver's metro
    assert "https://jobs.lever.co/initech/lev-o" not in urls  # on-site is out under Hybrid
    assert "https://boards.greenhouse.io/acme/jobs/21" not in urls  # San Jose is not Denver
    onsite, _ = _search_urls(tmp_path / "onsite", "onsite", "Denver, CO")
    assert "https://jobs.lever.co/initech/lev-o" in onsite and "https://boards.greenhouse.io/acme/jobs/21" not in onsite
    anything, summary_any = _search_urls(tmp_path / "any", "any")
    assert len(anything) == 6 and summary_any["work_mode_filtered_out"] == 0


# --- PUT /api/setup + the results read --------------------------------------------


def _setup_body(work_mode: str, city: str | None) -> dict[str, object]:
    return {
        "roles": ["Staff AI Engineer"],
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": work_mode,
        "city": city,
        "visa_sponsorship_required": False,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.5,
        "max_age_days": 60,
    }


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture
def client(fx: ProfileFixtureGig):
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as http:
            yield http
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_setup_save_stores_all_four_modes_without_collapsing(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    for mode, city in (("remote", None), ("hybrid", "Denver, CO"), ("onsite", "Pueblo, CO"), ("any", None)):
        response = client.put("/api/setup", json=_setup_body(mode, city))
        assert response.status_code == 200, response.text
        config = FindJobsConfig.from_json(json.loads((fx.target / "find-jobs.json").read_text()))
        assert config.work_mode is WorkModePreference(mode)
        assert config.remote is (mode == "remote")
        assert config.location == city
        assert response.json()["prefs"]["work_mode"] == mode
        served = client.get("/api/config").json()["config"]
        assert served["work_mode"] == mode


def test_the_setup_save_refuses_the_placeholder_city(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    before = (fx.target / "find-jobs.json").read_bytes()
    response = client.put("/api/setup", json=_setup_body("hybrid", PLACEHOLDER))
    assert response.status_code == 400
    assert "placeholder" in response.json()["error"]["field_errors"]["city"]
    assert (fx.target / "find-jobs.json").read_bytes() == before


def test_the_setup_prefill_reads_an_old_config_and_drops_the_placeholder(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    path = fx.target / "find-jobs.json"
    stored = json.loads(path.read_text())
    stored.pop("work_mode", None)
    path.write_text(json.dumps({**stored, "remote": True, "location": PLACEHOLDER}))
    response = client.get("/api/setup")
    assert response.status_code == 404
    prefill = response.json()["error"]["prefill"]
    assert prefill["work_mode"] == "any" and prefill["city"] is None
    assert client.get("/api/config").json()["config"]["location"] is None


def test_a_results_row_carries_why_it_passed() -> None:
    from types import SimpleNamespace

    from gigai.scout.find_jobs.api.run_reads import RunView
    from gigai.scout.find_jobs.contracts import PostingRowResult, RowOutcome

    rows = (PostingRowResult(REDDIT_REMOTE_US, RowOutcome.NEW), PostingRowResult(LEVER_DENVER_HYBRID, RowOutcome.NEW))
    evidence = SimpleNamespace(
        acquire_output=SimpleNamespace(rows=rows, carried_forward_assessments=()),
        assess_output=None,
        run_input=SimpleNamespace(config=_config("hybrid", "Denver, CO")),
    )
    view = RunView(evidence, scores={})
    fits = {row.posting.company: view.row_json(row, text=False)["work_mode_fit"] for row in view.rows}
    assert fits["reddit"] == {"mode": "remote", "source": "derived", "preference": "hybrid", "area": None, "in_area": None, "passes": True}
    assert fits["initech"]["area"] == "Denver" and fits["initech"]["in_area"] is True
    # A run with no sealed input yet: no fit, never an error.
    bare = RunView(SimpleNamespace(acquire_output=SimpleNamespace(rows=rows, carried_forward_assessments=()), assess_output=None, run_input=None), scores={})
    assert "work_mode_fit" not in bare.row_json(bare.rows[0], text=False)


def test_acquire_drops_rows_outside_the_work_mode_before_the_import(tmp_path: Path) -> None:
    from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
    from gigai.scout.find_jobs.contracts import NotAssessedReason
    from gigai.scout.find_jobs.market_acquisition import acquire_node

    from tests.behaviors.scout_find_jobs import test_import_bound as bound

    workpad, project_id, gig_id = bound._managed_workpad(tmp_path, "uat-bug-028-acquire")
    remote = bound._row(0, location="Remote - United States")
    city = bound._row(1, location="San Jose, California")
    unknown = bound._row(2, location="United States")
    config = replace(bound._config(ats=False), work_mode=WorkModePreference.REMOTE)

    out = acquire_node(
        bound._context(workpad, project_id, gig_id, "run_01", operation_key="acquire-run_01"),
        bound._input(config, (remote, city, unknown)),
        http_client=None, exa=bound._Exa(), ats=ATSBoardClients(), watchlist=bound._Watchlist(()),
    )

    assert {row.posting.normalized_url for row in out.rows} == {remote.normalized_url, unknown.normalized_url}
    assert {item.reason: item.count for item in out.dropped_counts} == {NotAssessedReason.WORK_MODE_MISMATCH: 1}


# --- a config without work_mode: the setup's saved answer, else Any ---------------


def _strip_work_mode(fx: ProfileFixtureGig, **changes: object) -> None:
    """Make find-jobs.json an older file: no work_mode, ``remote: true``."""

    path = fx.target / "find-jobs.json"
    stored = json.loads(path.read_text())
    stored.pop("work_mode", None)
    path.write_text(json.dumps({**stored, "remote": True, **changes}))


def _served_work_mode(client: httpx.Client) -> str | None:
    response = client.get("/api/config")
    assert response.status_code == 200, response.text
    return response.json()["config"].get("work_mode")


def test_an_old_config_takes_the_saved_remote_only(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    assert client.put("/api/setup", json=_setup_body("remote", None)).status_code == 200
    _strip_work_mode(fx)
    assert _served_work_mode(client) == "remote"


def test_an_old_config_takes_the_saved_any(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    assert client.put("/api/setup", json=_setup_body("any", None)).status_code == 200
    _strip_work_mode(fx)
    # remote: true in the file (what the old wizard wrote for Any) is not Remote-only.
    assert _served_work_mode(client) == "any"


def test_an_old_config_with_no_saved_answer_is_any(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    _strip_work_mode(fx)
    assert client.get("/api/setup").status_code == 404  # nothing saved
    assert _served_work_mode(client) is None
    config = FindJobsConfig.from_json(json.loads((fx.target / "find-jobs.json").read_text()))
    assert config.effective_work_mode is WorkModePreference.ANY


def test_an_explicit_work_mode_in_the_config_beats_the_saved_answer(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    assert client.put("/api/setup", json=_setup_body("remote", None)).status_code == 200
    _strip_work_mode(fx, work_mode="hybrid", location="Denver, CO")
    assert _served_work_mode(client) == "hybrid"
