"""0110-034: ``gigai.scout.story_bank`` -- a profile's answered questions, kept and reused.

Real gig (``build_gig_with_resume``), synthetic answers only; the model is a
scripted binding on the C1 seam (``proposal_execution.resolve_model_adapter``)
so every prompt is captured. Two profiles stand for two different people on
one machine: "default" (the gig's migrated default profile) and "other".

Covers: answers saved by the 0.1.10.4 code path are listed with no migration
and belong to the profile their answer history names, else the default; an
answer saved through the bank carries its question, tag, posting, dates,
revision and writer; profile 2 never sees profile 1's answers (list, near
match, the assess prompt, the answers the tailoring reads) until sharing is
set, and loses them when it is unset; the two can answer the same id
differently; edit / delete round trips, the stale-write refusal, a new story;
the personal-info refusal; the near match and its confirmation; the prompt
summaries' cap and redaction; the model-free tag.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.native_records import list_native_records
from gigai.private_records import PrivateRecordError
from gigai.scout import story_bank
from gigai.scout.experience_answers import read_answers, record_answer
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.profile_records import create_profile, selected_profile
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.story_bank import StoryBankError
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_GCP = "Yes: two years running batch workloads on GCP."
_MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


def _pending(question_id: str, question: str) -> str:
    return json.dumps(
        {
            "verdict": "pending_user_answers",
            "matrix": [
                {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                {"requirement": "The platform", "class": "askable", "status": "unclear", "resume_evidence": []},
            ],
            "suggestions": [],
            "questions": [{"question_id": question_id, "question": question, "requirement": "The platform"}],
            "not_a_match_reason": None,
        }
    )


class _Port:
    name = "fixture"
    timed_out = False

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self.outputs.pop(0), resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )


class _Binding:
    def __init__(self, outputs: list[str]) -> None:
        self.port = _Port(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def _install(monkeypatch: pytest.MonkeyPatch, outputs: list[str]) -> _Binding:
    binding = _Binding(outputs)

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


def _config(home: Path):
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
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


def _assess(fx: ProfileFixtureGig, profile_id: str, text: str, *, trigger: str | None = None):
    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=text), resume=AssessResumeInput(profile_id=profile_id)),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root), trigger=trigger,
    )


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def two(fx: ProfileFixtureGig) -> tuple[str, str]:
    """``(default profile id, other profile id)``: two people on one machine."""

    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    other = create_profile(
        fx.resolved, label="other person", titles=("staff backend engineer",), titles_to_avoid=(),
        queries=("staff backend engineer",), resume_ref=default.resume_ref,
    )
    return default.profile_id, other.profile_id


def _paths(fx: ProfileFixtureGig) -> dict[str, object]:
    return {"home_root": fx.home_root, "target": fx.target}


def _ids(fx: ProfileFixtureGig, profile_id: str, **kwargs) -> list[str]:
    return [entry.question_id for entry in story_bank.read_bank(**_paths(fx), profile_id=profile_id, **kwargs)]


# --- answers saved before the bank: listed, no migration --------------------------------------------


def test_answers_written_by_the_old_code_path_are_listed_with_no_migration(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, other = two
    # Exactly what 0.1.10.4's POST /api/answers and `scout answer` wrote: no profile, prompt = the id.
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:gcp", prompt="cloud:gcp", answer=_GCP)
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="years:python", prompt="years:python", answer="Six.")
    records_before = [(row["record_id"], row["revision_id"]) for row in list_native_records(home_root=fx.home_root, requested_target=fx.target)]

    entries = story_bank.read_bank(**_paths(fx), profile_id=default)

    assert [entry.question_id for entry in entries] == ["cloud:gcp", "years:python"]
    gcp = entries[0]
    assert gcp.answer == _GCP and gcp.legacy is True and gcp.shared is False and gcp.revision == 0
    assert gcp.question == "cloud:gcp" and gcp.tag == "technical" and entries[1].tag == "experience-level"
    assert gcp.updated_at, "the record's own date stands in for an answer saved before the bank"
    # Reading migrates nothing: no overlay file, no new record or revision.
    assert not story_bank.bank_path(fx.home_root, fx.target).exists()
    assert [(row["record_id"], row["revision_id"]) for row in list_native_records(home_root=fx.home_root, requested_target=fx.target)] == records_before
    # A profile that did not give them does not inherit them.
    assert _ids(fx, other) == []


def test_an_old_answer_belongs_to_the_profile_its_answer_history_names(fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    default, other = two
    _install(monkeypatch, [_MATCH])
    # The other person answered cloud:aws on their posting: the old route stored the re-assessment
    # under THEIR profile with the trigger answer:cloud:aws, and the answer with no profile at all.
    _assess(fx, other, "Requirements: Python. AWS.", trigger="answer:cloud:aws")
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:aws", prompt="cloud:aws", answer="Four years on AWS.")
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:gcp", prompt="cloud:gcp", answer=_GCP)

    assert _ids(fx, other) == ["cloud:aws"], "named by the answer history"
    assert _ids(fx, default) == ["cloud:gcp"], "no history: the default profile's"
    aws = story_bank.read_bank(**_paths(fx), profile_id=other)[0]
    assert [posting.kind for posting in aws.postings] == ["answered"] and aws.first_answered_at == aws.postings[0].at


# --- a save through the bank ----------------------------------------------------------------------


def test_a_saved_answer_carries_question_tag_posting_dates_revision_and_writer(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, _other = two
    posting = {"job_identity": "https://jobs.example.test/a", "title": "Staff Engineer", "company": "Acme", "url": "https://jobs.example.test/a"}

    story_bank.save_answer(
        **_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP,
        question="Have you run workloads on GCP?", posting=posting,
    )

    (entry,) = story_bank.read_bank(**_paths(fx), profile_id=default)
    assert (entry.question_id, entry.question, entry.answer, entry.tag) == ("cloud:gcp", "Have you run workloads on GCP?", _GCP, "technical")
    assert entry.legacy is False and entry.revision == 1 and entry.written_by == "operator"
    assert entry.first_answered_at and entry.first_answered_at == entry.updated_at
    assert [posting.to_json() for posting in entry.postings] == [{**posting, "kind": "answered", "at": entry.updated_at}]
    assert [item["action"] for item in entry.history] == ["answered"]
    # The answer text is still one experience_qa question, nothing else.
    assert set(read_answers(home_root=fx.home_root, requested_target=fx.target)) == {"cloud:gcp"}


# --- two different people ---------------------------------------------------------------------------


def test_a_second_profile_never_sees_the_first_ones_answers_until_shared_and_loses_them_when_unshared(
    fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    default, other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP, question="Have you run workloads on GCP?")
    reworded = {"question_id": "tooling:google_cloud_platform", "question": "Do you have hands-on Google Cloud Platform experience?"}

    def sees(profile_id: str) -> dict[str, object]:
        binding = _install(monkeypatch, [_pending(reworded["question_id"], reworded["question"])])
        _assess(fx, profile_id, f"Requirements: Python. Google Cloud Platform. ({profile_id})")
        entries = story_bank.read_bank(**_paths(fx), profile_id=profile_id)
        return {
            "list": [entry.question_id for entry in entries],
            "near": story_bank.near_match(entries, **reworded) is not None,
            "answers": sorted(story_bank.answers_for_profile(**_paths(fx), profile_id=profile_id)),
            "prompt": _GCP in binding.port.prompts[0] or "- cloud:gcp" in binding.port.prompts[0],
        }

    nothing = {"list": [], "near": False, "answers": [], "prompt": False}
    everything = {"list": ["cloud:gcp"], "near": True, "answers": ["cloud:gcp"], "prompt": True}
    assert sees(default) == everything
    assert sees(other) == nothing, "a different person's profile: no list, no near match, no answers, nothing in the prompt"

    result = story_bank.set_sharing(**_paths(fx), profile_id=other, share_with=default)
    assert result["share_with"] == default
    assert story_bank.sharing(**_paths(fx), profile_id=default)["read_by"] == [other]
    assert sees(other) == everything
    shared = story_bank.read_bank(**_paths(fx), profile_id=other)[0]
    assert shared.shared is True and shared.owner_profile_id == default
    assert sees(default) == everything and _ids(fx, default) == ["cloud:gcp"], "sharing is one way: the owner reads nothing new"

    story_bank.set_sharing(**_paths(fx), profile_id=other, share_with=None)
    assert sees(other) == nothing


def test_sharing_is_one_hop_and_explicit(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, other = two
    third = create_profile(
        fx.resolved, label="third", titles=("x",), titles_to_avoid=(), queries=("x",),
        resume_ref=selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target).resume_ref,  # type: ignore[union-attr]
    ).profile_id
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP)
    story_bank.save_answer(**_paths(fx), profile_id=other, question_id="cloud:aws", answer="Four years on AWS.")
    story_bank.set_sharing(**_paths(fx), profile_id=other, share_with=default)
    story_bank.set_sharing(**_paths(fx), profile_id=third, share_with=other)

    assert _ids(fx, third) == ["cloud:aws"], "third reads other's OWN answers, not what other reads from default"
    assert _ids(fx, other) == ["cloud:aws", "cloud:gcp"]
    with pytest.raises(StoryBankError) as itself:
        story_bank.set_sharing(**_paths(fx), profile_id=other, share_with=other)
    assert itself.value.code == "invalid_value"
    with pytest.raises(StoryBankError) as unknown:
        story_bank.set_sharing(**_paths(fx), profile_id=other, share_with="profile_nobody")
    assert unknown.value.code == "profile_not_found"


def test_two_profiles_answer_the_same_question_differently(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, other = two
    first = story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="Yes, two years.")
    second = story_bank.save_answer(**_paths(fx), profile_id=other, question_id="cloud:gcp", answer="No, never.")
    same_text = story_bank.save_answer(**_paths(fx), profile_id=other, question_id="years:python", answer="Six.")
    also_same = story_bank.save_answer(**_paths(fx), profile_id=default, question_id="years:python", answer="Six.")

    assert first.record_id != second.record_id, "each profile has its own record"
    assert same_text.record_id == second.record_id and also_same.record_id == first.record_id
    assert story_bank.answers_for_profile(**_paths(fx), profile_id=default)["cloud:gcp"].answer == "Yes, two years."
    assert story_bank.answers_for_profile(**_paths(fx), profile_id=other)["cloud:gcp"].answer == "No, never."
    # Shared: the reader's own answer wins the id both hold.
    story_bank.set_sharing(**_paths(fx), profile_id=other, share_with=default)
    assert story_bank.answers_for_profile(**_paths(fx), profile_id=other)["cloud:gcp"].answer == "No, never."


def test_an_old_answer_of_the_default_profile_is_not_overwritten_by_another_profile(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, other = two
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:gcp", prompt="cloud:gcp", answer=_GCP)

    story_bank.save_answer(**_paths(fx), profile_id=other, question_id="cloud:gcp", answer="No GCP.")
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="Three years now.", question="Have you used GCP?")

    assert story_bank.answers_for_profile(**_paths(fx), profile_id=other)["cloud:gcp"].answer == "No GCP."
    (entry,) = story_bank.read_bank(**_paths(fx), profile_id=default)
    assert entry.answer == "Three years now." and entry.question == "Have you used GCP?"
    assert entry.legacy is True, "edited in place: a new revision of its own (old) record"


# --- edit / delete / add ----------------------------------------------------------------------------


def test_edit_and_delete_round_trip(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="A", question="GCP?")

    edited = story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="B", actor="agent")
    assert (edited.answer, edited.edited, edited.revision, edited.written_by) == ("B", True, 2, "agent")
    back = story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="A")
    assert back.answer == "A" and back.revision == 3, "an edit back to an earlier answer is a new revision"
    tagged = story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", tag="Cloud Platforms")
    assert tagged.tag == "cloud platforms" and tagged.answer == "A" and tagged.revision == 4
    assert story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", tag="").tag == "technical"
    worded = story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", question="Have you used Google Cloud?")
    assert worded.question == "Have you used Google Cloud?" and worded.answer == "A"
    assert [item["by"] for item in worded.history] == ["operator", "agent", "operator", "operator", "operator", "operator"]

    with pytest.raises(StoryBankError) as not_mine:
        story_bank.edit_entry(**_paths(fx), profile_id=other, question_id="cloud:gcp", answer="X")
    assert not_mine.value.code == "not_found"
    with pytest.raises(StoryBankError) as nothing:
        story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp")
    assert nothing.value.code == "invalid_value"

    assert story_bank.delete_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp") == "cloud:gcp"
    assert _ids(fx, default) == [] and story_bank.answers_for_profile(**_paths(fx), profile_id=default) == {}
    assert read_answers(home_root=fx.home_root, requested_target=fx.target) == {}
    with pytest.raises(StoryBankError) as gone:
        story_bank.delete_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp")
    assert gone.value.code == "not_found"
    # Answered again after a delete: one question in the record, not two.
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="Again.")
    (again,) = story_bank.read_bank(**_paths(fx), profile_id=default)
    assert again.answer == "Again." and again.revision == 1


def test_a_stale_write_is_refused_with_the_current_entry(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, _other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="A")
    read = story_bank.read_bank(**_paths(fx), profile_id=default)[0]
    # The agent edits first; the UI still holds what it read.
    by_agent = story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="B", actor="agent", expected_updated_at=read.updated_at)

    for write in (
        lambda: story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="C", expected_updated_at=read.updated_at),
        lambda: story_bank.delete_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", expected_updated_at=read.updated_at),
    ):
        with pytest.raises(StoryBankError) as stale:
            write()
        assert stale.value.code == "story_bank_changed"
        assert stale.value.entry is not None and stale.value.entry.answer == "B" and stale.value.entry.written_by == "agent"
    assert story_bank.read_bank(**_paths(fx), profile_id=default)[0].answer == "B"
    # With the current value it goes through.
    assert story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer="C", expected_updated_at=by_agent.updated_at).answer == "C"


def test_add_a_story_nobody_asked_for_yet(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, _other = two
    story = "Situation: a 4 TB Postgres primary near its limit. Task: move it with no downtime. Action: led the dual-write cut-over. Result: zero lost writes."

    entry = story_bank.add_story(
        **_paths(fx), profile_id=default, question="Tell me about a database migration you led", answer=story, actor="agent"
    )

    assert entry.question_id == "story:database_led_migration" and entry.tag == "leadership"
    assert (entry.written_by, entry.revision, entry.postings) == ("agent", 1, ())
    assert [item["action"] for item in entry.history] == ["added"]
    with pytest.raises(StoryBankError) as exists:
        story_bank.add_story(**_paths(fx), profile_id=default, question="Tell me about a database migration you led", answer="Another.")
    assert exists.value.code == "story_exists" and exists.value.entry is not None and exists.value.entry.answer == story
    named = story_bank.add_story(**_paths(fx), profile_id=default, question="Kafka?", answer="Three years.", question_id="tool:kafka", tag="streaming")
    assert named.question_id == "tooling:kafka" and named.tag == "streaming"
    with pytest.raises(StoryBankError) as actor:
        story_bank.add_story(**_paths(fx), profile_id=default, question="Other?", answer="x", actor="robot")
    assert actor.value.code == "invalid_value"


# --- privacy ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, found",
    [
        ("Yes, write to zq7731@example.test for details.", "email"),
        ("Call me on 555-013-7731 to talk about it.", "phone"),
        ("See github.com/zq-7731/ledger for the code.", "links"),
        ("The write-up is at https://zq7731.example/gcp.", "links"),
        ("I lived at 12 Glimmerfall Street then.", "address"),
        ("Zephyrine Quillfeather ran that migration.", "name"),
    ],
)
def test_an_answer_with_personal_information_is_refused_on_save_and_on_edit(fx: ProfileFixtureGig, two: tuple[str, str], answer: str, found: str) -> None:
    default, _other = two
    names = ("Zephyrine Quillfeather",)
    with pytest.raises(StoryBankError) as saved:
        story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=answer, names=names)
    assert saved.value.code == "personal_info_refused" and found in str(saved.value)
    assert answer not in str(saved.value), "the refusal never echoes the text"
    assert _ids(fx, default) == [] and not story_bank.bank_path(fx.home_root, fx.target).exists()

    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP, names=names)
    with pytest.raises(StoryBankError) as edited:
        story_bank.edit_entry(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=answer, names=names)
    assert edited.value.code == "personal_info_refused"
    with pytest.raises(StoryBankError) as added:
        story_bank.add_story(**_paths(fx), profile_id=default, question="A new story", answer=answer, names=names)
    assert added.value.code == "personal_info_refused"
    assert story_bank.read_bank(**_paths(fx), profile_id=default)[0].answer == _GCP


def test_an_ordinary_answer_is_not_mistaken_for_a_name(fx: ProfileFixtureGig, two: tuple[str, str]) -> None:
    default, _other = two
    for index, answer in enumerate(("Apache Kafka", "Google Cloud", "Yes", "Six years, mostly at Acme in Denver, CO.")):
        story_bank.save_answer(**_paths(fx), profile_id=default, question_id=f"skill:s{index}", answer=answer, names=("Zephyrine Quillfeather",))
    assert len(_ids(fx, default)) == 4
    with pytest.raises(PrivateRecordError) as empty:
        story_bank.save_answer(**_paths(fx), profile_id=default, question_id="skill:empty", answer="   ")
    assert empty.value.code == "answer_invalid"


def test_what_reaches_the_prompt_is_capped_redacted_and_never_another_profiles(fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    default, other = two
    # Saved before the personal-info check existed: an old answer with contact details in it.
    record_answer(
        home_root=fx.home_root, requested_target=fx.target, question_id="cloud:azure", prompt="cloud:azure",
        answer="Three years on Azure; ask zq7731@example.test or 555-013-7731, see https://zq7731.example/azure.",
    )
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP + " " + "More detail. " * 40, question="Have you run workloads on GCP?")
    story_bank.save_answer(**_paths(fx), profile_id=other, question_id="cloud:aws", answer="OTHER-PERSON-ONLY four years on AWS.")
    binding = _install(monkeypatch, [_MATCH])

    _assess(fx, default, "Requirements: Python.")

    prompt = binding.port.prompts[0]
    bank_lines = [line for line in prompt.splitlines() if line.startswith("- ") and " | " in line and " answer: " in line]
    assert [line.split(" | ")[0] for line in bank_lines] == ["- cloud:gcp", "- cloud:azure"], "newest first"
    gcp_line = bank_lines[0]
    assert " | asked: Have you run workloads on GCP? | answer: Yes: two years" in gcp_line
    assert len(gcp_line.split(" answer: ", 1)[1]) <= story_bank.MAX_SUMMARY_CHARS and gcp_line.endswith("...")
    assert bank_lines[1] == "- cloud:azure | answer: Three years on Azure; ask or , see"
    for value in ("zq7731", "555-013", "example.test", "OTHER-PERSON-ONLY", "cloud:aws"):
        assert value not in prompt, value
    assert "STORY BANK (answers" in prompt and 'put "Story bank <question_id>: <the answer>"' in prompt
    assert "- cloud:azure: Three years on Azure; ask or , see" in prompt, "the PRIOR ANSWERS line is redacted too"

    entries = story_bank.read_bank(**_paths(fx), profile_id=default)
    many = tuple(
        story_bank.BankEntry(f"skill:s{index:03d}", "", "ok", "skill", default, False, False, False, None, None, None, (), "r", "v")
        for index in range(story_bank.MAX_PROMPT_SUMMARIES + 10)
    )
    assert len(story_bank.prompt_summaries(many)) == story_bank.MAX_PROMPT_SUMMARIES
    named = {item.question_id: item.summary for item in story_bank.prompt_summaries(entries, names=("Zephyrine Azure",))}
    assert named["cloud:azure"] == "Three years on ; ask or , see", "the words of a known name are removed from the line"


def test_a_profile_with_no_answers_gets_the_same_prompt_as_before(fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    default, other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP)
    binding = _install(monkeypatch, [_MATCH])
    _assess(fx, other, "Requirements: Python.")
    assert "STORY BANK (answers" not in binding.port.prompts[0] and "PRIOR ANSWERS (from earlier" not in binding.port.prompts[0]


# --- the near match and its confirmation ------------------------------------------------------------


def test_a_near_match_is_suggested_and_the_confirmation_stores_it_for_the_new_posting(fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    default, _other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP, question="Have you run workloads on GCP?")
    asked = {"question_id": "tooling:google_cloud_platform", "question": "Do you have hands-on Google Cloud Platform experience?"}
    # The model asked anyway (it did not reuse the bank line).
    _install(monkeypatch, [_pending(asked["question_id"], asked["question"])])
    response = _assess(fx, default, "Requirements: Python. Google Cloud Platform.")

    payload = story_bank.attach_suggestions(response.to_json(), home_root=fx.home_root, target=fx.target)
    (suggestion,) = payload["bank_suggestions"]  # type: ignore[misc]
    assert suggestion == {
        "question_id": "tooling:cloud_google_platform", "bank_question_id": "cloud:gcp", "bank_question": "Have you run workloads on GCP?",
        "answer": _GCP, "score": 1.0, "owner_profile_id": default, "shared": False,
    }
    assert "bank_suggestions" not in json.loads(Path(response.stored_path).read_text(encoding="utf-8")), "computed on read, never stored"

    # The user confirms: the same text is saved as the answer to posting B's question.
    posting_b = {"job_identity": response.job.job_identity, "title": "", "company": "", "url": None}
    story_bank.save_answer(
        **_paths(fx), profile_id=default, question_id=asked["question_id"], answer=suggestion["answer"],  # type: ignore[arg-type]
        question=asked["question"], posting=posting_b, confirmed_from=suggestion["bank_question_id"],  # type: ignore[arg-type]
    )
    by_id = {entry.question_id: entry for entry in story_bank.read_bank(**_paths(fx), profile_id=default)}
    confirmed = by_id["tooling:cloud_google_platform"]
    assert confirmed.answer == _GCP and confirmed.confirmed_from == "cloud:gcp"
    assert [(posting.job_identity, posting.kind) for posting in confirmed.postings] == [(response.job.job_identity, "confirmed")]
    # Now it is an exact answer: no suggestion is left for that question.
    assert "bank_suggestions" not in story_bank.attach_suggestions(response.to_json(), home_root=fx.home_root, target=fx.target)


@pytest.mark.parametrize(
    "question_id, question, expected",
    [
        ("tooling:google_cloud_platform", "Do you have hands-on Google Cloud Platform experience?", True),
        ("cloud:gcp_bigquery", "Have you used BigQuery on GCP?", True),
        ("cloud:aws", "Have you used AWS?", False),
        ("years:python", "How many years of Python?", False),
        ("cloud:gcp", "Have you used GCP?", False),  # the same id: plain reuse, never a suggestion
    ],
)
def test_the_near_match_is_token_overlap_and_never_the_exact_id(question_id: str, question: str, expected: bool) -> None:
    entry = story_bank.BankEntry("cloud:gcp", "Have you run workloads on GCP?", _GCP, "technical", "p", False, False, False, None, None, None, (), "r", "v")
    assert (story_bank.near_match((entry,), question_id=question_id, question=question) is not None) is expected


def test_an_assessment_that_cites_a_bank_answer_is_noted_on_that_entry(fx: ProfileFixtureGig, two: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    default, _other = two
    story_bank.save_answer(**_paths(fx), profile_id=default, question_id="cloud:gcp", answer=_GCP)
    cited = json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [
                {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                {"requirement": "Google Cloud Platform", "class": "askable", "status": "met", "resume_evidence": [f"Story bank cloud:gcp: {_GCP}"]},
            ],
            "suggestions": [], "questions": [], "not_a_match_reason": None,
        }
    )
    _install(monkeypatch, [cited])

    response = _assess(fx, default, "Requirements: Python. Google Cloud Platform.")

    (entry,) = story_bank.read_bank(**_paths(fx), profile_id=default)
    assert [(posting.job_identity, posting.kind) for posting in entry.postings] == [(response.job.job_identity, "reused")]
    assert entry.revision == 1, "a reuse is noted, it is not a write of the answer"


# --- the tag ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question_id, question, tag",
    [
        ("cloud:gcp", "", "technical"),
        ("language:kotlin_or_python", "", "technical"),
        ("years:python", "How many years leading Python teams?", "experience-level"),
        ("location:us_region", "", "eligibility"),
        ("education:enrolled", "", "education"),
        ("domain:fintech", "", "domain"),
        ("skill:team", "Have you led a team of engineers?", "leadership"),
        ("story:x", "Tell me about a time you disagreed with a stakeholder", "conflict"),
        ("story:y", "Describe an outage you caused", "failure"),
        ("skill:distributed_systems", "", "system-design"),
        ("unstructured", "", "other"),
    ],
)
def test_the_tag_is_model_free_and_stable(question_id: str, question: str, tag: str) -> None:
    assert story_bank.tag_for(question_id, question) == tag
    assert story_bank.tag_for(question_id, question) == tag
