"""The UI harness's own helpers, tested WITHOUT a browser (no Playwright, Chromium or psutil).

Requests are small fakes with the attributes Playwright's have; time is a hand-wound clock.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from tests.ui import support


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class FakeResponse:
    status: int
    url: str = ""
    request: object = None


@dataclass
class FakeRequest:
    url: str
    method: str = "GET"
    failure: str | None = None
    status: int | None = None

    def response(self) -> FakeResponse | None:
        return None if self.status is None else FakeResponse(self.status)


@dataclass
class FakeMessage:
    type: str
    text: str


def api(path: str) -> FakeRequest:
    return FakeRequest(f"http://127.0.0.1:1234{path}")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def net(clock: Clock) -> support.Network:
    return support.Network(clock=clock)


def make_recorder(net: support.Network, cpu: list[float]) -> support.Recorder:
    return support.Recorder(net, lambda: cpu[0])


# ---------------------------------------------------------------------------- the real-home refusal


def test_refuses_the_real_home_and_anything_inside_it(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / ".gigai").mkdir(parents=True)
    for bad in (real, real / ".gigai", real / "sub"):
        with pytest.raises(support.UnsafeHomeError, match="real home"):
            support.refuse_real_home(bad, real_home=real, temp_root=tmp_path)


def test_refuses_a_home_outside_the_temporary_directory(tmp_path: Path) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(support.UnsafeHomeError, match="not inside the temporary directory"):
        support.refuse_real_home(outside, real_home=tmp_path / "real", temp_root=temp)


def test_refuses_a_gigai_folder_even_inside_temp(tmp_path: Path) -> None:
    folder = tmp_path / ".gigai"
    folder.mkdir()
    with pytest.raises(support.UnsafeHomeError, match=r"\.gigai"):
        support.refuse_real_home(folder, real_home=tmp_path / "real", temp_root=tmp_path)


def test_refuses_a_gigai_home_override_pointing_elsewhere(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(support.UnsafeHomeError, match="GIGAI_HOME"):
        support.refuse_real_home(home, real_home=tmp_path / "real", temp_root=tmp_path, environ={"GIGAI_HOME": "/somewhere/else"})
    assert support.refuse_real_home(home, real_home=tmp_path / "real", temp_root=tmp_path, environ={"GIGAI_HOME": str(home / "x")}) == home.resolve()


def test_accepts_a_fresh_temporary_home(tmp_path: Path) -> None:
    home = tmp_path / "gigai-ui-abc"
    home.mkdir()
    assert support.refuse_real_home(home, real_home=tmp_path / "real", temp_root=tmp_path, environ={}) == home.resolve()


def test_the_real_home_is_not_taken_from_the_home_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before = support.real_user_home()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert support.real_user_home() == before  # HOME is swapped during a build; the real home must not follow it


# ---------------------------------------------------------------------------- request bookkeeping


def test_in_flight_peak_counts_overlap_and_frees_a_slot_at_the_same_instant(net: support.Network, clock: Clock) -> None:
    first, second, third = api("/api/postings"), api("/api/postings"), api("/api/postings")
    net.started(first)
    clock.advance(1)
    net.finished(first)
    net.started(second)  # starts at the instant the first ended: not an overlap
    assert net.peak_in_flight("/api/postings") == 1
    net.started(third)
    assert net.peak_in_flight("/api/postings") == 2
    assert net.peak_in_flight("/api/new") == 0


def test_only_api_requests_are_recorded_and_the_query_is_kept(net: support.Network) -> None:
    net.started(FakeRequest("http://127.0.0.1:1/assets/app.js"))
    net.started(api("/api/postings?limit=50"))
    assert [(item.resource, item.path) for item in net.order] == [("/api/postings", "/api/postings?limit=50")]


def test_dropped_requests_stop_counting_and_are_not_problems(net: support.Network, clock: Clock) -> None:
    net.started(api("/api/postings"))
    net.started(api("/api/new"))
    assert len(net.in_flight()) == 2
    clock.advance(2)
    net.drop_open("dropped by the reload")
    assert net.in_flight() == []
    assert net.problems() == []


def test_a_navigation_abort_is_not_a_problem_but_a_real_failure_is(net: support.Network) -> None:
    aborted, broken = api("/api/postings"), api("/api/new")
    net.started(aborted)
    net.started(broken)
    aborted.failure, broken.failure = "net::ERR_ABORTED", "net::ERR_CONNECTION_REFUSED"
    net.failed(aborted)
    net.failed(broken)
    assert net.problems() == ["request failed: GET /api/new: net::ERR_CONNECTION_REFUSED"]


def test_console_errors_page_errors_and_http_errors_are_problems_but_warnings_are_not(net: support.Network) -> None:
    net.console_message(FakeMessage("warning", "just a warning"))
    net.console_message(FakeMessage("error", "boom"))
    net.responded(FakeResponse(500, "http://127.0.0.1:1/api/answers", FakeRequest("http://127.0.0.1:1/api/answers", "POST")))
    net.responded(FakeResponse(200, "http://127.0.0.1:1/api/ok", FakeRequest("http://127.0.0.1:1/api/ok")))
    problems = net.problems()
    assert problems == ["console error: boom", "HTTP 500 POST /api/answers"]
    assert net.console == ["warning: just a warning", "error: boom"]


# ---------------------------------------------------------------------------- assertions


def test_requests_after_counts_from_the_step_and_can_filter_by_resource(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    net.started(api("/api/postings"))
    ui.step("opened")
    net.started(api("/api/postings"))
    net.started(api("/api/new"))
    assert ui.requests_after("opened") == 2
    assert ui.requests_after("opened", "/api/postings") == 1
    assert ui.requests_after("start") == 3
    assert ui.requests_after("now") == 0


def test_requests_after_failure_message_names_the_step_and_lists_the_requests(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    net.started(api("/api/postings"))
    with pytest.raises(AssertionError) as caught:
        assert ui.requests_after("start") <= 0
    assert "requests after 'start' = 1" in str(caught.value)
    assert "GET /api/postings" in str(caught.value)


def test_server_cpu_seconds_between_uses_the_marks(net: support.Network) -> None:
    cpu = [1.0]
    ui = make_recorder(net, cpu)
    cpu[0] = 1.5
    ui.step("a")
    cpu[0] = 4.0
    ui.step("b")
    cpu[0] = 9.0
    assert ui.server_cpu_seconds_between("a", "b") == pytest.approx(2.5)
    assert ui.server_cpu_seconds_between("start", "a") == pytest.approx(0.5)
    assert ui.server_cpu_seconds_between("b") == pytest.approx(5.0)  # "now" is the default end
    with pytest.raises(AssertionError, match="server CPU seconds 'a' -> 'b' = 2.50"):
        assert ui.server_cpu_seconds_between("a", "b") <= 1


def test_wall_seconds_between(net: support.Network, clock: Clock) -> None:
    ui = make_recorder(net, [0.0])
    clock.advance(3)
    ui.step("a")
    clock.advance(4)
    ui.step("b")
    assert ui.wall_seconds_between("a", "b") == pytest.approx(4)
    assert ui.wall_seconds_between("start", "b") == pytest.approx(7)


def test_requests_between_counts_one_step_and_writes_after_lists_what_was_not_a_read(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    net.started(api("/api/postings"))
    ui.step("a")
    net.started(api("/api/postings?window=new"))
    net.started(FakeRequest("http://127.0.0.1:1234/api/answers", method="POST"))
    ui.step("b")
    net.started(FakeRequest("http://127.0.0.1:1234/api/profiles/p1", method="DELETE"))
    assert ui.requests_between("a", "b") == 2 and ui.requests_between("a", "b", "/api/postings") == 1
    assert ui.requests_between("start", "a") == 1 and ui.requests_between("b") == 1
    assert ui.writes_after("start") == ["POST /api/answers", "DELETE /api/profiles/p1"]
    assert ui.writes_after("b") == ["DELETE /api/profiles/p1"] and ui.writes_after("now") == []


# ---------------------------------------------------------------------------- budgets: reported; failures only with GIGAI_UI_BUDGETS=enforce


def test_a_wall_budget_is_recorded_and_does_not_fail_unless_enforced(net: support.Network, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(support.BUDGETS_ENV, raising=False)
    log = support.BudgetLog()
    ui = support.Recorder(net, lambda: 0.0, budgets=log, test="test_flow")
    ui.step("a")
    clock.advance(7)
    ui.step("b")
    assert ui.wall_budget("slow step", 2.0, "a", "b") == pytest.approx(7)  # over, and not a failure
    ui.wall_budget("quick step", 10.0, "a", "b")
    assert [(line.name, line.kind, line.over, line.blocking) for line in log.lines] == [("slow step", "wall", True, False), ("quick step", "wall", False, False)]
    report = log.report()
    assert "OVER (reported, not enforced)" in report[0] and "slow step" in report[0] and "[test_flow]" in report[0]
    assert report[-1] == f"2 ceilings measured, 1 over (1 reported only: {support.BUDGETS_ENV}=enforce makes them failures)"
    assert log.to_json(enforced=False) == {
        "mode": "report", "ceilings": 2, "over": 1,
        "budgets": [
            {"test": "test_flow", "name": "slow step", "kind": "wall", "measured": 7.0, "limit": 2.0, "unit": "s", "over": True, "blocking": False},
            {"test": "test_flow", "name": "quick step", "kind": "wall", "measured": 7.0, "limit": 10.0, "unit": "s", "over": False, "blocking": False},
        ],
    }


def test_a_wall_budget_fails_when_enforced_and_says_wall_cpu_and_the_requests(net: support.Network, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(support.BUDGETS_ENV, "enforce")
    cpu = [1.0]
    log = support.BudgetLog()
    ui = support.Recorder(net, lambda: cpu[0], budgets=log, test="test_flow")
    ui.step("a")
    net.started(api("/api/postings?limit=50"))
    clock.advance(7)
    cpu[0] = 1.25
    ui.step("b")
    with pytest.raises(AssertionError) as caught:
        ui.wall_budget("slow step", 2.0, "a", "b")
    message = str(caught.value)
    assert "wall-clock budget 'slow step': 7.00 s is over 2.0 s" in message
    assert "wall 7.00 s, server CPU 0.25 s, 1 request(s)" in message and "GET /api/postings?limit=50" in message
    ui.wall_budget("quick step", 10.0, "a", "b")  # under: no failure, enforced or not
    assert [(line.over, line.blocking) for line in log.lines] == [(True, True), (False, True)]
    assert "OVER, FAILED" in log.report()[0]
    with pytest.raises(AssertionError, match="12.00 s is over 10.0 s"):
        ui.wall_budget_of("a second tab", 10.0, 12.0)


def test_the_one_switch_is_report_by_default_and_enforce_on_request() -> None:
    assert support.budgets_mode({}) == support.budgets_mode({support.BUDGETS_ENV: ""}) == support.budgets_mode({support.BUDGETS_ENV: " Report "}) == "report"
    assert support.budgets_mode({support.BUDGETS_ENV: "enforce"}) == support.budgets_mode({support.BUDGETS_ENV: " Enforce "}) == "enforce"
    assert support.budgets_enforced({support.BUDGETS_ENV: "enforce"})
    assert not support.budgets_enforced({}) and not support.budgets_enforced({support.BUDGETS_ENV: "report"})
    # A misspelt switch must not quietly report: a job that means to enforce would enforce nothing.
    for value in ("enforced", "1", "true", "yes", "block"):
        with pytest.raises(ValueError, match="use 'report' .* or 'enforce'"):
            support.budgets_mode({support.BUDGETS_ENV: value})


def test_a_cpu_budget_is_reported_and_fails_only_when_enforced(net: support.Network, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first week (0.1.10.9): no CPU ceiling was measured on a CI runner yet, so over is a line in the report."""

    monkeypatch.delenv(support.BUDGETS_ENV, raising=False)
    cpu = [0.0]
    log = support.BudgetLog()
    ui = support.Recorder(net, lambda: cpu[0], budgets=log, test="test_flow")
    ui.step("a")
    cpu[0] = 4.0
    ui.step("b")
    assert ui.cpu_budget("fine", 5.0, "a", "b") == pytest.approx(4.0)
    assert ui.cpu_budget("too much", 1.0, "a", "b") == pytest.approx(4.0)  # over, and not a failure
    assert [(line.name, line.kind, line.over, line.blocking) for line in log.lines] == [("fine", "cpu", False, False), ("too much", "cpu", True, False)]
    assert "OVER (reported, not enforced)" in log.report()[1]
    assert log.report()[-1] == f"2 ceilings measured, 1 over (1 reported only: {support.BUDGETS_ENV}=enforce makes them failures)"
    assert support.BudgetLog().report() == []  # nothing measured: nothing printed

    monkeypatch.setenv(support.BUDGETS_ENV, "enforce")
    with pytest.raises(AssertionError, match="server CPU budget 'too much': 4.00 s is over 1.0 s"):
        ui.cpu_budget("too much", 1.0, "a", "b")
    assert ui.cpu_budget("fine", 5.0, "a", "b") == pytest.approx(4.0)
    assert [(line.over, line.blocking) for line in log.lines[2:]] == [(True, True), (False, True)]
    assert "OVER, FAILED" in log.report()[2]
    assert log.report()[-1] == f"4 ceilings measured, 2 over (1 reported only: {support.BUDGETS_ENV}=enforce makes them failures)"
    assert log.to_json(enforced=True)["mode"] == "enforce"


