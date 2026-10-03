"""0110-039: a stored assessment made under older settings says so, and is re-assessed on a click.

The bug: after 0110-035/038 a find-jobs run refreshes its own verdicts (it
seals a prompt version, a constraints digest and a story bank stamp, and does
not carry an assessment forward when they changed). A STORED quick / job-page
/ "Assess all new" assessment recorded none of that, so a remote-only
profile kept the "needs your answers" (or "matched") it got from the prompt
that knew no work mode, and "Assess all new" skipped it as already assessed.

Now the stored record carries the same basis a run seals, a read compares it
with what the profile would be assessed with now (``assessment_basis``), and
"Assess all new" queues the stale ones. Nothing here calls a model on a
read: every test that reads counts the fake model's prompts.

Synthetic gig, fake model that answers only from the prompt text (0110-038's).
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from gigai.scout import assessment_basis, proposal_execution, quick_assess, story_bank
from gigai.scout.assessment_basis import BasisCheck
from gigai.scout.assessment_core import constraints_digest
from gigai.scout.find_jobs import assess_all
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.find_jobs.contracts import WorkModePreference
from gigai.scout.find_jobs.job_state import JobStateSources
from gigai.scout.profile_records import ProfileSearchSettings, create_profile, selected_profile

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

from .test_assess_work_mode import _model_that_also_applies_the_work_mode, _with_mode, _write_find_jobs
from .test_story_bank_run_assess import _RESUME, _Binding, _config

BASIS_KEYS = ("prompt_version", "constraints_digest", "story_bank")
_HOUSTON = "Bayou is hiring a Software Engineer. Hybrid: 2 days a week in our Houston office. Requirements: 5+ years of Python in production."
_REMOTE = "Opal is hiring a Software Engineer. Remote - United States. Requirements: 5+ years of Python in production."


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _Binding:
    fake = _Binding(_model_that_also_applies_the_work_mode)
    monkeypatch.setattr(quick_assess, "_resolve_binding", lambda config, model_target, *, home_root: fake)
    return fake


def _calls(model: _Binding) -> int:
    return len(model.port.prompts)


def _assess(fx: ProfileFixtureGig, text: str = _HOUSTON, *, profile_id: str | None = None, company: str = "Bayou") -> AssessResponse:
    resume = AssessResumeInput(profile_id=profile_id) if profile_id is not None else AssessResumeInput()
    return quick_assess.run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=text, title="Software Engineer", company=company), resume=resume),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root),
    )


def _as_stored_before_039(item: AssessResponse) -> AssessResponse:
    """Rewrite the stored file as a v4-era one: the same answer, no recorded basis."""

    path = Path(item.stored_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    for key in BASIS_KEYS:
        stored.pop(key, None)
    path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    old = quick_assess._read_stored(path)
    assert old is not None and old.prompt_version is None and old.constraints_digest is None and old.story_bank is None
    return old


def _reason(fx: ProfileFixtureGig, item: AssessResponse) -> str | None:
    stored = quick_assess._read_stored(Path(item.stored_path))
    assert stored is not None
    return BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).reason(stored)


def _state(fx: ProfileFixtureGig, item: AssessResponse) -> dict[str, object]:
    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.resolved, events={})
    return sources.state_for(item.job.job_identity, profile_id=item.resume.profile_id).to_json()


def _remote_and_visa(location: str = "Austin, TX"):
    return replace(_with_mode(WorkModePreference.REMOTE, location=location), visa_sponsorship_required=True)


# --- the record ---------------------------------------------------------------------------------------


def test_a_stored_assessment_records_the_basis_a_run_seals(fx: ProfileFixtureGig, model: _Binding) -> None:
    _write_find_jobs(fx, _remote_and_visa())

    item = _assess(fx)

    assert item.prompt_version == "assess-prompt-v5"
    assert item.constraints_digest == constraints_digest(
        visa_sponsorship_required=True, countries=("US",), location="Austin, TX", work_mode="remote"
    )
    assert item.story_bank is not None and item.story_bank.profile_id == item.resume.profile_id
    stored = json.loads(Path(item.stored_path).read_text(encoding="utf-8"))
    assert set(BASIS_KEYS) <= set(stored)
    # Digests and ids only: no constraint and no answer text is in the basis.
    basis_text = json.dumps({key: stored[key] for key in BASIS_KEYS})
    assert "remote" not in basis_text and "Austin" not in basis_text and "US" not in basis_text
    assert AssessResponse.from_json(stored).to_json() == stored


def test_a_profile_with_no_work_mode_records_the_v4_name(fx: ProfileFixtureGig, model: _Binding) -> None:
    _write_find_jobs(fx, _with_mode(None))

    item = _assess(fx)

    assert item.prompt_version == "assess-prompt-v4"
    assert item.constraints_digest == constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX")


def test_a_file_written_before_the_basis_reads_and_writes_as_it_was(fx: ProfileFixtureGig, model: _Binding) -> None:
    _write_find_jobs(fx, _with_mode(None))
    old = _as_stored_before_039(_assess(fx))

    stored = json.loads(Path(old.stored_path).read_text(encoding="utf-8"))
    assert not set(BASIS_KEYS) & set(stored)
    assert old.to_json() == stored


# --- stale, and why ------------------------------------------------------------------------------------


def test_an_old_assessment_of_a_remote_only_visa_profile_is_stale_and_says_why(fx: ProfileFixtureGig, model: _Binding) -> None:
    """The reported shape: assessed before the prompt knew the work mode, still shown as it was."""

    _write_find_jobs(fx, _with_mode(None))
    old = _as_stored_before_039(_assess(fx))
    assert old.result.verdict.value == "pending_user_answers"  # the old prompt asked about the Houston office
    made = _calls(model)

    _write_find_jobs(fx, _remote_and_visa())

    assert _reason(fx, old) == "older_prompt"
    state = _state(fx, old)
    assert state["state"] == "needs_answers", "the verdict still reads"
    assert state["assessment_stale"] == {"reason": "older_prompt"}
    check = BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved)
    assert check.served(old) == {"basis_stale": True, "basis_stale_reason": "older_prompt"}
    assert _calls(model) == made, "a read never calls a model"
    assert quick_assess._read_stored(Path(old.stored_path)).to_json() == old.to_json(), "and writes nothing"


def test_a_plain_profiles_old_assessment_is_not_stale(fx: ProfileFixtureGig, model: _Binding) -> None:
    """No work mode, and the sponsorship need and countries the record says: nothing the old prompt missed."""

    _write_find_jobs(fx, _with_mode(None))
    old = _as_stored_before_039(_assess(fx))

    assert _reason(fx, old) is None
    assert "assessment_stale" not in _state(fx, old)
    assert BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).served(old) == {"basis_stale": False}
    # "any" is no work mode either.
    _write_find_jobs(fx, _with_mode(WorkModePreference.ANY))
    assert _reason(fx, old) is None


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda config: replace(config, visa_sponsorship_required=True), "settings_changed"),
        (lambda config: replace(config, countries=("US", "CA")), "settings_changed"),
        (lambda config: replace(config, work_mode=WorkModePreference.HYBRID), "older_prompt"),
        # The location an old record used was not stored, so it is taken as unchanged.
        (lambda config: replace(config, location="Denver, CO"), None),
        (lambda config: replace(config, roles=("staff engineer",), merged_queries=("staff engineer",)), None),
    ],
)
def test_an_old_assessment_is_stale_only_for_what_its_prompt_missed_or_its_record_contradicts(
    fx: ProfileFixtureGig, model: _Binding, change, expected: str | None
) -> None:
    plain = _with_mode(None)
    _write_find_jobs(fx, plain)
    old = _as_stored_before_039(_assess(fx))

    _write_find_jobs(fx, change(plain))

    assert _reason(fx, old) == expected


def test_changing_the_work_mode_or_the_countries_makes_a_current_assessment_stale(fx: ProfileFixtureGig, model: _Binding) -> None:
    remote = _with_mode(WorkModePreference.REMOTE)
    _write_find_jobs(fx, remote)
    item = _assess(fx)
    assert _reason(fx, item) is None and "assessment_stale" not in _state(fx, item)
    made = _calls(model)

    for changed in (
        replace(remote, work_mode=WorkModePreference.HYBRID, remote=False),
        replace(remote, work_mode=None, remote=False),
        replace(remote, countries=("US", "CA")),
        replace(remote, location="Denver, CO"),
        replace(remote, visa_sponsorship_required=True),
    ):
        _write_find_jobs(fx, changed)
        assert _reason(fx, item) == "settings_changed", changed
        assert _state(fx, item)["assessment_stale"] == {"reason": "settings_changed"}

    # Target titles are context only (rule 6): a title edit voids nothing.
    _write_find_jobs(fx, replace(remote, roles=("staff engineer",), merged_queries=("staff engineer",)))
    assert _reason(fx, item) is None
    _write_find_jobs(fx, remote)
    assert _reason(fx, item) is None
    assert _calls(model) == made


def test_an_assessment_made_with_an_older_prompt_version_is_stale(fx: ProfileFixtureGig, model: _Binding) -> None:
    _write_find_jobs(fx, _with_mode(None))
    item = _assess(fx)
    path = Path(item.stored_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["prompt_version"] = "assess-prompt-v3"
    path.write_text(json.dumps(stored), encoding="utf-8")

    assert _reason(fx, item) == "older_prompt"


def test_each_profile_is_judged_by_its_own_settings(fx: ProfileFixtureGig, model: _Binding) -> None:
    remote = _with_mode(WorkModePreference.REMOTE)
    _write_find_jobs(fx, remote)
    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    other = create_profile(
        fx.resolved, label="other person", titles=("staff backend engineer",), titles_to_avoid=(), queries=("staff backend engineer",),
        resume_ref=default.resume_ref,
        search_settings=ProfileSearchSettings(location="Houston, TX", work_mode="hybrid", countries=("US",), max_age_days=None),
    )
    mine = _assess(fx, profile_id=default.profile_id)
    theirs = _assess(fx, profile_id=other.profile_id)
    assert mine.constraints_digest != theirs.constraints_digest
    assert _reason(fx, mine) is None and _reason(fx, theirs) is None

    # The default profile's settings change: only its own assessment is stale.
    _write_find_jobs(fx, replace(remote, countries=("US", "CA")))

    assert _reason(fx, mine) == "settings_changed"
    assert _reason(fx, theirs) is None
    check = BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved)
    assert check.current(default.profile_id).work_mode == "remote" and check.current(other.profile_id).work_mode == "hybrid"
    assert check.current("profile_00000000-0000-4000-8000-000000000000") is None, "a profile that is gone has no basis: nothing is called stale"


def test_the_story_bank_rule_is_the_runs_own(fx: ProfileFixtureGig, model: _Binding) -> None:
    """Stale when the bank now answers a question IT left open (0110-041: targeted); the twin of ``_basis_stale``."""

    _write_find_jobs(fx, _with_mode(None))
    item = _assess(fx)  # asks about the Houston office
    settled = _assess(fx, _REMOTE, company="Opal")  # no open question, cites nothing
    assert [question.question_id for question in item.result.structured_questions] == ["location:houston"]
    assert settled.result.verdict.value == "matched_above_threshold"
    assert _reason(fx, item) is None

    # 0110-041: an answer to a question it did NOT ask leaves it current (039 flagged it here).
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target,
        question_id="cloud:gcp", answer="Two years running services on GCP.", question="Do you have GCP experience?",
    )
    assert _reason(fx, item) is None and "assessment_stale" not in _state(fx, item)

    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target,
        question_id="location:houston", answer="I live in Houston and can be in the office.", question="Can you work from the Houston office?",
    )

    assert _reason(fx, item) == "story_bank_changed"
    assert _state(fx, item)["assessment_stale"] == {"reason": "story_bank_changed"}
    assert _reason(fx, settled) is None, "no open question and nothing cited: the bank cannot change it"

    # The same answer as a run's unchanged skip gives for the same recorded basis.
    bank = story_bank.assess_bank(home_root=fx.home_root, target=fx.target, profile_id=item.resume.profile_id)
    current = BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).current(item.resume.profile_id)
    for stored in (item, settled):
        prior = SimpleNamespace(
            prompt_version=stored.prompt_version, constraints_digest=stored.constraints_digest,
            bank_marks=stored.story_bank.entries, result=stored.result,
        )
        run_says = proposal_execution._basis_stale(prior, bank=bank, constraints=current.constraints_digest)
        assert run_says == (_reason(fx, stored) is not None)

    # An old record has no bank stamp: the bank rule is not applied to it.
    assert _reason(fx, _as_stored_before_039(item)) is None


def test_a_request_that_overrides_the_settings_is_recorded_as_made(fx: ProfileFixtureGig, model: _Binding) -> None:
    """An assessment asked for with its own preferences is not what the profile would get: it reads stale."""

    from gigai.scout.find_jobs.assess_contracts import AssessPreferences

    _write_find_jobs(fx, _with_mode(None))
    item = quick_assess.run_quick_assessment(
        AssessRequest(
            job=AssessJobInput(job_text=_HOUSTON, title="Software Engineer", company="Bayou"),
            resume=AssessResumeInput(),
            preferences=AssessPreferences(location="Houston, TX"),
        ),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root),
    )

    assert item.constraints_digest == constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Houston, TX")
    assert _reason(fx, item) == "settings_changed"


# --- a page of jobs is cheap ----------------------------------------------------------------------------


def test_a_page_of_jobs_reads_the_settings_once_and_never_the_whole_store(fx: ProfileFixtureGig, model: _Binding, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_find_jobs(fx, _with_mode(None))
    items = [
        _as_stored_before_039(_assess(fx, f"{_HOUSTON} Posting number {number}.", company=f"Bayou {number}")) for number in range(6)
    ]
    _write_find_jobs(fx, _remote_and_visa())
    made = _calls(model)

    def no_listing(*_args, **_kwargs):
        raise AssertionError("the whole store was read for a row")

    monkeypatch.setattr(quick_assess, "list_quick_assessments", no_listing)
    reads: dict[str, int] = {"profiles": 0, "config": 0, "bank": 0}
    from gigai.scout import profile_records

    real_profiles, real_location, real_bank = profile_records.list_profiles, quick_assess._config_location, story_bank.assess_bank

    def counted(name: str, real):
        def call(*args, **kwargs):
            reads[name] += 1
            return real(*args, **kwargs)

        return call

    monkeypatch.setattr(profile_records, "list_profiles", counted("profiles", real_profiles))
    monkeypatch.setattr(quick_assess, "_config_location", counted("config", real_location))
    monkeypatch.setattr(story_bank, "assess_bank", counted("bank", real_bank))

    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.resolved, events={})
    sources.state_for(items[0].job.job_identity, profile_id=items[0].resume.profile_id)  # the first row pays for the page
    spawned: list[str] = []
    real_run = subprocess.run

    def counting(args, *rest, **kwargs):
        spawned.append(" ".join(str(word) for word in args))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    states = [sources.state_for(item.job.job_identity, profile_id=item.resume.profile_id).to_json() for item in items[1:]]

    assert all(state["assessment_stale"] == {"reason": "older_prompt"} for state in states)
    assert reads == {"profiles": 1, "config": 1, "bank": 0}, "settings once a request; the bank only when a record needs it"
    assert spawned == [], "the rows after the first start no subprocess for the stale check"
    assert _calls(model) == made


def test_a_request_on_an_unchanged_workpad_pays_one_cheap_lookup(fx: ProfileFixtureGig, model: _Binding, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 0110-033 budget. Reading the profiles and the bank is 80 git subprocesses; they are kept
    while the journal head, find-jobs.json, the saved preferences and the bank's overlay are unchanged."""

    _write_find_jobs(fx, _with_mode(None))
    items = [_assess(fx, f"{_HOUSTON} Posting number {number}.", company=f"Bayou {number}") for number in range(4)]
    profile_id = items[0].resume.profile_id
    assert profile_id is not None
    # 0110-041: the answer to the question these four asked (an unrelated one would flag none of them).
    assert {question.question_id for item in items for question in item.result.structured_questions} == {"location:houston"}
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target,
        question_id="location:houston", answer="I live in Houston and can be in the office.", question="Can you work from the Houston office?",
    )
    spawned: list[str] = []
    real_run = subprocess.run

    def counting(args, *rest, **kwargs):
        spawned.append(" ".join(str(word) for word in args))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)

    def a_request() -> tuple[int, list[object]]:
        spawned.clear()
        sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.resolved, events={})
        states = [sources.state_for(item.job.job_identity, profile_id=profile_id).to_json().get("assessment_stale") for item in items]
        return len(spawned), states

    first, states = a_request()
    assert states == [{"reason": "story_bank_changed"}] * 4, "every row reached the bank rule"
    assert first > 20, "the first request reads the profiles and the bank from the journal"
    second, states = a_request()
    assert states == [{"reason": "story_bank_changed"}] * 4

    with monkeypatch.context() as off:
        off.setattr(BasisCheck, "reason", lambda self, item: None)
        without_the_check, _ = a_request()
    assert second <= without_the_check + 1, (second, without_the_check, "one git rev-parse, whatever the number of rows")

    # A change is seen by the very next request: the settings file here, the journal (an answer) above.
    _write_find_jobs(fx, _with_mode(WorkModePreference.REMOTE))
    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.resolved, events={})
    assert sources.state_for(items[0].job.job_identity, profile_id=profile_id).to_json()["assessment_stale"] == {"reason": "settings_changed"}


