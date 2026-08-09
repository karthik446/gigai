from __future__ import annotations

import json
from pathlib import Path
import uuid

from gigai.canonical import digest_imported_bytes
from gigai.proposal_interview import ReferenceDecision, answer_question, approve_session, build_session
from gigai.question_generation import generate_model_questions
from gigai.setup import build_config, run_setup


CORPUS_PATH = Path(__file__).parents[1] / "docs/development/evidence/phase-3/S22-01/evaluation-corpus.json"
SHA = "sha256:" + "1" * 64
REQUESTS = {
    "repository-feature": "Review a repository feature proposal against selected local source files.",
    "resume-tailoring": "Tailor a resume draft to a selected job description.",
    "reference-synchronization": "Compare two selected references and prepare a synchronized workpad draft.",
    "tabular-finance": "Analyze a local tabular finance fixture without sending it to a provider.",
}
EXPECTED_QUESTIONS = {"scope", "references", "effect", "privacy", "capability"}


def _id(prefix: str, index: int) -> str:
    return f"{prefix}_{uuid.UUID(f'00000000-0000-4000-8000-{index:012x}')}"


def test_s22_question_quality_corpus_reaches_expected_bounded_outcomes(tmp_path: Path) -> None:
    manifest = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    outcomes: dict[str, dict[str, object]] = {}

    for index, case in enumerate(manifest["cases"], start=1):
        request = REQUESTS[case["case_id"]]
        selected_bytes = f"{case['case_id']} selected reference\n".encode()
        reference = ReferenceDecision(
            _id("ref", index * 2), digest_imported_bytes(selected_bytes)
        )
        session = build_session(
            session_id=_id("session", index * 2),
            project_id=_id("project", 100 + index),
            gig_id=_id("gig", 200 + index),
            request_kind=case["case_id"],
            request_artifact={
                "path": f"review/{case['case_id']}/request.txt",
                "content_sha256": SHA,
                "media_type": "text/plain",
                "size_bytes": len(request.encode()),
            },
            request_sha256=SHA,
            references=(reference,),
            now="2026-08-09T00:00:00Z",
        )
        session = answer_question(session, "scope", request)
        session = answer_question(session, "references", [reference.reference_id])
        session = generate_model_questions(
            config=config,
            model_target="offline-default",
            session=session,
            reference_bytes={reference.reference_id: selected_bytes},
        )
        session = answer_question(session, "effect", "read_local")
        session = answer_question(session, "privacy", "local_only")
        session = answer_question(session, "capability", "none")
        session = answer_question(session, "operator-confirmation", True)
        approved = approve_session(
            session,
            proposal_id=_id("gp", 300 + index),
            proposal_sha256=SHA,
        )
        assert approved.state == "approved"
        question_ids = {question.question_id for question in approved.questions}
        assert EXPECTED_QUESTIONS.issubset(question_ids)
        outcomes[case["case_id"]] = {
            "state": approved.state,
            "question_ids": sorted(question_ids),
            "provider_access": "forbidden",
            "target_effects": "forbidden",
        }

    assert set(outcomes) == {case["case_id"] for case in manifest["cases"]}
    assert all(item["state"] == "approved" for item in outcomes.values())