def test_a_memory_budget_is_in_megabytes_reported_and_fails_only_when_enforced(net: support.Network, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(support.BUDGETS_ENV, raising=False)
    log = support.BudgetLog()
    ui = support.Recorder(net, lambda: 0.0, budgets=log, test="test_flow")
    assert ui.memory_budget_of("server memory", 600, 229.4) == pytest.approx(229.4)
    assert ui.memory_budget_of("server memory", 600, 845.0) == pytest.approx(845.0)  # over, and not a failure
    assert [(line.kind, line.unit, line.over, line.blocking) for line in log.lines] == [("rss", "MB", False, False), ("rss", "MB", True, False)]
    assert "229 MB of    600 MB" in log.report()[0] and log.to_json(enforced=False)["budgets"][0]["unit"] == "MB"

    monkeypatch.setenv(support.BUDGETS_ENV, "enforce")
    with pytest.raises(AssertionError, match="the peak was 845 MB, over 600 MB"):
        ui.memory_budget_of("server memory", 600, 845.0)
    assert (log.lines[-1].over, log.lines[-1].blocking) == (True, True)


def test_budgets_json_carries_what_the_run_took(net: support.Network, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CI job's numbers (how long the run and the home builds took) travel with the ceilings."""

    monkeypatch.delenv(support.BUDGETS_ENV, raising=False)
    log = support.BudgetLog()
    support.Recorder(net, lambda: 0.0, budgets=log, test="test_flow").wall_budget_of("a step", 2.0, 1.0)
    data = log.to_json(enforced=False, run={"seconds": 71.2, "small_home_build_seconds": 24.9})
    assert data["run"] == {"seconds": 71.2, "small_home_build_seconds": 24.9}
    assert "run" not in log.to_json(enforced=False)


def test_no_test_asserts_a_wall_time_directly() -> None:
    """Wall-clock ceilings go through `wall_budget` (reported; a failure only when enforced), never through a bare assert."""

    here = Path(__file__).resolve().parent
    for path in sorted(here.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "wall_seconds_between" in line:
                assert "assert" not in line and "measured(" not in line, f"{path.name}:{number} asserts a wall time; use ui.wall_budget(name, LIMIT, first, last)"


# ---------------------------------------------------------------------------- the order of the run


def test_modules_that_change_the_home_run_last_and_the_cold_flow_first() -> None:
    from types import SimpleNamespace

    from tests.ui import conftest

    def item(name: str, order: object = None) -> SimpleNamespace:
        module = SimpleNamespace() if order is None else SimpleNamespace(**{conftest.ORDER_ATTRIBUTE: order})
        return SimpleNamespace(name=name, module=module)

    items = [item("reads-1"), item("deletes", 90), item("reads-2"), item("assesses", 10), item("cold", -1), item("odd", "soon"), item("reads-3")]
    items.sort(key=conftest.ui_order)
    assert [entry.name for entry in items] == ["cold", "reads-1", "reads-2", "odd", "reads-3", "assesses", "deletes"]
    assert conftest.ui_order(SimpleNamespace(name="no module")) == 0


def test_the_flows_that_change_the_small_home_say_so() -> None:
    """A flow that writes to the shared home through the page declares UI_ORDER, so it runs after the readers."""

    here = Path(__file__).resolve().parent
    orders = {}
    for path in sorted(here.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        found = [line for line in text.splitlines() if line.startswith("UI_ORDER = ")]
        if found:
            orders[path.name] = int(found[0].split("=")[1].split("#")[0])
    assert orders["test_operator_sized_jobs.py"] < 0, "the cold flow must be the first to read the operator-sized list"
    changing = {"test_jobs_assess_these.py", "test_job_page_questions.py", "test_profile_delete.py"}
    assert changing <= set(orders) and all(orders[name] > 0 for name in changing)
    assert orders["test_profile_delete.py"] == max(orders.values()), "deleting a profile is the last thing done to the small home"


def test_no_more_than_one_in_flight_passes_for_sequential_and_fails_for_overlap(net: support.Network, clock: Clock) -> None:
    ui = make_recorder(net, [0.0])
    one, two = api("/api/postings"), api("/api/postings")
    net.started(one)
    clock.advance(1)
    net.finished(one)
    net.started(two)
    ui.no_more_than_one_in_flight("/api/postings")
    net.started(api("/api/postings"))
    with pytest.raises(AssertionError, match="2 requests for /api/postings were in flight at once"):
        ui.no_more_than_one_in_flight("/api/postings")
    ui.no_more_than_one_in_flight("/api/new")


def test_no_more_than_one_in_flight_counts_only_from_the_given_step(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    net.started(api("/api/postings"))
    net.started(api("/api/postings"))  # an earlier overlap
    ui.step("later")
    net.started(api("/api/postings"))
    with pytest.raises(AssertionError):
        ui.no_more_than_one_in_flight("/api/postings")
    ui.no_more_than_one_in_flight("/api/postings", since="later")


def test_step_names_are_unique_and_unknown_ones_are_explained(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    ui.step("a")
    with pytest.raises(ValueError, match="already used"):
        ui.step("a")
    with pytest.raises(ValueError, match="reserved"):
        ui.step("now")
    with pytest.raises(KeyError, match="no step named 'zzz'"):
        ui.requests_after("zzz")


def test_assert_clean_reports_every_problem(net: support.Network) -> None:
    ui = make_recorder(net, [0.0])
    ui.assert_clean()
    net.console_message(FakeMessage("error", "first"))
    net.console_errors.append("page error: second")
    with pytest.raises(AssertionError, match=r"2 problem\(s\)"):
        ui.assert_clean()


# ---------------------------------------------------------------------------- sampler and artifacts


def test_sampler_records_cpu_rss_and_in_flight_and_keeps_the_rss_peak(net: support.Network, clock: Clock) -> None:
    readings = iter([(1.0, 50 * 1048576), (2.5, 80 * 1048576), (3.0, 60 * 1048576)])
    sampler = support.Sampler(lambda: next(readings), net)
    net.started(api("/api/postings"))
    clock.advance(2)
    for _ in range(3):
        sampler.sample()
    lines = sampler.csv().splitlines()
    assert lines[0] == support.Sampler.HEADER
    assert lines[1].split(",")[1:] == ["1.00", "50", "1", "2.0"]
    assert len(lines) == 4
    assert sampler.peak_rss_bytes == 80 * 1048576


def test_write_artifacts_writes_the_files_a_person_needs(net: support.Network, tmp_path: Path) -> None:
    ui = make_recorder(net, [0.0])
    request = api("/api/postings")
    net.started(request)
    net.console_message(FakeMessage("error", "boom"))
    log = tmp_path / "server.log"
    log.write_text("\n".join(f"line {number}" for number in range(500)), encoding="utf-8")
    names = support.write_artifacts(tmp_path / "out", recorder=ui, sampler=None, server_log=log, reason="the test failed")
    assert names == ["console.txt", "problems.txt", "reason.txt", "requests.json", "server-log-tail.txt"]
    tail = (tmp_path / "out" / "server-log-tail.txt").read_text(encoding="utf-8").splitlines()
    assert tail[-1] == "line 499" and len(tail) == 200
    data = json.loads((tmp_path / "out" / "requests.json").read_text(encoding="utf-8"))
    assert data["requests"][0]["path"] == "/api/postings" and data["requests"][0]["seconds"] is None
    assert data["steps"][0]["step"] == "start"
    assert "boom" in (tmp_path / "out" / "problems.txt").read_text(encoding="utf-8")


def test_write_artifacts_survives_a_missing_server_log(net: support.Network, tmp_path: Path) -> None:
    ui = make_recorder(net, [0.0])
    support.write_artifacts(tmp_path / "out", recorder=ui, sampler=None, server_log=tmp_path / "nope.log", reason="x")
    assert "unreadable" in (tmp_path / "out" / "server-log-tail.txt").read_text(encoding="utf-8")


def test_safe_name_makes_a_folder_name_of_a_node_id() -> None:
    assert support.safe_name("tests/ui/test_smoke_flow.py::test_jobs[x y]") == "tests_ui_test_smoke_flow.py_test_jobs_x_y"


# ---------------------------------------------------------------------------- a wait that ran out of patience


def test_a_timeout_is_recognised_by_its_type_and_in_the_failures_that_name_one() -> None:
    playwright_timeout = type("TimeoutError", (Exception,), {})  # Playwright's own class has this name
    assert support.is_timeout(playwright_timeout("Page.wait_for_function: Timeout 20000ms exceeded."))
    assert support.is_timeout(TimeoutError("timed out"))  # the standard one: the server asked directly
    assert support.is_timeout(AssertionError("the Jobs list did not show rows in 20 s: 0 rows, requests in flight: none (TimeoutError)"))
    assert support.is_timeout(AssertionError("2 check(s) failed on the operator-sized home:\n    Locator.click: Timeout 20000ms exceeded."))
    try:
        try:
            raise playwright_timeout("Timeout 20000ms exceeded.")
        except Exception as inner:
            raise RuntimeError("the Save button was not there") from inner
    except RuntimeError as outer:
        assert support.is_timeout(outer)
    assert not support.is_timeout(AssertionError("assert 3 == 2"))
    assert not support.is_timeout(None)


def test_the_sampler_says_what_the_server_did_lately(net: support.Network, clock: Clock) -> None:
    now = [(1.0, 100 * 1048576)]
    sampler = support.Sampler(lambda: now[0], net)
    assert sampler.recent() == (0.0, 0.0, 0.0)  # no sample yet
    sampler.sample()
    clock.advance(30)
    now[0] = (3.0, 150 * 1048576)
    sampler.sample()
    clock.advance(4)
    now[0] = (3.5, 210 * 1048576)
    sampler.sample()
    span, cpu, rss_mb = sampler.recent(10.0)  # the first sample is older than 10 s
    assert (span, cpu, rss_mb) == (pytest.approx(4.0), pytest.approx(0.5), pytest.approx(210.0))


def test_the_heartbeat_says_how_long_the_script_and_the_frames_have_been_silent() -> None:
    beats = support.Heartbeat()
    assert beats.pulse(1000.0) == support.Pulse(0, None, None, 0.0, None) and "no beat was received" in beats.pulse(1000.0).lines()[0]
    for index in range(20):  # 10 s of a live page: a beat every half second, a frame drawn for each
        beats.beat((1000.0 + index * 0.5) * 1000, index)
    live = beats.pulse(1010.0)
    assert (live.beats, live.script_silent, live.frames_silent) == (20, pytest.approx(0.5), pytest.approx(0.5))
    assert live.longest_gap == pytest.approx(0.5) and not live.script_stalled and not live.drawing_stalled
    assert live.lines() == ["20 beat(s) received; the last one 0.5 s before the failure", "the frame count last moved 0.5 s before the failure"]

    # The script goes on and the frame count stands still: the browser stopped drawing (what U3's trace pointed at).
    for index in range(20, 40):
        beats.beat((1000.0 + index * 0.5) * 1000, 19)
    drawing = beats.pulse(1020.0)
    assert (drawing.script_silent, drawing.frames_silent) == (pytest.approx(0.5), pytest.approx(10.5))
    assert drawing.drawing_stalled and not drawing.script_stalled

    # Then the beats stop too: the page's script is not running.
    stuck = beats.pulse(1040.0)
    assert stuck.script_silent == pytest.approx(20.5) and stuck.script_stalled and not stuck.drawing_stalled
    assert stuck.longest_gap == pytest.approx(20.5) and stuck.gap_ended == pytest.approx(0.0)

    # A page that came back: the silence is still in the window, and it is over.
    beats.beat(1040.5 * 1000, 20)
    beats.beat(1041.0 * 1000, 21)
    back = beats.pulse(1041.2)
    assert not back.script_stalled and not back.drawing_stalled
    assert back.longest_gap == pytest.approx(21.0) and back.gap_ended == pytest.approx(0.7)
    assert back.lines()[-1] == "the longest silence was 21.0 s, ended 0.7 s before the failure"
    assert beats.pulse(999.0).beats == 0  # a beat after the moment asked about is not counted


def test_the_heartbeat_script_reports_through_the_binding_and_never_raises_in_the_page() -> None:
    script = support.HEARTBEAT_JS
    assert f"window.{support.HEARTBEAT_BINDING}(Date.now(), state.frames)" in script and "}, 500);" in script
    assert ".catch(() => {})" in script and "try {" in script  # a rejected or missing binding is not a page error
    assert "window.top !== window" in script  # one heartbeat per page, not one per frame


def _timeout_report(net: support.Network, clock: Clock, tmp_path: Path, *, held: bool, pulse: support.Pulse, server_answers: bool = True) -> str:
    cpu = [0.0]
    ui = make_recorder(net, cpu)
    done = api("/api/health")
    done.status = 200
    net.started(done)
    net.finished(done)
    if held:
        net.started(api("/api/postings?limit=50"))
    sampler = support.Sampler(lambda: (cpu[0], 212 * 1048576), net)
    sampler.sample()
    clock.advance(18.5)
    cpu[0] = 0.01
    sampler.sample()
    clock.advance(4)
    cpu[0] = 0.02
    sampler.sample()
    net.console_message(FakeMessage("warning", "a slow frame"))
    log = tmp_path / "server.log"
    log.write_text("\n".join(f"line {number}" for number in range(100)), encoding="utf-8")
    probes = [
        support.Probe(support.SERVER_HEALTH, server_answers, "200 in 0.01 s" if server_answers else "no answer in 2.0 s (TimeoutError: timed out)", side="server"),
        support.Probe(support.PAGE_SCREENSHOT, True, "taken"),
    ]
    error = AssertionError("Locator.click: Timeout 20000ms exceeded.\nCall log:\n  - waiting for locator(\"[data-action='save']\")")
    return support.timeout_report(recorder=ui, sampler=sampler, server_log=log, error=error, probes=probes, pulse=pulse, load="load average 14.2, 9.1, 5.0 on 4 CPUs")


LIVE = support.Pulse(120, 0.3, 0.3, 0.5, 0.3)


def test_the_timeout_report_of_a_stalled_browser_says_the_server_was_idle_and_the_page_silent(net: support.Network, clock: Clock, tmp_path: Path) -> None:
    """The U3 stall (once in 20 busy runs): nothing in flight, the server idle, the page not drawing, then not running."""

    text = _timeout_report(net, clock, tmp_path, held=False, pulse=support.Pulse(80, 19.6, 20.1, 19.6, 0.0))
    lines = text.splitlines()
    assert lines[0] == "A wait ran out of patience. What the harness saw right after (22.5 s on the page's clock):"
    assert "reading (a guess from the lines below): the page's script had not run for 19.6 s: the browser stalled, not the server." in lines
    assert "requests in flight: 0" in lines
    assert "    GET /api/health -> 200 0.00s (at 0.00s)" in lines  # the last requests that ended
    assert "the page's heartbeat (its own timer, every 0.5 s; the last 60 s)" in lines
    assert "    80 beat(s) received; the last one 19.6 s before the failure" in lines
    assert "    the frame count last moved 20.1 s before the failure" in lines
    assert "    the server answers /api/health: 200 in 0.01 s" in lines and "    the page gives a screenshot: taken" in lines
    assert "the server's process over the last 10.0 s of samples: 0.01 CPU s in 4.0 s, 212 MB" in lines  # idle
    assert "the machine: load average 14.2, 9.1, 5.0 on 4 CPUs" in lines
    assert "    Locator.click: Timeout 20000ms exceeded." in lines and "    warning: a slow frame" in lines
    assert lines[-1] == "    line 99" and "    line 40" in lines and "    line 39" not in lines  # the server log's last 60 lines

    drawing = _timeout_report(support.Network(clock=clock), clock, tmp_path, held=False, pulse=support.Pulse(120, 0.4, 20.2, 0.5, 0.4))
    assert "nothing was in flight, the page's script ran, and no frame was drawn for 20.2 s: the browser stopped drawing, not the server." in drawing


def test_the_timeout_report_of_a_slow_server_names_the_open_requests(net: support.Network, clock: Clock, tmp_path: Path) -> None:
    text = _timeout_report(net, clock, tmp_path, held=True, pulse=LIVE)
    assert "reading (a guess from the lines below): the page was waiting for the server: 1 request(s) open, the oldest for 22.5 s." in text
    assert "requests in flight: 1\n    GET /api/postings?limit=50 -> in flight (at 0.00s), open for 22.5 s" in text
    gone = _timeout_report(support.Network(clock=clock), clock, tmp_path, held=False, pulse=LIVE, server_answers=False)
    assert "nothing was in flight and the server did not answer when asked directly: the server is stuck or gone." in gone
    # A page whose script stopped keeps the requests the server already answered "in flight": the heartbeat decides.
    stopped = _timeout_report(support.Network(clock=clock), clock, tmp_path, held=True, pulse=support.Pulse(2, 6.2, 6.2, 6.2, 0.0))
    assert (
        "reading (a guess from the lines below): the page's script had not run for 6.2 s: the browser stalled, not the server "
        "(1 request(s) still open on the page, which a stopped page cannot finish: the server log says whether they were answered)."
    ) in stopped


def test_the_timeout_report_of_a_live_page_with_nothing_open_says_the_page_never_showed_it(net: support.Network, clock: Clock, tmp_path: Path) -> None:
    text = _timeout_report(net, clock, tmp_path, held=False, pulse=LIVE)
    assert "reading (a guess from the lines below): nothing was in flight and the page was alive: it waited for something the page never showed (a selector or a state)." in text
    recovered = _timeout_report(support.Network(clock=clock), clock, tmp_path, held=False, pulse=support.Pulse(90, 0.2, 0.2, 14.0, 6.0))
    assert "it waited for something the page never showed (a selector or a state); the page had been silent for 14.0 s before." in recovered
    # No beat at all is not "alive": the page stalled before its first beat, or was never heard.
    mute = _timeout_report(support.Network(clock=clock), clock, tmp_path, held=False, pulse=support.Pulse(0, None, None, 0.0, None))
    assert "the page sent no heartbeat at all: its script never ran, or stopped within its first half second (the browser, not the server)." in mute
    assert "no beat was received" in mute


def test_the_timeout_report_is_as_of_the_failure_not_of_the_teardown(net: support.Network, clock: Clock, tmp_path: Path) -> None:
    """The screenshot of a stuck page can take seconds: what was open is told as of the moment the wait gave up."""

    ui = make_recorder(net, [0.0])
    held = api("/api/postings?limit=50")
    held.status = 200
    net.started(held)
    clock.advance(20)
    failed = net.now()
    clock.advance(8)  # the teardown: the request ends during it
    net.finished(held)
    text = support.timeout_report(recorder=ui, sampler=None, server_log=None, error="Timeout 20000ms exceeded.", probes=[], at=failed)
    assert "What the harness saw right after (20.0 s on the page's clock):" in text
    assert "requests in flight: 1\n    GET /api/postings?limit=50 -> 200 28.00s (at 0.00s), open for 20.0 s" in text
    assert "the last 0 request(s) that ended" in text


# ---------------------------------------------------------------------------- the operator-sized home, built once for two takers


def _pristine(at: Path, **changes: object) -> Path:
    """A stand-in for a home built with `--pristine` for the root `at` (a few files; `changes` to its record)."""

    from tests.support import operator_home

    root = at
    pristine = root.with_name(root.name + operator_home.PRISTINE_SUFFIX)
    (pristine / "home" / "scout").mkdir(parents=True)
    (pristine / "home" / "config.toml").write_text("as built", encoding="utf-8")
    record = {
        "root": str(root), "home": str(root / "home"), "target": str(root / "home" / "scout"), "postings": 290_000, "companies": 10_350,
        "matched_titles": 605, "distinct_titles": 51_318, "assessed": 40, "profiles": {"default": "profile_1"}, "build_seconds": 50.0,
    } | changes
    (pristine / "operator-home.json").write_text(json.dumps(record), encoding="utf-8")
    return pristine


def test_a_prebuilt_home_is_handed_out_fresh_each_time_at_the_path_it_was_built_for(tmp_path: Path) -> None:
    from tests.support import operator_home

    root = tmp_path / "op"
    pristine = _pristine(root)
    env = {operator_home.PREBUILT_ENV: str(root)}
    assert operator_home.take_prebuilt(environ={}) is None  # nothing prebuilt: the caller builds its own, as before
    built = operator_home.take_prebuilt(environ=env)
    assert built is not None and Path(built.home) == root / "home" and built.postings == 290_000
    assert (root / "home" / "config.toml").read_text(encoding="utf-8") == "as built"
    # The first taker (the timing gate) reads and changes its home; the second (the browser flows) must get it cold.
    (root / "home" / "config.toml").write_text("changed by the first taker", encoding="utf-8")
    (root / "home" / "read-model.sqlite").write_text("warm", encoding="utf-8")
    again = operator_home.take_prebuilt(environ=env)
    assert again == built
    assert (root / "home" / "config.toml").read_text(encoding="utf-8") == "as built" and not (root / "home" / "read-model.sqlite").exists()
    assert (pristine / "home" / "config.toml").read_text(encoding="utf-8") == "as built" and not (pristine / "home" / "read-model.sqlite").exists()


def test_a_prebuilt_home_is_refused_when_it_is_not_the_home_asked_for(tmp_path: Path) -> None:
    from tests.support import operator_home

    root = tmp_path / "op"
    env = {operator_home.PREBUILT_ENV: str(root)}
    with pytest.raises(operator_home.OperatorHomeError, match="no prebuilt home"):
        operator_home.take_prebuilt(environ=env)
    # The home holds its own absolute path (config.toml, the assessment records): a copy elsewhere is another home.
    _pristine(root, root=str(tmp_path / "elsewhere"))
    with pytest.raises(operator_home.OperatorHomeError, match="was built for"):
        operator_home.take_prebuilt(environ=env)
    other = tmp_path / "small" / "op"
    other.parent.mkdir()
    _pristine(other, postings=29_000, companies=1_035)
    with pytest.raises(operator_home.OperatorHomeError, match="29000 postings x 1035 companies"):
        operator_home.take_prebuilt(environ={operator_home.PREBUILT_ENV: str(other)})
    assert not root.exists() and not other.exists()  # a refused home is not unpacked


def test_the_browser_flows_take_the_prebuilt_home_only_under_its_own_temporary_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.support import operator_home
    from tests.ui import operator_home_ui

    root = tmp_path / "ui-home" / "op"
    root.parent.mkdir()
    _pristine(root)
    monkeypatch.setenv(operator_home.PREBUILT_ENV, str(root))
    monkeypatch.delenv(operator_home_ui.SERVER_ROOT_ENV, raising=False)
    assert operator_home_ui.prebuilt_home() == root.parent  # HOME of the server: the folder the home was built in
    built = operator_home_ui.take_home(root.parent, env={})
    assert built is not None and Path(built.target) == root / "home" / "scout" and (root / "operator-home.json").is_file()
    with pytest.raises(operator_home_ui.OperatorHomeUiError, match="is not under the temporary HOME"):
        operator_home_ui.take_home(tmp_path / "another-home", env={})
    # Another checkout's product code cannot be served on a home this tree's code built.
    checkout = tmp_path / "old"
    (checkout / "src" / "gigai").mkdir(parents=True)
    (checkout / "src" / "gigai" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv(operator_home_ui.SERVER_ROOT_ENV, str(checkout))
    with pytest.raises(operator_home_ui.OperatorHomeUiError, match="cannot be combined"):
        operator_home_ui.take_home(root.parent, env={})
    monkeypatch.delenv(operator_home.PREBUILT_ENV)
    assert operator_home_ui.prebuilt_home() is None


# ---------------------------------------------------------------------------- the default run never starts a browser


def test_the_default_run_deselects_ui_tests_and_make_ui_test_selects_them() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    options = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "-m 'not ui'" in options["addopts"], "the CI shards and a plain pytest run must deselect browser tests"
    assert any(marker.startswith("ui:") for marker in options["markers"])
    if not (root / "Makefile").is_file():
        pytest.skip("the Makefile is excluded from the offline container build context")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    small, full, operator = (line for line in makefile.splitlines() if "pytest tests/ui" in line)
    for recipe in (small, full, operator):
        assert "GIGAI_UI_REQUIRED=1" in recipe and "-n 0" in recipe
        assert support.BUDGETS_ENV not in recipe, "report or enforce is the caller's choice (the CI job sets it in one place)"
        assert "--reruns" not in recipe and "retry" not in recipe
    # `make ui-test` never builds the operator-sized home; `make ui-test-full` runs everything; `make ui-test-operator`
    # is the operator-sized half alone (the release pre-check's `operator-home` job; the `ui` job runs the small half).
    assert ' -m "ui and not operator_sized" ' in small
    assert " -m ui " in full
    assert ' -m "ui and operator_sized" ' in operator
    assert any(marker.startswith("operator_sized:") for marker in options["markers"])


# ---------------------------------------------------------------------------- the operator-sized home's child processes


def test_operator_children_get_the_temporary_home_and_none_of_the_shells_scout_settings(tmp_path: Path) -> None:
    from tests.ui import operator_home_ui

    shell = {"HOME": "/Users/someone", "PATH": "/usr/bin", "GIGAI_HOME": "/Users/someone/.gigai", "GIGAI_SCOUT_PIPELINE": "0", "GIGAI_SCOUT_AUTO_REFRESH": "1", "GIGAI_TEST_LATENCY_SCALE": "4"}
    env = operator_home_ui.child_environment(tmp_path, environ=shell)
    assert env["HOME"] == str(tmp_path.resolve())
    assert "GIGAI_HOME" not in env and not [name for name in env if name.startswith("GIGAI_SCOUT_")]
    assert env["PATH"] == "/usr/bin" and env["GIGAI_TEST_LATENCY_SCALE"] == "4"
    assert "PYTHONPATH" not in env and "PYTHONPYCACHEPREFIX" not in env  # this tree's product code, as installed


def test_operator_children_refuse_the_real_home() -> None:
    from tests.ui import operator_home_ui

    with pytest.raises(support.UnsafeHomeError):
        operator_home_ui.child_environment(support.real_user_home() / ".gigai", environ={})


def test_a_server_root_puts_that_checkouts_code_first_and_writes_nothing_into_it(tmp_path: Path) -> None:
    from tests.ui import operator_home_ui

    checkout = tmp_path / "old-release"
    (checkout / "src" / "gigai").mkdir(parents=True)
    (checkout / "src" / "gigai" / "__init__.py").write_text("", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    shell = {"PYTHONPATH": "/somewhere/else", operator_home_ui.SERVER_ROOT_ENV: str(checkout)}
    assert operator_home_ui.server_root(shell) == checkout.resolve()
    env = operator_home_ui.child_environment(home, environ=shell)
    assert env["PYTHONPATH"].split(":") == [str(checkout.resolve() / "src"), "/somewhere/else"]
    assert Path(env["PYTHONPYCACHEPREFIX"]).is_relative_to(home.resolve())  # bytecode goes to the temporary HOME
    assert operator_home_ui.server_root({}) is None
    with pytest.raises(operator_home_ui.OperatorHomeUiError, match="not a GigAI checkout"):
        operator_home_ui.server_root({operator_home_ui.SERVER_ROOT_ENV: str(tmp_path)})


def test_the_operator_home_is_never_built_outside_the_temporary_directory() -> None:
    from tests.support import operator_home
    from tests.ui import operator_home_ui

    with pytest.raises(operator_home.OperatorHomeError, match="temporary directory"):
        operator_home_ui.build_home(support.real_user_home() / "gigai-ui-operator" / "op", env={})


def test_no_retry_anywhere_in_the_ui_tests() -> None:
    """The no-retry rule (README.md) is mechanical: no rerun plugin, decorator or loop-until-pass helper."""

    here = Path(__file__).resolve().parent
    banned = ("reruns", "rerun_", "flaky", "tenacity", "backoff", "@retry", "retry(", "retrying")
    for path in sorted(here.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8").lower()
        found = [word for word in banned if word in text]
        assert not found, f"{path.name} mentions {found}: no retry decorators, plugins or loops (tests/ui/README.md)"
