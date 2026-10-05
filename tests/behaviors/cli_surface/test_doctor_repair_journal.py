"""0110-10-17: a crash in the middle of a journal write blocked every later write, and nothing ran the recovery.

A journal write records its transaction (``scratch/.gigai-journal-<sequence>.json``) before it replaces a
file. A process that dies after that leaves a workpad that refuses the next write until
``journal.reconcile_journal`` has run, and no command and no server start ran it: the next save ended in a
traceback (the API in ``internal_error``), and a user who lost power mid-save had no documented way out.

Two states, by where the process died (a real SIGKILL of a real ``gigai`` process, on a home made by the
real ``gigai setup`` and ``gigai scout install``):

* **before a file was replaced**: the next save is refused with ``JournalReconciliationRequired``; reads
  answer. That refusal now ends in one line that names ``gigai doctor --repair-journal`` for that home
  (``next_action`` in ``--json``), and the API answers ``journal_reconciliation_required`` with
  ``next_action`` instead of ``internal_error``;
* **after the files were replaced** (before the handoff, or before the commit): the next save AND the
  reads are refused with ``JournalConflictError`` ("working evidence is extra"), raised by the read every
  save starts with. RJ2: when the interrupted write's transaction is in ``scratch/``, that refusal carries
  ``next_action`` too and is answered exactly like the first state (one line, ``--json``, the API), also
  when a caller wrapped it in its own error. The same refusal for a stray file with no such transaction
  passes through as before (a traceback, ``internal_error``) in the words it had; the remediation of plain
  ``gigai doctor``'s failed ``journal.index`` names the command in both cases.

What these tests pin:

* in BOTH states the documented command finishes the interrupted write and the next save then succeeds
  (the end outcome), the interrupted answer kept, through the CLI and through the API;
* on a healthy home the command reports nothing to repair and no file of the home or the workpads changes
  by a byte;
* a journal that cannot be reconciled is reported with the journal's own reason and exit code 1, and the
  refusal stays;
* the CLI root and the API's last resort take ONLY a refusal whose raise site set ``next_action``: every
  other exception passes exactly as before;
* RJ2: a read that is not refused never asks whether a write was interrupted (no listing, no git process),
  and the repair takes a workpad's ids from its path and refuses one whose Git markers name another.

Real journal commits and a killed subprocess (``tmp_path``): the integration lane.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading

import click
import httpx
import pytest
from click.testing import CliRunner

import gigai
from gigai import diagnostics, journal
from gigai.cli import cli
from gigai.private_records import PrivateRecordError
from gigai.scout.find_jobs.api.server import LOGGER_NAME, ScoutFindJobsBackend, serve
from gigai.scout.target_resolution import home_scout_target
from tests.behaviors.scout_find_jobs.test_present_logging import _LoggingBackend
from tests.support.setup_home import setup_home

#: The steps of ``journal._record_transition_locked`` after the transaction is on disk; a process killed on
#: entering one dies there. Before any file is replaced:
BEFORE_REPLACE = "_replace_artifacts"
#: After the files are replaced: before the handoff is written, and with the handoff written, before the commit.
AFTER_REPLACE = ("_write_atomic_temporary", "_commit_handoff")
EXTRA_FILE_REFUSAL = "journal working evidence is extra or redirected: if a save was interrupted, run 'gigai doctor --repair-journal' to finish it, then retry"
#: RJ2: the same refusal when the interrupted write's transaction is there (``{command}``: with ``--home`` at a boundary).
INTERRUPTED_FILES_REFUSAL = "journal working evidence is extra or redirected: an earlier write was interrupted; run '{command}' to finish it, then retry"


def _home(tmp_path: Path) -> Path:
    home = setup_home(tmp_path / "home", workpad_root=tmp_path / "workpads")
    installed = CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert installed.exit_code == 0, installed.output
    _save(home, "cloud:gcp", "Yes, four years.")
    return home


def _save(home: Path, question_id: str, answer: str, *, ok: bool = True, as_json: bool = True):
    result = CliRunner().invoke(
        cli,
        ["scout", "answers", "save", question_id, "--question", f"About {question_id}?", "--answer-text", answer, "--home", str(home), *(("--json",) if as_json else ())],
    )
    assert result.exit_code == (0 if ok else 1), result.output
    return result


def _list(home: Path):
    return CliRunner().invoke(cli, ["scout", "answers", "list", "--home", str(home), "--json"])


def _answers(home: Path) -> set[str]:
    listed = _list(home)
    assert listed.exit_code == 0, listed.output
    return {item["question_id"] for item in json.loads(listed.output)["answers"]}


def _repaired_one(home: Path, tmp_path: Path, *args: str) -> None:
    """``gigai doctor <args>`` finishes exactly one interrupted write, and plain ``gigai doctor`` then passes."""

    exit_code, report = _doctor(*args)
    assert exit_code == 0 and report["overall_status"] == "PASS"
    check = _repair_check(report)
    assert check["status"] == "PASS" and check["summary"].startswith("finished 1 interrupted journal write")
    finished = [line for line in check["evidence_safe_to_share"] if line.startswith("finished_gig=")]
    assert len(finished) == 1 and " sequence=" in finished[0] and " commit=" in finished[0]
    assert "finished_writes=1" in check["evidence_safe_to_share"] and "failed_journals=0" in check["evidence_safe_to_share"]
    assert _transactions(tmp_path) == []


def _killed_save(home: Path, question_id: str, *, at: str) -> None:
    """``gigai scout answers save`` in its own process, which is killed on entering ``journal.<at>``."""

    script = (
        "import os, signal, sys\n"
        "import gigai.journal as journal\n"
        f"journal.{at} = lambda *args, **kwargs: os.kill(os.getpid(), signal.SIGKILL)\n"
        "from gigai.cli import cli\n"
        "cli(sys.argv[1:])\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(gigai.__file__).parents[1]), os.environ.get("PYTHONPATH", "")))}
    crashed = subprocess.run(
        [sys.executable, "-c", script, "scout", "answers", "save", question_id, "--question", f"About {question_id}?", "--answer-text", "Yes, six years.", "--home", str(home), "--json"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert crashed.returncode == -signal.SIGKILL, (crashed.returncode, crashed.stdout, crashed.stderr)


def _transactions(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "workpads").glob("projects/*/gigs/*/scratch/.gigai-journal-*.json"))


def _doctor(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(cli, ["doctor", *args, "--json"])
    return result.exit_code, json.loads(result.output)


def _repair_check(report: dict) -> dict:
    assert report["command"] == "doctor" and report["scope"] == "journal_repair"
    return next(check for check in report["checks"] if check["id"] == "journal.repair")


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` by its sha256, every folder by name."""

    seen: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            seen[name] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            seen[name] = "folder"
        else:
            seen[name] = hashlib.sha256(path.read_bytes()).hexdigest()  # a test's own comparison, not product hashing
    return seen


