from types import SimpleNamespace
import json
from pathlib import Path
import uuid

import pytest

from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.canonical import digest_imported_bytes, parse_json_bytes
from gigai.config import CredentialReference, Endpoint, load_config
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.lifecycle import approve_offline, create_offline
from gigai.private_records import create_record, import_reference, migrate_workpad_layout
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    AssessInput,
    AssessmentQuestion,
    AssessmentResult,
    MatrixStatus,
    ModelTarget,
    NodeContext,
    NotAssessedReason,
    PinnedResume,
    PostingRow,
    Producer,
    RequirementClass,
    RequirementMatrixRow,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
    SourceKind,
    SponsorshipStatus,
    Verdict,
)
from gigai.scout.proposal_execution import (
    ScoutProposalExecutionError,
    assess_invocation_policy,
    assess_node,
)
from gigai.scout.proposal_records import read_proposal_revision, save_assessment_revision
from gigai.scout.proposals import parse_assessment_proposal
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from tests.behaviors.scout_discovery.test_scout07_posting_inputs import _completed_find_jobs


def _input():
    return SimpleNamespace(
        selected_postings=(SimpleNamespace(normalized_url="https://example.test/job"),),
        pinned_resume=SimpleNamespace(record_id="resume-record"),
    )


def test_local_assess_policy_is_offline_and_disallows_network():
    policy = assess_invocation_policy("ollama_local", _input())
    assert policy.local_allowed is True
    assert policy.network_allowed is False
    assert policy.offline is True


@pytest.mark.parametrize("target", ["codex_cli", "openrouter_api"])
def test_hosted_assess_policy_allows_network_and_is_not_offline(target):
    policy = assess_invocation_policy(target, _input())
    assert policy.local_allowed is False
    assert policy.network_allowed is True
    assert policy.offline is False


def test_unknown_assess_target_fails_loudly():
    with pytest.raises(ScoutProposalExecutionError):
        assess_invocation_policy("unknown", _input())


class _AssessmentAdapter:
    name = "deterministic"

    def invoke(self, request):
        return InvocationResult(
            status="success",
            output_text=json.dumps({
                "matrix": [{"requirement": "Python", "resume_evidence": ["Built APIs"], "status": "met"}],
                "suggestions": ["Keep the API example."],
                "questions": [],
            }),
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 8, 18),
            cost_status="unavailable",
        )


def test_real_b1_parse_and_save_revision_is_readable(tmp_path: Path):
    resolved, _selector, _snapshot, _metadata = _completed_find_jobs(tmp_path)
    posting = SelectedPosting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/101",
        url="https://boards.greenhouse.io/acme/jobs/101",
        content_sha256="sha256:" + "a" * 64,
        role_match=True,
    )
    pinned = PinnedResume("record_00000000-0000-4000-8000-000000000001", "revision_00000000-0000-4000-8000-000000000002", "sha256:" + "b" * 64)
    producer = Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "deterministic")
    model_result = _AssessmentAdapter().invoke(SimpleNamespace())
    raw = json.loads(model_result.output_text)
    parsed = parse_assessment_proposal({**raw, "posting": posting.to_json(), "proposal_revision_ref": None})
    assert isinstance(parsed, AssessmentResult)
    ref = save_assessment_revision(home_root=tmp_path / "home", target=resolved, posting=posting, result=parsed, producer=producer, pinned_resume=pinned)
    record_id, revision_id = ref.split("/")[-3], ref.split("/")[-1].removesuffix(".json")
    saved = read_proposal_revision(resolved=resolved, record_id=record_id, revision_id=revision_id)
    assert saved["assessment"]["posting"] == posting.to_json()


# --- assess_node: P2 (U25 posting text, U22 tolerant parse + isolation, U12 sponsorship) ---


class _ScriptedPort:
    """A model port whose ``invoke`` returns one scripted result per call.

    Raising an entry (instead of returning a result) simulates a model
    invocation failure for that call.
    """

    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success",
            output_text=item,
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2),
            cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)


