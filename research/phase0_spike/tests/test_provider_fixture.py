import json
from pathlib import Path


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "provider-capabilities-2026-07-30.json"
)


def test_recorded_provider_capabilities_are_sanitized_and_passed() -> None:
    evidence = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert evidence["committed_fixture_contains_nonce_or_session_id"] is False
    assert evidence["expected_structured_output"] == {
        "ack": "ACK",
        "write_status": "BLOCKED",
    }
    for provider in ("codex", "claude"):
        result = evidence["providers"][provider]
        assert result["native_structured_output"] is True
        assert result["session_id_captured"] is True
        assert result["resume_recalled_nonce"] is True
        assert result["read_only_file_absent"] is True
        assert result["usage_keys"]
