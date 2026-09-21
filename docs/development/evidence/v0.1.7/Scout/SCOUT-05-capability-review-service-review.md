# SCOUT-05 capability review/consent service — independent review

**Reviewer:** independent worker, read-only. **Date:** 2026-09-10.
**Scope:** the bounded generic capability review/consent service after the
Luna integration corrections (real registration, authenticated replay
references, manifest-ID-keyed pass decisions).

**Verdict: accept with findings.** The service delivers the authority
separation, immutability, and race behavior it claims, and its central
registration is real. Two test defects make part of the claimed evidence
non-discriminating, and one service gap (unbounded duplicate reviewed
manifests for a single parent) contradicts a requirement the accepted caller
audit stated explicitly. None of these grant activation, execute source, or
weaken the pointer gate. All are correctable in the NEXT lane; none of them
justify reverting this lane.

## Method and limits

Read-only source/doc inspection plus bounded inert disposable probes against
throwaway `tempfile` workpads built from the existing
`tests/test_scout05_bundled_tools._candidate` fixture. Per the dispatch I did
**not** rerun pytest, full suites, or a build; the only pytest invocation was
`--collect-only`, which executes no tests. No source or test file was edited,
no real private `.gigai` state, provider, network, approval, or commit to this
repository was touched. All probe directories were deleted. Probe results below
are reproducible from the commands shown.

Explicitly out of scope per the dispatch and treated as **not** findings: the
absence of a CLI/successor approval caller (NEXT lane); a base Gig with no
current capability pointer (deliberate); and the reviewed manifest retaining a
`pending` option decision (required — see Verified claim 4).

## Verified claims

**1. Central registration is real, not monkeypatched.**
`validators.py:35` lists `capability-review-decision.schema.json`;
`journal.py:111` lists `capability_review_decided`. `tests/test_scout05_capability_review.py`
contains no `monkeypatch`, `SCHEMA_NAMES`, or `TRANSITIONS` reference. Schema
bytes hash `fcb3129c06166aaae657adcc84a5f53459100b9fbf1b8fb8c9bc99e5d9aade15`,
matching both `schemas/SHA256SUMS:7` and `tools/verify_installed_schemas.py:26`;
`schemas/*.json` counts 57. A decision golden with nested strictness/operator/
effect negative cases exists at `research/contract_spike/tests/test_schemas.py:1438`.

**2. Evidence counts are honest.** `pytest --collect-only -q
tests/test_scout05_capability_review.py` reports **13 tests collected** (10
functions, one parametrized into 4), matching the implementation doc's claimed
"13 passed". The doc also correctly labels the earlier 10-test monkeypatched run
as historical.

**3. Review does not activate anything.** Probe: after a passing review, the
active pointer bytes, the pending parent manifest bytes, and the git tag list
were all byte-identical to before; no `receipts/` or `runs/` content appeared.
Independently, `scout_tools.py:230` resolves the executable manifest from
`pointer.get("capability_manifest")`, **not** by scanning
`manifests/capabilities/`. So publishing an `available`/`passed` manifest into
that directory cannot make it executable; only a direct operator approval that
attaches it to the pointer can. The no-activation claim holds structurally,
not merely by convention.

**4. The `pending` option compromise is justified, not a shortcut.**
`capabilities.py:151-152` unconditionally flags any option whose `decision !=
"pending"` as `proposal_not_pending`. A reviewed manifest therefore *cannot*
record the selected option in-manifest without weakening the shared validator.
Recording the selection in the immutable decision instead is the correct
choice, and the implementation doc states the reason accurately. Relatedly,
`capability-manifest.schema.json` is closed (`additionalProperties: false` at
both document and capability level), so a decision back-reference inside the
manifest is genuinely impossible — which makes the filename-derivation
convention (pass decision filename = reviewed manifest ID) a sound workaround
rather than an arbitrary one.

**5. `capability_manifest is not None` is the correct absence test.**
In both `active-gig-version.schema.json` and `-v2`, `capability_manifest` is
optional and `$ref`s `common#/$defs/artifact_ref`, an object that forbids null.
Absent and non-null are therefore equivalent; the guard is right.

**6. Race revalidation is stronger than its own tests prove.** Probe: with
`_before_publication` performing a *committed* base-version advance (write +
`git add` + `git commit`, not just a working-tree edit), the service refused
with `capability_review_current_version_conflict` and `manifests/capability-reviews/`
was never created. This exercises the `final_pointer != pointer` branch that the
existing tests do not reach — the shipped test only mutates the working tree,
which is caught earlier by the `_committed_file` HEAD-equality gate.

