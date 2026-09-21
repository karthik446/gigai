# SCOUT-07 posting-input resolver verification

Date: 2026-09-10. This focused gate closes the public-evidence limitations
identified in `SCOUT-07-posting-input-resolution-review.md` without changing
the accepted resolver or integrating Tailor/shared input schemas.

## Public authority coverage

The happy-path test now calls the one-step
`resolve_discovery_posting_input_from_journal` helper. It builds a real
disposable same-Gig lifecycle—`plan_v2` → `start_v2` → valid `.074` packet
checkpoint → succeeded `submit_v2`—then proves exact posting capture bytes,
opportunity/snapshot identity, source locator/status, fixed domain binding,
provenance refs, and unchanged committed journal state.

The focused file also covers:

- a second independently initialized disposable Gig/Run used as a foreign
  selector, which remains outside the selected Gig's journal and refuses with
  `posting_input_not_found`;
- an incomplete public Run explicitly cancelled through `cancel_v2`, whose
  selected non-succeeded receipt refuses with `posting_input_not_terminal`;
- a second terminal receipt published only by a narrowly described test-only
  journal fixture, using valid v2 schema bytes, unique receipt/handoff refs,
  and authenticated front matter; the resolver refuses with
  `posting_input_refused` rather than choosing one receipt;
- a genuine completed `no_match` packet, where an unlisted opportunity/snapshot
  selection refuses with `posting_input_not_found` and leaves the journal
  unchanged; and
- malformed selector shapes before journal access.

The prior copied in-memory domain-byte mutation is now named and documented as
`test_caller_pinned_snapshot_tamper_refuses_without_writing`: it demonstrates
the raw resolver's caller-pinned snapshot boundary only, not a committed
journal tamper path and not a production defect.

Every negative records the full committed snapshot before and after and asserts
identical HEAD/artifact bytes. The adversarial second-terminal fixture is
explicitly test-only because public APIs correctly prevent a second terminal
Run state; it does not weaken production authority or create a new record
family.

## Exact verification record

Final focused command after development:

```text
rtk .venv/bin/pytest -q tests/test_scout07_posting_inputs.py
11 passed in 88.85s (0:01:28)

rtk ruff check tests/test_scout07_posting_inputs.py
[]

rtk git diff --check
```

The focused run is offline disposable-fixture evidence. No broad SCOUT suite,
wheel/package build, provider/network operation, private Gig, activation, or
commit was performed.

## Remaining limitations

This gate does not prove CLI/UI discovery selection, shared external-recording
caller registration, G45 run-input publication for Tailor, Tailor generation,
question round-trip/final selection, provider execution, application effects,
or package/wheel behavior. The downstream caller must use the one-call helper
or a writer-held hydrated snapshot and preserve both opportunity and snapshot
identities; the raw snapshot primitive remains intentionally caller-trusted.
