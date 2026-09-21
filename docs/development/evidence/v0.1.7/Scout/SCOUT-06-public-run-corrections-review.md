# SCOUT-06 public Run corrections review (independent, review-only)

Date: 2026-09-10. Independent verification of the coordinator's corrections in
[SCOUT-06-public-run-corrections.md](SCOUT-06-public-run-corrections.md),
against the findings recorded in
[SCOUT-06-public-run-review.md](SCOUT-06-public-run-review.md).

Scope: D1 replay protocol guard, D2 executable domain requirement, D3
`_approved_fields` v2 bounds, and the publication-time source/schema
revalidation. Review only — no source, schema, test, fixture, or golden was
modified; no provider ran; nothing was committed, tagged, or published.

Per the dispatch: the coordinator's combined focused suite was **not**
duplicated. Luna's `scout_research_inputs.py` and its tests are that worker's
scope and were excluded — grep confirms neither the reviewed corrections nor
the SCOUT-07 candidate reference that module. No claim is made here that the
worktree as a whole was unchanged; other owners were concurrently active.

## Verdict

**All four corrections verified. No blocking defect. No new finding.**

D1 is genuinely closed, and closed in a stronger place than the review's own
minimal suggestion. D2 and D3 are closed as described. The publication-time
revalidation is correctly confined to an internal callable that no external
caller can supply.

One documentation-only nit is recorded at the end. It does not affect the
runtime and is not a blocker.

## D1 — verified closed (was blocking)

The guard `_require_replay_protocol` (`external_recording.py:1251-1264`)
refuses unless the recorded top-level `schema_version`, the **nested**
`invocation.schema_version`, and the caller's `schema_version` all agree.

**Placement is complete.** All five replay-style early returns are covered:

| Site | Path | How covered |
|---|---|---|
| `external_recording.py:742` | Plan — operation-key scan | direct call at 741 |
| `external_recording.py:952` | Plan — deterministic-ID lookup | direct call at 951 |
| `external_recording.py:1084` | `start` | inside `_replay` (1307) |
| `external_recording.py:1814` | `_progress` (checkpoint/cancel) | inside `_replay` (1307) |
| `external_recording.py:2191` | `_submit` | inside `_replay` (1307) |

Both Plan paths the corrections doc names are guarded, as claimed. Putting the
guard *inside* `_replay` before its `return payload` is better than patching
the three call sites: a future caller of `_replay` inherits the protection
rather than having to remember it.

I checked one further early return the prior review did not enumerate —
`external_recording.py:1131`, which returns an existing run record when
`runs/<run_id>/external-run.json` is already present. It carries **no**
`_require_replay_protocol` call. It is nonetheless safe: `run_id` derives from
`digest_imported_bytes(plan_bytes)` (1122-1128), so reaching it requires the
same Plan, and the Plan-vs-caller downgrade guard at
`external_recording.py:1087-1093` has already refused any protocol mismatch.
A run record's version always equals its Plan's, so the mismatch is
unreachable there. Recorded for completeness, not as a finding.

**Behaviour probed directly** against the real guard:

```
v1 record / v1 caller (same-protocol replay): ACCEPTED
v2 record / v2 caller (same-protocol replay): ACCEPTED
v2 record / v1 caller (the D1 attack):        REFUSED external_protocol_downgrade
v1 record / v2 caller (reverse direction):    REFUSED external_protocol_downgrade
top=1.0 nested=2.0 / v1 caller:               REFUSED external_protocol_downgrade
top=2.0 nested=1.0 / v2 caller:               REFUSED external_protocol_downgrade
missing nested invocation:                    REFUSED external_protocol_downgrade
nested not a mapping:                         REFUSED external_protocol_downgrade
```

Exact same-protocol replay is preserved in both directions; cross-version
replay is refused in both directions. The nested check also catches a forged
record whose top-level and nested versions disagree — beyond what the review
asked for.

**Digests and deterministic IDs are unchanged, as claimed.** The `normalized`
dict feeding `payload_sha256` (`external_recording.py:331-338`) still has
exactly `operation, project_id, gig_id, origin, actor, input` and still
excludes `protocol_version`. The Plan identity block (920-936) and the
`run_id` identity (1122-1128) likewise carry no protocol field. So the
narrow fix the review recommended was taken, and the fix it warned against —
folding `protocol_version` into `payload_sha256`, which would invalidate every
committed replay identity — was correctly avoided.

**The five regression tests are real.** `tests/test_scout06_protocol_replay.py`
contains exactly five parametrized cases:

- `test_v1_cannot_replay_an_existing_v2_operation[start|checkpoint|cancel]`
- `test_plan_replay_does_not_cross_protocols[first_version=1|2]`

They drive the real public API, and each asserts
`recording._snapshot(resolved) == before`, proving the refusal publishes
nothing. The Plan case uses the legacy field-list fixture in **both** version
directions.

```
.venv/bin/pytest -q tests/test_scout06_protocol_replay.py -p no:randomly
5 passed in 25.03s
```

This substantiates the corrections doc's claim that the prior review's stated
reachability ("exactly `start` and `cancel`") was too narrow: an empty
checkpoint and a legacy field-list Plan replay also cross protocols. That
correction to my predecessor's analysis is accurate.

## D2 — verified closed

The executable layer now carries the domain requirement independently of the
schema gate, at `external_recording.py:1997-2001`:

```python
if protocol_version == 2 and is_output and not is_domain_item:
    raise ExternalRecordingError(
        "external_domain_required",
        "version-2 outputs require their sealed domain evidence",
    )
```

This is exactly the guard the review proposed: an item classified as a sealed
output under v2 must carry the domain channel, regardless of whether the JSON
schema is present or intact. The weaker direction the review flagged — schema
strict, code permissive, so a future schema edit silently removes the only
enforcement — is now closed. Completion checks retain their existing
three-field form (`is_check` at 1986-1991 is untouched), as the corrections
doc states.

## D3 — verified closed

The v2 `allow_domains` branch (`external_recording.py:541-560`) now enforces
both bounds the v1 branch had. Probed against the live `LIMITS`:

```
max_artifacts_per_operation = 32
200-char field           v2_branch_accepts=False
64 fields                v2_branch_accepts=False
exactly 32 fields        v2_branch_accepts=True
exactly 64-char field    v2_branch_accepts=True
65-char field            v2_branch_accepts=False
valid research           v2_branch_accepts=True
```

Boundaries are correct in both directions. The corrections doc's claim that no
schema resource or historical version was widened holds: the bound is read
from the existing `LIMITS["max_artifacts_per_operation"]` constant and the
literal `64`, matching v1 rather than introducing new numbers.

## Publication-time source/schema revalidation — verified

This was the highest-risk correction, since it introduces a callable
parameter. It holds up.

**Placement.** `_journaled` (`external_recording.py:622-635`) takes
`revalidate: Callable[[], None] | None = None`, keyword-only, defaulting to
`None`, and invokes it inside the writer lock immediately before publication.
It runs *before* the `try` block, so a refusal surfaces as its own
`ExternalRecordingError` rather than being swallowed into the generic
`external_reconciliation_required` mapping. That ordering is right.

**The callable cannot be supplied externally.** Exactly two sites pass it —
`external_recording.py:2091` (checkpoint) and `2513` (submit) — each a locally
defined `revalidate_domains` closure that captures only local state and calls
the same `_validate_domain_binding` used elsewhere. Both are gated on
`protocol_version == 2`. Verified by introspection that no public entry point
exposes any callable beyond `uuid_factory`:

```
plan/plan_v2/start/start_v2/checkpoint/checkpoint_v2/submit/submit_v2/cancel/cancel_v2
  params = [home_root, requested_target, gig_id, envelope, uuid_factory(, protocol_version)]
  extra_callables = []   (all ten)
```

Grep for `revalidate` across `external_cli.py`, `capabilities.py` and
`scout_discovery.py` returns nothing. So no CLI input, Gig manifest, or
external agent can supply a callback or an executable module — the corrections
doc's claim is accurate. Checkpoint and submit both install it, as stated.

**Replay correctly skips it.** In both `_progress` (1812-1814) and `_submit`
(2189-2191) the replay short circuit returns before `_journaled` is ever
reached, so a completed identical retry does not rerun the fresh-publication
check and a later local edit cannot invalidate it. This matches the claim and
is confirmed by the passing
`test_completed_exact_replay_is_not_invalidated_by_later_source_mirror_edit`.

**The test race is honest about what it simulates.** In
`tests/test_scout06_research_run_adversarial.py:241-283` the docstring states
plainly that it injects only a publication race after the real bridge accepted
input, that a compliant competing writer is already excluded by flock, and
that no validator result is replaced by a fabricated acceptance. The mutation
writes to `resolved.path / source_ref["path"]` — the **working-tree mirror** —
not committed Git bytes, exactly as the corrections doc distinguishes. Each
case asserts `_state(...) == before`, so the refusal is atomic. No `xfail` or
expected-failure marker remains in the SCOUT-06 test files, consistent with
"the original expected-failure marker was removed."

Focused run of the correction's own evidence file (not the coordinator's
combined suite):

```
.venv/bin/pytest -q tests/test_scout06_research_run_adversarial.py -p no:randomly
16 passed in 104.80s
```

That includes source and schema mirror mutation at both checkpoint and submit
(`binding_member` × `operation` parametrization) plus completed replay after a
later mirror edit — the expanded coverage the corrections doc describes.

## Documentation nit (non-blocking, not a runtime defect)

The corrections doc says the check "verifies the pinned source/schema through
the existing committed authority path." That is accurate —
`_validate_domain_binding` re-resolves the committed artifact and compares it
against the packaged `FIXED_DOMAIN_RESOURCES`. Worth stating explicitly for
future readers: the closures revalidate the **domain binding for each
published output kind**, not every input ref in the Plan. That is the correct
scope for a domain-evidence publication check, but the doc's wording could be
read as broader than the code.

## What I did not do

- Did not rerun the coordinator's combined real-flow/adversarial/protocol/
  transport suite. The dispatch reserved it; the corrections doc claims no
  combined green result, and I make none either.
- Did not run or read Luna's `scout_research_inputs.py` tests for verdict
  purposes.
- No full suite, wheel build, provider call, private workpad, or commit.
- Reviewed source files (`external_recording.py`, the two SCOUT-06 test files)
  are untracked candidate work; `git status` confirms nothing was committed.

## Still open (unchanged by these corrections)

Carried forward from the prior review, neither delivered nor claimed here:
historical research reuse, installed-workflow/isolated-wheel verification,
user UAT and release acceptance. The source and contract remain a candidate
with no default promotion.