**7. Source inspection is inert and contained.** `scout_tools._inventory`
(`scout_tools.py:178-222`) reads bytes, refuses symlinks and executable-mode
members at every path component, and requires the tool root to match the
approved inventory set exactly. It never imports or executes source. The
service passes no source path and accepts no callback other than the
test-only `_before_publication` hook.

**8. Typed input strictness holds.** Probes confirmed refusals for
`base_version=True` (`type(x) is not int`, so `bool` is rejected — a real
trap avoided), `base_version=0`, `operator_confirmed=1` (truthy but not
`True`), whitespace-only rationale, non-string evidence, bogus outcome,
missing selected option, and `reviewed_manifest_id == manifest_id`.
`operation_key` rejects `int`, `bool`, `None`, `""`, and embedded spaces
before any I/O.

**9. Closed source/effect constraints are comprehensive.** Direct probes of
`_validate_requested_boundary` refused: non-`tool` kind, an extra operation,
unsorted operations, empty operations, `network_requirement=egress`, required
credentials, non-`local_artifact` source kind, `filesystem=write_all`, an
already-decided option, and a non-`use_available` option kind.

**10. Reviewer vs. operator consent is properly separated at the service
layer.** `_actor(..., operator=True)` requires `kind == "operator"`, and
`operator_confirmed is not True` refuses. This matches the SCOUT-00 amendment
requirement (lines 280-286) that the validated service "cannot manufacture
direct CLI consent."

## Findings

### F1 — Blocking for NEXT lane: a single parent can be reviewed into unbounded distinct `passed` manifests

**Severity:** high (correctness/authority ambiguity; not a live safety breach).
**Location:** `src/gigai/capability_review.py:405-413` (replay scan).

Idempotency is keyed **only** on `operation_key`. The replay scan `continue`s
past any prior decision whose `operation_key` differs, so a second call with a
different key re-runs the full publication path against the same still-pending
parent.

Reproduced (disposable workpad, two calls differing only in `operation_key`):

```
first  reviewed: manifests/capabilities/capmanifest_1b527839-976c-4c72-af51-a97bb12b1afb.json
second reviewed: manifests/capabilities/capmanifest_da7daa15-2104-43bf-a4ac-90cf2353ba7b.json
DUPLICATE: True
  capmanifest_...072.json  missing    pending  mver=1   <- parent, untouched
  capmanifest_1b527839...  available  passed   mver=2
  capmanifest_da7daa15...  available  passed   mver=2
```

Two equally-authoritative `available`/`passed` manifests, same `capability_id`,
same `manifest_version: 2`, each with its own valid consent decision. The
accepted caller audit required the opposite: "a changed request, multi-publisher
artifact, changed source, or current-version mismatch must produce a typed
refusal and no additional publication"
(`SCOUT-05-capability-consent-caller-audit.md:100-103`), and listed
"multi-publisher conflict ... refuse with no new receipt/publication" as a
required regression (line 141). Neither is implemented or tested.

**Why it is not a breach today:** the pointer gate (Verified claim 3) means
neither manifest is executable until a direct operator approval attaches one.
The damage is determinism, not privilege: a NEXT-lane caller asked to resolve
"the reviewed manifest for capability X" has no unambiguous answer, and an
operator approving by ID cannot tell from the manifest alone which consent
event produced it.

**Suggested resolution (NEXT lane, not applied here):** before publishing a
pass, scan committed decisions for any prior `reviewer_outcome == "passed"`
binding the same `gig_id`/`capability_id`/`parent_manifest_ref` and refuse with
a typed `capability_review_conflict`; add the multi-publisher regression the
audit already specified.

**Related acceptance limit (not a defect):** a *rejected* decision under one key
does not block a later *passed* decision under a new key. Probed and confirmed.
This is defensible as re-review after remediation, but it should be stated
deliberately in the contract rather than left as an emergent property.

### F2 — Two shipped assertions are tautological and prove nothing

**Severity:** medium (false evidence of a safety property).
**Location:** `tests/test_scout05_capability_review.py:151` and `:209`.

Both lines read:

```python
assert not list((workpad / "manifests/capabilities").glob("capmanifest_*.json")) == []
```

Python parses this as `not (list(...) == [])`, i.e. **"the list is non-empty."**
Since the pending parent is `capmanifest_00000000-0000-4000-8000-000000000072.json`
(`scout_bundled_tools.py:17`), the glob always matches at least that file, so
the assertion is true whether or not an unwanted new manifest was published.

Probe:

```
not [] == []   -> False
not [x] == []  -> True
fnmatch("capmanifest_...072.json", "capmanifest_*.json") -> True
```

