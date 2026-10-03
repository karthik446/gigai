"""0110-034b: the story bank feeds a find-jobs RUN's assess node, not only the job page.

A launched run's layout, as ``bindings._assess_bound`` gives it to the node:
``target`` is the project folder, the run's sealed input and its acquire
output are in the WORKPAD (``runs/<run_id>/sealed``, ``.../outputs``). Real
gig (``build_gig_with_resume``), synthetic answers only, and a fake model on
the C1 seam (``proposal_execution.resolve_model_adapter``) that follows
``assess.md``'s STORY BANK paragraph the way a real one is told to: when the
prompt offers a bank line for the fact (``- cloud:gcp | ...``) it marks the
requirement met, cites ``Story bank cloud:gcp: <answer>`` and asks nothing;
when the prompt offers no such line it asks the question, under the id it
would have picked for THIS posting's wording (``tooling:google_cloud_platform``).

0.1.10.7 C: answers and stories are the USER's (``story-bank-contract.md``), so
the per-profile cases of 0110-034 (a second profile's run never got the first
one's answers until they were shared) are gone: every profile's run reads the
same answers, and the stories that match each posting.

Covers: reuse of an answer given on posting A for a reworded requirement on
posting B, in a run; a run for another profile reuses the same answer; a
story that matches a run's posting is in that posting's prompt and one that
does not is not; a re-run after the bank changed assesses the unchanged
posting again, and does not while the bank is unchanged; what the run seals
(prompt version, the profile, the marks); the staleness rule itself;
``bank_suggestions`` on a run's ``/results`` and ``/posting`` bodies.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout import story_bank
from gigai.scout.find_jobs import market_acquisition
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AssessInput,
    ATSProvider,
    FindJobsConfig,
    ModelTarget,
    NodeContext,
    PinnedResume,
    PostingRow,
    RowOutcome,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.profile_records import create_profile, list_profiles, selected_profile
from gigai.scout.proposal_execution import assess_node
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

FIXTURES = Path(__file__).parent / "fixtures"
_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_POSTING_B = (
    "Borealis Freight is hiring a Staff Platform Engineer. Requirements: 5+ years of Python in production; "
    "hands-on experience with Google Cloud Platform; clear written communication. Remote within the United States."
)
_REQUIREMENT_B = "Hands-on experience with Google Cloud Platform"
_ASKED_ID = "tooling:cloud_google_platform"  # as stored: the model says tooling:google_cloud_platform, ids are normalized
_ANSWER = "Yes: two years running batch and streaming workloads on GCP (GKE, BigQuery, Pub/Sub)."
_POSTING_A = {"job_identity": "https://boards.greenhouse.io/aurora/jobs/1", "title": "Staff Engineer", "company": "Aurora", "url": "https://boards.greenhouse.io/aurora/jobs/1"}
_URL_B = "https://boards.greenhouse.io/borealis/jobs/2"
_BANK_LINE = re.compile(r"^- cloud:gcp \|(?:.*\|)? answer: (?P<answer>.*)$", re.MULTILINE)


def _model_that_follows_the_story_bank(prompt: str) -> str:
    python_row = {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}
    line = _BANK_LINE.search(prompt)
    if line is not None:
        return json.dumps(
            {
                "verdict": "matched_above_threshold",
                "matrix": [
                    python_row,
                    {"requirement": _REQUIREMENT_B, "class": "askable", "status": "met", "resume_evidence": [f"Story bank cloud:gcp: {line.group('answer')}"]},
                ],
                "suggestions": [],
                "questions": [],
                "not_a_match_reason": None,
            }
        )
    return json.dumps(
        {
            "verdict": "pending_user_answers",
            "matrix": [python_row, {"requirement": _REQUIREMENT_B, "class": "askable", "status": "unclear", "resume_evidence": []}],
            "suggestions": [],
            "questions": [{"question_id": "tooling:google_cloud_platform", "question": "Do you have hands-on Google Cloud Platform experience?", "requirement": _REQUIREMENT_B}],
            "not_a_match_reason": None,
        }
    )


class _Port:
    name = "fixture"
    timed_out = False

    def __init__(self, model=_model_that_follows_the_story_bank) -> None:
        self.prompts: list[str] = []
        self.model = model

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success",
            output_text=self.model(request.prompt),
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30),
            cost_status="unavailable",
        )


class _Binding:
    def __init__(self, model=_model_that_follows_the_story_bank) -> None:
        self.port = _Port(model)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def _config(home: Path):
    return build_config(
        home_root=home,
        workpad_root=home.parent / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(
                name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",),
                max_output_tokens=512, model_digest="sha256:" + "c" * 64,
            ),
        ),
    )


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def two(fx: ProfileFixtureGig) -> tuple[str, str]:
    """``(default profile id, other profile id)``: two different people on one machine."""

    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    other = create_profile(
        fx.resolved, label="other person", titles=("staff backend engineer",), titles_to_avoid=(),
        queries=("staff backend engineer",), resume_ref=default.resume_ref,
    )
    return default.profile_id, other.profile_id


@pytest.fixture
def binding(monkeypatch: pytest.MonkeyPatch) -> _Binding:
    fake = _Binding()

    def resolve(config, adapter_target, **_kwargs):
        return fake

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return fake


def _posting(url: str = _URL_B, text: str = _POSTING_B) -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="borealis",
        company="Borealis Freight", title="Software Engineer", location="Denver, CO",
        published_at="2026-09-20T00:00:00Z", content_sha256=digest_imported_bytes(text.encode("utf-8")),
        source_kind=SourceKind.EXA, query_key="software-engineer", text=text,
    )


def _pinned(fx: ProfileFixtureGig) -> PinnedResume:
    return PinnedResume(fx.resume_record_id, fx.resume_revision_id, digest_imported_bytes(_RESUME))


def _find_jobs_config() -> FindJobsConfig:
    config = FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text()))
    return replace(config, sources=SourceToggles(exa=False, ats=False, hiringcafe=False))


def _seal_run_input(fx: ProfileFixtureGig, run_id: str, *, profile_id: str | None, config: FindJobsConfig | None = None) -> None:
    """The sealed run input a launched run has, where a launched run has it: in the workpad."""

    config = config or _find_jobs_config()
    payload: dict[str, object] = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": _pinned(fx).to_json(),
    }
    if profile_id is not None:
        profile = next(item for item in list_profiles(fx.resolved) if item.profile_id == profile_id)
        payload["profile_ref"] = {"profile_id": profile.profile_id, "revision": profile.revision, "content_digest": profile.content_digest}
    sealed = fx.created.workpad / "runs" / run_id / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    (sealed / "find-jobs-run-input.json").write_text(json.dumps(payload))


def _context(fx: ProfileFixtureGig, run_id: str, slug: str) -> NodeContext:
    return NodeContext(
        run_id=run_id, project_id=fx.created.project_id, gig_id=fx.created.gig_id,
        graph_id="graph_find_jobs_test", graph_version=1, goal_slug=slug,
        manifest_digest="sha256:" + "0" * 64, operation_key=f"{slug}-{run_id}",
        target_observation_digest="sha256:" + "0" * 64, workpad_path=str(fx.created.workpad),
        redeemed_consent_ref="none", model_target=ModelTarget.OLLAMA_LOCAL,
    )


def _assess(
    fx: ProfileFixtureGig,
    run_id: str,
    *,
    profile_id: str | None,
    postings: list[PostingRow] | None = None,
    selected: list[PostingRow] | None = None,
    config: FindJobsConfig | None = None,
    seal: bool = True,
):
    """Run the assess node of ``run_id`` over a hand-written acquire batch, sealed for ``profile_id``.

    ``config`` is the run's sealed config (default: the fixture's);
    ``seal=False`` leaves the run with no sealed input at all.
    """

    postings = postings if postings is not None else [_posting()]
    selected = selected if selected is not None else postings
    outputs = fx.created.workpad / "runs" / run_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    if not (outputs / "acquire.json").exists():
        (outputs / "acquire.json").write_text(json.dumps({"rows": [{"posting": item.to_json(), "outcome": "new"} for item in postings]}))
    if seal:
        _seal_run_input(fx, run_id, profile_id=profile_id, config=config)
    assess_input = AssessInput(
        acquire_batch_ref=str(outputs / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=tuple(SelectedPosting(item.normalized_url, item.url, item.content_sha256, True) for item in selected),
        selection_cap=10,
        selection_reasons=tuple(SelectionReason(item.normalized_url, SelectionReasonCode.NEW) for item in selected),
        pinned_resume=_pinned(fx),
        target=str(fx.target),
        model_target=ModelTarget.OLLAMA_LOCAL,
        answer_association_version="scout-answer-association:1",
    )
    return assess_node(_context(fx, run_id, "assess"), assess_input, home_root=fx.home_root, target=fx.target, config=_config(fx.home_root))


def _run_id(number: int) -> str:
    return f"run_00000000-0000-4000-8000-{number:012d}"


def _asked(output) -> list[str]:
    return [question.question_id for item in output.assessments for question in item.structured_questions]


def _evidence(output) -> tuple[str, ...]:
    row = next(row for row in output.assessments[0].matrix if row.requirement == _REQUIREMENT_B)
    return row.resume_evidence


# --- fail-before / pass-after ---------------------------------------------------------------------


def test_a_run_settles_a_reworded_requirement_from_an_answer_given_on_another_posting(
    fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding
) -> None:
    default, _other = two
    # Posting A asked "cloud:gcp"; the user answered it on the job page: one of the user's answers.
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id="cloud:gcp",
        question="Do you have GCP experience?", answer=_ANSWER, job=_POSTING_A,
    )

    # A find-jobs run for that profile; its assess node sees posting B, which words the same fact differently.
    output = _assess(fx, _run_id(1), profile_id=default)

    assert len(output.assessments) == 1 and not output.not_assessed
    assert _asked(output) == [], f"the run asked the reworded question again: {_asked(output)}"
    assert output.assessments[0].verdict.value == "matched_above_threshold"
    assert _evidence(output) == (f"Story bank cloud:gcp: {_ANSWER}",), "the requirement cites the bank answer"
    assert len(binding.port.prompts) == 1, "reuse costs no extra model call"


def test_a_run_for_another_profile_reuses_the_same_answer(fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding) -> None:
    """0.1.10.7 C: answers are the user's. A run of ANY profile gets them; an empty bank still renders no paragraph."""

    _default, other = two
    empty = _assess(fx, _run_id(11), profile_id=other)
    prompt = binding.port.prompts[-1]
    assert _asked(empty) == [_ASKED_ID]
    assert "STORY BANK (answers" not in prompt and "PRIOR ANSWERS (from earlier" not in prompt, "an empty bank renders no paragraph at all"

    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id="cloud:gcp",
        question="Do you have GCP experience?", answer=_ANSWER, job=_POSTING_A, actor="agent",
    )

    # The second profile's run: the sealed profile_ref says whose run it is; the answer is the user's.
    reused = _assess(fx, _run_id(12), profile_id=other)
    assert _asked(reused) == [], "the run reuses the answer, whichever profile it runs for"
    assert _evidence(reused) == (f"Story bank cloud:gcp: {_ANSWER}",)
    assert _ANSWER in binding.port.prompts[-1]

    # Deleted: the next run does not get it.
    story_bank.delete_answer(home_root=fx.home_root, target=fx.target, question_id="cloud:gcp")
    again = _assess(fx, _run_id(13), profile_id=other)
    assert _asked(again) == [_ASKED_ID] and _ANSWER not in binding.port.prompts[-1]


