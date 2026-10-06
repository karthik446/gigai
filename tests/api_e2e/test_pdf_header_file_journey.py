"""0.1.11.3 item 13: the user's own header file -- real supervised server, synthetic values, a tmp home only.

The person keeps their name and contact details in ``header.json`` (here ``<home>/header.json``, the default of a
home other than ``~/.gigai``).  The journey:

1. ``POST /api/pdf-header`` answers Scout's own page (a request with this server's ``Origin``) with the file's values
   for the Generate PDF form; the PDF made from them prints every field; a value edited in the form wins over the file.
2. NOT for anyone else: no ``Origin`` (curl, a script, an agent), a foreign one, or a ``GET`` gets no value.
3. A missing, invalid or widely readable file is a 200 with one plain sentence, never an error page, never a value.
4. THE AGENT API NEVER EXPOSES IT: with the file in place, no ``GET`` of the API (every route of the OpenAPI document
   an agent reads, the index, ``llms.txt``, both parts of the job brief, the suggestions), no assessment, no stored
   resume and no headerless PDF holds a value of it.  The CLI's brief does not either.
5. ``gigai scout resume pdf --out`` makes the full PDF from the same file, the profile's sponsorship answer filling the
   work authorization line when the file has no such key.
6. NOWHERE ON DISK but the file itself: the store, the journal (every git object), the logs, the records, the
   suggestions, ``master.md``, the job resume's markdown and JSON, the resumes folder, the target, the workpad.
"""

from __future__ import annotations

import io
import json
import os
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
from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS

_URL = "https://boards.greenhouse.io/acme/jobs/101"
RESUME = (
    "## Experience\n"
    "### Northwind Health\n"
    "- Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%.\n"
    "- Ran the release calendar for 4 teams.\n"
    "\n"
    "## Skills\n"
    "Python · Postgres\n"
)
_JOB = {"job_text": "Acme is hiring a Staff Engineer for scheduling and billing systems. Requirements: Python, Postgres.", "title": "Staff Engineer", "company": "Acme"}
#: 0.1.11.3 items 15/16: ONE contact line: location | work authorization | links | email | phone.
def _contact(line: str) -> str:
    return f"Quillshire, ZZ | {line} | github.com/zq-invalid-7731 | zq-invalid-7731.example.invalid | linkedin.com/in/zq-invalid-7731 | zora.q@example.invalid | 555-0142-ZQ"


CONTACT = _contact("VISA: H1B (ZQ-7731)")


def _squeeze(*parts: str) -> str:
    return "".join("".join(parts).split())


def _pdf_text(pdf: bytes) -> str:
    """The PDF's text without white space (a long contact line wraps; a tracked heading extracts with spaces)."""
    return _squeeze(*(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages))


def _silent(what: str, *texts: str) -> None:
    for text in texts:
        for marker in MARKERS:
            assert marker not in text, f"{what}: {marker!r} is in {text[:400]!r}"


def _answer(response: httpx.Response) -> str:
    """Everything of a response a caller can read: the status line's headers and the body (a PDF as its text)."""
    headers = "\n".join(f"{name}: {value}" for name, value in response.headers.items())
    body = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages) if response.content.startswith(b"%PDF") else response.text
    return headers + "\n" + body


def _write_header(path: Path, content: object = FILE, mode: int = 0o600) -> None:
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.chmod(path, mode)


