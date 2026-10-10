"""0.1.11.10 Part A: ``gigai scout learning list | show | open | import`` against a temporary home.

1. ``import`` checks and stores a synthetic course; ``--json`` prints the stored record; a course that fails a
   check exits 1 with the named error (as JSON with ``--json``) and stores nothing; ``--cost-json`` and ``--replace``.
2. ``list`` and ``show`` read the store: ``--json`` is what the API routes answer; an unknown id exits 1.
3. ``open`` prints the address on the RUNNING server and opens the browser unless ``--no-browser``; with no server
   it says how to start one and exits 1; a pathway with no course yet is ``course_not_ready``. No real browser and
   no real server here: the browser opener and the server status are replaced.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import learning_store, run_supervisor
from gigai.scout.find_jobs.api.learning import pathway_response, pathways_response

from tests.behaviors.scout_learning.test_learning_store import home_target, make_course  # noqa: F401 - home_target is the fixture


def _run(home: Path, target: Path, *args: str):
    return CliRunner().invoke(cli, ["scout", "learning", *args, "--home", str(home), "--target", str(target)], catch_exceptions=False)


def _json(result) -> dict[str, object]:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def _status(state: str, url: str | None = None) -> run_supervisor.ScoutStatus:
    return run_supervisor.ScoutStatus(state=state, project_id="project_x", url=url, pid=4242 if url else None)


def test_import_stores_a_course_and_says_what_it_did(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    source = make_course(tmp_path / "mlops-course", lessons=3)
    cost_file = tmp_path / "cost.json"
    cost_file.write_text(json.dumps({"cli_model_calls": 11, "worker_minutes": 135, "web_fetches": 430}), encoding="utf-8")

    done = _run(home, target, "import", str(source), "--role", "MLOps engineer", "--cost-json", str(cost_file), "--json")
    assert done.exit_code == 0, done.output
    body = _json(done)
    record = learning_store.list_pathways(home, target)[0]
    assert body == {"schema_version": "scout-learning-import:1", "pathway": record.to_json(), "replaced": False, "files": body["files"], "inline_script_pages": 0}
    assert record.status == "done" and record.role_text == "MLOps engineer" and record.course is not None and record.course.lessons == 3
    assert record.cost == {"cli_model_calls": 11, "worker_minutes": 135, "web_fetches": 430, "web_searches": None, "tokens": None, "note": None}
    assert str(tmp_path) not in done.output, "no path of this computer is printed back in the record"

    # Plain text: one line of what was imported and how to open it; the same role again is a NEW pathway.
    again = _run(home, target, "import", str(source), "--role", "MLOps engineer")
    assert again.exit_code == 0, again.output
    newest = next(item for item in learning_store.list_pathways(home, target) if item.id != record.id)
    assert f"Imported {newest.id}: MLOps engineer (3 lessons" in again.output and f"gigai scout learning open {newest.id}" in again.output
    assert len(learning_store.list_pathways(home, target)) == 2

    replaced = _run(home, target, "import", str(make_course(tmp_path / "v2", lessons=5)), "--role", "MLOps engineer", "--replace", record.id)
    assert replaced.exit_code == 0 and f"Replaced {record.id}: MLOps engineer (5 lessons" in replaced.output, replaced.output
    assert len(learning_store.list_pathways(home, target)) == 2 and learning_store.get_pathway(home, target, record.id).revision == 2

    inline = make_course(tmp_path / "inline", extra={"extra.html": "<!doctype html><title>x</title><script>window.x = 1;</script>"})
    warned = _run(home, target, "import", str(inline), "--role", "MLOps engineer")
    assert warned.exit_code == 0 and "1 page holds an inline <script> block" in warned.output, warned.output


def test_a_refused_import_exits_1_with_the_named_error_and_stores_nothing(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    source = make_course(tmp_path / "course")
    (source / "assets" / "tool.exe").write_text("x", encoding="utf-8")
    refused = _run(home, target, "import", str(source), "--role", "MLOps engineer", "--json")
    assert refused.exit_code == 1
    error = _json(refused)
    assert error["status"] == "error" and error["error"]["code"] == "course_file_kind_refused" and "assets/tool.exe" in error["error"]["message"]
    plain = _run(home, target, "import", str(source), "--role", "MLOps engineer")
    assert plain.exit_code == 1 and "assets/tool.exe" in plain.output
    (source / "assets" / "tool.exe").unlink()

    cases = (
        (("--role", "write to jane.roe@example.com"), "personal_info_refused"),
        (("--role", "MLOps engineer", "--replace", "lp-00000000"), "pathway_not_found"),
        (("--role", "MLOps engineer", "--cost-json", str(tmp_path / "missing.json")), "invalid_value"),
    )
    for extra, code in cases:
        result = _run(home, target, "import", str(source), *extra, "--json")
        assert result.exit_code == 1 and _json(result)["error"]["code"] == code, result.output
    bad_cost = tmp_path / "bad.json"
    bad_cost.write_text('{"wat": 9}', encoding="utf-8")
    result = _run(home, target, "import", str(source), "--role", "MLOps engineer", "--cost-json", str(bad_cost), "--json")
    assert result.exit_code == 1 and _json(result)["error"]["code"] == "invalid_value"
    missing_role = CliRunner().invoke(cli, ["scout", "learning", "import", str(source), "--home", str(home), "--target", str(target)])
    assert missing_role.exit_code == 2 and "--role" in missing_role.output
    assert learning_store.list_pathways(home, target) == []


def test_list_and_show_read_the_store(home_target: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811
    home, target = home_target
    empty = _run(home, target, "list")
    assert empty.exit_code == 0 and "No learning pathways yet" in empty.output and "gigai scout learning import" in empty.output
    assert _json(_run(home, target, "list", "--json")) == {"schema_version": "scout-learning-pathways-response:1", "pathways": [], "total": 0}

    done = learning_store.import_course(home, target, make_course(tmp_path / "mlops-course", lessons=3), "MLOps engineer", {"web_fetches": 430}).pathway
    queued = learning_store.create_request(home, target, "Forward deployed engineer")

    failing = learning_store.create_request(home, target, "Product manager")
    with learning_store.write_lock(home, target):
        failed = learning_store.write_pathway(
            home, target, dataclasses.replace(failing, status="failed", error="the model target was not available", revision=2),
        )

    listed = _run(home, target, "list")
    assert listed.exit_code == 0, listed.output
    lines = listed.output.splitlines()
    assert any(line.startswith(done.id) and "done" in line and "MLOps engineer" in line and "3 lessons" in line and "[imported]" in line for line in lines), listed.output
    assert any(line.startswith(queued.id) and "queued" in line and "no course yet" in line and "[requested]" in line for line in lines), listed.output
    assert any(
        line.startswith(failed.id) and "failed" in line and "[requested]" in line and "error: the model target was not available" in line
        for line in lines
    ), listed.output
    assert _json(_run(home, target, "list", "--json")) == pathways_response(home, target)

    shown = _run(home, target, "show", done.id)
    assert shown.exit_code == 0, shown.output
    for part in (done.id, "MLOps engineer", "Status: done", "3 lessons", "Source: imported (from the folder mlops-course)", "430 page fetches", f"gigai scout learning open {done.id}"):
        assert part in shown.output, (part, shown.output)
    assert "Status: queued" in _run(home, target, "show", queued.id).output and "learning open" not in _run(home, target, "show", queued.id).output
    assert _json(_run(home, target, "show", done.id, "--json")) == pathway_response(done)

    for unknown in ("lp-00000000", "nope"):
        missing = _run(home, target, "show", unknown, "--json")
        assert missing.exit_code == 1 and _json(missing)["error"]["code"] == "pathway_not_found"
    assert _run(home, target, "show", "lp-00000000").exit_code == 1


def test_open_prints_the_running_servers_address_and_says_how_to_start_one(
    home_target: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    home, target = home_target
    done = learning_store.import_course(home, target, make_course(tmp_path / "course"), "MLOps engineer").pathway
    queued = learning_store.create_request(home, target, "Forward deployed engineer")
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *args, **kwargs: opened.append(url) or True)

    # No server (the real status of a home nothing was started for): how to start one, exit 1, no browser.
    stopped = _run(home, target, "open", done.id, "--no-browser")
    assert stopped.exit_code == 1 and "The Scout server is not running" in stopped.output and "gigai scout run" in stopped.output
    stopped_json = _run(home, target, "open", done.id, "--json")
    assert stopped_json.exit_code == 1 and _json(stopped_json)["error"]["code"] == "server_not_running"
    assert _run(home, target, "open", done.id).exit_code == 1 and opened == []

    # A running server.
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: _status(run_supervisor.STATE_RUNNING, "http://127.0.0.1:8765"))
    address = f"http://127.0.0.1:8765/learning/{done.id}/index.html"
    quiet = _run(home, target, "open", done.id, "--no-browser")
    assert quiet.exit_code == 0 and quiet.output.strip() == address and opened == []
    loud = _run(home, target, "open", done.id)
    assert loud.exit_code == 0 and loud.output.strip() == address and opened == [address]
    as_json = _run(home, target, "open", done.id, "--json")
    assert _json(as_json) == {"schema_version": "scout-learning-open:1", "id": done.id, "url": address, "opened": False}
    assert opened == [address], "--json never opens a browser"

    # A pathway with no course yet, an unknown one, a server that does not answer.
    waiting = _run(home, target, "open", queued.id, "--json")
    assert waiting.exit_code == 1 and _json(waiting)["error"]["code"] == "course_not_ready"
    unknown = _run(home, target, "open", "lp-00000000", "--json")
    assert unknown.exit_code == 1 and _json(unknown)["error"]["code"] == "pathway_not_found"
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: _status(run_supervisor.STATE_UNREACHABLE, "http://127.0.0.1:8765"))
    unreachable = _run(home, target, "open", done.id, "--no-browser")
    assert unreachable.exit_code == 1 and "did not answer" in unreachable.output and opened == [address]
