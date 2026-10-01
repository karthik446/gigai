"""uat-bug-020: the one resume import path and the two routes the wizard uses.

``gigai.scout.resume_import`` is what ``gigai scout resume add`` and ``POST
/api/resumes`` both call. Against a real, journal-authoritative workpad
(``tests.support.scout_profile_fixtures.build_gig_with_resume``, which
imports its resume with the CLI's own operation keys) and the real
``ScoutFindJobsBackend``/``serve()`` server:

* resume bytes held in memory become a stored resume whose content is
  exactly those bytes; the temporary file they pass through is gone
  afterwards, also when the import refuses them;
* the same bytes again store nothing (idempotent by content), also under
  another name, and also when they are the resume a file import stored;
* a file name from a browser becomes one the import accepts;
* ``POST /api/resumes`` answers with ids only and logs neither the resume
  nor its file name;
* ``GET /api/secrets/status`` answers booleans, from the environment and the
  home's secret store, and never reads the operator's default home.
"""

from __future__ import annotations

import base64
import logging
import tempfile
import threading
from pathlib import Path

import httpx
import pytest

from gigai import secrets_store
from gigai.private_records import PrivateRecordError, list_imports, read_record
from gigai.scout import resume_import
from gigai.scout.find_jobs.api import resumes as resumes_api
from gigai.scout.find_jobs.api import secrets_status
from gigai.scout.find_jobs.present_api import LOGGER_NAME, ScoutFindJobsBackend, serve
from gigai.scout.resume_import import (
    PASTED_RESUME_FILE_NAME,
    RESUME_MAX_BYTES,
    ResumeImportError,
    import_resume_bytes,
    import_resume_file,
    safe_resume_file_name,
)
from gigai.secrets_catalog import KNOWN_SERVICES

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

FIXTURE_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. (fixture only.)\n"
PASTED = "Jordan Rivera\nStaff engineer: nine years of Go and Kafka.\n"


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=FIXTURE_RESUME)