def _index_check(home: Path) -> dict:
    full = CliRunner().invoke(cli, ["doctor", "--home", str(home), "--json"])
    return next(check for check in json.loads(full.output)["checks"] if check["id"] == "journal.index")


@pytest.mark.parametrize("crash_point", (BEFORE_REPLACE, *AFTER_REPLACE))
def test_after_a_killed_save_the_documented_command_makes_the_next_save_succeed(tmp_path: Path, crash_point: str) -> None:
    """The end outcome alone, at every point a save can die after its transaction is on disk."""

    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at=crash_point)
    assert _save(home, "cloud:azure", "Some.", ok=False).exit_code == 1  # the symptom: the next save is refused

    repaired = CliRunner().invoke(cli, ["doctor", "--repair-journal", "--home", str(home)])
    assert repaired.exit_code == 0, repaired.output
    assert "PASS journal.repair: finished 1 interrupted journal write" in repaired.output

    saved = _save(home, "cloud:azure", "Some.", as_json=False)
    assert saved.output.startswith("Saved cloud:azure (revision 1")
    assert _answers(home) == {"cloud:gcp", "cloud:aws", "cloud:azure"}  # the interrupted save was finished, not dropped


def test_killed_before_a_file_is_replaced_the_refusal_names_the_command_and_it_makes_the_next_save_succeed(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at=BEFORE_REPLACE)
    assert len(_transactions(tmp_path)) == 1  # the crash left its transaction, in one workpad

    # The next save: refused in one line that names the command for this home; no traceback.
    command = f"gigai doctor --repair-journal --home {shlex.quote(str(home))}"
    refused = _save(home, "cloud:azure", "Some.", ok=False)
    assert isinstance(refused.exception, SystemExit), refused.exception
    error = json.loads(refused.output)["error"]
    assert error["code"] == "journal_reconciliation_required"
    assert error["next_action"] == command
    assert error["message"] == f"journal transaction state already exists: an earlier write was interrupted; run '{command}' to finish it, then retry"
    plain = _save(home, "cloud:azure", "Some.", ok=False, as_json=False)
    assert isinstance(plain.exception, SystemExit), plain.exception
    assert plain.output == f"Error: {error['message']}\n"
    assert _answers(home) == {"cloud:gcp"}  # reads are not blocked; nothing of either save is visible yet

    # The command, run as the refusal names it.
    named = shlex.split(error["next_action"])
    assert named[:2] == ["gigai", "doctor"]
    _repaired_one(home, tmp_path, *named[2:])

    # The end outcome: the next save succeeds, and the interrupted one was finished, not dropped.
    saved = json.loads(_save(home, "cloud:azure", "Some.").output)
    assert saved["ok"] is True and saved["answer"]["question_id"] == "cloud:azure"
    assert _answers(home) == {"cloud:gcp", "cloud:aws", "cloud:azure"}

    # A second run has nothing left to do.
    exit_code, report = _doctor("--repair-journal", "--home", str(home))
    assert exit_code == 0 and _repair_check(report)["summary"].startswith("no interrupted journal write")
    assert _index_check(home)["status"] == "PASS"


