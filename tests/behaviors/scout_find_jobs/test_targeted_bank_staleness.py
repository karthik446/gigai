"""0110-041: ``story_bank_changed`` is targeted.

Before (0110-034b / 0110-039): answering ONE story-bank question flagged
every stored assessment of the profile that had left ANY question open, and
"Assess all" then re-assessed all of them (a few hundred model calls on one
click).

Now an assessment is stale for the bank only when a bank entry added or
edited since its basis answers one of ITS OWN open questions (the same id,
or ``story_bank.near_match``, the model-free match behind
``bank_suggestions``), or when it cites a bank answer that was edited,
deleted (as before). The entries that matched are served
(``basis_stale_bank``) so the reason line can name the question. A run's
unchanged skip (``proposal_execution._basis_stale``) applies the same rule
through the same function: the twin assertions here run both over the same
records.

Synthetic gig and fake model. The 300 stored assessments are copies of one
real stored assessment, each with its own posting identity and its own open
question, written into the store as files. No read here calls a model.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.scout import proposal_execution, quick_assess, story_bank
from gigai.scout.assessment_basis import BasisCheck
from gigai.scout.find_jobs import assess_all
from gigai.scout.find_jobs.api.assess_all import _quick_verdicts, stale_stored
from gigai.scout.find_jobs.assess_contracts import AssessResponse
from gigai.scout.find_jobs.contracts import StoryBankStamp
from gigai.scout.find_jobs.job_state import JobStateSources
from gigai.scout.profile_records import selected_profile
from gigai.scout.question_ids import normalize_question_id

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

from .test_assess_work_mode import _model_that_also_applies_the_work_mode, _with_mode, _write_find_jobs
from .test_stale_assessments import _assess
from .test_story_bank_run_assess import _RESUME, _Binding

COUNT = 300
EXACT = 3  # this one's question gets an answer under the same id
NEAR = 7  # this one's question is answered by an entry worded differently (cloud:gcp)
_NEAR_ID = normalize_question_id("tooling:google_cloud_platform")
_NEAR_QUESTION = "Do you have hands-on experience with Google Cloud Platform?"
SEEN = 9  # this one's question nearly matches an entry the bank ALREADY held when it was assessed (cloud:aws)
_SEEN_ID = normalize_question_id("tooling:amazon_web_services")
_SEEN_QUESTION = "Do you have hands-on experience with Amazon Web Services?"


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _Binding:
    fake = _Binding(_model_that_also_applies_the_work_mode)
    monkeypatch.setattr(quick_assess, "_resolve_binding", lambda config, model_target, *, home_root: fake)
    return fake


def _question(number: int) -> tuple[str, str]:
    if number == NEAR:
        return _NEAR_ID, _NEAR_QUESTION
    if number == SEEN:
        return _SEEN_ID, _SEEN_QUESTION
    return normalize_question_id(f"skill:alpha{number}"), f"Do you have alpha{number} experience?"


def _save(fx: ProfileFixtureGig, profile_id: str, question_id: str, answer: str, question: str) -> None:
    # 0.1.10.7 C: answers are the user's; ``profile_id`` only says whose assessments the test is about.
    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=question_id, answer=answer, question=question)


def _edit(fx: ProfileFixtureGig, profile_id: str, question_id: str, answer: str) -> None:
    entry = next(item for item in story_bank.read_bank(home_root=fx.home_root, target=fx.target) if item.question_id == question_id)
    story_bank.edit_answer(
        home_root=fx.home_root, target=fx.target, question_id=question_id,
        answer=answer, expected_revision=entry.revision,
    )


def _stamp_now(fx: ProfileFixtureGig, profile_id: str) -> dict[str, object]:
    bank = story_bank.assess_bank(home_root=fx.home_root, target=fx.target, profile_id=profile_id)
    return StoryBankStamp(profile_id, bank.digest, dict(bank.marks)).to_json()


def _write_copy(fx: ProfileFixtureGig, template: dict[str, object], name: str, change) -> AssessResponse:
    """One more stored assessment: ``template`` for another posting, changed by ``change(stored)``."""

    stored = copy.deepcopy(template)
    text_digest = "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()
    job = stored["job"]
    assert isinstance(job, dict)
    job["job_identity"], job["text_sha256"], job["company"] = f"text:{text_digest}", text_digest, name
    change(stored)
    profile_id = stored["resume"]["profile_id"]  # type: ignore[index]
    path = quick_assess.quick_assess_path(fx.home_root, fx.target, profile_id, job["job_identity"])
    stored["stored_path"] = str(path)
    path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    item = quick_assess._read_stored(path)
    assert item is not None, name
    return item


def _asks(number: int):
    def change(stored: dict[str, object]) -> None:
        question_id, question = _question(number)
        result = stored["result"]
        assert isinstance(result, dict)
        result["questions"] = [question]
        result["structured_questions"] = [{"question_id": question_id, "question": question, "requirement": f"Requirement {number}"}]

    return change


def _cites(question_id: str, stamp: dict[str, object]):
    def change(stored: dict[str, object]) -> None:
        result = stored["result"]
        assert isinstance(result, dict)
        result["questions"], result["structured_questions"] = [], []
        result["verdict"] = "matched_above_threshold"
        result["matrix"] = [{"class": "hard", "requirement": "Domain experience", "resume_evidence": [f"Story bank {question_id}: four years"], "status": "met"}]
        stored["story_bank"] = stamp

    return change


@pytest.fixture
def page(fx: ProfileFixtureGig, model: _Binding) -> SimpleNamespace:
    """One profile, a bank with two entries, and 300 stored assessments that each ask their own question.

    ``industry:fintech`` answers none of the 300 questions. ``cloud:aws``
    nearly matches number ``SEEN``'s: an answer its assessment was offered
    and still asked about (it stays a suggestion on its page).
    """

    _write_find_jobs(fx, _with_mode(None))
    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None
    _save(fx, profile.profile_id, "industry:fintech", "Four years building payment systems.", "Do you have fintech experience?")
    _save(fx, profile.profile_id, "cloud:aws", "Three years on AWS.", "Do you have AWS experience?")
    first = _assess(fx)  # a real stored assessment, made with the bank as it is now
    template = json.loads(Path(first.stored_path).read_text(encoding="utf-8"))
    assert [entry["question_id"] for entry in template["story_bank"]["entries"]] == ["cloud:aws", "industry:fintech"]
    seen = story_bank.near_match(
        story_bank.read_bank(home_root=fx.home_root, target=fx.target), question_id=_SEEN_ID, question=_SEEN_QUESTION
    )
    assert seen is not None and seen.bank_question_id == "cloud:aws"
    Path(first.stored_path).unlink()
    items = [_write_copy(fx, template, f"Company {number}", _asks(number)) for number in range(COUNT)]
    assert len({item.result.structured_questions[0].question_id for item in items}) == COUNT
    assert len({item.job.job_identity for item in items}) == COUNT
    return SimpleNamespace(profile_id=profile.profile_id, items=items, template=template, calls=len(model.port.prompts))


def _check(fx: ProfileFixtureGig) -> BasisCheck:
    return BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved)


def _stale(fx: ProfileFixtureGig, items) -> dict[int, dict[str, object]]:
    """``{index: served keys}`` for the stale ones, one request over the whole page."""

    check = _check(fx)
    served = [check.served(item) for item in items]
    return {index: keys for index, keys in enumerate(served) if keys["basis_stale"]}


def _run_skip_says_stale(fx: ProfileFixtureGig, items, profile_id: str) -> set[int]:
    """What a run's unchanged skip decides for the same recorded basis (``_basis_stale``)."""

    bank = story_bank.assess_bank(home_root=fx.home_root, target=fx.target, profile_id=profile_id)
    current = _check(fx).current(profile_id)
    assert current is not None
    found: set[int] = set()
    for index, item in enumerate(items):
        prior = SimpleNamespace(
            prompt_version=item.prompt_version, constraints_digest=item.constraints_digest,
            bank_marks=None if item.story_bank is None else item.story_bank.entries, result=item.result,
        )
        if proposal_execution._basis_stale(prior, bank=bank, constraints=current.constraints_digest):
            found.add(index)
    return found


