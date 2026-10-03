"""0110-046 packet 3: every resume import removes and discards the contact lines, and says so.

``resume_pii.strip_contact_lines`` is the strip (the same lines ``model_resume`` withholds from a model,
plus the name's words in the body, spike finding e); ``resume_import`` stores only what is left, from
``gigai scout resume add``, ``POST /api/resumes`` (the wizard and a new profile's paste / upload) alike.
The removed values are in no file under the synthetic home or the workpad afterwards; the CLI, the API
and the UI get ``REMOVED_MESSAGE`` and counts by kind, never a value.  Synthetic markers only.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
from click.testing import CliRunner

from gigai.cli import cli
from gigai.private_records import read_record
from gigai.scout.find_jobs.api.server import serve
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend
from gigai.scout.resume_import import import_resume_bytes
from gigai.scout.resume_pii import REMOVED_MESSAGE, strip_contact_lines
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init
from tests.support.scout_profile_fixtures import build_gig_with_resume

RESUME = (
    "# Zora Quillfeather\n"
    "Staff Platform Engineer\n"
    "zora.q@example.invalid | (555) 014-2999 | 12 Quill Street, Nowhere, ZZ 00000\n"
    "linkedin.com/in/zq-invalid | https://zq.example.invalid\n"
    "\n"
    "## Summary\n"
    "Platform engineer with nine years building billing systems. Write to zora.q@example.invalid.\n"
    "\n"
    "## Experience\n"
    "### Northwind Health\n"
    "- Quillfeather led the scheduling rebuild on Python and Postgres.\n"
    "- Cut p95 latency by 40%.\n"
)
MARKERS = ("Zora", "Quillfeather", "zora.q@example.invalid", "014-2999", "Quill Street", "zq-invalid", "zq.example.invalid")


def _holders(*roots: Path) -> list[tuple[str, str]]:
    return sorted(
        (str(path), marker)
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and ".git" not in path.parts
        for marker in MARKERS
        if marker.encode() in path.read_bytes()
    )


def test_the_strip_removes_the_name_and_contact_lines_and_counts_them() -> None:
    stripped = strip_contact_lines(RESUME)
    for marker in MARKERS:
        assert marker not in stripped.text, marker
    assert stripped.text.startswith("Staff Platform Engineer\n\n## Summary\nPlatform engineer with nine years building billing systems. Write to")
    assert "- led the scheduling rebuild on Python and Postgres." in stripped.text, "the name's words go from the body too"
    assert "- Cut p95 latency by 40%." in stripped.text and "### Northwind Health" in stripped.text
    assert stripped.removed == {"name": 2, "email": 2, "phone": 1, "links": 1, "address": 1}
    assert stripped.name_words == frozenset({"zora", "quillfeather"})
    assert strip_contact_lines(stripped.text).text == stripped.text, "stripping again changes nothing"


def test_a_clean_resume_is_stored_byte_for_byte() -> None:
    clean = "## Summary\nPlatform engineer.\n\n## Skills\n- Python, Kubernetes, socket.io\n"
    stripped = strip_contact_lines(clean)
    assert stripped.text == clean and stripped.removed == {} and not stripped.changed


def test_an_import_stores_only_the_stripped_resume(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    imported = import_resume_bytes(
        home_root=fx.home_root, requested_target=fx.target, data=RESUME.encode(), file_name="Zora_Quillfeather_CV.md", gig_id=fx.created.gig_id,
    )
    assert imported.contact_removed == {"name": 2, "email": 2, "phone": 1, "links": 1, "address": 1}
    assert imported.label == "resume.md", "a file name holding the name is not stored"
    stored = read_record(
        home_root=fx.home_root, requested_target=fx.target, record_id=imported.record_id, content=True, gig_id=fx.created.gig_id,
    )["content"]
    assert stored == (strip_contact_lines(RESUME).text + "\n").encode()
    again = import_resume_bytes(
        home_root=fx.home_root, requested_target=fx.target, data=RESUME.encode(), file_name="Zora_Quillfeather_CV.md", gig_id=fx.created.gig_id,
    )
    assert not again.created and again.record_id == imported.record_id, "re-adding the same file creates nothing"
    assert _holders(fx.home_root, fx.target, fx.resolved.path) == [], "no file under the home, target or workpad keeps a removed value"


def test_the_cli_says_what_it_removed(tmp_path: Path) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume-in.md"
    source.write_text(RESUME, encoding="utf-8")
    base = ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target)]
    as_json = CliRunner().invoke(cli, [*base, "--json"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.output.strip().splitlines()[-1])
    assert payload["contact_removed"] == {"removed": {"name": 2, "email": 2, "phone": 1, "links": 1, "address": 1}, "message": REMOVED_MESSAGE}
    plain = CliRunner().invoke(cli, base)
    assert plain.exit_code == 0 and f"{REMOVED_MESSAGE} (removed: name 2, email 2, phone 1, links 1, address 1)" in plain.output
    for marker in MARKERS:
        assert marker not in as_json.output and marker not in plain.output
    assert _holders(home, target, resolve_workpad_path(home, target)) == []


def test_the_api_says_what_it_removed(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    httpd = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_address[1]}", timeout=60.0) as client:
            pasted = client.post("/api/resumes", json={"text": RESUME})
            clean = client.post("/api/resumes", json={"text": "## Summary\nPlatform engineer.\n"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert pasted.status_code == 201, pasted.text
    assert pasted.json()["contact_removed"] == {"removed": {"name": 2, "email": 2, "phone": 1, "links": 1, "address": 1}, "message": REMOVED_MESSAGE}
    assert clean.status_code == 201 and clean.json()["contact_removed"] is None
    for marker in MARKERS:
        assert marker not in pasted.text
