"""Fixtures of the browser tests: a real Scout server on a small synthetic home, and a page that keeps books.

Nothing here imports Playwright or psutil at module level: the normal shards collect this folder
(its browser-free unit tests run there) on machines that have neither. Browser tests are marked
`ui` (automatically, when they use these fixtures) and the default `-m "not ui"` deselects them;
`make ui-test` selects them and sets GIGAI_UI_REQUIRED=1, so a missing browser is a failure there
and a skip anywhere else.

Home and server: `tools.media.demo_home.build` (the real `gigai setup` / `init` / `scout resume
add`, the real sources updater on a transport that serves only made-up boards, the real
supervised Scout server on the fixture model, a free port) in a fresh temporary HOME. The HOME
swap lasts only while the home is built and stopped; the server keeps the environment it started
with. One home and one server per pytest session: a flow that changes the home runs last, or
builds its own.

The operator-sized home (`operator_server`, `operator_ui`; tests/ui/operator_home_ui.py) is a second
home and a second server, built once per session only when a test asks for it. Such a test is
marked `operator_sized` as well: `make ui-test` deselects it, `make ui-test-full` runs it.

On a failed test, `ui` writes screenshot, Playwright trace, requests, console, server log tail
and the CPU/RSS samples to `$GIGAI_UI_ARTIFACTS/<test>/` (default `build/ui-artifacts/`). When the
failure is a wait that ran out of patience, `timeout.txt` also says what was in flight, what the
page's heartbeat was (every page tells the harness twice a second that its script runs and its
frames are drawn: `support.Heartbeat`) and whether the server still answered: there is no retry,
so a timeout has to explain itself.

Order: a module that changes the shared home says so with `UI_ORDER = <n>` (a positive number runs
after every module without one, lowest first; a negative one runs first). Budgets: every timing
ceiling a flow measures (wall-clock, server CPU, server memory) is printed at the end of the run
and written to `budgets.json` beside the artifacts; one that is over fails a test only when
`GIGAI_UI_BUDGETS=enforce` (support.py; `report` is the default, in CI too).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request

import pytest

pytest.register_assert_rewrite("tests.ui.jobs_page")  # its asserts explain themselves, like a test module's

from tests.ui import support  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REQUIRED_ENV = "GIGAI_UI_REQUIRED"
ARTIFACTS_ENV = "GIGAI_UI_ARTIFACTS"
VIEWPORT = {"width": 1280, "height": 800}
#: How long Playwright waits for anything (a patience, never a budget: budgets are the assertions).
PATIENCE_MS = int(os.environ.get("GIGAI_UI_PATIENCE_MS", "20000"))
#: Environment the server and the home build must NOT inherit from the developer's shell.
SCRUBBED = ("GIGAI_HOME", "GIGAI_SCOUT_PIPELINE")
#: Belt and braces beside the fixture seams demo_home sets: no network snapshot, no background model tagging.
EXTRA_SEAMS = {"GIGAI_SCOUT_SNAPSHOT": "0", "GIGAI_SCOUT_MODEL_TAGS": "0"}
FIXTURE_LATENCY_SCALE = "8"  # only widens the server's 15 s health wait on a slow machine

_OPERATOR_FIXTURES = frozenset({"operator_ui", "operator_server"})
_UI_FIXTURES = frozenset({"ui", "ui_browser", "scout_server"}) | _OPERATOR_FIXTURES
#: A module's place in the run (see the docstring): the name of the module attribute.
ORDER_ATTRIBUTE = "UI_ORDER"
#: Every timing ceiling the session's flows measured (printed and written at the end of the run).
_BUDGETS = support.BudgetLog()
#: What the run itself took: the home builds here, the whole run at the end (written to budgets.json).
_RUN: dict[str, object] = {}
_RUN_STARTED = time.monotonic()
#: How long the server gets to answer /api/health after a wait timed out (`server_probe`).
PROBE_SECONDS = 2.0


# ---------------------------------------------------------------------------- marking and failure state


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items) -> None:
    """A test that uses a browser fixture is a `ui` test, whether or not its author remembered the mark."""

    for item in items:
        used = set(getattr(item, "fixturenames", ()))
        if _UI_FIXTURES & used:
            item.add_marker(pytest.mark.ui)
        if _OPERATOR_FIXTURES & used:
            item.add_marker(pytest.mark.operator_sized)
    # The home is shared by the session: a module that changes it runs after the ones that only read it
    # (a stable sort: everything without UI_ORDER keeps its place).
    items.sort(key=ui_order)


def ui_order(item) -> int:
    value = getattr(getattr(item, "module", None), ORDER_ATTRIBUTE, 0)
    return value if type(value) is int else 0


def pytest_sessionstart(session) -> None:
    """A misspelt GIGAI_UI_BUDGETS stops the run before it starts (it would quietly enforce nothing)."""

    try:
        support.budgets_mode()
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
    if report.when == "call" and call.excinfo is not None:
        item.ui_call_error = call.excinfo.value  # `_ui_session` asks whether it was a timeout


def artifacts_folder() -> Path:
    return Path(os.environ.get(ARTIFACTS_ENV) or REPO / "build" / "ui-artifacts").resolve()


def pytest_terminal_summary(terminalreporter) -> None:
    """The timing ceilings of the run, measured against their limits: printed, and written to budgets.json."""

    lines = _BUDGETS.report()
    if not lines:
        return
    enforced = support.budgets_enforced()
    mode = "ENFORCED: a ceiling that is over failed its test" if enforced else f"reported only: {support.BUDGETS_ENV}={support.ENFORCE} makes a ceiling that is over a failure"
    terminalreporter.section(f"ui budgets ({mode})")
    for line in lines:
        terminalreporter.write_line(line)
    folder = artifacts_folder()
    run = {"seconds": round(time.monotonic() - _RUN_STARTED, 1), **_RUN}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "budgets.json").write_text(json.dumps(_BUDGETS.to_json(enforced=enforced, run=run), indent=2), encoding="utf-8")
        terminalreporter.write_line(f"written to {folder / 'budgets.json'}")
    except OSError as error:  # the report is on the terminal either way
        terminalreporter.write_line(f"budgets.json was not written: {error}")


def _unavailable(reason: str) -> None:
    if os.environ.get(REQUIRED_ENV) == "1":
        pytest.fail(f"{reason} ({REQUIRED_ENV}=1 makes this a failure; `uv run --group ui playwright install chromium`)")
    pytest.skip(reason)


# ---------------------------------------------------------------------------- the home and the server


@contextmanager
def synthetic_environment(home: Path) -> Iterator[None]:
    """HOME is `home` (a fresh temporary directory, refused otherwise) and the fixture seams are on; the old environment comes back after."""

    home = support.refuse_real_home(home)
    before = dict(os.environ)
    try:
        for name in SCRUBBED:
            os.environ.pop(name, None)
        os.environ["HOME"] = str(home)
        os.environ.update(EXTRA_SEAMS)
        os.environ.setdefault("GIGAI_TEST_LATENCY_SCALE", FIXTURE_LATENCY_SCALE)
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


@dataclass(frozen=True)
class ScoutServer:
    url: str
    pid: int
    log_path: str
    home: Path  # the temporary HOME
    root: Path  # demo root inside it
    build_seconds: float
    demo: object  # tools.media.demo_home.DemoHome

    @property
    def gigai_home(self) -> Path:
        """The demo's GigAI home, `<temporary HOME>/.gigai` (tools.media.demo_home.demo_gigai_home)."""

        from tools.media import demo_home

        return demo_home.demo_gigai_home(self.root)

    @property
    def target(self) -> Path:
        return self.gigai_home / "scout"