# --- "Assess all new" ------------------------------------------------------------------------------------


def _row(url: str, outcome: str = "new", *, company: str = "") -> SimpleNamespace:
    posting = SimpleNamespace(normalized_url=url, url=url, title=f"Engineer {url}", company=company or f"Company {url}", location="Remote")
    return SimpleNamespace(posting=posting, outcome=outcome)


def test_assess_all_queues_a_stale_stored_assessment_and_skips_a_current_one() -> None:
    rows = [_row("new"), _row("stale"), _row("current"), _row("stale-unchanged", "unchanged"), _row("never-unchanged", "unchanged")]
    stored = {"stale", "current", "stale-unchanged"}

    before = assess_all.build_queue(rows, run_assessed=(), not_assessed_reasons={}, already_assessed=stored)
    assert [item.normalized_url for item in before] == ["new"], "what HEAD did: every stored assessment was skipped"

    queue = assess_all.build_queue(
        rows,
        run_assessed=(),
        not_assessed_reasons={"stale-unchanged": "unchanged", "never-unchanged": "unchanged"},
        already_assessed=stored,
        stale={"stale", "stale-unchanged"},
    )

    # An unchanged posting with a stale stored assessment is assessed again; one never assessed stays out, as before.
    assert [item.normalized_url for item in queue] == ["new", "stale", "stale-unchanged"]