@pytest.fixture
def temp_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every temporary directory the import makes, as it makes it."""

    made: list[Path] = []
    real = tempfile.mkdtemp

    def recording(*args: object, **kwargs: object) -> str:
        path = real(*args, **kwargs)  # type: ignore[arg-type]
        made.append(Path(path))
        return path

    monkeypatch.setattr(resume_import.tempfile, "mkdtemp", recording)
    return made


def _resumes(fx: ProfileFixtureGig) -> list[dict[str, object]]:
    imports = list_imports(
        home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id
    )
    return [item for item in imports if item.get("kind") == "resume"]


def _content(fx: ProfileFixtureGig, record_id: str, revision_id: str) -> bytes:
    record = read_record(
        home_root=fx.home_root,
        requested_target=fx.target,
        record_id=record_id,
        revision_id=revision_id,
        content=True,
        gig_id=fx.resolved.gig_id,
    )
    content = record["content"]
    assert isinstance(content, bytes)
    return content


# --- the import ---------------------------------------------------------------------


def test_resume_bytes_are_stored_as_they_are_and_the_temp_file_is_removed(
    fx: ProfileFixtureGig, temp_dirs: list[Path]
) -> None:
    data = PASTED.encode("utf-8")
    stored = import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=data)

    assert stored.created and stored.reference_created and stored.record_created
    assert stored.label == PASTED_RESUME_FILE_NAME
    assert stored.content_sha256.startswith("sha256:")
    assert _content(fx, stored.record_id, stored.revision_id) == data
    assert len(_resumes(fx)) == 2  # the fixture's and this one

    assert len(temp_dirs) == 1
    assert not temp_dirs[0].exists()


def test_the_same_bytes_again_store_nothing(fx: ProfileFixtureGig) -> None:
    data = PASTED.encode("utf-8")
    first = import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=data)
    again = import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=data)
    renamed = import_resume_bytes(
        home_root=fx.home_root, requested_target=fx.target, data=data, file_name="another name.txt"
    )

    for repeat in (again, renamed):
        assert not repeat.created
        assert (repeat.reference_id, repeat.record_id, repeat.revision_id) == (
            first.reference_id,
            first.record_id,
            first.revision_id,
        )
        assert repeat.content_sha256 == first.content_sha256
    assert len(_resumes(fx)) == 2

    edited = import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=data + b"More.\n")
    assert edited.created and edited.record_id != first.record_id
    assert len(_resumes(fx)) == 3


def test_bytes_and_a_file_are_one_import_path(fx: ProfileFixtureGig, tmp_path: Path) -> None:
    """The fixture stored ``resume-1.md`` with the CLI's operation keys: the
    same bytes through either entry are that resume, not a second one."""

    from_bytes = import_resume_bytes(
        home_root=fx.home_root, requested_target=fx.target, data=FIXTURE_RESUME, file_name="resume-1.md"
    )
    source = tmp_path / "copy" / "resume-1.md"
    source.parent.mkdir()
    source.write_bytes(FIXTURE_RESUME)
    from_file = import_resume_file(home_root=fx.home_root, requested_target=fx.target, source=source)

    for stored in (from_bytes, from_file):
        assert not stored.created
        assert stored.record_id == fx.resume_record_id
        assert stored.revision_id == fx.resume_revision_id
    assert len(_resumes(fx)) == 1


def test_a_refused_import_leaves_no_temp_file(fx: ProfileFixtureGig, temp_dirs: list[Path]) -> None:
    with pytest.raises(PrivateRecordError) as refused:
        import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=b"\xff\xfe not text")
    assert refused.value.code == "reference_invalid_utf8"
    with pytest.raises(PrivateRecordError) as too_large:
        import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=b"x" * (RESUME_MAX_BYTES + 1))
    assert too_large.value.code == "reference_too_large"

    assert len(temp_dirs) == 2
    assert not any(path.exists() for path in temp_dirs)
    assert len(_resumes(fx)) == 1


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("resume.md", "resume.md"),
        ("Resume.MD", "Resume.md"),
        ("cv.markdown", "cv.markdown"),
        ("My Resume (final).txt", "My-Resume-final.txt"),
        ("Jordan Rivera résumé (2026).md", "Jordan-Rivera-r-sum-2026.md"),
        ("C:\\Users\\me\\Documents\\resume.txt", "resume.txt"),
        ("../../etc/resume.md", "resume.md"),
        ("履歴書.md", "resume.md"),
        ("---.md", "resume.md"),
        ("x" * 200 + ".txt", "x" * 60 + ".txt"),
    ],
)
def test_a_browser_file_name_becomes_one_the_import_accepts(given: str, expected: str) -> None:
    name = safe_resume_file_name(given)
    assert name == expected
    # The operation key the import builds from it: at most 160 characters of
    # [A-Za-z0-9._:-] (private_records._receipt_path).
    key = f"scout-resume-add:{name}:sha256:{'0' * 64}"
    assert len(key) <= 160
    assert all(char.isascii() and (char.isalnum() or char in "._:-") for char in key)


@pytest.mark.parametrize("given", ["resume.pdf", "resume.docx", "resume", "resume.md.exe", ".md", ""])
def test_a_format_the_import_does_not_accept_is_refused(given: str) -> None:
    with pytest.raises(ResumeImportError) as refused:
        safe_resume_file_name(given)
    assert refused.value.code == "resume_media_type_unsupported"


# --- POST /api/resumes' body ---------------------------------------------------------


def test_the_request_is_pasted_text_or_a_file() -> None:
    assert resumes_api.parse_request({"text": PASTED}) == (PASTED.encode("utf-8"), PASTED_RESUME_FILE_NAME, "pasted")
    encoded = base64.b64encode(FIXTURE_RESUME).decode("ascii")
    assert resumes_api.parse_request({"file_name": "My Resume.md", "content_base64": encoded}) == (
        FIXTURE_RESUME,
        "My-Resume.md",
        "uploaded",
    )


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ([], "wrong_type"),
        ({}, "resume_input_invalid"),
        ({"text": PASTED, "file_name": "a.md", "content_base64": "YQ=="}, "resume_input_invalid"),
        ({"text": ""}, "resume_input_invalid"),
        ({"text": " \n\t"}, "resume_input_invalid"),
        ({"text": None}, "wrong_type"),
        ({"text": "\ud800"}, "resume_invalid_utf8"),
        ({"text": PASTED, "label": "x"}, "unknown_key"),
        ({"file_name": "a.md"}, "resume_input_invalid"),
        ({"content_base64": "YQ=="}, "resume_input_invalid"),
        ({"file_name": "a.md", "content_base64": "%%%"}, "resume_input_invalid"),
        ({"file_name": "a.md", "content_base64": ""}, "resume_input_invalid"),
        ({"file_name": "a.pdf", "content_base64": "YQ=="}, "resume_media_type_unsupported"),
        ({"file_name": "a.md", "content_base64": base64.b64encode(b"\xff\xfe").decode("ascii")}, "resume_invalid_utf8"),
        ({"text": "x" * (RESUME_MAX_BYTES + 1)}, "resume_too_large"),
    ],
)
def test_a_bad_request_is_refused_with_its_own_code(body: object, code: str) -> None:
    with pytest.raises(ResumeImportError) as refused:
        resumes_api.parse_request(body)
    assert refused.value.code == code


# --- the routes, over HTTP -------------------------------------------------------------


@pytest.fixture
def running_server(fx: ProfileFixtureGig):
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client, fx
    finally:
        server.shutdown()
        server.server_close()


def test_the_route_answers_with_ids_and_logs_no_resume(running_server, caplog: pytest.LogCaptureFixture) -> None:
    client, fx = running_server
    encoded = base64.b64encode(PASTED.encode("utf-8")).decode("ascii")
    with caplog.at_level(logging.DEBUG):
        stored = client.post("/api/resumes", json={"file_name": "Jordan Rivera.md", "content_base64": encoded})
        again = client.post("/api/resumes", json={"file_name": "Jordan Rivera.md", "content_base64": encoded})
        refused = client.post("/api/resumes", json={"text": "  "})

    assert stored.status_code == 201, stored.text
    body = stored.json()
    assert set(body) == {"schema_version", "resume_ref", "label", "created"}
    assert set(body["resume_ref"]) == {"record_id", "revision_id", "content_sha256"}
    assert body["created"] is True and body["label"] == "Jordan-Rivera.md"
    assert again.status_code == 200 and again.json() == {**body, "created": False}
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "resume_input_invalid"
    assert _content(fx, body["resume_ref"]["record_id"], body["resume_ref"]["revision_id"]) == PASTED.encode("utf-8")

    # The profile routes take the ids as they are.
    created = client.post(
        "/api/profiles",
        json={
            "label": "Staff platform",
            "titles": ["staff platform engineer"],
            "resume_record_id": body["resume_ref"]["record_id"],
            "resume_revision_id": body["resume_ref"]["revision_id"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["profile"]["resume_ref"] == body["resume_ref"]

    # The answer names the stored file (its label), never what is in it;
    # the server's log names neither.
    for text in (stored.text, again.text):
        assert "Staff engineer" not in text and "Kafka" not in text and encoded not in text
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    for message in messages:
        assert "Jordan" not in message and "Kafka" not in message and encoded not in message
    assert "resume stored: source=uploaded created=True" in messages
    assert "resume stored: source=uploaded created=False" in messages


def test_the_key_state_is_booleans_from_the_environment_and_the_home(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fx = running_server
    for env_var in KNOWN_SERVICES.values():
        monkeypatch.delenv(env_var, raising=False)

    nothing = client.get("/api/secrets/status")
    assert nothing.status_code == 200, nothing.text
    assert nothing.json() == {
        "schema_version": "scout-secrets-status:1",
        "keys": {"exa": False, "openai": False, "openrouter": False},
    }

    monkeypatch.setenv("EXA_API_KEY", "exa-environment-secret")
    # A JEV_API_KEY left in the store is no longer a known service: it is not reported.
    secrets_store.set("JEV_API_KEY", "jev-stored-secret", home_root=fx.home_root)
    secrets_store.set("OPENROUTER_API_KEY", "   ", home_root=fx.home_root)  # blank is not set
    some = client.get("/api/secrets/status")
    assert some.json()["keys"] == {"exa": True, "openai": False, "openrouter": False}
    assert "secret" not in some.text.replace("scout-secrets-status", "")

    monkeypatch.setenv("EXA_API_KEY", "  ")
    secrets_store.remove("JEV_API_KEY", home_root=fx.home_root)
    assert client.get("/api/secrets/status").json()["keys"] == {
        "exa": False,
        "openai": False,
        "openrouter": False,
    }


def test_without_a_home_only_the_environment_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(**_kwargs: object) -> set[str]:
        raise AssertionError("the operator's default home must not be read")

    monkeypatch.setattr(secrets_status.secrets_store, "names_set", never)
    for env_var in KNOWN_SERVICES.values():
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("JEV_API_KEY", "jev-environment-secret")

    assert secrets_status.keys_set(None) == {"exa": False, "openai": False, "openrouter": False}


def test_the_unsupported_format_message_names_the_conversion_command() -> None:
    with pytest.raises(ResumeImportError) as refused:
        safe_resume_file_name("resume.pdf")
    assert "uvx --from 'markitdown[pdf,docx]' markitdown" in str(refused.value)