def _assess_fixture(tmp_path: Path) -> tuple[dict, Path]:
    """A resolved workpad + a pinned resume, ready for a direct assess_node call."""
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    # U2: assess_node resolves the sealed "ollama_local" enum to whichever
    # configured target uses that adapter kind, so the fixture needs a real
    # one (named however setup would name it) even though the model call
    # itself is scripted via the resolve_model_adapter monkeypatch below.
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            endpoints=(
                Endpoint(name="offline", adapter="deterministic"),
                Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
            ),
            model_targets=(
                ConfigModelTarget(
                    name="offline-default",
                    endpoint="offline",
                    model="fixture-v1",
                    capabilities=("text",),
                    max_output_tokens=64,
                ),
                ConfigModelTarget(
                    name="ollama-default",
                    endpoint="ollama",
                    model="fixture-model",
                    capabilities=("text",),
                    max_output_tokens=512,
                    model_digest="sha256:" + "c" * 64,
                ),
            ),
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 40))
    created = create_offline(
        home_root=home, requested_target=target, name="assess-fixture", open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home, requested_target=target, proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    gig_id = created.gig_id
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    migrate_workpad_layout(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)

    source = tmp_path / "resume.md"
    source.write_bytes(b"Built and operated Python backend services for 6 years.\n")
    imported = import_reference(
        home_root=home, requested_target=target, gig_id=gig_id, kind="resume",
        source=source, operation_key="assess-fixture-resume-import",
    )
    revision = create_record(
        home_root=home, requested_target=target, gig_id=gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"}, origin="imported",
        operation_key="assess-fixture-resume-record",
    )
    pinned = PinnedResume(revision.record_id, revision.revision_id, digest_imported_bytes(source.read_bytes()))
    config = load_config(home)
    return {"home": home, "target": target, "gig_id": gig_id, "resolved": resolved, "pinned": pinned, "config": config}, target


def _posting(
    *,
    normalized_url: str,
    text: str | None,
    location: str = "Denver, CO",
    sponsorship: SponsorshipStatus | None = None,
    company: str = "Acme",
    title: str = "Software Engineer",
) -> PostingRow:
    return PostingRow(
        url=normalized_url,
        normalized_url=normalized_url,
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company=company,
        title=title,
        location=location,
        published_at="2026-09-20T00:00:00Z",
        content_sha256="sha256:" + "a" * 64,
        source_kind=SourceKind.ATS,
        query_key="software-engineer",
        text=text,
        sponsorship=sponsorship,
    )


def _run_assess(
    fixture: dict,
    target: Path,
    *,
    run_id: str,
    postings: list[PostingRow],
    outputs: list[object],
    monkeypatch: pytest.MonkeyPatch,
    visa_sponsorship_required: bool | None = None,
    countries: list[str] | None = None,
    outcomes: dict[str, str] | None = None,
    selected_postings: list[PostingRow] | None = None,
    selection_cap: int = 10,
):
    """Run assess_node directly against a hand-written acquire batch.

    ``postings`` is every row in the acquire batch (in order); ``outcomes``
    maps a normalized_url to its RowOutcome string (default "new" for any
    posting not named). ``selected_postings`` defaults to ``postings`` (every
    row selected) when omitted; pass a subset to exercise non-selected
    candidates (over_cap / exclusion_reason labeling).
    """
    run_dir = target / "runs" / run_id
    (run_dir / "outputs").mkdir(parents=True)
    outcomes = outcomes or {}
    (run_dir / "outputs" / "acquire.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"posting": posting.to_json(), "outcome": outcomes.get(posting.normalized_url, "new")}
                    for posting in postings
                ]
            }
        )
    )
    if visa_sponsorship_required is not None or countries is not None:
        (run_dir / "sealed").mkdir(parents=True)
        (run_dir / "sealed" / "find-jobs-run-input.json").write_text(
            json.dumps(
                {
                    "schema_version": "scout-find-jobs-run-input:1",
                    "config": {
                        "schema_version": "find-jobs-config:1",
                        "roles": ["Software Engineer"],
                        "merged_queries": ["software engineer"],
                        "location": None,
                        "remote": True,
                        "published_after": None,
                        "sources": {"exa": True, "ats": True, "hiringcafe": False},
                        "default_assess_cap": 10,
                        "default_model_target": "ollama_local",
                        "visa_sponsorship_required": bool(visa_sponsorship_required),
                        **({"countries": countries} if countries else {}),
                    },
                    "config_digest": "sha256:" + "0" * 64,
                    "selection_cap": selection_cap,
                    "selection_rule": "new_or_edited_role_match",
                    "model_target": "ollama_local",
                    "pinned_resume": fixture["pinned"].to_json(),
                }
            )
        )
        # config_digest must equal the actual config digest; recompute in place.
        from gigai.scout.find_jobs.contracts import FindJobsConfig

        payload = json.loads((run_dir / "sealed" / "find-jobs-run-input.json").read_text())
        payload["config_digest"] = FindJobsConfig.from_json(payload["config"]).digest()
        (run_dir / "sealed" / "find-jobs-run-input.json").write_text(json.dumps(payload))

    selected_source = selected_postings if selected_postings is not None else postings
    selected = tuple(
        SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True) for posting in selected_source
    )
    context = NodeContext(
        run_id=run_id,
        project_id=fixture["resolved"].project_id,
        gig_id=fixture["gig_id"],
        graph_id="graph_find_jobs_test",
        graph_version=1,
        goal_slug="assess",
        manifest_digest="sha256:" + "0" * 64,
        operation_key="assess-test",
        target_observation_digest="sha256:" + "0" * 64,
        workpad_path=str(fixture["resolved"].path),
        redeemed_consent_ref="none",
        model_target=ModelTarget.OLLAMA_LOCAL,
    )
    assess_input = AssessInput(
        acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=selected,
        selection_cap=selection_cap,
        selection_reasons=tuple(
            SelectionReason(posting.normalized_url, SelectionReasonCode.NEW) for posting in selected_source
        ),
        pinned_resume=fixture["pinned"],
        target=str(target),
        model_target=ModelTarget.OLLAMA_LOCAL,
        answer_association_version="scout-answer-association:1",
    )
    binding = _ScriptedBinding(outputs)
    monkeypatch.setattr(
        "gigai.scout.proposal_execution.resolve_model_adapter",
        lambda config, adapter_target, **_kwargs: binding,
    )
    output = assess_node(context, assess_input, home_root=fixture["home"], target=target, config=fixture["config"])
    return output, binding


