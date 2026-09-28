"""http-no-lookup: no HTTP server in the product waits on a reverse-DNS lookup.

Stock ``HTTPServer.server_bind`` calls ``socket.getfqdn(host)`` after
``bind()`` and before ``listen()``. On GitHub's macOS runners that lookup of
``127.0.0.1`` blocks for more than 30 s (actions/setup-python#1223), and the
port refuses every connection until it returns. The tests below inject a
lookup that blocks for 35 s and require every server the product builds to
bind and answer promptly without making it.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import click
import pytest
from click.testing import CliRunner

import gigai
import gigai.cli as gigai_cli
from gigai.canonical import canonical_json_bytes
from gigai.http_server import NoLookupHTTPServer, NoLookupThreadingHTTPServer
from gigai.learning import publish_learning_record
from gigai.lifecycle import approve_offline, create_offline
from gigai.proposal_interview import InterviewHTTPServer, ReferenceDecision, build_session
from gigai.setup import build_config, run_setup
from gigai.setup_interview import SetupDraft, SetupHTTPServer
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from tests.behaviors.runtime_run_authority.test_g20_learning_runtime import (
    _manifest,
    _prepare_observation,
    _record,
)
from tests.support.latency import latency_bound

_SLOW_LOOKUP_SECONDS = 35.0
_PROMPT_SECONDS = 5.0
_SHA = "sha256:" + "1" * 64


class _BlockedLookup:
    """A ``socket.getfqdn`` that blocks, and the threads started under it."""

    def __init__(self) -> None:
        self.lookups: list[str] = []
        self.release = threading.Event()
        self._threads: list[threading.Thread] = []

    def getfqdn(self, name: str = "") -> str:
        self.lookups.append(name)
        self.release.wait(_SLOW_LOOKUP_SECONDS)
        return name

    def promptly(self, call, *, what: str, seconds: float = _PROMPT_SECONDS):
        """Run ``call`` on a thread; fail unless it returns within the bound."""

        results: list[object] = []
        failures: list[BaseException] = []

        def _run() -> None:
            try:
                results.append(call())
            except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
                failures.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        self._threads.append(thread)
        thread.start()
        thread.join(latency_bound(seconds))
        if failures:
            raise failures[0]
        assert results, f"{what} did not return while the reverse-DNS lookup was blocked"
        return results[0]

    def finish(self) -> None:
        self.release.set()
        for thread in self._threads:
            thread.join(5.0)


@pytest.fixture
def blocked_lookup(monkeypatch):
    blocked = _BlockedLookup()
    monkeypatch.setattr(socket, "getfqdn", blocked.getfqdn)
    try:
        yield blocked
    finally:
        blocked.finish()


class _OkHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _get(url: str) -> tuple[int, bytes]:
    with urlopen(url, timeout=latency_bound(_PROMPT_SECONDS)) as response:
        return response.status, response.read()


def _setup_server() -> SetupHTTPServer:
    return SetupHTTPServer(
        SetupDraft("/tmp/gigai", "/tmp/gigai/workpads", "/usr/bin/true", False, "codex-default"),
        model_options=({"id": "codex-default", "label": "Codex CLI", "description": "Local."},),
        detected_models=(),
        on_apply=lambda _draft: {"status": "ok"},
    )


def _interview_server() -> InterviewHTTPServer:
    return InterviewHTTPServer(
        build_session(
            session_id="session_00000000-0000-4000-8000-000000000001",
            project_id="project_00000000-0000-4000-8000-000000000002",
            gig_id="gig_00000000-0000-4000-8000-000000000003",
            request_kind="repository-feature",
            request_artifact={
                "path": "draft/request.txt",
                "content_sha256": _SHA,
                "media_type": "text/plain",
                "size_bytes": 12,
            },
            request_sha256=_SHA,
            references=(ReferenceDecision("ref_00000000-0000-4000-8000-000000000005", _SHA),),
            max_rounds=3,
            now="2026-08-09T00:00:00Z",
        )
    )


def test_the_injected_lookup_blocks_a_stock_server(blocked_lookup) -> None:
    # The control: the same injection stalls the stdlib's own bind, so the
    # tests below cannot pass because the lookup was never reachable.
    servers: list[HTTPServer] = []
    starter = threading.Thread(
        target=lambda: servers.append(ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)),
        daemon=True,
    )
    starter.start()
    starter.join(1.0)
    try:
        assert servers == [], "a stock server bound without waiting for the lookup"
        assert blocked_lookup.lookups == ["127.0.0.1"]
    finally:
        blocked_lookup.release.set()
        starter.join(5.0)
        for server in servers:
            server.server_close()


@pytest.mark.parametrize("server_class", [NoLookupHTTPServer, NoLookupThreadingHTTPServer])
def test_core_server_binds_and_answers_while_the_lookup_is_blocked(blocked_lookup, server_class) -> None:
    server = blocked_lookup.promptly(
        lambda: server_class(("127.0.0.1", 0), _OkHandler), what=server_class.__name__
    )
    loop = threading.Thread(target=server.serve_forever, daemon=True)
    loop.start()
    try:
        host, port = server.server_address[:2]
        assert _get(f"http://127.0.0.1:{port}/") == (200, b"ok")
        assert (server.server_name, server.server_port) == (host, port) == ("127.0.0.1", port)
    finally:
        server.shutdown()
        server.server_close()
        loop.join(5.0)
    assert blocked_lookup.lookups == []


def test_core_threading_server_is_still_a_threading_http_server() -> None:
    assert issubclass(NoLookupThreadingHTTPServer, ThreadingHTTPServer)
    assert issubclass(NoLookupThreadingHTTPServer, NoLookupHTTPServer)
    assert NoLookupThreadingHTTPServer.daemon_threads is True
    assert NoLookupThreadingHTTPServer.process_request is ThreadingHTTPServer.process_request
    assert NoLookupThreadingHTTPServer.server_bind is NoLookupHTTPServer.server_bind


def test_setup_server_binds_and_answers_while_the_lookup_is_blocked(blocked_lookup) -> None:
    server = blocked_lookup.promptly(lambda: _setup_server().start(), what="SetupHTTPServer")
    try:
        status, body = _get(server.url)
        assert status == 200
        assert b"GigAI setup" in body
        assert isinstance(server._server, NoLookupThreadingHTTPServer)
    finally:
        server.close()
    assert blocked_lookup.lookups == []


def test_interview_server_binds_and_answers_while_the_lookup_is_blocked(blocked_lookup) -> None:
    server = blocked_lookup.promptly(lambda: _interview_server().start(), what="InterviewHTTPServer")
    try:
        status, body = _get(server.url)
        assert status == 200
        assert b"Define a Gig" in body
        assert isinstance(server._server, NoLookupThreadingHTTPServer)
    finally:
        server.close()
    assert blocked_lookup.lookups == []


def test_scout_server_binds_and_answers_health_while_the_lookup_is_blocked(blocked_lookup) -> None:
    from gigai.scout.find_jobs.api.server import serve

    server = blocked_lookup.promptly(lambda: serve(bind=("127.0.0.1", 0)), what="Scout's serve()")
    loop = threading.Thread(target=server.serve_forever, daemon=True)
    loop.start()
    try:
        status, body = _get(f"http://127.0.0.1:{server.server_address[1]}/api/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}
        assert isinstance(server, NoLookupThreadingHTTPServer)
    finally:
        server.shutdown()
        server.server_close()
        loop.join(5.0)
    assert blocked_lookup.lookups == []


class _Visitor:
    """Stands in for the operator's browser: opens the page, then closes the server."""

    def __init__(self, monkeypatch, name: str) -> None:
        self.servers: list[object] = []
        self.pages: list[tuple[int, bytes]] = []
        visitor = self

        class Recorded(getattr(gigai_cli, name)):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                visitor.servers.append(self)

        def _open(url: str, *_args: object, **_kwargs: object) -> bool:
            visitor.pages.append(_get(url))
            visitor.servers[-1].close()
            return True

        monkeypatch.setattr(gigai_cli, name, Recorded)
        monkeypatch.setattr(gigai_cli.webbrowser, "open", _open)