The intended property in both tests — *no reviewed manifest was published* in
the rejected-review case (`:140`) and in the in-lock source-mutation case
(`:195`) — is currently **unverified**. Both properties do in fact hold (I
confirmed the source-race case leaves `manifests/capability-reviews/`
non-existent), so this is a test defect, not a behavior defect. Correct form:
assert the glob returns exactly the parent, e.g.
`== [workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json"]`.

### F3 — Foreign-Gig test accepts either of two codes and never reaches the service guard

**Severity:** low (imprecise evidence).
**Location:** `tests/test_scout05_capability_review.py:237`.

```python
assert refused.value.code == "capability_review_current_version_conflict" or \
       refused.value.code == "capability_review_authority_unavailable"
```

Probed: both a foreign `gig_id` and a foreign `project_id` refuse with
`capability_review_authority_unavailable`, raised by
`journal._validate_workpad` — the service's own
`pointer.get("gig_id") != gig_id` check at `capability_review.py:344` is never
reached. The refusal is correct, but the test documents neither which layer
enforces it nor the service guard it appears to target, and would still pass if
that guard were deleted. Pin the expected code, and cover the service-level
guard separately (e.g. a workpad whose pointer names a different Gig).

### F4 — Refusal codes conflate unsupported permissions with unsupported kind

**Severity:** low (caller ergonomics).
**Location:** `src/gigai/capability_review.py:236-238`.

`network_requirement != "none"` and non-empty `credential_requirements` both
raise `capability_review_unsupported_kind`, although neither concerns the
capability's *kind*. A caller distinguishing "wrong sort of capability" from
"asked for network/credentials I will not grant" cannot do so from the code.
`capability_review_effect_refused` (or a dedicated code) would be accurate.

### F5 — Precision: the final inventory comparison is weaker than it looks

**Severity:** informational.
**Location:** `src/gigai/capability_review.py:449-453`.

`final_inventory != actual_inventory` compares two values both normalized from
the *same* `binding`, so the comparison itself can never differ. The race is
genuinely caught — but by `_inventory` raising `ScoutToolError` on byte drift
(`scout_tools.py:199-200`), one line earlier. The shipped in-lock source-mutation
test passes through the exception path, not the comparison. The comparison is
harmless defense-in-depth; it should not be read as the mechanism, and a reader
auditing the race guard should be pointed at `_inventory`.

### F6 — Minor: `_ref` cannot express an `artifact_ref` carrying `canonical_sha256`

**Severity:** informational.
**Location:** `src/gigai/capability_review.py:97-104`.

`_ref` emits exactly four keys, and `parent_manifest_ref` is compared with
`dict(parent_manifest_ref) != actual_parent_ref`. `common#/$defs/artifact_ref`
permits an optional `canonical_sha256`, so a caller that legitimately supplies
a five-key ref is refused with `capability_review_parent_ref_mismatch`. No
current caller does this; worth pinning in the NEXT lane's caller contract.

## Pre-existing gap confirmed still open (correctly deferred)

Caller-audit **F2** is unchanged by this lane: `lifecycle.approve_offline`'s
`--capability-manifest-id` path resolves through
`capabilities.capability_manifest_artifact_ref`, which validates schema
validity, path/ID agreement, and Gig ownership — but **not**
`security_review.status == "passed"`, not `availability_state == "available"`,
and not the existence of any review decision. Confirmed by inspection:
`capabilities.py` contains no occurrence of `capability-review`, `capreview`,
`review_decision`, or `capability_review`.

This lane does not widen that hole — but it does change its character. Before,
no `passed` manifest existed to attach. Now the repository can contain several,
and nothing at approval time requires the attached one to be backed by a
decision. The NEXT lane should add the approval guard and the decision-lookup
step together, using the manifest-ID-derived decision filename this lane
established.

## Acceptance limits

- Verified by probe, not by the shipped suite: the committed-advance race
  (claim 6), duplicate publication (F1), immutability under a passing review
  (claim 3), and the closed-constraint matrix (claim 9).
- I did not execute the focused suite, any full suite, or a build; the
  "13 passed in 24.45s" figure is reported by the implementer and corroborated
  here only as a collection count of 13.
- No claim is made about the wheel/installed-code evidence in
  `SCOUT-05-bundled-tools-integration.md`; that was outside this review.
- The same-account editable-Python trust boundary is unchanged and is not a
  sandbox claim. This review does not certify the Scout capability as reviewed,
  installed, executable, or safe to expose to an external agent.

## Recommended disposition

Accept the lane. Carry **F1** into the NEXT (CLI/successor approval) lane as a
blocking prerequisite, since that lane is the first consumer that must resolve
"the" reviewed manifest deterministically. Fix **F2** and **F3** as a small
test-only correction — they are cheap and they currently overstate what the
suite proves. **F4**–**F6** are optional polish.
