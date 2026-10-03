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

On a failed test, `ui` writes screenshot, Playwright trace, requests, console, server log tail
and the CPU/RSS samples to `$GIGAI_UI_ARTIFACTS/<test>/` (default `build/ui-artifacts/`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
import time

import pytest

from tests.ui import support

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

_UI_FIXTURES = frozenset({"ui", "ui_browser", "scout_server"})


# ---------------------------------------------------------------------------- marking and failure state


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items) -> None:
    """A test that uses a browser fixture is a `ui` test, whether or not its author remembered the mark."""

    for item in items:
        if _UI_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.ui)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


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
    def target(self) -> Path:
        return self.root / "home" / "scout"


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
            state = run_supervisor.status(home_root=root / "home", requested_target=root / "home" / "scout")
        if state.state != "running" or state.pid is None:
            raise RuntimeError(f"the Scout server is not running after the home was built: {state.state}")
        yield ScoutServer(demo.url, state.pid, str(state.log_path), home, root, time.monotonic() - started, demo)
    finally:
        try:
            with synthetic_environment(home):
                from tools.media import demo_home

                demo_home.stop(root)
        finally:
            shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(scope="session")
def ui_artifacts() -> Path:
    folder = Path(os.environ.get(ARTIFACTS_ENV) or REPO / "build" / "ui-artifacts").resolve()
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

    def __init__(self, page, server: ScoutServer, network: support.Network, probe: support.ProcessTreeProbe) -> None:
        super().__init__(network, probe.cpu_seconds)
        self.page, self.server = page, server

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


@pytest.fixture
def ui(request: pytest.FixtureRequest, ui_browser, scout_server: ScoutServer, ui_artifacts: Path) -> Iterator[Ui]:
    context = ui_browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
    context.set_default_timeout(PATIENCE_MS)
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    network = support.Network()
    network.attach(page)
    probe = support.ProcessTreeProbe(scout_server.pid)
    sampler = support.Sampler(probe.read, network)
    sampler.start()
    session = Ui(page, scout_server, network, probe)
    folder = ui_artifacts / support.safe_name(request.node.nodeid)
    try:
        yield session
    finally:
        failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
        setup_failed = getattr(request.node, "rep_setup", None) is not None and request.node.rep_setup.failed
        sampler.stop()
        problems = session.problems()
        keep = failed or setup_failed or bool(problems)
        folder_files: list[str] = []
        if keep:
            folder.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(folder / "screenshot.png"), timeout=10_000)
            except Exception as error:  # a page that is gone or hung must not hide the other artifacts
                (folder / "screenshot-failed.txt").write_text(f"{error}\n", encoding="utf-8")
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
