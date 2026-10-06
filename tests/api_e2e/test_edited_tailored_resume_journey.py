"""0110-10-05: ``PUT /api/tailored-resumes`` and ``GET`` / ``PUT /api/resumes-folder`` over HTTP against the real supervised server.

The home is a temporary one, so the resumes folder is ``<home>/resumes`` (only the default home
``~/.gigai`` uses ``~/Documents/GigAI/resumes``): nothing here can reach the real Documents folder.

1. A tailoring puts the job's markdown in the resumes folder; ``GET /api/resumes-folder`` says where
   the folder is and, for the job, which file.
2. ``PUT /api/tailored-resumes`` with an edit the resume cannot support is 422
   ``edited_resume_unsupported`` (line number and number, never the text); a contact detail is 422
   ``personal_info_refused``; nothing is stored either time.
3. A supported edit is stored as that job's tailored resume, marked edited by the agent (a loopback
   write that names no writer and is not from the Scout UI), the job is queued and the server's
   runner makes the Scout ATS score and label again from it, keeping the edited resume; the folder's
   file follows; the same body again changes nothing; a write from the Scout UI is the operator's.
4. Shape errors are typed 422s; a foreign Origin is 403 and changes nothing.
5. ``PUT /api/resumes-folder`` chooses another folder (created), refuses a relative path, and an
   empty path goes back to the default.
"""

from __future__ import annotations

from pathlib import Path
import time

import httpx
import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config
from tests.support.latency import latency_bound

_URL = "https://boards.greenhouse.io/acme/jobs/101"
_RESUME_LINE = "Software engineer with Python service experience."  # harness.add_resume's one line
_OWN_LINE = "Python service engineer."
_EMAIL = "zora.quillfeather@zq.example.invalid"


def _wait_for_pipeline(client: httpx.Client, *, deadline_seconds: float = 90.0) -> dict[str, object]:
    """Poll ``GET /api/pipeline`` until the job's background pipeline is over (the server's runner does the work)."""

    deadline = time.monotonic() + latency_bound(deadline_seconds)
    seen: dict[str, object] | None = None
    while time.monotonic() < deadline:
        seen = next((item for item in client.get("/api/pipeline").json()["jobs"] if item["job_identity"] == _URL), None)
        if seen is not None and seen["state"] in ("done", "failed"):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"the job's pipeline never finished: {seen}")


