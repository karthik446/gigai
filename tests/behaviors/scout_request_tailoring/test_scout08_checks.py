"""Focused proof for the pure SCOUT-08 deterministic checks lane."""

from __future__ import annotations

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.scout.checks import MAX_DOCUMENT_BYTES, check_document


def _codes(report: dict[str, object]) -> set[str]:
    return {str(item["code"]) for item in report["findings"]}  # type: ignore[index]


def test_resume_report_is_exact_byte_bound_and_inspectable() -> None:
    document = (
        b"# Ada Example\n\n"
        b"## Skills\nPython and SQL\n\n"
        b"## Experience\nBuilt reliable tools.\n\n"
        b"## Education\nBSc\n\n"
        b"Email: ada@example.test | +1 555 123 4567\n"
    )
    report = check_document(
        document,
        "resume",
        posting_terms=["Python", "Kubernetes"],
        length_requirements={"min_words": 4, "max_words": 60},
        contact_requirements={"email": True, "phone": True},
        heading_requirements=["Skills", "Experience"],
    )

    assert report["rule"] == "scout.document_checks"
    assert report["rule_version"] == "1.0"
    assert report["inputs"]["document_sha256"] == digest_imported_bytes(document)  # type: ignore[index]
    assert report["inputs"]["document_size_bytes"] == len(document)  # type: ignore[index]
    assert "term_present" in _codes(report)
    assert "term_absent" in _codes(report)
    assert "semantic_factuality_not_assessed" in _codes(report)
    assert "requirement_to_evidence_not_assessed" in _codes(report)
    assert not any("ada@example.test" in str(item) for item in report["findings"])  # type: ignore[operator]
    assert not any("555 123" in str(item) for item in report["findings"])  # type: ignore[operator]

    output_without_digest = dict(report["output"])  # type: ignore[arg-type]
    output_without_digest.pop("sha256")
    reconstruction = dict(report)
    reconstruction["output"] = output_without_digest
    assert report["output"]["sha256"] == digest_imported_bytes(canonical_json_bytes(reconstruction))  # type: ignore[index]


def test_cover_letter_specificity_placeholders_and_lexical_label_are_advisory() -> None:
    report = check_document(
        b"# Cover Letter\n\nDear Hiring Team,\n\nI am excited to help [COMPANY] as a {{JOB_TITLE}}.\n",
        "cover_letter",
        posting_terms=["Python"],
        cover_letter_requirements={"job_title": "Platform Engineer", "company_name": "Acme"},
    )
    codes = _codes(report)
    assert "placeholder_present" in codes
    assert "cover_letter_specificity_missing" in codes
    assert "term_absent" in codes
    assert "term_present" not in codes
    assert report["assessment_limits"] == [
        "semantic_factuality_not_assessed",
        "requirement_to_evidence_judgment_not_assessed",
    ]


def test_contact_values_are_never_echoed_and_supplied_values_can_be_checked() -> None:
    secret_email = "private.person@example.test"
    report = check_document(
        f"# Resume\n\nContact: {secret_email}\n".encode(),
        "resume",
        contact_requirements={"email": secret_email, "location": "Denver"},
    )
    assert "contact_missing" in _codes(report)
    assert secret_email not in str(report)
    assert "Denver" not in str(report)


def test_malformed_utf8_is_reported_without_decoding_or_echoing_bytes() -> None:
    document = b"# Resume\n\xff\xfe\x00"
    report = check_document(document, "resume", posting_terms=["secret"])
    assert _codes(report) == {"document_not_utf8", "semantic_factuality_not_assessed", "requirement_to_evidence_not_assessed"}
    assert report["inputs"]["document_sha256"] == digest_imported_bytes(document)  # type: ignore[index]
    assert report["measurements"] == {"word_count": 0, "character_count": 0, "line_count": 0, "heading_count": 0}
    assert report["output"]["checks_complete"] is False  # type: ignore[index]


def test_oversize_and_hostile_controls_are_bounded() -> None:
    document = ("# Resume\n\u202e" + "x" * MAX_DOCUMENT_BYTES).encode()

    def hostile_unbounded_terms():
        while True:
            yield "x"

    report = check_document(document, "resume", posting_terms=hostile_unbounded_terms())
    codes = _codes(report)
    assert "document_oversize" in codes
    assert "posting_terms_bounded" in codes
    assert "control_text_present" not in codes  # oversize content is not inspected
    assert report["output"]["finding_count"] <= report["bounded"]["max_findings"]  # type: ignore[index]
    assert report["output"]["checks_complete"] is False  # type: ignore[index]
    assert len(report["findings"]) <= 128  # type: ignore[arg-type]


def test_hostile_control_text_is_detected_when_within_limit() -> None:
    report = check_document("# Resume\nSafe\u0000 text\n".encode(), "resume")
    assert "control_text_present" in _codes(report)
    control = next(item for item in report["findings"] if item["code"] == "control_text_present")  # type: ignore[union-attr]
    assert control["evidence_refs"] == [{"kind": "document", "line": 2}]


