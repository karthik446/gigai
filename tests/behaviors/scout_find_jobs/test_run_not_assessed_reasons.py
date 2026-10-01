"""0110-037: a launched run says why it did not assess a posting.

A launched run's layout, as ``bindings._assess_bound`` gives it to the assess
node: ``target`` is the project folder, the run's sealed input is in the
WORKPAD (``runs/<run_id>/sealed``). The node read the config that labels the
rows it does not assess from the target folder, found none, and so wrote no
reason at all: on the Jobs page those rows said "Not assessed" where a quick
assess, or a run called directly, says "Not fully assessed" (``over_cap``),
"Duplicate" (``duplicate``) or the location reason (``display.js``).

What is pinned here, on a real gig with a hand-written acquire batch and a
fake model on ``proposal_execution.resolve_model_adapter``:

* each reason on its row, the same labels as everywhere else: ``over_cap``,
  ``duplicate``, ``location_mismatch``; an assessed row has none; a posting
  whose title is not one of the run's roles is not a row of the run at all;
* the labels are the ones acquire's own selection gives (``selection.
  select_for_assessment`` over the same rows), so the two cannot disagree;
* the run's selection does not change: the same postings are assessed, with
  the same number of model calls, as by a run that has no config to label
  from (what every launched run was before);
* the reads serve it: ``GET .../posting``'s ``not_assessed_reason`` and the
  ``payload.not_assessed`` of the page that lists the row.

Acquire never keeps a posting outside the run's countries (it is counted in
``dropped_counts``), so in a run launched from the UI ``location_mismatch``
only shows on a row an older batch still carries; the batch here has one.
The catalog-size run through the real server is
``tests/api_e2e/test_run_reads_fast_journey.py``.
"""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

from gigai.canonical import digest_imported_bytes
from gigai.scout.find_jobs.api import run_reads
from gigai.scout.find_jobs.contracts import (
    AssessInput,
    ATSProvider,
    ModelTarget,
    PostingRow,
    PostingRowResult,
    RowOutcome,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
    SourceKind,
)
from gigai.scout.find_jobs.selection import select_for_assessment, selection_limits
from gigai.scout.proposal_execution import assess_node

from tests.behaviors.scout_find_jobs.test_story_bank_run_assess import (  # noqa: F401 - fx and binding are fixtures
    _Binding,
    _config,
    _context,
    _find_jobs_config,
    _pinned,
    _run_id,
    _seal_run_input,
    binding,
    fx,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig

_CAP = 2


def _posting(number: int, company: str, title: str, *, day: int, location: str = "Denver, CO") -> PostingRow:
    url = f"https://boards.greenhouse.io/{company.lower()}/jobs/{number}"
    text = f"{company} is hiring a {title}. Requirements: 5+ years of Python in production. ({number})"
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token=company.lower(),
        company=company, title=title, location=location,
        published_at=f"2026-09-{day:02d}T00:00:00Z", content_sha256=digest_imported_bytes(text.encode("utf-8")),
        source_kind=SourceKind.EXA, query_key="software-engineer", text=text,
    )


# The run's batch. Acme lists the same job twice (the older one is the copy); with a cap of two, the
# newest posting of the two newest companies is assessed and Cobalt's is left out by the cap.
_ASSESSED_ACME = _posting(1, "Acme", "Software Engineer", day=28)
_COPY_ACME = _posting(2, "Acme", "Software Engineer", day=21)
_ASSESSED_BOREALIS = _posting(3, "Borealis", "Software Engineer", day=27)
_OVER_CAP = _posting(4, "Cobalt", "Data Engineer", day=24)
_ABROAD = _posting(5, "Lumen", "Software Engineer", day=26, location="London, United Kingdom")
_OTHER_ROLE = _posting(6, "Nimbus", "Office Manager", day=26)
_BATCH = [_ASSESSED_ACME, _COPY_ACME, _ASSESSED_BOREALIS, _OVER_CAP, _ABROAD, _OTHER_ROLE]
_ELIGIBLE = [_ASSESSED_ACME, _COPY_ACME, _ASSESSED_BOREALIS, _OVER_CAP]


def _run_config():
    return replace(_find_jobs_config(), countries=("US",))


def _selected() -> list[PostingRow]:
    """What acquire selects from the batch's eligible rows under the cap (its own helper)."""

    cap, per_company = selection_limits(_CAP)
    return list(select_for_assessment(_ELIGIBLE, cap=cap, per_company=per_company).selected)


