"""N11-C: every way a find-jobs search starts reads the company index.

A search that fetched the boards itself took about 20 minutes. ``acquire_node``
therefore reads the index unless a caller asks for the fetch by name
(``boards_from="fetch"``: the acquire tests that exercise fetching), and this
file pins each production way in:

- a caller that names no board source (the default);
- the binding every run executes (``bindings.register_find_jobs_nodes``),
  resolved AND executed against a fetch-refusing board client;
- the run's child process (``bindings._child_worker_entry``);
- the API (``POST /api/run`` on ``ScoutFindJobsBackend``, what the UI's Find
  jobs button calls);
- the CLI (``gigai scout run``, detached and ``--foreground``, and the server
  module it starts): there is no CLI command that runs a search on its own,
  the CLI starts the server and the search goes through its API.

The launch itself (a spawned run) is replaced where a test only needs to
know which acquire a run would execute; ``tests/api_e2e/
test_search_reads_index_journey.py`` runs a whole search through the real
supervisor, server and child.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
import inspect
import threading
from pathlib import Path

from click.testing import CliRunner
import httpx
import pytest

from gigai import graph_node_registry, run
from gigai.cli import cli
from gigai.scout import run_supervisor
from gigai.scout.find_jobs import bindings, market_acquisition, present_api
from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend
from gigai.scout.find_jobs.contracts import (
    ACQUIRE_CAPABILITY,
    ATSProvider,
    RowOutcome,
)
from gigai.scout.find_jobs.market_acquisition import (
    BOARDS_FROM_FETCH,
    BOARDS_FROM_INDEX,
    SOURCES_UPDATE_REQUIRED_CODE,
    AcquireAllSourcesFailedError,
    acquire_node,
)
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.find_jobs.watchlist import JournalWatchlistClient
from gigai.workpad import resolve_workpad

from tests.behaviors.scout_find_jobs.conftest import load_fixture
from tests.behaviors.scout_find_jobs.test_acquire_reads_index import (
    _SearchClient,
    _update_sources,
)
from tests.behaviors.scout_find_jobs.test_acquire_scale import (
    _Exa,
    _Watchlist,
    _board,
    _context,
    _input,
    _limits,
    _managed,
    _real_context,
)
from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture as _approved_scout
from tests.behaviors.scout_find_jobs.test_scout_run_supervisor import _free_port, _setup_and_init


class _RecordingBoards:
    """A board client that records every call and answers none."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        self.calls.append(f"{provider}:{board_token}")
        raise RuntimeError("a search asked a board")

    def list_board(self, client, provider, board_token, config):
        self.calls.append(f"{provider}:{board_token}")
        raise RuntimeError("a search asked a board")


def _boards_from(node_callable: object) -> str:
    """Where a bound acquire gets its boards: the keyword it carries, else the default."""

    keywords: dict[str, object] = {}
    while isinstance(node_callable, partial):
        keywords = {**node_callable.keywords, **keywords}
        node_callable = node_callable.func
    assert node_callable is acquire_node, f"the bound acquire is {node_callable!r}, not acquire_node"
    if "boards_from" in keywords:
        return str(keywords["boards_from"])
    return str(inspect.signature(acquire_node).parameters["boards_from"].default)


def _registered_acquires() -> dict[str, str]:
    """``{graph id: board source}`` of every acquire node in the registry."""

    return {
        key[0]: _boards_from(node.callable)
        for key, node in graph_node_registry._REGISTRY.items()
        if key[2] == "acquire" and key[3] == ACQUIRE_CAPABILITY
    }


