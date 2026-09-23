# P2-EVAL-03 — Synthetic evaluation pack and rubric

**Status:** Preparation complete, execution not authorized. **Owner:** P2-EVAL-03
only; no shared roadmap, README, product source, test, schema, or configuration
file was changed. The pack is synthetic and reviewable offline, but it is not
evidence of model quality, installed routing, caller behavior, live/provider
execution, or release readiness.
Current authoring is preparation, not an unbiased executed evaluation: the
cases were selected and drafted in one offline authoring lane, no backend was
run blind, no candidate outputs were evaluated, and no human gold exists yet.

## Scope and evidence boundary

This packet prepares a bounded 20-case Scout-shaped decision pack: 10 objective
cases and 10 judgment cases. It contains no real resume, preference, job,
private, or future-user data; each source is an explicitly fictional text block
with a stable UTF-8 byte digest. Five cases are marked held out (two objective,
three judgment); held-out status is independent from the expected envelope branch
and never disables integrity checks.

The packet records the proposed `gigai.typed_decision.v1` wrapper, its
deterministic-first rubric, setup-parity rows, receipt fields, and adjudication
queue. It does not implement a production evaluator, make a model/provider call,
start a daemon, search the network, install or download anything, or evaluate a
candidate output. Human gold, installed proof, local-model proof, hosted proof,
route/privacy proof, and release acceptance remain open gates.

Owned artifacts:

- `docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/cases.json` —
  20 immutable-on-freeze source blocks, criteria, expected envelope branches,
  objective derivations, judgment drafts, and pending adjudication records.
- `docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/grader-self-checks.json` —
  two known-good and six known-bad candidate vectors, authored but not executed
  against a production evaluator.
- `docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-manifest.json` —
  machine-readable case index, hashes, compatibility, grader, setup, receipt,
  and gate metadata.

The machine-readable pack and self-check file have SHA-256 digests recorded in
the manifest. Each case's `source.bytes_text` is hashed as exact UTF-8 bytes;
the digest is not a digest of a normalized or host-MIME representation.

The pre-edit `rtk git status --short` receipt already showed dirty v0.1.8
README/direction/roadmap/spike/task docs, `pyproject.toml`, deleted Scout test
files, and untracked evidence/tests/tools/S12 work. Those paths were not edited;
concurrent peer-owned Phase 2 evidence that appeared later was also left alone.

## Source receipts and compatibility

The ticket requires exact reuse analysis rather than a parallel contract. The
source receipts below were read from this checkout; line numbers are part of the
evidence boundary.

