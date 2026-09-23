# PRIVACY-01: The package privacy guard doesn't recognize v2 external-recording records

**Status:** Recorded 2026-09-22 from the S14 follow-up. Not started, and no
implementation is authorized.
**Owner/system:** GigAI package privacy (`package_privacy.py`, used by
`package.inspect_package`).
**Priority:** Privacy defense in depth. There is a confirmed gap at the
guard level. Whether it can be reached through export is **unproven**.

## Ticket

**Problem:** `_PRIVATE_SCHEMAS` (`package_privacy.py:39-51`) lists only the
**v1** external-recording schemas. v2 records declare `schema_version: "2.0"`,
so they can't match any listed schema. The guard's path rules (`runs/`,
`run-plans/`, …) still catch a record left in place. A record copied under
another path (for example `docs/x.json`) is not recognized:
`private_package_provenance` returns `False` for real v2 checkpoint, run and
receipt records.

**Intended behavior:** The schema-shape fallback recognizes every
external-recording version that current code writes. The external-recording
CLI still defaults to protocol v1, so both v1 and v2 are live.

**Tasks:**

1. **Establish whether the gap can be reached through export.** Can any
   real workflow place an external-recording record outside the private
   path roots in a package source tree? This is unproven, and the ticket
   must not treat it as a demonstrated leak.
2. Choose the fix shape. One option is to list the v2 schemas. The other is
   to recognize the external-recording family regardless of version, so the
   next variant isn't missed.
3. Check the other entries in `_PRIVATE_SCHEMAS` for the same
   single-version pattern.

**Acceptance:**

- **All five v2 families are recognized.** For each of plan, run,
  checkpoint, receipt and invocation, a valid v2 instance placed at a
  non-private path (for example `docs/x.json`) makes
  `private_package_provenance` return `True`. The instance must validate
  against its own v2 schema, so the test proves recognition rather than
  shape coincidence. The earlier executed sample covered only checkpoint,
  run and receipt. Plan and invocation weren't sampled and must be included
  here.
- **v1 coverage is kept.** A valid v1 instance of each of the same five
  families at a non-private path still returns `True`.
- **A non-private negative case.** A non-private JSON file at the same kind
  of path returns `False`. The file should look similar, for example an
  ordinary Gig document with a `schema_version` field. This shows the fix
  doesn't simply flag every JSON file.
- The export-reachability finding (task 1) is recorded either way.

**Evidence:** [S14 audit, F4 and F6](../v0.1.8/spikes/evidence/S14-schema-inventory-audit.md).
This is Claude's reported, executed result at the guard level only. **The
operator hasn't independently reviewed it yet.**