@pytest.mark.parametrize("crash_point", AFTER_REPLACE)
def test_killed_after_the_files_are_replaced_the_refusal_names_the_command_and_it_makes_the_next_save_succeed(tmp_path: Path, crash_point: str) -> None:
    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at=crash_point)
    assert len(_transactions(tmp_path)) == 1

    # RJ2: the next save AND the reads are refused by the read every save starts with, in one line that names
    # the command for this home, exactly like a save refused before a file was replaced; no traceback.
    command = f"gigai doctor --repair-journal --home {shlex.quote(str(home))}"
    message = INTERRUPTED_FILES_REFUSAL.format(command=command)
    for refused in (_save(home, "cloud:azure", "Some.", ok=False), _list(home)):
        assert refused.exit_code == 1 and isinstance(refused.exception, SystemExit), refused.exception
        assert json.loads(refused.output) == {
            "status": "error",
            "error": {"code": "journal_reconciliation_required", "message": message, "next_action": command},
        }
    for plain in (
        _save(home, "cloud:azure", "Some.", ok=False, as_json=False),
        CliRunner().invoke(cli, ["scout", "answers", "list", "--home", str(home)]),
    ):
        assert plain.exit_code == 1 and isinstance(plain.exception, SystemExit), plain.exception
        assert plain.output == f"Error: {message}\n"
    # Plain doctor fails on that workpad, and its remediation names the command.
    index = _index_check(home)
    assert index["status"] == "FAIL" and index["summary"] == "authoritative workpad has uncommitted divergence"
    assert "run 'gigai doctor --repair-journal' to finish it" in index["remediation"]

    # The command, run as the refusal names it.
    named = shlex.split(command)
    _repaired_one(home, tmp_path, *named[2:])

    # The end outcome: the next save succeeds, the reads answer, and the interrupted save was finished.
    saved = json.loads(_save(home, "cloud:azure", "Some.").output)
    assert saved["ok"] is True and saved["answer"]["question_id"] == "cloud:azure"
    assert _answers(home) == {"cloud:gcp", "cloud:aws", "cloud:azure"}
    assert _index_check(home)["status"] == "PASS"


@pytest.mark.parametrize("crash_point", (BEFORE_REPLACE, *AFTER_REPLACE))
def test_through_the_api_the_refusal_names_the_command_and_the_save_succeeds_after_it(tmp_path: Path, crash_point: str) -> None:
    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at=crash_point)
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=home_scout_target(home)), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60) as client:
            body = {"question_id": "cloud:azure", "question": "Azure?", "answer": "Some."}
            refused = client.post("/api/answers", json=body)
            assert refused.status_code == 500
            error = refused.json()["error"]
            command = f"gigai doctor --repair-journal --home {shlex.quote(str(home))}"
            if crash_point == BEFORE_REPLACE:
                # The refusal names the command; reads still answer.
                assert error == {
                    "code": "journal_reconciliation_required",  # was internal_error, "an internal error occurred"
                    "message": f"journal transaction state already exists: an earlier write was interrupted; run '{command}' to finish it, then retry",
                    "next_action": command,
                }
                assert client.get("/api/answers").status_code == 200
            else:
                # RJ2: the save AND the read are refused, and both answer like the first state (it was
                # internal_error, "an internal error occurred", for both).
                assert error == {
                    "code": "journal_reconciliation_required",
                    "message": INTERRUPTED_FILES_REFUSAL.format(command=command),
                    "next_action": command,
                }
                read = client.get("/api/answers")
                assert read.status_code == 500 and read.json()["error"] == error
                story = client.post("/api/stories", json={"title": "A story", "narrative": {"situation": "Synthetic."}})
                assert story.status_code == 500 and story.json()["error"] == error  # it reads the answers before it saves

            # The repair runs beside the live server (it takes the journal's writer lock); the same request then saves.
            _repaired_one(home, tmp_path, "--repair-journal", "--home", str(home))
            saved = client.post("/api/answers", json=body)
            assert saved.status_code in (200, 201), saved.text
            assert {item["question_id"] for item in client.get("/api/answers").json()["answers"]} == {"cloud:gcp", "cloud:aws", "cloud:azure"}
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)