def test_cli_setup_page_answers_while_the_lookup_is_blocked(blocked_lookup, monkeypatch, tmp_path: Path) -> None:
    visitor = _Visitor(monkeypatch, "SetupHTTPServer")
    # Keep the page independent of the CLIs installed on this machine.
    monkeypatch.setattr(gigai_cli, "discover_installed_models", lambda: ())

    def _setup() -> str:
        with pytest.raises(click.ClickException) as excinfo:
            gigai_cli._run_browser_setup(
                home_value=tmp_path / "home",
                workpad_root=None,
                editor="/usr/bin/true",
                open_with_target=None,
                create_model_target=None,
                credential_ref=(),
                clear_credentials=False,
                endpoint_spec=(),
                model_target_spec=(),
                target_output_limit_spec=(),
                target_reasoning_effort_spec=(),
                as_json=True,
                open_browser=True,
            )
        return excinfo.value.message

    message = blocked_lookup.promptly(_setup, what="the CLI's setup page", seconds=10.0)

    # The visitor closed the page without submitting it.
    assert message == "setup was cancelled or expired; no changes were applied"
    assert [status for status, _ in visitor.pages] == [200]
    assert b"GigAI setup" in visitor.pages[0][1]
    assert isinstance(visitor.servers[0]._server, NoLookupThreadingHTTPServer)
    assert not (tmp_path / "home" / "config.toml").exists()
    assert blocked_lookup.lookups == []


