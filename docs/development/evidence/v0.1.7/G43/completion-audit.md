# G43 Completion Audit

**Goal:** G43 — Adaptive Review Profiles and Sealed Run Plans
**Release:** v0.1.7
**Outcome:** Complete

## Delivered behavior

G43 adds a bounded, immutable `run-plan` preparation path. `gigai run-plan
create` resolves the current approved Gig authority, seals explicit input and
runtime/discovery evidence, chooses a finite profile, assigns fixed typed
participants, and returns a deterministic `run_plan_<uuidv4>` identity.
Repeated equivalent planning is idempotent; unsafe paths, changed sources,
changed authority, changed target configuration, unusable targets, ambiguous
classification, or a missing opt-in fail closed.

`gigai run --plan PLAN_ID --confirm` revalidates the sealed plan before it
allocates a Run. Fresh direct operator consent binds the exact plan ID and
digest. The existing Run manifest retains that plan as a sealed source; G43
does not become a second Run, consent, approval, capability, or active-version
authority.

After the existing Run authority records `run_started`, G43 writes the
Run-scoped review bridge: bundle, findings, trace, verification records,
optional adjudication, report, and review-loop. New bridge report and loop
records are schema version `1.1`; older `1.0` artifacts remain readable. The
bridge records deterministic pending-verification state only. It does not
claim a provider call, provider quality, or hidden model transcript.

## Verification

- Focused G43/G16/schema/CLI verification: `55 passed, 67 subtests passed`.
- Full suite: `683 passed, 1 skipped, 74 subtests passed`.
- The sole skip is the opt-in live local-CLI UAT test.
- The initial sandboxed full run reached `678 passed`; its five failures were
  all loopback HTTP socket-bind denials. The privileged rerun completed with
  no failures.
- `tools/verify_installed_schemas.py` verifies the packaged 34-schema
  inventory and exact digests.
- `git diff --check` is clean.

## Authority and handoff

The implementation preserves the audited G40 direct-consent flow, approved
version/tag validation, G41 project/workpad authority, and G16 replayable
review evidence. G45 may now add private references and pasted Run inputs;
G46.1 may then supply the first private `tailor-resume-for-job` Gig contract.

Provider-backed review quality and the private résumé UAT are intentionally
not asserted by deterministic G43 evidence. They remain explicit later UAT
and release evidence, not a claim hidden inside this completion audit.
