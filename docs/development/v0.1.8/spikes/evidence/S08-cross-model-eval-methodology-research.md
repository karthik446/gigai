# S08 — Cross-model decision evaluation methodology

**Date:** 2026-09-20
**Scope:** v0.1.8 research only, per the
[S08 spike brief](../S08-cross-model-decision-evaluation-methodology.md). No
adoption, model call, paid API usage, or runtime change is authorized by this
document. No live cross-backend comparison was run; all numbers below are
estimates or citations of third-party public material, never measurements.
**Question:** How do we run the same Scout-shaped decision task fairly across
local Qwen/Ollama, hosted GPT, Luna/Codex, and Claude Sonnet, and get a
comparison that means something rather than a demo that favors whichever
backend happens to match the grader's own style?

## Executive summary

No single public harness already does what this spike needs — a small,
frozen, cross-*setup* typed-decision comparison with human-adjudicated gold
and separated quality/cost/latency reporting. But three pieces of prior art
are directly reusable, each for a different slice:

- **TypeSafe's `system-one-adapter-python`** documents the exact retry/debug
  mechanics a comparison harness needs for malformed-output recovery
  (`n_retry_malformed_structure`, `response.debug.llm_attempts`,
  `retry_reasons`) and a discrete-vs-probability answer-mode split that maps
  cleanly onto this spike's decision envelope.
- **DeepSeek Harness's** append-only session log / trajectory model, and the
  third-party `dsh-eval` tool built on it, give a concrete per-attempt
  logging schema (steps, tokens, `llmMs`/`toolMs`/`ttftMs`, retry counts,
  invalid-tool-call counts, judge verdict) and a paired-A/B comparison
  convention (`dsh eval compare base.json candidate.json` → signed `B - A`
  deltas) worth copying the *shape* of.
- **Inspect AI's `model_graded_qa`/`model_graded_fact`** scorers document a
  disclosed, swappable judge-model parameter, a grading template with
  injection-resistant prompt construction, and last-match-wins regex grade
  parsing — a concrete pattern for the "name the judge, don't trust it
  blindly" requirement in the brief.

None of these tools is a drop-in Scout harness. This document adapts their
concrete mechanics — not their frameworks — into a case-pack format,
gold-labeling process, setup-parity table, and grading design that stays
compatible with the existing Jev/Qwen pilot's conventions
([jev-typesafe-ai-structured-decisions-research.md](jev-typesafe-ai-structured-decisions-research.md),
[qwen38-scout-local-eval.md](qwen38-scout-local-eval.md)).

## 1. Existing harness patterns surveyed

### 1.1 DeepSeek Harness (`deepseek-ai/deepseek-harness`)

DeepSeek Harness is an official DeepSeek AI open-source agent framework,
in developer preview, built on a plugin kernel ("Cordis") where models,
tools, skills, sessions, sandboxes, storage, loops, scheduling, and the UI
are all swappable plugins.[^ds-repo][^ds-site]

Mechanics directly relevant to this spike:

- **Append-only session log as the source of truth for evaluation.**
  Everything the model sees or does — system prompts, reasoning, tool calls
  and results, subagent scheduling, context injections — is written to an
  append-only log (`session.v3.jsonl.zstd` in the current format
  generation), one `SessionEvent` row per `(session_id, seq, type, time,
  data)`, read back in `seq` order.[^ds-persist][^ds-arch] This is a strong
  match for the brief's "per-attempt debug history" requirement: instead of
  a harness re-deriving what happened from separately-kept fields, every
  attempt (including retries, tool calls, and malformed outputs) is one more
  event in the same ordered log, and a **Trajectory view** lets a reviewer
  inspect records "by source" and resume/fork/search/replay the same event
  stream.[^ds-site] For S08's case-pack, this argues for logging each
  backend's full attempt sequence (not just the final answer) in an
  ordered, appendable structure keyed by case ID and attempt number, so a
  human adjudicator or later re-grader can replay exactly what the model
  saw and produced without re-running it.
- **A benchmarking-specific run mode.** DeepSeek Harness documents four
  runtime modes — Standard (full toolset), Code (model-generated
  orchestration), Minimal (basic tools, meant for benchmarking), and Creator
  — as an explicit acknowledgment that a fair benchmark needs a reduced,
  fixed tool surface distinct from the harness's normal operating
  mode.[^ds-site] This directly supports the brief's setup-parity
  requirement: "tool access" is called out by DeepSeek's own documentation
  as a variable to fix deliberately for evaluation, not an emergent
  side-effect of whatever mode a user happens to be in.
