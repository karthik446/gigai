"""0.1.11.3 item 6: the "Work authorization" line of the Generate PDF form -- real supervised server, synthetic only.

A synthetic home with a resume and a master (so ``master.md`` is in the resumes folder), one stored job resume, then:

1. ``POST /api/tailored-resumes/pdf`` with the form's ``header`` and ``work_authorization``: it prints in the header's
   ONE contact line, after the location (0.1.11.3 item 15); the response carries it nowhere but the PDF bytes;
2. the same form WITHOUT the line (left out, and empty): no line;
3. no ``header`` (an agent's PDF) and ``gigai scout resume pdf`` (the CLI's, saved to the resumes folder): no line;
4. ``POST /api/resume/pdf`` (markdown) takes the same field; a two-line or over-long value is a 422 that echoes nothing.

Then the line is NOWHERE on disk: not in ``master.md``, the job resume's markdown or JSON, a file of the resumes
folder, the home (settings, logs), the target, the workpad or any object of its git journal.  It is only what the
user chose to print on one PDF; sponsorship stays a label for jobs.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

MARK = "ZQ-7731"
LINE = f"H-1B, requires sponsorship ({MARK})"
FORM = {"name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ", "linkedin": "", "link": ""}
CONTACT = "Quillshire, ZZ | zora.q@example.invalid | 555-0142-ZQ"
WITH_LINE = f"Quillshire, ZZ | {LINE} | zora.q@example.invalid | 555-0142-ZQ"
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


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def test_the_work_authorization_line_prints_in_the_pdf_header_and_is_stored_nowhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(RESUME, encoding="utf-8")
    runner = CliRunner()
    added = runner.invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    made = runner.invoke(cli, ["scout", "resume", "master", "init", "--from", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert made.exit_code == 0, made.output
    master_file = home / "resumes" / "master.md"
    assert master_file.is_file(), "the master is a file in the (synthetic) resumes folder"

    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
        assert tailored.status_code == 200, tailored.text
        key = {"profile_id": tailored.json()["resume"]["profile_id"], "job_identity": tailored.json()["job"]["job_identity"]}

        # 1. with the line: in the ONE contact line, after the location; nothing of it in the response's headers.
        with_line = client.post("/api/tailored-resumes/pdf", json={**key, "header": {**FORM, "work_authorization": LINE}})
        assert with_line.status_code == 200 and with_line.content.startswith(b"%PDF"), with_line.text
        text = _text(with_line.content)
        assert text.split("\n")[:2] == ["ZORA QUILLFEATHER", WITH_LINE], text[:300]
        assert text.count(LINE) == 1, "the line prints once, in the header only"
        assert MARK not in "\n".join(f"{name}: {value}" for name, value in with_line.headers.items())

        # 2. without the line: left out of the form, and empty.
        for form in (FORM, {**FORM, "work_authorization": ""}, {**FORM, "work_authorization": "   "}):
            plain = client.post("/api/tailored-resumes/pdf", json={**key, "header": form})
            assert plain.status_code == 200, plain.text
            body = _text(plain.content)
            assert body.split("\n")[:2] == ["ZORA QUILLFEATHER", CONTACT] and "sponsorship" not in body.lower() and MARK not in body
            assert body.split("\n")[2:] == text.split("\n")[2:], "the rest of the PDF is the same"
            assert _pages(plain.content) == _pages(with_line.content), "the work authorization does not add a page"

        # 3. an agent's PDF (no form) has no header at all.
        agent = client.post("/api/tailored-resumes/pdf", json=key)
        assert agent.status_code == 200 and MARK not in _text(agent.content) and "sponsorship" not in _text(agent.content).lower()

        # 4. the markdown route takes the same field; a bad value is refused without being echoed.
        markdown = client.post("/api/resume/pdf", json={"markdown": RESUME, "header": {**FORM, "work_authorization": LINE}})
        assert markdown.status_code == 200, markdown.text
        assert _text(markdown.content).split("\n")[:2] == ["ZORA QUILLFEATHER", WITH_LINE]
        for bad in (LINE + "\nand a second line", LINE + "x" * 200):
            refused = client.post("/api/tailored-resumes/pdf", json={**key, "header": {**FORM, "work_authorization": bad}})
            assert refused.status_code == 422 and MARK not in refused.text and "header.work_authorization" in refused.text, refused.text

        # The stored job resume (what the API serves: markdown and JSON) never got the line.
        stored = client.get("/api/tailored-resumes", params=key)
        assert stored.status_code == 200 and MARK not in stored.text and "sponsorship" not in stored.text.lower()
        folder = Path(client.get("/api/resumes-folder").json()["path"])
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # `resume folder` copies nothing in any more (0.1.11.4 J1: the job's markdown is in the jobs folder); then the
    # CLI's PDF: headerless, to --out and into the resumes folder, no line.
    copied = runner.invoke(cli, ["scout", "resume", "folder", "--home", str(home), "--json"])
    assert copied.exit_code == 0 and json.loads(copied.output)["copied"] == 0, copied.output
    out = tmp_path / "cli.pdf"
    cli_pdf = runner.invoke(cli, ["scout", "resume", "pdf", "--in", str(source), "--out", str(out), "--home", str(home), "--target", str(target), "--json"])
    assert cli_pdf.exit_code == 0, cli_pdf.output
    assert MARK not in cli_pdf.output and MARK not in _text(out.read_bytes()) and "sponsorship" not in _text(out.read_bytes()).lower()
    in_folder = runner.invoke(cli, ["scout", "resume", "pdf", "--in", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert in_folder.exit_code == 0, in_folder.output
    out.unlink()
    source.unlink()

    # Nowhere on disk: master.md, the job resume's files, the resumes folder, the home (logs too), the target, the workpad.
    assert folder == home / "resumes" and folder.is_dir()
    names = sorted(path.name for path in folder.iterdir())
    # 0.1.11.4 J1: the resumes folder holds master.md and the headerless PDFs; the job's markdown is resume.md in its
    # own folder of the jobs folder (<home>/jobs here), which never holds a PDF.
    assert "master.md" in names and not any(name.endswith(".md") and not name.startswith("master") for name in names) and any(name.endswith(".pdf") for name in names), names
    job_files = sorted(path.relative_to(home / "jobs").as_posix() for path in (home / "jobs").rglob("*") if path.is_file())
    assert [name.rsplit("/", 1)[-1] for name in job_files] == [".gigai-job.json", "resume.md"], job_files
    for path in (home / "jobs").rglob("*"):
        assert not path.is_file() or (MARK.encode() not in path.read_bytes() and b"sponsorship" not in path.read_bytes().lower()), path.name
    assert MARK not in master_file.read_text(encoding="utf-8") and "sponsorship" not in master_file.read_text(encoding="utf-8").lower()
    for path in folder.iterdir():
        data = path.read_bytes()
        assert MARK.encode() not in data, path.name
        if path.suffix == ".pdf":
            assert MARK not in _text(data) and "sponsorship" not in _text(data).lower(), path.name
    holders = sorted(
        str(path)
        for root in (home, target, workpad)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and MARK.encode() in path.read_bytes()
    )
    assert holders == [], holders
    objects = subprocess.run(["git", "-C", str(workpad), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout
    assert MARK.encode() not in objects, "no journal object holds the line"
    assert_clean_and_healthy(workpad, home)
