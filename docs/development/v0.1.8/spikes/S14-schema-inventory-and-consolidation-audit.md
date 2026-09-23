# S14 — Schema inventory and consolidation audit

## Ticket

**Status:** Audit documented 2026-09-22; awaiting operator review.  
**Requested:** 2026-09-22. **Scope:** v0.1.8 spike; no new release gate.  
**Execution:** Audit recorded, documentation only. Audit and reporting only; no schema deletion,
merge, version bump, or breaking change is authorized by this ticket.

**Problem:** `src/gigai/schemas/` holds 85 files, 82 of them
`*.schema.json` resources (see inventory
below), and its own README describes growth as a long, strictly additive
sequence of amendments (G15 through G43.1, JSL-01, SCOUT-02/03/R4/R5,
RUNTIME-01, ...), each pinning prior hashes and declaring earlier resources
"byte-identical." Several concepts exist as multiple packaged versions with no
documented retirement recorded so far (`model-invocation` has v1/v2/v3;
`external-recording-*` has five sub-concepts each also packaged as `-v2`;
`graph-selection-record`, `gig-proposal`, `active-gig-version`, `run-plan`,
`run-manifest`, and `scout-document-selection` each have at least two packaged
versions). Whether each additional version is a currently-validated input
contract, a reader for historical data only, or something else is exactly
what this audit needs to establish — it is not yet known, and different
versions may intentionally serve different supported contracts rather than
being redundant.

**Intended behavior:** Produce a full inventory of every schema in
`src/gigai/schemas/`, what it is for, and what (if anything) reads or writes
it in code today. For each schema with more than one packaged version,
establish — with grep-backed evidence, not assumption — which version(s)
current input validators accept, which are read by legacy-only paths, and
which have no evident reader or writer at all. Do not conclude any version is
redundant, superseded, or safe to retire without that evidence; a
multi-version family may reflect genuinely different supported contracts
rather than accumulated cruft.

**Proposed tasks (refine before execution):**

- Enumerate every `*.schema.json` file in `src/gigai/schemas/` (inventory
  below is a starting count, not the final list) and group them by concept
  family (e.g. all `external-recording-*` files as one family, all
  `run-plan`/`run-manifest` variants as another).
- For each family with multiple packaged versions, identify with code
  evidence: which version(s) current input validators actually accept for
  new writes, which are read by legacy-only paths for historical data, and
  whether the schemas README's stated "byte-identical, hash-pinned" policy
  is the reason multiple versions are packaged together.
- Grep the codebase for actual readers/writers of each schema (validators,
  serializers, fixtures, tests) and cite them; do not classify a schema's
  status without that citation.
- Distinguish, per schema, three states established by evidence: (1) accepted
  by a current input validator, (2) read only by a legacy/compatibility path,
  (3) no evident reader or writer found — flag (3) explicitly for operator
  review rather than assuming it is dead code or safe to remove.
- Note where the amendment-letter naming scheme itself (G15, G43.1, JSL-01,
  SCOUT-R5, ...) makes it hard to tell what a schema is for without reading
  the README narrative; assess whether a clearer naming/grouping convention
  is worth proposing separately.
- For each multi-version family, report the evidence found and an open
  question rather than a retirement recommendation, unless the evidence
  clearly shows a version has no current validator and no legacy reader —
  in that narrow case only, name it as a candidate for further operator
  review, not as approved for removal.

**Acceptance / attached evidence:** A table of every schema file, its concept
family, and an evidence-backed state (accepted-by-current-validator /
legacy-read-only / no-evident-reader-or-writer / unclear), with the specific
grep/code citation for each. A short summary of how many families have
multiple packaged versions and what evidence (if any) distinguishes their
roles. No schema is deleted, merged, or version-bumped as part of this spike,
and no version is labeled redundant or safe to retire without a cited
reader/writer trail; any consolidation is a separate, explicitly authorized
follow-up.

**Current evidence pointer:** [S14 schema inventory audit](evidence/S14-schema-inventory-audit.md).
All 82 schemas are classified with citations. 76 are validated against
their file by current code. None of the 12 multi-version families is legacy-read-only: current
writers still emit the older versions, chosen by payload shape (the
external-recording CLI defaults to protocol v1). Seven schemas are flagged
for operator review. None lacks a reader or writer; three have hand-rolled
code contracts that disagree with the packaged file
(`handoff-frontmatter`, `role-reference`, `scout-answer-association`). The privacy
guard recognizes only v1 external-recording payloads, which needs a
decision. Nothing is recommended for removal. A follow-up (F6, test-derived and
executed) found three things. **0 of 28** real journal handoffs conform to
`handoff-frontmatter`. `gigai occurrence declare` can't accept a graph-set
(v2) Gig and gives a misleading "invalid" error. The privacy guard misses
real v2 records once they are renamed; that is a confirmed guard gap, but
no export leak has been demonstrated.

**Evidence at ticket drafting:** A preliminary file count taken during ticket drafting (via
`find src/gigai/schemas -type f`, excluding `__pycache__`): 85 files total —
82 `.schema.json` files plus `__init__.py`, `README.md`, and `SHA256SUMS`.
This is a raw count, not the audited inventory the spike itself must produce.

## Framing — why this matters now

This audit was requested alongside [S15](S15-goal-and-tool-unit-composition-research.md)
because the two are related but distinct: S14 asks whether the *current*
schema set is more than it needs to be; S15 asks how a goal-graph should be
*composed* from smaller goal-unit and tool definitions going forward. A
cleaner composition model (S15) may naturally reduce the need for some of
today's schema variants, but S14 should stand on its own as a factual audit
of what exists now, not assume S15's outcome.

## Conversation notes

Condensed from the 2026-09-22 discussion. The operator's framing: "we need to
audit the shit ton of schemas we have.. do we need all of them? why are there
so many!" This is a direct request to inventory and explain the schema count,
not a request to consolidate them yet — consolidation is explicitly deferred
to a follow-up decision after this audit is reviewed.

The schemas README already documents its own growth policy (strictly
additive, hash-pinned, "byte-identical" preserved across amendments), which
is itself likely the proximate cause of the sprawl and should be evaluated
as part of the audit rather than treated as an unquestionable constraint.

## Related work and open placement

- [src/gigai/schemas/README.md](../../../../src/gigai/schemas/README.md)
  documents the additive-amendment history this audit must account for.
- [S15 — goal and tool-unit composition research](S15-goal-and-tool-unit-composition-research.md)
  is the companion spike from the same discussion; see Framing above for how
  the two relate without one depending on the other's completion.
- [goal-graph.schema.json](../../../../src/gigai/schemas/goal-graph.schema.json),
  [graph_set.py](../../../../src/gigai/graph_set.py), and
  [scout_materialization.py](../../../../src/gigai/scout_materialization.py)
  are concrete starting points already identified for tracing readers/writers:
  `graph_set.py` covers graph authority/selection/validation, while
  `scout_materialization.py` compiles graphs from declared selectors — check
  both before concluding which schemas a given code path actually uses.

No schema deletion, merge, or breaking change is authorized by recording this
ticket.
