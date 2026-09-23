# P2-AUD-01 — Verify v0.1.7 baseline and audit the S10 local invocation route

**Status:** Complete; output in [`../evidence/P2-AUD-01-baseline-and-invocation.md`](../evidence/P2-AUD-01-baseline-and-invocation.md). Terra-reviewed: Terra `ctx_7ff479b90fc9` → Luna fix `ctx_461e730dc554` → Terra pass `ctx_57133c5bc2bb`. **Owner role:** Luna/max audit writer;
root reconciles release evidence and integration disposition. **Review:** Terra
independent review after the report is complete.

## Jira-style ticket

**User problem:** The checkout contains a `v0.1.7` commit/tag and candidate
artifact receipts, but an earlier publication-preparation receipt says the
local/remote tag and GitHub release were absent. The S10 adapter has source and
offline evidence, yet that does not prove the caller path, installed route,
live locality, cancellation behavior, or no-hosted-fallback policy.

**Intended behavior/outcome:** Produce one evidence-backed baseline that says
exactly which v0.1.7 source, artifact, installed identity, tag, publication,
CI, Debian, and UAT claims are proven, stale, conflicting, or still open; and
one caller map from the real Scout proposal/Run entry point through model
selection, adapter invocation, validation, persistence, and failure handling.
The report must leave the v0.1.7 release prerequisite explicit until exact
artifact/publication evidence agrees; it must not promote a local tag or commit
title into a ship claim.

**Scope:** Read-only source, schema, workflow, Git metadata, and existing
v0.1.7/S10 evidence review; source-to-caller and evidence classification;
offline/injected versus installed versus live/provider boundary analysis.

**Non-goals:** No source/config/schema/test edits, model/provider/daemon calls,
downloads, private-workpad access, publication, tag/push, release workflow,
new fallback, or implementation of any missing route.

**Tasks:**

1. Capture the exact current source baseline (`HEAD`, branch, local tag object,
   `pyproject.toml`, `uv.lock`, package version) and compare it with the exact
   candidate identities and byte digests recorded by R7 evidence.
2. Reconcile `SCOUT-R7-final-candidate-verification-20260921.md`,
   `SCOUT-R7-publication-preparation-20260921.md`, `SCOUT-12-user-uat-checklist.md`,
   and `.github/workflows/release.yml`. Separate candidate verification,
   release authorization, publication, and human UAT; record stale/conflicting
   observations without rewriting their historical reports.
3. Trace the actual local proposal caller through
   `scout_proposal_execution.execute_local_proposal`, `model_execution`,
   `adapters.factory.resolve_model_adapter`, and
   `adapters.ollama_local.OllamaLocalAdapter`; include `scout_proposal_records`,
   Run/journal artifacts, and the public CLI/Run entry point that can reach it.
4. Classify each S10 claim as source-verified, injected/offline-only,
   normally-installed, or live/provider-required. Record endpoint/model/digest
   identity, timeout/size/context bounds, malformed-output handling,
   cancellation/failure classes, and where the no-hosted-fallback decision is
   enforced versus merely expected of the caller.
5. Name the smallest follow-up proof and its authorization gate for every open
   claim; do not execute it in this story.

**Acceptance:**

- A reviewer can distinguish source/tag/package version, candidate artifact,
  installed-wheel proof, exact-tag CI, publication, and personal UAT without
  inference from a commit title. The current local-tag versus prior
  publication-prep conflict has an explicit disposition and does not silently
  clear the release prerequisite.
- The caller map names exact functions/files and shows the path's inputs,
  outputs, journal/Run receipts, and refusal or error semantics. It states where
  local-only transport is required and where caller policy still needs proof.
- Offline/injected proof is not labeled installed/live, and a successful adapter
  check is not labeled caller, privacy, or release acceptance.
- The report contains no new runtime behavior and a Terra reviewer can check
  every claim against a cited source or receipt.