- **`dsh-eval` (third-party, built on top of deepseek-harness).** This is
  not an official DeepSeek repository, but it is a concrete, publicly
  documented benchmark schema built directly on the harness's session log,
  and its metric vocabulary is worth citing because it is close to what
  S08 needs to report. Its benchmark YAML defines `name`, `model`,
  `profile` (e.g. `headless`), `command`, `trials`, `timeoutMs`, `seed`,
  `cases[]`, and a `pricing` table; each case has `id`, `prompt`,
  `workspace`, `expected.tool` (scripted tool-selection check), and
  `expected.check` (a shell exit code as the success criterion).[^dsh-eval]
  Its computed metrics separate **success/accuracy** (`taskSuccess`,
  `toolSelectionAccuracy`, tool success rate), **resource usage** (`steps`,
  token buckets, context consumption, cost via the pricing table),
  **performance** (`llmMs`, `toolMs`, `ttftMs`, `latencyMs`), **robustness**
  (`llm/retry` events, invalid-tool-call counts), and **quality** (an LLM
  judge's "final answer score" and hallucination flags) as distinct
  buckets rather than one blended number — directly consistent with this
  spike's "quality, cost, and latency stay separate" requirement.[^dsh-eval]
  Its `dsh eval compare base.json candidate.json` command computes signed
  `B - A` deltas across pooled metrics for a paired comparison, and `dsh
  eval import` supports "keyless replay" — importing recorded sessions from
  other harnesses (it names `codex` and `claude-code` explicitly) for
  offline comparison without re-spending API credit.[^dsh-eval] The
  replay-from-recorded-log idea is directly reusable for S08: once a
  backend's typed-decision responses are captured once, later re-grading
  (e.g. after a grader bug fix, as happened in the Qwen pilot) should not
  require a second paid call.

Caveat: `dsh-eval` is community tooling, not a DeepSeek-published evaluation
methodology, and its judge-quality metric is described only at the level of
"a judge model scores the final answer and flags hallucination" — the
public README does not document its judge prompt template or disagreement
handling, so S08 should not borrow its judge design, only its metric-bucket
vocabulary and paired-comparison convention.

### 1.2 TypeSafe `system-one-adapter-python`

Already partially surveyed in the Jev research doc; this pass reads the
adapter README directly for the three mechanics the brief calls out by
name.[^ts-adapter]

- **Discrete vs. probability answer modes.** The adapter's
  `llm_answer_mode` client parameter takes `"discrete"` (one value per
  question) or `"probabilities"` (a per-label distribution). This maps
  directly onto the difference between a plain enum answer
  (`requirement_status: "supported"`) and a distribution over that enum —
  useful for S08 because it gives a concrete, provider-agnostic way to
  request either mode from an LLM backend that doesn't have Jev's native
  typed-answer API, rather than inventing a bespoke prompt convention per
  backend.
- **Malformed-output retry handling.** `n_retry_malformed_structure`
  controls a retry budget shared across a single evaluation call; on schema
  failure, the adapter resubmits with a correction message while preserving
  the earlier response and correction messages in context (not discarding
  them).[^ts-adapter] This is a specific, citable pattern for the "retries
  are logged, not silently discarded" requirement already followed in the
  Qwen pilot (original responses preserved even after `scoring-repair-rerun`
  fixed the grader).
- **Per-attempt debug history.** `response.debug` exposes `llm_attempts`,
  `retry_reasons`, and normalization diagnostics; each attempt records
  message snapshots, `model_request_parameters` (schema/structured-output
  settings), the raw `llm_response`, `debug_info`, and model/provider/error
  identity. A call that fails before any model response is returned leaves
  that attempt's response as `None` rather than omitting the attempt
  entirely.[^ts-adapter] This is the closest existing precedent to the
  brief's "record configured retry limit, observed retry count, malformed-
  schema retry count, and terminal failure class" requirement, and it
  additionally shows a concrete convention for representing a
  request/transport failure as a still-logged attempt with a null response,
  which S08's case log should copy.
- **Logging fields for cost/speed comparison.** `response.usage` reports
  `input_tokens_total`, `output_tokens_total`, `n_retries`, and `latency`
  on a `SystemOneResponse`-shaped object, explicitly to enable comparing an
  LLM-backed adapter run against real Jev.[^ts-adapter] This is a direct,
  reusable field list for S08's per-attempt logging table.

### 1.3 Inspect AI (`UKGovernmentBEIS/inspect_ai`)

Inspect is the UK AI Safety Institute's open-source LLM evaluation
framework, built around Dataset (cases with `input`/`target`), Solver (how
the model is run), and Scorer (how output is graded).[^inspect-repo] Two of
its scorers are directly relevant to S08's LLM-judge requirement:
`model_graded_qa` and `model_graded_fact`.[^inspect-scorer]

Concrete, citable mechanics:

- **The judge model is an explicit, disclosed parameter, not an implicit
  default.** Both scorers take `model: list[str | Model] | str | Model |
  None = None` and a `model_role: str | ModelRole | None = "grader"`; when
  `model` is a list, "each model grades independently and the grades are
  combined by `reducer`" (default `"majority"`).[^inspect-scorer] This is a
  concrete pattern for the brief's "name which model judges, disclose that
  choice as a variable" requirement — Inspect makes the judge identity a
  first-class, loggable configuration value, and supports a judge panel
  with majority-vote reduction as a documented option if a single judge's
  idiosyncrasies are a concern.
- **Injection-resistant grading-prompt construction.** The grading template
  is parameterized on `question`, `answer`, `criterion`, and `instructions`,
  and the implementation "neutralize[s] structural delimiters in all
  dataset-controlled inputs so a model cannot inject fake `[END DATA]` /
  `[BEGIN DATA]` markers into the judge prompt."[^inspect-scorer] Relevant
  precedent given the Qwen pilot's own `injected_posting_instruction` case
  and RUNTIME-01's prompt-injection framing — a case-pack grading template
  should escape or fence untrusted case text the same way before handing it
  to any judge model.
- **Verdict parsing that resists reasoning-trace injection.** Grade
  extraction is regex-based with "a leading greedy `.*`" so the match binds
  to the *last* grade mention in the judge's output, explicitly to prevent
  an earlier, attacker-influenced mention inside chain-of-thought reasoning
  from being picked up as the verdict; unrecognized verdicts are a parse
  failure, not a silent fallback to some default grade.[^inspect-scorer]
  This is a specific, citable technique worth adopting verbatim in S08's
  grader: prefer the final structured field over any free-text reasoning,
  and treat an unparseable judge output as a grading failure to log, not as
  an implicit "no".

Inspect's model-graded scorer is judge-vs-candidate agreement by design; it
does not itself check the judge against independently human-adjudicated
gold. S08 must add that check itself (Section 3.4) — Inspect only supplies
the mechanics for running and disclosing the judge, not for validating it.

### 1.4 Other frameworks — scoped citations only

- **promptfoo** documents an explicit deterministic-vs-model-graded split in
  its own terms: "Deterministic evaluation metrics are programmatic tests
  run directly on the LLM output... fast and reliable for objective checks"
  versus "Model-assisted evaluation metrics rely on LLMs... ideal for
  subjective qualities."[^promptfoo] Its `llm-rubric` assertion is
  explicitly an LLM-as-judge check and supports pointing the grader at a
  specific provider via an assertion-level `provider` field, rather than
  silently using whatever provider key is available in the
  environment.[^promptfoo] This is cited only for the deterministic/judged
  split terminology and the "grader is a configurable, not ambient, choice"
  convention — S08 does not need promptfoo's YAML config format or its
  broader prompt-testing feature set.
- **OpenAI `evals`** documents a `CompletionFn` protocol so an eval can
  target "prompt chains or tool-using agents," not just a single
  completion call, and a registry naming convention
  `<eval_name>.<split>.<version>` for grouping comparable eval
  runs.[^openai-evals] The naming convention (name/split/version) is
  directly reusable for S08's case-pack and schema versioning (Section 2.2
  already uses a `schema_version` field per the Jev doc's contract; OpenAI's
  convention supports adding an explicit case-pack version/split identifier
  alongside it). Not otherwise mined further — OpenAI evals' registry and
  CLI tooling are not a fit for a 15–30 case frozen pack.
- **EleutherAI `lm-evaluation-harness`** documents its `doc_to_text` /
  `doc_to_target` task-YAML convention, where a `doc` is a free-form dict
  and `doc_to_target` supplies the gold answer (an index for multiple
  choice tasks).[^lm-eval-harness] Cited only for the general "a case is a
  document plus a formatter function that extracts the gold answer"
  pattern; the harness's actual runner is built around static
  multiple-choice/perplexity benchmarks over large public datasets, which
  is not the shape of a 15–30 case, human-adjudicated, tool-using decision
  task, so its execution engine is not otherwise reused here.

### 1.5 What is genuinely new to this spike, not covered by prior art