@pytest.fixture(scope="session")
def scout_server() -> Iterator[ScoutServer]:
    try:
        import psutil  # noqa: F401  (the process probe needs it; checked before the 30 s home build)
    except ImportError:
        _unavailable("psutil is not installed")
    # Gitignored and outside any git repository: `gigai init` refuses a target inside one.
    home = Path(tempfile.mkdtemp(prefix="gigai-ui-")).resolve()
    root = home / "demo"
    started = time.monotonic()
    try:
        with synthetic_environment(home):
            from gigai.scout import run_supervisor
            from tools.media import demo_home

            demo = demo_home.build(root, log=lambda line: None)
            gigai_home = demo_home.demo_gigai_home(root)
            state = run_supervisor.status(home_root=gigai_home, requested_target=gigai_home / "scout")
        if state.state != "running" or state.pid is None:
            raise RuntimeError(f"the Scout server is not running after the home was built: {state.state}")
        _RUN["small_home_build_seconds"] = round(time.monotonic() - started, 1)
        yield ScoutServer(demo.url, state.pid, str(state.log_path), home, root, time.monotonic() - started, demo)
    finally:
        try:
            with synthetic_environment(home):
                from tools.media import demo_home

                demo_home.stop(root)
        finally:
            shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(scope="session")
def operator_server(request: pytest.FixtureRequest) -> Iterator[object]:
    """The real server process, cold, on the operator-sized synthetic home (built here, once, in a temporary HOME).

    With GIGAI_OPERATOR_HOME_PREBUILT (the release pre-check's `operator-home` job) the home is a fresh copy of the one
    that job built once, and HOME is the temporary folder it was built in (left in place: this run did not make it).
    """

    try:
        import psutil  # noqa: F401
    except ImportError:
        _unavailable("psutil is not installed")
    from tests.ui import operator_home_ui

    reporter = request.config.pluginmanager.get_plugin("terminalreporter")

    def say(line: str) -> None:  # the build and start times are part of the result: shown whatever the capture mode
        if reporter is not None:
            reporter.write_line(f"[ui] {line}")

    prebuilt = operator_home_ui.prebuilt_home()
    home = prebuilt if prebuilt is not None else Path(tempfile.mkdtemp(prefix="gigai-ui-operator-")).resolve()
    server = None
    try:
        server = operator_home_ui.start(home)
        built = server.built
        how = f"taken from the prebuilt home in {server.build_seconds:.1f} s (built once, in {built.build_seconds} s)" if server.prebuilt else f"built in {server.build_seconds:.1f} s"
        say(
            f"operator-sized home: {built.postings} postings x {built.companies} companies, {len(built.profiles)} profiles, "
            f"{how}; server up in {server.start_seconds:.1f} s"
            + (f"; product code of {server.server_root}" if server.server_root else "")
        )
        _RUN.update(operator_home_seconds=round(server.build_seconds, 1), operator_home_prebuilt=server.prebuilt, operator_server_start_seconds=round(server.start_seconds, 2))
        yield server
    finally:
        try:
            if server is not None:
                operator_home_ui.stop(server.process)
        finally:
            if prebuilt is None:
                shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(scope="session")
