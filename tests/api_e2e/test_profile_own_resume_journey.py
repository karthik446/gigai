"""0110-016: a profile created with its own (pasted) resume, over HTTP.

The requests the Settings form sends (``profileResumeModel.createProfileWith
Resume``): ``POST /api/resumes {"text"}`` then ``POST /api/profiles`` with that
resume_ref. The new profile's resume_ref differs from the selected profile's,
and, once selected, assess and tailor read THAT resume (fake model seam, no
live call).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

PASTED = "Forward deployed engineer: customer-facing Go and Kafka integrations for six years.\n"
POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    "Requirements: Python in production; GCP experience is a plus. Remote within the US."
)


def test_a_profile_created_with_a_pasted_resume_has_its_own_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        listing = client.get("/api/profiles").json()
        default = listing["profiles"][0]
        assert listing["selected_profile_id"] == default["profile_id"]

        stored = client.post("/api/resumes", json={"text": PASTED})
        assert stored.status_code in (200, 201), stored.text
        ref = stored.json()["resume_ref"]
        created = client.post(
            "/api/profiles",
            json={
                "label": "Forward Deployed Engineer",
                "titles": ["forward deployed engineer"],
                "resume_record_id": ref["record_id"],
                "resume_revision_id": ref["revision_id"],
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["profile"]

        # Its own resume, not the selected profile's.
        assert profile["resume_ref"]["record_id"] != default["resume_ref"]["record_id"]
        assert profile["resume_ref"]["content_sha256"] != default["resume_ref"]["content_sha256"]
        assert profile["resume_ref"]["content_sha256"] == ref["content_sha256"]
        assert client.get("/api/profiles").json()["selected_profile_id"] == default["profile_id"]

        # Select it: assess and tailor use that resume.
        assert client.post("/api/profiles/selection", json={"profile_id": profile["profile_id"]}).status_code == 200
        assessed = client.post("/api/assess", json={"job": {"job_text": POSTING}})
        assert assessed.status_code == 200, assessed.text
        resume = assessed.json()["resume"]
        assert resume["profile_id"] == profile["profile_id"]
        assert resume["content_sha256"] == profile["resume_ref"]["content_sha256"]

        tailored = client.post("/api/tailored-resumes", json={"job": {"job_text": POSTING}})
        assert tailored.status_code == 200, tailored.text
        body = tailored.json()
        assert body["resume"]["profile_id"] == profile["profile_id"]
        assert body["sources"]["resume_content_sha256"] == profile["resume_ref"]["content_sha256"]
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
