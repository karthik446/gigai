"""0.1.11.2 RANKVIS: the rank row is ALWAYS on the Jobs page. A REAL Scout server, a real browser, no route stub.

The local release check found no "Rank now" / "Re-rank latest 100" / "ranked X of Y" anywhere on the Jobs page of a
home whose in-window postings were all ranked. So each case here is its own real server (`serve()` with the real
backend and the built `ui/dist`, in this process, on a free port) on a synthetic home whose state the test makes:
postings of the last 7 days (`PostingsFixture.seed`), the scripted rank model, NO background runner (nothing ranks
unless the test or a click asks, so "not ranked" stays not ranked until the click). The page's requests are the
server's own answers: nothing is routed, fulfilled or rewritten in the three cases the release check names.

Pinned:
- ALL RANKED: the row is there, "Ranked N of N (last 7 days)"; "Rank now" is greyed and says why in its own text
  ("Rank now: nothing to rank") and in its title; "Re-rank latest 100" is on and opens the cost dialog (postings,
  calls, today's count) with no model call; Cancel leaves the count;
- RANKING OFF (`PUT /api/settings/background` `rank.enabled` false, the real switch): the row is there, both buttons
  are greyed and say "ranking is off", and the line says how to turn it on;
- SOME NOT RANKED: "Ranked 0 of N (last 7 days) · N not ranked yet"; "Rank now" is on, a click ranks them (the
  scripted model) and the row ends at "Ranked N of N", as the server's own ranking block does;
- no line says rows are hidden or offers "show"; the ranked-low postings stand under a plain "Ranked low (N)" divider
  inside the list;
- THE LIST COULD NOT BE READ (the one case no real server makes: `GET /api/postings` is cut for the page): the row is
  still there and says "Ranking status unavailable", never nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import urllib.request

import pytest

from tests.ui.conftest import _ui_session
from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui

ROUTE = "/api/postings/rank"
PANEL, LINE, NOTICE = tid("rank-panel"), tid("rank-status-line"), tid("rank-notice")
RANK_NOW, RERANK, DIALOG = tid("rank-now"), tid("rerank-latest"), tid("rerank-dialog")
HOW_TO = 'set "rank": {"enabled": true}'
_POSTING_LINE = re.compile(r"^(p\d+) \| ", re.MULTILINE)


@dataclass
class RankServer:
    """What `conftest._ui_session` needs of a server (`url`, `pid`, `log_path`), plus the home and the model behind it."""

    url: str
    pid: int
    log_path: str | None
    fx: object
    model: object


@pytest.fixture
def rank_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RankServer]:
    """A real server on a synthetic home with two active profiles and the scripted rank model; no background runner."""

    from gigai.adapters.port import InvocationResult, NormalizedUsage
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve
    from gigai.scout.pipeline import rank_now
    from tests.support.answers_stories_fixtures import config as fixture_config
    from tests.support.pipeline_fixtures import PipelineModel, install_model
    from tests.support.posting_fixtures import build_postings_fixture

    class RankModel(PipelineModel):
        """Every posting of a rank prompt gets a score: the first of a prompt low (30, "ranked low"), the others 70."""

        def __init__(self) -> None:
            super().__init__()
            self.current = SimpleNamespace(target=SimpleNamespace(model="fixture-model"))
            self.rank_prompts: list[str] = []

        def answer(self, prompt: str):  # type: ignore[no-untyped-def]
            if "\nPOSTINGS (" not in prompt:
                return super().answer(prompt)
            self.rank_prompts.append(prompt)
            block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
            ids = _POSTING_LINE.findall(block)
            items = [{"posting_id": pid, "score": 30 if place == 0 else 70, "reasons": ["fits"], "blockers": []} for place, pid in enumerate(ids)]
            return InvocationResult(
                status="success", output_text=json.dumps(items), resolved_model="fixture-model", raw_usage={},
                normalized_usage=NormalizedUsage(100, 10, 110), cost_status="unavailable",
            )

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    model = RankModel()
    install_model(monkeypatch, model)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)  # the rank cache is keyed by the configured model
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield RankServer(f"http://127.0.0.1:{server.server_address[1]}", os.getpid(), None, fx, model)
    finally:
        rank_now.wait_for_jobs(timeout=60)
        server.shutdown()
        server.server_close()
        serving.join(10)


@pytest.fixture
def rank_ui(request: pytest.FixtureRequest, ui_browser, rank_home: RankServer, ui_artifacts: Path):
    with _ui_session(request, ui_browser, rank_home, ui_artifacts) as session:
        yield session


def _seed(home: RankServer, count: int) -> None:
    """`count` postings only the second profile matches, posted in the last day, none ranked."""

    from tests.support.posting_fixtures import TITLE_SECOND_ONLY, lever_job

    now = datetime.now(UTC)
    jobs = [
        # Distinct text per posting: the score cache is keyed by a posting's content.
        lever_job("rv", n, title=TITLE_SECOND_ONLY, text=f"Posting number {n}: build reliable Python services, variant {n}.", created=now - timedelta(hours=1, minutes=n))
        for n in range(1, count + 1)
    ]
    home.fx.seed("rv", jobs, seen_at=now - timedelta(minutes=30))  # type: ignore[attr-defined]
    _save_preferences(home)


def _save_preferences(home: RankServer) -> None:
    """The onboarding's own save (`PUT /api/setup`), so the page opens on Jobs and not on the wizard.

    AFTER the postings are stored (saving first would seed the bundled catalog), with the default profile's own titles.
    """

    def ask(path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(home.url + path, data=data, method="GET" if body is None else "PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")

    profiles = ask("/api/profiles")["profiles"]
    default = next(item for item in profiles if item["profile_id"] == home.fx.default_profile_id)  # type: ignore[attr-defined]
    ask("/api/setup", {
        "roles": list(default["titles"]), "titles_to_avoid": [], "countries": ["US"], "work_mode": "any", "city": None,
        "visa_sponsorship_required": False, "exclude_companies": [], "watch_companies": [], "company_stage_size": None,
        "industries_include": [], "industries_exclude": [], "must_have_stack": [], "dealbreaker_stack": [], "cadence_days": 7,
        "budget_usd_per_session": 0.5,
    })
    after = {item["profile_id"]: item["titles"] for item in ask("/api/profiles")["profiles"]}
    assert after == {item["profile_id"]: item["titles"] for item in profiles}, "saving the preferences changed a profile's titles"


def _text(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


def _totals(read: dict) -> tuple[int, int]:
    rows = read["ranking"]["by_profile"]
    return sum(item["ranked"] for item in rows), sum(item["total"] for item in rows)


def _rank_all(ui) -> dict:
    """The server ranks what is unranked (its own route, as the CLI or an agent asks); the read once the job is done."""

    from gigai.scout.pipeline import rank_now

    ui.server_json(ROUTE, {"mode": "unranked", "approve": True})
    assert rank_now.wait_for_jobs(timeout=60)
    return ui.server_json(ROUTE, {})


def _open(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()


def _no_line_hides_rows(ui) -> None:
    """Nothing on the page says rows are hidden, counts "weak fits, ranked low" or offers "show" (the old collapse)."""

    page = " ".join((ui.page.locator("body").inner_text() or "").split()).lower()
    for gone in ("weak fits, ranked low", "ranked low: show", "back to the list"):
        assert gone not in page, gone
    offers = [text.strip().lower() for text in ui.page.locator("button, a").all_text_contents()]
    assert "show" not in offers, offers


def test_with_every_posting_ranked_the_row_is_there_rank_now_says_why_it_is_off_and_re_rank_shows_its_cost(rank_ui, rank_home: RankServer) -> None:
    ui, total = rank_ui, 5
    _seed(rank_home, total)
    ranked = _rank_all(ui)
    assert ranked["enabled"] is True and _totals(ranked) == (total, total) and ranked["ranking"]["in_progress"] is False
    assert ranked["ranking"]["stale_resume"] is False  # all ranked, nothing stale: the state the release check had
    calls = len(rank_home.model.rank_prompts)  # type: ignore[attr-defined]

    _open(ui)
    # THE OUTCOME: the row is on the page, visible, with the count and both buttons.
    assert ui.page.locator(PANEL).is_visible() and ui.page.locator(RANK_NOW).is_visible() and ui.page.locator(RERANK).is_visible()
    assert _text(ui, LINE) == f"Ranked {total} of {total} (last 7 days)"
    assert _text(ui, RANK_NOW) == "Rank now: nothing to rank" and ui.page.locator(RANK_NOW).is_disabled()
    assert ui.page.locator(RANK_NOW).get_attribute("title") == "Nothing to rank: every posting of the last 7 days is ranked."
    assert _text(ui, RERANK) == "Re-rank latest 100" and ui.page.locator(RERANK).is_enabled()
    shot(ui, "rankvis-all-ranked")

    # The ranked-low posting stands under a plain divider inside the list; no line hides rows or offers "show".
    low = ui.server_json("/api/postings?limit=200")["counts"]["ranked_low"]
    assert low >= 1
    assert _text(ui, f"{tid('jobs-list')} {tid('ranked-low-divider')}") == f"Ranked low ({low})"
    assert ui.job_rows() == total  # every posting is a row, the ranked-low ones too
    _no_line_hides_rows(ui)

    # Re-rank latest 100: the cost first (postings, calls, today's count); the dialog and Cancel call no model.
    used = ranked["calls_today"]["used"]
    ui.page.locator(RERANK).click()
    ui.page.locator(DIALOG).wait_for()
    assert _text(ui, f"{DIALOG} h2") == f"Re-rank the latest {total} postings?"
    assert _text(ui, f"{DIALOG} [data-role='rerank-postings']").startswith(f"Postings: {total},")
    assert _text(ui, f"{DIALOG} [data-role='rerank-cost']") == "Cost: 1 model call (up to 50 postings a call, at most 2 calls)"
    assert _text(ui, f"{DIALOG} [data-role='rerank-today']") == f"Today: {used} of 100 rank calls used today; {100 - used} left"
    shot(ui, "rankvis-rerank-dialog")
    ui.page.locator(f"{DIALOG} [data-action='rerank-cancel']").click()
    ui.page.locator(DIALOG).wait_for(state="detached")
    after = ui.server_json(ROUTE, {})
    assert after["calls_today"]["used"] == used and len(rank_home.model.rank_prompts) == calls  # type: ignore[attr-defined]
    assert ui.page.locator(PANEL).is_visible() and _text(ui, LINE) == f"Ranked {total} of {total} (last 7 days)"
    ui.assert_clean()


def test_with_ranking_off_the_row_is_there_and_both_buttons_say_ranking_is_off(rank_ui, rank_home: RankServer) -> None:
    ui, total = rank_ui, 3
    _seed(rank_home, total)
    ui.server_json("/api/settings/background", {"rank": {"enabled": False}}, method="PUT")  # the real switch
    read = ui.server_json("/api/postings?limit=5")["ranking"]
    assert read["enabled"] is False and _totals({"ranking": read}) == (0, total)

    _open(ui)
    assert ui.page.locator(PANEL).is_visible()
    line = _text(ui, LINE)
    assert line.startswith(f"Ranked 0 of {total} (last 7 days) · Ranking is off. To turn it on, ") and HOW_TO in line
    assert (_text(ui, RANK_NOW), _text(ui, RERANK)) == ("Rank now: ranking is off", "Re-rank latest 100: ranking is off")
    assert ui.page.locator(RANK_NOW).is_disabled() and ui.page.locator(RERANK).is_disabled()
    assert HOW_TO in (ui.page.locator(RANK_NOW).get_attribute("title") or "") and HOW_TO in (ui.page.locator(RERANK).get_attribute("title") or "")
    shot(ui, "rankvis-ranking-off")
    assert rank_home.model.rank_prompts == []  # type: ignore[attr-defined]
    ui.assert_clean()


def test_with_postings_not_ranked_rank_now_is_on_and_a_click_ranks_them(rank_ui, rank_home: RankServer) -> None:
    ui, total = rank_ui, 4
    _seed(rank_home, total)
    assert _totals(ui.server_json(ROUTE, {})) == (0, total)

    _open(ui)
    assert ui.page.locator(PANEL).is_visible()
    assert _text(ui, LINE) == f"Ranked 0 of {total} (last 7 days) · {total} not ranked yet"
    assert _text(ui, RANK_NOW) == "Rank now" and ui.page.locator(RANK_NOW).is_enabled()
    assert _text(ui, RERANK) == "Re-rank latest 100" and ui.page.locator(RERANK).is_enabled()
    shot(ui, "rankvis-not-ranked")

    ui.page.locator(RANK_NOW).click()
    # THE OUTCOME: the count moves to N of N on the page, with nothing else done.
    ui.page.wait_for_function("([line, text]) => (document.querySelector(line)?.textContent || '').trim() === text", arg=[LINE, f"Ranked {total} of {total} (last 7 days)"], timeout=60000)
    ui.settle()
    assert _text(ui, NOTICE) == f"Ranked {total} postings in 1 call."
    assert _text(ui, RANK_NOW) == "Rank now: nothing to rank" and ui.page.locator(RANK_NOW).is_disabled()
    listed = ui.server_json("/api/postings?limit=200")
    assert _totals(listed) == (total, total) and all(row["rank_score"] is not None for row in listed["postings"]["rows"])
    assert len(rank_home.model.rank_prompts) == 1  # type: ignore[attr-defined]
    assert ui.page.locator(f"{tid('job-row')} [data-role='score']").count() == total
    assert not [text for text in ui.page.locator(tid("job-row")).all_text_contents() if "not ranked yet" in text.lower()]
    shot(ui, "rankvis-ranked-by-the-click")
    ui.assert_clean()


def test_a_rank_that_finishes_in_the_background_shows_on_the_page_without_a_reload(rank_ui, rank_home: RankServer) -> None:
    """0.1.11.3 P10 (alpha user: "ZERO ranked jobs after ranking had finished; had to refresh"). The page is open and NOT reloaded.

    The rank is started by the server's own route (a background lane, another tab, the CLI): the page did not start it, so
    it has no job of its own to read. The row says "Ranking… X of Y", then "Ranked N of N", and the list's rows carry their
    scores, from the page's own watch (GET /api/postings/ranking: GET only, two counts), never a reload.
    """

    ui, total = rank_ui, 4
    _seed(rank_home, total)
    requests: list[tuple[str, str]] = []
    ui.page.on("request", lambda request: requests.append((request.method, request.url.split(rank_home.url, 1)[-1])))
    _open(ui)
    assert _text(ui, LINE) == f"Ranked 0 of {total} (last 7 days) · {total} not ranked yet"
    assert len([text for text in ui.page.locator(tid("job-row")).all_text_contents() if "not ranked yet" in text.lower()]) == total
    ui.page.evaluate("() => { window.__notReloaded = true; }")

    ui.server_json(ROUTE, {"mode": "unranked", "approve": True})  # not the page's doing
    # THE OUTCOME, with no reload and no click: the row and the list moved.
    ui.page.wait_for_function("([line, text]) => (document.querySelector(line)?.textContent || '').trim() === text", arg=[LINE, f"Ranked {total} of {total} (last 7 days)"], timeout=30000)
    ui.page.wait_for_function(  # the list is read again right after the row moves
        "(row) => document.querySelectorAll(row).length > 0 && ![...document.querySelectorAll(row)].some((el) => /not ranked yet/i.test(el.textContent))",
        arg=tid("job-row"), timeout=30000,
    )
    ui.settle()
    assert ui.page.evaluate("() => window.__notReloaded === true"), "the page was reloaded"
    assert _text(ui, RANK_NOW) == "Rank now: nothing to rank" and ui.page.locator(RANK_NOW).is_disabled()
    assert ui.page.locator(f"{tid('job-row')} [data-role='score']").count() == total
    assert not [text for text in ui.page.locator(tid("job-row")).all_text_contents() if "not ranked yet" in text.lower()]
    assert len(rank_home.model.rank_prompts) == 1  # type: ignore[attr-defined]
    # The watch only READS, and only the light route (never POST /api/postings/rank {} of its own, never a second list read per tick).
    watched = [item for item in requests if item[1] == "/api/postings/ranking"]
    assert watched and all(method == "GET" for method, _ in watched), requests
    assert not [item for item in requests if item[0] != "GET" and item[1] != "/api/new/seen"], requests
    shot(ui, "rank-watch-finished-without-reload")
    # Idle: once ranked, the page stops reading the ranking.
    settled = len([item for item in requests if item[1] == "/api/postings/ranking"])
    ui.page.wait_for_timeout(5500)
    assert len([item for item in requests if item[1] == "/api/postings/ranking"]) == settled
    ui.assert_clean()


def test_when_the_list_cannot_be_read_the_row_says_the_ranking_status_is_unavailable(rank_ui, rank_home: RankServer) -> None:
    """No real server makes this one: the page's `GET /api/postings` is cut (the only request touched in this file)."""

    ui = rank_ui
    _seed(rank_home, 2)
    ui.page.route(re.compile(r"/api/postings(\?.*)?$"), lambda route: route.abort() if route.request.method == "GET" else route.continue_())
    ui.goto("/#/jobs")
    ui.page.locator(PANEL).wait_for()
    ui.page.wait_for_function("(line) => (document.querySelector(line)?.textContent || '').includes('unavailable')", arg=LINE)
    assert _text(ui, LINE) == "Ranking status unavailable."
    assert ui.page.locator(PANEL).get_attribute("data-rank-status") == "unavailable"
    assert ui.page.locator(RANK_NOW).is_visible() and ui.page.locator(RERANK).is_visible()
    shot(ui, "rankvis-status-unavailable")
    ui.page.unroute_all()
    # The cut requests are this test's own doing: they are not the browser's problems.
    ui.network.console_errors.clear()
    ui.network.http_errors.clear()
    with ui.network.lock:
        for item in ui.network.order:
            item.dropped = True