def _error(response: httpx.Response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    return response.json()["error"]["message"]


def test_edited_tailored_resume_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    monkeypatch.setenv("GIGAI_SCOUT_PIPELINE", "on")  # the pipeline is on: the server's runner re-checks the edited resume
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        folder = home / "resumes"

        # ---- 1. a tailoring lands in the folder ------------------------------------------------
        assert client.post("/api/assess", json={"job": {"job_url": _URL}}).status_code == 200
        tailored = client.post("/api/tailored-resumes", json={"job": {"job_url": _URL}})
        assert tailored.status_code == 200, tailored.text
        first = tailored.json()
        key = {"profile_id": first["resume"]["profile_id"], "job_identity": first["job"]["job_identity"]}
        assert "edited" not in first

        where = client.get("/api/resumes-folder")
        assert where.status_code == 200, where.text
        assert where.json() == {
            "schema_version": "scout-resumes-folder-response:1", "path": str(folder), "shown": where.json()["shown"], "source": "default",
            "default": where.json()["default"], "exists": True,
        }
        files = client.get("/api/resumes-folder", params=key).json()["files"]
        assert files["pdf"] is None and files["markdown"].startswith("acme-software-engineer-") and files["markdown"].endswith(".md")
        visible = folder / files["markdown"]
        text = visible.read_text(encoding="utf-8")
        assert _RESUME_LINE in text and "<!--" not in text
        _error(client.get("/api/resumes-folder", params={"profile_id": key["profile_id"]}), 422, "invalid_value")
        _error(client.get("/api/resumes-folder", params={"bogus": "1"}), 422, "unknown_key")

        def stored() -> dict:
            (item,) = client.get("/api/tailored-resumes", params=key).json()["items"]
            return item

        # ---- 2. refusals: nothing is stored -----------------------------------------------------
        url = "/api/tailored-resumes"
        unsupported = client.put(url, json={"job_url": _URL, "markdown": text + "- Cut latency by 37% with Rust.\n"})
        message = _error(unsupported, 422, "edited_resume_unsupported")
        assert 'the number "37"' in message and "line " in message and "Cut latency" not in message
        personal = client.put(url, json={"job_url": _URL, "markdown": text + f"- Reach me at {_EMAIL}.\n"})
        assert _EMAIL not in _error(personal, 422, "personal_info_refused")
        assert stored() == first

        # ---- 3. a supported edit is the job's tailored resume, marked edited by the agent ----------
        edited_markdown = text + f"- {_OWN_LINE}\n"
        put = client.put(url, json={"job_url": _URL, "markdown": edited_markdown, "source": "added one line, at the user's request"})
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["changed"] is True
        assert body["edited"]["written_by"] == "agent" and body["edited"]["source"] == "added one line, at the user's request"
        assert body["recheck"]["result"] == "enqueued" and body["recheck"]["error_code"] is None and body["recheck"]["runner"] is True
        assert body["producer"]["callable"] == "scout.tailor.attach" and body["usage"] is None
        assert body["stored_path"] == first["stored_path"] and body["created_at"] == first["created_at"]
        lines = [line for section in body["result"]["sections"] for line in section.get("lines", [])]
        own = [line for line in lines if line["kind"] == "custom"]
        assert [(line["text"], line["origin"], line["refs"]) for line in own] == [(_OWN_LINE, "user", [])] and "edited_from" not in own[0]
        assert any(line["kind"] != "custom" and line["refs"] for line in lines), "the unchanged lines keep their sources"
        shown = stored()
        assert {name: value for name, value in body.items() if name not in ("changed", "recheck")} == shown

        finished = _wait_for_pipeline(client)
        assert finished["state"] == "done", finished
        detail = client.get("/api/pipeline/job", params=key)
        assert detail.status_code == 200, detail.text
        assert stored() == shown, "the pipeline kept the edited resume"
        files = client.get("/api/resumes-folder", params=key).json()["files"]
        assert _OWN_LINE in (folder / files["markdown"]).read_text(encoding="utf-8")
        assert sorted(path.suffix for path in folder.iterdir()) == [".md"], "one markdown for the job; no PDF was asked for"

        again = client.put(url, json={"job_url": _URL, "markdown": edited_markdown})
        assert again.status_code == 200 and again.json()["changed"] is False and again.json()["recheck"]["result"] == "unchanged"
        assert stored() == shown

        # A write from the Scout UI (a page of this server sends its Origin) is the operator's.
        from_ui = client.put(url, json={"job_url": _URL, "markdown": text}, headers={"Origin": server.base_url})
        assert from_ui.status_code == 200, from_ui.text
        assert from_ui.json()["edited"]["written_by"] == "operator" and from_ui.json()["changed"] is True
        _wait_for_pipeline(client)

        # A headerless PDF over the API is answered to the caller; the folder gets PDFs from `gigai scout resume pdf`.
        pdf = client.post("/api/tailored-resumes/pdf", json=key)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert sorted(path.suffix for path in folder.iterdir()) == [".md"]

        # ---- 4. shape errors, and the write guards ------------------------------------------------
        kept = stored()
        _error(client.put(url, json={"job_url": _URL, "markdown": text, "bogus": 1}), 422, "unknown_key")
        _error(client.put(url, json={"job_url": _URL}), 422, "invalid_value")
        _error(client.put(url, json={"job_url": _URL, "markdown": text, "profile_id": "../x"}), 422, "invalid_value")
        _error(client.put(url, json={"job_url": _URL, "markdown": text, "actor": "robot"}), 422, "invalid_value")
        _error(client.put(url, json={"job_url": _URL, "markdown": "no sections here\n"}), 422, "resume_markdown_invalid")
        _error(client.put(url, json=[1]), 422, "wrong_type")
        foreign = httpx.put(f"{server.base_url}{url}", json={"job_url": _URL, "markdown": edited_markdown}, headers={"Origin": "http://evil.example.test"})
        assert foreign.status_code == 403 and foreign.json()["error"]["code"] == "forbidden_origin"
        assert stored() == kept

        # ---- 5. the folder is a setting ---------------------------------------------------------
        chosen = tmp_path / "my resumes"
        changed = client.put("/api/resumes-folder", json={"path": str(chosen)})
        assert changed.status_code == 200, changed.text
        assert changed.json()["path"] == str(chosen) and changed.json()["source"] == "setting" and chosen.is_dir()
        assert client.get("/api/resumes-folder").json()["path"] == str(chosen)
        _error(client.put("/api/resumes-folder", json={"path": "relative/folder"}), 422, "invalid_value")
        _error(client.put("/api/resumes-folder", json={"path": str(home / "scout" / "x")}), 422, "invalid_value")
        _error(client.put("/api/resumes-folder", json={"path": 7}), 422, "wrong_type")
        _error(client.put("/api/resumes-folder", json={}), 422, "invalid_value")
        _error(client.put("/api/resumes-folder", json={"path": str(chosen), "bogus": 1}), 422, "unknown_key")
        assert client.get("/api/resumes-folder").json()["path"] == str(chosen), "a refused value changes nothing"
        back = client.put("/api/resumes-folder", json={"path": ""})
        assert back.status_code == 200 and back.json()["source"] == "default" and back.json()["path"] == str(folder)

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