| Source | Relevant receipt and consequence |
| --- | --- |
| `docs/development/v0.1.8/phase-2/tickets/P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md:33-56` | Requires S08 envelope/R6 reuse, 15–30 stable synthetic cases, objective-versus-judgment split, deterministic-first grading, setup receipts, and P2-AUD-02 reconciliation. |
| `docs/development/v0.1.8/phase-2/tickets/P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md:60-87` | Requires offline reviewability, pending human gold, separate setup classes, no hosted fallback, and explicit preparation/execution boundaries. |
| `docs/development/v0.1.8/phase-2/tickets/P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md:91-117` | Defines case provenance fields and the shared `typed_decision` / `abstain` / `uncertain` / `refused_or_failed` states. |
| `docs/development/v0.1.8/phase-2/tickets/P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md:119-158` | Defines evidence checks, narrow judge fallback, setup fixed/varying fields, and failure dispositions. |
| `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md:41-52` | Holds source bytes, criteria, IDs, and logical tasks constant while allowing documented wrapper differences. |
| `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md:54-77` | Makes objective gold mechanical, keeps judgment labels human-adjudicated, permits multiple acceptable labels, and says 15–30 cases do not support broad ranking. |
| `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md:79-97` | Requires setup asymmetry, deterministic grading, and separate quality/cost/latency axes. |
| `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md:110-113` | Requires the four shared envelope outcomes. |
| `docs/development/v0.1.8/spikes/evidence/jev-typesafe-ai-structured-decisions-research.md:206-231` | Supplies the conceptual `gigai.typed_decision.v1` shape and explicitly separates status, evidence sufficiency, review recommendation, and evidence refs. |
| `docs/development/v0.1.8/spikes/evidence/jev-typesafe-ai-structured-decisions-research.md:233-255` | Requires frozen synthetic records, evidence allowlists, known-bad outputs, held-out variants, per-attempt identity/retry/usage/timing, and per-axis reporting. |
| `src/gigai/schemas/runtime-evaluation-pack.schema.json:1-38` | Existing installed pack contract: `schema_version=1.0`, `kind=runtime_evaluation_pack`, two-to-64 cases, `synthetic=true`, `r7-output-contract:1` criteria, and known-good/known-bad vectors. |
| `docs/development/evidence/v0.1.7/Scout/SCOUT-R6-contract.md:8-27` | R6 is domain-neutral, uses explicit local/Luna targets and consent, validates one good/bad vector, and does not claim release/live proof. |
| `docs/development/evidence/v0.1.7/Scout/SCOUT-R6-contract.md:29-45` | Existing R6 preserves input digest, raw/null output, error, wall time, retries, usage, invocation ID, setup identity, and deterministic source allowlists. |
| `src/gigai/runtime_comparison.py:178-209` | Existing loader validates the installed pack, uniqueness, synthetic-only input, supported grader identity/version, and grader vectors. |
| `src/gigai/runtime_comparison.py:282-343` | Existing deterministic grader checks strict output shape, criterion identity, evidence-ID allowlisting, expected verdict/status, and unsupported claims. |
| `src/gigai/schemas/runtime-comparison-attempt.schema.json:1-12` | Existing attempt receipt fields include setup identity, run status, per-case result/grade refs, wall time, retries, invocation ID, usage unknown, terminal, and observed identity. |

### Reuse versus proposed amendment

The pack reuses the existing `runtime-evaluation-pack` vocabulary where it is
actually compatible: versioned pack/case IDs, development/calibration/held-out
splits, synthetic inputs, evidence records, and known-good/known-bad validation
vectors. It also mirrors R6's independent setup attempts and receipt identity.

The proposed `gigai.typed_decision.v1` envelope is a documentation-level
wrapper for this preparation story, not an installed schema. It keeps S08's
fields (`schema_version`, `case_id`, `decisions`, `evidence_refs`,
`abstention`) and makes the four outcome branches explicit with `outcome` and
an optional `uncertainty` object:

```json
{
  "schema_version": "gigai.typed_decision.v1",
  "case_id": "p2e03-o01-extract-location",
  "outcome": "typed_decision|abstain|uncertain|refused_or_failed",
  "decisions": {
    "requirement_status": "supported|unclear|unsupported|not_applicable",
    "evidence_sufficiency": "sufficient|insufficient",
    "needs_human_review": true
  },
  "evidence_refs": ["src-o01-posting"],
  "abstention": null,
  "uncertainty": null
}
```

That wrapper is intentionally not silently asserted to conform to the existing
R6 output. R6's installed candidate output remains `r7-output-contract:1` with
`verdict`, `criteria`, and `unsupported_claims`; it does not accept the proposed
envelope, source-byte fields, pending-human-gold fields, or uncertainty branch.
The manifest records this as `proposed_documentation_only_no_runtime_schema_change`.
An eventual implementation packet must choose an explicit adapter/amendment
after P2-AUD-02 vocabulary reconciliation and P2-FREEZE-04 review.

## Case inventory

All rows below are in `cases.json`; the manifest is the machine-readable source
of the corresponding byte digests and gate status.