def _store_bytes(page: SimpleNamespace) -> list[bytes]:
    return [Path(item.stored_path).read_bytes() for item in page.items]


def test_one_answer_flags_only_the_assessments_that_asked_it(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace) -> None:
    before = _store_bytes(page)
    assert _stale(fx, page.items) == {}, "300 open questions, a bank they were all made with: nothing is stale"

    # An edit to an entry none of them asked about flags none (it flagged all 300 before 0110-041).
    _edit(fx, page.profile_id, "industry:fintech", "Five years building payment systems.")
    assert _stale(fx, page.items) == {}
    assert _run_skip_says_stale(fx, page.items, page.profile_id) == set()

    # ONE answer under the id one of them asked, and one that answers another's question in other words.
    exact_id, exact_question = _question(EXACT)
    _save(fx, page.profile_id, exact_id, "Yes, three years.", exact_question)
    _save(fx, page.profile_id, "cloud:gcp", "Two years running services on GCP.", "Do you have GCP experience?")

    # (Number SEEN's question nearly matches cloud:aws, which its assessment was made with: writing OTHER
    # entries of the same profile does not make that one new to it.)
    stale = _stale(fx, page.items)
    assert sorted(stale) == [EXACT, NEAR], "exactly the two that asked"
    assert len(page.items) - len(stale) == 298
    assert stale[EXACT] == {
        "basis_stale": True,
        "basis_stale_reason": "story_bank_changed",
        "basis_stale_bank": [{"match": "exact", "bank_question_id": exact_id, "bank_question": exact_question, "question_id": exact_id, "question": exact_question}],
    }
    assert stale[NEAR] == {
        "basis_stale": True,
        "basis_stale_reason": "story_bank_changed",
        "basis_stale_bank": [
            {"match": "near", "bank_question_id": "cloud:gcp", "bank_question": "Do you have GCP experience?", "question_id": _NEAR_ID, "question": _NEAR_QUESTION}
        ],
    }
    # The near match is the one behind bank_suggestions, not a second rule.
    bank = story_bank.read_bank(home_root=fx.home_root, target=fx.target)
    suggestion = story_bank.near_match(bank, question_id=_NEAR_ID, question=_NEAR_QUESTION)
    assert suggestion is not None and suggestion.bank_question_id == "cloud:gcp"
    # No answer text is served with the reason.
    assert "Two years" not in json.dumps(stale) and "three years" not in json.dumps(stale)

    # The marker on the job's state is the same two.
    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.resolved, events={})
    states = [sources.state_for(item.job.job_identity, profile_id=page.profile_id).to_json().get("assessment_stale") for item in page.items]
    assert [index for index, state in enumerate(states) if state is not None] == [EXACT, NEAR]
    assert states[EXACT] == {"reason": "story_bank_changed"}

    # Twin: a run's unchanged skip decides the same for the same 300 records.
    assert _run_skip_says_stale(fx, page.items, page.profile_id) == {EXACT, NEAR}

    assert len(model.port.prompts) == page.calls, "no read calls a model"
    assert _store_bytes(page) == before, "and none writes the store"