def test_on_a_healthy_home_the_command_reports_nothing_to_repair_and_changes_no_byte(tmp_path: Path) -> None:
    home = _home(tmp_path)
    # One read first: the first read after a write updates the workpad's own caches in scratch/.
    assert _answers(home) == {"cloud:gcp"}
    before = _snapshot(tmp_path)
    assert any("/.git/" in name for name in before) and any(name.startswith("home/") for name in before)

    result = CliRunner().invoke(cli, ["doctor", "--repair-journal", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert "PASS journal.repair: no interrupted journal write in" in result.output and "nothing to repair" in result.output
    assert "finished_writes=0" in result.output and "failed_journals=0" in result.output and "Overall: PASS" in result.output
    after = _snapshot(tmp_path)
    assert {name: (before.get(name), after.get(name)) for name in before.keys() | after.keys() if before.get(name) != after.get(name)} == {}
    _save(home, "cloud:aws", "Yes, six years.")  # and the home still takes a write


def test_a_journal_that_cannot_be_reconciled_is_reported_with_its_reason_and_the_refusal_stays(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at="_replace_artifacts")
    (transaction,) = _transactions(tmp_path)
    transaction.write_bytes(b"{not the transaction the writer left")

    exit_code, report = _doctor("--repair-journal", "--home", str(home))

    check = _repair_check(report)
    assert exit_code == 1 and report["overall_status"] == "FAIL" and check["status"] == "FAIL"
    managed = len(list((tmp_path / "workpads").glob("projects/*/gigs/*")))
    assert managed > 1 and check["summary"] == f"1 of {managed} managed journals could not be reconciled"  # the others were visited
    failed = [line for line in check["evidence_safe_to_share"] if line.startswith("failed_gig=")]
    assert failed == [f"failed_gig={transaction.parents[1].name} code=journal_reconciliation_required reason=journal transaction state is corrupt"]
    assert check["remediation"]
    assert transaction.read_bytes() == b"{not the transaction the writer left"  # left for a person to look at
    assert "journal transaction state already exists" in _save(home, "cloud:azure", "Some.", ok=False).output


def test_only_a_write_refused_behind_an_interrupted_one_names_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.diagnostics import journal_repair_refusal

    interrupted = journal._interrupted_write("journal transaction state already exists")
    assert type(interrupted) is journal.JournalReconciliationRequired and interrupted.code == "journal_reconciliation_required"
    assert interrupted.next_action == journal.JOURNAL_REPAIR_COMMAND == "gigai doctor --repair-journal"
    assert str(interrupted) == (
        "journal transaction state already exists: an earlier write was interrupted; "
        "run 'gigai doctor --repair-journal' to finish it, then retry"
    )

    # The default home needs no --home; another one is named, quoted for a shell.
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "default home"))
    assert journal_repair_refusal(interrupted, None) == (str(interrupted), "gigai doctor --repair-journal")
    assert journal_repair_refusal(interrupted, tmp_path / "default home") == (str(interrupted), "gigai doctor --repair-journal")
    elsewhere = journal_repair_refusal(interrupted, tmp_path / "other home")
    assert elsewhere is not None
    message, command = elsewhere
    assert command == f"gigai doctor --repair-journal --home '{tmp_path / 'other home'}'" and command in message

    # A refusal the reconciliation would only repeat, and any other error, name nothing.
    assert journal.JournalReconciliationRequired("journal transaction state is corrupt").next_action is None
    for other in _others():
        assert journal_repair_refusal(other, tmp_path / "other home") is None
    # The doctor module cannot import the journal's constant at import time (the journal imports it): same words.
    assert EXTRA_FILE_REFUSAL == "journal working evidence is extra or redirected" + journal.INTERRUPTED_WRITE_HINT
    assert f"run '{journal.JOURNAL_REPAIR_COMMAND}' to finish it" in inspect.getsource(diagnostics._journal_index_checks)