def test_the_header_file_fills_the_form_and_the_pdf_and_reaches_nobody_else(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(RESUME, encoding="utf-8")
    runner = CliRunner()
    for command in (["scout", "resume", "add", str(source)], ["scout", "resume", "master", "init", "--from", str(source)]):
        done = runner.invoke(cli, [*command, "--home", str(home), "--target", str(target), "--json"])
        assert done.exit_code == 0, done.output
    write_offline_find_jobs_config(target, sources_live=True)
    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["visa_sponsorship_required"] = True  # the profile's sponsorship answer: the line's default when the file has none
    config_path.write_text(json.dumps(config), encoding="utf-8")

    # The person's own file, in place BEFORE the server starts (so nothing at startup picks it up either).
    header_file = home / "header.json"
    _write_header(header_file)

    server = start_server(home, target, monkeypatch=monkeypatch)
    out = tmp_path / "cli.pdf"
    try:
        client = server.client
        page = {"Origin": server.base_url.rstrip("/")}  # what Scout's own page sends on every write

        # ---- 1. Scout's own page: the form's values, then the PDF from them --------------------------------
        filled = client.post("/api/pdf-header", json={}, headers=page)
        assert filled.status_code == 200, filled.text
        assert filled.headers["cache-control"] == "no-store" and filled.headers["x-gigai-labels"] == "user-private"
        body = filled.json()
        assert body["schema_version"] == "scout-pdf-header-prefill:1" and body["state"] == "filled" and body["warning"] is None
        assert body["shown"].endswith("header.json") and body["message"] == f"Filled from {body['shown']}" and body["has_work_authorization"] is True
        values = body["values"]
        assert values == {
            "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
            # 0.1.11.3 item 16: the file's GitHub and LinkedIn links fill the form's id fields, as the id alone.
            "github": "zq-invalid-7731", "linkedin": "zq-invalid-7731", "website": "", "link": "", "work_authorization": "VISA: H1B (ZQ-7731)",
            "links": [{"label": "Link", "url": "zq-invalid-7731.example.invalid"}],
        }, "the response is the person's own details for their own form: not tokenized by the outbound check"

        printed = client.post("/api/resume/pdf", json={"markdown": RESUME, "header": values}, headers=page)
        assert printed.status_code == 200 and printed.content.startswith(b"%PDF"), printed.text
        assert _pdf_text(printed.content).startswith(_squeeze("ZORA QUILLFEATHER", CONTACT, "EXPERIENCE")), _answer(printed)[-600:]

        # Precedence: what the person edits in the form wins over the file.
        edited = {**values, "name": "Riley Formedit", "work_authorization": "Green card holder", "github": "riley-formedit", "links": []}
        changed = _pdf_text(client.post("/api/resume/pdf", json={"markdown": RESUME, "header": edited}, headers=page).content)
        assert changed.startswith("RILEYFORMEDIT") and _squeeze("Green card holder") in changed and "github.com/riley-formedit" in changed
        assert "QUILLFEATHER" not in changed and "ZQ-7731" not in changed and "zq-invalid-7731.example.invalid" not in changed

        # ---- 2. nobody else ----------------------------------------------------------------------------------
        for name, refused in (
            ("no Origin (curl, a script, an agent)", client.post("/api/pdf-header", json={})),
            ("another site's Origin", client.post("/api/pdf-header", json={}, headers={"Origin": "http://evil.example.invalid"})),
            ("another local port's Origin", client.post("/api/pdf-header", json={}, headers={"Origin": "http://127.0.0.1:1"})),
        ):
            assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden_origin", f"{name}: {refused.text}"
            _silent(name, _answer(refused))
        no_get = client.get("/api/pdf-header", headers=page)
        assert no_get.status_code == 404, "there is no GET: the values never sit behind a URL"
        _silent("GET /api/pdf-header", _answer(no_get))
        not_json = client.post("/api/pdf-header", content=b"{}", headers={**page, "Content-Type": "text/plain"})
        assert not_json.status_code == 415
        bad_body = client.post("/api/pdf-header", json={"path": "/etc/passwd"}, headers=page)
        assert bad_body.status_code == 422, "the route reads the one default file: a caller names no path"
        _silent("refusals", _answer(not_json), _answer(bad_body))

        # ---- 3. a file that is missing, broken or widely readable: one plain sentence ------------------------
        _write_header(header_file, mode=0o644)
        loose = client.post("/api/pdf-header", json={}, headers=page).json()
        assert loose["state"] == "filled" and loose["warning"] == f"{loose['shown']} can be read by other users of this computer. To keep it to yourself: chmod 600 {loose['shown']}"
        _write_header(header_file, '{"name": "Zora Quillfeather", "email": ')
        broken = client.post("/api/pdf-header", json={}, headers=page)
        assert broken.status_code == 200 and broken.json()["state"] == "invalid" and broken.json()["values"] is None
        assert "is not valid JSON" in broken.json()["message"]
        _write_header(header_file, {**FILE, "phone": ["555-0142-ZQ"]})
        wrong = client.post("/api/pdf-header", json={}, headers=page)
        assert wrong.status_code == 200 and wrong.json()["state"] == "invalid" and "phone must be text in double quotes" in wrong.json()["message"]
        _silent("an invalid file's answer", _answer(broken), _answer(wrong))
        header_file.unlink()
        missing = client.post("/api/pdf-header", json={}, headers=page)
        assert missing.status_code == 200 and missing.json()["state"] == "missing" and missing.json()["values"] is None
        assert missing.json()["message"].startswith(f"There is no header file at {missing.json()['shown']}. ")
        _write_header(header_file)

        # ---- 4. the agent API never exposes it ---------------------------------------------------------------
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
        assert len(reads) > 30 and {"/api/jobs/brief", "/api/master", "/api/postings", "/api/answers", "/api/config", "/api/resumes-folder"} <= set(reads)
        asked = [*reads, *(path + query for path, query in params.items()), f"/api/jobs/brief?url={url}&part=posting", "/api", "/llms.txt", "/api/openapi.json"]
        answered = 0
        for path in asked:
            response = client.get(path)
            assert response.status_code < 500, f"GET {path}: {response.status_code} {response.text[:200]}"
            answered += response.status_code == 200
            _silent(f"GET {path}", _answer(response))
        assert answered > 25, "the reads answered for real (a wall of 4xx would prove nothing)"
        brief = client.get(f"/api/jobs/brief?url={url}")
        assert brief.status_code == 200 and brief.json()["part"] == "yours", brief.text
        # The document an agent reads names the route once, as not for it, and shows no value.
        operation = document["paths"]["/api/pdf-header"]["post"]
        assert list(document["paths"]["/api/pdf-header"]) == ["post"] and "NOT for agents: it answers only Scout's own browser page" in operation["description"]
        assert "/api/pdf-header" not in client.get("/llms.txt").text, "the agent's start page does not send it there"

        # An agent's PDFs (no form) stay headerless: the file is never applied behind the caller's back.
        for name, response in (
            ("POST /api/tailored-resumes/pdf", client.post("/api/tailored-resumes/pdf", json=key)),
            ("POST /api/resume/pdf", client.post("/api/resume/pdf", json={"markdown": RESUME})),
        ):
            assert response.status_code == 200 and response.content.startswith(b"%PDF") and "x-gigai-finish-url" in response.headers, name
            _silent(name, _answer(response))
        stored = client.post("/api/tailored-resumes/pdf", json={**key, "header": values}, headers=page)
        assert stored.status_code == 200 and _pdf_text(stored.content).startswith(_squeeze("ZORA QUILLFEATHER", CONTACT))

        folder = Path(client.get("/api/resumes-folder").json()["path"])
        workpad = resolve_workpad_path(home, target)

        # ---- 5. the CLI: the brief holds nothing of it; `resume pdf --out` makes the full PDF ----------------
        for part in ([], ["--posting"]):
            cli_brief = runner.invoke(cli, ["scout", "resume", "brief", "--job-url", _URL, *part, "--home", str(home), "--target", str(target), "--json"])
            assert cli_brief.exit_code == 0, cli_brief.output
            _silent("gigai scout resume brief", cli_brief.output)
        pdf_args = ["scout", "resume", "pdf", "--job-url", key["job_identity"], "--home", str(home), "--target", str(target), "--json"]
        full = runner.invoke(cli, [*pdf_args, "--out", str(out)])
        assert full.exit_code == 0, full.output
        assert json.loads(full.output)["header"] is True and json.loads(full.output)["finish_url"] is None
        assert _pdf_text(out.read_bytes()).startswith(_squeeze("ZORA QUILLFEATHER", CONTACT))
        _silent("gigai scout resume pdf --out", full.output.replace(str(home), ""))
        # No work_authorization key in the file: the line is the profile's sponsorship answer.
        _write_header(header_file, {key_: value for key_, value in FILE.items() if key_ != "work_authorization"})
        defaulted = runner.invoke(cli, [*pdf_args, "--out", str(out)])
        assert defaulted.exit_code == 0, defaulted.output
        assert _pdf_text(out.read_bytes()).startswith(_squeeze("ZORA QUILLFEATHER", _contact("Requires visa sponsorship")))
        _write_header(header_file)
        # Without --out the PDF goes to the resumes folder: headerless, and the command says why.
        in_folder = runner.invoke(cli, pdf_args)
        assert in_folder.exit_code == 0, in_folder.output
        assert json.loads(in_folder.output)["header"] is False and "pass --out FILE" in json.loads(in_folder.output)["header_note"]
        _silent("gigai scout resume pdf", in_folder.output.replace(str(home), ""))
    finally:
        stop_server(server)

    # ---- 6. nowhere on disk but the file itself -------------------------------------------------------------
    copied = runner.invoke(cli, ["scout", "resume", "folder", "--home", str(home), "--json"])
    assert copied.exit_code == 0, copied.output
    out.unlink()
    source.unlink()
    assert folder == home / "resumes" and folder.is_dir()
    names = sorted(path.name for path in folder.iterdir())
    # 0.1.11.4 J1: the resumes folder holds master.md and the headerless PDFs; the job's markdown is resume.md in its
    # own folder of the jobs folder (<home>/jobs here), which never holds a PDF.
    assert "master.md" in names and not any(name.endswith(".md") and not name.startswith("master") for name in names) and any(name.endswith(".pdf") for name in names), names
    job_files = sorted(path.relative_to(home / "jobs").as_posix() for path in (home / "jobs").rglob("*") if path.is_file())
    assert [name.rsplit("/", 1)[-1] for name in job_files] == [".gigai-job.json", "resume.md"], job_files
    for path in folder.iterdir():
        if path.suffix == ".pdf":
            _silent(f"the resumes folder's {path.name}", "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages))
    assert any(home.rglob("*.log")) or any((home / "scout").rglob("*log*")), "the home holds the server's logs, so the scan below reads them"
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
    assert json.loads(header_file.read_text(encoding="utf-8")) == FILE, "the person's file is as they left it"
    assert_clean_and_healthy(workpad, home)
