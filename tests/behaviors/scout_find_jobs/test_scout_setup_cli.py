"""``gigai scout install`` / ``gigai scout resume add`` / ``gigai gig use``.

Covers U6 (install Scout with no Python helper), U14 (activate Scout with no
manual TOML edit), and U15 (a one-step resume + record wrapper), all through
the installed CLI surface (``click.testing.CliRunner`` against ``gigai.cli``),
starting from the same non-interactive ``gigai setup`` invocation release.yml
uses for its smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.run import resolve_newest_resume
from gigai.scout.find_jobs.contracts import FindJobsConfig


def _git_target(target: Path) -> None:
    target.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "Scout Setup Test"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "scout-setup-test@gigai.invalid"], check=True)
    (target / "README.md").write_text("Scout setup CLI fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "--quiet", "-m", "fixture"], check=True)


def _non_git_target(target: Path) -> None:
    target.mkdir(parents=True)


def _setup_and_init(tmp_path: Path, *, git: bool = True) -> tuple[Path, Path]:
    """Non-interactive ``gigai setup`` (as release.yml's smoke step does) + ``gigai init --target``."""

    home = tmp_path / "home"
    target = tmp_path / "target"
    (_git_target if git else _non_git_target)(target)

    runner = CliRunner()
    setup_result = runner.invoke(
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
    assert setup_result.exit_code == 0, setup_result.output

    init_result = runner.invoke(
        cli,
        ["init", "--home", str(home), "--target", str(target), "--username", "scout-setup-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output
    return home, target


def _invoke(runner: CliRunner, home: Path, target: Path, *args: str) -> object:
    result = runner.invoke(cli, [*args, "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_scout_install_binds_approves_and_activates_from_a_clean_project(
    tmp_path: Path, git: bool
) -> None:
    home, target = _setup_and_init(tmp_path, git=git)
    runner = CliRunner()

    payload = _invoke(runner, home, target, "scout", "install")
    assert payload["bound"] is True
    assert payload["approved"] is True
    assert payload["activated"] is True
    assert payload["wrote_starter_config"] is True
    assert payload["changed"] is True
    gig_id = payload["gig_id"]

    gigs_result = runner.invoke(cli, ["gigs", "--home", str(home), "--target", str(target), "--json"])
    assert gigs_result.exit_code == 0, gigs_result.output
    gigs_payload = json.loads(gigs_result.output)
    entry = next(item for item in gigs_payload["entries"] if item["gig_id"] == gig_id)
    assert entry["status"] == "Approved"

    if git:
        import tomllib

        binding = tomllib.loads((target / ".gigai" / "project.toml").read_text(encoding="utf-8"))
        assert binding["active_gig_id"] == gig_id
    else:
        # Non-git targets have no project.toml; the registry is authoritative.
        assert not (target / ".gigai" / "project.toml").exists()

    config_path = target / "find-jobs.json"
    assert config_path.is_file()
    config = FindJobsConfig.from_json(json.loads(config_path.read_text(encoding="utf-8")))
    assert config.roles


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_scout_install_rerun_is_a_no_op(tmp_path: Path, git: bool) -> None:
    home, target = _setup_and_init(tmp_path, git=git)
    runner = CliRunner()

    first = _invoke(runner, home, target, "scout", "install")
    second = _invoke(runner, home, target, "scout", "install")

    assert second["gig_id"] == first["gig_id"]
    assert second["bound"] is False
    assert second["approved"] is False
    assert second["activated"] is False
    assert second["wrote_starter_config"] is False
    assert second["changed"] is False


def test_scout_install_never_overwrites_an_existing_find_jobs_config(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)
    custom = b'{"custom": true}'
    (target / "find-jobs.json").write_bytes(custom)

    runner = CliRunner()
    payload = _invoke(runner, home, target, "scout", "install")
    assert payload["wrote_starter_config"] is False
    assert (target / "find-jobs.json").read_bytes() == custom


def test_gig_use_errors_for_an_unknown_gig_and_names_the_fix(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["gig", "use", "gig_00000000-0000-4000-8000-000000000000", "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "gig_not_installed"
    assert "gigai scout install" in payload["error"]["message"]


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_gig_use_selects_an_installed_approved_gig(tmp_path: Path, git: bool) -> None:
    home, target = _setup_and_init(tmp_path, git=git)
    runner = CliRunner()
    installed = _invoke(runner, home, target, "scout", "install")
    gig_id = installed["gig_id"]

    result = runner.invoke(
        cli, ["gig", "use", gig_id, "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"ok": True, "gig_id": gig_id, "active": True}


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_resolve_workpad_names_gig_use_when_no_active_gig_is_selected(
    tmp_path: Path, git: bool
) -> None:
    from gigai.default_init import initialize_defaults
    from gigai.scout.template import scout_candidate_inventory
    from gigai.workpad import NoActiveGigError, resolve_workpad

    home, target = _setup_and_init(tmp_path, git=git)
    initialize_defaults(
        home_root=home,
        requested_target=target,
        username="scout-setup-test",
        inventory=scout_candidate_inventory(),
    )
    # Bound but not yet approved/active: resolving with no explicit --gig
    # must fail with a message naming `gigai gig use`, not a bare code.
    with pytest.raises(NoActiveGigError, match=r"gigai gig use <gig_id>"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=None)


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_scout_resume_add_creates_the_wrapper_find_jobs_resolves(tmp_path: Path, git: bool) -> None:
    home, target = _setup_and_init(tmp_path, git=git)
    runner = CliRunner()
    _invoke(runner, home, target, "scout", "install")

    resume_source = tmp_path / "resume.md"
    resume_source.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["reference_created"] is True
    assert payload["record_created"] is True

    resolved = resolve_newest_resume(home, target)
    assert resolved is not None


def test_scout_resume_add_is_idempotent_for_the_same_file_bytes(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()
    _invoke(runner, home, target, "scout", "install")

    resume_source = tmp_path / "resume.md"
    resume_source.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    first = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)

    second = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)

    assert second_payload["reference_id"] == first_payload["reference_id"]
    assert second_payload["record_id"] == first_payload["record_id"]
    assert second_payload["reference_created"] is False
    assert second_payload["record_created"] is False


@pytest.mark.parametrize("git", [True, False], ids=["git-target", "non-git-target"])
def test_scout_resume_add_installs_scout_when_not_yet_installed(tmp_path: Path, git: bool) -> None:
    """``resume add`` alone (no prior ``scout install``) is a true one-step command."""

    home, target = _setup_and_init(tmp_path, git=git)
    runner = CliRunner()

    resume_source = tmp_path / "resume.md"
    resume_source.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scout_installed"] is True
    assert payload["reference_created"] is True
    assert payload["record_created"] is True
    gig_id = payload["gig_id"]

    # Scout is now installed/approved/active, same as `scout install` would leave it.
    gigs_result = runner.invoke(cli, ["gigs", "--home", str(home), "--target", str(target), "--json"])
    assert gigs_result.exit_code == 0, gigs_result.output
    gigs_payload = json.loads(gigs_result.output)
    scout_entry = next(item for item in gigs_payload["entries"] if item["gig_id"] == gig_id)
    assert scout_entry["status"] == "Approved"

    config_path = target / "find-jobs.json"
    assert config_path.is_file()

    # Rerunning is idempotent: Scout is already installed, nothing changes.
    second = runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    )
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["scout_installed"] is False
    assert second_payload["reference_created"] is False
    assert second_payload["record_created"] is False


def test_scout_commands_resolve_a_bound_non_git_target_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``install``/``resume add``/``run``/``status``/``stop`` all work with no
    ``--target`` when cwd is (or is under) an already-bound non-Git target.

    Reproduces the exact one-command flow the brief requires: setup -> init
    --target . -> resume add -> run --no-browser -> status -> stop, entirely
    from inside the target directory (and, for `status`, a subfolder of it).
    """

    home, target = _setup_and_init(tmp_path, git=False)
    runner = CliRunner()

    resume_source = tmp_path / "resume.md"
    resume_source.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    monkeypatch.chdir(target)

    install_result = runner.invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert install_result.exit_code == 0, install_result.output
    install_payload = json.loads(install_result.output)
    assert install_payload["bound"] is True

    resume_result = runner.invoke(
        cli, ["scout", "resume", "add", str(resume_source), "--home", str(home), "--json"]
    )
    assert resume_result.exit_code == 0, resume_result.output
    resume_payload = json.loads(resume_result.output)
    # Scout was already installed by the step above: rerunning install via
    # resume add is a no-op here.
    assert resume_payload["scout_installed"] is False
    assert resume_payload["reference_created"] is True

    # Disable every live source so `run` never makes a network call.
    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": False, "hiringcafe": False}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    run_result = runner.invoke(cli, ["scout", "run", "--no-browser", "--home", str(home), "--json"])
    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(run_result.output)
    assert run_payload["ok"] is True

    try:
        # `status` from a subfolder of the target must resolve the same binding.
        subfolder = target / "nested"
        subfolder.mkdir()
        monkeypatch.chdir(subfolder)
        status_result = runner.invoke(cli, ["scout", "status", "--home", str(home), "--json"])
        assert status_result.exit_code == 0, status_result.output
        status_payload = json.loads(status_result.output)
        assert status_payload["state"] == "running"
    finally:
        monkeypatch.chdir(target)
        stop_result = runner.invoke(cli, ["scout", "stop", "--home", str(home), "--json"])
        assert stop_result.exit_code == 0, stop_result.output
        assert json.loads(stop_result.output)["stopped"] is True


def test_scout_install_still_requires_target_from_an_unbound_non_git_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-Git cwd that was never `gigai init --target`-ed keeps requiring
    --target; implicit cwd resolution never invents a binding.
    """

    home, _target = _setup_and_init(tmp_path, git=False)
    unbound = tmp_path / "unbound"
    unbound.mkdir()
    monkeypatch.chdir(unbound)

    result = CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    message = payload["error"]["message"]
    assert "Git repository" in message or "target" in message


def test_starter_find_jobs_config_validates_against_the_contract(tmp_path: Path) -> None:
    from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG, write_starter_find_jobs_config

    target = tmp_path / "target"
    target.mkdir()
    wrote = write_starter_find_jobs_config(target)
    assert wrote is True

    config_path = target / "find-jobs.json"
    loaded = FindJobsConfig.from_json(json.loads(config_path.read_text(encoding="utf-8")))
    assert loaded == STARTER_FIND_JOBS_CONFIG

    # Never overwrites an existing file.
    config_path.write_bytes(b'{"custom": true}')
    assert write_starter_find_jobs_config(target) is False
    assert config_path.read_bytes() == b'{"custom": true}'
