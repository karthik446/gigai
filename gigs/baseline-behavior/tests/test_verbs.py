"""The verbs against a loopback `http.server` and a scratch gigai home: no network, no model call."""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import sqlite3
import threading
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from gigai.sdk import GigContext

from gigai_baseline_behavior.cli import baseline_behavior

NAME = "baseline-behavior"


class Service:
    """A loopback HTTP service whose answers the test changes: ``routes[(method, path)] = (status, body)``.

    ``/drop`` hangs up without answering; an unknown route answers 404."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, bytes]] = {}
        self.seen: list[tuple[str, str, bytes]] = []
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                service.seen.append((self.command, self.path, body))
                if self.path == "/drop":
                    self.close_connection = True
                    return
                status, payload = service.routes.get((self.command, self.path), (404, b"not found"))
                self.send_response(status)
                if status in (301, 302):
                    self.send_header("Location", "http://198.51.100.1/elsewhere")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _answer

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def service() -> Iterator[Service]:
    service = Service()
    thread = threading.Thread(target=service.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service
    finally:
        service.server.shutdown()
        service.server.server_close()
        thread.join(timeout=5)


def dead_url() -> str:
    """A loopback URL nothing listens on."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def gig(tmp_path: Path) -> GigContext:
    manifest = tomllib.loads(resources.files("gigai_baseline_behavior").joinpath("gig.toml").read_text(encoding="utf-8"))
    return GigContext(tmp_path / "home", NAME, manifest, logging.getLogger("test"))


def run(gig: GigContext, *args: str) -> Result:
    return CliRunner().invoke(baseline_behavior, list(args), obj=gig)


def requests_file(tmp_path: Path, *rows: dict) -> str:
    path = tmp_path / "requests.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return str(path)


def store_rows(gig: GigContext) -> dict[tuple[str, str], tuple[int, bytes, str]]:
    database = sqlite3.connect(gig.home / "gigs" / NAME / "baseline.sqlite")
    try:
        return {
            (snapshot, key): (status, body, digest)
            for snapshot, key, status, body, digest in database.execute(
                "SELECT snapshot, request_key, status, response_body, content_sha256 FROM responses"
            )
        }
    finally:
        database.close()


def test_init_probes_and_writes_config(gig: GigContext, service: Service) -> None:
    service.routes[("GET", "/")] = (200, b"ok")
    result = run(gig, "init", "--target", service.url + "/")
    assert result.exit_code == 0, result.output
    config = tomllib.loads((gig.home / "gigs" / NAME / "config.toml").read_text(encoding="utf-8"))
    assert config == {"target": service.url, "timeout_seconds": 10}
    assert service.seen == [("GET", "/", b"")]


def test_init_refuses_a_bad_or_silent_target(gig: GigContext) -> None:
    assert run(gig, "init", "--target", "ftp://example.test").exit_code == 2
    result = run(gig, "init", "--target", dead_url())
    assert result.exit_code == 1
    assert "no answer" in result.output
    assert not (gig.home / "gigs" / NAME / "config.toml").exists()


@pytest.mark.parametrize(
    ("broken", "kept_timeout", "dropped"),
    [
        ("target = 'ftp://x'\ntimeout_seconds = 3\nnoise = 1\n", 3, "noise"),
        ("target = 5\ntimeout_seconds = -1\n", 10, "timeout_seconds"),
        ("this is [[[ not toml", 10, "the unreadable config.toml"),
    ],
)
def test_init_repairs_a_hand_broken_config(gig: GigContext, service: Service, broken: str, kept_timeout: int, dropped: str) -> None:
    service.routes[("GET", "/")] = (200, b"ok")
    config = gig.home / "gigs" / NAME / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(broken, encoding="utf-8")
    assert run(gig, "capture", "--snapshot", "x").exit_code != 0  # every other verb refuses the broken file
    result = run(gig, "init", "--target", service.url)
    assert result.exit_code == 0, result.output
    assert f"dropped from the old config.toml: {dropped}" in result.output
    assert tomllib.loads(config.read_text(encoding="utf-8")) == {"target": service.url, "timeout_seconds": kept_timeout}
    assert run(gig, "capture", "--snapshot", "x").exit_code == 0