def test_posting_text_reaches_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/101",
        text="We need 5+ years of Python. Remote OK. No visa sponsorship available for this role.",
    )
    good = json.dumps({
        "matrix": [{"requirement": "5+ years Python", "resume_evidence": ["Built Python services for 6 years"], "status": "met"}],
        "suggestions": ["Call out the backend ownership."],
        "questions": [],
        "sponsorship": "not_offered",
    })
    output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000101",
        postings=[posting], outputs=[good], monkeypatch=monkeypatch,
    )
    assert len(output.assessments) == 1
    assert not output.not_assessed
    assert posting.text in binding.port.prompts[0]
    assert posting.title in binding.port.prompts[0]
    assert posting.company in binding.port.prompts[0]


def test_missing_posting_text_is_not_assessed_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(normalized_url="https://boards.greenhouse.io/acme/jobs/202", text=None)
    output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000102",
        postings=[posting], outputs=[], monkeypatch=monkeypatch,
    )
    assert not output.assessments
    assert len(output.not_assessed) == 1
    assert output.not_assessed[0].reason is NotAssessedReason.FAILED
    assert binding.port.prompts == []


def test_visa_sponsorship_required_reaches_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/303",
        text="We need 5+ years of Python.",
    )
    good = json.dumps({
        "matrix": [{"requirement": "5+ years Python", "resume_evidence": ["Built Python services for 6 years"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })
    _output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000103",
        postings=[posting], outputs=[good], monkeypatch=monkeypatch, visa_sponsorship_required=True,
    )
    assert "visa sponsorship required = yes" in binding.port.prompts[0]


def test_string_resume_evidence_is_normalized_and_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """U22 real failure: Codex returned resume_evidence as a string, not an array."""
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/404",
        text="We need Kubernetes production experience.",
    )
    sloppy = json.dumps({
        "matrix": [{"requirement": "Kubernetes", "resume_evidence": "Ran production Kubernetes clusters", "status": "yes"}],
        "suggestions": "Mention the cluster count.",
        "questions": None,
        "sponsorship": "not offered",
    })
    output, _binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000104",
        postings=[posting], outputs=[sloppy], monkeypatch=monkeypatch,
    )
    assert len(output.assessments) == 1
    assert not output.not_assessed
    assessed = output.assessments[0]
    assert assessed.matrix[0].resume_evidence == ("Ran production Kubernetes clusters",)
    assert assessed.matrix[0].status is MatrixStatus.MET
    assert assessed.sponsorship is SponsorshipStatus.NOT_OFFERED


