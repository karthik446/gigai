# SCOUT-08 request-before-Run and framing corrections

Date: 2026-09-10. This bounded correction addresses the two blockers recorded
in `SCOUT-08-run-integration-review.md`; it does not broaden tailoring into
UI, final document selection, application effects, providers, or semantic
factuality.

## F1 — exact request authority before Run

`tailor-application` v2 Plans now require exactly one canonical,
strictly-shaped `scout-tailoring-request:1` carried by an explicitly selected
`g45_run_input`. The fixed request validator enforces bounded canonical JSON,
closed keys, the two required source roles, unique source IDs and input
indices, bounded nested collections, and closed check-option shapes. Plan
sealing resolves and authenticates the candidate's committed snapshot bytes
under the existing writer lock, rejects request/source-role reuse and multiple
request candidates, and records a strict `tailoring_request` descriptor with
the selected index and authenticated identity/refs.

Start/checkpoint/submit revalidate the ordinary sealed inputs and then redeem
only that descriptor's exact G45 snapshot ref. They do not infer request
authority from sidecar role metadata, content base64, a `g45_reference`, a
`scout_record` wrapper, or a second request-shaped selected input. Existing v1
and non-tailoring v2 Plan schemas remain version-aware; only the tailoring v2
branch requires the additive field.

Focused public tests cover absent, duplicate, wrong-family, source-role reuse,
foreign, and changed request inputs, each asserting the journal HEAD and
artifact map remain unchanged before any Plan/Run publication. Existing valid
resume-only and both-document flows plus exact submit replay remain covered.

## F2 — canonical bundle framing

The fixed length-delimited decoder requires ASCII decimal tokens with no sign,
leading zero, whitespace, Unicode digit, or practical overflow; positive
lengths are bounded to the document limit before slicing. Duplicate/missing
markers and trailing bytes remain refused, while bytes containing literal
document-header text are recovered exactly because framing is driven by the
length token.

`tests/test_scout08_request_framing.py` records ten focused parser tests:
canonical positive recovery plus `+6`, `06`, whitespace, Unicode digits,
oversize, duplicate marker, missing marker, and extra-byte refusals. The
separate pure `.075` packet inventory and historical source bytes are
unchanged.

## Verification boundary

Current focused evidence:

```text
rtk .venv/bin/pytest -q tests/test_scout08_run_integration.py tests/test_scout08_request_framing.py
23 passed in 136.26s (0:02:16)
rtk .venv/bin/pytest -q tests/test_scout08_tailoring_packet.py
26 passed in 0.08s
rtk ruff check src/gigai/external_recording.py src/gigai/scout_tailoring.py tests/test_scout08_run_integration.py tests/test_scout08_request_framing.py tools/verify_installed_schemas.py
[]
rtk proxy /bin/zsh -lc 'PYTHONPATH=src python tools/verify_installed_schemas.py'
verified 64 installed GigAI schemas
```

This is offline public-fixture evidence only. Remaining gates are independent
review, historical focused packet evidence, package/wheel proof, UI/question
round trip, document selection/publication, activation, provider operation,
and semantic factuality or hiring/ATS claims.
