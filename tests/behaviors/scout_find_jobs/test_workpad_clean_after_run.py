"""regression-001: a live find-jobs run must never leave the workpad dirty.

Root cause (fixed on cd1ea89): `market_acquisition._write_raw_payloads`
writes `runs/<run_id>/raw/` whenever the real HTTP recording client is used
(U26), and `progress.py`'s `ProgressWriter` writes `runs/<run_id>/progress/`
as work happens (B4). Neither path is ever named by a journal commit, and
the workpad's `.git/info/exclude` didn't cover either root, so
`git status --porcelain --untracked-files=all` came back non-empty and
`read_index`'s `_require_clean_authority` raised
"authoritative workpad has uncommitted divergence" -- failing `gigai doctor`
and every later `run.py`/`occurrence.py` call on that gig.

These tests use a real managed workpad (via `create_offline`, the same
provisioning a real run uses) so the fix is proven against the actual
`.git` substrate, not a plain `tmp_path`.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

import httpx
import pytest

from gigai.diagnostics import run_doctor
from gigai.index import read_index
from gigai.lifecycle import create_offline
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    FindJobsConfig,
    NodeContext,
    SelectionRule,
    SourceToggles,
)
from gigai.scout.find_jobs.exa_client import EXA_API_KEY_ENV_VAR, ExaSearchClient
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.find_jobs.progress import read_progress
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target

from tests.support.workpad_assertions import assert_managed_workpad_clean


def _uuids():
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 32)
    )
    return lambda: next(values)


def _managed_workpad(tmp_path: Path, name: str = "find-jobs-clean-proof"):
    """A real, git-initialized, journaled workpad -- the same substrate a
    live run uses -- via the same provisioning path `gigai create` does.

    Returns ``(home_root, CreateResult)``: ``home_root`` is what
    ``run_doctor`` (i.e. `gigai doctor`) takes.
    """

    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name=name,
        open_editor=False,
        uuid_factory=_uuids(),
    )
    return home, created


def _find_jobs_config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=False, hiringcafe=False),
    )


class _Watchlist:
    def active_entries(self):
        return ()

    def add_to_watchlist(self, entry):
        return entry


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


def _acquire_with_recording_client(
    workpad: Path, run_id: str, *, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run one real acquire through the RECORDING client (raw/ gets written)
    plus its progress writer (progress/ gets written), directly into
    ``workpad`` -- exactly the two paths regression-001 covers."""

    from types import SimpleNamespace

    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "test-exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://job-boards.greenhouse.io/acme/jobs/1", "title": "SE"}]},
        )

    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)

    context = NodeContext(
        run_id=run_id, project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=f"acquire-{run_id}",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(workpad), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )
    acquire_input = AcquireInput(
        _find_jobs_config(), "sha256:" + "c" * 64, None, (), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        acquire_node(
            context, acquire_input, http_client=client,
            exa=ExaSearchClient(), ats=ATSBoardClients(), watchlist=_Watchlist(),
        )


# --- 1. Repro: fails on cd1ea89, passes after the fix -----------------------


def test_acquire_through_recording_client_leaves_workpad_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, created = _managed_workpad(tmp_path)
    _acquire_with_recording_client(created.workpad, "run_regression_001", monkeypatch=monkeypatch)

    # Sanity: both artifact roots this regression covers actually got written,
    # so a pass here is not a vacuous "nothing was written" pass.
    run_root = created.workpad / "runs" / "run_regression_001"
    assert (run_root / "raw" / "index.json").is_file()
    assert (run_root / "progress" / "steps.json").is_file()

    assert_managed_workpad_clean(created.workpad)
    projection = read_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    assert projection.gig_id == created.gig_id


# --- 2. Upgrade: an 0.1.8.1-shaped dirty workpad self-heals ------------------


def test_existing_workpad_with_untracked_raw_and_progress_self_heals(
    tmp_path: Path,
) -> None:
    """The 0.1.8.1/0.1.9 state: raw/ and progress/ already exist untracked
    (as if written before this fix existed, or before this workpad was ever
    repaired), with `.git/info/exclude` not yet covering them. `read_index`
    (and doctor's `journal.index` check, called the same way `gigai doctor`
    does) must repair and succeed -- no manual step, no data loss."""

    home, created = _managed_workpad(tmp_path, name="upgrade-proof")
    # Simulate a workpad provisioned *before* this fix existed: its
    # `.git/info/exclude` predates RUN_LOCAL_ARTIFACT_EXCLUDES (provisioning
    # already writes them on a fresh workpad, so wipe the file to reproduce
    # the pre-fix state exactly).
    (created.workpad / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    run_root = created.workpad / "runs" / "run_legacy"
    raw_dir = run_root / "raw" / "greenhouse"
    raw_dir.mkdir(parents=True)
    (raw_dir / "0.json.gz").write_bytes(b"not-really-gzip-but-irrelevant-here")
    (run_root / "raw" / "index.json").write_text("{}", encoding="utf-8")
    progress_dir = run_root / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "steps.json").write_text("{}", encoding="utf-8")

    # Confirms the fixture actually reproduces the pre-fix dirty state before
    # asserting the repair path clears it.
    result = subprocess.run(
        ["git", "-C", os.fspath(created.workpad), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout != ""

    projection = read_index(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    assert projection.gig_id == created.gig_id
    assert_managed_workpad_clean(created.workpad)
    # No data loss: the pre-existing raw/progress bytes are untouched.
    assert (raw_dir / "0.json.gz").read_bytes() == b"not-really-gzip-but-irrelevant-here"
    assert (progress_dir / "steps.json").read_text(encoding="utf-8") == "{}"

    report = run_doctor(home)
    checks = {check.id: check for check in report.checks}
    assert checks["journal.index"].status == "PASS"


# --- 3. Second run on the same gig starts and completes, workpad clean ------


def test_second_find_jobs_run_after_a_recorded_run_stays_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, created = _managed_workpad(tmp_path, name="second-run-proof")
    _acquire_with_recording_client(created.workpad, "run_one", monkeypatch=monkeypatch)
    assert_managed_workpad_clean(created.workpad)

    # A second run starts by resolving the journal index -- exactly what
    # run.py:330 does at the start of every run -- and must not raise even
    # though the first run's raw/progress files are still sitting untracked.
    read_index(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)

    _acquire_with_recording_client(created.workpad, "run_two", monkeypatch=monkeypatch)

    second_run_root = created.workpad / "runs" / "run_two"
    assert (second_run_root / "raw" / "index.json").is_file()
    assert read_progress(second_run_root).steps["acquire"]["status"] == "done"

    assert_managed_workpad_clean(created.workpad)
    read_index(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
