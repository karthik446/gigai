# Session Resume Spike — Findings

Date: 2026-07-27
Spike: `research/experiments/resume/resume-spike`
Providers: `codex-cli 0.144.6`, `claude 2.1.220 (Claude Code)`
Method: nonce recall with a fresh-session control arm, plus mode/cwd inheritance probes.

Corrects the earlier review claim that native resume was "entirely unvalidated."
It was unvalidated; it is now validated, and it works on both providers.

## 1. Resume carries context on both providers

| Arm | Codex | Claude |
|---|---|---|
| Session id captured | yes | yes (harness-supplied) |
| Resumed answer | nonce recalled | nonce recalled |
| Control (fresh session, same question) | `NONE` | `NONE` |
| Verdict | RESUME_CARRIES_CONTEXT | RESUME_CARRIES_CONTEXT |

The control arm is what makes this evidence rather than a guess: a fresh session
asked the identical question answered `NONE`, so recall came from the session,
not from prompt leakage or model guessing.

Chained resume (three turns on one lane, two distinct facts) also works. Both
providers recalled both facts in order and kept the session id stable across all
three turns. This is the `--rounds 2+` debate shape, so it is the shape that
matters.

## 2. Session id handling differs, and Claude's is better for the harness

- **Claude accepts `--session-id <uuid>`.** The harness generates the UUID,
  passes it on the first call, and resumes with `--resume <uuid>`. The same id
  round-trips in the response envelope. No parsing required.
- **Codex assigns its own.** It must be read from the `thread.started` JSONL
  event (`thread_id`, a UUIDv7). Requires `--json`.

Implication for the data model: `step_runs.provider_session_id` is
harness-authored for Claude and provider-authored for Codex. The store must
tolerate both directions.

## 3. `codex exec resume` has a narrower flag surface than `codex exec`

Verified by diffing `--help` output. Accepted by `exec` but **rejected by
`resume`**:

```
--cd  --sandbox  --add-dir  --color  --profile  --oss  --local-provider
```

Consequences:

- **Execution mode cannot be re-asserted by flag on resume.** Default
  inheritance is safe: a session seeded `--sandbox read-only`, resumed with no
  sandbox flag, answered `BLOCKED` and wrote no file.
- **Working directory cannot be set on resume, and is not inherited.** Resuming
  from a different cwd, the agent reported the *invocation* cwd, not the cwd the
  session was created in. So the resumed step operates wherever the harness
  process happens to be. The harness must set the subprocess `cwd` explicitly on
  every resume — there is no flag to fall back on.

## 4. Neither provider locks the execution mode to the session

This is the security finding.

| Probe | Result |
|---|---|
| Codex resume, no sandbox flag | `BLOCKED`, no file written — inherits read-only |
| Codex resume + `-c sandbox_mode="workspace-write"` | `WROTE`, file written |
| Claude session seeded `--permission-mode plan`, resumed with `acceptEdits` + `Write,Bash` | `WROTE`, file written |

A read-only session can be resumed into a write-capable one on both providers.
`resume` still accepts `-c` on Codex, which is enough to widen the sandbox.

So plan §9's rule that resume requires a matching execution mode is correct and
**must be enforced by the harness itself** — neither CLI will refuse a mode
change. The mode belongs in the harness ledger as the authority, and
`compatibility_hash` must include it. §8's "workflows cannot broaden modes" holds
only because the harness is the sole author of argv; that invariant is now
load-bearing rather than incidental.

## 5. Structured run data is available for free

**Codex** `--json` emits clean JSONL: `thread.started` (with `thread_id`),
`turn.started`, `item.completed` (with `item.type == "agent_message"`),
`turn.completed` (with `usage`). That is enough for `normalize()` and
`run_events`.

**Claude** `--output-format json` returns an envelope containing:

```
session_id  uuid  result  is_error  subtype  stop_reason  terminal_reason
permission_denials  num_turns  usage  modelUsage  total_cost_usd
duration_ms  duration_api_ms  ttft_ms  time_to_request_ms  api_error_status
```

`terminal_reason` and `permission_denials` are named fields the plan's §11.1
objective-facts lane asks for. Do not re-derive them.

Also worth noting: `codex exec --output-schema <FILE>` takes a JSON Schema for
the final response. That is a direct mechanism for the structured step handoff
§9 requires and does not specify.

