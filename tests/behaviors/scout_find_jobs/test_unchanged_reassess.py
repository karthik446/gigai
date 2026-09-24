"""uat-bug-009: an UNCHANGED posting with no successful prior assessment
must not be permanently unassessable.

Root cause (confirmed): ``market_acquisition.py``'s candidate loop treated
every ``RowOutcome.UNCHANGED`` row as excluded from ``candidates`` outright
(the same URL/content-digest was already seen in an earlier acquire batch),
regardless of whether that earlier run ever actually produced a *successful*
assessment for it -- see the pre-fix code at what is now
``market_acquisition.py``'s ``_acquire_node_body`` candidates loop (line
~588 on 332adbb: ``if outcome in {RowOutcome.NEW, RowOutcome.EDITED}``, no
UNCHANGED branch at all). ``proposal_execution.py``'s own candidate loop
mirrored the same exclusion (line ~230 on 332adbb: ``if outcome not in
(RowOutcome.NEW, RowOutcome.EDITED): continue``). A run 1 that failed at
assess (uat-bug-005) left every posting acquired but never assessed; run 2
re-acquiring the identical postings saw them all as UNCHANGED and excluded
every one of them from selection -- exactly the operator's evidence run
(``assessed=0`` on 17 UNCHANGED postings, every earlier run having failed at
assess).

These tests call ``acquire_node`` directly (EXECUTED), the same way
``test_acquire_network.py`` does, against a real, git-initialized, journaled
workpad (``_managed_workpad``, the identical helper that file uses) across
two "runs" -- a real workpad, rather than a mocked ``import_public_rows``,
is required here because ``_prior_observations``'s own UNCHANGED detection
reads back the *real* ``records/scout-acquisition/*/input.json`` acquire's
own journal write produced for the earlier run. Each run's sealed
find-jobs-run-input.json, and (where a prior run needs to have "succeeded at
assess") its outputs/assess.json, are written by hand on top of that real
workpad -- the identical fixture-construction style
``test_assess_model_policy.py``'s ``_run_assess`` already uses for its own
hand-written ``outputs/acquire.json``/sealed run input.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import uuid

from gigai.canonical import canonical_json_bytes
from gigai.lifecycle import create_offline
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AcquireOutput,
    ATSProvider,
    AssessmentResult,
    AssessOutput,
    FindJobsConfig,
    MatrixStatus,
    ModelTarget,
    NodeContext,
    PinnedResume,
    PostingRow,
    PostingRowResult,
    Producer,
    RequirementMatrixRow,
    RowOutcome,
    SelectedPosting,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target

FIXTURES = Path(__file__).parent / "fixtures"


def _managed_workpad(tmp_path: Path, name: str = "uat-bug-009-proof") -> tuple[Path, str, str]:
    """A real, git-initialized, journaled workpad -- see test_acquire_network.py's own.

    uat-bug-009's own tests need ``import_public_rows`` to actually write
    (unlike most of test_acquire_network.py's tests, which mock it out) so
    ``_prior_observations``/``_prior_assessments`` have real, on-disk
    evidence to read back for run 2 -- so this also returns the workpad's
    real ``project_id``/``gig_id`` for ``NodeContext`` to match, rather than
    the placeholder ids a mocked-``import_public_rows`` test can get away
    with.
    """
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home, workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",), open_with_target=False,
        )
    )
    initialize_target(
        home_root=home, requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 32))
    created = create_offline(
        home_root=home, requested_target=target, name=name, open_editor=False,
        uuid_factory=lambda: next(values),
    )
    return created.workpad, created.project_id, created.gig_id


def _context(workpad: Path, project_id: str, gig_id: str, run_id: str, *, operation_key: str) -> NodeContext:
    return NodeContext(
        run_id=run_id, project_id=project_id, gig_id=gig_id,
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=operation_key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(workpad), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _config() -> FindJobsConfig:
    payload = json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
    config = FindJobsConfig.from_json(payload)
    return replace(config, sources=SourceToggles(exa=False, ats=False, hiringcafe=False))


def _row(url: str, *, company: str = "Acme", title: str = "Software Engineer", content_sha256: str = "sha256:" + "c" * 64) -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme",
        company=company, title=title, location="Denver, CO",
        published_at="2026-09-20T00:00:00Z", content_sha256=content_sha256,
        source_kind=SourceKind.EXA, query_key="software-engineer",
    )


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


class _Watchlist:
    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return ()


def _seal_run_input(workpad: Path, run_id: str, *, resume_revision_id: str) -> None:
    """Hand-write the sealed find-jobs-run-input.json a real run would have.

    This is the file ``run.launch_find_jobs_run`` writes before the graph
    even starts (before acquire's node body runs) -- see
    ``market_acquisition._resume_revision_for_run``'s own docstring for why
    acquire reads this file rather than growing AcquireInput's own sealed
    contract with a resume field.
    """
    config = _config()
    run_dir = workpad / "runs" / run_id
    (run_dir / "sealed").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": {
            "record_id": "record_00000000-0000-4000-8000-000000000001",
            "revision_id": resume_revision_id,
            "content_sha256": "sha256:" + "d" * 64,
        },
    }
    (run_dir / "sealed" / "find-jobs-run-input.json").write_text(json.dumps(payload))


def _write_successful_assess_output(
    workpad: Path, run_id: str, *, posting: PostingRow, resume_revision_id: str
) -> None:
    """Hand-write outputs/assess.json for an earlier run that DID succeed.

    A minimal, contract-valid ``AssessOutput`` with exactly one successful
    assessment for ``posting`` -- everything ``_prior_assessments`` needs to
    find it, and nothing ``_validate_assessment_partition`` would reject.
    """
    assert posting.content_sha256 is not None
    selected = SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True)
    pinned = PinnedResume("record_00000000-0000-4000-8000-000000000001", resume_revision_id, "sha256:" + "d" * 64)
    assessment = AssessmentResult(
        posting=selected,
        matrix=(RequirementMatrixRow("Python", ("Built APIs",), MatrixStatus.MET),),
        suggestions=(), questions=(), proposal_revision_ref=None,
    )
    output = AssessOutput(
        selected_postings=(selected,), pinned_resume=pinned, target=str(workpad),
        selection_cap=10, selection_rule=SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
        candidate_rows=(PostingRowResult(posting, RowOutcome.NEW),),
        assessments=(assessment,), not_assessed=(), proposal_revision_refs=(),
        model_target=ModelTarget.OLLAMA_LOCAL,
        producer=Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "fixture"),
        usage=None, failures=(),
    )
    outputs_dir = workpad / "runs" / run_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "assess.json").write_bytes(canonical_json_bytes(output.to_json()))


def _acquire(workpad: Path, project_id: str, gig_id: str, run_id: str, rows: list[PostingRow]) -> "AcquireOutput":
    # A real, git-journaled workpad (not a mocked import_public_rows) is
    # required: _prior_observations's own UNCHANGED detection reads back the
    # real records/scout-acquisition/*/input.json acquire's own journal
    # write produced for the earlier run.
    input = AcquireInput(_config(), "sha256:" + "e" * 64, None, tuple(rows), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)
    return acquire_node(
        _context(workpad, project_id, gig_id, run_id, operation_key=f"acquire-{run_id}"), input,
        http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(),
    )


# --- Acceptance test 1 (the ticket's repro): run 1 acquires postings and
# fails at assess (no outputs/assess.json is ever written for it -- exactly
# what a failed run leaves behind); run 2, re-acquiring the identical
# postings, must assess them up to the cap rather than seeing every one as
# UNCHANGED-and-therefore-excluded. This FAILS on 332adbb (candidates == 0
# in run 2, every row UNCHANGED and unconditionally excluded).


def test_run2_reassesses_postings_whose_run1_never_produced_an_assessment(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    rows = [
        _row("https://boards.greenhouse.io/acme/jobs/101", company="Acme", title="Senior Software Engineer"),
        _row("https://boards.greenhouse.io/acme/jobs/102", company="Globex", title="Staff Software Engineer", content_sha256="sha256:" + "f" * 64),
    ]

    run1_id = "run_00000000-0000-4000-8000-000000000001"
    _seal_run_input(workpad, run1_id, resume_revision_id="revision_00000000-0000-4000-8000-000000000001")
    out1 = _acquire(workpad, project_id, gig_id, run1_id, rows)
    assert {row.outcome for row in out1.rows} == {RowOutcome.NEW}
    assert len(out1.selected_postings) == 2
    # run 1 "fails at assess": no runs/<run1_id>/outputs/assess.json is ever
    # written (the exact shape a real AssessInput/model failure leaves).

    run2_id = "run_00000000-0000-4000-8000-000000000002"
    _seal_run_input(workpad, run2_id, resume_revision_id="revision_00000000-0000-4000-8000-000000000001")
    out2 = _acquire(workpad, project_id, gig_id, run2_id, rows)

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    # The bug: both rows would be excluded from candidates entirely, leaving
    # selected_postings empty and the run "succeeds with 0 assessed" again.
    assert len(out2.selected_postings) == 2
    assert {p.normalized_url for p in out2.selected_postings} == {row.normalized_url for row in rows}
    assert out2.carried_forward_assessments == ()


# --- Acceptance test 2: a posting successfully assessed in run 1, unchanged
# in run 2 (same resume revision), is skipped in run 2 and its earlier
# assessment is carried into run 2's output.


def test_run2_skips_and_carries_forward_a_successfully_assessed_unchanged_posting(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"
    posting = _row("https://boards.greenhouse.io/acme/jobs/201")

    run1_id = "run_00000000-0000-4000-8000-000000000011"
    _seal_run_input(workpad, run1_id, resume_revision_id=resume_revision_id)
    out1 = _acquire(workpad, project_id, gig_id, run1_id, [posting])
    assert len(out1.selected_postings) == 1
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=resume_revision_id)

    run2_id = "run_00000000-0000-4000-8000-000000000012"
    _seal_run_input(workpad, run2_id, resume_revision_id=resume_revision_id)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    # Genuinely skippable this time: a successful prior assessment exists
    # for the same content digest and the same resume revision.
    assert out2.selected_postings == ()
    assert len(out2.carried_forward_assessments) == 1
    carried = out2.carried_forward_assessments[0]
    assert carried.normalized_url == posting.normalized_url
    assert carried.result.posting.content_sha256 == posting.content_sha256
    assert carried.result.matrix[0].requirement == "Python"


# --- Acceptance test 3: a changed resume revision makes the earlier
# assessment not count -- the posting becomes re-eligible even though its
# content digest is unchanged.


def test_changed_resume_revision_makes_the_posting_re_eligible(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    posting = _row("https://boards.greenhouse.io/acme/jobs/301")

    run1_id = "run_00000000-0000-4000-8000-000000000021"
    old_resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"
    _seal_run_input(workpad, run1_id, resume_revision_id=old_resume_revision_id)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=old_resume_revision_id)

    # run 2 is sealed with a DIFFERENT resume revision (the operator updated
    # their resume between runs) -- the prior assessment was against the old
    # resume and must not count.
    run2_id = "run_00000000-0000-4000-8000-000000000022"
    new_resume_revision_id = "revision_00000000-0000-4000-8000-000000000099"
    _seal_run_input(workpad, run2_id, resume_revision_id=new_resume_revision_id)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    assert len(out2.selected_postings) == 1
    assert out2.selected_postings[0].normalized_url == posting.normalized_url
    assert out2.carried_forward_assessments == ()


def test_missing_sealed_run_input_never_carries_forward(tmp_path: Path) -> None:
    """No sealed find-jobs-run-input.json at all (an older run dir, or a
    direct-call test that never seals one) -- the resume revision is
    unresolvable, so an UNCHANGED row is conservatively re-eligible rather
    than silently trusted as "already assessed"."""
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    posting = _row("https://boards.greenhouse.io/acme/jobs/401")
    resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"

    run1_id = "run_00000000-0000-4000-8000-000000000031"
    _seal_run_input(workpad, run1_id, resume_revision_id=resume_revision_id)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=resume_revision_id)

    run2_id = "run_00000000-0000-4000-8000-000000000032"
    # No _seal_run_input call for run2_id: its sealed/find-jobs-run-input.json
    # is never written.
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert len(out2.selected_postings) == 1
    assert out2.carried_forward_assessments == ()
