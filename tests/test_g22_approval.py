from __future__ import annotations

from pathlib import Path
import subprocess
import uuid

from gigai.canonical import digest_imported_bytes, parse_json_bytes
from gigai.lifecycle import approve_interview_session, start_interview
from gigai.proposal_interview import answer_question
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, shell=False)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    assert initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    ).project_id == PROJECT_ID
    return home, target


def _uuids() -> callable:
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 64))
    return lambda: next(values)


def test_operator_approval_seals_the_proposal_without_starting_a_run(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    reference = tmp_path / "source.txt"
    reference.write_bytes(b"source bytes\n")
    values = _uuids()
    started = start_interview(
        home_root=home,
        requested_target=target,
        name="approved-interview",
        request="Prepare a bounded local proposal.",
        reference_paths=(reference,),
        uuid_factory=values,
    )
    session = started.session
    session = answer_question(session, "scope", "Prepare a bounded local proposal.")
    session = answer_question(session, "references", [session.references[0].reference_id])
    session = answer_question(session, "effect", "write_workpad")
    session = answer_question(session, "privacy", "local_only")
    session = answer_question(session, "capability", "none")
    assert session.state == "proposal_ready"

    approved = approve_interview_session(
        home_root=home,
        requested_target=target,
        start=started,
        session=session,
        uuid_factory=values,
    )

    assert approved.state == "approved"
    assert approved.proposal_id is not None
    proposal_bytes = (started.workpad / "manifests/gig-proposal.json").read_bytes()
    assert validate_serialized_contract("gig-proposal.schema.json", proposal_bytes).valid
    proposal = parse_json_bytes(proposal_bytes)
    assert proposal["status"] == "approved"
    assert (started.workpad / "manifests/active-gig-version.json").is_file()
    assert not (started.workpad / "runs").exists()
    assert digest_imported_bytes(reference.read_bytes()) == started.session.references[0].content_sha256
    assert _git(started.workpad, "tag", "--list", "gig-v000001").stdout.strip() == "gig-v000001"