def ui_artifacts() -> Path:
    folder = artifacts_folder()
    if folder.exists():
        shutil.rmtree(folder)  # one run's failures only
    return folder


# ---------------------------------------------------------------------------- the browser


@pytest.fixture(scope="session")
def ui_browser() -> Iterator[object]:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        _unavailable("playwright is not installed")
        return
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            _unavailable(f"chromium cannot start: {str(error).splitlines()[0][:160]}")
            return
        try:
            yield browser
        finally:
            browser.close()


class Ui(support.Recorder):
    """One page with its books: `ui.page`, `ui.step(...)`, the assertions of `support.Recorder`."""

    def __init__(
        self, page, server, network: support.Network, probe: support.ProcessTreeProbe, sampler: support.Sampler | None = None, *,
        test: str = "", heartbeat: support.Heartbeat | None = None,
    ) -> None:
        super().__init__(network, probe.cpu_seconds, budgets=_BUDGETS, test=test)
        self.page, self.server, self.sampler = page, server, sampler
        self.heartbeat = heartbeat if heartbeat is not None else support.Heartbeat()

    def goto(self, route: str = "/#/jobs") -> None:
        self.page.goto(self.server.url + route)

    def reload(self) -> None:
        self.network.drop_open("dropped by the reload")  # Playwright sends no event for these
        self.page.reload()

    def wait_for_jobs_list(self, timeout_ms: int = PATIENCE_MS) -> None:
        """The Jobs list has rows and no longer says "Loading"."""

        try:
            self.page.wait_for_function(support.LIST_READY_JS, timeout=timeout_ms)
        except Exception as error:  # Playwright's TimeoutError; say what the page showed instead
            count = self.page.locator('[data-role="postings-count"]')
            shown = count.first.text_content(timeout=1000) if count.count() else "(no count line)"
            flying = ", ".join(item.line() for item in self.network.in_flight()) or "none"
            raise AssertionError(
                f"the Jobs list did not show rows in {timeout_ms / 1000:.0f} s: {self.job_rows()} rows "
                f"[data-testid=job-row], count line {shown!r}, url {self.page.url}, requests in flight: {flying} "
                f"({type(error).__name__})"
            ) from None

    def job_rows(self) -> int:
        return self.page.locator(support.tid("job-row")).count()

    def job_titles(self) -> list[str]:
        """The titles of the rows shown, in order."""

        return self.page.locator(f"{support.tid('job-row')} [data-action='open-job']").all_text_contents()

    def wait_for_job_page(self, timeout_ms: int = PATIENCE_MS) -> None:
        """A job page is shown in full: its title, its state line and its pipeline timeline (not the "Loading job…" stub)."""

        self.page.wait_for_function(support.JOB_PAGE_READY_JS, timeout=timeout_ms)

    def settle(self) -> None:
        """Nothing is in flight any more (so the requests of a step can be counted)."""

        self.page.wait_for_load_state("networkidle")

    def server_json(self, path: str, body: dict | None = None, *, method: str | None = None, timeout: float = 60) -> dict:
        """Ask the server directly, as an agent or the CLI would (never through the page: its books do not count this)."""

        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.server.url + path, data=data, method=method or ("GET" if body is None else "POST"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def watch_count_line(self) -> None:
        """From the next page load on, keep every text the Jobs count line shows (call before `goto`)."""

        self.page.add_init_script(support.COUNT_LINE_WATCH_JS)

    def count_lines(self) -> list[str | None]:
        """Every text the count line has shown so far, in order; None stands for "not on the Jobs page"."""

        return list(self.page.evaluate("() => window.__gigaiCountLines || []"))

    def peak_server_rss_mb(self) -> float:
        return (self.sampler.peak_rss_bytes if self.sampler is not None else 0) / 1048576

    def memory_budget(self, name: str, limit_mb: float) -> float:
        """The server's peak resident memory since this page opened, against a ceiling (recorded; a failure only when enforced)."""

        return self.memory_budget_of(name, limit_mb, self.peak_server_rss_mb())


def server_probe(server_url: str, *, timeout: float = PROBE_SECONDS) -> support.Probe:
    """Asked right after a wait timed out: does the server still answer? (The page is not asked: support.PAGE_SCREENSHOT.)"""

    started = time.monotonic()
    try:
        with urllib.request.urlopen(server_url + "/api/health", timeout=timeout) as response:
            status = response.status
    except OSError as error:  # a timeout, a refused connection, an HTTP error: all of them are "no answer"
        return support.Probe(support.SERVER_HEALTH, False, f"no answer in {time.monotonic() - started:.1f} s ({type(error).__name__}: {str(error)[:100]})", "server")
    return support.Probe(support.SERVER_HEALTH, True, f"{status} in {time.monotonic() - started:.2f} s", "server")


@contextmanager
def _ui_session(request: pytest.FixtureRequest, ui_browser, scout_server, ui_artifacts: Path) -> Iterator[Ui]:
    """One page with its books on `scout_server` (anything with `url`, `pid`, `log_path`); artifacts when the test fails."""

    context = ui_browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
    context.set_default_timeout(PATIENCE_MS)
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    network = support.Network()
    network.attach(page)
    heartbeat = support.Heartbeat()
    page.expose_binding(support.HEARTBEAT_BINDING, lambda _source, at_ms, frames: heartbeat.beat(at_ms, frames))
    page.add_init_script(support.HEARTBEAT_JS)
    probe = support.ProcessTreeProbe(scout_server.pid)
    sampler = support.Sampler(probe.read, network)
    sampler.start()
    session = Ui(page, scout_server, network, probe, sampler, test=request.node.name, heartbeat=heartbeat)
    folder = ui_artifacts / support.safe_name(request.node.nodeid)
    try:
        yield session
    finally:
        failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
        setup_failed = getattr(request.node, "rep_setup", None) is not None and request.node.rep_setup.failed
        call_error = getattr(request.node, "ui_call_error", None)
        timed_out = failed and support.is_timeout(call_error)
        failed_at, failed_clock = time.time(), network.now()  # the report is as of now, whatever the teardown takes
        probes = [server_probe(scout_server.url)] if timed_out else []  # first: as close to the timeout as it gets
        sampler.stop()
        problems = session.problems()
        keep = failed or setup_failed or bool(problems)
        folder_files: list[str] = []
        if keep:
            folder.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(folder / "screenshot.png"), timeout=10_000)
                probes.append(support.Probe(support.PAGE_SCREENSHOT, True, "taken"))
            except Exception as error:  # a page that is gone or hung must not hide the other artifacts
                (folder / "screenshot-failed.txt").write_text(f"{error}\n", encoding="utf-8")
                probes.append(support.Probe(support.PAGE_SCREENSHOT, False, f"failed ({type(error).__name__})"))
        if timed_out:  # before the page closes: closing ends its open requests
            report = support.timeout_report(recorder=session, sampler=sampler, server_log=scout_server.log_path, error=call_error, probes=probes, pulse=heartbeat.pulse(failed_at), at=failed_clock)
            (folder / "timeout.txt").write_text(report, encoding="utf-8")
            print(f"\n[ui] {request.node.nodeid}: " + report.split("\nconsole: ")[0].rstrip() + f"\n(the console and the server log: {folder / 'timeout.txt'})")
        try:
            context.tracing.stop(path=str(folder / "trace.zip") if keep else None)
        except Exception as error:
            if keep:
                (folder / "trace-failed.txt").write_text(f"{error}\n", encoding="utf-8")
        context.close()
        network.drop_open("dropped when the page closed")
        if keep:
            reason = "the test failed" if failed else ("the test could not start" if setup_failed else "the browser reported problems")
            folder_files = support.write_artifacts(folder, recorder=session, sampler=sampler, server_log=scout_server.log_path, reason=reason)
            print(f"\n[ui] artifacts for {request.node.nodeid}: {folder} ({', '.join(folder_files)})")
    if problems and not failed:
        raise AssertionError(f"{len(problems)} problem(s) in the browser (artifacts: {folder}):\n  " + "\n  ".join(problems))


@pytest.fixture
def ui(request: pytest.FixtureRequest, ui_browser, scout_server: ScoutServer, ui_artifacts: Path) -> Iterator[Ui]:
    with _ui_session(request, ui_browser, scout_server, ui_artifacts) as session:
        yield session


@pytest.fixture
def operator_ui(request: pytest.FixtureRequest, ui_browser, operator_server, ui_artifacts: Path) -> Iterator[Ui]:
    """`ui` on the operator-sized home (`operator_server`)."""

    with _ui_session(request, ui_browser, operator_server, ui_artifacts) as session:
        yield session