def test_capture_twice_diff_report(gig: GigContext, service: Service, tmp_path: Path) -> None:
    service.routes[("GET", "/")] = (200, b"home")
    service.routes[("GET", "/users")] = (200, b'[{"id":1}]')
    service.routes[("POST", "/users")] = (201, b'{"id":2}')
    assert run(gig, "init", "--target", service.url).exit_code == 0
    file = requests_file(
        tmp_path,
        {"method": "GET", "path": "/users"},
        {"path": "/missing"},
        {"method": "POST", "path": "/users", "body": {"name": "ada"}},
    )
    first = run(gig, "capture", "--snapshot", "one", "--requests", file)
    assert first.exit_code == 0, first.output
    assert ("POST", "/users", b'{"name":"ada"}') in service.seen

    service.routes[("GET", "/users")] = (200, b'[{"id":1},{"id":3}]')
    assert run(gig, "capture", "--snapshot", "two", "--requests", file).exit_code == 0

    rows = store_rows(gig)
    assert rows[("one", "GET /users")][2] == hashlib.sha256(b'200\n[{"id":1}]').hexdigest()
    assert rows[("two", "GET /users")][2] == hashlib.sha256(b'200\n[{"id":1},{"id":3}]').hexdigest()
    assert rows[("one", "GET /missing")][:2] == (404, b"not found")
    assert len([key for snapshot, key in rows if snapshot == "one" and key.startswith("POST /users body:")]) == 1

    changed = run(gig, "diff", "one", "two")
    assert changed.exit_code == 1
    assert "changed  GET /users  status 200 -> 200" in changed.output
    assert "one vs two: 1 differ, 2 same" in changed.output
    assert run(gig, "diff", "one", "one").exit_code == 0
    assert run(gig, "diff", "one", "nope").exit_code == 2

    report = run(gig, "report", "--snapshot", "two", "--json")
    assert report.exit_code == 0
    data = json.loads(report.output)
    assert data["requests"] == 3
    assert data["statuses"] == {"200": 1, "201": 1, "404": 1}
    assert data["target"] == service.url


def test_diff_names_requests_only_one_side_has(gig: GigContext, service: Service, tmp_path: Path) -> None:
    service.routes[("GET", "/")] = (200, b"home")
    service.routes[("GET", "/old")] = (200, b"old")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    assert run(gig, "capture", "--snapshot", "a", "--requests", requests_file(tmp_path, {"path": "/"}, {"path": "/old"})).exit_code == 0
    assert run(gig, "capture", "--snapshot", "b", "--requests", requests_file(tmp_path, {"path": "/"}, {"path": "/new"})).exit_code == 0
    result = run(gig, "diff", "a", "b")
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "added    GET /new  status - -> 404",
        "removed  GET /old  status 200 -> -",
        "a vs b: 2 differ, 1 same",
    ]


def test_snapshots_are_immutable(gig: GigContext, service: Service) -> None:
    service.routes[("GET", "/")] = (200, b"v1")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    assert run(gig, "capture", "--snapshot", "base").exit_code == 0
    service.routes[("GET", "/")] = (200, b"v2")
    again = run(gig, "capture", "--snapshot", "base")
    assert again.exit_code == 1
    assert "never change" in again.output
    assert [body for _, body, _ in store_rows(gig).values()] == [b"v1"]


def test_capture_stores_nothing_when_a_request_gets_no_answer(gig: GigContext, service: Service, tmp_path: Path) -> None:
    service.routes[("GET", "/")] = (200, b"ok")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    file = requests_file(tmp_path, {"path": "/"}, {"path": "/drop"})
    result = run(gig, "capture", "--snapshot", "down", "--requests", file)
    assert result.exit_code == 1
    assert f"GET {service.url}/drop: no answer" in result.output
    assert run(gig, "report", "--snapshot", "down").exit_code == 2


def test_redirects_are_recorded_not_followed(gig: GigContext, service: Service) -> None:
    service.routes[("GET", "/")] = (302, b"")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    result = run(gig, "capture", "--snapshot", "moved")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("302 ")
    assert len(service.seen) == 2  # the init probe and the capture; never the Location


def test_bad_input_is_refused(gig: GigContext, service: Service, tmp_path: Path) -> None:
    assert run(gig, "capture", "--snapshot", "x").exit_code == 2  # no target yet
    service.routes[("GET", "/")] = (200, b"ok")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    for rows in ([{"path": "http://elsewhere/"}], [{"path": "/a"}, {"path": "/a"}], [{"path": "/a", "headers": {}}]):
        assert run(gig, "capture", "--snapshot", "x", "--requests", requests_file(tmp_path, *rows)).exit_code == 2
    assert run(gig, "capture", "--snapshot", "bad name").exit_code == 2


def test_generate_tests_dry_run_prints_the_prompt(gig: GigContext, service: Service) -> None:
    service.routes[("GET", "/")] = (200, b"hello")
    assert run(gig, "init", "--target", service.url).exit_code == 0
    assert run(gig, "capture", "--snapshot", "base").exit_code == 0
    dry = run(gig, "generate-tests", "--snapshot", "base", "--dry-run")
    assert dry.exit_code == 0
    assert f"the HTTP service at {service.url}" in dry.output
    assert "### GET /\nrequest: GET /\nstatus: 200\nresponse body:\n```\nhello\n```" in dry.output
    later = run(gig, "generate-tests", "--snapshot", "base")
    assert later.exit_code == 2
    assert "ships later" in later.output


def test_outside_gigai_is_a_usage_error() -> None:
    assert CliRunner().invoke(baseline_behavior, ["report", "--snapshot", "x"]).exit_code == 2
