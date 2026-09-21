# SCOUT R7 corrections review — 2026-09-21

Status: **bounded independent risk review; one contract defect found; R7 is not
accepted.**

This review read `SCOUT-release-execution-graph.md`, the R7 inventory, media,
core, verifier, candidate, source-triage reports, and the current source/test
diffs. It preserved the unrelated dirty worktree and changed only this report;
there was no provider/model call, private data access, activation, publication,
model download, commit/reset, full-suite rerun, or loopback rerun.

## Finding

### R7-CR-01 — ordinary input restriction contradicts the accepted CLI contract

**Severity: P1 contract break for non-text declared artifact classes.**

The public `run-plan create` surface still accepts `--class` values including
`code_review` and `--artifact-class` values `code`, `structured_data`, and
`mixed` (`src/gigai/cli.py:2799-2803`). `create_run_plan` also retains all of
those artifact classes in `_ARTIFACT_CLASSES` and accepts them at
`src/gigai/run_plan.py:1671-1681`. However every ordinary `--input` is now
passed through `_text_artifact` at `src/gigai/run_plan.py:1697-1705`, whose
deterministic mapping at `:189-215` refuses every suffix except `.md`,
`.markdown`, and `.txt`, and refuses non-UTF-8 bytes.

One disposable synthetic probe created `source.py` containing `print(1)` and
called the same `_text_artifact` boundary used by ordinary ingestion:

```text
suffix=.py code=run_plan_input_mismatch message=each input must be plain text or Markdown
```

This is not merely a missing test: the accepted CLI/input contract exposes
classes that the implementation can no longer materialize. The provider-review
consumer is intentionally text-only (`src/gigai/provider_review.py:551-571`),
so the deterministic text gate is appropriate for that downstream lane; it is
not sufficient justification for silently narrowing ordinary Run Plan input
creation. Resolve by either (a) narrowing the CLI/schema/classification
contract to text-only for this command, or (b) retaining deterministic,
allowlisted media handling for declared code/structured-data/mixed inputs and
keeping the provider-review text gate before provider execution. No correction
was made in this review.

## Accepted bounded corrections

### Deterministic media, bytes, paths, and historical Plans

`src/gigai/run_plan.py:189-215` removes host `mimetypes` dependence, maps the
three documented text suffixes case-insensitively, reads exact bytes, validates
UTF-8, and seals digest/size/media in the ordinary reference. Baseline approval
and review-subject ingestion use the same helper at `:389-393` and `:485-489`,
so leaf regular-file/symlink, bytes, suffix, and UTF-8 behavior is consistent
between those two lanes. Plan reads do not call this helper: historical bytes
are read and identity-validated at `:1421-1457`, while sealed source
revalidation checks safe path, symlink components, exact digest, and size at
`:677-724`; no historical Plan is reclassified or rewritten. The bounded
review did not establish ancestor-symlink rejection for caller-supplied source
paths; this remains a path-proof boundary, not evidence of historical mutation.

### Occurrence reconciliation

`src/gigai/occurrence.py:233-243` treats only the
`run_details_reconciliation_required:` refusal as transient and returns the
unchanged `run_prepared` record. Other `RunError` values propagate. The
reconciliation path contains no launch call (`:215-259`); Run allocation stays
in the trigger path, so the correction does not duplicate Runs or adopt
uncommitted status. This retains the committed-only gate in
`src/gigai/run.py:806-824`. The focused report's later-terminal-retry evidence
is consistent with this boundary.

### Lifecycle allocator and init authority

`src/gigai/lifecycle.py:2597-2604` is a thin lifecycle-owned wrapper around
the canonical Gig allocator. `src/gigai/default_init.py:691-700` uses it only
to reserve unbound IDs inside the existing registry transaction; the durable
intent remains `pending` at `:701-703`, and newly materialized Scout bindings
explicitly retain `approval: {state: unapproved, approved_version: None}` at
`:830-847`. The inspected correction does not create active selection or
approval authority; active-pointer inspection is read-only at
`default_init.py:362-375`. This is bounded source evidence, not whole-init
acceptance.

### Canonical receipt hashing

`src/gigai/scout_interview_records.py:461-463` now calls
`digest_imported_bytes(operation_key.encode("utf-8"))` and removes only the
`sha256:` display prefix. The receipt path remains exactly
`records/operations/interview-<hex>.json`; this preserves the prior filename
identity while routing hashing through `src/gigai/canonical.py:256-263`.
No duplicate local SHA implementation was found in this receipt path.

## Inventory and verifier/scenario review

The inventory correction retains strict identity rather than discovered-count
acceptance:

- `research/contract_spike/tests/test_schemas.py:16-99` pins the exact 82
  packaged resource names; setup rejects missing/additional resources at
  `:1607-1622`.
- Every non-common resource has a golden and canonicalization check at
  `:1643-1657`; the additive resource set has unknown-root, missing-required,
  and selected nested-unknown negatives at `:1659-1718`. Open nested objects
  documented by the correction were not falsely treated as closed.
- The historical 64-resource tuple remains explicit in
  `src/gigai/validators.py:28-105`, and `tests/test_g17_capabilities.py:73-82`
  retains exact manifest and installation hash assertions.
- `tests/test_scout06_source_contract.py:34-103` pins the exact 21-member
  authoring map and still asserts that research/proposal sources do not enter
  the closed CRUD capability inventory.

No green-by-weakening was found in the inspected G41/G42/scenario updates.
They add the accepted synthetic username to `tests/test_g41_package_boundary.py`
and `tests/test_g42_catalog.py`, preserve package/adoption/private-content
assertions, and update the scenario harness only for contract-generated
`.gigai/local`, `.gigai/locks`, workpad project paths, and the exact installed
interpreter subprocess (`tests/scenarios/harness.py:347-379,440-468`). The
installed verifier changes retain exact registry/table/row assertions and
explicit negative behavior; the G04 status helper intentionally excludes only
generated untracked `.gigai/packages/` entries (`tools/verify_installed_g04.py:19-29,166-189`),
with package existence checked by the scenario lane (`tests/test_g04_installed_scenarios.py:233-244`).

The five G22/G26/setup-browser loopback failures remain **environment-refused,
not code-proven**: all fail at socket construction in
`src/gigai/proposal_interview.py:783` or `src/gigai/setup_interview.py:171`
with `PermissionError: [Errno 1] Operation not permitted`, before their
token/session assertions. They require a loopback-permitting environment; the
loopback-only boundary must not be weakened here.

## Release boundary

The reports and current source support bounded synthetic corrections for the
inventory, deterministic text-media gate, committed occurrence retry,
lifecycle allocation, canonical receipt identity, and verifier fixtures. The
ordinary-input contract defect above remains open, and the captured source
matrix, installed-wheel, live-provider, Linux/Windows, Docker, publication,
and UAT gates remain separate. This report makes no whole-release claim.