**Required evidence:** Planned report at
`docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-baseline-and-invocation.md`
with a machine-readable claim matrix at
`docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-claims.json` (or an
explicitly justified equivalent). Include command provenance only for
read-only checks actually run.

**Dependencies:** None for the initial audit; use the S10 and R7 evidence
listed below. P2-FREEZE-04 consumes this report. The v0.1.7 release prerequisite
is a standing constraint, not a dependency to be waived by this ticket.

## Granular audit specification

### Baseline and release disposition

Inspect, without mutating the checkout:

- `git rev-parse HEAD`, `git show-ref --tags`, annotated versus lightweight
  tag object identity, `git show <candidate>`, `pyproject.toml`, and `uv.lock`;
- `.github/workflows/release.yml` preflight, exact-tag CI, artifact, publication,
  attestation, and clean-install jobs;
- the exact wheel/sdist paths and SHA-256 values in the R7 final-candidate and
  publication-preparation receipts, including their disposable nature;
- the recorded full offline matrix, installed verifiers, Scout smoke, Debian
  first failure, publication prerequisites, and UAT checklist.

The report must say “candidate verified locally, publication/UAT not approved”
unless a fresh exact publication receipt proves otherwise. A local `v0.1.7`
tag may prove only local tag presence and object identity; it does not prove a
remote tag, package index publication, or a user-installed artifact. If current
read-only observations disagree with historical receipts, retain both dates and
identify the boundary that changed or remains unknown.

### S10 route and failure boundaries

Trace these concrete seams and do not invent a second route:

| Concern | Primary source target | Required finding |
| --- | --- | --- |
| Caller and private-source join | `src/gigai/scout_proposal_execution.py` (`execute_local_proposal`, `_resolve_sources`, invocation/result publication) | Which authenticated posting/private revisions are joined; what is journaled before/after domain publication; what remains a proposal result versus an application or document effect |
| Run/goal authority | `src/gigai/run.py`, `src/gigai/model_execution.py`, `src/gigai/run_plan.py`, `src/gigai/journal.py` | Exact Run/goal/plan checks, receipt paths, cancellation/conflict behavior, and whether a failure terminalizes domain state |
| Model target selection | `src/gigai/model_targets.py`, `src/gigai/adapters/factory.py`, `src/gigai/config.py` | Explicit target/endpoint/model/digest/bounds and refusal when target is not local Ollama |
| Local transport | `src/gigai/adapters/ollama_local.py`, `src/gigai/adapters/port.py` | Loopback URL, identity-before/after check, cloud-marker refusal, response bounds, timeout, malformed response, and normalized usage/error categories |
| Public entry points | `src/gigai/cli.py`, `src/gigai/data/scout/gig.py`, proposal/run CLI modules and relevant tests | Whether the route is callable from installed/public CLI or only source/injected helpers; exact consent flags and no-model path |
| Durable output/readers | `src/gigai/scout_proposal_records.py`, `src/gigai/scout_report_readers.py`, `src/gigai/scout_projection.py` | Which artifacts are authoritative, how bytes/digests are redeemed, and whether report/projection is a view rather than a write authority |

For every boundary, classify “implemented source guard,” “offline/injected
evidence,” “installed evidence,” “live/provider evidence,” or “unproven.”
Cancellation means actual caller/transport behavior, not a CLI `Ctrl-C` analogy;
no-hosted-fallback means an enforced refusal or an explicit unresolved caller
policy, not a label in an adapter test.

### Output and edge cases

Record open questions for missing or conflicting evidence, including absent
artifact files, artifact/source digest mismatch, local-versus-remote tag
disagreement, package-index absence, model digest race, unavailable Ollama,
timeout, cancellation, malformed output, private source resolution failure,
journal conflict, retry/idempotency, and a caller that tries to use a hosted
target. Keep all of these as dispositions for later authorized work, not fixes
inside this documentation-only ticket.