None of the surveyed harnesses combine: (a) a small, versioned, hand-
adjudicated case pack (all of the above assume either large public datasets
or CI-scale synthetic suites), (b) an explicit split between objective and
judgment cases with a recorded human adjudicator identity per judgment
case, and (c) comparison across *fully independent, non-drop-in-replaceable
setups* (a hosted API, a CLI agent harness, and a loopback local model)
rather than swapping a `model=` string inside one harness. Sections 2–4
below are S08's own design for that gap, built using the mechanics
surveyed above.

## 2. Frozen case-pack format

### 2.1 Case schema

Each case is a single JSON (or YAML) document, versioned as a set (the
whole pack has a `pack_version`, each case has a `case_id` stable across
that version). Following OpenAI evals' name/split/version convention
(§1.4) and the Jev doc's `schema_version` field:

```json
{
  "pack_version": "s08.posting-evidence-triage.v1",
  "case_id": "pet-001",
  "case_kind": "objective" ,
  "state": {
    "posting": { "id": "posting-001", "text": "..." },
    "evidence_items": [
      { "id": "evidence-001", "text": "..." },
      { "id": "evidence-002", "text": "..." }
    ]
  },
  "question": {
    "id": "requirement_status",
    "instructions": "Given the posting requirement and the evidence items, classify whether the evidence supports, contradicts, or is silent on the requirement.",
    "criteria": {
      "supported": "At least one evidence item explicitly and unambiguously satisfies the requirement.",
      "unclear": "Evidence exists but does not unambiguously resolve the requirement either way.",
      "unsupported": "No evidence item addresses the requirement, or evidence contradicts it.",
      "not_applicable": "The requirement does not apply given other stated facts in the posting."
    }
  },
  "gold": {
    "label": "unclear",
    "acceptable_labels": ["unclear"],
    "expected_envelope_outcome": "typed_decision",
    "rationale": "Evidence-002 mentions a related but not identical skill; a reasonable adjudicator could read it as partial support, but it does not unambiguously satisfy the stated requirement. The evidence is present and readable, so a confident typed_decision of 'unclear' is the expected outcome, not abstention.",
    "provenance": {
      "drafted_by": "claude-sonnet-draft",
      "adjudicated_by": "human:<adjudicator-id>",
      "adjudication_date": "YYYY-MM-DD",
      "adjudication_mode": "blind-to-backend",
      "disagreement_log": []
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": ["evidence-002"],
    "forbidden_invented_claims": ["candidate has 5 years of X"],
    "min_evidence_refs": 1
  },
  "known_bad_variant_of": null,
  "held_out": true,
  "notes": "Deliberately ambiguous case; do not use as a wrapper/prompt example for any backend. held_out and expected_envelope_outcome are independent: this case is held-out AND expects a confident typed_decision, not abstention — ambiguity in the gold label itself is not the same as insufficient evidence to decide at all."
}
```

Field notes:

- `case_kind` is `"objective"` or `"judgment"` (§3.1) and is never
  inferred from the label — it is set when the case is authored, before
  any draft label exists, so a judgment case cannot quietly become
  "objective" just because a frontier model was confident about it.
- `gold.acceptable_labels` is a list, not a single string, to support the
  brief's requirement to "allow the gold set to record genuinely ambiguous
  cases and more than one acceptable answer rather than forcing a single
  correct string." A single-answer case simply has a list of length 1.
- `gold.provenance` separates `drafted_by` (may be a frontier model, tagged
  as such, never anonymized as "gold" directly) from `adjudicated_by` (must
  be a named human identity or role, never a model). `disagreement_log`
  is populated when adjudicators disagreed (§3.3) rather than silently
  collapsed to one adjudicator's preference.
