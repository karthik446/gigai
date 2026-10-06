"""0110-046 acceptance: GigAI stores no contact data -- against the real supervised server, synthetic only.

A synthetic home with a pre-0.1.10.7 ``resume-display.json`` (a saved name and contact items), then:

1. ``gigai scout resume add`` of a resume whose header holds the marker name, email, phone, address and
   links: the import strips and reports them;
2. the server: the one-time cleanup has run at start (the display file's name / contact go);
   ``GET /api/privacy/cleanup`` reports the counts, ``PUT`` marks it shown; a tailored resume is made, the Generate PDF form's
   marker values render ONE PDF (the header is correct; no response header carries them), a render with
   a bad form FAILS with 422 (the 500 path is forced in-process: test_generate_pdf_form_privacy.py), and
   the agent's headerless PDF carries the finish link;
3. ``gigai scout privacy`` prints the same report, and again (idempotent).

Then a recursive search of the synthetic home (settings, logs including the server log, caches), the
target, and the workpad (every working file AND every git object of the journal) finds no marker; the
only place a marker exists is the text of the PDF the form rendered (asserted in memory).
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.resume_display import SCHEMA_VERSION
from gigai.scout.resume_pii import REMOVED_MESSAGE

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

NAME, EMAIL, PHONE = "Zora Quillfeather", "zora.q@example.invalid", "555-0142-ZQ"
FORM = {"name": NAME, "email": EMAIL, "phone": PHONE, "location": "Quillshire, ZZ", "linkedin": "linkedin.com/in/zq-invalid", "link": "zq.example.invalid"}
RESUME = (
    f"# {NAME}\n"
    "Staff Platform Engineer\n"
    f"{EMAIL} | {PHONE} | (555) 014-2999 | 12 Quill Street, Quillshire, ZZ 00000\n"
    "linkedin.com/in/zq-invalid | https://zq.example.invalid\n"
    "\n"
    "## Experience\n"
    "### Northwind Health\n"
    "- Quillfeather rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%.\n"
    "- Ran the release calendar for 4 teams.\n"
)
MARKERS = ("Zora", "Quillfeather", EMAIL, PHONE, "014-2999", "Quill Street", "Quillshire", "zq-invalid", "zq.example.invalid")
_JOB = {"job_text": "Acme is hiring a Staff Engineer for scheduling and billing systems. Requirements: Python, Postgres.", "title": "Staff Engineer", "company": "Acme"}


def _found(data: bytes) -> list[str]:
    return [marker for marker in MARKERS if marker.encode() in data]


def _git_objects(workpad: Path) -> bytes:
    """Every object of the workpad's journal, decompressed (blobs, trees, commits)."""
    return subprocess.run(
        ["git", "-C", str(workpad), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True
    ).stdout


def test_no_contact_data_is_stored_anywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    legacy = home / "scout" / "resume-display.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION, "name": NAME, "contact": [{"kind": "email", "value": EMAIL}, {"kind": "phone", "value": PHONE}],
        "titles": {}, "spacing_scale": 1.0, "auto_fit": True, "updated_at": "2026-09-30T00:00:00Z",
    }))

    # 1. import: stripped and reported.
    source = tmp_path / "resume.md"
    source.write_text(RESUME, encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    payload = json.loads(added.output.strip().splitlines()[-1])
    assert payload["contact_removed"]["message"] == REMOVED_MESSAGE
    assert payload["contact_removed"]["removed"] == {"name": 2, "email": 1, "phone": 1, "links": 1, "address": 1}
    source.unlink()  # the user's own file, outside GigAI

    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        # 2a. the one-time cleanup ran at start (`gigai scout run` / the server process): the saved name /
        # contact are gone; the report is read and shown once.
        deadline = time.monotonic() + 30
        report = client.get("/api/privacy/cleanup")
        while report.json().get("status") == "not_run" and time.monotonic() < deadline:  # the server runs it off the startup path
            time.sleep(0.2)
            report = client.get("/api/privacy/cleanup")
        assert report.status_code == 200, report.text
        assert not _found(legacy.read_bytes()), "the display file holds the layout only"
        body = report.json()
        assert body["status"] == "already_done" and body["removed_any"] is True and body["shown"] is False
        assert body["display_settings"] == {"name": 1, "contact": 2}
        assert body["resumes"]["cleaned"] == 0, "the import already stored the resume clean"
        assert body["text"] == "Removed the saved name, contact (2) from the PDF settings (title, spacing and auto fit kept)."
        assert not _found(report.content)
        shown = client.put("/api/privacy/cleanup", json={"shown": True})
        assert shown.status_code == 200 and shown.json()["shown"] is True
        assert client.get("/api/privacy/cleanup").json() == {**body, "shown": True}
        assert client.put("/api/privacy/cleanup", json={"shown": False}).status_code == 422

        # 2b. a tailored resume, then ONE PDF with the form's values.
        tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
        assert tailored.status_code == 200, tailored.text
        assert not _found(tailored.content), "the model saw and the store holds no marker"
        key = {"profile_id": tailored.json()["resume"]["profile_id"], "job_identity": tailored.json()["job"]["job_identity"]}
        pdf = client.post("/api/tailored-resumes/pdf", json={**key, "header": FORM})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.text
        headers = "\n".join(f"{name}: {value}" for name, value in pdf.headers.items())
        assert not _found(headers.encode()), headers
        assert re.fullmatch(r'attachment; filename="acme-staff-engineer-\d{4}-\d{2}-\d{2}\.pdf"', pdf.headers["content-disposition"])
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
        assert text.startswith(f"ZORA QUILLFEATHER\nQuillshire, ZZ | linkedin.com/in/zq-invalid | zq.example.invalid | {EMAIL} | {PHONE}\n")
        assert text.count("QUILLFEATHER") == 1 and "Quillfeather" not in text, "the name prints once, in the header only"

        # 2c. a render that fails (a bad form): a typed 422 that echoes nothing.
        failed = client.post("/api/tailored-resumes/pdf", json={**key, "header": {**FORM, "phone": 5550142, "fax": PHONE}})
        assert failed.status_code == 422 and not _found(failed.content)
        failed = client.post("/api/resume/pdf", json={"markdown": "## Hobbies\n- " + NAME + "\n", "header": FORM})
        assert failed.status_code == 422 and not _found(failed.content)

        # 2d. the agent's PDF: no header, and the finish link.
        agent = client.post("/api/tailored-resumes/pdf", json=key)
        assert agent.status_code == 200 and agent.headers["x-gigai-finish-url"].startswith(f"http://127.0.0.1:{server.port}/#/pdf/")
        assert not _found(agent.content) and "x-gigai-finish-url" not in pdf.headers
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # 3. the CLI report, twice (idempotent).
    for _ in range(2):
        printed = CliRunner().invoke(cli, ["scout", "privacy", "--home", str(home), "--target", str(target), "--json"])
        assert printed.exit_code == 0, printed.output
        cli_report = json.loads(printed.output.strip().splitlines()[-1])
        assert cli_report["status"] == "already_done" and cli_report["display_settings"] == {"name": 1, "contact": 2}
    plain = CliRunner().invoke(cli, ["scout", "privacy", "--home", str(home), "--target", str(target)])
    assert plain.exit_code == 0 and plain.output.strip() == body["text"]

    # The search: no marker in any file under the home (logs included), the target or the workpad, nor in any
    # object of the workpad's git journal.
    assert (home / "logs").is_dir() and any((home / "logs").rglob("*.log")), "the server log this checks is under the home"
    holders = sorted(
        (str(path), _found(path.read_bytes()))
        for root in (home, target, workpad)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and _found(path.read_bytes())
    )
    assert holders == [], holders
    assert _found(_git_objects(workpad)) == [], "no journal object holds a marker"
    assert_clean_and_healthy(workpad, home)