def test_verdict_carrying_answer_reaches_the_saved_assessment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P2 (v0.1.9): a full S29 r1-shaped answer (verdict, class, structured
    questions) survives assess_node end to end -- normalize, validate, save."""
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/909",
        text="We need GCP experience.",
    )
    verdict_answer = json.dumps({
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"},
            {"requirement": "GCP", "class": "askable", "resume_evidence": [], "status": "unclear"},
        ],
        "suggestions": [],
        "questions": [
            {"question_id": "cloud:gcp", "question": "Have you used GCP?", "requirement": "GCP"}
        ],
        "not_a_match_reason": None,
    })
    output, _binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000109",
        postings=[posting], outputs=[verdict_answer], monkeypatch=monkeypatch,
    )
    assert len(output.assessments) == 1
    assessed = output.assessments[0]
    assert assessed.verdict is Verdict.PENDING_USER_ANSWERS
    assert assessed.matrix[1].requirement_class is RequirementClass.ASKABLE
    assert assessed.matrix[1].status is MatrixStatus.UNCLEAR
    assert assessed.structured_questions == (AssessmentQuestion("cloud:gcp", "Have you used GCP?", "GCP"),)
    assert assessed.questions == ("Have you used GCP?",)  # C9: the shipped UI keeps reading strings
    assert assessed.proposal_revision_ref  # saved via save_assessment_revision, same as any other result


def test_garbage_answer_retries_once_then_not_assessed_while_others_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, target = _assess_fixture(tmp_path)
    bad_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/505",
        text="We need Rust experience.",
    )
    good_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/606",
        text="We need Go experience.",
    )
    garbage = "not json at all, sorry"
    still_garbage = "still not json"
    good = json.dumps({
        "matrix": [{"requirement": "Go", "resume_evidence": ["Built Go services"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })
    output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000105",
        postings=[bad_posting, good_posting], outputs=[garbage, still_garbage, good], monkeypatch=monkeypatch,
    )
    assert len(binding.port.prompts) == 3  # 2 attempts for bad_posting + 1 for good_posting
    assert "did not match the required JSON shape" in binding.port.prompts[1]
    assert len(output.not_assessed) == 1
    assert output.not_assessed[0].posting.normalized_url == bad_posting.normalized_url
    assert output.not_assessed[0].reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert len(output.assessments) == 1
    assert output.assessments[0].posting.normalized_url == good_posting.normalized_url


def test_all_postings_failing_at_the_model_raises_a_specific_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/707",
        text="We need Elixir experience.",
    )
    garbage = "not json at all"
    with pytest.raises(ScoutProposalExecutionError, match="every selected posting failed"):
        _run_assess(
            fixture, target, run_id="run_00000000-0000-4000-8000-000000000106",
            postings=[posting], outputs=[garbage, garbage], monkeypatch=monkeypatch,
        )


def test_fenced_json_output_is_extracted_before_normalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/808",
        text="We need SQL experience.",
    )
    fenced = "Here is my answer:\n```json\n" + json.dumps({
        "matrix": [{"requirement": "SQL", "resume_evidence": ["Wrote SQL migrations"], "status": "partially"}],
        "suggestions": [],
        "questions": [],
    }) + "\n```\nLet me know if you need more."
    output, _binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000107",
        postings=[posting], outputs=[fenced], monkeypatch=monkeypatch,
    )
    assert len(output.assessments) == 1
    # P2 (v0.1.9): the normalizer maps the old prompt's "partially" synonym
    # onto the new prompt's "unclear" word (plan section "P2", operator
    # answer 10) -- the old PARTIAL enum member is kept only to parse an
    # already-stored old-shape result, never emitted by live normalization.
    assert output.assessments[0].matrix[0].status is MatrixStatus.UNCLEAR


def test_model_denied_is_not_assessed_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/909",
        text="We need Java experience.",
    )
    denied = ModelInvocationError("credential missing")
    denied.code = "model_denied"
    output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000108",
        postings=[posting], outputs=[denied], monkeypatch=monkeypatch,
    )
    assert len(binding.port.prompts) == 1
    assert len(output.not_assessed) == 1
    assert output.not_assessed[0].reason is NotAssessedReason.MODEL_DENIED


def test_candidate_partition_mixes_assessed_over_cap_and_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coordinator-specified mix: in-cap assessed, over_cap, location_mismatch,
    sponsorship_excluded, and an unchanged row excluded from candidates entirely.
    """
    fixture, target = _assess_fixture(tmp_path)

    assessed_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/1001",
        text="We need Go experience.",
    )
    over_cap_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/1002",
        text="We need Rust experience.",
        # wire-selection: a distinct company+title from assessed_posting so
        # this row is genuinely over the cap once select_for_assessment is
        # wired in, not a "duplicate" of assessed_posting (the shared "Acme"
        # / "Software Engineer" defaults would otherwise dedupe the two).
        # Title keeps "Software Engineer" so it still role-matches the
        # sealed config's roles=["Software Engineer"].
        company="Globex",
        title="Senior Software Engineer",
    )
    location_mismatch_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/1003",
        text="We need Java experience.",
        location="Bengaluru, India",
    )
    sponsorship_excluded_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/1004",
        text="We need C++ experience. No visa sponsorship available for this role.",
        sponsorship=SponsorshipStatus.NOT_OFFERED,
    )
    unchanged_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/1005",
        text="We need PHP experience.",
    )

    good = json.dumps({
        "matrix": [{"requirement": "Go", "resume_evidence": ["Built Go services"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })

    output, binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000201",
        postings=[
            assessed_posting,
            over_cap_posting,
            location_mismatch_posting,
            sponsorship_excluded_posting,
            unchanged_posting,
        ],
        outcomes={unchanged_posting.normalized_url: "unchanged"},
        # Only the first posting is selected (as acquire's own cap=1 loop
        # would have chosen it first); the rest are role-matched new/edited
        # candidates acquire's loop passed over for a specific reason.
        selected_postings=[assessed_posting],
        selection_cap=1,
        countries=["US"],
        visa_sponsorship_required=True,
        outputs=[good],
        monkeypatch=monkeypatch,
    )

    by_url = {row.posting.normalized_url: row for row in output.not_assessed}
    assert len(output.assessments) == 1
    assert output.assessments[0].posting.normalized_url == assessed_posting.normalized_url
    assert by_url[over_cap_posting.normalized_url].reason is NotAssessedReason.OVER_CAP
    assert by_url[location_mismatch_posting.normalized_url].reason is NotAssessedReason.LOCATION_MISMATCH
    assert by_url[sponsorship_excluded_posting.normalized_url].reason is NotAssessedReason.SPONSORSHIP_EXCLUDED
    # uat-bug-009: an UNCHANGED row is no longer excluded from candidates
    # outright -- this fixture has no `outputs/assess.json` anywhere on disk
    # (no earlier run ever produced a successful assessment for it), so it's
    # still eligible for selection under the cap, same as any other
    # candidate. It shares its default company/title ("Acme"/"Software
    # Engineer") with `assessed_posting`, which is already selected/in-cap,
    # so `select_for_assessment` correctly dedupes it as DUPLICATE rather
    # than assessing it a second time or counting it as a fresh OVER_CAP.
    assert by_url[unchanged_posting.normalized_url].reason is NotAssessedReason.DUPLICATE

    # candidate_rows: complete, non-overlapping partition (T4). uat-bug-009:
    # the unchanged row IS a candidate now (no prior successful assessment
    # exists for it anywhere), unlike before this fix.
    candidate_urls = {row.posting.normalized_url for row in output.candidate_rows}
    assert candidate_urls == {
        assessed_posting.normalized_url,
        over_cap_posting.normalized_url,
        location_mismatch_posting.normalized_url,
        sponsorship_excluded_posting.normalized_url,
        unchanged_posting.normalized_url,
    }
    assessed_urls = {a.posting.normalized_url for a in output.assessments}
    not_assessed_urls = set(by_url)
    assert assessed_urls | not_assessed_urls == candidate_urls
    assert not (assessed_urls & not_assessed_urls)

    # The frozen contract's own T4 partition validation (candidate rows
    # complete + non-overlapping) must accept this output on a round-trip.
    from gigai.scout.find_jobs.contracts import AssessOutput

    assert AssessOutput.from_json(output.to_json()) == output


def test_candidate_dropped_as_duplicate_is_labelled_duplicate_not_over_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wire-selection (B2): a candidate that select_for_assessment drops for
    being a near-identical duplicate of the selected posting -- same
    company + normalized title + country -- must be labelled
    NotAssessedReason.DUPLICATE, not the OVER_CAP fallback the un-wired
    code used for every unselected row regardless of why it was dropped.
    """
    fixture, target = _assess_fixture(tmp_path)

    assessed_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/2001",
        text="We need Go experience.",
    )
    duplicate_posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/2002",
        text="We need Rust experience.",
        # Same company + title + location as assessed_posting (and the
        # fixture's shared published_at) -- select_for_assessment's dedupe
        # key, so this is a genuine duplicate, not an over-cap drop.
    )

    good = json.dumps({
        "matrix": [{"requirement": "Go", "resume_evidence": ["Built Go services"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })

    output, _binding = _run_assess(
        fixture, target, run_id="run_00000000-0000-4000-8000-000000000202",
        postings=[assessed_posting, duplicate_posting],
        selected_postings=[assessed_posting],
        selection_cap=1,
        countries=["US"],
        visa_sponsorship_required=False,
        outputs=[good],
        monkeypatch=monkeypatch,
    )

    by_url = {row.posting.normalized_url: row for row in output.not_assessed}
    assert len(output.assessments) == 1
    assert by_url[duplicate_posting.normalized_url].reason is NotAssessedReason.DUPLICATE


# --- U2: sealed enum -> configured target resolution (0.1.8.1 UAT) ---


def test_resolve_configured_target_name_for_adapter_finds_setup_named_target(tmp_path: Path) -> None:
    """setup names targets "codex-default" etc; the sealed enum is "codex_cli"."""
    from gigai.scout.proposal_execution import _resolve_configured_target_name_for_adapter

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512),
        ),
    )
    assert _resolve_configured_target_name_for_adapter(config, "codex_cli") == "codex-default"


def test_resolve_configured_target_name_for_adapter_fails_loudly_when_unmatched(tmp_path: Path) -> None:
    from gigai.scout.proposal_execution import (
        ScoutProposalExecutionError,
        _resolve_configured_target_name_for_adapter,
    )

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    with pytest.raises(ScoutProposalExecutionError, match="no configured model target uses adapter 'openrouter_api'"):
        _resolve_configured_target_name_for_adapter(config, "openrouter_api")


def test_resolve_configured_target_name_for_adapter_fails_loudly_when_ambiguous(tmp_path: Path) -> None:
    """No silent fallback: two targets of the same adapter kind is also an error."""
    from gigai.scout.proposal_execution import (
        ScoutProposalExecutionError,
        _resolve_configured_target_name_for_adapter,
    )

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="codex-alt", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512),
            ConfigModelTarget("codex-alt-default", "codex-alt", "default", ("text",), 512),
        ),
    )
    with pytest.raises(ScoutProposalExecutionError, match="multiple configured model targets use adapter 'codex_cli'"):
        _resolve_configured_target_name_for_adapter(config, "codex_cli")


def test_resolve_configured_target_name_for_adapter_exact_name_wins_over_ambiguous_scan(
    tmp_path: Path,
) -> None:
    """uat-bug-005 repro: the 0.1.8.x README told users to create a target
    literally named "codex_cli" alongside setup's own "codex-default", both
    on the same codex endpoint. The sealed value "codex_cli" must resolve to
    the exactly-named target instead of raising the adapter-scan ambiguity
    error -- today (before the fix) this raises "multiple configured model
    targets use adapter 'codex_cli'"."""
    from gigai.scout.proposal_execution import _resolve_configured_target_name_for_adapter

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512),
            ConfigModelTarget("codex_cli", "codex", "default", ("text",), 512),
        ),
    )
    assert _resolve_configured_target_name_for_adapter(config, "codex_cli") == "codex_cli"