def _files_refusal() -> journal.JournalConflictError:
    """RJ2: what ``journal._extra_working_evidence`` raises when an interrupted write's transaction is there."""

    refusal = journal.JournalConflictError(INTERRUPTED_FILES_REFUSAL.format(command=journal.JOURNAL_REPAIR_COMMAND))
    refusal.next_action = journal.JOURNAL_REPAIR_COMMAND
    return refusal


def _wrapped(cause: BaseException) -> PrivateRecordError:
    """A caller's own error raised ``from`` the journal's, as ``private_records`` raises it."""

    wrapper = PrivateRecordError("private_record_conflict", "private record is unavailable")
    wrapper.__cause__ = cause
    return wrapper


def test_the_refusal_is_found_along_the_causes_with_a_bound_and_no_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.diagnostics import _CAUSE_CHAIN_LINKS, journal_repair_refusal

    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "default home"))
    for refusal in (_files_refusal(), journal._interrupted_write("journal transaction state already exists")):
        found = (str(refusal), "gigai doctor --repair-journal")
        assert journal_repair_refusal(refusal, None) == found
        assert journal_repair_refusal(_wrapped(refusal), None) == found  # raise ... from the refusal
        assert journal_repair_refusal(_wrapped(_wrapped(refusal)), None) == found
        while_handling = RuntimeError("raised while the refusal was handled")
        while_handling.__context__ = refusal
        assert journal_repair_refusal(while_handling, None) == found
        # The message is the journal's own, with the home's command; the wrapper's words are not used.
        message, command = journal_repair_refusal(_wrapped(refusal), tmp_path / "other home")
        assert command == f"gigai doctor --repair-journal --home '{tmp_path / 'other home'}'"
        assert message == str(refusal).replace("gigai doctor --repair-journal", command)

        # ``raise ... from None`` cut the chain: a traceback would not print the refusal, and it is not taken.
        cut = RuntimeError("raised from None")
        cut.__context__, cut.__suppress_context__ = refusal, True
        assert journal_repair_refusal(cut, None) is None

        # A bound: the refusal is found behind one link fewer than the bound, and not behind the bound.
        near = far = refusal
        for _ in range(_CAUSE_CHAIN_LINKS - 1):
            near = _wrapped(near)
        assert journal_repair_refusal(near, None) == found
        for _ in range(_CAUSE_CHAIN_LINKS):
            far = _wrapped(far)
        assert journal_repair_refusal(far, None) is None

    # A loop of causes ends, with and without a refusal in it.
    first, second = RuntimeError("first"), RuntimeError("second")
    first.__cause__, second.__cause__ = second, first
    assert journal_repair_refusal(first, None) is None
    looped = _files_refusal()
    first.__cause__, second.__cause__, looped.__cause__ = second, looped, first
    assert journal_repair_refusal(first, None) == (str(looped), "gigai doctor --repair-journal")
    # An error of another kind that happens to carry ``next_action`` is not the journal's refusal.
    foreign = RuntimeError("an assessment failed")
    foreign.next_action = "gigai doctor --repair-journal"  # type: ignore[attr-defined]
    assert journal_repair_refusal(_wrapped(foreign), None) is None


def _others() -> tuple[Exception, ...]:
    """Errors the boundaries must not take: no raise site set ``next_action`` on them."""

    return (
        journal.JournalReconciliationRequired("journal transaction state is corrupt"),  # the repair would only repeat it
        journal.JournalUnbornError("journal head is unexpectedly unborn"),
        journal.JournalConflictError(EXTRA_FILE_REFUSAL),  # names the command in its text, and still passes
        RuntimeError("gigai doctor --repair-journal"),
    )