def test_a_run_puts_the_story_that_matches_its_posting_into_the_prompt_and_no_other(fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding) -> None:
    """Acceptance (b), in a find-jobs run: searched per posting, only the relevant story is sent."""

    from gigai.scout import stories

    default, _other = two
    relevant = stories.save_story(
        home_root=fx.home_root, target=fx.target, actor="agent",
        fields={"title": "Moved the data platform to Google Cloud Platform", "raw": "I moved our batch jobs to GCP and BigQuery.", "tags": ["gcp"]},
    )
    unrelated = stories.save_story(
        home_root=fx.home_root, target=fx.target, actor="agent",
        fields={"title": "Settled a disagreement with a designer", "raw": "We tested both onboarding flows and kept hers."},
    )

    output = _assess(fx, _run_id(31), profile_id=default)

    prompt = binding.port.prompts[-1]
    assert f"- {relevant.story_id} | asked: Moved the data platform to Google Cloud Platform | answer: " in prompt
    assert "I moved our batch jobs to GCP and BigQuery." in prompt
    assert unrelated.story_id not in prompt and "kept hers" not in prompt
    # Sealed with the run: both stories' marks (a later edit of either is then visible), no story text.
    assert output.story_bank is not None and set(output.story_bank.entries) == {relevant.story_id, unrelated.story_id}
    assert "batch jobs" not in json.dumps(output.story_bank.to_json())