@pytest.fixture
def unbound(monkeypatch: pytest.MonkeyPatch):
    """An interpreter with no find-jobs binding; whatever a test binds is undone."""

    saved = dict(graph_node_registry._REGISTRY)
    graph_node_registry.clear()
    monkeypatch.setattr(bindings, "_LIVE_BINDINGS", {})
    # Restores the worker entry the launch hook replaces.
    monkeypatch.setattr(run, "_worker_entry", run._worker_entry)
    monkeypatch.delenv(bindings._HOME_ROOT_ENV, raising=False)
    for name in (bindings._TEST_HTTP_ENV, bindings._TEST_MODEL_ENV, "EXA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    try:
        yield
    finally:
        graph_node_registry.clear()
        graph_node_registry._REGISTRY.update(saved)


# --- the default ---------------------------------------------------------------


def test_acquire_reads_the_index_unless_a_caller_asks_for_the_fetch() -> None:
    assert BOARDS_FROM_INDEX == "index" and BOARDS_FROM_FETCH == "fetch"
    for function in (acquire_node, market_acquisition._acquire_node_body):
        assert inspect.signature(function).parameters["boards_from"].default == BOARDS_FROM_INDEX, function.__name__


def test_a_caller_that_names_no_board_source_asks_no_board(tmp_path: Path) -> None:
    home, target, workpad, gig_id = _managed(tmp_path)
    _update_sources(home)
    search, boards = _SearchClient(), _RecordingBoards()

    with search.client() as client:
        out = acquire_node(
            _real_context(home, target, workpad, gig_id, key="default-source", run_id="run_01"),
            _input(),
            http_client=client,
            exa=_Exa(),
            ats=boards,
            watchlist=_Watchlist([_board(ATSProvider.GREENHOUSE, "acme")]),
            home_root=home,
            target=target,
            limits=_limits(concurrency=1),
        )

    assert boards.calls == [] and search.requests == [], "a caller that forgot boards_from fetched the boards"
    assert [row.posting.title for row in out.rows] == ["Software Engineer", "Senior Software Engineer"]
    assert {row.outcome for row in out.rows} == {RowOutcome.NEW}
    progress = read_progress(workpad / "runs" / "run_01").boards
    assert progress["source"] == "index" and progress["requests"] == 0


def test_the_fetch_still_runs_for_a_caller_that_asks_for_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    boards = _RecordingBoards()

    with pytest.raises(AcquireAllSourcesFailedError) as caught:
        acquire_node(
            _context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=boards,
            watchlist=_Watchlist([_board(ATSProvider.GREENHOUSE, "acme")]),
            limits=_limits(concurrency=1), boards_from=BOARDS_FROM_FETCH,
        )

    assert boards.calls == ["greenhouse:acme"]
    assert caught.value.code == "acquire_all_sources_failed"


# --- the binding every run executes ---------------------------------------------


def test_the_bound_acquire_of_a_run_reads_the_index_and_asks_no_board(
    unbound, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home, target, workpad = _approved_scout(tmp_path)
    search, boards = _SearchClient(), _RecordingBoards()
    monkeypatch.setattr(bindings, "_http_client", search.client)
    monkeypatch.setattr(bindings, "ATSBoardClients", lambda: boards)
    JournalWatchlistClient(home, target).add_to_watchlist(_board(ATSProvider.GREENHOUSE, "acme"))

    nodes = bindings.register_find_jobs_nodes(home_root=home, target=target)

    acquire = next(node for node in nodes if node.goal_slug == "acquire")
    assert _boards_from(acquire.callable) == BOARDS_FROM_INDEX
    registered = _registered_acquires()
    assert bindings.GRAPH_ID in registered and len(registered) >= 2, "the sealed graph's own id is not bound"
    assert set(registered.values()) == {BOARDS_FROM_INDEX}, registered
    # The launch hook makes the run's child bind the same way (next test).
    assert run._worker_entry is bindings._child_worker_entry

    # EXECUTED, not only resolved: the callable a run is handed.
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)

    def _run_context(n: int):
        return replace(
            _context(workpad, f"bound-acquire-{n}", f"run_{n:02d}"),
            project_id=resolved.project_id, gig_id=resolved.gig_id,
        )

    with pytest.raises(AcquireAllSourcesFailedError) as caught:
        acquire.callable(_run_context(1), _input())

    assert caught.value.code == SOURCES_UPDATE_REQUIRED_CODE, "nothing stored: the search says Run Update sources"
    assert boards.calls == [] and search.requests == []

    _update_sources(home)
    out = acquire.callable(_run_context(2), _input())

    assert boards.calls == [] and search.requests == [], "the bound acquire asked a board"
    assert [row.posting.title for row in out.rows] == ["Software Engineer", "Senior Software Engineer"]
    progress = read_progress(workpad / "runs" / "run_02").boards
    assert progress["source"] == "index" and progress["requests"] == 0 and progress["cached"] == 1


def test_the_child_of_a_run_binds_the_index_before_it_executes(
    unbound, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home, target, _workpad = _approved_scout(tmp_path)
    monkeypatch.setenv(bindings._HOME_ROOT_ENV, str(home))
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(run, "_worker_entry", lambda *args: seen.append(_registered_acquires()))
    graph = {"goals": [{"executor": {"capability": ACQUIRE_CAPABILITY}}]}

    class _Resolved:
        target_root = target.resolve()

    bindings._child_worker_entry(_Resolved(), "run_01", 1, graph, {}, "handoff_01", "sha256:" + "a" * 64)

    assert len(seen) == 1 and seen[0], "the child executed before it bound acquire"
    assert set(seen[0].values()) == {BOARDS_FROM_INDEX}, seen[0]


# --- the API (the UI's Find jobs) -----------------------------------------------


def test_post_api_run_launches_a_run_whose_acquire_reads_the_index(
    unbound, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home, target, _workpad = _approved_scout(tmp_path)
    launched: list[dict[str, object]] = []

    def _launch(**kwargs: object) -> str:
        launched.append(
            {
                "acquires": _registered_acquires(),
                "child_binds": run._worker_entry is bindings._child_worker_entry,
                "home": kwargs["home_root"],
            }
        )
        return "run_entrypoint_01"

    monkeypatch.setattr(run, "launch_find_jobs_run", _launch)
    server = present_api.serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0, trust_env=False) as client:
            config = client.get("/api/config")
            assert config.status_code == 200, config.text
            request = {**load_fixture("fixture-api-run-request-v1.json"), "config_digest": config.json()["config_digest"]}
            started = client.post("/api/run", json=request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert started.status_code == 202, started.text
    assert started.json()["run_id"] == "run_entrypoint_01"
    assert len(launched) == 1
    assert launched[0]["child_binds"] is True
    assert launched[0]["acquires"] and set(launched[0]["acquires"].values()) == {BOARDS_FROM_INDEX}, launched[0]


# --- the CLI ---------------------------------------------------------------------


def _assert_runs_through_the_binding(backend: object) -> None:
    """The served backend is the one whose run the API test above launches."""

    assert type(backend) is ScoutFindJobsBackend
    source = inspect.getsource(ScoutFindJobsBackend.start_run)
    assert "register_find_jobs_nodes(home_root=self.home_root, target=target)" in source


def test_gigai_scout_run_foreground_serves_the_backend_that_binds_the_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home, target = _setup_and_init(tmp_path)
    served: list[object] = []
    monkeypatch.setattr(present_api, "_run_forever", lambda bind, *, backend=None: served.append(backend))

    result = CliRunner().invoke(
        cli,
        ["scout", "run", "--foreground", "--no-browser", "--port", str(_free_port()), "--home", str(home), "--target", str(target), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert len(served) == 1
    _assert_runs_through_the_binding(served[0])


def test_gigai_scout_run_detached_starts_the_server_module_that_binds_the_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home, target = _setup_and_init(tmp_path)
    spawned: list[list[str]] = []

    class _NotStarted(Exception):
        pass

    real_popen = run_supervisor.subprocess.Popen

    def _popen(argv, *args, **kwargs):
        # Only the server spawn is refused; setup's own git calls run.
        if present_api.__name__ not in list(argv):
            return real_popen(argv, *args, **kwargs)
        spawned.append(list(argv))
        raise _NotStarted

    monkeypatch.setattr(run_supervisor.subprocess, "Popen", _popen)
    with pytest.raises(_NotStarted):
        run_supervisor.start(
            home_root=home, requested_target=target, port=_free_port(), foreground=False, open_browser=False
        )

    assert len(spawned) == 1
    argv = spawned[0]
    assert argv[1:3] == ["-m", present_api.__name__], argv

    # What that module does with the same arguments: serve the real backend.
    served: list[object] = []
    monkeypatch.setattr(present_api, "_run_forever", lambda bind, *, backend=None: served.append(backend))
    present_api.main(argv[3:])

    assert len(served) == 1
    _assert_runs_through_the_binding(served[0])
    assert served[0].home_root == home.resolve()
