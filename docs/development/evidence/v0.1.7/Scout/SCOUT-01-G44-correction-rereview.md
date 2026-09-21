# SCOUT-01 — G44 correction and fresh review

## Scope and authorization

On 2026-09-08 the operator explicitly instructed the coordinator to correct
G44 against the unchanged approved baseline and re-review with Claude retained.
The coordinator applies the edits and invokes the existing CLI on that direction.
This is operator-directed, agent-applied work, not a claim that the operator
personally edited the file or typed the Run command. No approval/consent
artifact is manufactured, and no new baseline approval is needed.

Only the public G44 review subject is corrected. Original sealed subject and
baseline snapshots, provider responses, findings, verification, and adjudication
records stay intact. Runtime source changes from the earlier recovery remain
untouched. This task does not activate G44 or implement SCOUT-02.

## Correction map

| Existing finding | Baseline | Subject correction |
| --- | --- | --- |
| `finding_28f68c5d-eb68-46e7-8902-05f8a4ef0299`, repeated as `finding_06736cc8-5da5-4feb-aeaa-f8075f6cd476` | Required behavior 5 | `one_off` explicitly creates no candidate, Gig, Run, or durable request-text record; explicit one-off selection is not a retention exception. Only separately selected private candidate creation can retain request text. Acceptance case 8 covers this. |
| `finding_f6cca09e-5e68-4479-8ff8-2eafb63a396f` | Required behavior 2–3 | Routing explicitly excludes clipboard inspection, browser/tool use, and target effects, in addition to existing restrictions. Acceptance case 9 covers the complete boundary. |

The outcome description/diagram also includes the already-declared
`needs_operator_input` result, matching baseline requirement 1. No new route
or runtime capability is added.

Original subject SHA-256:
`92434595cd00b311e489c8cef9de7bb529482f43bebcc0de4dd5bda8642eeefc`.
Unchanged baseline SHA-256:
`a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d`.
Existing approval:
`requirements_baseline_approval_feffb475-1357-4a26-a986-a5456a2b212d`.

The fresh Plan will reference original Plan
`run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca`, preserving the approved
baseline identity. Claude and Luna remain reviewers, Terra verifier, and Sol
adjudicator. The latter two are invoked only if findings require them.

## Completion evidence — 2026-09-08

**Outcome:** correction/re-review completed under the operator's explicit
delegation. The two original defects are corrected; the fresh review has no
accepted or deferred findings after adjudication. This is not a zero-finding
report and not a `no_fix_required` receipt.

| Identity or check | Recorded value |
| --- | --- |
| Corrected subject SHA-256 | `4b196f7fea9bf403e185cc477a4d691aac623cafd716f6ea1baa6516c53212cd` |
| Fresh Plan | `run_plan_3322ecf0-b016-4730-bdc2-95e953c4f434` |
| Plan digest | `sha256:db6202be5d22c341488f764cc887d18ca9950bff56f9250df9b9e085688d04bf` |
| Fresh Run | `run_23375ef2-59f8-4139-848f-37a70da3f4c7` |
| Provider terminal commit | `6f6aca1549c548872b9cfe6441873e8501f763f9` |
| Run terminal commit | `becebbda94d3b492cd21bc47b6933582750e3ad6` |
| Run interval | 16:50:26–16:51:41 UTC |
| Provider / Run status | `complete` / `succeeded` |
| Finding counts | 3 emitted; 3 rejected; 0 accepted; 0 deferred |
| Target before/after digest | Both `sha256:35cf9fa0714cb5a2a0d4a89ab4a8e9e681d441fa89414f98595e6dc55f7f0b38` |

All four CLI-backed invocations succeeded without invocation errors:

| Participant | Invocation | Result |
| --- | --- | --- |
| Claude reviewer | `inv_1ba64d8e-3664-4326-95a6-042ba673c9fe` | Three typed findings |
| Luna reviewer | `inv_3b6dc88e-4d44-4e5b-8009-06e53cbf92b3` | `{"findings":[]}` |
| Terra verifier | `inv_055f2d74-f36e-4b4e-bbb9-3a58b9e17dfa` | Checked every finding against both sealed inputs |
| Sol adjudicator | `inv_82a3e873-bc25-4597-bb53-688a010c4b65` | Rejected all three as requirements defects |

### Fresh finding dispositions

- `finding_928e4339-98f6-436f-b03b-0f9c73a76f32`: alleged sixth route.
  Terra contradicted it; Sol rejected it. The five diagram entries map to
  the five exact routing outcomes, which are explicitly enumerated below.
- `finding_0216aafc-81bc-49c3-a024-45bf1379f6e2`: alleged permissive one-off
  retention. Terra contradicted it; Sol rejected it. The corrected subject
  unconditionally forbids retention, including explicit one-off selection.
- `finding_c7519310-478e-480f-ab98-16e54549a4c6`: differing acceptance-list
  numbering/additional coverage. Terra marked the observation verified while
  explicitly stating it does not conflict with the baseline; Sol rejected it
  as a requirements defect. All required coverage is present.

The coordinator checked these dispositions against the actual subject and
baseline. No further subject edits are supported by those three findings.
All finding records remain in their immutable Run history; rejection does
not delete them, and prior findings are linked to corrections rather than
rewritten retroactively.

### Scope of closure and remaining limitations

SCOUT-01's **correction/re-review branch** is complete under the operator's
latest explicit instruction to have the coordinator apply the corrections and
run the review. Historical handoff wording called for a human-applied edit and
personally issued terminal commands. This record instead states the actual
delegated execution; it must never be used as proof of manual keyboard actions.
The separately issued baseline approval remains unchanged and authenticated.
No general relaxation of future operator-only decisions is implied.

No `provider-review closeout` command was invoked: the fresh report contains
three rejected findings and is not eligible for the zero-finding no-fix branch.
The correction map, original verified findings, fresh bound review and complete
adjudication are the completion evidence. SCOUT-02 is ready for its own bounded
implementation dispatch, but this task did not activate or implement it.

Accounting remains as disclosed in [runtime recovery](SCOUT-01-live-review-recovery.md):
67,361 recorded input tokens and 3,284 output tokens, with total and monetary
cost unknown. The output-reservation budget and offline `remaining_budget`
presentation are not proof of total-token or billing enforcement. No API target
was selected. These limitations are not claimed fixed by a document correction.

## Verification

- Fresh focused Run Plan/provider-review suite: **17 passed in 45.60 seconds**.
- `git diff --check`: passed.
- Corrected subject and unchanged baseline hashes match the sealed Plan.
- Runtime source was not changed; the earlier **734 tests / 80 subtests** result
  remains prior runtime evidence, not a newly executed full suite this turn.
- Live provider evidence was inspected separately from fixture tests, including
  every reviewer response, verifier outcome, adjudication, and committed Run status.
- No commit, release, historical artifact rewrite, or baseline reapproval.
