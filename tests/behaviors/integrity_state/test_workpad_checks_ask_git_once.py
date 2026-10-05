"""0110-11 STORE2: a workpad check asks git once, and refuses exactly what it refused before.

``journal._check_workpad`` asked git five questions about the workpad's
configuration (four ``git config --local --get`` and ``git remote``) and
``workpad._check_workpad_repository`` the same five and two ``rev-parse``.
Both now start ONE process for the five (``workpad.ownership_config_proven``)
and one for the two, and ask the old questions whenever that one process does
not prove every old answer.

THE PROOF is the table below. The two checks as they were before this change
are copied here (``_old_journal_check``, ``_old_repository_check``: the
oracles). For every configuration in the table the new check and the old one
give the same answer: the same pass, or the same exception type with the same
text. And whenever the one process says "proven", the old check passes.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from gigai import journal, workpad
from gigai.journal import JournalConflictError
from gigai.private_records import migrate_workpad_layout
from gigai.workpad import (
    WORKPAD_GIT_USER_EMAIL,
    WORKPAD_GIT_USER_NAME,
    WORKPAD_GITIGNORE,
    WORKPAD_V2_GITIGNORE,
    WorkpadConflictError,
    workpad_layout_version,
)

PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PASS = ("pass", "")


# --- the oracles: the two checks exactly as they were before STORE2 -----------------------------


def _old_journal_check(root: Path, project_id: str, gig_id: str) -> None:
    """``journal._check_workpad`` at 0.1.10.11 before STORE2, verbatim (``_git`` is the journal's)."""

    _git = journal._git
    expected = {
        "user.name": WORKPAD_GIT_USER_NAME,
        "user.email": WORKPAD_GIT_USER_EMAIL,
        "gigai.project-id": project_id,
        "gigai.gig-id": gig_id,
    }
    try:
        layout_version = workpad_layout_version(root, project_id=project_id, gig_id=gig_id)
    except Exception as exc:
        raise JournalConflictError("journal workpad layout is invalid") from exc
    expected_ignore = WORKPAD_V2_GITIGNORE if layout_version == 2 else WORKPAD_GITIGNORE
    if (root / ".gitignore").read_bytes() != expected_ignore:
        raise JournalConflictError("journal workpad ignore rules differ from declared layout")
    for key, value in expected.items():
        observed = _git(root, "config", "--local", "--get", key, check=False)
        if observed.returncode != 0 or observed.stdout.rstrip("\n") != value:
            raise JournalConflictError("journal workpad ownership marker mismatches")
    if _git(root, "remote").stdout.strip():
        raise JournalConflictError("journal workpad has a remote")


def _old_repository_check(
    root: Path,
    project_id: str,
    gig_id: str,
    *,
    allow_journal: bool = False,
    allow_semantic_state: bool = False,
) -> None:
    """``workpad._check_workpad_repository`` at 0.1.10.11 before STORE2, verbatim (``_git`` is the workpad's)."""

    _git = workpad._git
    entries = {path.name for path in root.iterdir()}
    allowed = {".git", ".gitignore"}
    layout_version = workpad_layout_version(root, project_id=project_id, gig_id=gig_id)
    if allow_journal:
        allowed.add("handoffs")
        allowed.add("scratch")
        allowed.add("manifests")
    if allow_semantic_state:
        allowed.update(
            {
                "gig.md",
                "goals",
                "reviews",
                "reports",
                "decisions",
                "manifests",
                "scratch",
                "state.sqlite",
                "runs",
                "run-plans",
                "graph-selections",
                "review-inputs",
                "addressed",
                "feedback",
                "findings",
                "review",
                "traces",
                "tools",
                "occurrences",
                "comparisons",
            }
        )
    if layout_version == 2:
        allowed.update(workpad._V2_ROOTS)
    if not {".git", ".gitignore"}.issubset(entries) or not entries <= allowed:
        unexpected = sorted(entries - allowed)
        detail = f": {', '.join(unexpected)}" if unexpected else ""
        raise WorkpadConflictError(
            "workpad contains semantic or unexpected top-level state" + detail
        )
    tools = root / "tools"
    if tools.exists() and (tools.is_symlink() or not tools.is_dir()):
        raise WorkpadConflictError("workpad tools root is redirected or invalid")
    handoffs = root / "handoffs"
    if handoffs.exists() and (handoffs.is_symlink() or not handoffs.is_dir()):
        raise WorkpadConflictError("workpad handoff directory is redirected or invalid")
    ignore = root / ".gitignore"
    expected_ignore = WORKPAD_V2_GITIGNORE if layout_version == 2 else WORKPAD_GITIGNORE
    if ignore.is_symlink() or ignore.read_bytes() != expected_ignore:
        raise WorkpadConflictError("workpad ignore rules differ from the declared layout contract")
    inside = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise WorkpadConflictError("workpad is not a local Git repository")
    git_dir = _git(root, "rev-parse", "--absolute-git-dir").stdout.strip()
    if Path(git_dir).resolve(strict=True) != (root / ".git").resolve(strict=True):
        raise WorkpadConflictError("workpad uses an unexpected Git directory")
    expected_config = {
        "user.name": WORKPAD_GIT_USER_NAME,
        "user.email": WORKPAD_GIT_USER_EMAIL,
        "gigai.project-id": project_id,
        "gigai.gig-id": gig_id,
    }
    for key, expected in expected_config.items():
        value = _git(root, "config", "--local", "--get", key, check=False)
        if value.returncode != 0 or value.stdout.rstrip("\n") != expected:
            raise WorkpadConflictError(f"workpad Git ownership marker {key} mismatches")
    if _git(root, "remote").stdout.strip():
        raise WorkpadConflictError("workpad must not configure a Git remote")
    if (
        not allow_journal
        and _git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
    ):
        raise WorkpadConflictError("G05 workpad must remain unborn without a commit")


# --- the fixture ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_git_configuration_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each case says what the environment gives git: nothing a developer's or an agent's shell exports leaks in."""

    for name in list(os.environ):
        if name.startswith(("GIT_CONFIG_", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING")):
            monkeypatch.delenv(name)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A workpad as GigAI provisions it: unborn, layout v1, the four markers."""

    path = tmp_path / "workpad"
    path.mkdir()
    workpad._initialize_workpad_repository(path, PROJECT_ID, GIG_ID)
    return path


def _raw_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True, check=True,
    )


def _append(root: Path, text: str) -> None:
    with (root / ".git" / "config").open("a", encoding="utf-8") as stream:
        stream.write(text)


def _environment(monkeypatch: pytest.MonkeyPatch, *pairs: tuple[str, str]) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(pairs)))
    for index, (key, value) in enumerate(pairs):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)


class _Spawns:
    """The git processes started while it is open: ``subprocess.run`` is how both modules start one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.commands: list[str] = []
        real = subprocess.run

        def run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.path.basename(str(argv[0])) == "git":
                self.commands.append(" ".join(str(part) for part in argv[3:]))  # after ``git -C <root>``
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)


def _outcome(check: Callable[[], None]) -> tuple[str, str]:
    try:
        check()
    except Exception as error:  # noqa: BLE001 - the type and the text are what is compared
        return type(error).__name__, str(error)
    return PASS


# --- the table -----------------------------------------------------------------------------------

Change = Callable[[Path, pytest.MonkeyPatch], object]
MARKERS = ("user.name", "user.email", "gigai.project-id", "gigai.gig-id")


def _unset(key: str) -> Change:
    return lambda root, _monkeypatch: _raw_git(root, "config", "--local", "--unset", key)


def _set(key: str, value: str) -> Change:
    return lambda root, _monkeypatch: _raw_git(root, "config", "--local", key, value)


def _text(text: str) -> Change:
    return lambda root, _monkeypatch: _append(root, text)


def _included_file(directive: str, content: str) -> Change:
    def change(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
        (root / ".git" / "extra").write_text(content, encoding="utf-8")
        _append(root, directive)

    return change


def _marker_only_in_an_included_file(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    _raw_git(root, "config", "--local", "--unset", "gigai.gig-id")
    (root / ".git" / "extra").write_text(f"[gigai]\n\tgig-id = {GIG_ID}\n", encoding="utf-8")
    _append(root, "[include]\n\tpath = extra\n")


def _worktree_scope_remote(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    _raw_git(root, "config", "--local", "extensions.worktreeConfig", "true")
    (root / ".git" / "config.worktree").write_text('[remote "origin"]\n\turl = https://example.invalid/x.git\n', encoding="utf-8")


def _marker_only_in_the_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _raw_git(root, "config", "--local", "--unset", "user.email")
    _environment(monkeypatch, ("user.email", WORKPAD_GIT_USER_EMAIL))


def _not_a_repository(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.rmtree(root / ".git")
    (root / ".git").mkdir()


def _git_directory_elsewhere(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = root.parent / "elsewhere.git"
    (root / ".git").rename(elsewhere)
    (root / ".git").write_text(f"gitdir: {elsewhere}\n", encoding="utf-8")


def _legacy_remotes_file(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote described outside the configuration, the way git did before 1.5: ``git remote`` does not list it."""

    (root / ".git" / "remotes").mkdir(exist_ok=True)
    (root / ".git" / "remotes" / "legacy").write_text("URL: https://example.invalid/x.git\nPull: refs/heads/main:refs/heads/origin\n", encoding="utf-8")


def _legacy_branches_file(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / ".git" / "branches").mkdir(exist_ok=True)
    (root / ".git" / "branches" / "oldstyle").write_text("https://example.invalid/y.git#main\n", encoding="utf-8")


def _no_git(_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)


def _upper_case_section(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    config = root / ".git" / "config"
    config.write_text(config.read_text(encoding="utf-8").replace("[user]", "[USER]").replace("\tname =", "\tNAME ="), encoding="utf-8")


#: The old check's answer here is whatever the installed git says of such a configuration; the new one's must be the same.
GIT_DECIDES = "git decides"

#: (case, the change to a provisioned workpad, "refuses" / "passes" as the OLD check answers, whether one process proves it).
CASES: tuple[tuple[str, Change, str, bool], ...] = (
    ("as provisioned", lambda _root, _monkeypatch: None, "passes", True),
    # A missing key.
    *((f"{key} is missing", _unset(key), "refuses", False) for key in MARKERS),
    # A wrong value.
    *((f"{key} has another value", _set(key, "someone-else"), "refuses", False) for key in MARKERS),
    ("user.name differs only in case", _set("user.name", WORKPAD_GIT_USER_NAME.lower()), "refuses", False),
    ("a marker is set twice, the last value wrong", _text("[gigai]\n\tgig-id = gig_other\n"), "refuses", False),
    ("a marker is set twice, the last value right", _text(f"[gigai]\n\tgig-id = gig_other\n\tgig-id = {GIG_ID}\n"), "passes", False),
    ("a marker is set again without a value", _text("[user]\n\tname\n"), "refuses", False),
    ("a marker is only in an included file", _marker_only_in_an_included_file, "refuses", False),
    ("a marker is only in the environment", _marker_only_in_the_environment, "refuses", False),
    # A remote present.
    ("a remote with a url", _text('[remote "origin"]\n\turl = https://example.invalid/x.git\n'), "refuses", False),
    ("a remote with only a fetch line", _text('[remote "backup"]\n\tfetch = +refs/heads/*:refs/remotes/backup/*\n'), "refuses", False),
    ("a remote in an included file", _included_file("[include]\n\tpath = extra\n", '[remote "origin"]\n\turl = https://example.invalid/x.git\n'), "refuses", False),
    ("a remote in the worktree's configuration", _worktree_scope_remote, "refuses", False),
    ("a remote given by the environment", lambda _root, monkeypatch: _environment(monkeypatch, ("remote.origin.url", "https://example.invalid/x.git")), "refuses", False),
    # What git itself refuses to read.
    ("a configuration file git cannot parse", _text("[user\n"), "refuses", False),
    ("a branch section with an empty name", _text('[branch ""]\n\tx = 1\n'), GIT_DECIDES, False),
    ("a remote whose name is a space", _text('[remote " "]\n\turl = https://example.invalid/x.git\n'), GIT_DECIDES, False),
    ("a boolean git cannot read", _set("core.bare", "maybe"), GIT_DECIDES, False),
    ("not a repository", _not_a_repository, "refuses", False),
    ("no git executable", _no_git, "refuses", False),
    # What the old check let through, by its own questions: still let through, by the same questions.
    ("a marker's value ends in a line break", _text('[user]\n\tname = "GigAI Journal\\n"\n'), "passes", False),
    ("remote.pushDefault alone names no remote", _text("[remote]\n\tpushDefault = origin\n"), "passes", False),
    ("an included file that changes nothing asked", _included_file("[include]\n\tpath = extra\n", "[user]\n\tname = someone-else\n"), "passes", False),
    ("another key in the repository's file", _set("core.editor", "true"), "passes", False),
    ("another key from the environment", lambda _root, monkeypatch: _environment(monkeypatch, ("core.editor", "true")), "passes", False),
    # Still one process. The two legacy files are in no configuration: if a git ever lists them in ``git remote``, the
    # old check refuses, "proven" is then wrong, and these rows fail.
    ("a remote described in a legacy .git/remotes file", _legacy_remotes_file, GIT_DECIDES, True),
    ("a remote described in a legacy .git/branches file", _legacy_branches_file, GIT_DECIDES, True),
    ("the section and key names in upper case", _upper_case_section, "passes", True),
    (
        "an agent shell's credential settings in the environment",
        lambda _root, monkeypatch: _environment(monkeypatch, ("credential.interactive", "false"), ("credential.guiPrompt", "not-a-boolean")),
        "passes", True,
    ),
)


def _assert_the_table_is_right(old: tuple[str, str], old_answer: str) -> None:
    """The table says what the OLD check answers, so a case cannot quietly turn into another one."""

    if old_answer != GIT_DECIDES:
        assert (old == PASS) is (old_answer == "passes"), old


@pytest.mark.parametrize(("change", "old_answer", "one_process"), [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_the_journal_check_answers_as_the_old_one_for_every_configuration(
    root: Path, monkeypatch: pytest.MonkeyPatch, change: Change, old_answer: str, one_process: bool
) -> None:
    change(root, monkeypatch)
    old = _outcome(lambda: _old_journal_check(root, PROJECT_ID, GIG_ID))
    _assert_the_table_is_right(old, old_answer)
    spawns = _Spawns(monkeypatch)
    new = _outcome(lambda: journal._check_workpad(root, PROJECT_ID, GIG_ID))
    assert new == old  # the same pass, or the same exception type and text
    started = list(spawns.commands)
    proven = workpad.ownership_config_proven(root, PROJECT_ID, GIG_ID)
    assert proven is one_process
    if proven:
        assert old == PASS  # "proven" never says more than the five old questions
        assert started == ["config --list -z --show-scope"]  # was: four ``config --local --get`` and ``remote``


@pytest.mark.parametrize(("change", "old_answer", "one_process"), [case[1:] for case in CASES], ids=[case[0] for case in CASES])
@pytest.mark.parametrize("allow_journal", (False, True))
def test_the_repository_check_answers_as_the_old_one_for_every_configuration(
    root: Path, monkeypatch: pytest.MonkeyPatch, change: Change, old_answer: str, one_process: bool, allow_journal: bool
) -> None:
    change(root, monkeypatch)
    old = _outcome(lambda: _old_repository_check(root, PROJECT_ID, GIG_ID, allow_journal=allow_journal))
    _assert_the_table_is_right(old, old_answer)
    spawns = _Spawns(monkeypatch)
    new = _outcome(lambda: workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID, allow_journal=allow_journal))
    assert new == old
    if one_process:
        # Was: two rev-parse, four config, remote (and the unborn question when no journal is allowed).
        assert spawns.commands == [
            "rev-parse --is-inside-work-tree --absolute-git-dir",
            "config --list -z --show-scope",
            *(() if allow_journal else ("rev-parse --verify HEAD",)),
        ]


REPOSITORY_ONLY: tuple[tuple[str, Change, str], ...] = (
    ("the Git directory is somewhere else", _git_directory_elsewhere, "workpad uses an unexpected Git directory"),
    ("not a repository", _not_a_repository, "workpad is not a local Git repository"),
    ("an unexpected top-level entry", lambda root, _monkeypatch: (root / "notes.txt").write_text("x", encoding="utf-8"), "workpad contains semantic or unexpected top-level state: notes.txt"),
    ("other ignore rules", lambda root, _monkeypatch: (root / ".gitignore").write_bytes(b"/objects/\n"), "workpad ignore rules differ from the declared layout contract"),
    ("a remote", _text('[remote "origin"]\n\turl = https://example.invalid/x.git\n'), "workpad must not configure a Git remote"),
    ("a marker with another value", _set("gigai.project-id", "project_other"), "workpad Git ownership marker gigai.project-id mismatches"),
)


@pytest.mark.parametrize(("change", "text"), [case[1:] for case in REPOSITORY_ONLY], ids=[case[0] for case in REPOSITORY_ONLY])
def test_the_repository_check_refuses_with_the_old_text(root: Path, monkeypatch: pytest.MonkeyPatch, change: Change, text: str) -> None:
    change(root, monkeypatch)
    old = _outcome(lambda: _old_repository_check(root, PROJECT_ID, GIG_ID))
    assert old == ("WorkpadConflictError", text)
    assert _outcome(lambda: workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)) == old


def test_a_committed_workpad_is_refused_where_none_is_allowed_and_the_journal_checks_text_is_the_old_one(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert migrate_workpad_layout(workpad=root, project_id=PROJECT_ID, gig_id=GIG_ID, uuid_factory=lambda: uuid.UUID("11111111-1111-4111-8111-111111111111"))
    old = _outcome(lambda: _old_repository_check(root, PROJECT_ID, GIG_ID))
    assert old == ("WorkpadConflictError", "G05 workpad must remain unborn without a commit")  # the last question, still its own process
    assert _outcome(lambda: workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)) == old
    for change, text in (
        (_text('[remote "origin"]\n\turl = https://example.invalid/x.git\n'), "journal workpad has a remote"),
        (_set("user.email", "someone@example.invalid"), "journal workpad ownership marker mismatches"),
    ):
        kept = (root / ".git" / "config").read_bytes()
        change(root, monkeypatch)
        old = _outcome(lambda: _old_journal_check(root, PROJECT_ID, GIG_ID))
        assert old == ("JournalConflictError", text)
        assert _outcome(lambda: journal._check_workpad(root, PROJECT_ID, GIG_ID)) == old
        (root / ".git" / "config").write_bytes(kept)


def test_a_layout_v2_workpad_with_a_journal_is_checked_with_three_and_four_processes(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The workpad every Scout home has: was 7 git processes a journal check and 9 a repository check."""

    migrate_workpad_layout(workpad=root, project_id=PROJECT_ID, gig_id=GIG_ID, uuid_factory=lambda: uuid.UUID("11111111-1111-4111-8111-111111111111"))
    journal._check_workpad(root, PROJECT_ID, GIG_ID)  # the layout marker's publisher is kept by the first check
    assert _outcome(lambda: _old_journal_check(root, PROJECT_ID, GIG_ID)) == PASS
    assert _outcome(lambda: _old_repository_check(root, PROJECT_ID, GIG_ID, allow_journal=True)) == PASS
    spawns = _Spawns(monkeypatch)
    journal._check_workpad(root, PROJECT_ID, GIG_ID)
    layout = [command.split()[0] for command in spawns.commands[:2]]
    assert layout == ["show", "cat-file"] and spawns.commands[2:] == ["config --list -z --show-scope"]
    del spawns.commands[:]
    workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID, allow_journal=True)
    assert [command.split()[0] for command in spawns.commands] == ["show", "cat-file", "rev-parse", "config"]
    del spawns.commands[:]
    _old_journal_check(root, PROJECT_ID, GIG_ID)
    assert len(spawns.commands) == 7
    del spawns.commands[:]
    _old_repository_check(root, PROJECT_ID, GIG_ID, allow_journal=True)
    assert len(spawns.commands) == 9


def test_a_check_that_passed_is_asked_again_and_sees_what_changed(root: Path) -> None:
    """Nothing is kept between two checks: a retry after a refusal sees the new state, and a pass does not outlive a change."""

    journal._check_workpad(root, PROJECT_ID, GIG_ID)
    workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)
    _raw_git(root, "remote", "add", "origin", "https://example.invalid/x.git")
    assert _outcome(lambda: journal._check_workpad(root, PROJECT_ID, GIG_ID)) == ("JournalConflictError", "journal workpad has a remote")
    assert _outcome(lambda: workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)) == ("WorkpadConflictError", "workpad must not configure a Git remote")
    _raw_git(root, "remote", "remove", "origin")
    journal._check_workpad(root, PROJECT_ID, GIG_ID)
    workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)
    _raw_git(root, "config", "--local", "gigai.gig-id", "gig_other")
    assert _outcome(lambda: journal._check_workpad(root, PROJECT_ID, GIG_ID)) == ("JournalConflictError", "journal workpad ownership marker mismatches")
    assert _outcome(lambda: workpad._check_workpad_repository(root, PROJECT_ID, GIG_ID)) == ("WorkpadConflictError", "workpad Git ownership marker gigai.gig-id mismatches")