## 6. Token economics of resume vs transcript-stuffing

Three-turn chain, trivial prompts:

| Turn | Codex input (cached) | Claude total input (cache read) | Claude cost |
|---|---|---|---|
| 1 | 15,562 (13,056) | 6,750 (2,800) | $0.0416 |
| 2 | 31,332 (28,160) | 6,782 (6,748) | $0.0038 |
| 3 | 47,139 (43,264) | 6,824 (6,780) | $0.0044 |

- Claude's accounted input stays flat (~6.8k) and is ~99% cache-read after turn
  one. Cost drops 10x once the cache is warm.
- Baseline overhead per Codex call is ~15.5k input tokens before any user
  content. Relevant to any step-count budgeting.

### 6a. Codex usage is cumulative per thread (settled)

Run `./research/experiments/resume/resume-spike --token-accounting`. Turn 1 carried ~7.5k of filler;
turn 2 was a three-word prompt.

| Turn | Prompt | Reported `input_tokens` |
|---|---|---|
| 1 | ~7.5k filler + instruction | 23,094 |
| 2 | "Reply with only the word ACK2." | 46,386 |

Ratio 2.009, delta 23,292 — approximately turn 1's own value. A three-word
request cannot contain 23k tokens, so **the reported figure is a running total
for the thread, not the size of that request.**

Therefore per-call usage is `reported[N] - reported[N-1]` within a session lane.
Storing the raw value as if it were per-call overcounts badly: a three-round
debate would report roughly 3x the proposer lane's actual usage.

Claude is the opposite — genuinely per-request, split across `input_tokens`,
`cache_creation_input_tokens`, and `cache_read_input_tokens`, which must be
summed for a total. Claude also reports `total_cost_usd`; Codex reports no cost
field at all.

Conclusion on the plan's §9 handoff rule: resume's advantage over the POC's
transcript-stuffing is **cache hits, not smaller payloads**. Growth is comparable;
price per token is not. That strengthens the case for resume but does not remove
the need to bound what crosses providers, since cross-provider handoff can never
be cache-served.

## 7. What this changes in the plan

1. Phase 1 step 5 ("implement compatible native resume") is no longer a research
   spike. Keep it. `resume: required` in the built-in workflow is achievable.
2. Add to §9: resume-compatibility must be harness-enforced, because providers
   accept mode changes silently. Define `compatibility_hash` as at minimum
   provider + CLI version + model + execution mode + resolved workspace path.
3. Add to §9: on Codex resume, mode and cwd are not expressible as flags. Set
   subprocess `cwd` explicitly and never pass `-c sandbox_mode` from anything a
   workflow can influence.
4. `--ephemeral` on Codex disables session persistence and therefore disables
   resume. Do not use it in workflows that declare `resume: required`.
5. Record `terminal_reason` and `permission_denials` from Claude verbatim rather
   than deriving equivalents.
6. `codex exec --output-schema` is the tool for the structured-handoff hole.

## 8. Incidental

- `timeout(1)` does not exist on this macOS box. The harness must own timeouts
  in-process, which the POC already does.
- First spike run reported a false `RESUME_DOES_NOT_CARRY_CONTEXT` because
  `--color never` made `resume` exit 2 on argument parsing. Provider adapters
  must treat a non-zero exit with empty output as a harness/argv bug, not a
  model result — otherwise `doctor` and the run ledger will record fiction.

## 9. Codex challenger model identifier — settled 2026-07-29

The model identifier is **`gpt-5.6-sol`**.

Two independent checks agree:

1. The current official Codex model-selection manual says the default Power
   setting uses `gpt-5.6-sol` with medium reasoning and recommends Sol for
   complex, open-ended, high-value review work.
2. A live call through the installed `codex-cli 0.146.0-alpha.2` accepted the
   identifier and returned `MODEL_ID_OK`.

Exact live argv:

```text
codex exec --ephemeral --ignore-user-config --sandbox read-only \
  --skip-git-repo-check --cd <repo-root> --model gpt-5.6-sol --json \
  "Reply with exactly: MODEL_ID_OK"
```

Observed usage: 16,732 input tokens and 7 output tokens. This settles the
placeholder in the review role binding; the built-in challenger default is
`gpt-5.6-sol`.

