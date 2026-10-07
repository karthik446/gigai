"""0.1.11.3 item 13: the privacy rules of the header file that are about the CODE and about one server process.

``tests/api_e2e/test_pdf_header_file_journey.py`` holds the rules a real server shows from outside (no other route
returns a value; nothing on disk).  Here:

- BY STRUCTURE: only the form's prefill route and ``gigai scout resume pdf`` can read the file at all (no store,
  journal, record, brief, suggestion or prompt module imports the reader), and the prefill route is the one JSON
  answer written around the outbound check;
- NO MODEL PROMPT: in one process that has just read the file for the form and for a PDF, the requests an
  assessment and a tailoring send to the model hold no value of it;
- NO LOG: with every logger at DEBUG, the prefill (filled, invalid, refused) and the PDF log no value.

0.1.11.3 item 14: the form's Save button is the ONE writer of the file (``pdf_header_save``, called by one route
handler); its request's values reach no log and no model prompt either.

0.1.11.5 PH: the job page's resume preview is a THIRD reader, added on purpose: it shows the header the PDF will have,
as page pictures, to Scout's own page only (``_preview_file_header``: the same Origin rule, checked before the file is
opened).  Its request reaches no log and no model prompt, and its answer holds the values in the pictures alone.

Synthetic values and tmp homes only.
"""

from __future__ import annotations

import ast
import base64
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
#: The places that may read the user's header file, and the functions of each that do.
READERS = {
    "scout/find_jobs/api/pdf_header.py": {
        "_handle_post_pdf_header",  # the Generate PDF form's prefill: Scout's own page only
        "_preview_file_header",  # 0.1.11.5 PH (reviewed, deliberate): the job page's preview pictures, Scout's own page only
    },
    "scout/pdf_header_cli.py": {"header_for_pdf"},  # a PDF COMMAND's header: the values go into the one PDF at --out (0.1.11.4 C2: one reading, two commands)
}
#: 0.1.11.5 PH: the one handler that asks for the preview's reading (``POST /api/tailored-resumes/preview``).
PREVIEW_READER = "_preview_file_header"
PREVIEW_CALLS = {"scout/find_jobs/api/tailored_resumes.py": {"_handle_post_tailored_resume_preview"}}
#: 0.1.11.4 C2: the commands that may ask for that reading, and the function of each that does. Each writes ONE PDF to --out.
COMMAND_READER = "header_for_pdf"
COMMANDS = {
    "scout/scout_cli.py": "resume_pdf_command",  # `gigai scout resume pdf`
    "scout/cover_letter_cli.py": "cover_letter_pdf_command",  # `gigai scout cover-letter pdf`
}
#: 0.1.11.3 item 14: the one module that WRITES the file (the form's Save button), and the one function that calls it.
WRITER = "scout/pdf_header_save.py"
WRITER_CALLS = {"scout/find_jobs/api/pdf_header.py": "_handle_post_pdf_header_save"}
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
    assert sorted(importers) == sorted([*READERS, WRITER]), "a new reader of the user's header file needs a privacy review and a row in READERS"
    # The writer takes the file's rules from the reader and never reads the file itself.
    assert "read_header_file" not in (SRC / WRITER).read_text(encoding="utf-8")
    del importers[WRITER]
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
        assert calls == READERS[relative], f"{relative}: the file is read in {sorted(calls)}"
        assert holders <= READERS[relative], f"{relative}: {sorted(holders)} use the reader module"