def test_the_cli_root_takes_only_a_refusal_with_next_action_and_passes_every_other_error_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gigai.cli as cli_module

    raised: list[Exception] = []

    def _raise(_home_root: Path):
        raise raised[-1]

    monkeypatch.setattr(cli_module, "run_doctor", _raise)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "home"))
    for other in _others():
        raised.append(other)
        for args in (["doctor"], ["doctor", "--json"], ["doctor", "--home", str(tmp_path / "elsewhere")]):
            result = CliRunner().invoke(cli, args)
            assert result.exception is other and result.exit_code == 1 and result.output == "", (other, args)

    raised.append(journal._interrupted_write("next handoff path is already present and uncommitted"))
    message = str(raised[-1])
    plain = CliRunner().invoke(cli, ["doctor"])  # the default home: the command as the journal names it
    assert plain.exit_code == 1 and plain.output == f"Error: {message}\n"
    as_json = CliRunner().invoke(cli, ["doctor", "--json"])
    assert as_json.exit_code == 1 and json.loads(as_json.output) == {
        "status": "error",
        "error": {"code": "journal_reconciliation_required", "message": message, "next_action": "gigai doctor --repair-journal"},
    }
    for spelling in (["--home", str(tmp_path / "elsewhere")], [f"--home={tmp_path / 'elsewhere'}"]):
        named = CliRunner().invoke(cli, ["doctor", *spelling, "--json"])
        assert json.loads(named.output)["error"]["next_action"] == f"gigai doctor --repair-journal --home {tmp_path / 'elsewhere'}"

    # RJ2: the refusal of a read that met an interrupted write's files, alone and inside a caller's own error.
    for carrier in (_files_refusal(), _wrapped(_files_refusal())):
        raised.append(carrier)
        message = INTERRUPTED_FILES_REFUSAL.format(command="gigai doctor --repair-journal")
        plain = CliRunner().invoke(cli, ["doctor"])
        assert plain.exit_code == 1 and plain.output == f"Error: {message}\n"
        as_json = CliRunner().invoke(cli, ["doctor", "--json"])
        assert as_json.exit_code == 1 and json.loads(as_json.output) == {
            "status": "error",
            "error": {"code": "journal_reconciliation_required", "message": message, "next_action": "gigai doctor --repair-journal"},
        }
    # An answer a command gave itself is not replaced, whatever it was raised from.
    own = click.ClickException("the command's own answer")
    own.__cause__ = _files_refusal()
    raised.append(own)
    answered = CliRunner().invoke(cli, ["doctor", "--json"])
    assert answered.exit_code == 1 and answered.output == "Error: the command's own answer\n"


class _RaisingBackend(_LoggingBackend):
    """``GET /api/config`` raises whatever the test put in ``raises``."""

    raises: Exception | None = None

    def read_config(self):  # noqa: ANN201 - the double's own signature
        assert self.raises is not None
        raise self.raises


def test_the_api_last_resort_takes_only_a_refusal_with_next_action_and_answers_every_other_error_as_before(caplog: pytest.LogCaptureFixture) -> None:
    backend = _RaisingBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            for other in _others():
                backend.raises = other
                caplog.clear()
                with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                    response = client.get("/api/config")
                assert response.status_code == 500
                assert response.json() == {"error": {"code": "internal_error", "message": "an internal error occurred"}}, other
                (logged,) = [record for record in caplog.records if record.name == LOGGER_NAME and record.levelno >= logging.WARNING]
                assert logged.levelno == logging.ERROR and logged.getMessage() == "unhandled exception in GET /api/config"
                assert logged.exc_info is not None and logged.exc_info[1] is other  # the traceback, as before

            backend.raises = journal._interrupted_write("journal transaction state already exists")
            caplog.clear()
            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                response = client.get("/api/config")
            assert response.status_code == 500
            assert response.json() == {
                "error": {"code": "journal_reconciliation_required", "message": str(backend.raises), "next_action": "gigai doctor --repair-journal"}
            }
            (logged,) = [record for record in caplog.records if record.name == LOGGER_NAME and record.levelno >= logging.WARNING]
            assert logged.levelno == logging.WARNING and logged.exc_info is None
            assert logged.getMessage() == f"GET /api/config refused: {backend.raises}"

            # RJ2: the refusal of a read that met an interrupted write's files, alone and inside a caller's own error.
            message = INTERRUPTED_FILES_REFUSAL.format(command="gigai doctor --repair-journal")
            for carrier in (_files_refusal(), _wrapped(_files_refusal())):
                backend.raises = carrier
                caplog.clear()
                with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                    response = client.get("/api/config")
                assert response.status_code == 500
                assert response.json() == {
                    "error": {"code": "journal_reconciliation_required", "message": message, "next_action": "gigai doctor --repair-journal"}
                }
                (logged,) = [record for record in caplog.records if record.name == LOGGER_NAME and record.levelno >= logging.WARNING]
                assert logged.levelno == logging.WARNING and logged.exc_info is None
                assert logged.getMessage() == f"GET /api/config refused: {message}"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)


def test_repair_journal_is_not_combined_with_a_live_check(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["doctor", "--repair-journal", "--live", "--model-target", "cheap", "--home", str(tmp_path / "home")])
    assert result.exit_code == 2 and "--repair-journal cannot be combined with --live" in result.output
    assert not (tmp_path / "home").exists()