| Case IDs | Kind | Family | Split | Expected envelope | Gold state |
| --- | --- | --- | --- | --- | --- |
| `p2e03-o01-extract-location`, `p2e03-o02-extract-compensation` | objective | extraction | development | typed_decision | mechanically derived |
| `p2e03-o03-absent-start-date`, `p2e03-o07-focused-question` | objective | abstention/focused question | development | abstain | mechanically derived |
| `p2e03-o04-not-applicable-clearance` | objective | requirement status | development | typed_decision | mechanically derived |
| `p2e03-o05-evidence-id-allowlist` | objective | evidence integrity | development | typed_decision | mechanically derived |
| `p2e03-o06-latest-amendment` | objective | ordered extraction | development | typed_decision | mechanically derived |
| `p2e03-o08-enum-membership` | objective | schema/enum | development | typed_decision | mechanically derived |
| `p2e03-o09-unstated-oncall` | objective | uncertainty | final_held_out_acceptance | uncertain | mechanically derived |
| `p2e03-o10-unauthorized-effect` | objective | refusal/failure | final_held_out_acceptance | refused_or_failed | mechanically derived |
| `p2e03-j01-kubernetes-duration`, `p2e03-j02-python-production` | judgment | support versus unclear | calibration | typed_decision | pending human adjudication |
| `p2e03-j03-afterhours-constraint` | judgment | constraint handling | calibration | typed_decision | pending human adjudication |
| `p2e03-j04-equivalent-experience` | judgment | evidence sufficiency | calibration | typed_decision | pending human adjudication |
| `p2e03-j05-travel-question` | judgment | focused question | calibration | typed_decision | pending human adjudication |
| `p2e03-j06-conflicting-location` | judgment | source conflict | final_held_out_acceptance | uncertain | pending human adjudication |
| `p2e03-j07-soc2-support`, `p2e03-j08-work-authorization` | judgment | evidence/constraint | calibration | typed_decision | pending human adjudication |
| `p2e03-j09-kafka-or-nats` | judgment | multi-acceptable support | final_held_out_acceptance | typed_decision | pending human adjudication |
| `p2e03-j10-conflicting-onsite-preference` | judgment | constraint conflict | final_held_out_acceptance | uncertain | pending human adjudication |

### Objective derivation

Objective labels are derived from explicit source rules, not model output:

- literal fields (`LOCATION`, `COMPENSATION`) are extracted exactly;
- explicit absence yields `abstain` or `uncertain`, never an invented value;
- `not_applicable` is used only when the source explicitly says the criterion
  does not apply;
- source references must be in the case allowlist and required source IDs must
  be present;
- the highest ordered amendment wins only where the case criterion says so;
- unauthorized effects yield `refused_or_failed` and no published decision;
- enum membership is closed to `supported`, `unclear`, `unsupported`, and
  `not_applicable`.

The objective `gold` objects state `mechanically_derived` and a rule
provenance. They do not claim model correctness. The two held-out objective
cases remain integrity-checked even though their source/gold material must be
kept out of future candidate prompts and grader-tuning examples. A future
prompt renderer must expose only the held-out case's source bytes and criteria;
it must keep `expected_envelope`, `gold`, and `judgment_draft` on the evaluator
side until the run is complete.

### Judgment queue

Every `j01`–`j10` case has `gold.status=pending_human_adjudication`,
`human_adjudication.status=pending`, null adjudicator identity/date, blind-to-
backend mode, an empty disagreement log, and no finalized acceptable-label
set. The `judgment_draft` is machine-authored pack preparation only; it is not
a model call and never counts as gold. A later adjudicator must read the source
bytes directly, record role/identity and date, decide or override the draft,
record disagreements, and may record multiple acceptable labels. No name, date,
decision, or human gold was backfilled here.

## Worked grading examples

These examples show the format without executing a model or evaluator.

1. `p2e03-o01-extract-location` has one literal source and one allowlisted ID.
   The expected branch is `typed_decision`; `requirement_status=supported`,
   `evidence_sufficiency=sufficient`, and `extracted_fields.location=Denver,
   CO`. A candidate that cites `src-o01-posting` passes the objective vector;
   an invented ID fails evidence integrity even if the text is otherwise right.

2. `p2e03-o03-absent-start-date` has no usable start date. The expected branch
   is `abstain`, with no decision object, one allowed evidence ref, and
   `missing_required_field`. A candidate that invents `2026-10-01` is an
   over-confident branch failure, not a low-quality score.

3. `p2e03-j02-python-production` distinguishes Python scripting from production
   service construction. Its draft says supported with insufficient evidence,
   but human gold is still pending and may select `unclear` or `unsupported`.
   A draft agreement cannot be reported as model quality.

