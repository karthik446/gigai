# SCOUT-07 real discovery Run integration review (independent)

Date: 2026-09-10. Independent, read-only review of the real journaled discovery
Run integration in `external_recording.py`, `graph_set.py`, `scout_template.py`,
`scout_materialization.py`, and `tests/test_scout07_discovery_run_flow.py`.

Reviewed against `SCOUT-07-run-integration-implementation.md`, the prior
`SCOUT-07-discovery-review.md`, and `SCOUT-06-public-run-corrections-review.md`.
No production source, schema, fixture, test, or unrelated documentation was
modified. No provider, network, private-user-state, wheel, activation, commit,
or broad suite operation was performed.

## Verdict

**One non-blocking but real inventory-integrity defect; Run-path authority
checks otherwise hold for the reviewed bounded slice.**

The focused real flow passes, and direct source inspection confirms fixed
discovery dispatch, committed schema/source byte checks, journal-lock placement,
native input revalidation, graph identity checks, exact output/check binding,
and exact submit replay. The implementation does not yet establish a
Scout-specific public discovery CLI or installed workflow; the existing CLI is
the generic external-recording surface described below.

## Verification performed

Focused test:

```text
.venv/bin/pytest -q tests/test_scout07_discovery_run_flow.py -p no:randomly
3 passed in 18.75s
```

Actual CLI inspection:

```text
.venv/bin/gigai external --help
Commands: cancel, checkpoint, graphs, inspect, plan, start, submit
```

Top-level help has `external`, `graph-set`, `run`, `run-plan`, and related
generic commands, but no `scout`, `find-jobs`, or discovery-specific command.
Therefore the handoff's generic “CLI wiring missing” statement is directionally
correct only as remaining public product scope: generic external CLI wiring is
delivered, while public Scout-specific caller/argument/input-resolution wiring
is not.

## Checks that hold

### Fixed dispatch and source/schema binding

`external_recording.py:56-60,109-136` fixes discovery to the literal
`gigai.scout_discovery` bridge and maps the discovery schema ID to the fixed
validator. `external_recording.py:1416-1489` re-authenticates the approved
contract's committed schema and validator-source refs and compares their bytes
with the packaged validator resources. `graph_set.py:88-147` admits only the
two recognized domain IDs, validator IDs, exact filename suffixes, media types,
safe paths, digest, and bounded size shape. No caller-supplied executable
module or callback is accepted.

### Locking, replay, and source-publication guards

All Plan/Run operations execute through `run_with_journal_writer`, whose writer
lock encloses the operation (`journal.py:242-257`). Discovery checkpoint and
submit perform the final domain-binding revalidation through an internal
closure immediately before `_journaled` publication
(`external_recording.py:2132-2151` and `2555-2570`). `_replay` returns before
fresh publication validation, preserving exact idempotent replay; the SCOUT-06
protocol and committed-artifact conflict guards remain in place.

### Native preference, graph/contract identity, and input boundaries

`scout_inputs.py:214-254` re-resolves each sealed native revision and its blob
sidecar against the same committed snapshot. The profile preference bytes used
by the fixed discovery renderer come from that authenticated snapshot via
`external_recording.py:1650-1663`; foreign/missing revisions therefore refuse
before publication. `external_recording.py:1374-1397` checks the committed
selected graph identity and graph version, while
`external_recording.py:1400-1413` and `559-622` enforce the sealed output
contract and recognized domain binding.

### Truthful output and exact replay

The supplied flow creates a real disposable Git target, initializes and
offline-approves the candidate, creates a native `profile_preferences` record,
then performs v2 Plan, start, discovery checkpoint, submit, and exact submit
replay (`tests/test_scout07_discovery_run_flow.py:68-85`). The fixture uses a
no-match packet with unknown employer sponsorship, so it does not claim live
discovery or eligibility. Foreign native revision refusal is covered at
`tests/test_scout07_discovery_run_flow.py:87-93`; partial output and journal
atomicity/tamper refusal are covered at `95-111`.

## Finding

### F1 — Existing software inventory does not validate exact member inventory

**Severity: non-blocking for this Run flow; material for exact inventory
versioning and candidate re-materialization.**

When an existing inventory is found,
`scout_materialization.py:480-491` validates only the top-level fields
`schema_version`, `kind`, `template_id`, `definition_version`, `source_digest`,
`compiler_version`, and that `members` is a list. It does not compare
`existing_payload["members"]` to the freshly generated exact member set
`expected_members` (whose digest/size rows are constructed at `439-451`).
Consequently, a committed inventory with the right top-level version/digest
metadata but omitted, extra, reordered, or wrong digest/size member rows is
accepted and reused. The function then authenticates only the prepared
capability manifest (`492-508`), not the inventory member rows.

This is a real gap in the implementation's “exact inventory versioning” claim,
although it does not let the reviewed external Run execute arbitrary source:
Run publication separately authenticates the output contract's exact committed
schema/source bytes. The correction should compare the normalized exact member
list (or canonical member bytes) against the freshly generated inventory before
reusing the existing version; preserve refusal on any conflict.

## Remaining scope, explicitly not delivered

- Scout-specific public CLI/caller wiring for discovery and native preference
  selection; the generic `gigai external` commands are present.
- Public product workflow and historical-research integration.
- Match/partial/positive discovery fixture breadth beyond the bounded no-match
  and partial flow; no market/provider completeness claim.
- Selected-posting/application posting workflow and downstream publication.
- Installed-wheel/workflow proof, source-inventory registration in any external
  central registry, default promotion, activation, release acceptance, or live
  provider/web discovery.

## Review boundary

This is an independent source review, not model self-review. Evidence above is
limited to the focused test, direct source inspection, and CLI help inspection;
it is not combined-suite, installed-package, provider-dogfood, or deployment
evidence.