- `held_out: true` cases are never shown to anyone drafting wrapper text or
  tuning the grader — this echoes the coordinator review's specific
  criticism of the Qwen pilot ("prompts disclose much of the intended
  answer") and the brief's "held-out variants not shown in prompts or
  grader-tuning examples; no answer-leading wording."
- `known_bad_variant_of` links a deliberately-broken case (invalid enum,
  invented evidence ID, malformed structure expected) back to its
  well-formed sibling case ID, reusing the Jev doc's "known-bad outputs"
  list (§"Frozen cases and grader" in the Jev doc).

### 2.2 Shared decision-envelope schema (reused from Jev research)

Every backend's answer is validated against the same envelope regardless of
how it was elicited (native Jev-style typed API, an LLM structured-output
mode, or a CLI agent's final JSON). This is the Jev doc's proposed
contract, adopted here without modification per the brief's explicit
instruction to reuse it:

```json
{
  "schema_version": "gigai.typed_decision.v1",
  "case_id": "pet-001",
  "decisions": {
    "requirement_status": "supported|unclear|unsupported|not_applicable",
    "evidence_sufficiency": "sufficient|insufficient",
    "needs_human_review": true
  },
  "evidence_refs": ["evidence-002"],
  "abstention": null
}
```

`abstention` is populated instead of `decisions` under the Jev doc's
abstention state machine, reused verbatim:

```text
if transport/error/timeout/truncation/schema_invalid:
    outcome = refused_or_failed   # no decision published
elif evidence_missing or question_out_of_scope:
    outcome = abstain             # route to review / more evidence
elif confidence/probability below the frozen task threshold:
    outcome = uncertain           # route to review
else:
    outcome = typed_decision      # apply only a separately authorized effect
```

All four backends (Jev-shaped hosted API, GPT, Claude Sonnet, Qwen/Ollama,
Luna/Codex) are graded on which of these four outcome states they land in
per case, not on free text — this is what makes the comparison "the same
decision states," per the brief, rather than four separately-parsed answer
formats.

## 3. Gold-labeling process

### 3.1 Objective vs. judgment split

- **Objective cases** (target: roughly a third of the pack, e.g. 5–10 of
  15–30): schema validity of a supplied example, "does evidence ID
  `evidence-002` exist in the state," literal keyword/ID presence, `enum`
  membership. Gold label is produced by a script against the case's own
  `state`, with **no model in the loop at all** — not even to draft it.
  These exist primarily to calibrate the deterministic grader itself
  (known-good/known-bad, per RUNTIME-01's "validate the grader with
  known-good and known-bad outputs before evaluating candidates").
- **Judgment cases** (the remainder): "does this evidence satisfy this
  requirement," "is this fairly `unclear` vs. `unsupported`." A frontier
  model (Claude or GPT, explicitly logged as `drafted_by`) may propose a
  candidate label to save adjudicator time, but that draft is never gold
  until a human adjudicates it.

### 3.2 Human adjudication step

- The adjudicator reads the case `state` and `question` directly and
  either confirms, overrides, or replaces the drafted label. The
  adjudicator's identity (a name or role, e.g. `human:operator`) is
  recorded in `gold.provenance.adjudicated_by`; a case with no such field
  populated is not eligible to be used as gold, full stop.
- **Blind adjudication where practical.** Per the brief, adjudication
  compares the *draft label* against the source material, not any specific
  backend's actual output — the adjudicator should not see which backend
  (if any) produced which candidate answer while adjudicating gold. In
  practice this means: draft gold labels are prepared once, before any
  backend is run, using only a frontier-model draft plus the source
  material; adjudication happens against that draft, not against
  backend transcripts. This ordering (gold frozen before any comparison
  run) is what keeps the benchmark from becoming "agreement with
  Claude/GPT," because the adjudicator's decision authority sits above the
  frontier-model draft rather than validating a specific frontier answer
  after the fact.
- **Ambiguous / multi-acceptable-answer cases.** Where the adjudicator
  judges more than one label defensible (e.g. `unclear` and `unsupported`
  both reasonably fit), `gold.acceptable_labels` records all of them rather
  than forcing a single string, and `notes` records why. A grader then
  scores a backend's answer as correct if it falls in that set, and this is
  reported per-case so a reader can see which cases were genuinely
  contestable rather than treating all "correct" scores as equally solid.
- **Disagreement logging.** If more than one adjudicator reviews a case
  (recommended for at least the deliberately tricky `unclear`/`insufficient`
  cases) and they disagree, `gold.provenance.disagreement_log` records both
  proposed labels, both adjudicator identities, and the resolution (e.g.
  "second adjudicator's label adopted after discussion" or "case marked
  multi-acceptable instead of resolved to one label"). Disagreement is data
  about task difficulty, not noise to be silently averaged away.

### 3.3 What this process explicitly does not do

It does not let a frontier model's own output on a case count as, or
default to, that case's gold label. The frontier model's role is limited to
drafting a candidate label *before* any comparison run, for adjudicator
efficiency; the adjudicator can and should override it, and every override
is exactly as visible in the record as an agreement would be (both are just
`adjudicated_by` + a label, with the draft preserved separately for
audit). This is the direct, structural answer to the brief's stated risk
that "skipping human adjudication turns the benchmark into 'agreement with
Claude/GPT.'"

### 3.4 Judge-vs-gold check (for the unavoidable-LLM-judge cases)

For any case where grading genuinely requires semantic judgment that a
deterministic script cannot perform (e.g. "does this free-text explanation
actually cite the evidence it claims to"), and an LLM judge is used at
*grading* time (as opposed to gold-*drafting* time, which is a separate,
earlier step under §3.2):

- The judge model is a named, disclosed configuration value (Inspect AI's
  `model`/`model_role` pattern, §1.3) — e.g. "grading judge:
  claude-sonnet-5, pinned" — never an ambient default.
- The judge is run against the same case set as any candidate backend, and
  its verdicts are checked against the human-adjudicated gold set the same
  way a candidate backend's answers are (Section 5 grading mechanism),
  producing a judge-accuracy-against-gold number that is reported alongside
  candidate results, not assumed.
- If the judge model is the same model family as one of the compared
  backends (e.g. grading Claude Sonnet's answers with a Claude Sonnet
  judge), that fact is explicitly flagged in the report as a
  same-family-judge risk, independent of the judge's measured accuracy
  against gold.
- Following Inspect AI's grading-prompt convention (§1.3): the judge
  prompt fences/escapes case text so a case's own content cannot inject a
  fake verdict marker, and verdict extraction takes the last
  structured-field grade rather than the first mention inside any
  reasoning trace.

## 4. Setup-parity recording table

Extending the existing pilot's table (Jev research doc, "GigAI comparison:
Jev-shaped local decisions versus complete setups") with GPT and Claude
Sonnet rows, and separating "must hold constant" from "allowed to differ,
must be logged" as two explicit columns per the brief, rather than one
combined "what must be comparable" column as in the original table:

| Setup label | Inference path | Must hold constant | Allowed to differ (must be logged) |
| --- | --- | --- | --- |
| `jev-hosted-decision-api` | TypeSafe hosted Jev endpoint, pinned model ID, official SDK/HTTP | Frozen `state`, `question`/criteria text, case IDs, decision-envelope schema, grader, decision thresholds | Remote network path, API version, account/rate limits, retry count, usage/cost, resolved model, wrapper text mapping Jev's `choice`/`score`/`noul` onto the shared envelope |
| `gpt-hosted-api` (new) | Hosted OpenAI-compatible API, pinned model ID/snapshot, structured-output mode | Same frozen `state`/criteria/case IDs/envelope/grader/thresholds as above | Prompt/system-message wrapper needed to elicit the envelope, structured-output vs. free-JSON mode, retry policy on malformed output (TypeSafe adapter's `n_retry_malformed_structure` pattern, §1.2, is a reasonable template), resolved model/snapshot ID, token usage/cost, latency |
| `claude-sonnet-api` (new) | Hosted Anthropic API, pinned model ID, tool-use or structured-output mode | Same frozen `state`/criteria/case IDs/envelope/grader/thresholds as above | Prompt/system-message wrapper, tool-use schema if used to emit the envelope, retry policy, resolved model ID, token usage/cost, latency |
| `luna-codex-cli` | Luna/Codex CLI or approved harness, as actually configured | Same logical cases and shared decision envelope; prompt/tool adaptation recorded, not hidden | CLI version, model identity/digest if available, tools/context available to the CLI, permissions, retries, hosted/local destination, observed agent activity (e.g. did it call any tool at all) |
| `qwen38-ollama-local` | Installed `qwen3.8` (or current pinned tag) via loopback Ollama, per the existing pilot's endpoint/identity discipline | Same logical cases, shared decision envelope, grader, thresholds | Observed digest (must be captured at run time, not assumed from the pilot), Ollama version, quantization, context/`think` settings, hardware, warm/cold timing, tool set actually exposed |

Columns that are the same for every row, restated once instead of per-row
to avoid the false impression that they vary: case-pack `pack_version` and
`case_id` set; the `question.instructions`/`criteria` text (byte-identical
across backends — this is the "same task, not same prompt" boundary the
brief draws); the shared `gigai.typed_decision.v1` envelope and its
validator; the grader and its thresholds; and the abstention state
machine (§2.2). A "wrapper text" artifact (the actual system/user message
or tool schema used to elicit the envelope from that specific backend) is
stored per setup, per the brief's requirement to make the "same task" vs.
"how each harness asks for it" boundary inspectable, not implicit.

## 5. Grading mechanism

**Deterministic-first**, following the brief and the Jev doc's proposed
grader outline, in this priority order:

1. **Schema validity** — does the response parse as
   `gigai.typed_decision.v1` (or a recognized `abstention` state)? Fully
   mechanical; no model involved.
2. **Envelope-outcome branch — decide this before running steps 3–4, not
   after.** Determine which outcome state the response actually landed in
   (`typed_decision` / `abstain` / `uncertain` / `refused_or_failed`, per
   the abstention state machine in §2.2) and compare it against
   `gold.expected_envelope_outcome` for this case:
   - If the response's actual outcome state **matches**
     `gold.expected_envelope_outcome` and that expected state is
     `abstain`/`uncertain`/`refused_or_failed` (i.e., `decisions` is
     correctly absent per the envelope contract, §2.2's "`abstention` is
     populated instead of `decisions`"), mark **only** the label-specific
     part of steps 3–4 — enum-value validity of `requirement_status` and
     the gold-label match — **not applicable** for this case, since there
     is no `decisions.requirement_status` to check against either. Record
     this case's label-match result as not-applicable, then continue to
     the evidence-integrity checks below rather than stopping outright.
   - **Evidence-integrity checks still run on a valid abstention, and are
     not skipped by this branch.** A response's `abstention` state may
     still carry `evidence_refs` (partial evidence that informed the
     decision to abstain) or explanatory text, and both must still be
     checked: do all `evidence_refs` present exist in the case's `state`,
     and are any `forbidden_invented_claims` present in the response text
     (including its abstention rationale, not only a `decisions` block)?
     An abstention that cites a nonexistent evidence ID or invents a claim
     while explaining why it abstained is a real failure — abstaining
     correctly does not excuse fabricating evidence — and must fail this
     check the same way a `typed_decision` would. Only the *gold-label*
     match is genuinely not-applicable to an abstention; evidence
     integrity is not.
   - If the response's actual outcome state **does not match**
     `gold.expected_envelope_outcome`, the case fails at the envelope-
     outcome check regardless of what (if anything) `decisions` contains —
     an overconfident `typed_decision` where `abstain` was expected is
     wrong even if the label inside it happens to match gold, and a
     spurious `abstain` where `typed_decision` was expected is wrong even
     though no label was offered to check. Record which direction the
     mismatch went (over-confident vs. under-confident) since that is a
     materially different failure mode from a wrong label. Evidence-
     integrity checks (step 3 below) still run in this branch too, on
     whatever evidence/claims the mismatched response actually contains.
   - Only if `gold.expected_envelope_outcome` is `typed_decision` **and**
     the response actually produced one does the gold-label match in step
     4 run against the `decisions` field.
3. **Evidence-ID correctness and forbidden-claim check — runs regardless
   of envelope outcome** (per the branch above; this is the one check that
   is never skipped by a valid abstention): do all `evidence_refs` present
   in the response (inside `decisions`, or inside an `abstention` block's
   supporting evidence) exist in the case's `state`; are any
   `forbidden_invented_claims` present anywhere in the response text,
   including abstention rationale? Mechanical string/set membership checks
   against the frozen case, not a model judgment.
4. **Enum validity and gold-label match** (label-specific; not applicable
   when step 2 found a correct abstention, per that branch) — is
   `requirement_status` one of the four allowed values, and does
   `decisions.requirement_status` (etc.) fall in `gold.acceptable_labels`?
   Mechanical set membership against the human-adjudicated gold record
   from Section 3.

   Recall `held_out` and `gold.expected_envelope_outcome` are unrelated
   axes and must not be conflated: `held_out` means "unseen during
   wrapper/prompt/grader-tuning development" (§3.4/Field notes above) and
   applies to any case regardless of what the correct backend behavior is;
   a case can be held-out and still expect a confident `typed_decision`
   (e.g. `pet-004`'s known-bad grader self-test), or not held-out and
   still expect `abstain`. The envelope-outcome branch in step 2 is what
   decides whether step 4's label check runs — never `held_out`, and never
   an inference from whether the case merely "sounds ambiguous." Step 3
   (evidence-ID and forbidden-claim integrity) always runs regardless of
   envelope outcome, per the branch above.
5. **LLM-judge step, only where 1–4 cannot resolve it** — e.g. free-text
   rationale quality, or "does the cited span actually support the
   claim" when that requires reading prose rather than checking an ID
   against a fixed list. Used sparingly, per §3.4: named judge model,
   fenced/escaped case text, last-structured-field verdict parsing
   (Inspect AI pattern, §1.3), and the judge's own accuracy checked
   against human-adjudicated gold rather than trusted on its agreement with
   candidates alone.

The grader reports each of these five checks **separately per case and per
setup** (schema validity rate, enum/evidence correctness rate, gold-match
rate, abstention correctness rate, judge-step accuracy where used) rather
than collapsing them into one pass/fail number — this both follows the
brief's "quality... kept separate" instruction at a finer grain and echoes
the coordinator review's specific complaint that the Qwen pilot's
"13/13 is not a capability acceptance score" because its single aggregate
hid which checks were doing the real work.

## 6. Reported axes

Three axes, reported per setup and per case, never blended into one score,
directly following the brief and matching `dsh-eval`'s bucket separation
(§1.1):

- **Quality** — the five-way grader breakdown from Section 5, plus
  coverage/abstention tradeoffs (what fraction of cases got a
  `typed_decision` vs. `abstain`/`uncertain`/`refused_or_failed`, and
  correctness conditional on each). Calibration/reliability buckets are
  reported only where a backend actually emits a probability/confidence
  value (per the Jev doc's caution against inventing one for backends that
  only emit a label).
- **Cost** — token usage (input/output) and, where the setup has a public
  per-token price, an estimated dollar cost per case and per full pack run.
  Local/loopback setups (Qwen/Ollama) report compute time and hardware
  metadata instead of a dollar figure, not a fabricated zero.
- **Latency** — wall time per case including retries, cold vs. warm state,
  and (following TypeSafe's adapter and `dsh-eval`'s field lists, §1.1–1.2)
  retry count and terminal failure class as a related-but-distinct
  robustness figure, not folded into the latency number itself.

A free-but-worse and a paid-but-better setup must both be fully reportable
side by side under this scheme without an artificial "winner" field.

## 7. Worked example — synthetic "posting evidence triage" cases

Five fully synthetic cases. No real job posting, resume, or candidate data
is used anywhere in this section; all names, requirements, and evidence
text below are invented for this document only, consistent with the Jev
doc's proposed contract (§"Proposed independent typed decision contract").

### Case `pet-001` — objective (evidence ID exists, literal match)

```json
{
  "pack_version": "s08.posting-evidence-triage.v1",
  "case_id": "pet-001",
  "case_kind": "objective",
  "state": {
    "posting": {
      "id": "posting-101",
      "text": "Requirement: Candidate must have a valid driver's license."
    },
    "evidence_items": [
      { "id": "evidence-101", "text": "I have held a Class C driver's license since 2019." }
    ]
  },
  "question": {
    "id": "requirement_status",
    "instructions": "Given the posting requirement and the evidence items, classify whether the evidence supports, contradicts, or is silent on the requirement.",
    "criteria": {
      "supported": "At least one evidence item explicitly and unambiguously satisfies the requirement.",
      "unclear": "Evidence exists but does not unambiguously resolve the requirement either way.",
      "unsupported": "No evidence item addresses the requirement, or evidence contradicts it.",
      "not_applicable": "The requirement does not apply given other stated facts in the posting."
    }
  },
  "gold": {
    "label": "supported",
    "acceptable_labels": ["supported"],
    "expected_envelope_outcome": "typed_decision",
    "rationale": "evidence-101 states a currently held driver's license, a literal, mechanical match to the requirement. No judgment call.",
    "provenance": {
      "drafted_by": "script:literal-match",
      "adjudicated_by": "n/a (objective case; no adjudication required)",
      "adjudication_date": "2026-09-20",
      "adjudication_mode": "n/a",
      "disagreement_log": []
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": ["evidence-101"],
    "forbidden_invented_claims": [],
    "min_evidence_refs": 1
  },
  "known_bad_variant_of": null,
  "held_out": false,
  "notes": "Objective calibration case for the grader itself; not a judgment test."
}
```

### Case `pet-002` — judgment, genuinely ambiguous, multi-acceptable

```json
{
  "case_id": "pet-002",
  "case_kind": "judgment",
  "state": {
    "posting": {
      "id": "posting-102",
      "text": "Requirement: Candidate must have professional experience with distributed systems."
    },
    "evidence_items": [
      { "id": "evidence-102", "text": "Built and maintained a multi-region caching layer used by three internal services." }
    ]
  },
  "question": {
    "id": "requirement_status",
    "instructions": "Given the posting requirement and the evidence items, classify whether the evidence supports, contradicts, or is silent on the requirement.",
    "criteria": {
      "supported": "At least one evidence item explicitly and unambiguously satisfies the requirement.",
      "unclear": "Evidence exists but does not unambiguously resolve the requirement either way.",
      "unsupported": "No evidence item addresses the requirement, or evidence contradicts it.",
      "not_applicable": "The requirement does not apply given other stated facts in the posting."
    }
  },
  "gold": {
    "label": "unclear",
    "acceptable_labels": ["supported", "unclear"],
    "expected_envelope_outcome": "typed_decision",
    "rationale": "A multi-region caching layer used by several services is strong circumstantial evidence of distributed-systems experience but never uses that phrase and does not state the work was done in a 'professional' capacity as opposed to, e.g., a side project. Two adjudicators reasonably differed here (see disagreement_log). The evidence is present and legible — the disagreement is about how to classify it, not about whether there is enough evidence to decide at all — so a confident typed_decision remains the expected outcome even though the label itself has two acceptable values.",
    "provenance": {
      "drafted_by": "claude-sonnet-draft",
      "adjudicated_by": "human:adjudicator-A (final)",
      "adjudication_date": "2026-09-20",
      "adjudication_mode": "blind-to-backend",
      "disagreement_log": [
        {
          "adjudicator": "human:adjudicator-A",
          "proposed_label": "unclear",
          "reasoning": "No explicit 'distributed systems' phrase or team/employment context stated."
        },
        {
          "adjudicator": "human:adjudicator-B",
          "proposed_label": "supported",
          "reasoning": "Multi-region + multi-service caching is a textbook distributed-systems task; overly literal to require the exact phrase."
        }
      ]
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": ["evidence-102"],
    "forbidden_invented_claims": ["candidate has a formal distributed systems degree"],
    "min_evidence_refs": 1
  },
  "known_bad_variant_of": null,
  "held_out": true,
  "notes": "Deliberately ambiguous; resolved as multi-acceptable rather than forced to one label. Held out from any wrapper/prompt example."
}
```

### Case `pet-003` — judgment, unsupported (evidence silent)

```json
{
  "case_id": "pet-003",
  "case_kind": "judgment",
  "state": {
    "posting": {
      "id": "posting-103",
      "text": "Requirement: Candidate must be willing to work rotating on-call shifts."
    },
    "evidence_items": [
      { "id": "evidence-103", "text": "Five years of backend development experience in Python and Go." }
    ]
  },
  "question": {
    "id": "requirement_status",
    "instructions": "Given the posting requirement and the evidence items, classify whether the evidence supports, contradicts, or is silent on the requirement.",
    "criteria": {
      "supported": "At least one evidence item explicitly and unambiguously satisfies the requirement.",
      "unclear": "Evidence exists but does not unambiguously resolve the requirement either way.",
      "unsupported": "No evidence item addresses the requirement, or evidence contradicts it.",
      "not_applicable": "The requirement does not apply given other stated facts in the posting."
    }
  },
  "gold": {
    "label": "unsupported",
    "acceptable_labels": ["unsupported"],
    "expected_envelope_outcome": "typed_decision",
    "rationale": "Evidence is entirely about technical stack; nothing addresses on-call willingness. This is a clean 'silent, not merely weak' case, distinct from pet-002. Silence itself is a decidable answer here (unsupported), not a reason to abstain.",
    "provenance": {
      "drafted_by": "claude-sonnet-draft",
      "adjudicated_by": "human:adjudicator-A",
      "adjudication_date": "2026-09-20",
      "adjudication_mode": "blind-to-backend",
      "disagreement_log": []
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": [],
    "forbidden_invented_claims": ["candidate has confirmed on-call availability"],
    "min_evidence_refs": 0
  },
  "known_bad_variant_of": null,
  "held_out": true,
  "notes": "Tests whether a backend invents on-call willingness from unrelated technical evidence (a specific known failure mode to watch for)."
}
```

### Case `pet-004` — known-bad variant (malformed/invented evidence probe)

```json
{
  "case_id": "pet-004",
  "case_kind": "objective",
  "state": {
    "posting": {
      "id": "posting-101",
      "text": "Requirement: Candidate must have a valid driver's license."
    },
    "evidence_items": [
      { "id": "evidence-101", "text": "I have held a Class C driver's license since 2019." }
    ]
  },
  "question": {
    "id": "requirement_status",
    "instructions": "Given the posting requirement and the evidence items, classify whether the evidence supports, contradicts, or is silent on the requirement.",
    "criteria": {
      "supported": "At least one evidence item explicitly and unambiguously satisfies the requirement.",
      "unclear": "Evidence exists but does not unambiguously resolve the requirement either way.",
      "unsupported": "No evidence item addresses the requirement, or evidence contradicts it.",
      "not_applicable": "The requirement does not apply given other stated facts in the posting."
    }
  },
  "gold": {
    "label": "supported",
    "acceptable_labels": ["supported"],
    "expected_envelope_outcome": "typed_decision",
    "rationale": "Same well-formed source case as pet-001; this variant exists purely to test whether the grader (not the backend) correctly flags a response that cites a nonexistent evidence ID as an evidence-support failure regardless of label correctness.",
    "provenance": {
      "drafted_by": "script:literal-match",
      "adjudicated_by": "n/a (objective case)",
      "adjudication_date": "2026-09-20",
      "adjudication_mode": "n/a",
      "disagreement_log": []
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": ["evidence-101"],
    "forbidden_invented_claims": [],
    "min_evidence_refs": 1
  },
  "known_bad_variant_of": "pet-001",
  "held_out": true,
  "notes": "Grader self-test case: a candidate answer citing evidence-999 (which does not exist in this case's state) must fail the evidence-ID check in Section 5 step 2 even if requirement_status is correct."
}
```

### Case `pet-005` — judgment, evidence-insufficient / expected abstention

```json
{
  "case_id": "pet-005",
  "case_kind": "judgment",
  "state": {
    "posting": {
      "id": "posting-104",
      "text": "Requirement: Candidate must be authorized to work in the stated jurisdiction without sponsorship."
    },
    "evidence_items": [
      { "id": "evidence-104", "text": "Currently based in the same metro area as the role." }
    ]
  },
  "question": {
    "id": "evidence_sufficiency",
    "instructions": "Given the posting requirement and the evidence items, decide whether the available evidence is sufficient to answer the requirement at all, independent of what the answer would be.",
    "criteria": {
      "sufficient": "The evidence directly addresses work authorization or sponsorship status.",
      "insufficient": "The evidence does not address work authorization or sponsorship status, even indirectly."
    }
  },
  "gold": {
    "label": "insufficient",
    "acceptable_labels": ["insufficient"],
    "expected_envelope_outcome": "abstain",
    "rationale": "Geographic proximity says nothing about legal work authorization or sponsorship need; a well-behaved backend should abstain rather than guess.",
    "provenance": {
      "drafted_by": "claude-sonnet-draft",
      "adjudicated_by": "human:adjudicator-B",
      "adjudication_date": "2026-09-20",
      "adjudication_mode": "blind-to-backend",
      "disagreement_log": []
    }
  },
  "evidence_requirements": {
    "accepted_source_ids": [],
    "forbidden_invented_claims": ["candidate does not require sponsorship", "candidate requires sponsorship"],
    "min_evidence_refs": 0
  },
  "known_bad_variant_of": null,
  "held_out": true,
  "notes": "held_out here means unseen during wrapper/prompt development for any backend (per the held-out definition above), independent of the expected decision outcome. Separately: the gold label is 'insufficient', and a well-behaved backend's expected envelope outcome is abstain (evidence_missing) rather than committing to a typed_decision on that label. The grader checks label-correctness and abstention-correctness as two independent axes; a backend that abstains here is graded on whether abstaining was the right call, not penalized as having produced a wrong typed answer."
}
```

These five cases demonstrate: an objective calibration case (`pet-001`), a
genuinely ambiguous multi-acceptable judgment case with a logged
adjudicator disagreement (`pet-002`), a clean unsupported/silent-evidence
case that probes for invented claims (`pet-003`), a known-bad grader
self-test variant (`pet-004`), and a case where the *correct* backend
behavior is to abstain rather than answer (`pet-005`). This is enough to
exercise every part of the frozen-case schema, the gold-labeling process,
and the grader's five-step check list in Section 5 — proving the format is
usable, not just specified, per the brief's requirement, at a scale far
below the 15–30 case pilot target.

## 8. Recommendation for a first real comparison run

This section is a documented estimate for planning purposes only; no run
is authorized or performed here.

- **Setups:** the five in Section 4's table —
  `jev-hosted-decision-api`, `gpt-hosted-api`, `claude-sonnet-api`,
  `luna-codex-cli`, `qwen38-ollama-local`. If resourcing forces a smaller
  first run, `qwen38-ollama-local` plus one hosted frontier setup (Claude
  or GPT) is the minimum pair that actually tests the spike's stated
  question (local vs. hosted, not just hosted vs. hosted); `jev-hosted-
  decision-api` and `luna-codex-cli` are next in priority since both
  already have partial pilot groundwork (Jev doc, RUNTIME-01).
- **Case count:** 20 cases as a first pack — within the brief's 15–30
  pilot bound, split roughly 6 objective / 14 judgment, with at least 4
  judgment cases deliberately ambiguous (multi-acceptable) and at least 3
  known-bad grader-self-test variants (à la `pet-004`), consistent with the
  worked example's proportions in Section 7.
- **Estimated adjudication time:** roughly 10–20 minutes per judgment case
  for a single adjudicator (read state, evaluate the frontier-model draft
  against source material, record rationale) — call it 3–5 hours for 14
  judgment cases by one adjudicator. Add a second adjudication pass on the
  hardest 4–6 cases (the deliberately ambiguous and known-bad ones) for
  disagreement logging, roughly another 1–2 hours. Total estimate: **4–7
  adjudicator-hours** for a 20-case pack. This is a planning estimate, not
  a measurement — no adjudication was performed for this document beyond
  the five worked examples in Section 7, which took substantially less
  time each because the ambiguity and disagreement text were authored
  directly rather than adjudicated from a blind draft.
- **Estimated paid-call cost:** documented/estimated only, no calls made.
  Per case, a hosted frontier call (GPT or Claude Sonnet) drafting or
  answering a typed-decision question with a few hundred input tokens of
  state plus criteria and well under 200 output tokens is, at current
  publicly listed per-token pricing for mid-tier hosted models, on the
  order of low-single-digit cents per call; with retries (budget 2x for
  malformed-output recovery, per the TypeSafe adapter's retry-budget
  pattern, §1.2) and 20 cases across two hosted setups (GPT, Claude
  Sonnet), a full first run is very unlikely to exceed roughly **$1–3 in
  total hosted API spend** for the candidate-answering pass alone. If an
  LLM-judge grading pass (§3.4) is also needed for free-text cases, add a
  similar order-of-magnitude amount. `jev-hosted-decision-api` calls are
  priced separately by TypeSafe and were not estimated here (no pricing
  page was reviewed in this pass; the Jev research doc's sources list does
  not include a pricing page). This estimate should be replaced with an
  actual quote from each provider's current pricing page before any real
  paid run, and is not a commitment or authorization to spend it.
- **Open questions:**
  - Should `luna-codex-cli`'s "observed activity" (whether it uses tools
    at all for a task that doesn't strictly require them) be normalized
    against the other setups, or is that asymmetry itself part of what's
    being compared? RUNTIME-01 already flags this as an explicit
    non-hidden asymmetry; S08 inherits but does not resolve it.
  - How many adjudicators are actually available, and does a single-
    adjudicator pilot (with no second-pass disagreement check) meaningfully
    weaken the "not just agreement with Claude/GPT" claim in Section 3?
    A one-adjudicator pilot is still an improvement over zero adjudication,
    but the brief's own bias caution is strongest with at least two
    independent adjudicators on the hardest cases.
  - Where should the case pack and gold labels live so they survive
    "held-out" discipline (not visible to whoever writes each backend's
    wrapper text) while still being reviewable — a separate access
    boundary within the repository, or a genuinely separate store? Not
    resolved here; this is an operational/tooling decision the brief does
    not ask this document to make.
  - Does the local Qwen setup need a distinct evidence-sufficiency
    threshold from the hosted setups given the qwen38 pilot's `think:false`
    configuration, or should the frozen decision threshold in Section 4
    truly be setup-invariant as the brief implies? Worth a small pre-check
    before the first real run rather than an assumption either way.

## 9. Pilot-bound caveat (explicit, per the brief)

Everything above targets a **15–30 case pilot**. That is enough to shake
out the case-pack format, the adjudication workflow, the setup-parity
table, and the grader's mechanics — it is explicitly **not** enough to
support a reliable broad ranking of these five setups, a calibration study
of any backend's confidence/probability outputs, or a claim that one
backend is "better than" another in general. A 20-case pack, even
perfectly adjudicated, has wide per-criterion confidence intervals and no
statistical power for subgroup analysis (e.g. "does backend X do worse
specifically on `unclear` cases"). Any report produced from a first real
run under this methodology should say so as plainly as this section does,
and should present per-case results (Section 5's granular breakdown) so a
reader can judge the evidence directly rather than trusting a single
number computed from 20 cases.

## Sources reviewed (primary-first)

All web sources below were accessed on 2026-09-20.

- [`deepseek-ai/deepseek-harness` repository](https://github.com/deepseek-ai/deepseek-harness) — official DeepSeek AI open-source agent harness, plugin architecture, developer preview status.
- [DeepSeek Harness `BENCHMARK.md`](https://github.com/deepseek-ai/deepseek-harness/blob/master/BENCHMARK.md) — minimal; points to SDK quickstart and a `jsonrpc-agent` minimal variant, no detailed methodology in this file itself.
- [DeepSeek Harness product page](https://deepseek.com/harness/en/) — "Agent = Model + Harness" framing, Cordis plugin kernel, append-only session log, Trajectory view, four runtime modes (Standard/Code/Minimal/Creator).
- [DeepSeek Harness session-persistence architecture note](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-06-14-session-persistence.md) and [Session Persistence reference docs](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/persistence) — `session.v3.jsonl.zstd` format, `SessionEvent` row structure `(session_id, seq, type, time, data)`, `KNOWN_SESSION_EVENT_TYPES`/`ignorable` schema-versioning rule.
- [`hccccc01333/dsh-eval` repository](https://github.com/hccccc01333/dsh-eval) — third-party (not official DeepSeek) benchmark tool built on deepseek-harness session logs; benchmark YAML schema, metric buckets (success/accuracy, resource usage, performance, robustness, quality), `dsh eval compare`, `dsh eval import` keyless replay.
- [`typesafe-ai/system-one-adapter-python` repository](https://github.com/typesafe-ai/system-one-adapter-python) — `llm_answer_mode` (discrete/probabilities), `n_retry_malformed_structure`, `response.debug` (`llm_attempts`, `retry_reasons`), `response.usage` fields.
- [`UKGovernmentBEIS/inspect_ai` repository](https://github.com/UKGovernmentBEIS/inspect_ai) — Dataset/Solver/Scorer architecture overview.
- [Inspect AI `scorer/_model.py`](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/scorer/_model.py) — `model_graded_qa`/`model_graded_fact` signatures, `model`/`model_role`/`reducer` judge-disclosure parameters, injection-resistant grading-template construction, last-match-wins grade-pattern parsing.
- [Promptfoo model-graded metrics docs](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/) and [assertions/metrics docs](https://www.promptfoo.dev/docs/configuration/expected-outputs/) — deterministic-vs-model-graded terminology, `llm-rubric` assertion, per-assertion `provider` override.
- [OpenAI `evals` repository](https://github.com/openai/evals) and [`completion-fn-protocol.md`](https://github.com/openai/evals/blob/main/docs/completion-fn-protocol.md) — `CompletionFn` protocol for chain/tool-using targets, `<eval_name>.<split>.<version>` registry naming convention.
- [EleutherAI `lm-evaluation-harness` task guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/master/docs/task_guide.md) — `doc_to_text`/`doc_to_target` case-formatting convention, gold-answer-as-index-for-multiple-choice pattern.

## Repository sources reused (already reviewed prior to this document)

- [Jev / TypeSafe AI structured-decision research](jev-typesafe-ai-structured-decisions-research.md) — abstention contract, proposed typed-decision schema, proposed setup-parity table this document extends.
- [Qwen3.8 Scout local-model pilot](qwen38-scout-local-eval.md) — existing setup-label conventions and logging fields.
- [Qwen3.8 pilot coordinator review](qwen38-scout-coordinator-review.md) — named weaknesses (leading prompts, weak semantic checks, non-held-out cases) this methodology is designed not to repeat.
- [RUNTIME-01 — Local execution and backend comparison](../../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md) — narrower v0.1.7 local-backend comparison scope this methodology stays compatible with.
- [S08 spike brief](../S08-cross-model-decision-evaluation-methodology.md) — the request this document answers.

[^ds-repo]: [`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness), accessed 2026-09-20.
[^ds-site]: [DeepSeek Harness developer preview](https://deepseek.com/harness/en/), accessed 2026-09-20.
[^ds-persist]: [DeepSeek Harness session-persistence architecture note](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-06-14-session-persistence.md), accessed 2026-09-20.
[^ds-arch]: [Session Persistence reference](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/persistence), accessed 2026-09-20.
[^dsh-eval]: [`hccccc01333/dsh-eval`](https://github.com/hccccc01333/dsh-eval), accessed 2026-09-20. Community/third-party tool, not an official DeepSeek AI repository.
[^ts-adapter]: [`typesafe-ai/system-one-adapter-python`](https://github.com/typesafe-ai/system-one-adapter-python), accessed 2026-09-20.
[^inspect-repo]: [`UKGovernmentBEIS/inspect_ai`](https://github.com/UKGovernmentBEIS/inspect_ai), accessed 2026-09-20.
[^inspect-scorer]: [`inspect_ai/scorer/_model.py`](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/scorer/_model.py), accessed 2026-09-20.
[^promptfoo]: [Promptfoo model-graded metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/), accessed 2026-09-20.
[^openai-evals]: [`openai/evals` completion-fn-protocol.md](https://github.com/openai/evals/blob/main/docs/completion-fn-protocol.md), accessed 2026-09-20.
[^lm-eval-harness]: [`EleutherAI/lm-evaluation-harness` task_guide.md](https://github.com/EleutherAI/lm-evaluation-harness/blob/master/docs/task_guide.md), accessed 2026-09-20.
