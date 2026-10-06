"""0.1.11.3 item 14: "Save these details to <path>" -- real supervised server, synthetic values, a tmp home only.

``POST /api/pdf-header/save`` is the Generate PDF form's Save button: the one place GigAI writes the user's own
``header.json`` (here ``<home>/header.json``, the default of a home other than ``~/.gigai``).  The journey:

1. NOT for anyone but Scout's own page: no ``Origin`` (curl, a script, an agent), a foreign one, or a ``GET`` writes
   nothing and answers no value.
2. A click writes exactly the typed fields, mode 0600, and says where; the form's prefill then reads them back.
3. A file that is there is not replaced without a yes: ``exists`` (cancel: unchanged), then ``replace`` writes.
4. Details the file's rules refuse are a 422 that names a field and a rule, never a value; the file is unchanged.
5. A folder that cannot be written is one plain sentence, never an error page.
6. A file that still holds ``REPLACE`` placeholders: all of them -> the form is not filled and says so; some -> the
   real fields fill, the skipped ones are named, a missing name is flagged; no PDF of the command line prints one.
7. THE AGENT API NEVER EXPOSES IT: with the saved file in place, no ``GET`` of the API, no brief and no headerless
   PDF holds a value; the OpenAPI document names the route once, as not for agents; ``llms.txt`` does not.
8. NOWHERE ON DISK but the file itself: the store, the journal (every git object), the logs, the records, the resumes
   folder, the target, the workpad.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config
from tests.behaviors.scout_find_jobs.test_pdf_header_file import MARKERS
from tests.behaviors.scout_find_jobs.test_pdf_header_save import TEMPLATE, TYPED

_URL = "https://boards.greenhouse.io/acme/jobs/101"
RESUME = (
    "## Experience\n"
    "### Northwind Health\n"
    "- Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%.\n"
    "\n"
    "## Skills\n"
    "Python · Postgres\n"
)
_JOB = {"job_text": "Acme is hiring a Staff Engineer for scheduling and billing systems. Requirements: Python, Postgres.", "title": "Staff Engineer", "company": "Acme"}
OLD = {"name": "Riley Formerfile", "email": "riley.old@example.invalid"}
ROUTE = "/api/pdf-header/save"


def _silent(what: str, *texts: str) -> None:
    for text in texts:
        for marker in MARKERS:
            assert marker not in text, f"{what}: {marker!r} is in {text[:400]!r}"


def _answer(response: httpx.Response) -> str:
    headers = "\n".join(f"{name}: {value}" for name, value in response.headers.items())
    body = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages) if response.content.startswith(b"%PDF") else response.text
    return headers + "\n" + body


def _names(folder: Path) -> list[str]:
    return sorted(item.name for item in folder.iterdir() if "header" in item.name)


def test_the_save_button_writes_the_header_file_once_and_the_values_reach_nobody_else(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(RESUME, encoding="utf-8")
    runner = CliRunner()
    for command in (["scout", "resume", "add", str(source)], ["scout", "resume", "master", "init", "--from", str(source)]):
        done = runner.invoke(cli, [*command, "--home", str(home), "--target", str(target), "--json"])
        assert done.exit_code == 0, done.output
    write_offline_find_jobs_config(target, sources_live=True)
    header_file = home / "header.json"
    assert not header_file.exists()

    server = start_server(home, target, monkeypatch=monkeypatch)
    out = tmp_path / "cli.pdf"
    try:
        client = server.client
        page = {"Origin": server.base_url.rstrip("/")}  # what Scout's own page sends on every write

        # ---- 1. nobody but Scout's own page ------------------------------------------------------------------
        for name, refused in (
            ("no Origin (curl, a script, an agent)", client.post(ROUTE, json=TYPED)),
            ("another site's Origin", client.post(ROUTE, json=TYPED, headers={"Origin": "http://evil.example.invalid"})),
            ("another local port's Origin", client.post(ROUTE, json=TYPED, headers={"Origin": "http://127.0.0.1:1"})),
            ("no Origin, with replace", client.post(ROUTE, json={**TYPED, "replace": True})),
        ):
            assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden_origin", f"{name}: {refused.text}"
            _silent(name, _answer(refused))
        for method in ("get", "put", "delete"):
            assert getattr(client, method)(ROUTE, headers=page).status_code in (404, 405, 415), f"there is no {method.upper()}"
        assert client.post(ROUTE, content=json.dumps(TYPED).encode(), headers={**page, "Content-Type": "text/plain"}).status_code == 415
        assert not header_file.exists() and _names(home) == [], "a refused request writes nothing"

        # ---- 2. the click: exactly the typed fields, 0600, the path it wrote ---------------------------------
        saved = client.post(ROUTE, json=TYPED, headers=page)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body == {
            "schema_version": "scout-pdf-header-save:1", "state": "saved", "shown": body["shown"],
            "message": f"Saved your details to {body['shown']}. GigAI keeps no other copy.",
        }
        assert body["shown"].endswith("header.json") and saved.headers["x-gigai-labels"] == "user-private"
        _silent("the save's answer", _answer(saved))
        assert json.loads(header_file.read_text(encoding="utf-8")) == TYPED, "the file has exactly the fields typed"
        assert stat.S_IMODE(header_file.stat().st_mode) == 0o600 and _names(home) == ["header.json"], "0600, and no temporary file left"
        # The form's prefill reads it back: no warning (0600), every value.
        filled = client.post("/api/pdf-header", json={}, headers=page).json()
        assert filled["state"] == "filled" and filled["warning"] is None and filled["placeholders"] == [] and filled["notice"] is None and filled["name_note"] is None
        assert filled["shown"] == body["shown"], "the button names the path the form reads"
        assert filled["values"] == {
            "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
            "github": "zq-invalid-7731", "linkedin": "zq-invalid-7731", "website": "zq-invalid-7731.example.invalid", "link": "",
            "work_authorization": "VISA: H1B (ZQ-7731)", "links": [{"label": "Talks", "url": "zq-invalid-7731.example.invalid/talks"}],
        }
        assert filled["values"] == {**TYPED, "link": ""}, "save -> reload: the form holds what was typed"

        # ---- 3. never over an existing file without a yes ----------------------------------------------------
        header_file.write_text(json.dumps(OLD), encoding="utf-8")
        os.chmod(header_file, 0o644)
        before = header_file.read_bytes()
        asked = client.post(ROUTE, json=TYPED, headers=page)
        assert asked.status_code == 200 and asked.json()["state"] == "exists", asked.text
        assert asked.json()["message"] == f"There is already a file at {body['shown']}. It was not changed."
        explicit_no = client.post(ROUTE, json={**TYPED, "replace": False}, headers=page)
        assert explicit_no.json()["state"] == "exists"
        assert header_file.read_bytes() == before and stat.S_IMODE(header_file.stat().st_mode) == 0o644, "cancel: the file is as it was"
        _silent("the 'exists' answer", _answer(asked), _answer(explicit_no))
        replaced = client.post(ROUTE, json={**TYPED, "replace": True}, headers=page)
        assert replaced.status_code == 200 and replaced.json()["state"] == "saved", replaced.text
        assert json.loads(header_file.read_text(encoding="utf-8")) == TYPED and stat.S_IMODE(header_file.stat().st_mode) == 0o600
        assert _names(home) == ["header.json"]

        # ---- 3b. item 16: addresses pasted into the id fields are saved as the shorthand, and print as links ----
        pasted = {
            "name": "Zora Quillfeather", "email": "", "phone": "", "location": "Quillshire, ZZ",
            "github": "https://github.com/zq-invalid-7731", "linkedin": "https://www.linkedin.com/in/zq-invalid-7731/",
            "website": "https://www.zq-invalid-7731.example.invalid/", "links": [{"label": "Link", "url": "github.com/zq-invalid-7731"}],
            "work_authorization": "", "replace": True,
        }
        short = client.post(ROUTE, json=pasted, headers=page)
        assert short.status_code == 200 and short.json()["state"] == "saved", short.text
        _silent("the shorthand save's answer", _answer(short))
        assert json.loads(header_file.read_text(encoding="utf-8")) == {
            "name": "Zora Quillfeather", "location": "Quillshire, ZZ", "github": "zq-invalid-7731", "linkedin": "zq-invalid-7731",
            "website": "zq-invalid-7731.example.invalid", "work_authorization": "",
        }, "the ids alone; no empty field; the link that is the GitHub profile is not written twice"
        assert stat.S_IMODE(header_file.stat().st_mode) == 0o600 and _names(home) == ["header.json"]
        reloaded = client.post("/api/pdf-header", json={}, headers=page).json()["values"]
        assert (reloaded["github"], reloaded["linkedin"], reloaded["website"], reloaded["links"]) == ("zq-invalid-7731", "zq-invalid-7731", "zq-invalid-7731.example.invalid", [])
        line = "Quillshire, ZZ | github.com/zq-invalid-7731 | zq-invalid-7731.example.invalid | linkedin.com/in/zq-invalid-7731"
        from_form = client.post("/api/resume/pdf", json={"markdown": RESUME, "header": reloaded}, headers=page)
        assert from_form.status_code == 200 and from_form.content.startswith(b"%PDF"), from_form.text
        form_text = "\n".join(sheet.extract_text() for sheet in PdfReader(io.BytesIO(from_form.content)).pages)
        assert line in form_text and "https://" not in form_text, form_text[:300]
        by_command = runner.invoke(cli, ["scout", "resume", "pdf", "--in", str(source), "--home", str(home), "--target", str(target), "--out", str(out), "--header", str(header_file), "--json"])
        assert by_command.exit_code == 0, by_command.output
        command_text = "\n".join(sheet.extract_text() for sheet in PdfReader(io.BytesIO(out.read_bytes())).pages)
        assert line in command_text and "https://" not in command_text, "the command prints the same compact line from the same file"
        out.unlink()
        assert client.post(ROUTE, json={**TYPED, "replace": True}, headers=page).json()["state"] == "saved"

        # ---- 4. details the file's rules refuse: a field and a rule, never a value ---------------------------
        before = header_file.read_bytes()
        for name, details, code, says in (
            ("a list for a text", {**TYPED, "phone": ["555-0142-ZQ"]}, "invalid_value", "phone must be text in double quotes"),
            ("too long", {**TYPED, "location": "Quillshire" * 30}, "invalid_value", "location is longer than 200 characters"),
            ("seven links", {**TYPED, "links": [{"label": "L", "url": f"zq-invalid-7731.example.invalid/{n}"} for n in range(7)]}, "invalid_value", "links holds more than 6 links"),
            ("a form key that is not the file's", {**TYPED, "link": "zq-invalid-7731.example.invalid"}, "unknown_key", "not a field of the header file"),
            ("a GitHub id that is not one", {**TYPED, "github": "zq invalid 7731/x"}, "invalid_value", 'github must be your GitHub id alone, like "your-id" (your GitHub profile address also works)'),
            ("a LinkedIn id that is not one", {**TYPED, "linkedin": "in/zq invalid 7731"}, "invalid_value", 'linkedin must be your LinkedIn id alone, like "your-id" (your LinkedIn profile address also works)'),
            ("a website that is not an address", {**TYPED, "website": "zq-invalid-7731"}, "invalid_value", 'website must be a site address, like "example.com"'),
            ("a value where a key goes", {**TYPED, "zora.q@example.invalid": "x"}, "unknown_key", "not a field of the header file"),
            ("a path", {**TYPED, "path": "/tmp/elsewhere.json"}, "unknown_key", "not a field of the header file"),
            ("replace that is not yes or no", {**TYPED, "replace": "yes"}, "wrong_type", "replace must be true or false"),
            ("nothing typed", {"replace": True}, "invalid_value", "there is nothing to save"),
            ("a placeholder", {**TYPED, "name": "REPLACE: your name", "replace": True}, "invalid_value", "a value still starts with REPLACE"),
        ):
            refused = client.post(ROUTE, json={"replace": True, **details}, headers=page)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code and says in refused.json()["error"]["message"], f"{name}: {refused.text}"
            _silent(name, _answer(refused))
        not_an_object = client.post(ROUTE, json=["Zora Quillfeather"], headers=page)
        assert not_an_object.status_code == 422
        _silent("a body that is a list", _answer(not_an_object))
        assert header_file.read_bytes() == before and _names(home) == ["header.json"], "a refused save changes nothing"

        # ---- 5. a folder that cannot be written: one plain sentence ------------------------------------------
        header_file.unlink()
        os.chmod(home, 0o500)
        try:
            if not os.access(home, os.W_OK):
                locked = client.post(ROUTE, json=TYPED, headers=page)
                assert locked.status_code == 200 and locked.json()["state"] == "not_writable", locked.text
                assert locked.json()["message"].startswith("Your details were not saved: GigAI cannot write to ")
                assert locked.json()["message"].endswith("Check that the folder is there and that you may write to it.")
                _silent("the not-writable answer", _answer(locked))
                assert not header_file.exists()
        finally:
            os.chmod(home, 0o700)

        # ---- 6. a file that still holds REPLACE placeholders -------------------------------------------------
        header_file.write_text(json.dumps(TEMPLATE), encoding="utf-8")
        os.chmod(header_file, 0o600)
        template = client.post("/api/pdf-header", json={}, headers=page).json()
        assert template["state"] == "placeholder" and template["values"] is None
        assert template["message"] == f"{template['shown']} still has placeholder values: replace the REPLACE: fields (or save your details here)."
        assert template["placeholders"] == ["name", "email", "phone", "location", "links", "work_authorization"] and template["name_note"] == f"{template['shown']} has no name yet."
        assert "your full name" not in json.dumps(template) and "linkedin.com/in/you" not in json.dumps(template), "a placeholder's own text is not sent either"
        pdf_args = ["scout", "resume", "pdf", "--in", str(source), "--home", str(home), "--target", str(target), "--json", "--out", str(out)]
        refused_pdf = runner.invoke(cli, pdf_args)
        assert refused_pdf.exit_code == 1 and json.loads(refused_pdf.output)["error"]["code"] == "header_file_placeholders", refused_pdf.output
        assert "header.json still has placeholder values: replace the REPLACE: fields (or save your details here)" in refused_pdf.output and not out.exists()
        # Some real fields: they fill, the rest are named; no name yet is flagged, and the command line refuses.
        header_file.write_text(json.dumps({**TEMPLATE, "email": "zora.q@example.invalid", "location": ""}), encoding="utf-8")
        mixed = client.post("/api/pdf-header", json={}, headers=page).json()
        assert mixed["state"] == "filled" and mixed["values"]["email"] == "zora.q@example.invalid" and mixed["values"]["name"] == "" and mixed["values"]["links"] == []
        assert mixed["placeholders"] == ["name", "phone", "links", "work_authorization"] and mixed["notice"].endswith("Skipped: name, phone, links, work_authorization.")
        assert mixed["name_note"] == f"{mixed['shown']} has no name yet." and "REPLACE" not in json.dumps(mixed["values"])
        nameless = runner.invoke(cli, pdf_args)
        assert nameless.exit_code == 1 and "header.json has no name yet; a PDF header needs one" in nameless.output and not out.exists(), nameless.output
        # Saving from the form (the person said yes) replaces the template with their own details.
        assert client.post(ROUTE, json=TYPED, headers=page).json()["state"] == "exists"
        assert client.post(ROUTE, json={**TYPED, "replace": True}, headers=page).json()["state"] == "saved"
        assert json.loads(header_file.read_text(encoding="utf-8")) == TYPED

        # ---- 7. the agent API never exposes it ---------------------------------------------------------------
        assessed = client.post("/api/assess", json={"job": {"job_url": _URL}})
        assert assessed.status_code == 200, assessed.text
        tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
        assert tailored.status_code == 200, tailored.text
        key = {"profile_id": tailored.json()["resume"]["profile_id"], "job_identity": tailored.json()["job"]["job_identity"]}
        _silent("an assessment and a tailoring", _answer(assessed), _answer(tailored))

        document = client.get("/api/openapi.json").json()
        url = quote(_URL, safe="")
        params = {
            "/api/jobs": f"?url={url}", "/api/jobs/brief": f"?url={url}", "/api/jobs/suggestions": f"?url={url}",
            "/api/tailored-resumes": f"?profile_id={key['profile_id']}", "/api/pipeline/job": f"?url={url}",
        }
        reads = sorted(path for path, operations in document["paths"].items() if "get" in operations and "{" not in path)
        assert len(reads) > 30 and {"/api/jobs/brief", "/api/master", "/api/postings", "/api/config", "/api/resumes-folder"} <= set(reads)
        asked_paths = [*reads, *(path + query for path, query in params.items()), f"/api/jobs/brief?url={url}&part=posting", "/api", "/llms.txt", "/api/openapi.json"]
        answered = 0
        for path in asked_paths:
            response = client.get(path)
            assert response.status_code < 500, f"GET {path}: {response.status_code} {response.text[:200]}"
            answered += response.status_code == 200
            _silent(f"GET {path}", _answer(response))
        assert answered > 25, "the reads answered for real (a wall of 4xx would prove nothing)"
        # The document an agent reads names the route once, as not for it; the agent's start page does not send it there.
        operation = document["paths"][ROUTE]["post"]
        assert list(document["paths"][ROUTE]) == ["post"] and "NOT for agents: it answers only Scout's own browser page" in operation["description"]
        assert "NOT for agents" in document["paths"]["/api/pdf-header"]["post"]["description"]
        assert "/api/pdf-header" not in client.get("/llms.txt").text
        # An agent's PDFs (no form) stay headerless.
        for name, response in (
            ("POST /api/tailored-resumes/pdf", client.post("/api/tailored-resumes/pdf", json=key)),
            ("POST /api/resume/pdf", client.post("/api/resume/pdf", json={"markdown": RESUME})),
        ):
            assert response.status_code == 200 and response.content.startswith(b"%PDF"), name
            _silent(name, _answer(response))
        for part in ([], ["--posting"]):
            cli_brief = runner.invoke(cli, ["scout", "resume", "brief", "--job-url", _URL, *part, "--home", str(home), "--target", str(target), "--json"])
            assert cli_brief.exit_code == 0, cli_brief.output
            _silent("gigai scout resume brief", cli_brief.output)

        folder = Path(client.get("/api/resumes-folder").json()["path"])
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # ---- 8. nowhere on disk but the file itself -------------------------------------------------------------
    source.unlink()
    assert folder == home / "resumes"
    for path in (folder.iterdir() if folder.is_dir() else ()):
        if path.suffix == ".pdf":
            _silent(f"the resumes folder's {path.name}", "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages))
    assert any(home.rglob("*.log")) or any((home / "scout").rglob("*log*")), "the home holds the server's logs, so the scan below reads them"
    logged = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in home.rglob("*") if path.is_file() and "log" in path.name)
    assert ROUTE in logged, "the save requests are in the log (so the scan reads something)"
    holders = sorted(
        (str(path), marker)
        for root in (home, target, workpad)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path != header_file
        for marker in MARKERS
        if marker.encode() in path.read_bytes()
    )
    assert holders == [], holders
    objects = subprocess.run(["git", "-C", str(workpad), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout
    for marker in MARKERS:
        assert marker.encode() not in objects, f"a journal object holds {marker!r}"
    assert json.loads(header_file.read_text(encoding="utf-8")) == TYPED
    assert_clean_and_healthy(workpad, home)