def _assess(fx: ProfileFixtureGig, run_id: str, *, seal: bool):
    """The assess node of a launched run over ``_BATCH``: sealed input in the workpad, ``target`` the project folder."""

    outputs = fx.created.workpad / "runs" / run_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "acquire.json").write_text(json.dumps({"rows": [{"posting": item.to_json(), "outcome": "new"} for item in _BATCH]}))
    if seal:
        _seal_run_input(fx, run_id, profile_id=None, config=_run_config())
    selected = _selected()
    assess_input = AssessInput(
        acquire_batch_ref=str(outputs / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=tuple(SelectedPosting(item.normalized_url, item.url, item.content_sha256, True) for item in selected),
        selection_cap=_CAP,
        selection_reasons=tuple(SelectionReason(item.normalized_url, SelectionReasonCode.NEW) for item in selected),
        pinned_resume=_pinned(fx),
        target=str(fx.target),
        model_target=ModelTarget.OLLAMA_LOCAL,
        answer_association_version="scout-answer-association:1",
    )
    return assess_node(_context(fx, run_id, "assess"), assess_input, home_root=fx.home_root, target=fx.target, config=_config(fx.home_root))


def _reasons(output) -> dict[str, str]:
    return {row.posting.normalized_url: row.reason.value for row in output.not_assessed}


def _assessed(output) -> list[str]:
    return [item.posting.normalized_url for item in output.assessments]


def test_the_fixture_is_a_launched_run(fx: ProfileFixtureGig) -> None:
    # The sealed input is in the workpad and the target folder has no run folder: what a launched run has.
    _seal_run_input(fx, _run_id(70), profile_id=None, config=_run_config())
    assert (fx.created.workpad / "runs" / _run_id(70) / "sealed" / "find-jobs-run-input.json").is_file()
    assert not (fx.target / "runs").exists()
    assert {item.normalized_url for item in _selected()} == {_ASSESSED_ACME.normalized_url, _ASSESSED_BOREALIS.normalized_url}


def test_a_launched_run_labels_each_row_it_did_not_assess(fx: ProfileFixtureGig, binding: _Binding) -> None:
    output = _assess(fx, _run_id(71), seal=True)

    assert _reasons(output) == {
        _COPY_ACME.normalized_url: "duplicate",
        _OVER_CAP.normalized_url: "over_cap",
        _ABROAD.normalized_url: "location_mismatch",
    }
    assert sorted(_assessed(output)) == sorted([_ASSESSED_ACME.normalized_url, _ASSESSED_BOREALIS.normalized_url])
    assert not set(_assessed(output)) & set(_reasons(output)), "an assessed row has no reason"
    # A title that is none of the run's roles is not a row of the run: no reason, no assessment.
    listed = {row.posting.normalized_url for row in output.candidate_rows}
    assert _OTHER_ROLE.normalized_url not in listed and len(listed) == len(_BATCH) - 1
    # The labels are acquire's own: the same helper over the same rows.
    cap, per_company = selection_limits(_CAP)
    dropped = select_for_assessment(_ELIGIBLE, cap=cap, per_company=per_company).dropped
    assert {url: ("duplicate" if reason == "duplicate" else "over_cap") for url, reason in dropped.items()} == {
        url: reason for url, reason in _reasons(output).items() if reason != "location_mismatch"
    }


def test_the_labels_do_not_change_what_the_run_assesses(fx: ProfileFixtureGig, binding: _Binding) -> None:
    # A run with nothing to label from: only its selected postings are rows. Every launched run was this before.
    unlabelled = _assess(fx, _run_id(72), seal=False)
    calls = len(binding.port.prompts)
    assert _reasons(unlabelled) == {} and calls == _CAP

    labelled = _assess(fx, _run_id(73), seal=True)
    assert _assessed(labelled) == _assessed(unlabelled), "the same postings, in the same order"
    assert [item.verdict for item in labelled.assessments] == [item.verdict for item in unlabelled.assessments]
    assert len(binding.port.prompts) == 2 * calls, "no posting the run did not select reaches the model"
    assert tuple(labelled.selected_postings) == tuple(unlabelled.selected_postings)
    assert len(_reasons(labelled)) == 3


def test_the_run_reads_serve_the_reason_of_each_row(fx: ProfileFixtureGig, binding: _Binding) -> None:
    output = _assess(fx, _run_id(74), seal=True)
    kept = [item for item in _BATCH if item is not _OTHER_ROLE]
    acquire = SimpleNamespace(
        rows=tuple(PostingRowResult(item, RowOutcome.NEW) for item in kept), carried_forward_assessments=(), rank_scores=()
    )
    view = run_reads.RunView(SimpleNamespace(run_id=_run_id(74), acquire_output=acquire, assess_output=output), scores={})

    def reason(posting: PostingRow) -> str | None:
        rows = run_reads.posting_rows(view, posting.normalized_url)
        assert rows is not None
        return run_reads.posting_detail(_run_id(74), rows)["not_assessed_reason"]  # type: ignore[return-value]

    assert {item.company: reason(item) for item in kept if item is not _COPY_ACME} == {
        "Acme": None, "Borealis": None, "Cobalt": "over_cap", "Lumen": "location_mismatch",
    }
    assert reason(_COPY_ACME) == "duplicate"
    # The grid's order puts the assessed rows first; each not-assessed row carries its reason on its own page.
    assert [row.posting.normalized_url for row in view.rows[:2]] == [_ASSESSED_ACME.normalized_url, _ASSESSED_BOREALIS.normalized_url]
    on_pages = {url: view.not_assessed[url].reason.value for url in (row.posting.normalized_url for row in view.rows[2:])}
    assert on_pages == _reasons(output)