def test_assess_all_plans_the_stale_ones_and_no_more(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace) -> None:
    exact_id, exact_question = _question(EXACT)
    _save(fx, page.profile_id, exact_id, "Yes, three years.", exact_question)
    _save(fx, page.profile_id, "cloud:gcp", "Two years running services on GCP.", "Do you have GCP experience?")

    # What POST /assess-all {} computes: the store once, the stale ones, the queue, the plan.
    latest: dict = {}
    quick = _quick_verdicts(fx.home_root, fx.target, page.profile_id, latest)
    assert len(latest) == COUNT
    view = SimpleNamespace(evidence=SimpleNamespace(started_at=None), assessments={}, carried_forward={})
    stale = stale_stored(latest, view, home_root=fx.home_root, target=fx.target, resolved=fx.resolved)
    rows = [
        SimpleNamespace(
            posting=SimpleNamespace(normalized_url=item.job.job_identity, url=item.job.job_identity, title=f"Engineer {index}", company=f"Company {index}", location="Remote"),
            outcome="new",
        )
        for index, item in enumerate(page.items)
    ]
    queue = assess_all.build_queue(rows, run_assessed=(), not_assessed_reasons={}, already_assessed=quick, stale=stale)
    plan = assess_all.plan(count=len(queue), model_target="codex_cli", concurrency=4, stale_count=sum(1 for item in queue if item.normalized_url in stale))

    expected = {page.items[EXACT].job.job_identity, page.items[NEAR].job.job_identity}
    assert stale == expected
    assert {item.normalized_url for item in queue} == expected
    assert (plan["count"], plan["new_count"], plan["stale_count"]) == (2, 0, 2), "one click: two calls, not 300"
    assert len(_stale(fx, page.items)) == plan["stale_count"]
    assert len(model.port.prompts) == page.calls