# --- what the run seals -----------------------------------------------------------------------------


def test_the_run_seals_the_prompt_version_and_the_bank_it_read(fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding) -> None:
    from gigai.scout.assessment_core import ASSESS_PROMPT_VERSION_NO_WORK_MODE
    from gigai.scout.find_jobs.contracts import AssessOutput

    default, other = two
    empty = _assess(fx, _run_id(21), profile_id=default)
    # 0110-038: this run's config has no work mode, so its prompt is the one v4 rendered and keeps that name.
    assert empty.prompt_version == ASSESS_PROMPT_VERSION_NO_WORK_MODE == "assess-prompt-v4"
    assert empty.story_bank is not None and empty.story_bank.profile_id == default and empty.story_bank.entries == {}

    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id="cloud:gcp", answer=_ANSWER, job=_POSTING_A)
    first = _assess(fx, _run_id(22), profile_id=default)
    assert first.story_bank is not None and list(first.story_bank.entries) == ["cloud:gcp"]
    assert first.story_bank.bank_digest != empty.story_bank.bank_digest
    # The seal holds marks, never the answer or anything derived from its text.
    sealed = json.dumps(first.to_json())
    assert _ANSWER not in first.story_bank.entries["cloud:gcp"] and "two years running" not in json.dumps(first.story_bank.to_json())
    assert AssessOutput.from_json(json.loads(sealed)) == first, "the sealed output reads back as it was written"

    # The same bank read again seals the same digest; an edit changes it.
    assert _assess(fx, _run_id(23), profile_id=default).story_bank == first.story_bank
    entry = story_bank.read_bank(home_root=fx.home_root, target=fx.target)[0]
    story_bank.edit_answer(
        home_root=fx.home_root, target=fx.target, question_id="cloud:gcp",
        answer="Three years on GCP.", expected_revision=entry.revision,
    )
    edited = _assess(fx, _run_id(24), profile_id=default)
    assert edited.story_bank.bank_digest != first.story_bank.bank_digest and list(edited.story_bank.entries) == ["cloud:gcp"]

    # The stamp names the run's own profile; the bank it read is the same (the user's).
    theirs = _assess(fx, _run_id(25), profile_id=other).story_bank
    assert theirs.profile_id == other and theirs.entries == edited.story_bank.entries

    # The reuse is noted on the bank entry, as a quick assessment notes it.
    kinds = {(job.job_identity, job.kind) for job in story_bank.read_bank(home_root=fx.home_root, target=fx.target)[0].jobs}
    assert (_URL_B, "reused") in kinds