def _stray_file(tmp_path: Path) -> Path:
    """A file no commit holds, where the read of the answers looks (not yet written)."""

    (blobs,) = (tmp_path / "workpads").glob("projects/*/gigs/*/records/record_*/blobs")
    return blobs / "stray.json"


def _serving(home: Path):
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=home_scout_target(home)), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    return server, serving


def test_a_stray_file_with_no_interrupted_write_is_refused_as_before(tmp_path: Path) -> None:
    """RJ2: "extra or redirected" names the repair as a fact only when the write it would finish is there.

    Nothing of this changed with RJ2 (the test passes before it too): the same exception, in the same words.
    """

    home = _home(tmp_path)
    stray = _stray_file(tmp_path)
    workpad = stray.parents[3]
    server, serving = _serving(home)
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60) as client:
            stray.write_bytes(b"{}\n")
            # No transaction in scratch/, then the transaction of a write that was committed long ago (a
            # process killed after its commit leaves one): the repair would finish nothing in either.
            for stale_transaction in (None, workpad / "scratch" / ".gigai-journal-000000000001.json"):
                if stale_transaction is not None:
                    stale_transaction.write_bytes(b"{}\n")
                for refused in (_list(home), _save(home, "cloud:azure", "Some.", ok=False)):
                    # The exception itself passes the CLI root, in the words it had before RJ2.
                    assert refused.exit_code == 1 and type(refused.exception) is journal.JournalConflictError
                    assert str(refused.exception) == EXTRA_FILE_REFUSAL and getattr(refused.exception, "next_action", None) is None
                    assert refused.output == ""
                for response in (client.get("/api/answers"), client.post("/api/answers", json={"question_id": "cloud:azure", "question": "Azure?", "answer": "Some."})):
                    assert response.status_code == 500
                    assert response.json() == {"error": {"code": "internal_error", "message": "an internal error occurred"}}
                exit_code, report = _doctor("--repair-journal", "--home", str(home))
                assert exit_code == 0 and _repair_check(report)["summary"].startswith("no interrupted journal write")
                assert stray.exists()  # the repair has nothing to finish, and it removes nothing
            stray.unlink()
            assert client.get("/api/answers").status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    assert _answers(home) == {"cloud:gcp"}


