"""uat-bug-018: ``gigai application record`` normalizes an http(s) ``external_ref``.

Scout joins an application event to a posting by exact match on the
posting's normalized URL. The CLI used to record ``external_ref`` exactly as
typed, so a URL with a tracking parameter, a trailing slash or an upper-case
host never joined. Now an http(s) URL goes through find-jobs' own
``normalize_url``; anything else is recorded verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.application_cli import _normalized_external_ref
from gigai.cli import cli
from gigai.scout.find_jobs.job_state import derive_job_state, read_application_events
from gigai.workpad import resolve_workpad
from tests.behaviors.scout_proposals_tools.test_scout05_first_proposal import _bound_defaults

NORMALIZED_URL = "https://boards.greenhouse.io/acme/jobs/12345"
PASTED = "text:sha256:" + "a" * 64


def _record(tmp_path: Path, home: Path, target: Path, gig: str, *, operation: str, **fields: object) -> dict[str, object]:
    request = tmp_path / f"{operation}.json"
    request.write_text(
        json.dumps(
            {
                "operation_key": operation,
                "event_kind": "applied",
                "occurred_at": "2026-09-10T12:00:00-06:00",
                "timezone": "America/Denver",
                "document_refs": [],
                **fields,
            }
        )
    )
    result = CliRunner().invoke(
        cli,
        ["application", "record", "--gig", gig, "--home", str(home), "--target", str(target), "--input", str(request), "--confirm", "--json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.parametrize(
    ("typed", "recorded"),
    [
        ("https://Boards.Greenhouse.io/acme/jobs/12345/?utm_source=linkedin&gh_src=abc", NORMALIZED_URL),
        ("HTTPS://boards.greenhouse.io:443/acme/jobs/12345#apply", NORMALIZED_URL),
        (NORMALIZED_URL, NORMALIZED_URL),
        ("http://example.com/jobs/7?b=2&a=1", "http://example.com/jobs/7?a=1&b=2"),
        # Not an http(s) URL: verbatim.
        (PASTED, PASTED),
        ("acme-staff-engineer-2026", "acme-staff-engineer-2026"),
        ("ftp://example.com/jobs/7/", "ftp://example.com/jobs/7/"),
        ("  https://boards.greenhouse.io/acme/jobs/12345/", "  https://boards.greenhouse.io/acme/jobs/12345/"),
        # An http(s) URL the normalizer refuses: verbatim, never an error.
        ("https://user:secret@example.com/jobs/7/", "https://user:secret@example.com/jobs/7/"),
        ("https://", "https://"),
    ],
)
def test_only_an_http_url_is_normalized(typed: str, recorded: str) -> None:
    data = {"external_ref": typed, "event_kind": "applied"}
    assert _normalized_external_ref(data) == {"external_ref": recorded, "event_kind": "applied"}
    assert data == {"external_ref": typed, "event_kind": "applied"}  # the input is never edited


@pytest.mark.parametrize(
    "data",
    [
        {"opportunity_ref": "opportunity_" + "0" * 32, "event_kind": "applied"},
        {"external_ref": 42},
        {"external_ref": None},
        {},
        ["not", "an", "object"],
        "https://boards.greenhouse.io/acme/jobs/12345/",
    ],
)
def test_anything_without_a_string_external_ref_is_passed_through(data: object) -> None:
    assert _normalized_external_ref(data) is data


def test_a_typed_url_is_recorded_normalized_and_joins_the_job(tmp_path: Path) -> None:
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)

    first = _record(
        tmp_path, home, target, gig, operation="cli-typed-url",
        external_ref="https://Boards.Greenhouse.io/acme/jobs/12345/?utm_source=linkedin",
    )
    assert first["status"] == "recorded"
    assert first["event"]["external_ref"] == NORMALIZED_URL

    # Submitting the same file again is still the same operation.
    again = _record(
        tmp_path, home, target, gig, operation="cli-typed-url",
        external_ref="https://Boards.Greenhouse.io/acme/jobs/12345/?utm_source=linkedin",
    )
    assert again["status"] == "already_recorded"

    pasted = _record(tmp_path, home, target, gig, operation="cli-pasted", external_ref=PASTED)
    assert pasted["event"]["external_ref"] == PASTED

    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    events = read_application_events(resolved)
    assert set(events) == {NORMALIZED_URL, PASTED}
    # The job state reads the typed URL's event under the posting's identity.
    assert derive_job_state(events=events[NORMALIZED_URL]).state == "applied"
    assert derive_job_state(events=events[PASTED]).state == "applied"
