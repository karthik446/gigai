"""0.1.11.3 item 13: the privacy rules of the header file that are about the CODE and about one server process.

``tests/api_e2e/test_pdf_header_file_journey.py`` holds the rules a real server shows from outside (no other route
returns a value; nothing on disk).  Here:

- BY STRUCTURE: only the form's prefill route and ``gigai scout resume pdf`` can read the file at all (no store,
  journal, record, brief, suggestion or prompt module imports the reader), and the prefill route is the one JSON
  answer written around the outbound check;
- NO MODEL PROMPT: in one process that has just read the file for the form and for a PDF, the requests an
  assessment and a tailoring send to the model hold no value of it;
- NO LOG: with every logger at DEBUG, the prefill (filled, invalid, refused) and the PDF log no value.

Synthetic values and tmp homes only.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import threading
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

import gigai
from gigai.cli import cli
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.api.server import LOGGER_NAME, serve
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend

from tests.api_e2e.harness import setup_and_init, write_offline_find_jobs_config
from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS

SRC = Path(gigai.__file__).resolve().parent
READER = "pdf_header_file"
#: The two places that may read the user's header file, and the function of each that does.
READERS = {
    "scout/find_jobs/api/pdf_header.py": "_handle_post_pdf_header",  # the Generate PDF form's prefill: Scout's own page only
    "scout/scout_cli.py": "resume_pdf_command",  # `gigai scout resume pdf`: the values go into the one PDF at --out
}
RESUME = "## Experience\n### Northwind Health\n- Rebuilt the scheduling service on Python and Postgres.\n\n## Skills\nPython · Postgres\n"
_JOB = {"job_text": "Acme is hiring a Staff Engineer for scheduling and billing systems. Requirements: Python, Postgres.", "title": "Staff Engineer", "company": "Acme"}


def _imports_the_reader(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (READER in (node.module or "") or any(alias.name == READER for alias in node.names)):
            return True
        if isinstance(node, ast.Import) and any(READER in alias.name for alias in node.names):
            return True
    return False


def test_only_the_form_route_and_the_pdf_command_can_read_the_file() -> None:
    """No store, journal, record, brief, suggestion, resumes-folder or prompt module imports the reader, so none can hold its values."""
    importers = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative == f"scout/{READER}.py":
            continue
        source = path.read_text(encoding="utf-8")
        if READER in source or "header.json" in source:
            tree = ast.parse(source)
            if _imports_the_reader(tree):
                importers[relative] = tree
            else:
                # A mention in words (a docstring, a help text) is fine; a path built by hand is not.
                assert "read_header_file" not in source and '"header.json"' not in source, f"{relative} names the header file without the reader"
    assert sorted(importers) == sorted(READERS), "a new reader of the user's header file needs a privacy review and a row in READERS"
    for relative, tree in importers.items():
        holders = {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(node, (ast.Name, ast.Attribute)) and READER in ast.unparse(node) for node in ast.walk(function))
        }
        calls = {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(node, ast.Call) and ast.unparse(node.func).endswith("read_header_file") for node in ast.walk(function))
        }
        assert calls == {READERS[relative]}, f"{relative}: the file is read in {sorted(calls)}"
        assert holders <= {READERS[relative]}, f"{relative}: {sorted(holders)} use the reader module"


def test_the_prefill_route_is_the_one_json_answer_written_around_the_outbound_check() -> None:
    """Every other JSON response leaves through ``_write_json`` (``redact_payload``); this one is the person's own details for their own form."""
    api = SRC / "scout" / "find_jobs" / "api"
    around = sorted(
        path.name
        for path in api.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._write_bytes"
        and any(isinstance(arg, ast.Constant) and arg.value == "application/json" for arg in node.args)
    )
    assert around == ["pdf_header.py"], around
    route = (api / "pdf_header.py").read_text(encoding="utf-8")
    assert route.index('self.headers.get("Origin")') < route.index("read_header_file(default_path(home_root))"), "the caller is checked before the file is opened"
    assert '"Cache-Control": "no-store"' in route and "_logger" not in route and "logging" not in route


@pytest.fixture
def running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One in-process server over a real synthetic home, every logger at DEBUG into a file, every model request recorded."""
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(RESUME, encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"])
    if (target / "find-jobs.json").is_file():
        write_offline_find_jobs_config(target)
    header_file = home / "header.json"
    header_file.write_text(json.dumps(FILE), encoding="utf-8")
    os.chmod(header_file, 0o600)

    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    sent: list[bytes] = []
    answer = bindings._test_model_handler

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.encode() + b"\n" + request.content)
        return answer(request)

    monkeypatch.setattr(bindings, "_test_model_handler", recording)

    log_file = tmp_path / "everything.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    root, logger = logging.getLogger(), logging.getLogger(LOGGER_NAME)
    previous = (root.level, logger.level)
    for item in (root, logger):
        item.addHandler(handler)
        item.setLevel(logging.DEBUG)
    httpd = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with httpx.Client(base_url=base, timeout=120.0) as client:
            yield client, {"Origin": base}, header_file, sent, log_file
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        for item, level in zip((root, logger), previous):
            item.removeHandler(handler)
            item.setLevel(level)
        handler.close()


def test_no_model_prompt_and_no_log_holds_a_value_of_the_file(running) -> None:
    client, page, header_file, sent, log_file = running

    # The file is read in THIS process, for the form and for a PDF made from the form ...
    filled = client.post("/api/pdf-header", json={}, headers=page)
    assert filled.status_code == 200 and filled.json()["state"] == "filled", filled.text
    values = filled.json()["values"]
    assert values["name"] == "Zora Quillfeather"
    made = client.post("/api/resume/pdf", json={"markdown": RESUME, "header": values}, headers=page)
    assert made.status_code == 200 and made.content.startswith(b"%PDF"), made.text
    # ... refused for a caller that is not Scout's page, and read again when it is broken ...
    assert client.post("/api/pdf-header", json={}).status_code == 403
    header_file.write_text('{"name": "Zora Quillfeather", "phone": ["555-0142-ZQ"]}', encoding="utf-8")
    assert client.post("/api/pdf-header", json={}, headers=page).json()["state"] == "invalid"
    header_file.write_text(json.dumps(FILE), encoding="utf-8")
    assert client.post("/api/pdf-header", json={}, headers=page).json()["state"] == "filled"

    # ... and then the model is called: an assessment and a tailoring.
    assessed = client.post("/api/assess", json={"job": _JOB})
    assert assessed.status_code == 200, assessed.text
    tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
    assert tailored.status_code == 200, tailored.text

    prompts = [request for request in sent if b"Northwind" in request or b"Staff Engineer" in request]
    assert len(prompts) >= 2, f"the model requests were recorded ({len(sent)} requests, {len(prompts)} with the resume or the posting)"
    for request in sent:
        for marker in MARKERS:
            assert marker.encode() not in request, f"a model request holds {marker!r}"

    logged = log_file.read_text(encoding="utf-8")
    assert "/api/pdf-header" in logged, "the route's requests are in the log (so the scan reads something)"
    assert "forbidden_origin: the header file is read for Scout's own page only" in logged
    for marker in MARKERS:
        assert marker not in logged, f"a log line holds {marker!r}"