@pytest.mark.parametrize(
    "sealed_value,exact_endpoint_name,exact_adapter,credential,base_url",
    [
        ("codex_cli", "codex", "codex_cli", None, None),
        ("ollama_local", "ollama", "ollama_local", None, "http://127.0.0.1:11434"),
        ("openrouter_api", "openrouter", "openrouter_api", "openrouter-api", None),
    ],
)
def test_resolve_configured_target_name_for_adapter_exact_name_wins_for_every_sealed_value(
    tmp_path: Path,
    sealed_value: str,
    exact_endpoint_name: str,
    exact_adapter: str,
    credential: str | None,
    base_url: str | None,
) -> None:
    """Same exact-name-wins path for every sealed adapter kind, not just codex_cli."""
    from gigai.scout.proposal_execution import _resolve_configured_target_name_for_adapter

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(
            (CredentialReference(name=credential, kind="environment", reference="OPENROUTER_API_KEY"),)
            if credential
            else ()
        ),
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name=exact_endpoint_name, adapter=exact_adapter, credential=credential, base_url=base_url),
            Endpoint(
                name=f"{exact_endpoint_name}-alt",
                adapter=exact_adapter,
                credential=credential,
                base_url=base_url,
            ),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget(
                f"{exact_endpoint_name}-default",
                exact_endpoint_name,
                "default",
                ("text",),
                512,
                model_digest=("sha256:" + "c" * 64) if exact_adapter == "ollama_local" else None,
            ),
            ConfigModelTarget(
                sealed_value,
                f"{exact_endpoint_name}-alt",
                "default",
                ("text",),
                512,
                model_digest=("sha256:" + "d" * 64) if exact_adapter == "ollama_local" else None,
            ),
        ),
    )
    assert _resolve_configured_target_name_for_adapter(config, sealed_value) == sealed_value