def test_cli_improve_interview_answers_while_the_lookup_is_blocked(
    blocked_lookup, monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="g20-base", open_editor=False)
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    record = _record()
    record["project_id"] = created.project_id
    record["gig_id"] = created.gig_id
    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True
    )
    pointer_path = resolved.path / "manifests/active-gig-version.json"
    _prepare_observation(home, record, pointer_path)
    publish_learning_record(home_root=home, record=record, source_root=home, active_pointer_path=pointer_path)
    manifest = _manifest([record["learning_id"]])
    manifest["project_id"] = created.project_id
    manifest["gig_id"] = created.gig_id
    manifest["parent_proposal_id"] = created.proposal_id
    manifest_path = tmp_path / "improve-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"improve evidence\n")

    visitor = _Visitor(monkeypatch, "InterviewHTTPServer")
    result = blocked_lookup.promptly(
        lambda: CliRunner().invoke(
            gigai_cli.improve_command,
            [
                str(manifest_path),
                "--request",
                "Tighten the rubric from observed review outcomes.",
                "--reference",
                str(evidence),
                "--target",
                str(target),
                "--home",
                str(home),
                "--model-target",
                "offline-default",
                "--open",
                "--json",
            ],
        ),
        what="the CLI's improve interview",
        seconds=20.0,
    )

    assert result.exit_code == 0, result.output
    assert [status for status, _ in visitor.pages] == [200]
    assert isinstance(visitor.servers[0]._server, NoLookupThreadingHTTPServer)
    assert blocked_lookup.lookups == []


# --- no server in src/gigai uses the stock bind -----------------------------

_SOURCE_ROOT = Path(gigai.__file__).resolve().parent
_CORE_MODULE = _SOURCE_ROOT / "http_server.py"
# ``WSGIServer`` and ``make_server`` build on ``HTTPServer`` and keep its bind.
_STOCK_SERVERS = frozenset({"HTTPServer", "ThreadingHTTPServer", "WSGIServer", "make_server"})


def _annotation_nodes(tree: ast.AST) -> set[int]:
    annotations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            annotations.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
    return {id(child) for annotation in annotations for child in ast.walk(annotation)}


