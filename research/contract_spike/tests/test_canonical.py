from __future__ import annotations

import json
import unittest
from pathlib import Path

from ..canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_digest,
    normalize_owned_text,
    parse_json_bytes,
    parse_json_front_matter,
    render_json_front_matter,
    sha256_digest,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


class CanonicalContractTests(unittest.TestCase):
    def test_golden_vectors(self) -> None:
        fixture = json.loads((FIXTURES / "canonical-vectors.json").read_text(encoding="utf-8"))
        for vector in fixture["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    canonical_json_bytes(vector["input"]),
                    vector["canonical_utf8"].encode("utf-8"),
                )
                self.assertEqual(canonical_json_digest(vector["input"]), vector["sha256"])

    def test_unicode_is_preserved_not_normalized(self) -> None:
        composed = canonical_json_digest({"text": "é"})
        decomposed = canonical_json_digest({"text": "e\u0301"})
        self.assertNotEqual(composed, decomposed)

    def test_forbidden_values_and_member_names_fail_closed(self) -> None:
        rejected = [
            {"amount": 1.5},
            {"amount": 9_007_199_254_740_992},
            {"not-ascii-é": "value"},
            {"bad key": "value"},
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(CanonicalizationError):
                canonical_json_bytes(value)

    def test_duplicate_members_are_rejected_before_canonicalization(self) -> None:
        with self.assertRaisesRegex(CanonicalizationError, "duplicate JSON member"):
            parse_json_bytes(b'{"a":1,"a":2}')

    def test_owned_text_normalization(self) -> None:
        self.assertEqual(normalize_owned_text("line one\r\nline two\n\n"), b"line one\nline two\n")
        with self.assertRaises(CanonicalizationError):
            normalize_owned_text("\ufeffnot allowed")
        with self.assertRaises(CanonicalizationError):
            normalize_owned_text("not\x00allowed")

    def test_front_matter_round_trip_and_body_digest(self) -> None:
        body = "Run paused for operator review.\r\n"
        body_bytes = normalize_owned_text(body)
        metadata = {
            "body_sha256": sha256_digest(body_bytes),
            "run_id": "run_77777777-7777-4777-8777-777777777777",
            "schema_version": "1.0",
        }
        document = render_json_front_matter(metadata, body)
        parsed_metadata, parsed_body = parse_json_front_matter(document)
        self.assertEqual(parsed_metadata, metadata)
        self.assertEqual(parsed_body, body_bytes)
        self.assertTrue(document.endswith(b"review.\n"))

    def test_front_matter_rejects_noncanonical_metadata_and_body(self) -> None:
        body = b"body\n"
        digest = sha256_digest(body)
        noncanonical = (
            b'---gigai-json\n{"schema_version": "1.0", "body_sha256":"'
            + digest.encode("ascii")
            + b'"}\n---\nbody\n'
        )
        with self.assertRaisesRegex(CanonicalizationError, "not canonical JSON"):
            parse_json_front_matter(noncanonical)

        metadata = {"body_sha256": digest, "schema_version": "1.0"}
        canonical_metadata = canonical_json_bytes(metadata)
        bad_body = b"---gigai-json\n" + canonical_metadata + b"\n---\nbody\r\n"
        with self.assertRaisesRegex(CanonicalizationError, "not normalized"):
            parse_json_front_matter(bad_body)


if __name__ == "__main__":
    unittest.main()
