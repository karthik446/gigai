from __future__ import annotations

import json
from pathlib import Path
import uuid
from urllib.request import Request, urlopen

from gigai.lifecycle import approve_interview_session, persist_interview_session, start_interview
from gigai.proposal_interview import InterviewHTTPServer
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _uuids() -> callable:
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 64))
    return lambda: next(values)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    return home, target


def test_http_answers_then_operator_approval_reaches_terminal_lifecycle(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    reference = tmp_path / "source.txt"
    reference.write_bytes(b"HTTP-selected bytes\n")
    values = _uuids()
    started = start_interview(
        home_root=home,
        requested_target=target,
        name="http-approved",
        request="Approve this local proposal.",
        reference_paths=(reference,),
        uuid_factory=values,
    )
    server = InterviewHTTPServer(
        started.session,
        on_session=lambda session: persist_interview_session(
            workpad=started.workpad,
            project_id=started.project_id,
            gig_id=started.gig_id,
            session=session,
            uuid_factory=values,
        ),
        on_approval=lambda session: approve_interview_session(
            home_root=home,
            requested_target=target,
            start=started,
            session=session,
            uuid_factory=values,
        ),
    ).start()
    try:
        endpoint = f"{server.url}/events"

        def send(question_id: str, value: object) -> None:
            request = Request(
                endpoint,
                data=json.dumps({"event": "answer", "question_id": question_id, "value": value}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                assert response.status == 200

        send("scope", "Approve this local proposal.")
        send("references", [started.session.references[0].reference_id])
        send("effect", "write_workpad")
        send("privacy", "local_only")
        send("capability", "none")

        with urlopen(
            Request(endpoint, data=b'{"event":"approve"}', headers={"Content-Type": "application/json"}, method="POST"),
            timeout=4,
        ) as response:
            assert json.loads(response.read())["state"] == "approved"
        assert server.wait(timeout=1).state == "approved"
        assert (started.workpad / "manifests/active-gig-version.json").is_file()
        assert not (started.workpad / "runs").exists()
    finally:
        server.close()