def test_an_output_sealed_before_the_bank_reads_and_writes_as_it_was(fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding) -> None:
    from gigai.scout.find_jobs.contracts import AssessOutput

    default, _other = two
    output = _assess(fx, _run_id(31), profile_id=default)
    old = output.to_json()
    del old["prompt_version"], old["story_bank"]
    parsed = AssessOutput.from_json(json.loads(json.dumps(old)))
    assert parsed.prompt_version is None and parsed.story_bank is None
    assert canonical_json_bytes(parsed.to_json()) == canonical_json_bytes(old)


# --- a re-run after the bank changed -----------------------------------------------------------------


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


def _acquire(fx: ProfileFixtureGig, run_id: str, *, profile_id: str):
    """The acquire node of ``run_id``, as a launched run calls it (home and target bound), over posting B."""

    _seal_run_input(fx, run_id, profile_id=profile_id)
    acquire_input = AcquireInput(_find_jobs_config(), "sha256:" + "e" * 64, None, (_posting(),), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)
    output = acquire_node(
        _context(fx, run_id, "acquire"), acquire_input,
        http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(), home_root=fx.home_root, target=fx.target,
    )
    outputs = fx.created.workpad / "runs" / run_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "acquire.json").write_bytes(canonical_json_bytes(output.to_json()))
    return output


def _run(fx: ProfileFixtureGig, number: int, *, profile_id: str):
    """One run: acquire, then assess what acquire selected, then the sealed ``outputs/assess.json``.

    Returns ``(acquire output, assess output or None)``; ``None`` when
    acquire selected nothing (the posting was skipped as unchanged).
    """

    run_id = _run_id(number)
    acquired = _acquire(fx, run_id, profile_id=profile_id)
    if not acquired.selected_postings:
        return acquired, None
    assessed = _assess(fx, run_id, profile_id=profile_id, postings=[item.posting for item in acquired.rows])
    (fx.created.workpad / "runs" / run_id / "outputs" / "assess.json").write_bytes(canonical_json_bytes(assessed.to_json()))
    return acquired, assessed


