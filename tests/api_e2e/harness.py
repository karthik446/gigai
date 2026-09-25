"""test-gap-001: the real-server harness every API e2e journey builds on.

Every journey in this suite drives Scout **only through HTTP**, against the
real supervisor (``gigai.scout.run_supervisor.start``/``stop``, the same
entry `gigai scout run --no-browser` uses), a temp ``--home``, and a real
git-managed workpad (created through the normal ``gigai setup`` + ``gigai
init`` path -- see ``_setup_and_init`` in
``tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py``, which this
mirrors). Only the network edges are faked, through the three seams the M1/
uat-bug-005 real-backend tests (plus P6's Jev client) already use and prove
inert when unset (see ``bindings.py``'s own docstring):

- ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1`` -- swaps in an ``httpx.MockTransport``
  serving fixed Exa/Greenhouse-shaped JSON for acquire's HTTP client
  (``bindings._test_provider_handler``). No live network call reaches Exa or
  any ATS board.
- ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1`` -- swaps in an ``httpx.MockTransport``
  standing in for the local model adapter's Ollama-shaped calls
  (``bindings._test_model_handler``). No live model process is required.
- ``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1`` (P6) -- swaps in an
  ``httpx.MockTransport`` standing in for Jev's ``/v1/decide`` call
  (``bindings._test_jev_handler``), for both the acquire node's own ranking
  call and the ``/rank`` route's. No live Jev call is required; used by
  ``test_rank_journey.py``.

All three env vars are read only inside ``bindings.py``; production callers
(and every test in this repo that does not set them) leave them unset, so
the production path is untouched. this module never introduces a new seam.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import run_supervisor

# The inert-unless-set test seams bindings.py exposes for the real-backend
# M1/uat-bug-005 tests. Setting HTTP+MODEL makes a real find-jobs run fully
# offline: no live Exa/ATS/model call. P6 adds a third, JEV, for the rank
# journey (test_rank_journey.py) -- fake Jev responses, no live call.
TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"
TEST_JEV_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_JEV"

# Generous per-journey wait budgets. A find-jobs run against the fixture
# transports finishes in well under a second; these are loose enough to
# survive heavy parallel CPU contention without being a real UAT clock.
POLL_DEADLINE_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.05


def free_port() -> int:
    """An ephemeral, currently-unused loopback port.

    Never the operator's live Scout port (8765, ``run_supervisor.DEFAULT_PORT``):
    binding port 0 and reading back the OS-assigned port guarantees this
    suite never collides with a real running instance.
    """

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def setup_and_init(tmp_path: Path) -> tuple[Path, Path]:
    """Real ``gigai setup`` + ``gigai init`` into a temp home/target.

    Configures an ``ollama_local``-adapter model target named exactly
    ``ollama_local``, using ``bindings.TEST_MODEL_NAME``/``TEST_MODEL_DIGEST``
    -- the same sealed identity ``test_m1_end_to_end.py``'s ``_fixture``
    builds (there via ``build_config`` directly; here via the real ``gigai
    setup`` CLI, since this suite drives everything through public entry
    points once a server is involved). This is required for
    ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1`` to actually intercept assess's
    model call: ``bindings._patch_test_model_transport`` only substitutes a
    MockTransport for a target resolved onto the ``ollama_local`` adapter,
    so a differently-adapted target (e.g. the ``openai_api``-adapter target
    ``test_scout_run_supervisor.py``'s own ``_setup_and_init`` configures,
    for its seam-free journeys) would try a real network call and fail
    assess with "no configured model target uses adapter 'ollama_local'".
    The endpoint's loopback URL is never actually dialed (the MockTransport
    intercepts every call before any socket opens).
    """

    from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME

    return _setup_and_init(
        tmp_path,
        extra_setup_args=(
            "--endpoint",
            "ollama_local=ollama_local:http://127.0.0.1:11434",
            "--model-target",
            f"ollama_local=ollama_local:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
            "--create-model-target",
            "ollama_local",
        ),
    )


def setup_and_init_without_a_model_target(tmp_path: Path) -> tuple[Path, Path]:
    """Same as ``setup_and_init``, but with no target named exactly
    ``ollama_local``.

    For journeys (e.g. the failed-run-then-next-run-assesses repro) that
    need to add their OWN, deliberately ambiguous pair of
    ``ollama_local``-adapter targets afterward, with nothing already named
    exactly ``ollama_local`` to short-circuit the ambiguity via the
    exact-name-match rule (``_resolve_configured_target_name_for_adapter``).

    Configures a differently-named ``ollama_local``-adapter target
    (``seed-default``) and creates it explicitly via ``--create-model-target``.
    ``gigai setup`` requires *some* create-target: passing no endpoint/
    target/create-target at all falls back to auto-detecting a Codex/Claude
    executable already installed on the *host* running the test (see
    ``cli.py``'s "no usable model runtime is configured" error), which is
    true on a developer's machine but never true on a bare CI runner. Naming
    it something other than ``ollama_local`` keeps this hermetic while still
    leaving nothing named exactly ``ollama_local`` for the ambiguity check.
    """

    from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME

    return _setup_and_init(
        tmp_path,
        extra_setup_args=(
            "--endpoint",
            "seed=ollama_local:http://127.0.0.1:11434",
            "--model-target",
            f"seed-default=seed:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
            "--create-model-target",
            "seed-default",
        ),
    )


def _setup_and_init(tmp_path: Path, *, extra_setup_args: tuple[str, ...]) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)

    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            *extra_setup_args,
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output

    init_result = runner.invoke(
        cli,
        ["init", "--home", str(home), "--target", str(target), "--username", "api-e2e-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output
    return home, target


def add_resume(home: Path, target: Path, tmp_path: Path) -> None:
    """Import a resume through the real ``gigai scout resume add`` path.

    Several journeys (config, run) need ``resume_preview`` non-``None``;
    this is the same operator-facing path M1's ``_fixture`` uses
    (``import_reference`` + a record), done here through the CLI instead
    since this suite drives everything through public entry points, never
    library internals, once a server is involved.
    """

    resume_source = tmp_path / "resume.md"
    resume_source.write_text(
        "Software engineer with Python service experience.\n", encoding="utf-8"
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code == 0, result.output


def write_offline_find_jobs_config(target: Path, *, sources_live: bool = False) -> None:
    """Replace find-jobs.json's placeholder starter fields with real ones.

    ``install_scout``/``ensure_scout_ready`` writes
    ``STARTER_FIND_JOBS_CONFIG`` (``scout_cli.py``) the first time the
    server starts -- its ``roles``/``location`` are literal
    ``"REPLACE_WITH_..."`` placeholders never overwritten automatically, and
    acquire would find nothing real against them even with the fixture
    transport wired. This writes the same roles/location/sources shape M1's
    ``_fixture`` uses so a real acquire pass matches the fixture's
    Exa/Greenhouse-shaped postings. ``sources_live=True`` is used by the
    journeys that want that real acquire pass (against the
    ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP`` fixture); ``sources_live=False``
    (the default) keeps every source off, so acquire is a fast,
    guaranteed-empty no-op for journeys that only care about the HTTP
    contract around it.
    """

    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["roles"] = ["software engineer"]
    config["merged_queries"] = ["software engineer"]
    config["location"] = "Denver, CO"
    config["sources"] = {
        "exa": sources_live,
        "ats": sources_live,
        "hiringcafe": False,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")


@dataclass
class RunningServer:
    home: Path
    target: Path
    port: int
    pid: int
    base_url: str
    client: httpx.Client


def start_server(
    home: Path,
    target: Path,
    *,
    port: int | None = None,
    monkeypatch: pytest.MonkeyPatch,
    test_http: bool = True,
    test_model: bool = True,
    test_jev: bool = False,
) -> RunningServer:
    """Start the real supervised server (``gigai scout run --no-browser``).

    Sets the inert-unless-unset bindings.py seams in THIS process's
    environment before starting: the supervisor spawns the server as a
    child process (``subprocess.Popen``), which inherits the environment,
    and the child is where the seams are actually read (bindings.py is
    imported fresh in that child). A parent-constructed MockTransport could
    never reach the child either way (not picklable across the process
    boundary) -- this is exactly why the seam exists as an env var and not
    a Python-level monkeypatch/fixture.

    ``test_jev`` (P6) defaults to ``False``: only ``test_rank_journey.py``
    opts in, so every other journey's acquire node runs with no Jev key
    (fail open, today's ordering) exactly as before this packet -- proving
    the "no key" half of P6's own behavior contract for every other journey
    in this suite, not just its own.
    """

    if test_http:
        monkeypatch.setenv(TEST_HTTP_ENV, "1")
    if test_model:
        monkeypatch.setenv(TEST_MODEL_ENV, "1")
    if test_jev:
        monkeypatch.setenv(TEST_JEV_ENV, "1")
        monkeypatch.setenv("JEV_API_KEY", "api-e2e-test-jev-key")
    monkeypatch.setenv("EXA_API_KEY", "api-e2e-test-key")

    chosen_port = port if port is not None else free_port()
    result = run_supervisor.start(
        home_root=home,
        requested_target=target,
        port=chosen_port,
        foreground=False,
        open_browser=False,
        allow_test_seams=(test_http or test_model or test_jev),
    )
    base_url = result.state.url
    client = httpx.Client(base_url=base_url, timeout=20.0)
    return RunningServer(
        home=home,
        target=target,
        port=result.state.port,
        pid=result.state.pid,
        base_url=base_url,
        client=client,
    )


def stop_server(server: RunningServer) -> None:
    """Stop the supervised child and assert no leftover process survives.

    Idempotent (mirrors ``run_supervisor.stop``'s own contract); safe to
    call even if the server already died mid-test.
    """

    server.client.close()
    run_supervisor.stop(home_root=server.home, requested_target=server.target)
    assert not _process_is_alive(server.pid), (
        f"Scout server pid {server.pid} survived stop_server() -- a leftover "
        "process would corrupt every later test's ephemeral-port assumption"
    )


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def poll_until_terminal(client: httpx.Client, run_id: str, *, deadline_seconds: float = POLL_DEADLINE_SECONDS) -> dict[str, object]:
    """Poll ``GET /api/runs/{run_id}`` the way the UI does, until terminal."""

    deadline = time.monotonic() + deadline_seconds
    last_body: object = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        last_body = response.text
        if response.status_code == 200:
            body = response.json()
            if body["status"] in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}:
                return body
        elif response.status_code != 200:
            pytest.fail(f"GET /api/runs/{run_id} returned {response.status_code}: {response.text}")
            raise AssertionError("unreachable")
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"run {run_id} did not terminalize before timeout; last response={last_body!r}")
    raise AssertionError("unreachable")


def resolve_workpad_path(home: Path, target: Path) -> Path:
    """The real managed workpad's git root for ``(home, target)``.

    ``target`` (from ``setup_and_init``) is a plain, non-git "requested
    target" folder; the actual journaled, git-managed workpad a run writes
    into lives elsewhere, under the configured ``workpad_root``
    (``<tmp_path>/workpads`` here) -- exactly what ``ScoutFindJobsBackend``
    itself resolves on every call (``_resolved_run``). After-journey
    cleanliness/doctor checks must run against this path, never ``target``.
    """

    from gigai.workpad import resolve_workpad

    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=None,
        allow_semantic_state=True,
    )
    return resolved.path


def run_request_body(config_digest: str, **overrides: object) -> dict[str, object]:
    """The canonical ``POST /api/run`` body, from the same fixture the
    real-backend M1/assess-failure tests use."""

    from tests.behaviors.scout_find_jobs.conftest import load_fixture

    request = load_fixture("fixture-api-run-request-v1.json")
    return {**request, "config_digest": config_digest, **overrides}


__all__ = [
    "TEST_HTTP_ENV",
    "TEST_MODEL_ENV",
    "RunningServer",
    "add_resume",
    "free_port",
    "poll_until_terminal",
    "resolve_workpad_path",
    "run_request_body",
    "setup_and_init",
    "setup_and_init_without_a_model_target",
    "start_server",
    "stop_server",
    "write_offline_find_jobs_config",
]