def _stock_server_uses(source: str) -> list[str]:
    """Every use of a stock server in ``source`` other than a type annotation.

    A plain ``import`` of the name is not a use. Importing it under another
    name is reported, because a use of the alias would not be recognised.
    """

    tree = ast.parse(source)
    annotations = _annotation_nodes(tree)
    uses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            if node.name.split(".")[-1] in _STOCK_SERVERS and node.asname not in (None, node.name):
                uses.append((node.lineno, f"{node.name} imported as {node.asname}"))
            continue
        if id(node) in annotations:
            continue
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        else:
            continue
        if name in _STOCK_SERVERS:
            uses.append((node.lineno, name))
    return [f"line {line}: {use}" for line, use in sorted(uses)]


def _product_sources() -> list[Path]:
    return sorted(
        path
        for path in _SOURCE_ROOT.rglob("*.py")
        if path != _CORE_MODULE and "node_modules" not in path.parts
    )


def test_the_source_scan_recognises_every_way_to_use_a_stock_server() -> None:
    assert _stock_server_uses(
        "import http.server\n"
        "from http.server import ThreadingHTTPServer\n"
        "from http.server import HTTPServer as Server\n"
        "from wsgiref.simple_server import make_server\n"
        "class Mine(ThreadingHTTPServer):\n"
        "    pass\n"
        "class Theirs(http.server.HTTPServer):\n"
        "    pass\n"
        "one = http.server.ThreadingHTTPServer(('127.0.0.1', 0), None)\n"
        "two = ThreadingHTTPServer(('127.0.0.1', 0), None)\n"
        "factory = ThreadingHTTPServer\n"
        "three = make_server('127.0.0.1', 0, None)\n"
    ) == [
        "line 3: HTTPServer imported as Server",
        "line 5: ThreadingHTTPServer",
        "line 7: HTTPServer",
        "line 9: ThreadingHTTPServer",
        "line 10: ThreadingHTTPServer",
        "line 11: ThreadingHTTPServer",
        "line 12: make_server",
    ]
    assert _stock_server_uses(
        "from http.server import ThreadingHTTPServer\n"
        "from gigai.http_server import NoLookupThreadingHTTPServer\n"
        "class Mine(NoLookupThreadingHTTPServer):\n"
        "    pass\n"
        "def serve() -> ThreadingHTTPServer:\n"
        "    server: ThreadingHTTPServer = Mine(('127.0.0.1', 0), None)\n"
        "    return server\n"
    ) == []


def test_no_product_module_builds_or_subclasses_a_stock_server() -> None:
    sources = _product_sources()
    assert len(sources) > 50, "the scan did not find the product's source"

    offenders = {
        str(path.relative_to(_SOURCE_ROOT)): uses
        for path in sources
        if (uses := _stock_server_uses(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, (
        "build HTTP servers from gigai.http_server (NoLookupHTTPServer / "
        f"NoLookupThreadingHTTPServer), not the stock classes: {offenders}"
    )


def test_every_server_class_in_the_product_derives_from_the_core_class() -> None:
    server_classes: dict[str, type] = {}
    for path in _product_sources():
        if "HTTPServer" not in path.read_text(encoding="utf-8"):
            continue
        relative = path.relative_to(_SOURCE_ROOT).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        module = importlib.import_module(".".join(["gigai", *parts]))
        for name, value in vars(module).items():
            if inspect.isclass(value) and issubclass(value, HTTPServer) and value.__module__ == module.__name__:
                server_classes[f"{module.__name__}.{name}"] = value

    assert "gigai.scout.find_jobs.api.server._ScoutHTTPServer" in server_classes
    for name, server_class in server_classes.items():
        assert issubclass(server_class, NoLookupHTTPServer), name
        assert server_class.server_bind is NoLookupHTTPServer.server_bind, name


def test_the_cli_builds_the_setup_and_interview_servers_it_imports() -> None:
    # cli.py has no server class of its own: its two pages are these wrappers.
    assert gigai_cli.SetupHTTPServer is SetupHTTPServer
    assert gigai_cli.InterviewHTTPServer is InterviewHTTPServer