def test_a_rerun_assesses_an_unchanged_posting_again_once_the_bank_can_answer_its_question(
    fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    default, _other = two
    # The rank step is another model call (fails open); this test is about the selection.
    monkeypatch.setattr(market_acquisition, "_rank_candidates", lambda *args, **kwargs: ())

    # Run 1: nothing in the bank. Posting B is new, assessed, and the question is asked.
    first_acquire, first = _run(fx, 41, profile_id=default)
    assert {row.outcome for row in first_acquire.rows} == {RowOutcome.NEW}
    assert first is not None and _asked(first) == [_ASKED_ID]

    # Run 2: the same posting, the same resume, the same (empty) bank: skipped, the verdict carried forward.
    second_acquire, second = _run(fx, 42, profile_id=default)
    assert {row.outcome for row in second_acquire.rows} == {RowOutcome.UNCHANGED}
    assert second is None and len(second_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 1, "an unchanged bank costs no model call"

    # The user answers the question on ANOTHER posting, under another wording.
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id="cloud:gcp",
        question="Do you have GCP experience?", answer=_ANSWER, job=_POSTING_A,
    )

    # Run 3: posting B is still unchanged, but its open question may be answerable now: assessed again.
    third_acquire, third = _run(fx, 43, profile_id=default)
    assert {row.outcome for row in third_acquire.rows} == {RowOutcome.UNCHANGED}
    assert third_acquire.carried_forward_assessments == (), "the stale verdict is not served"
    assert third is not None and _asked(third) == [] and _evidence(third) == (f"Story bank cloud:gcp: {_ANSWER}",)

    # Run 4: nothing changed since: skipped again, and it is run 3's verdict that is carried.
    fourth_acquire, fourth = _run(fx, 44, profile_id=default)
    assert fourth is None and len(fourth_acquire.carried_forward_assessments) == 1
    assert fourth_acquire.carried_forward_assessments[0].result.structured_questions == ()
    calls = len(binding.port.prompts)

    # The cited answer is edited: the verdict that cites it is made again.
    entry = story_bank.read_bank(home_root=fx.home_root, target=fx.target)[0]
    story_bank.edit_answer(
        home_root=fx.home_root, target=fx.target, question_id="cloud:gcp",
        answer="Three years on GCP, mostly BigQuery.", expected_revision=entry.revision,
    )
    fifth_acquire, fifth = _run(fx, 45, profile_id=default)
    assert fifth is not None and fifth_acquire.carried_forward_assessments == ()
    assert _evidence(fifth) == ("Story bank cloud:gcp: Three years on GCP, mostly BigQuery.",)
    assert len(binding.port.prompts) == calls + 1


def _bank_of(**marks: str) -> story_bank.AssessBank:
    """A bank with one entry per ``question_id=mark`` (``__`` for ``:``), as ``assess_bank`` builds it."""

    entries = tuple(
        story_bank.BankEntry(
            question_id=key.replace("__", ":"), question=f"About {key.replace('__', ' ')}?", answer="An answer.", tag="technical",
            created_at=None, updated_at=None, jobs=(), record_id=f"record_{key}", revision_id=mark,
        )
        for key, mark in marks.items()
    )
    return story_bank.AssessBank("profile_1", entries=entries, marks={entry.question_id: entry.revision_id for entry in entries})


def test_the_staleness_rule() -> None:
    """0110-041: targeted. A changed entry counts only when it answers one of the assessment's OWN open questions."""

    stale = story_bank.bank_makes_stale
    asked = [SimpleNamespace(question_id=_ASKED_ID, question=f"Do you have {_REQUIREMENT_B}?")]  # tooling:cloud_google_platform
    other = [SimpleNamespace(question_id="years:rust", question="How many years of Rust?")]
    cites = ["Story bank cloud:gcp: two years"]
    gcp, gcp_edited, empty = _bank_of(cloud__gcp="a"), _bank_of(cloud__gcp="b"), _bank_of()

    # Nothing open, nothing cited: the bank cannot change this verdict.
    assert not stale(questions=[], evidence=["six years"], sealed_marks={}, bank=gcp)
    # An open question: stale when an entry the run never saw (new, or edited) answers IT: the same id ...
    assert stale(questions=asked, evidence=[], sealed_marks={}, bank=_bank_of(tooling__cloud_google_platform="a"))
    # ... or the near match behind bank_suggestions (cloud:gcp for "Google Cloud Platform") ...
    assert stale(questions=asked, evidence=[], sealed_marks={}, bank=gcp)
    assert stale(questions=asked, evidence=[], sealed_marks={"cloud:gcp": "a"}, bank=gcp_edited)
    # ... a run from before the bank reached runs saw none of it ...
    assert stale(questions=asked, evidence=[], sealed_marks=None, bank=gcp)
    assert not stale(questions=asked, evidence=[], sealed_marks=None, bank=empty)
    # ... and NOT when the new or edited entry answers some other question (the broad rule before 0110-041).
    assert not stale(questions=other, evidence=[], sealed_marks={}, bank=gcp)
    assert not stale(questions=other, evidence=[], sealed_marks={"cloud:gcp": "a"}, bank=gcp_edited)
    assert not stale(questions=asked, evidence=[], sealed_marks={"cloud:gcp": "a"}, bank=_bank_of(cloud__gcp="a", years__rust="c"))
    # Not when the bank is what the run saw, or only lost an entry (a deletion answers nothing).
    assert not stale(questions=asked, evidence=[], sealed_marks={"cloud:gcp": "a"}, bank=gcp)
    assert not stale(questions=asked, evidence=[], sealed_marks={"cloud:gcp": "a", "years:go": "b"}, bank=gcp)
    # A cited answer: stale when it was edited or deleted; not when another entry changed.
    assert stale(questions=[], evidence=cites, sealed_marks={"cloud:gcp": "a"}, bank=gcp_edited)
    assert stale(questions=[], evidence=cites, sealed_marks={"cloud:gcp": "a"}, bank=empty)
    assert not stale(questions=[], evidence=cites, sealed_marks={"cloud:gcp": "a"}, bank=_bank_of(cloud__gcp="a", years__go="c"))

    # What matched is named: the entry, how, and the question it answers.
    (near,) = story_bank.bank_matches(questions=asked, evidence=[], sealed_marks={}, bank=gcp)
    assert (near.match, near.bank_question_id, near.question_id) == ("near", "cloud:gcp", _ASKED_ID)
    (cited,) = story_bank.bank_matches(questions=[], evidence=cites, sealed_marks={"cloud:gcp": "a"}, bank=empty)
    assert cited.to_json() == {"match": "cited", "bank_question_id": "cloud:gcp"}


# --- the questions that remain: the same near match as the job page -----------------------------------


def test_a_runs_results_and_posting_bodies_carry_bank_suggestions_for_the_questions_left_open(
    fx: ProfileFixtureGig, two: tuple[str, str], binding: _Binding
) -> None:
    from gigai.scout.find_jobs.api.run_reads import attach_bank_suggestions, open_run_questions

    default, other = two
    output = _assess(fx, _run_id(51), profile_id=default)  # an empty bank: the question is asked
    assessment = output.assessments[0].to_json()
    assert [item["question_id"] for item in assessment["structured_questions"]] == [_ASKED_ID]
    results = {"payload": {"assessments": [assessment]}, "carried_forward_assessments": []}
    posting = {"assessment": None, "carried_forward": {"normalized_url": _URL_B, "result": assessment, "from_run_date": None}}
    paths = {"home_root": fx.home_root, "target": fx.target}
    assert [item["question_id"] for item in open_run_questions(results)] == [_ASKED_ID] == [item["question_id"] for item in open_run_questions(posting)]

    # Nothing in the bank: no key at all.
    attach_bank_suggestions(results, **paths, profile_id=default)
    assert "bank_suggestions" not in results

    # The answer arrives later, under another id: the run's stored question now has a near match.
    story_bank.save_answer(**paths, question_id="cloud:gcp", question="Do you have GCP experience?", answer=_ANSWER, job=_POSTING_A)
    for body in (results, posting):
        attach_bank_suggestions(body, **paths, profile_id=default)
        (suggestion,) = body["bank_suggestions"]
        assert suggestion["question_id"] == _ASKED_ID and suggestion["bank_question_id"] == "cloud:gcp" and suggestion["answer"] == _ANSWER
        assert suggestion == story_bank.near_match(
            story_bank.read_bank(**paths), question_id=_ASKED_ID, question="Do you have hands-on Google Cloud Platform experience?"
        ).to_json(), "the same match as GET /api/answers/match and the job page"

    # 0.1.10.7 C: the answers are the user's: another profile's run, and a run with no profile, get the same suggestion.
    for profile_id in (other, None):
        theirs = {"payload": {"assessments": [assessment]}, "carried_forward_assessments": []}
        attach_bank_suggestions(theirs, **paths, profile_id=profile_id)
        assert [item["bank_question_id"] for item in theirs["bank_suggestions"]] == ["cloud:gcp"]
    # No open question: nothing read, nothing added.
    done = {"payload": {"assessments": []}}
    attach_bank_suggestions(done, **paths, profile_id=default)
    assert "bank_suggestions" not in done
