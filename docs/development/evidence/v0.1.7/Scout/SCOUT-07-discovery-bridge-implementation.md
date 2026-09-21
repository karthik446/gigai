# SCOUT-07 fixed discovery bridge — coordinator implementation

Date: 2026-09-10. Candidate component only, not journal or installed acceptance.

`src/gigai/scout_discovery.py` imports one literal packaged `.074` renderer and
its fixed `urn:gigai:scout:discovery-packet:2` schema. The validator identity is
`scout-job-discovery:2`. Neither source nor schema is selected by a Gig path or
agent callback. No discovery resources have been added to the source inventory
or promoted into the default catalog by this change.

The caller must supply authenticated Plan/Run origin, the exact normalized
selected inputs, and committed preference bytes. The bridge first checks the
strict schema and exact origin/input equality, then rebuilds the packet using
the supplied profile and supporting bytes. Unknown nested fields/types,
changed profile revisions, altered optional references, missing/extra evidence,
capture tampering and rendered-document changes refuse with content-free typed
errors. Optional contents are not read or interpreted as eligibility evidence.
The bridge does not authenticate a journal by itself; trusted caller wiring is
still required. Source verification and fit remain external-agent declarations,
not GigAI attestations or permission to apply.

Coordinator evidence:

- Corrected discovery packet suite: **17 passed in 0.20s**.
- New bridge suite: **19 passed in 0.18s**.
- Scoped Ruff for the new bridge and tests passed.
- Initial test collection failed because a fixture import lacked the `tests.`
  package prefix; corrected before the passing run.

Independent packet/bridge review, source/compiler inclusion, fixed generic
domain dispatch, committed preference-byte redemption, real public recording
and installed workflow evidence remain open. The bridge is deliberately not
registered while the research runtime undergoes independent review.

## Coordinator identity correction

Two pure probes confirmed false opportunity-ID equality in the unregistered
candidate: `(employer="a\\nb", posting_id="c")` versus
`(employer="a", posting_id="b\\nc")`, and a URL-looking posting ID versus
the same value used as a locator fallback. The original newline-concatenated
hash input did not preserve field boundaries or identity namespaces.

The candidate now hashes a canonical object containing employer, explicit
`posting_id`/`locator` kind and exact selected identity. No existing user Gig
or shipped identity was migrated. Two regression cases cover the collisions.
Combined packet/bridge verification after the correction: **38 passed in
0.38s**. The candidate schema shape/hash is unchanged; source bytes changed
before any source-inventory inclusion. Independent review remains required.