def test_a_page_of_jobs_reads_the_bank_once(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries changed since the basis x open questions, in memory: one bank read a request, none per row."""

    exact_id, exact_question = _question(EXACT)
    _save(fx, page.profile_id, exact_id, "Yes, three years.", exact_question)
    reads = {"assess_bank": 0, "read_bank": 0}
    real_assess_bank, real_read_bank = story_bank.assess_bank, story_bank.read_bank

    def assess_bank(*args, **kwargs):
        reads["assess_bank"] += 1
        return real_assess_bank(*args, **kwargs)

    def read_bank(*args, **kwargs):
        reads["read_bank"] += 1
        return real_read_bank(*args, **kwargs)

    def no_listing(*_args, **_kwargs):
        raise AssertionError("the whole store was read for a row")

    monkeypatch.setattr(story_bank, "assess_bank", assess_bank)
    monkeypatch.setattr(story_bank, "read_bank", read_bank)
    monkeypatch.setattr(quick_assess, "list_quick_assessments", no_listing)

    check = _check(fx)
    stale = [index for index, item in enumerate(page.items) if check.reason(item) is not None]
    served = [check.served(item) for item in page.items]  # the route's second pass over the same request

    assert stale == [EXACT] and sum(1 for keys in served if keys["basis_stale"]) == 1
    assert reads == {"assess_bank": 1, "read_bank": 1}, "600 checks, one bank read"

    # The next request on an unchanged workpad reads no bank at all (0110-039's kept read).
    assert [index for index, item in enumerate(page.items) if _check(fx).reason(item) is not None] == [EXACT]
    assert reads == {"assess_bank": 1, "read_bank": 1}
    assert len(model.port.prompts) == page.calls


def test_an_assessment_that_cited_an_entry_is_stale_when_it_is_edited_or_deleted(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace) -> None:
    default = page.profile_id
    _save(fx, default, "industry:logistics", "Three years of routing software.", "Do you have logistics experience?")
    stamp = _stamp_now(fx, default)
    assert {entry["question_id"] for entry in stamp["entries"]} == {"cloud:aws", "industry:fintech", "industry:logistics"}  # type: ignore[index, union-attr]
    cited = {name: _write_copy(fx, page.template, f"Cites {name}", _cites(f"industry:{name}", stamp)) for name in ("fintech", "logistics")}
    asks = _write_copy(fx, page.template, "Asks something else", lambda stored: (_asks(11)(stored), stored.__setitem__("story_bank", stamp)))
    items = [cited["fintech"], cited["logistics"], asks]

    def both() -> dict[int, list[dict[str, object]]]:
        stale = _stale(fx, items)
        assert _run_skip_says_stale(fx, items, default) == set(stale), "the run's skip agrees"
        return {index: keys["basis_stale_bank"] for index, keys in stale.items()}  # type: ignore[misc]

    assert both() == {}

    _edit(fx, default, "industry:fintech", "Five years building payment systems.")
    assert both() == {0: [{"match": "cited", "bank_question_id": "industry:fintech", "bank_question": "Do you have fintech experience?"}]}

    entry = next(item for item in story_bank.read_bank(home_root=fx.home_root, target=fx.target) if item.question_id == "industry:logistics")
    story_bank.delete_answer(home_root=fx.home_root, target=fx.target, question_id="industry:logistics", expected_revision=entry.revision)
    found = both()
    assert sorted(found) == [0, 1], "the one that only asked an unrelated question stays current through both"
    assert found[1] == [{"match": "cited", "bank_question_id": "industry:logistics"}], "a deleted entry has no question to name"
    assert len(model.port.prompts) == page.calls


def test_a_stamp_as_it_was_recorded_before_reads_as_it_was_and_gets_the_targeted_rule(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace) -> None:
    """Nothing is added to the stored record: the stamp is ``{profile_id, digest, entries[{question_id, mark}]}``, as 0110-039 wrote it."""

    stamp = page.template["story_bank"]
    assert set(stamp) == {"profile_id", "digest", "entries"} and all(set(entry) == {"question_id", "mark"} for entry in stamp["entries"])
    assert StoryBankStamp.from_json(stamp).to_json() == stamp
    item = page.items[EXACT]
    stored = json.loads(Path(item.stored_path).read_text(encoding="utf-8"))
    assert AssessResponse.from_json(stored).to_json() == stored, "read and written byte for byte"

    exact_id, exact_question = _question(EXACT)
    _save(fx, page.profile_id, exact_id, "Yes, three years.", exact_question)
    assert _check(fx).reason(item) == "story_bank_changed"
    assert _check(fx).reason(page.items[EXACT + 1]) is None

    # A record with a basis but no stamp at all (a pasted resume with no profile to read a bank for) saw an
    # empty bank: every entry is new to it, and still only the one that answers its question counts.
    def no_stamp(number: int):
        def change(stored: dict[str, object]) -> None:
            _asks(number)(stored)
            stored.pop("story_bank")

        return change

    asked_it = _write_copy(fx, page.template, "No stamp, asked it", no_stamp(EXACT))
    asked_other = _write_copy(fx, page.template, "No stamp, asked another", no_stamp(12))
    assert asked_it.story_bank is None and _check(fx).reason(asked_it) == "story_bank_changed"
    assert _check(fx).reason(asked_other) is None
    assert len(model.port.prompts) == page.calls


def test_writing_one_entry_leaves_the_other_entries_marks_as_they_were(fx: ProfileFixtureGig, model: _Binding, page: SimpleNamespace) -> None:
    """Answers share one record, whose revision id changes on every write: the mark is per entry."""

    def bank() -> story_bank.AssessBank:
        return story_bank.assess_bank(home_root=fx.home_root, target=fx.target, profile_id=page.profile_id)

    before = bank()
    _edit(fx, page.profile_id, "industry:fintech", "Five years building payment systems.")
    _save(fx, page.profile_id, "cloud:gcp", "Two years running services on GCP.", "Do you have GCP experience?")
    after = bank()

    assert after.marks["cloud:aws"] == before.marks["cloud:aws"], "not written: the same mark"
    assert after.marks["industry:fintech"] != before.marks["industry:fintech"]
    assert [entry.question_id for entry in story_bank.changed_entries(before.marks, after)] == ["cloud:gcp", "industry:fintech"]
    # Marks are ids and counters: no answer text and nothing derived from it.
    assert all(len(mark) == 16 and "GCP" not in mark for mark in after.marks.values())