def test_resolve_configured_target_name_for_adapter_ambiguous_error_names_targets_and_fix(
    tmp_path: Path,
) -> None:
    """When several enabled targets share an adapter and none is named the
    sealed value, the error must name the targets and how to fix it (disable
    or remove one via config.toml or `gigai setup`)."""
    from gigai.scout.proposal_execution import (
        ScoutProposalExecutionError,
        _resolve_configured_target_name_for_adapter,
    )

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="codex-alt", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512),
            ConfigModelTarget("codex-alt-default", "codex-alt", "default", ("text",), 512),
        ),
    )
    with pytest.raises(ScoutProposalExecutionError) as excinfo:
        _resolve_configured_target_name_for_adapter(config, "codex_cli")
    message = str(excinfo.value)
    assert "codex-default" in message
    assert "codex-alt-default" in message
    assert "config.toml" in message or "gigai setup" in message


def test_resolve_configured_target_name_for_adapter_ignores_disabled_targets(tmp_path: Path) -> None:
    from gigai.scout.proposal_execution import _resolve_configured_target_name_for_adapter

    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="codex-alt", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512, enabled=False),
            ConfigModelTarget("codex-alt-default", "codex-alt", "default", ("text",), 512),
        ),
    )
    assert _resolve_configured_target_name_for_adapter(config, "codex_cli") == "codex-alt-default"


