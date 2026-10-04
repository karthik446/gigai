"""0110-10-04 B: who wrote an answer, and where it came from (real supervised server; synthetic data, no model call).

The Helm answer of the report was written by an agent through the CLI, from
evidence in another of the user's repos, and stored as ``written_by:
operator``. The end outcome:

1. A write through the loopback API that is not the Scout UI's (no browser
   ``Origin``, no ``actor``) is recorded as the AGENT's. The UI's own write
   (its ``Origin``, or ``X-GigAI-Actor: operator``) is the operator's. An
   explicit ``actor`` or header always wins. The same default holds for an
   edit and for a story.
2. ``source`` is free text an agent records with the answer ("from the user's
   repo, at the user's request"); every read returns it; a new answer text
   without a source lets it go; contact-shaped text is refused.
3. The CLI says the same with ``--as agent`` (``--actor`` still works) and
   ``--source``, and the skill file tells agents to pass them.
4. The documented examples carry the field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.agent_skill import source_text
from gigai.scout.find_jobs.api import openapi
from gigai.scout.scout_cli import scout_group

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_SOURCE = "from the user's repo infra-charts, at the user's request"


def test_answer_provenance_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        ui = {"Origin": server.base_url.rstrip("/")}

        # 1. No actor, no browser Origin: an agent on the loopback API.
        saved = client.post("/api/answers", json={"question_id": "tooling:helm", "answer": "Yes, Helm charts for six services.", "source": _SOURCE})
        assert saved.status_code == 201, saved.text
        helm = saved.json()["answer"]
        assert (helm["written_by"], helm["source"], helm["history"][-1]["by"]) == ("agent", _SOURCE, "agent")

        # The Scout UI's own write is the operator's: its Origin, and the header it sends.
        from_ui = client.post("/api/answers", json={"question_id": "cloud:gcp", "answer": "Yes, two years."}, headers=ui)
        assert from_ui.status_code == 201, from_ui.text
        assert (from_ui.json()["answer"]["written_by"], from_ui.json()["answer"]["source"]) == ("operator", None)
        by_header = client.post("/api/answers", json={"question_id": "cloud:aws", "answer": "Yes, four years."}, headers={"X-GigAI-Actor": "operator"})
        assert by_header.json()["answer"]["written_by"] == "operator"
        # Said out loud wins over where the request came from.
        said = client.post("/api/answers", json={"question_id": "tooling:docker", "answer": "Yes, daily.", "actor": "agent"}, headers=ui)
        assert said.json()["answer"]["written_by"] == "agent"

        # The same default for an edit and for a story.
        edited = client.put("/api/answers/cloud:gcp", json={"revision": 1, "tag": "cloud"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["answer"]["written_by"] == "agent"
        story = client.post("/api/stories", json={"title": "Cut CI time", "raw": "I moved the runners to Kubernetes."})
        assert story.status_code == 201, story.text
        assert story.json()["story"]["written_by"] == "agent"

        # 2. Every read returns the source; the tag edit above left another answer's alone.
        listed = {item["question_id"]: item for item in client.get("/api/answers").json()["answers"]}
        assert (listed["tooling:helm"]["written_by"], listed["tooling:helm"]["source"]) == ("agent", _SOURCE)
        assert client.get("/api/answers/tooling:helm").json()["answer"]["source"] == _SOURCE
        # A source alone can be set on an existing answer; a new text without one lets it go.
        sourced = client.put("/api/answers/cloud:aws", json={"revision": 1, "source": "from the user's notes"}, headers=ui)
        assert sourced.status_code == 200, sourced.text
        assert (sourced.json()["answer"]["source"], sourced.json()["answer"]["written_by"]) == ("from the user's notes", "operator")
        retold = client.put("/api/answers/tooling:helm", json={"revision": 1, "answer": "Yes, Helm and Kustomize."}, headers=ui)
        assert (retold.json()["answer"]["source"], retold.json()["answer"]["written_by"]) == (None, "operator")
        refused = client.post("/api/answers", json={"question_id": "tooling:argo", "answer": "Yes.", "source": "ask rowan@acme-hiring.example"})
        assert (refused.status_code, refused.json()["error"]["code"]) == (422, "personal_info_refused")
        wrong = client.post("/api/answers", json={"question_id": "tooling:argo", "answer": "Yes.", "source": 3})
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (422, "invalid_value")

        # 3. The CLI: --as (and --actor, as before) and --source.
        runner = CliRunner()
        base = ["--home", str(home), "--target", str(target), "--json"]
        cli_saved = runner.invoke(
            scout_group, ["answers", "save", "tooling:kubernetes", "--answer-text", "Yes, three clusters.", "--as", "agent", "--source", _SOURCE, *base]
        )
        assert cli_saved.exit_code == 0, cli_saved.output
        answer = json.loads(cli_saved.output)["answer"]
        assert (answer["written_by"], answer["source"]) == ("agent", _SOURCE)
        older = runner.invoke(
            scout_group, ["answer", "tooling:terraform", "--answer-text", "Yes, modules.", "--actor", "agent", "--source", _SOURCE, *base]
        )
        assert older.exit_code == 0, older.output
        assert (json.loads(older.output)["answer"]["written_by"], json.loads(older.output)["answer"]["source"]) == ("agent", _SOURCE)
        plain = runner.invoke(scout_group, ["answers", "show", "tooling:kubernetes", "--home", str(home), "--target", str(target)])
        assert plain.exit_code == 0 and "agent" in plain.output and _SOURCE in plain.output, plain.output
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)


def test_the_skill_file_and_the_docs_say_how_to_record_who_writes() -> None:
    skill = source_text()
    assert "--as agent" in skill and "--source" in skill and "X-GigAI-Actor: agent" in skill
    post = next(spec for spec in openapi.ROUTES if (spec.method, spec.path) == ("POST", "/api/answers"))
    assert post.request_example is not None and "source" in post.request_example
    assert "source" in {param.name for param in post.params}
    assert "source" in post.example["answer"]  # type: ignore[operator]