def test_a_read_or_a_save_that_is_not_refused_asks_nothing_about_an_interrupted_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RJ2 costs nothing on a healthy path: no listing of scratch/ and no git process until the refusal fires."""

    home = _home(tmp_path)
    asked: list[str] = []
    git_calls: list[list[str]] = []
    real_refusal, real_listdir, real_next_sequence, real_run = journal._extra_working_evidence, os.listdir, journal._next_sequence, subprocess.run

    def listing(path=".", *args):  # noqa: ANN001, ANN202 - os.listdir's own signature
        if not isinstance(path, int) and os.path.basename(os.fspath(path)) == journal.TRANSACTION_DIRECTORY:
            asked.append("scratch listing")
        return real_listdir(path, *args)

    def run(command, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202 - subprocess.run's own signature
        if isinstance(command, (list, tuple)) and "git" in os.path.basename(str(command[0])):
            git_calls.append([str(word) for word in command])
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(journal, "_extra_working_evidence", lambda *args: asked.append("refusal") or real_refusal(*args))
    monkeypatch.setattr(journal, "_next_sequence", lambda *args: asked.append("next sequence") or real_next_sequence(*args))
    monkeypatch.setattr(os, "listdir", listing)
    monkeypatch.setattr(subprocess, "run", run)

    def sequence_questions() -> int:
        # ``_next_sequence``'s two git processes: ``show --format=%B --no-patch HEAD`` and ``show --format= --name-only HEAD``.
        return sum(1 for words in git_calls if "show" in words and words[-1] == "HEAD")

    server, serving = _serving(home)
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60) as client:
            # Reads, through the CLI and the API: the refusal is never built, scratch/ is never listed, and no
            # process asks git for the next sequence (a read never does).
            assert _answers(home) == {"cloud:gcp"}
            assert client.get("/api/answers").status_code == 200
            assert asked == [] and sequence_questions() == 0 and git_calls
            # Saves: the write asks for its own sequence, as it always did (two git processes each time), and
            # nothing else is asked.
            _save(home, "cloud:aws", "Yes, six years.")
            assert _answers(home) == {"cloud:gcp", "cloud:aws"}
            assert client.post("/api/answers", json={"question_id": "cloud:azure", "question": "Azure?", "answer": "Some."}).status_code in (200, 201)
            assert asked and set(asked) == {"next sequence"} and sequence_questions() == 2 * len(asked)

            # The counters do see it: a refused read builds the refusal and lists scratch/ (and, with no
            # transaction file there, still starts no git process for the sequence). The file is put there
            # right after a save: a read at a head it has already read does not look at the files again.
            stray = _stray_file(tmp_path)
            stray.write_bytes(b"{}\n")
            del asked[:], git_calls[:]
            assert _list(home).exit_code == 1
            assert set(asked) == {"refusal", "scratch listing"} and sequence_questions() == 0
            # Only a transaction file in scratch/ (here one of a sequence committed long ago) makes the refusal
            # ask git which sequence is next: two processes for each time the refusal is built.
            (stray.parents[3] / "scratch" / ".gigai-journal-000000000001.json").write_bytes(b"{}\n")
            del asked[:], git_calls[:]
            refused = _list(home)
            assert refused.exit_code == 1 and str(refused.exception) == EXTRA_FILE_REFUSAL
            assert set(asked) == {"refusal", "scratch listing", "next sequence"}
            assert sequence_questions() == 2 * asked.count("next sequence") == 2 * asked.count("refusal")
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)


def _set_marker(workpad: Path, key: str, value: str) -> None:
    subprocess.run(
        ["git", "-C", str(workpad), "config", "--local", key, value],
        check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )


def test_the_repair_takes_the_ids_from_the_path_and_refuses_a_workpad_whose_markers_name_another(tmp_path: Path) -> None:
    """RJ2: the ids came from the workpad's own Git markers, so the journal compared the markers with themselves.

    (Before, a layout-2 workpad with an edited marker was still refused, by the journal's check of its
    committed layout marker, as "journal layout state is not recoverable" or "invalid canonical ownership
    IDs". Now the repair says what differs, before ``reconcile_journal`` is called at all.)
    """

    home = _home(tmp_path)
    _killed_save(home, "cloud:aws", at=BEFORE_REPLACE)
    (transaction,) = _transactions(tmp_path)
    workpad = transaction.parents[1]
    project = workpad.parents[1].name
    assert workpad.name.startswith("gig_") and project.startswith("project_")
    other_gig = "gig_00000000-0000-4000-8000-000000000017"
    other_project = "project_00000000-0000-4000-8000-000000000017"
    assert _answers(home) == {"cloud:gcp"}  # one read first: the first read after a write updates the workpad's own caches in scratch/

    def refused(key: str, value: str, shown: str, named: str) -> None:
        _set_marker(workpad, key, value)
        before = _snapshot(tmp_path)
        exit_code, report = _doctor("--repair-journal", "--home", str(home))
        check = _repair_check(report)
        assert exit_code == 1 and report["overall_status"] == "FAIL" and check["status"] == "FAIL"
        evidence = check["evidence_safe_to_share"]
        assert [line for line in evidence if line.startswith("failed_gig=")] == [
            f"failed_gig={workpad.name} code=journal_conflict reason=ownership markers differ from the workpad's path: {key} is {shown}, the directory is {named}"
        ]
        assert "finished_writes=0" in evidence and "failed_journals=1" in evidence
        assert f"managed_workpads={len(list((tmp_path / 'workpads').glob('projects/*/gigs/*')))}" in evidence  # the others were visited
        # Nothing was written: the interrupted write is still there, and no file anywhere changed by a byte.
        after = _snapshot(tmp_path)
        assert {name: (before.get(name), after.get(name)) for name in before.keys() | after.keys() if before.get(name) != after.get(name)} == {}
        assert _transactions(tmp_path) == [transaction]
        if shown != value:
            assert value not in json.dumps(report)  # a marker that is not an id is not printed

    refused("gigai.gig-id", other_gig, other_gig, workpad.name)
    refused("gigai.gig-id", "not an id: some private words", "not a gig id", workpad.name)
    _set_marker(workpad, "gigai.gig-id", workpad.name)
    refused("gigai.project-id", other_project, other_project, project)
    refused("gigai.project-id", other_gig, "not a project id", project)

    # The markers name the directories again: the same command finishes the write.
    _set_marker(workpad, "gigai.project-id", project)
    _repaired_one(home, tmp_path, "--repair-journal", "--home", str(home))
    assert _answers(home) == {"cloud:gcp", "cloud:aws"}
