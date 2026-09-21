"""Focused canonical framing checks for the SCOUT-08 tailoring bridge."""

from __future__ import annotations

import pytest

from gigai.scout_tailoring import (
    ScoutTailoringError,
    decode_tailoring_bundle,
    encode_tailoring_bundle,
)


def _bundle(data: bytes = b"Python") -> bytes:
    return encode_tailoring_bundle({"resume": data})


def _replace_length(bundle: bytes, token: bytes) -> bytes:
    marker = b"Byte length: "
    start = bundle.index(marker) + len(marker)
    end = bundle.index(b"\n", start)
    return bundle[:start] + token + bundle[end:]


@pytest.mark.parametrize("token", [b"+6", b"06", b" 6", b"6 ", "６".encode("utf-8")])
def test_noncanonical_length_tokens_refuse(token: bytes) -> None:
    with pytest.raises(ScoutTailoringError) as refused:
        decode_tailoring_bundle(_replace_length(_bundle(), token), ["resume"])
    assert refused.value.code == "tailoring_domain_invalid"


def test_oversize_length_token_refuses_before_payload_slice() -> None:
    with pytest.raises(ScoutTailoringError) as refused:
        decode_tailoring_bundle(_replace_length(_bundle(), b"256001"), ["resume"])
    assert refused.value.code == "tailoring_domain_invalid"


def test_duplicate_document_marker_refuses() -> None:
    duplicate = _bundle() + b"## Document: resume\n\nByte length: 6\n\nPython\n\n"
    with pytest.raises(ScoutTailoringError) as refused:
        decode_tailoring_bundle(duplicate, ["resume"])
    assert refused.value.code == "tailoring_domain_invalid"


def test_missing_document_marker_refuses() -> None:
    with pytest.raises(ScoutTailoringError) as refused:
        decode_tailoring_bundle(_bundle(), ["resume", "cover_letter"])
    assert refused.value.code == "tailoring_domain_invalid"


def test_extra_bytes_refuse() -> None:
    with pytest.raises(ScoutTailoringError) as refused:
        decode_tailoring_bundle(_bundle() + b"trailing", ["resume"])
    assert refused.value.code == "tailoring_domain_invalid"


def test_headerlike_payload_bytes_round_trip_exactly() -> None:
    payload = b"## Document: cover_letter\n\nByte length: 4\n\nGo!\n"
    assert decode_tailoring_bundle(_bundle(payload), ["resume"])["resume"] == payload