def test_assess_node_resolves_the_real_adapter_without_a_literal_enum_named_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: assess_node must not call resolve_model_adapter with the
    literal enum string "ollama_local" -- it must pass the configured
    target's own name ("ollama-default" in this fixture)."""
    fixture, target = _assess_fixture(tmp_path)
    posting = _posting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/u2",
        text="We need Python experience.",
    )
    good = json.dumps({
        "matrix": [{"requirement": "Python", "resume_evidence": ["Built APIs"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })
    run_id = "run_00000000-0000-4000-8000-000000000901"
    run_dir = target / "runs" / run_id
    (run_dir / "outputs").mkdir(parents=True)
    (run_dir / "outputs" / "acquire.json").write_text(
        json.dumps({"rows": [{"posting": posting.to_json(), "outcome": "new"}]})
    )
    selected = (SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),)
    context = NodeContext(
        run_id=run_id,
        project_id=fixture["resolved"].project_id,
        gig_id=fixture["gig_id"],
        graph_id="graph_find_jobs_test",
        graph_version=1,
        goal_slug="assess",
        manifest_digest="sha256:" + "0" * 64,
        operation_key="assess-test",
        target_observation_digest="sha256:" + "0" * 64,
        workpad_path=str(fixture["resolved"].path),
        redeemed_consent_ref="none",
        model_target=ModelTarget.OLLAMA_LOCAL,
    )
    assess_input = AssessInput(
        acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=selected,
        selection_cap=10,
        selection_reasons=(SelectionReason(posting.normalized_url, SelectionReasonCode.NEW),),
        pinned_resume=fixture["pinned"],
        target=str(target),
        model_target=ModelTarget.OLLAMA_LOCAL,
        answer_association_version="scout-answer-association:1",
    )
    seen_targets: list[str] = []

    def _fake_resolve(config, adapter_target, **_kwargs):
        seen_targets.append(adapter_target)
        return _ScriptedBinding([good])

    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", _fake_resolve)
    output = assess_node(context, assess_input, home_root=fixture["home"], target=target, config=fixture["config"])
    assert seen_targets == ["ollama-default"]
    assert len(output.assessments) == 1