def test_a_stale_near_copy_is_still_assessed_again() -> None:
    rows = [_row("a", company="Acme"), _row("b", company="Acme")]
    for row in rows:
        row.posting.title = "Software Engineer"

    queue = assess_all.build_queue(rows, run_assessed=(), not_assessed_reasons={}, already_assessed={"a", "b"}, stale={"a", "b"})

    assert [item.normalized_url for item in queue] == ["a", "b"], "both cards carry the marker; the click clears both"


def test_the_plan_counts_new_and_stale_apart() -> None:
    plan = assess_all.plan(count=7, model_target="codex_cli", concurrency=4, stale_count=3)
    assert (plan["count"], plan["new_count"], plan["stale_count"]) == (7, 4, 3)
    plain = assess_all.plan(count=7, model_target="codex_cli", concurrency=4)
    assert (plain["count"], plain["new_count"], plain["stale_count"]) == (7, 7, 0)


def test_the_job_skips_a_current_stored_assessment_and_not_a_stale_one(fx: ProfileFixtureGig, model: _Binding) -> None:
    """What the job checks just before each call (a card may have been assessed meanwhile)."""

    _write_find_jobs(fx, _with_mode(None))
    old = _as_stored_before_039(_assess(fx))
    profile_id = old.resume.profile_id
    assert profile_id is not None
    item = assess_all.QueueItem(normalized_url=old.job.job_identity, url="https://example.test/job")
    stored = assess_all.stored_for_profile(fx.home_root, fx.target, profile_id)
    current = assess_all.current_for_profile(fx.home_root, fx.target, profile_id)
    never = assess_all.QueueItem(normalized_url="text:sha256:" + "0" * 64, url="https://example.test/never")

    assert stored(item) and current(item), "a plain profile's old assessment is current: skipped"
    assert not current(never)

    _write_find_jobs(fx, _remote_and_visa())
    made = _calls(model)

    assert stored(item), "HEAD's check: the file is there, so the posting was skipped"
    assert not current(item), "now: it is stale, so the job assesses it again"
    assert _calls(model) == made

    again = _assess(fx)  # the job's own single-posting call
    assert again.prompt_version == "assess-prompt-v5" and again.result.verdict.value == "not_a_match"
    assert current(item) and _reason(fx, again) is None
    assert [entry.trigger for entry in again.history] == ["assess", "reassess"], "the earlier verdict stays in the history"


def test_the_reasons_are_a_closed_set() -> None:
    assert assessment_basis.BASIS_STALE_REASONS == ("older_prompt", "settings_changed", "story_bank_changed")


def test_a_job_that_ended_while_a_request_was_read_is_never_interrupted(tmp_path: Path) -> None:
    """A request reads the job record first and builds its summary last. The stale check made that
    window longer: a job that ends inside it read "interrupted" (a running record, no live owner)."""

    root = tmp_path / "aa_1"
    root.mkdir()
    running = {"kind": "assess_all", "record_id": "aa_1", "run_id": "run_1", "profile_id": "p1", "status": "running", "queue": []}
    read_earlier = assess_all.AssessAllRecord("aa_1", root, dict(running))

    (root / "record.json").write_text(json.dumps({**running, "status": "complete"}), encoding="utf-8")
    assert assess_all.summary(read_earlier)["status"] == "running", "this once; the next read says complete, with its counts"
    assert assess_all.summary(assess_all.AssessAllRecord("aa_1", root, {**running, "status": "complete"}))["status"] == "complete"

    (root / "record.json").write_text(json.dumps(running), encoding="utf-8")
    assert assess_all.summary(read_earlier)["status"] == "interrupted", "a job nobody runs is still interrupted"