def test_length_heading_and_requirement_input_errors_are_static_and_bounded() -> None:
    report = check_document(
        b"plain text only",
        "resume",
        length_requirements={"min_words": 100, "max_words": 2, "unknown": 4},
        contact_requirements={"email": True, "unknown": True},
        heading_requirements=["Experience", "Education"],
    )
    codes = _codes(report)
    assert "length_requirement_invalid" in codes
    assert "length_out_of_bounds" in codes
    assert "contact_missing" in codes
    assert "contact_requirement_invalid" in codes
    assert "headings_missing" in codes
    assert codes >= {"semantic_factuality_not_assessed", "requirement_to_evidence_not_assessed"}


def test_mapping_order_and_repeated_calls_produce_the_same_report() -> None:
    document = "# Resume\n\n## Experience\nPython\n".encode()
    kwargs = {
        "posting_terms": ["Python", "SQL"],
        "length_requirements": {"max_words": 30, "min_words": 1},
        "contact_requirements": {"phone": True, "email": True},
        "heading_requirements": ["Experience"],
    }
    first = check_document(document, "resume", **kwargs)
    second = check_document(document, "resume", **kwargs)
    assert first == second


def test_unknown_kind_and_non_bytes_are_reported_without_filesystem_or_network() -> None:
    report = check_document("# not bytes", "other")  # type: ignore[arg-type]
    assert "document_bytes_required" in _codes(report)
    assert "document_kind_invalid" in _codes(report)
    assert report["inputs"]["document_sha256"] is None  # type: ignore[index]


def test_heading_requirements_match_supported_headings_not_prose_substrings() -> None:
    present = check_document(
        b"# Resume\n\n## Experience\nBuilt tools.\n",
        "resume",
        heading_requirements=["Experience"],
    )
    absent = check_document(
        b"# Resume\nExperience mentioned in prose.",
        "resume",
        heading_requirements=["Experience"],
    )
    plaintext = check_document(
        b"Resume\n======\n\nExperience\n\nBuilt tools.\n",
        "resume",
        heading_requirements=["Experience"],
    )
    assert "heading_missing" not in _codes(present)
    assert "heading_missing" in _codes(absent)
    assert "heading_missing" not in _codes(plaintext)
    assert present["measurements"]["heading_count"] == 2  # type: ignore[index]


def test_invalid_kind_is_a_bounded_non_sensitive_marker() -> None:
    hostile_kind = "x" * 100_000
    report = check_document(b"# Resume\n", hostile_kind)
    rendered = canonical_json_bytes(report)
    assert report["document_kind"] == "invalid"
    assert hostile_kind not in rendered.decode("utf-8")
    assert len(rendered) < 10_000


def test_requirements_digest_binds_effective_settings_without_echoing_contact_values() -> None:
    document = b"# Resume\n\n## Experience\nShort text.\n"
    first = check_document(
        document,
        "resume",
        length_requirements={"max_words": 100},
        contact_requirements={"email": "private.one@example.test", "phone": True},
        heading_requirements=["Experience"],
    )
    changed = check_document(
        document,
        "resume",
        length_requirements={"max_words": 200},
        contact_requirements={"phone": True, "email": "private.two@example.test"},
        heading_requirements=["Experience"],
    )
    reordered = check_document(
        document,
        "resume",
        length_requirements={"max_words": 100},
        contact_requirements={"phone": True, "email": "private.one@example.test"},
        heading_requirements=["Experience"],
    )
    assert first["inputs"]["requirements_sha256"] != changed["inputs"]["requirements_sha256"]  # type: ignore[index]
    assert first["inputs"]["requirements_sha256"] == reordered["inputs"]["requirements_sha256"]  # type: ignore[index]
    assert "private.one@example.test" not in canonical_json_bytes(first).decode()
    assert "private.two@example.test" not in canonical_json_bytes(changed).decode()


def test_malformed_requirement_shapes_are_bounded_and_reported() -> None:
    huge_key = "k" * 100_000
    report = check_document(
        b"# Resume\n",
        "resume",
        length_requirements={huge_key: 1, "max_chars": 10**100},
        contact_requirements={"email": []},  # type: ignore[dict-item]
        cover_letter_requirements={"unknown": "value"},
        heading_requirements=["h" * 100_000],
    )
    codes = _codes(report)
    assert "length_requirements_invalid" in codes
    assert "contact_requirement_invalid" in codes
    assert "cover_letter_requirement_invalid" in codes
    assert "heading_requirements_invalid" in codes
    assert len(canonical_json_bytes(report)) < 10_000


def test_finding_cap_is_visible_and_does_not_claim_complete_checks() -> None:
    report = check_document(
        b"# Resume\n",
        "resume",
        posting_terms=[f"term-{index}" for index in range(128)],
    )
    assert len(report["findings"]) <= 128  # type: ignore[arg-type]
    assert "findings_truncated" in _codes(report)
    assert report["output"]["checks_complete"] is False  # type: ignore[index]