def test_only_the_preview_route_asks_for_the_previews_reading_and_only_for_scouts_own_page() -> None:
    """0.1.11.5 PH: one handler calls ``_preview_file_header``; the caller is checked before the file is opened; the
    values go to the renderer and into no key of the answer, no log and no file."""
    callers = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        source = path.read_text(encoding="utf-8")
        if PREVIEW_READER not in source:
            continue
        calls = {
            function.name
            for function in ast.walk(ast.parse(source))
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(node, ast.Call) and ast.unparse(node.func).endswith(PREVIEW_READER) for node in ast.walk(function))
        }
        if calls:  # a mention in words (a docstring) is fine
            callers[relative] = calls
    assert callers == PREVIEW_CALLS, callers
    route = (SRC / "scout" / "find_jobs" / "api" / "pdf_header.py").read_text(encoding="utf-8")
    reading = route[route.index(f"def {PREVIEW_READER}"):route.index("def _handle_post_pdf_header_save")]
    assert reading.index('if not self.headers.get("Origin"):\n            return None') < reading.index("read_header_file("), "the caller is checked before the file is opened"
    for banned in ("_log", "self._write", "self._error", "open(", "write_text", "write_bytes"):
        assert banned not in reading, f"the preview's reading must not {banned}"
    handlers = (SRC / "scout" / "find_jobs" / "api" / "tailored_resumes.py").read_text(encoding="utf-8")
    handler = handlers[handlers.index("def _handle_post_tailored_resume_preview"):handlers.index("def _refuse_large_body")]
    code = handler.split('"""')[2]  # past the docstring
    # What is read goes into ``form`` and from there to the renderer alone: every use of it is "is it there" or the render's own argument.
    function = next(node for node in ast.walk(ast.parse(handlers)) if isinstance(node, ast.FunctionDef) and node.name == "_handle_post_tailored_resume_preview")
    uses = [node for node in ast.walk(function) if isinstance(node, ast.Name) and node.id == "form" and isinstance(node.ctx, ast.Load)]
    asked = {id(node.left) for node in ast.walk(function) if isinstance(node, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops)}
    rendered = {id(keyword.value) for node in ast.walk(function) if isinstance(node, ast.Call) and ast.unparse(node.func) == "stored_resume_pdf" for keyword in node.keywords if keyword.arg == "form"}
    assert len(rendered) == 1 and uses and all(id(node) in asked | rendered for node in uses), "the header's values go somewhere else than the render"
    answer = code[code.index("self._write_json(HTTPStatus.OK"):]
    assert "form" not in answer and '"header_shown": shown' in answer and '{"Cache-Control": "no-store"}' in answer
    assert "placeholder=form is None" in code, "with no header of the person's the pictures show the placeholder, never a blank or an error"


def test_only_the_two_pdf_commands_ask_for_the_header_and_neither_prints_it() -> None:
    """0.1.11.4 C2: ``header_for_pdf`` is called by ``resume pdf`` and ``cover-letter pdf`` only; the reading logs and prints nothing."""
    callers = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        source = path.read_text(encoding="utf-8")
        if relative == "scout/pdf_header_cli.py" or COMMAND_READER not in source:
            continue
        calls = {
            function.name
            for function in ast.walk(ast.parse(source))
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(node, ast.Call) and ast.unparse(node.func).endswith(COMMAND_READER) for node in ast.walk(function))
        }
        if calls:  # a mention in words (a docstring) is fine
            callers[relative] = calls
    assert callers == {relative: {function} for relative, function in COMMANDS.items()}, callers
    reading = (SRC / "scout" / "pdf_header_cli.py").read_text(encoding="utf-8")
    for banned in ("import logging", "getLogger", "print(", "click.echo", "_logger", "write_text", "write_bytes"):
        assert banned not in reading, f"pdf_header_cli must not {banned}"
    # The cover-letter brief and the letter's renderer are not readers: neither imports the file's reader or the commands' reading.
    for module in ("cover_letter_brief.py", "cover_letter.py"):
        tree = ast.parse((SRC / "scout" / module).read_text(encoding="utf-8"))
        imported = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert not [line for line in imported if READER in line or "pdf_header_cli" in line], module