## 10. S1 structured-handoff decision — settled 2026-07-29

The raw run evidence is intentionally excluded from the public repository
because it contains workstation paths, session identifiers, and unrelated
checkout provenance. The sanitized fixture and findings remain reproducible.

Models:

- baseline and challenger: `gpt-5.6-sol` through `codex-cli 0.146.0-alpha.2`;
- reviewer: `claude-sonnet-5` through Claude Code `2.1.220`.

Exact spike argv:

```text
research/experiments/resume/resume-spike --structured-handoff \
  --workspace research/experiments/resume/fixtures/s1 \
  --review-file enrollment_reconciler.py \
  --review-file test_enrollment_reconciler.py \
  --goal "Reconcile enrollment events into application status updates and publish \
the result. The implementation must tolerate retries and large mixed-event \
backlogs while preserving existing multi-tenant and privacy guarantees." \
  --codex-model gpt-5.6-sol \
  --claude-model claude-sonnet-5 \
  --timeout 900 \
  --output /tmp/gigai-s1-structured-handoff-evidence.json
```

### Method

The fixture carried six held-aside defects:

1. application lookup was not partner-scoped;
2. the processed marker poisoned retries before side effects completed;
3. the program cache omitted the partner from its key;
4. timezone replacement changed offset-aware instants;
5. logs exposed a user UUID and arbitrary raw payload;
6. pagination stopped based on the filtered rather than raw page count.

The ground truth was not present in either provider's workspace prompt. Arm A was
a fresh Codex single-pass review. Arms B and C shared one Sonnet review, then
fresh Codex sessions received either Sonnet's prose or its validated findings.
Every arm saw the same target files and goal.

### Results

| Arm | Seeded defects found | Clear false positives | Total tokens | Versus baseline |
|---|---:|---:|---:|---:|
| A — single model | 6/6 | 0 | 82,328 | — |
| B — prose handoff | 6/6 | 0 | 201,984 | +145.3% |
| C — structured handoff | 6/6 | 0 | 156,456 | +90.0% |

The baseline also found the real unbounded-result-list issue. The prose arm added
the real concurrent check-then-act race, one contract-dependent event-type claim,
and one fixture/git metadata note. The structured arm added the same unbounded
list and concurrency issues the baseline already had, plus two
contract-dependent claims about state transitions and per-event failure
isolation. It found no additional seeded defect and no new clearly confirmed
defect beyond the baseline.

Both challenger arms made the same disposition on every Sonnet finding. Structured
used 22.5% fewer total tokens than prose and found four additional concerns versus
three, but two of its four additions needed a contract or human decision. The
current schema gives challenger-originated findings no independent status, which
made them look recommendation-ready when they were not.

### Decision

1. **A mandatory challenger is not justified as the default review shape.** On
   this fixture it cost 90–145% more without improving seeded recall or producing
   a new clearly confirmed defect. The built-in `review` defaults to
   `verify → reviewer → recommendations`.
2. **Challenge remains an explicit escalation.** When selected, it uses a
   different provider and receives validated findings only.
3. **Do not pass or retain prose as a handoff sibling.** It cost more and did not
   improve any disposition.
4. **Keep the candidate finding schema:** `id`, `severity`, `claim`, `evidence`,
   `recommendation`, `confidence`.
5. **Keep the candidate challenge schema:** `finding_id`, `status`, `evidence`,
   `explanation`, `confidence`.
6. **Keep the candidate recommendation schema:** `rank`, `finding_id`,
   `severity`, `recommendation`, `because`, `evidence`, `status`.
7. **Challenger-originated findings enter as `needs_human` and are not
   recommendations until independently accepted.**

### Incidental adapter and S2 evidence

- Codex may emit multiple `agent_message` events. The adapter must retain all as
  events but use the last one as the final structured response; concatenating
  them produced invalid JSON after an otherwise successful run.
- Of three explicit Sonnet first attempts observed while reaching the decision
  run, two were schema-valid and one was malformed JSON. That is not enough to
  settle S2, but it proves the bounded corrective retry is necessary and that
  failed attempts belong in token accounting.
- The spike now checkpoints each provider result before later validation so a
  downstream parser failure cannot discard paid evidence.
