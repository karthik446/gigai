"""0110-006 packet C: ``PUT /api/tailored-resumes/lines`` over HTTP against the real supervised server.

Tailor once with the fixture model's lossy marker (the operator's weaker
Staff and DSAR rewrites, which the no-loss check replaces with the original
lines), then: (a) ``use: rewritten`` on a fallback line answers 200 with the
choice materialized (``origin: user``, the pair swapped, ``updated_at``
unchanged) and GET, the stored ``.md`` and the PDF text all follow; (b) the
same PUT again changes nothing; (c) ``use: original`` undoes it; (d) a stale
``updated_at`` is 409 ``tailored_resume_changed`` and changes nothing; (e) an
unknown line id or ``use`` is 422 ``invalid_value``, an unknown job 404; (f)
Host / CSRF rejections change nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_LOSSY_MARKER, TEST_MODEL_LOSSY_REWRITES

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_STAFF = "Managed a group of 4 analysts supporting scheduling and billing systems end to end, owning the release calendar, vendor contact, and staff training across 2 hospitals."
_DSAR = "Built the medication reconciliation (MRX) workflow handling admission and discharge lists end to end under HIPAA and OH state requirements."
_WEAKER_STAFF = TEST_MODEL_LOSSY_REWRITES[_STAFF.rstrip(".")]
_RESUME = f"## Experience\n**Clinical Applications Manager — Example Corp** (2020–present)\n- {_STAFF}\n- {_DSAR}\n"
_POSTING = "Acme is hiring a Staff Engineer to lead scheduling and billing systems and medication reconciliation. Requirements: Python."


def _text(pdf: bytes) -> str:
    return " ".join("\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split())


def _bullets(payload: dict) -> list[dict]:
    experience = next(section for section in payload["result"]["sections"] if section["heading"] == "experience")
    return experience["entries"][0]["bullets"]


def test_line_choice_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(_RESUME, encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        tailored = client.post(
            "/api/tailored-resumes",
            json={"job": {"job_text": _POSTING + " " + TEST_MODEL_LOSSY_MARKER, "title": "Staff Engineer", "company": "Acme"}},
        )
        assert tailored.status_code == 200, tailored.text
        payload = tailored.json()
        key = {"profile_id": payload["resume"]["profile_id"], "job_identity": payload["job"]["job_identity"]}
        stamp = payload["updated_at"]
        staff = _bullets(payload)[0]
        assert staff["text"] == f"- {_STAFF}" and staff["origin"] == "fallback" and staff["alternative"]["text"] == _WEAKER_STAFF
        body = {**key, "updated_at": stamp, "line_id": staff["id"], "use": "rewritten"}
        url = "/api/tailored-resumes/lines"

        def shown() -> dict:
            items = client.get("/api/tailored-resumes", params=key).json()["items"]
            assert len(items) == 1
            return items[0]

        assert _text(client.post("/api/tailored-resumes/pdf", json=key).content).count(_STAFF) == 1

        # (a) use rewritten: the choice is materialized and GET, the .md and the PDF follow.
        chosen = client.put(url, json=body)
        assert chosen.status_code == 200, chosen.text
        line = _bullets(chosen.json())[0]
        assert line["text"] == _WEAKER_STAFF and line["kind"] == "rewritten" and line["origin"] == "user" and line["id"] == staff["id"]
        assert line["alternative"]["text"] == f"- {_STAFF}" and line["alternative"]["lost"] == staff["alternative"]["lost"]
        assert chosen.json()["updated_at"] == stamp and _WEAKER_STAFF in chosen.json()["markdown"]
        assert shown() == chosen.json()
        assert _WEAKER_STAFF in Path(chosen.json()["markdown_path"]).read_text(encoding="utf-8")
        pdf_text = _text(client.post("/api/tailored-resumes/pdf", json=key).content)
        assert _WEAKER_STAFF in pdf_text and _STAFF not in pdf_text
        assert _bullets(shown())[1]["text"] == f"- {_DSAR}"  # the other line is untouched
        # 0.1.11.4 J1: the job's resume.md in the jobs folder follows the choice (and Restore, below).
        job_folder = client.get("/api/jobs-folder", params=key).json()["job"]
        assert job_folder["relative"] == "acme/staff-engineer" and job_folder["files"] == {"resume": "resume.md"}
        resume_md = Path(job_folder["path"]) / "resume.md"
        assert resume_md.parent == home / "jobs" / "acme" / "staff-engineer"
        assert _WEAKER_STAFF in resume_md.read_text(encoding="utf-8") and _STAFF not in resume_md.read_text(encoding="utf-8")

        # (b) the same choice again changes nothing.
        again = client.put(url, json=body)
        assert again.status_code == 200 and again.json() == chosen.json()
        assert shown() == chosen.json()

        # (c) undo.
        undone = client.put(url, json={**body, "use": "original"})
        assert undone.status_code == 200 and _bullets(undone.json())[0]["text"] == f"- {_STAFF}"
        assert _bullets(undone.json())[0]["origin"] == "user" and shown() == undone.json()
        assert _text(client.post("/api/tailored-resumes/pdf", json=key).content).count(_STAFF) == 1
        assert resume_md.read_text(encoding="utf-8").count(_STAFF) == 1 and _WEAKER_STAFF not in resume_md.read_text(encoding="utf-8"), "Restore rewrites resume.md"
        assert sorted(path.name for path in resume_md.parent.iterdir()) == [".gigai-job.json", "resume.md"]

        # (d) a stale updated_at is 409 and changes nothing.
        stale = client.put(url, json={**body, "updated_at": "2020-01-01T00:00:00+00:00"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "tailored_resume_changed"
        assert shown() == undone.json()

        # (e) unknown line id / use / job, and shape errors.
        unknown = client.put(url, json={**body, "line_id": "L999"})
        assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "invalid_value"
        bad_use = client.put(url, json={**body, "use": "both"})
        assert bad_use.status_code == 422 and bad_use.json()["error"]["code"] == "invalid_value"
        for bad in ({}, {"profile_id": "x"}, {**body, "extra": 1}, {**body, "line_id": 1}, {**body, "profile_id": "../x"}):
            response = client.put(url, json=bad)
            assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value", (bad, response.text)
        missing = client.put(url, json={**body, "job_identity": "https://example.test/none"})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
        assert shown() == undone.json()

        # (f) CSRF / Host: refused, and nothing changed.
        full = f"{server.base_url}{url}"
        wrong_origin = httpx.put(full, json=body, headers={"Origin": "http://evil.example.test"})
        assert wrong_origin.status_code == 403 and wrong_origin.json()["error"]["code"] == "forbidden_origin"
        wrong_type = httpx.put(full, content=b"{}", headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        wrong_host = httpx.put(full, json=body, headers={"Host": "evil.example.test"})
        assert wrong_host.status_code == 403 and wrong_host.json()["error"]["code"] == "forbidden_origin"
        assert shown() == undone.json()

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