4. `p2e03-j05-travel-question` tests whether one focused question resolves the
   missing travel constraint. The proposed question is a draft only; the later
   human record decides usefulness and may document disagreement.

## Deterministic-first grader

The behavior specification is deliberately staged:

1. Validate JSON/envelope shape, required fields, allowed outcome, case ID, and
   the case's schema version.
2. Compare actual and expected outcome branches. Report over-confident
   mismatches (for example `typed_decision` where `abstain` is required) apart
   from under-confident mismatches (for example `abstain` where an objective
   typed decision is required).
3. Check every evidence ref against the case allowlist and source digest; check
   forbidden/invented claims for every branch, including abstention and refusal.
4. For a valid `typed_decision`, check enum membership and objective exact gold
   or a human-approved acceptable-label set. Do not run semantic label checks
   against a pending judgment draft.
5. Use a named LLM judge only for irreducible semantic text after stages 1–4.
   The current judge identity is null because no judge is authorized. Any later
   judge must receive fenced/escaped case content, return structured fields, and
   have verdicts validated against real human gold before its labels count.

`grader-self-checks.json` contains two known-good vectors and six known-bad
vectors: invented evidence ID, invalid enum, overconfident missing-field answer,
unauthorized decision, forbidden claim, and malformed JSON. The vectors specify
the intended failure class, so a future validator must fail for the intended
reason. They are authored offline; no production evaluator or candidate output
was run in this story.

## Setup parity and receipts

Held constant across a future run: pack/case IDs and source bytes, task criteria,
shared envelope revision, grader revision, thresholds, abstention rules, and the
wall-time boundary. Per setup: wrapper/system text, tools/context, retry policy,
model ID/digest, runtime/CLI version, endpoint/locality, permissions, token
usage, cost, cold/warm state, cancellation, and observed terminal failure.

The manifest records four planned rows:

| Setup | Route class | R6 relation | Current state |
| --- | --- | --- | --- |
| `qwen38-ollama-local` / `qwen_ollama` | local adapter | `ollama_local` | not authorized/not observed; digest and settings null |
| `luna-codex-cli` / `luna_codex` | CLI/harness | `codex_cli` | not authorized/not observed; identity and destination null |
| `jev-hosted-decision-api` / `jev_hosted_decision_api` | hosted | future/non-R6 | not authorized/not observed; provider consent required |
| `future-provider-route` | future provider | future/non-R6 | disabled placeholder; no route contract |

No row permits silent fallback. Private inputs are excluded from every planned
row; a hosted row must be separately authorized and cannot be used as a repair
path for local or CLI failure.

Per-case/per-attempt receipt fields are machine-listed in the manifest. The
minimum required record includes case/source/pack/wrapper digests, setup and
attempt IDs, configured and observed retries, malformed/timeout/cancellation
flags, terminal failure class, raw output reference, envelope/evidence/label/
abstention checks, judge identity and human-gold validation, model identity and
runtime versions, token usage and unknown-usage marker, cost amount/currency,
latency, cold/warm state, timestamps, tools/context digest, endpoint locality,
and permission profile. `quality`, `coverage_and_abstention`, `cost`,
`latency`, `retries`, and `terminal_failures` remain separate axes. Unknown
usage/cost is recorded as unknown; local compute is not fabricated as zero
cost, and timeout/cancellation is not converted into a low-quality label.

## Proposed quantitative dispositions

These are future comparison guardrails, not acceptance results:

- Offline pack integrity: every source digest must match, case IDs must be
  unique, and known-good/bad vectors must pass/fail for their intended reasons.
  A failure is `fix/re-evaluate` before any model assessment.
- Objective quality: require 100% mechanical integrity on objective cases for a
  comparison to proceed. Any invalid envelope, wrong branch, invented source
  ref, forbidden claim, or enum error is preserved per case and triggers
  `fix/re-evaluate` or `defer assessment`, not a blended score.
