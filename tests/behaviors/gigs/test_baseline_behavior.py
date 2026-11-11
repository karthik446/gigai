"""`gigs/baseline-behavior`, the first gig outside the `gigai` wheel, installed editable the way `test_gig_new` installs
a generated gig (its `src/` and a synthetic dist-info on PYTHONPATH; no index): `gig check` passes, its verbs work end
to end through `gigai baseline-behavior ...` against a loopback `http.server`, its own tests pass, and it keeps the
scaffold's pins and no workpad. Every gig run is a subprocess with a scratch HOME, so no gig code enters this process.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gigai.gigs import manifest as gig_manifest
from tests.behaviors.gigs.test_gig_new import CLI, _editable, _outcomes, _run

ROOT = Path(__file__).resolve().parents[3]
GIG = ROOT / "gigs" / "baseline-behavior"
PACKAGE = GIG / "src" / "gigai_baseline_behavior"
# Directories a developer's run of the gig's own loop (`pytest`, `gigai gig check .`, a build) leaves behind, all
# gitignored: not the gig's sources.
LEFTOVER_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist"}


def workpad_mentions(project: Path) -> list[str]:
    """Files of ``project`` that mention a workpad, over its text sources only: leftover directories, bytecode and
    anything that is not UTF-8 text are skipped."""

    found = []
    for path in sorted(project.rglob("*")):
        parts = path.relative_to(project).parts
        if not path.is_file() or path.suffix in {".pyc", ".pyo"}:
            continue
        if any(part in LEFTOVER_DIRS or part.endswith(".egg-info") for part in parts):
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "workpad" in text:
            found.append("/".join(parts))
    return found



@pytest.fixture(autouse=True)
def _no_bytecode(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gig's sources live in the repository: its runs here leave no __pycache__ in them.
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture
def service() -> Iterator[tuple[str, dict[str, bytes]]]:
    """A loopback HTTP service answering ``GET <path>`` with 200 and ``answers[path]`` (404 when absent)."""

    answers: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = answers.get(self.path)
            payload = b"not found" if body is None else body
            self.send_response(404 if body is None else 200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", answers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def installed(tmp_path: Path) -> list[Path]:
    """PYTHONPATH of the gig installed editable under ``tmp_path`` (installed on first use)."""

    site = tmp_path / f"site-editable-{GIG.name}"
    return [site, GIG / "src"] if site.is_dir() else _editable(tmp_path, GIG)


def gigai(tmp_path: Path, *argv: str, cwd: Path | None = None):
    return _run(tmp_path, installed(tmp_path), "-c", CLI, *argv, cwd=cwd)


def test_it_keeps_the_scaffold_pins_and_no_workpad() -> None:
    manifest = tomllib.loads((PACKAGE / "gig.toml").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((GIG / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert gig_manifest.manifest_errors(manifest) == []
    assert manifest["gig"]["name"] == "baseline-behavior"
    assert manifest["gig"]["core"] == ">=0.2,<0.4"
    assert "workpad" not in manifest["gig"]
    assert manifest["commands"]["provides"] == ["init", "capture", "diff", "report", "generate-tests"]
    assert manifest["data"]["stores"] == [{"name": "baseline", "engine": "sqlite"}]
    assert pyproject["name"] == "gigai-baseline-behavior"
    assert pyproject["dependencies"] == ["gigai>=0.2.0a1,<0.4", "click>=8"]
    assert pyproject["entry-points"] == {"gigai.gigs": {"baseline-behavior": "gigai_baseline_behavior.cli:baseline_behavior"}}
    assert pyproject["description"] == manifest["gig"]["description"]
    assert workpad_mentions(GIG) == []


def test_the_workpad_scan_survives_the_gigs_own_check_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # AGENTS.md and README tell a developer to run `gigai gig check .` in the gig folder; its item 13 (pytest) and the
    # gig import leave __pycache__/*.pyc there. Run that loop on a copy, bytecode allowed, then scan the copy.
    copy = tmp_path / "copy" / GIG.name
    shutil.copytree(GIG, copy, ignore=shutil.ignore_patterns(*LEFTOVER_DIRS, "*.egg-info", "*.pyc"))
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE")
    check = _run(tmp_path, _editable(tmp_path, copy), "-c", CLI, "gig", "check", ".", cwd=copy)
    assert check.returncode == 0, check.stdout + check.stderr
    assert list(copy.rglob("*.pyc")), "the check loop left no bytecode, so this test proves nothing"
    assert workpad_mentions(copy) == []


def test_gig_check_passes(tmp_path: Path) -> None:
    result = gigai(tmp_path, "gig", "check", ".", cwd=GIG)
    outcomes = _outcomes(result.stdout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "gigai gig check baseline-behavior (gigai-baseline-behavior)"
    for item in ("1", "core", "2", "3", "4", "5", "6", "7", "8", "13"):
        assert outcomes.get(item) == {"PASS"}, (item, result.stdout)
    assert "FAIL" not in {outcome for found in outcomes.values() for outcome in found}


def test_capture_twice_then_diff_and_report(tmp_path: Path, service: tuple[str, dict[str, bytes]]) -> None:
    url, answers = service
    answers.update({"/": b"home", "/users": b'[{"id":1}]', "/health": b"ok"})
    requests = tmp_path / "requests.jsonl"
    requests.write_text('{"path": "/users"}\n{"method": "GET", "path": "/health"}\n', encoding="utf-8")

    assert gigai(tmp_path, "baseline-behavior", "init", "--target", url).returncode == 0
    first = gigai(tmp_path, "baseline-behavior", "capture", "--snapshot", "before", "--requests", str(requests))
    assert first.returncode == 0, first.stderr
    answers["/users"] = b'[{"id":1},{"id":2}]'
    assert gigai(tmp_path, "baseline-behavior", "capture", "--snapshot", "after", "--requests", str(requests)).returncode == 0

    diff = gigai(tmp_path, "baseline-behavior", "diff", "before", "after")
    assert diff.returncode == 1
    assert diff.stdout.splitlines() == ["changed  GET /users  status 200 -> 200", "before vs after: 1 differ, 1 same"]
    assert gigai(tmp_path, "baseline-behavior", "diff", "before", "before").returncode == 0

    database = sqlite3.connect(tmp_path / ".gigai" / "gigs" / "baseline-behavior" / "baseline.sqlite")
    try:
        hashes = dict(database.execute("SELECT snapshot || ' ' || request_key, content_sha256 FROM responses"))
    finally:
        database.close()
    assert hashes == {
        "before GET /users": hashlib.sha256(b'200\n[{"id":1}]').hexdigest(),
        "before GET /health": hashlib.sha256(b"200\nok").hexdigest(),
        "after GET /users": hashlib.sha256(b'200\n[{"id":1},{"id":2}]').hexdigest(),
        "after GET /health": hashlib.sha256(b"200\nok").hexdigest(),
    }

    report = gigai(tmp_path, "baseline-behavior", "report", "--snapshot", "after", "--json")
    assert report.returncode == 0
    assert {key: value for key, value in json.loads(report.stdout).items() if key != "captured_at"} == {
        "snapshot": "after",
        "target": url,
        "requests": 2,
        "statuses": {"200": 2},
    }

    dry = gigai(tmp_path, "baseline-behavior", "generate-tests", "--snapshot", "after", "--dry-run")
    assert dry.returncode == 0
    assert "### GET /users" in dry.stdout
    assert gigai(tmp_path, "baseline-behavior", "generate-tests", "--snapshot", "after").returncode == 2


def test_capture_help_and_the_gigs_own_tests(tmp_path: Path) -> None:
    assert gigai(tmp_path, "baseline-behavior", "capture", "--help").returncode == 0
    own = _run(tmp_path, installed(tmp_path), "-m", "pytest", "-q", "tests", cwd=GIG)
    assert own.returncode == 0, own.stdout[-2000:]
