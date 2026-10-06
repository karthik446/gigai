"""0110-10-05 C: ``PUT /api/tailored-resumes/length`` over HTTP against the real supervised server.

A synthetic resume that prints on 3 pages is tailored by a model that only
copies it (in this process: the server's fixture model answers with two
lines, and a cut needs three pages), then the server is started on that
home.  The stored resume left out its two oldest roles and the old roles'
later bullets; then: (a) ``use: restore`` answers 200 with every role and
bullet back, ``status: restored``, ``updated_at`` unchanged, and GET, the
stored ``.md`` and the PDF's page count all follow; (b) the same PUT again
changes nothing; (c) ``use: cut`` leaves exactly the same things out again;
(d) a stale ``updated_at`` is 409 ``tailored_resume_changed`` and changes
nothing; (e) a bad body or ``use`` is 422 ``invalid_value``, an unknown job
404; (f) Host / CSRF rejections change nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.tailored_resume import TailorRequest, run_tailored_resume

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config
from tests.support.tailor_cases import CASES, copy_everything, install_scripted_model, ollama_config

_CASE = CASES["over_long"]
#: 0.1.11.3 item 15: the length rule keeps room for the PDF's header, so the third-oldest role is cut too.
_CUT = [
    "Software Engineer — Harrow Analytics (2011–2013)", "Junior Developer — Bellweather Retail (2009–2011)",
    "Junior Developer — Dunmore Telecom (2007–2009)",
]


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _roles(payload: dict) -> list[int]:
    experience = next(section for section in payload["result"]["sections"] if section["heading"] == "experience")
    return [len(entry["bullets"]) for entry in experience["entries"]]


def test_length_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(_CASE["resume"], encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    write_offline_find_jobs_config(target)
    with monkeypatch.context() as patch:
        install_scripted_model(patch, [copy_everything(_CASE["resume"])])
        tailored = run_tailored_resume(
            TailorRequest(job=AssessJobInput(job_text=_CASE["posting"], title=_CASE["title"], company=_CASE["company"])),
            home_root=home, target=target, config=ollama_config(home),
        )
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        key = {"profile_id": tailored.resume.profile_id, "job_identity": tailored.job.job_identity}
        url = "/api/tailored-resumes/length"

        def shown() -> dict:
            items = client.get("/api/tailored-resumes", params=key).json()["items"]
            assert len(items) == 1
            return items[0]

        def pdf_pages() -> int:
            response = client.post("/api/tailored-resumes/pdf", json=key)
            assert response.status_code == 200, response.text
            return _pages(response.content)

        first = shown()
        stamp = first["updated_at"]
        length = first["result"]["length"]
        assert (length["status"], length["pages"], length["full_pages"]) == ("cut", 2, 3)
        assert [role["role"] for role in length["cut"]] == _CUT and len(length["trimmed"]) == 5
        assert _roles(first) == [10, 10, 10, 3, 3] and pdf_pages() == 2
        body = {**key, "updated_at": stamp, "use": "restore"}

        # (a) restore: every role and bullet is back, and GET, the .md and the PDF follow.
        restored = client.put(url, json=body)
        assert restored.status_code == 200, restored.text
        payload = restored.json()
        assert payload["result"]["length"]["status"] == "restored" and payload["updated_at"] == stamp
        assert [role["role"] for role in payload["result"]["length"]["cut"]] == _CUT  # what "cut" would leave out again
        assert _roles(payload) == [10, 10, 10, 6, 6, 6, 6, 6]
        assert shown() == payload
        markdown = Path(payload["markdown_path"]).read_text(encoding="utf-8")
        assert markdown == payload["markdown"] and "Dunmore Telecom" in markdown and "Bellweather Retail" in markdown
        assert pdf_pages() == 3

        # (b) the same action again changes nothing.
        again = client.put(url, json=body)
        assert again.status_code == 200 and again.json() == payload and shown() == payload

        # (c) cut again: exactly what the tailoring stored.
        cut = client.put(url, json={**body, "use": "cut"})
        assert cut.status_code == 200 and cut.json() == first and shown() == first
        assert "Dunmore Telecom" not in Path(first["markdown_path"]).read_text(encoding="utf-8") and pdf_pages() == 2

        # (d) a stale updated_at is 409 and changes nothing.
        stale = client.put(url, json={**body, "updated_at": "2020-01-01T00:00:00+00:00"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "tailored_resume_changed"
        assert shown() == first

        # (e) bad use / body / job.
        bad_use = client.put(url, json={**body, "use": "shrink"})
        assert bad_use.status_code == 422 and bad_use.json()["error"]["code"] == "invalid_value"
        for bad in ({}, {"profile_id": "x"}, {**body, "extra": 1}, {**body, "use": 1}, {**body, "line_id": "L1"}, {**body, "profile_id": "../x"}):
            response = client.put(url, json=bad)
            assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value", (bad, response.text)
        missing = client.put(url, json={**body, "job_identity": "https://example.test/none"})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
        assert shown() == first

        # (f) CSRF / Host: refused, and nothing changed.
        full = f"{server.base_url}{url}"
        wrong_origin = httpx.put(full, json=body, headers={"Origin": "http://evil.example.test"})
        assert wrong_origin.status_code == 403 and wrong_origin.json()["error"]["code"] == "forbidden_origin"
        wrong_type = httpx.put(full, content=b"{}", headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        wrong_host = httpx.put(full, json=body, headers={"Host": "evil.example.test"})
        assert wrong_host.status_code == 403 and wrong_host.json()["error"]["code"] == "forbidden_origin"
        assert shown() == first

        # The route is in the spec an agent reads, with its labels.
        operation = client.get("/api/openapi.json").json()["paths"][url]["put"]
        assert operation["x-gigai-labels"] and "restore" in str(operation)

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