def test_only_the_save_button_route_can_write_the_file() -> None:
    """0.1.11.3 item 14: one module writes the header file, one route handler calls it, and the handler logs no value."""
    name = Path(WRITER).stem
    callers = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        source = path.read_text(encoding="utf-8")
        if relative == WRITER or name not in source:
            continue
        tree = ast.parse(source)
        imports = any(isinstance(node, (ast.Import, ast.ImportFrom)) and name in ast.unparse(node) for node in ast.walk(tree))
        calls = {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(node, ast.Call) and ast.unparse(node.func).endswith("save_header_file") for node in ast.walk(function))
        }
        if imports or calls:  # a mention in words (a docstring, a comment) is fine
            callers[relative] = calls
    assert callers == {relative: {function} for relative, function in WRITER_CALLS.items()}, callers
    writer = (SRC / WRITER).read_text(encoding="utf-8")
    for banned in ("import logging", "getLogger", "print(", "_logger"):
        assert banned not in writer, f"pdf_header_save must not {banned}"
    route = (SRC / "scout" / "find_jobs" / "api" / "pdf_header.py").read_text(encoding="utf-8")
    handler = route[route.index("def _handle_post_pdf_header_save"):]
    assert handler.index('self.headers.get("Origin")') < handler.index("self._read_json_body()") < handler.index("save_header_file("), "the caller is checked before the body is read"
    # What the handler says back is built from the save's own answer and fixed sentences: never from the body.
    assert "saved.to_json()" in handler and "{body" not in handler and "{key" not in handler.replace("{key: value for key, value in body.items()", "")


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

    # ... saved from the form (0.1.11.3 item 14: refused without the page's Origin, asked before replacing, then written) ...
    typed = {"name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
             "github": "zq-invalid-7731", "links": [{"label": "Talks", "url": "zq-invalid-7731.example.invalid/talks"}], "work_authorization": "VISA: H1B (ZQ-7731)"}
    assert client.post("/api/pdf-header/save", json=typed).status_code == 403
    assert client.post("/api/pdf-header/save", json=typed, headers=page).json()["state"] == "exists"
    assert client.post("/api/pdf-header/save", json={**typed, "replace": True}, headers=page).json()["state"] == "saved"
    assert client.post("/api/pdf-header/save", json={**typed, "phone": ["555-0142-ZQ"]}, headers=page).status_code == 422
    assert json.loads(header_file.read_text(encoding="utf-8")) == typed
    header_file.write_text(json.dumps(FILE), encoding="utf-8")

    # ... and then the model is called: an assessment and a tailoring.
    assessed = client.post("/api/assess", json={"job": _JOB})
    assert assessed.status_code == 200, assessed.text
    tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
    assert tailored.status_code == 200, tailored.text

    # 0.1.11.5 PH: the job page's preview reads the file too, for Scout's own page; any other caller gets the placeholder.
    key = {"profile_id": tailored.json()["resume"]["profile_id"], "job_identity": tailored.json()["job"]["job_identity"]}
    shown = client.post("/api/tailored-resumes/preview", json=key, headers=page)
    other = client.post("/api/tailored-resumes/preview", json=key)
    assert shown.status_code == 200 and other.status_code == 200, (shown.text, other.text)
    assert (shown.json()["header_shown"], other.json()["header_shown"]) == ("file", "placeholder")
    assert shown.headers["cache-control"] == "no-store" and other.headers["cache-control"] == "no-store"
    assert base64.b64decode(shown.json()["images"][0]) != base64.b64decode(other.json()["images"][0]), "the two callers got the same header"
    for answer in (shown, other):
        said = json.dumps({name: value for name, value in answer.json().items() if name != "images"}) + "\n" + "\n".join(f"{name}: {value}" for name, value in answer.headers.items())
        for marker in MARKERS:
            assert marker not in said, f"the preview's answer holds {marker!r} outside its pictures"
    # ... and the model is called once more after it.
    assert client.post("/api/assess", json={"job": {**_JOB, "title": "Staff Engineer II"}}).status_code == 200

    prompts = [request for request in sent if b"Northwind" in request or b"Staff Engineer" in request]
    assert len(prompts) >= 2, f"the model requests were recorded ({len(sent)} requests, {len(prompts)} with the resume or the posting)"
    for request in sent:
        for marker in MARKERS:
            assert marker.encode() not in request, f"a model request holds {marker!r}"

    logged = log_file.read_text(encoding="utf-8")
    assert "/api/pdf-header" in logged, "the route's requests are in the log (so the scan reads something)"
    assert "forbidden_origin: the header file is read for Scout's own page only" in logged
    assert "/api/pdf-header/save" in logged and "forbidden_origin: the header file is written for Scout's own page only" in logged
    assert logged.count("/api/tailored-resumes/preview") >= 2, "the preview's requests are in the log"
    for marker in MARKERS:
        assert marker not in logged, f"a log line holds {marker!r}"
