"""0110-034: ``gigai scout story-bank add|list|show|edit|delete|share`` and ``scout answer``'s
story bank options (CliRunner over the real installed CLI; no model, synthetic answers).

Fixture: ``test_scout_answer_cli._setup`` (``gigai setup``/``init``/``scout install``/``resume add``).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.profile_records import create_profile, selected_profile
from gigai.workpad import resolve_workpad

from .test_scout_answer_cli import _setup

_STORY = "Situation: a 4 TB Postgres primary near its limit. Action: led the dual-write cut-over. Result: zero lost writes."
_STORY_ID = "story:database_led_migration"


def _bank(runner: CliRunner, home: Path, target: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = runner.invoke(cli, ["scout", "story-bank", *args, "--home", str(home), "--target", str(target), "--json"])
    return result.exit_code, json.loads(result.output.strip().splitlines()[-1])


def _second_profile(home: Path, target: Path) -> tuple[str, str]:
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    default = selected_profile(resolved, home_root=home, target=target)
    assert default is not None
    other = create_profile(resolved, label="someone else", titles=("x",), titles_to_avoid=(), queries=("x",), resume_ref=default.resume_ref)
    return default.profile_id, other.profile_id


def test_story_bank_cli_add_list_show_edit_delete(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    code, empty = _bank(runner, home, target, "list")
    assert code == 0 and empty["entries"] == [] and empty["schema_version"] == "scout-story-bank-response:1"

    story_file = tmp_path / "story.md"
    story_file.write_text(_STORY, encoding="utf-8")
    code, added = _bank(runner, home, target, "add", "--question", "Tell me about a database migration you led", "--answer-file", str(story_file), "--actor", "agent")
    assert code == 0, added
    entry = added["entry"]
    assert (entry["question_id"], entry["answer"], entry["tag"], entry["written_by"], entry["revision"]) == (_STORY_ID, _STORY, "leadership", "agent", 1)  # type: ignore[index]
    code, again = _bank(runner, home, target, "add", "--question", "Tell me about a database migration you led", "--answer-text", "Another.")
    assert code == 1 and again["error"]["code"] == "story_exists" and again["error"]["entry"]["answer"] == _STORY  # type: ignore[index]

    code, listed = _bank(runner, home, target, "list", "--search", "postgres", "--tag", "leadership")
    assert code == 0 and [item["question_id"] for item in listed["entries"]] == [_STORY_ID] and listed["total"] == 1  # type: ignore[union-attr]
    code, shown = _bank(runner, home, target, "show", _STORY_ID)
    assert code == 0 and shown["entry"]["updated_at"] == entry["updated_at"]  # type: ignore[index]
    code, missing = _bank(runner, home, target, "show", "cloud:nothing")
    assert code == 1 and missing["error"]["code"] == "not_found"  # type: ignore[index]

    # The operator edits; an agent holding the older updated_at is refused and gets the entry as it is now.
    code, edited = _bank(runner, home, target, "edit", _STORY_ID, "--answer-text", _STORY + " p95 latency down 30%.", "--updated-at", str(entry["updated_at"]))  # type: ignore[index]
    assert code == 0 and edited["entry"]["revision"] == 2 and edited["entry"]["written_by"] == "operator" and edited["entry"]["edited"] is True  # type: ignore[index]
    code, stale = _bank(runner, home, target, "edit", _STORY_ID, "--tag", "migrations", "--actor", "agent", "--updated-at", str(entry["updated_at"]))  # type: ignore[index]
    assert code == 1 and stale["error"]["code"] == "story_bank_changed" and stale["error"]["entry"]["revision"] == 2  # type: ignore[index]
    code, tagged = _bank(runner, home, target, "edit", _STORY_ID, "--tag", "migrations", "--actor", "agent", "--updated-at", str(edited["entry"]["updated_at"]))  # type: ignore[index]
    assert code == 0 and (tagged["entry"]["tag"], tagged["entry"]["written_by"], tagged["entry"]["revision"]) == ("migrations", "agent", 3)  # type: ignore[index]
    code, refused = _bank(runner, home, target, "edit", _STORY_ID, "--answer-text", "Ask zq7731@example.test about it.")
    assert code == 1 and refused["error"]["code"] == "personal_info_refused" and "zq7731" not in json.dumps(refused)  # type: ignore[index]

    plain = runner.invoke(cli, ["scout", "story-bank", "list", "--home", str(home), "--target", str(target)])
    assert plain.exit_code == 0 and f"{_STORY_ID} [migrations]" in plain.output and "1 of 1 entries" in plain.output
    detail = runner.invoke(cli, ["scout", "story-bank", "show", _STORY_ID, "--home", str(home), "--target", str(target)])
    assert "Question: Tell me about a database migration you led" in detail.output and "Written by agent" in detail.output

    code, unconfirmed = _bank(runner, home, target, "delete", _STORY_ID)
    assert code == 1 and unconfirmed["error"]["code"] == "confirm_required"  # type: ignore[index]
    code, deleted = _bank(runner, home, target, "delete", _STORY_ID, "--confirm", "--updated-at", str(tagged["entry"]["updated_at"]))  # type: ignore[index]
    assert code == 0 and deleted["deleted"] == _STORY_ID
    assert _bank(runner, home, target, "list")[1]["entries"] == []


def test_scout_answer_lands_in_the_profiles_bank_and_another_profile_sees_it_only_when_shared(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)
    default, other = _second_profile(home, target)

    answered = runner.invoke(
        cli,
        ["scout", "answer", "cloud:gcp", "--question", "Have you run workloads on GCP?", "--answer-text", "Yes, two years on GCP.",
         "--home", str(home), "--target", str(target), "--json"],
    )
    assert answered.exit_code == 0, answered.output
    payload = json.loads(answered.output.strip().splitlines()[-1])
    assert payload["profile_id"] == default and payload["question_id"] == "cloud:gcp"
    refused = runner.invoke(cli, ["scout", "answer", "cloud:aws", "--answer-text", "Call 555-013-7731.", "--home", str(home), "--target", str(target), "--json"])
    assert refused.exit_code == 1 and json.loads(refused.output.strip().splitlines()[-1])["error"]["code"] == "personal_info_refused"
    theirs = runner.invoke(cli, ["scout", "answer", "cloud:gcp", "--answer-text", "No GCP at all.", "--profile", other, "--actor", "agent", "--home", str(home), "--target", str(target), "--json"])
    assert theirs.exit_code == 0 and json.loads(theirs.output.strip().splitlines()[-1])["profile_id"] == other

    mine = _bank(runner, home, target, "list")[1]["entries"]
    assert [(item["question_id"], item["question"], item["answer"], item["written_by"]) for item in mine] == [  # type: ignore[union-attr]
        ("cloud:gcp", "Have you run workloads on GCP?", "Yes, two years on GCP.", "operator")
    ]
    their_list = _bank(runner, home, target, "list", "--profile", other)[1]["entries"]
    assert [(item["answer"], item["written_by"], item["shared"]) for item in their_list] == [("No GCP at all.", "agent", False)]  # type: ignore[union-attr]
    story = _bank(runner, home, target, "add", "--question", "Kafka?", "--answer-text", "Three years of Kafka.", "--id", "tool:kafka")[1]
    assert story["entry"]["question_id"] == "tooling:kafka"  # type: ignore[index]
    assert [item["question_id"] for item in _bank(runner, home, target, "list", "--profile", other)[1]["entries"]] == ["cloud:gcp"]  # type: ignore[union-attr]

    code, sharing = _bank(runner, home, target, "share", "--profile", other)
    assert code == 0 and sharing["sharing"]["share_with"] is None  # type: ignore[index]
    code, shared = _bank(runner, home, target, "share", "--profile", other, "--with", default)
    assert code == 0 and shared["sharing"]["share_with"] == default  # type: ignore[index]
    seen = _bank(runner, home, target, "list", "--profile", other)[1]["entries"]
    assert [(item["question_id"], item["shared"]) for item in seen] == [("cloud:gcp", False), ("tooling:kafka", True)], "its own cloud:gcp wins; the rest is shared"  # type: ignore[union-attr]
    assert _bank(runner, home, target, "share")[1]["sharing"]["read_by"] == [other]  # type: ignore[index]
    code, both = _bank(runner, home, target, "share", "--profile", other, "--with", default, "--off")
    assert code == 1 and both["error"]["code"] == "invalid_value"  # type: ignore[index]
    code, off = _bank(runner, home, target, "share", "--profile", other, "--off")
    assert code == 0 and off["sharing"]["share_with"] is None  # type: ignore[index]
    assert [item["question_id"] for item in _bank(runner, home, target, "list", "--profile", other)[1]["entries"]] == ["cloud:gcp"]  # type: ignore[union-attr]
    code, unknown = _bank(runner, home, target, "list", "--profile", "profile_nobody")
    assert code == 1 and unknown["error"]["code"] == "profile_not_found"  # type: ignore[index]
