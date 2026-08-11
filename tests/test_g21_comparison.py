from __future__ import annotations

from pathlib import Path

from gigai.comparison import compare_occurrences
from gigai.occurrence import declare_occurrence, trigger_occurrence

from .test_g21_occurrence import _fixture, _uuids


def test_comparison_binds_two_runs_and_never_selects_winner(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    snapshot = bundle_path.relative_to(
        next((tmp_path / "workpads").rglob("gig_*/"))
    ).as_posix()
    first = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-10",
        snapshot_path=snapshot,
        uuid_factory=_uuids(),
    )
    first_run = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=first.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
    )
    second = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-11",
        snapshot_path=snapshot,
        prior_occurrence_id=first.occurrence_id,
        uuid_factory=_uuids(),
    )
    second_run = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=second.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
    )
    assert first_run.state == second_run.state == "run_terminal"
    assert first_run.run_id and second_run.run_id and first_run.run_id != second_run.run_id

    comparison, attached = compare_occurrences(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        current_occurrence_id=second.occurrence_id,
        uuid_factory=_uuids(),
    )
    assert comparison["current_run_id"] == second_run.run_id
    assert comparison["prior_run_id"] == first_run.run_id
    assert comparison["result"] == "changed"
    assert comparison["selected_winner"] is None
    assert attached.state == "closed"
    assert (attached.workpad / comparison["evidence"][0]["path"]).is_file()
