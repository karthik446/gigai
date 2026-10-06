"""uat-bug-017 (N22): Scout always lives in ``<home>/scout``; the folder never matters.

Every ``gigai scout ...`` command resolves its target as: ``--target`` if
given, else ``<home>/scout`` (created and bound when missing). The folder the
command runs from is never consulted, and a Scout project registered
elsewhere is never picked up implicitly: it is left untouched and named once
in a notice.

Hermetic: every test runs against a temp ``--home`` under ``tmp_path`` --
never the operator's real ``~/.gigai`` or their ``~/scout``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import scout_cli
from gigai.scout.target_resolution import (
    ScoutTargetError,
    earlier_project_notice_marker,
    home_scout_target,
    resolve_scout_target,
)

NOTICE_OPENING = "Scout now lives in"


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _setup(tmp_path: Path, *, home_name: str = "home") -> Path:
    """Non-interactive ``gigai setup`` only -- no ``gigai init``, no bound target."""

    home = tmp_path / home_name
    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return home


def _init(home: Path, target: Path, *, username: str = "home-default-test") -> None:
    target.mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--username", username, "--json"]
    )
    assert result.exit_code == 0, result.output


def _earlier_scout_project(home: Path, target: Path) -> Path:
    """A Scout project bound and installed outside ``<home>/scout`` (the operator's ``~/scout``)."""

    _init(home, target)
    result = CliRunner().invoke(
        cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output
    return target


def _tree(root: Path) -> dict[str, tuple[int, int] | None]:
    """Every path under ``root`` with its size and mtime (``None`` for a directory)."""

    snapshot: dict[str, tuple[int, int] | None] = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        snapshot[os.fspath(path.relative_to(root))] = (
            None if path.is_dir() else (stat.st_size, stat.st_mtime_ns)
        )
    return snapshot


# --- The cwd never matters ---------------------------------------------------


def test_a_registered_cwd_is_not_used_install_lands_in_home_scout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-before/pass-after for the cwd case: before uat-bug-017, running
    ``gigai scout install`` inside a registered project installed Scout there.
    """

    home = _setup(tmp_path)
    project = tmp_path / "some-project"
    _init(home, project)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["bound"] is True

    assert (home / "scout" / "find-jobs.json").is_file()
    assert not (project / "find-jobs.json").exists()


def test_the_resolver_returns_home_scout_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    registered = tmp_path / "registered"
    _init(home, registered)
    earlier = _earlier_scout_project(home, tmp_path / "earlier-scout")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    nested = earlier / "nested"
    nested.mkdir()

    resolved: list[Path] = []
    for cwd in (registered, earlier, nested, unrelated):
        monkeypatch.chdir(cwd)
        resolved.append(resolve_scout_target(home_root=home, requested_target=None))

    assert resolved == [home_scout_target(home)] * 4


# Minimal valid arguments for every Scout command that takes ``--target``.
_TARGET_COMMANDS: dict[str, tuple[str, ...]] = {
    "install": ("scout", "install"),
    "resume add": ("scout", "resume", "add", "{resume}"),
    "resume tailor": ("scout", "resume", "tailor", "--job-text", "{posting}", "--resume-text", "Python engineer."),
    "resume master show": ("scout", "resume", "master", "show"),
    "resume master init": ("scout", "resume", "master", "init", "--from", "{resume}"),
    "resume master history": ("scout", "resume", "master", "history"),
    "resume master add": ("scout", "resume", "master", "add", "--section", "other", "--text", "Certification: Example (2020)"),
    "resume master edit": ("scout", "resume", "master", "edit", "b-example", "--text", "Shipped the export.", "--revision", "1"),
    "resume master remove": ("scout", "resume", "master", "remove", "b-example", "--revision", "1"),
    "resume master sync": ("scout", "resume", "master", "sync"),
    "resume master selection show": ("scout", "resume", "master", "selection", "show"),
    "resume master selection status": ("scout", "resume", "master", "selection", "status"),
    "resume master selection refresh": ("scout", "resume", "master", "selection", "refresh"),
    "run": ("scout", "run", "--no-browser"),
    "stop": ("scout", "stop"),
    "status": ("scout", "status"),
    "privacy": ("scout", "privacy"),
    "discover": ("scout", "discover", "--status"),
    "prep": ("scout", "prep", "https://boards.greenhouse.io/example/jobs/1"),
    "assess": ("scout", "assess", "--job-text", "{posting}", "--resume-text", "Python engineer."),
    "answer": ("scout", "answer", "years-of-python", "--answer-text", "Eight years."),
    "watchlist add": ("scout", "watchlist", "add", "https://boards.greenhouse.io/example"),
    "sources update": ("scout", "sources", "update"),
    "resume pdf": ("scout", "resume", "pdf", "--tailored", "--job-url", "https://boards.greenhouse.io/example/jobs/1", "--out", "out.pdf"),
    "resume length": ("scout", "resume", "length", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    # 0.1.11 N5: the commands of the chat step (resume_job_cli).
    "resume brief": ("scout", "resume", "brief", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    "resume store": ("scout", "resume", "store", "--in", "{resume}", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    "resume pick": ("scout", "resume", "pick", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    # 0.1.11.4 C2: the cover-letter brief reads the stored assessment (the PDF command takes no --target).
    "cover-letter brief": ("scout", "cover-letter", "brief", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    "suggestions list": ("scout", "suggestions", "list", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    "suggestions add": ("scout", "suggestions", "add", "--job-url", "https://boards.greenhouse.io/example/jobs/1", "--kind", "reword", "--line", "b-example", "--why", "Say the technique."),
    "suggestions resolve": ("scout", "suggestions", "resolve", "sg-1", "--job-url", "https://boards.greenhouse.io/example/jobs/1", "--how", "master_line", "--ref", "b-example"),
    "suggestions dismiss": ("scout", "suggestions", "dismiss", "sg-1", "--job-url", "https://boards.greenhouse.io/example/jobs/1"),
    "answers list": ("scout", "answers", "list"),
    "answers show": ("scout", "answers", "show", "technical:python"),
    "answers save": ("scout", "answers", "save", "technical:python", "--answer-text", "Eight years."),
    "answers delete": ("scout", "answers", "delete", "technical:python", "--confirm"),
    "answers migrate": ("scout", "answers", "migrate"),
    "story list": ("scout", "story", "list"),
    "story show": ("scout", "story", "show", "story:led_team"),
    "story save": ("scout", "story", "save", "--title", "Led a team of four"),
    "story delete": ("scout", "story", "delete", "story:led_team", "--confirm"),
    "story prep": ("scout", "story", "prep"),
    "snapshot import": ("scout", "snapshot", "import"),
    "snapshot status": ("scout", "snapshot", "status"),
    "profile list": ("scout", "profile", "list"),
    "profile delete": ("scout", "profile", "delete", "profile_00000000-0000-4000-8000-000000000001"),
    "profile update": ("scout", "profile", "update", "profile_00000000-0000-4000-8000-000000000001", "--work-mode", "remote"),
    "metrics": ("scout", "metrics"),
    "new": ("scout", "new", "--peek"),
    "jobs list": ("scout", "jobs", "list"),
    "jobs assess": ("scout", "jobs", "assess", "--window", "new"),
    "jobs import-runs": ("scout", "jobs", "import-runs"),
    "pipeline rank": ("scout", "pipeline", "rank"),
    "pipeline run": ("scout", "pipeline", "run", "--once"),
    "pipeline status": ("scout", "pipeline", "status"),
    "pipeline process": ("scout", "pipeline", "process", "https://boards.greenhouse.io/example/jobs/1"),
    "pipeline cancel": ("scout", "pipeline", "cancel", "https://boards.greenhouse.io/example/jobs/1"),
    "pipeline retry": ("scout", "pipeline", "retry", "https://boards.greenhouse.io/example/jobs/1"),
    "pipeline approvals list": ("scout", "pipeline", "approvals", "list"),
    "pipeline approvals approve": ("scout", "pipeline", "approvals", "approve", "apv_0123456789abcdef0123456789abcdef"),
    "pipeline approvals deny": ("scout", "pipeline", "approvals", "deny", "apv_0123456789abcdef0123456789abcdef"),
}


def test_every_command_that_takes_target_is_covered() -> None:
    """A new ``--target`` command must be added to the table above."""

    def _walk(group, prefix: tuple[str, ...]):
        for name, command in group.commands.items():
            if hasattr(command, "commands"):
                yield from _walk(command, (*prefix, name))
            elif any(param.name == "target_value" for param in command.params):
                yield " ".join((*prefix, name))

    assert sorted(_walk(scout_cli.scout_group, ())) == sorted(_TARGET_COMMANDS)


@pytest.mark.parametrize("command", sorted(_TARGET_COMMANDS))
def test_every_command_resolves_the_same_target_from_two_cwds(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each command is stopped right after it resolves its target, so the
    target it would act on is observed without running the command's work.
    """

    home = _setup(tmp_path)
    earlier = _earlier_scout_project(home, tmp_path / "earlier-scout")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    resume = tmp_path / "resume.md"
    resume.write_text("Software engineer with Python service experience.\n", encoding="utf-8")
    posting = tmp_path / "posting.txt"
    posting.write_text("Senior Python engineer. Remote.\n", encoding="utf-8")

    seen: list[Path] = []

    def _resolve_then_stop(**kwargs: object) -> Path:
        seen.append(resolve_scout_target(**kwargs))  # type: ignore[arg-type]
        raise ScoutTargetError("stopped after resolving the target")

    monkeypatch.setattr(scout_cli, "resolve_scout_target", _resolve_then_stop)
    args = [part.format(resume=resume, posting=posting) for part in _TARGET_COMMANDS[command]]

    for cwd in (earlier, unrelated):
        monkeypatch.chdir(cwd)
        result = CliRunner().invoke(cli, [*args, "--home", str(home), "--json"])
        assert result.exit_code == 1, result.output
        assert "stopped after resolving the target" in result.output

    assert seen == [home_scout_target(home)] * 2


def test_run_status_and_stop_reach_the_same_instance_from_different_cwds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    earlier = _earlier_scout_project(home, tmp_path / "earlier-scout")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    runner = CliRunner()

    monkeypatch.chdir(earlier)
    install = runner.invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert install.exit_code == 0, install.output

    # Disable every live source so `run` never makes a network call.
    config_path = home / "scout" / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": False, "hiringcafe": False}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    port = _free_port()
    try:
        run = runner.invoke(
            cli, ["scout", "run", "--no-browser", "--port", str(port), "--home", str(home), "--json"]
        )
        assert run.exit_code == 0, run.output
        run_payload = json.loads(run.output)

        monkeypatch.chdir(unrelated)
        status = runner.invoke(cli, ["scout", "status", "--home", str(home), "--json"])
        assert status.exit_code == 0, status.output
        status_payload = json.loads(status.output)
        assert status_payload["state"] == "running"
        assert status_payload["pid"] == run_payload["pid"]
        assert status_payload["project_id"] == run_payload["project_id"]
    finally:
        monkeypatch.chdir(tmp_path)
        stop = runner.invoke(cli, ["scout", "stop", "--home", str(home), "--json"])
        assert stop.exit_code == 0, stop.output
    assert json.loads(stop.output)["stopped"] is True


# --- --target is the only override -------------------------------------------


def test_target_still_overrides_and_leaves_home_scout_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    explicit = tmp_path / "explicit"
    _init(home, explicit)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    result = CliRunner().invoke(
        cli, ["scout", "install", "--home", str(home), "--target", str(explicit), "--json"]
    )
    assert result.exit_code == 0, result.output

    assert (explicit / "find-jobs.json").is_file()
    assert not (home / "scout").exists()


# --- The one-time notice about an earlier project ----------------------------


def test_the_notice_is_shown_exactly_once_for_an_earlier_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    earlier = _earlier_scout_project(home, tmp_path / "earlier-scout")
    monkeypatch.chdir(earlier)
    runner = CliRunner()

    first = runner.invoke(cli, ["scout", "status", "--home", str(home)])
    assert first.exit_code == 0, first.output
    assert first.output.count(NOTICE_OPENING) == 1
    assert (
        f"Scout now lives in {home / 'scout'}; your earlier data in {earlier} is untouched; "
        f"use --target {earlier} to open it."
    ) in first.output
    assert earlier_project_notice_marker(home).is_file()

    second = runner.invoke(cli, ["scout", "status", "--home", str(home)])
    assert second.exit_code == 0, second.output
    assert NOTICE_OPENING not in second.output

    # A different command does not show it again either.
    third = runner.invoke(cli, ["scout", "stop", "--home", str(home)])
    assert third.exit_code == 0, third.output
    assert NOTICE_OPENING not in third.output


def test_the_notice_reads_as_the_operator_sees_it_under_their_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With GigAI's home at ``~/.gigai`` and the earlier project at ``~/scout``."""

    monkeypatch.setenv("HOME", str(tmp_path))
    home = _setup(tmp_path, home_name=".gigai")
    _earlier_scout_project(home, tmp_path / "scout")

    result = CliRunner().invoke(cli, ["scout", "status", "--home", str(home)])
    assert result.exit_code == 0, result.output
    assert (
        "Scout now lives in ~/.gigai/scout; your earlier data in ~/scout is untouched; "
        "use --target ~/scout to open it."
    ) in result.output


def test_no_notice_when_no_earlier_project_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _setup(tmp_path)
    # Registered, but Scout was never installed there: not an earlier Scout project.
    plain = tmp_path / "plain-project"
    _init(home, plain)
    monkeypatch.chdir(plain)
    runner = CliRunner()

    for args in (["scout", "install"], ["scout", "status"], ["scout", "stop"]):
        result = runner.invoke(cli, [*args, "--home", str(home)])
        assert result.exit_code == 0, result.output
        assert NOTICE_OPENING not in result.output

    assert not earlier_project_notice_marker(home).exists()


def test_no_notice_with_an_explicit_target(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    earlier = _earlier_scout_project(home, tmp_path / "earlier-scout")

    result = CliRunner().invoke(cli, ["scout", "status", "--home", str(home), "--target", str(earlier)])
    assert result.exit_code == 0, result.output
    assert NOTICE_OPENING not in result.output
    assert not earlier_project_notice_marker(home).exists()


def test_json_output_stays_parseable_and_does_not_use_up_the_notice(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    _earlier_scout_project(home, tmp_path / "earlier-scout")
    runner = CliRunner()

    as_json = runner.invoke(cli, ["scout", "status", "--home", str(home), "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output)["state"] == "stopped"
    assert NOTICE_OPENING not in as_json.output

    plain = runner.invoke(cli, ["scout", "status", "--home", str(home)])
    assert plain.exit_code == 0, plain.output
    assert plain.output.count(NOTICE_OPENING) == 1


# --- Nothing is written outside <home>/scout without --target ----------------


def test_no_writes_outside_the_home_without_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    earlier = _earlier_scout_project(home, outside / "earlier-scout")
    registered = outside / "registered"
    _init(home, registered)
    unrelated = outside / "unrelated"
    unrelated.mkdir()
    resume = outside / "resume.md"
    resume.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    before = _tree(outside)
    runner = CliRunner()
    for cwd in (earlier, registered, unrelated):
        monkeypatch.chdir(cwd)
        for args in (
            ["scout", "install"],
            ["scout", "resume", "add", str(resume)],
            ["scout", "status"],
            ["scout", "stop"],
        ):
            result = runner.invoke(cli, [*args, "--home", str(home)])
            assert result.exit_code == 0, result.output

    assert _tree(outside) == before
    assert (home / "scout" / "find-jobs.json").is_file()


# --- The server entry uses the same default ----------------------------------


def test_the_server_entry_defaults_to_home_scout_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gigai scout run`` always hands its server an explicit ``--target``;
    started directly with none, the server uses ``<home>/scout`` too.
    """

    from gigai.scout.find_jobs import present_api

    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)
    backends: list[object] = []
    monkeypatch.setattr(
        present_api, "_run_forever", lambda bind, *, backend=None: backends.append(backend)
    )
    home = tmp_path / "home"
    home.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()

    for cwd in (explicit, tmp_path):
        monkeypatch.chdir(cwd)
        present_api.main(["--home", str(home)])
    present_api.main(["--home", str(home), "--target", str(explicit)])

    targets = [backend.target for backend in backends]  # type: ignore[attr-defined]
    assert targets == [(home / "scout").resolve(), (home / "scout").resolve(), explicit.resolve()]
