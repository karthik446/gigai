from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gigai.canonical import parse_json_bytes
from gigai.config import load_config
from gigai.lifecycle import (
    build_interview_proposal,
    persist_interview_session,
    record_builder_state,
    start_interview,
)
from gigai.proposal_interview import (
    InterviewHTTPServer,
    block_session,
    request_revision,
)
from gigai.question_generation import G27_DISCOVERY_PROMPT, generate_model_questions
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def test_builder_review_can_revise_rebuild_and_reject_without_activation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    started = start_interview(
        home_root=home,
        requested_target=target,
        name="review-actions",
        request="Review this repository.",
        reference_paths=(),
    )
    reference_bytes = dict(started.reference_bytes)
    builder_review: dict[str, object] = {}
    built_proposal_id: str | None = None

    def on_questions(session):
        question_ids = {item.question_id for item in session.questions}
        if "desired-outputs" in question_ids:
            return session
        return generate_model_questions(
            config=load_config(home),
            model_target="offline-default",
            session=session,
            reference_bytes=reference_bytes,
            prompt_name=G27_DISCOVERY_PROMPT,
        )

    def on_build(session):
        nonlocal built_proposal_id
        built = build_interview_proposal(
            home_root=home,
            requested_target=target,
            start=started,
            session=session,
            model_target="offline-default",
            reference_bytes=reference_bytes,
        )
        proposal = parse_json_bytes(
            (started.workpad / "manifests/gig-proposal.json").read_bytes()
        )
        built_proposal_id = str(proposal["proposal_id"])
        draft = parse_json_bytes(
            (started.workpad / "manifests/proposal-draft-manifest.json").read_bytes()
        )
        builder_review.clear()
        builder_review.update(draft["research"])
        return built

    def on_revision(session):
        nonlocal built_proposal_id
        built_proposal_id = None
        revised = request_revision(session)
        record_builder_state(
            start=started,
            session=revised,
            state="revised",
            terminal_reason=None,
            transition="gig_builder_revised",
        )
        return revised

    def on_rejection(session):
        rejected = block_session(session, "operator_rejected")
        record_builder_state(
            start=started,
            session=rejected,
            state="rejected",
            terminal_reason="operator_rejected",
            transition="gig_builder_rejected",
        )
        return rejected

    server = InterviewHTTPServer(
        started.session,
        on_session=lambda session: persist_interview_session(
            workpad=started.workpad,
            project_id=started.project_id,
            gig_id=started.gig_id,
            session=session,
        ),
        on_questions=on_questions,
        on_build=on_build,
        on_revision=on_revision,
        on_rejection=on_rejection,
        builder_review=builder_review,
        builder_mode=True,
    ).start()
    try:
        endpoint = f"{server.url}/events"

        def send(event: str, **values: object) -> dict[str, object]:
            snapshot = parse_json_bytes(
                (started.workpad / "manifests/proposal-interview.json").read_bytes()
            )
            payload = {
                "event": event,
                "revision": snapshot["revision"],
                "sequence": len(snapshot["events"]) + 1,
                **values,
            }
            try:
                with urlopen(
                    Request(
                        endpoint,
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=5,
                ) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                raise AssertionError(error.read().decode()) from error

        send("answer", question_id="scope", value="Review this repository")
        send("answer", question_id="desired-outputs", value=["resume"])
        send("answer", question_id="changing-context", value="The repository changes between Runs")
        send("build")
        first_proposal_id = built_proposal_id
        assert first_proposal_id is not None

        assert send("revise")["state"] == "questions_pending"
        send("answer", question_id="scope", value="Review and explain the important work")
        send("build")
        assert built_proposal_id is not None
        assert built_proposal_id != first_proposal_id

        assert send("reject")["state"] == "blocked"
        assert server.wait(timeout=1).state == "blocked"
        builder_snapshot = parse_json_bytes(
            (started.workpad / "manifests/gig-builder-session.json").read_bytes()
        )
        assert builder_snapshot["state"] == "rejected"
        assert builder_snapshot["terminal_reason"] == "operator_rejected"
        assert not (started.workpad / "manifests/active-gig-version.json").exists()
    finally:
        server.close()
