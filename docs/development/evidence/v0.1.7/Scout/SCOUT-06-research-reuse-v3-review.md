# SCOUT-06 v3 research-reuse independent review

Date: 2026-09-10

## Scope and review posture

This is a bounded independent review of the new `research-packet:3` /
`scout-role-research:3` renderer, its closed recording and input-resolution
paths, and the candidate `1.3` / compiler `scout-candidate-compiler:4`
materialization.  It makes no production or test change and does not treat the
recorded 29-test lane, the one unknown-validator test, or a source review as
whole-Scout, provider, activation, wheel, or legacy-v2-to-v3 acceptance.

The frozen SCOUT-00 contract is the decision baseline.  In particular, it
requires sealed exact input/content references and explicit same-Gig
predecessors, while preserving dynamic iteration without relaxing sealing
([SCOUT-00-contract-amendments.md](SCOUT-00-contract-amendments.md), sections
3 and B1).  The implementation note's new “one-hop” policy is therefore
reviewed as an implementation choice, not accepted as a new freeze-contract
limit on an ordinary successor lifecycle.

## Accepted subset

The following v3 authority boundary is accepted by source inspection:

* `scout_template.py` maps only the new candidate `1.3` to literal `.076`
  source/schema bytes; `scout_materialization.py` pins both exact bytes in the
  `research-role` output contract.  The retained v2 bridge/resource identity
  is not rewritten by this mapping.
* `graph_set.py` admits a closed domain tuple and rejects unknown nested
  fields, invalid paths, digests, media types, and schema/validator IDs.
  `external_recording._validate_domain_binding` then compares the committed
  schema and source bytes with the reviewed installed package.  A suffix-only
  reference is consequently not an import capability or a byte-substitution
  bypass.
* `scout_research_inputs._fixed_domain_binding` has a closed `(schema_id,
  validator_id)` dispatch table for v2 and v3.  It reads the selected
  contract/schema/source from the same pinned snapshot and compares those
  bytes to the fixed package before obtaining a validator; the dynamic import
  is derived only from that closed table, not a sidecar value.
* The selector is exact and closed (`family`, `run_id`, `receipt_id`, and
  `output_kind`).  Hydration retains the complete selected Run’s receipts and
  checkpoints plus output/check/contract/schema/source/supporting bytes at a
  single writer HEAD.  `_historical_research_inputs` passes actual committed
  markdown, sidecar, domain, and supporting bytes to the v3 bridge rather
  than relying on pointers alone.
* The `.076` schema is additive and strict at its object boundaries.  Its
  historical `scout_research` shape names exact artifacts; the renderer also
  requires the complete field set and couples allowed schema/validator pairs,
  so the schema's independent enums do not become a pair-mismatch bypass.

This acceptance is limited to the source-level fixed-package and byte-binding
properties above.  It does not replace the separate legacy completed-v2
public-path fixture, which the implementation report honestly lists as an
acceptance gap and which is assigned to the independent Luna lane.

## Blocking finding — v3 research reuse becomes a two-Run ceiling

**Severity: blocking for SCOUT-06 v3 research-reuse acceptance; not a finding
against unrelated external-recording operations.**

The public v3 path can complete a first Run and then a second Run selecting
the first.  A third Run can resolve and start using the completed second Run,
but it cannot checkpoint its research output: the broker rehydrates the
second Run’s actual bytes and `scout_research_v3.validate_research_domain`
rejects them whenever the second sidecar contains its own
`family: scout_research` input.  The exact refusal is
`research_domain_input_mismatch: historical research input ancestry is too
deep`; it occurs before checkpoint publication, so no third completion is
possible with ordinary research-result reuse.

This is not a request for unbounded history traversal.  The frozen contract
explicitly permits a sealed predecessor/successor and says changed required
inputs use a successor while preserving dynamic iteration; it does not freeze
a two-Run lifetime for research reuse.  The current implementation already
has a bounded alternative: the selected historical Run is revalidated and
its complete committed tuple is hydrated at the one caller-held HEAD.  That
is sufficient to authenticate the selected second result without recursively
walking the second result's selected-input ancestry.

### Reproduction

One disposable bridge probe created a public synthetic completed v3 research
Run, resolved its exact normalized `scout_research` envelope, and supplied a
historical v3 packet whose sealed selected inputs contained that envelope.
The fixed bridge refused with the exact code and message above.  The probe
used a temporary initialized workpad only; the temporary directory was
removed, and no workspace artifact was changed.

The public-recording call chain reaches the same condition:

1. `checkpoint_v2` revalidates every sealed input under its writer and calls
   `_historical_research_inputs`, which redeems the selected Run’s actual
   output bytes.
2. `_fixed_research_v3_validator` invokes the literal fixed v3 bridge with
   those bytes as `historical_inputs`.
3. The bridge rejects any `scout_research` item inside the historical sidecar
   before it validates the historical packet.  A second Run has exactly such
   an item after selecting the first, so a third Run selecting that second
   result fails at checkpoint.

An earlier disposable full three-Run public-path attempt produced no result
before the execution capture limit, so it is deliberately not counted as
proof.  The bridge probe and the recorded public call chain establish the
typed refusal; a focused public third-Run regression should be added with the
fix.

### Minimal fix direction

Keep the fixed bridge, closed dispatch, committed-byte comparison, and
one-writer snapshot.  Change the ancestry rule so it does not reject a
previously sealed, fully resolved historical Run merely because that Run had
one research input of its own; authenticate the direct selected tuple at the
current boundary without recursively reopening its lineage.  Add a focused
three-Run public fixture proving Plan/start/checkpoint/submit for the third
Run, exact second-result bytes, unchanged earlier records, and refusal of
tampered or foreign direct history.  The test must state the bounded traversal
rule explicitly rather than encoding an accidental two-Run cap.

## Verification inventory and remaining acceptance

I did not rerun the reported 29-test / 77.29-second lane, its unknown-validator
test, or any whole suite.  The independent scratch bridge probe is the only
completed probe result used here; no provider/network operation, user data,
wheel build, activation, commit, or repository mutation was performed.

Accepted: closed v3 identity dispatch, strict nested contract/schema shape,
fixed installed-byte authentication, same-writer source hydration, and actual
historical-byte handoff.  Blocked: v3 research reuse cannot be accepted as an
ordinary iterative lifecycle until the third-Run ceiling is removed or the
frozen contract is explicitly amended to impose that product restriction.
Deferred to the Luna-owned independent test lane: an end-to-end retained-v2
completed Run reused by a v3 candidate without rewriting old installed/user
state.
