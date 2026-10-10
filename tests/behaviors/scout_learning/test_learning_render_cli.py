"""0.1.11.10 Part B packet G3: ``gigai scout learning render COURSE_JSON OUT_DIR [--paths DIR]``.

A pure file-to-file command (no ``--home``/``--target``: it never touches the learning store). ``--json`` prints
one JSON object; a bad course exits 1 with every problem listed, as JSON with ``--json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli

from tests.behaviors.scout_learning.test_learning_render import mini_course, mini_paths


def _run(*args: str):
    return CliRunner().invoke(cli, ["scout", "learning", "render", *args], catch_exceptions=False)


def _json(result) -> dict[str, object]:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def _write(tmp_path: Path, course: dict, paths: dict[str, dict] | None = None) -> tuple[Path, Path | None]:
    course_path = tmp_path / "course.json"
    course_path.write_text(json.dumps(course), encoding="utf-8")
    paths_dir = None
    if paths:
        paths_dir = tmp_path / "paths"
        paths_dir.mkdir()
        for cid, path in paths.items():
            (paths_dir / f"{cid}.json").write_text(json.dumps(path), encoding="utf-8")
    return course_path, paths_dir


def test_render_writes_the_site_and_says_what_it_did(tmp_path: Path) -> None:
    course_path, paths_dir = _write(tmp_path, mini_course(), mini_paths())
    out_dir = tmp_path / "out"

    result = _run(str(course_path), str(out_dir), "--paths", str(paths_dir))

    assert result.exit_code == 0, result.output
    assert "OK: wrote" in result.output and str(out_dir) in result.output
    assert (out_dir / "index.html").is_file()


def test_render_json_reports_pages_and_size(tmp_path: Path) -> None:
    course_path, _paths_dir = _write(tmp_path, mini_course())
    out_dir = tmp_path / "out"

    result = _run(str(course_path), str(out_dir), "--json")

    assert result.exit_code == 0, result.output
    body = _json(result)
    assert body["schema_version"] == "scout-learning-render:1"
    assert body["out_dir"] == str(out_dir)
    assert body["pages"] == 1 + 5
    assert body["size_bytes"] > 0
    assert body["stripped_invisible"] == 0


def test_a_bad_course_exits_1_with_every_problem_and_writes_nothing(tmp_path: Path) -> None:
    course = mini_course()
    course["modules"][0]["concepts"][0]["related"] = ["concept-does-not-exist"]
    course_path, _paths_dir = _write(tmp_path, course)
    out_dir = tmp_path / "out"

    as_json = _run(str(course_path), str(out_dir), "--json")
    assert as_json.exit_code == 1
    error = _json(as_json)
    assert error["status"] == "error" and error["error"]["code"] == "course_render_failed"
    assert any("unknown concept id" in problem for problem in error["error"]["problems"])
    assert not out_dir.exists()

    plain = _run(str(course_path), str(out_dir))
    assert plain.exit_code == 1 and "unknown concept id" in plain.output


def test_an_unreadable_course_json_exits_1() -> None:
    result = CliRunner().invoke(cli, ["scout", "learning", "render", "/no/such/course.json", "/tmp/out"])
    assert result.exit_code != 0