- Judgment quality: report acceptable-label agreement only after human gold is
  complete. Because this is a 20-case methodology pilot, an exploratory
  `>=0.80` acceptable-label agreement can be a review trigger, but is not a
  release gate, broad ranking, or calibration claim.
- Any unresolved human gold, missing receipt axis, setup identity mismatch,
  timeout, cancellation, or terminal transport failure yields `defer
  assessment` for that case/setup. A repeated quality or authority issue that
  affects scope is escalated as `reassess release scope with the user`.

Retries must be counted and preserve the original malformed/failed output. A
retry does not erase a failure, and no hosted fallback, omitted case, skipped
human label, or single quality/cost/latency score is permitted.

## Freeze inputs and open gates

P2-AUD-02 runs independently; this story did not wait for, poll, or consume its
peer output. At the initial file inventory no P2-AUD-02 evidence path was
listed; concurrent work may add peer-owned files later, and neither that
initial absence nor a later filename is evidence of the peer's completion or
content. Final freeze must read and reconcile the authoritative peer output
when its owner delivers it.
Before P2-FREEZE-04, reconcile its operation vocabulary and source-boundary
findings against the planned mapping in the manifest:

| Planned mapping | Current P2-EVAL-03 spelling | Freeze action |
| --- | --- | --- |
| requirement status | `supported`, `unclear`, `unsupported`, `not_applicable` | confirm exact peer vocabulary and retain an explicit alias map if it differs |
| evidence sufficiency | `sufficient`, `insufficient` | confirm whether peer uses sufficiency versus coverage and document mapping |
| outcome | `typed_decision`, `abstain`, `uncertain`, `refused_or_failed` | confirm transport/error versus semantic uncertainty boundary |
| source authority | exact UTF-8 source bytes plus IDs/digests | confirm committed/source record authority and no private-data ingress |

Open gates include human adjudication/human-gold records, later operator
consent, installed setup proof, explicit model/provider execution authorization,
and final interface freeze. Human gold and explicit execution authorization are
downstream evaluation/release gates carried forward by P2-FREEZE-04, not
prerequisites for creating this documentation freeze. The completed offline
preparation is not blocked on obtaining user adjudication now; it is explicitly
not ready to claim judgment gold or model quality.

## Validation receipts

Only bounded, read-only offline checks were used for these artifacts:

```text
rtk python3 -m json.tool docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/cases.json
rtk python3 -m json.tool docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/grader-self-checks.json
rtk python3 -m json.tool docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-manifest.json
rtk python3 -c 'hash each cases.json source.bytes_text as UTF-8; report count and mismatches'
rtk shasum -a 256 docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/cases.json docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/grader-self-checks.json
```

The source-hash receipt reported `cases 20 hash_mismatches 0`. The manifest
records pack SHA-256 `fa2f486f74628be295ce585c3f30066cd8162507652e5ef69fa98032d08c1143`
and self-check SHA-256
`d9bc8517dec063a85456e28b9b5c916023f6b34210f22ebb72fd8babc95a6ed4`.
Receipt summary: each of the three `python3 -m json.tool` commands exited 0;
the final cross-file check exited 0 with `cases 20 self_checks 8 mismatches []`;
`rtk shasum -a 256 docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/grader-self-checks.json`
returned the self-check digest above; and the owned-file trailing-whitespace
check exited 1 with no output (no matches).
The raw `rtk proxy git diff --check` receipt was exit 2 because pre-existing
dirty files contain trailing whitespace at
`docs/development/v0.1.8/spikes/README.md:5,10`,
`docs/development/v0.1.8/spikes/S07-execution-modes-and-cost-aware-orchestration.md:4`,
`docs/development/v0.1.8/spikes/S10-ollama-invocation-and-harness-onboarding.md:5`, and
`docs/development/v0.1.8/tasks/V018-01-workpad-navigation-and-readable-artifacts.md:6`; the owned
P2-EVAL-03 files had no trailing-whitespace matches in a separate read-only
check. Those unrelated findings were preserved rather than cleaned up.
No test suite, production evaluator, model, provider, daemon, search, package
installation, commit, push, publication, or shared file edit was performed.
