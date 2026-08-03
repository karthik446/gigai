# GigAI Phase 0 spikes

- **Date:** 2026-07-30
- **Status:** complete
- **Parent plan:** `docs/architecture/v14-implementation-plan.md`, Section 21
- **Evidence code:** `research/phase0_spike/`

The revision-14 serialized-contract follow-up is
`docs/research/phase-0-contract-closure.md`, with binding schemas in
`src/gigai/schemas/` and executable evidence in `research/contract_spike/`.

These spikes answer the seven Phase 0 questions with executable evidence. They
do not implement GigAI. Each spike must leave:

1. a fixture or captured command output;
2. an automated assertion where practical;
3. a written decision;
4. explicit limitations and a trigger for revisiting the decision.

No ordinary spike test may spend model tokens or call a live integration.
Provider live probes are separate, explicit commands and their usage is
recorded.

## Summary

| ID | Question | Status | Decision |
|---|---|---|---|
| P0.1 | Is `gig` an acceptable console command? | decided | No. Use `gigai`; do not install a `gig` alias. |
| P0.2 | Can annotations produce good CLI help without workflow-specific Click code? | proven | Yes for flat scalar, path, literal, boolean, and repeated-list inputs. |
| P0.3 | Callable-first `PythonTool` or subprocess boundary from day one? | decided | Callable-first authoring, subprocess execution from day one. |
| P0.4 | Are planning placeholders useful for `review`? | proven | Yes. Require a case only when Python inspects a planned result. |
| P0.5 | What is the minimum dirty-workflow replay bundle? | proven | Exact workflow/tool/resource/project bytes plus hashes and Git/runtime context. |
| P0.6 | Which Codex and Claude CLI capabilities remain true? | proven | Both pass native schema, read-only, session/resume, and usage probes. |
| P0.7 | Can one OpenAI-compatible adapter cover OpenRouter and custom endpoints? | decided | Yes for v1's non-streaming core, configured by dialect and capabilities. |

## Rules for interpreting results

- One successful fixture proves feasibility, not general correctness.
- Provider `--help` proves flag availability, not runtime behavior.
- Provider behavior is stamped with the exact CLI version.
- A local mock proves adapter normalization, not remote compatibility.
- Performance numbers are directional and include machine/runtime context.
- A declaration is not a sandbox. Tool-boundary conclusions must distinguish
  authoring ergonomics from execution isolation.
- Arbitrary Python cannot be made statically declarative by renaming it a plan.

---

## P0.1 — Console command

### Question

Is `gig` an acceptable console command on supported developer machines?

### Method

Check:

- current machine `PATH` for `gig` and `gigai`;
- Python package-name availability separately from console-command collision;
- prominent existing developer tools using `gig`;
- pronunciation, typo risk with `git`, and shell completion behavior;
- fallback behavior if a collision exists.

### Evidence

Installed-machine check on 2026-07-30:

```text
command -v gig    -> not found
command -v gigai  -> not found
```

External collision evidence:

- PyPI already publishes [`gig`](https://pypi.org/project/gig/) as an active
  `.gitignore` CLI. It documents `pip install gig`, `gig list`, and
  `gig --help`.
- A current cross-platform Go task tool also installs
  [`gig`](https://pkg.go.dev/github.com/NeerajG03/gig) through Homebrew or
  `go install`. Its commands include `gig init`, `gig list`, and `gig show`,
  directly colliding with this plan's proposed surface.
- A search did not establish ownership or publishability of the `gigai` package
  name. Package and repository reservation remains a publication task; it is
  separate from choosing the console command.

### Decision

`gig` is not an acceptable primary executable. The fact that it is absent from
this machine does not outweigh two existing developer CLIs, including one with
overlapping subcommands.

Use `gigai` as the canonical executable and in every documented command. Do not
ship a `gig` compatibility alias: aliases still shadow existing executables and
make scripts machine-dependent.

### Revisit when

- a supported-machine bootstrap detects a collision;
- public package publication introduces a naming conflict;
- user testing shows persistent `git`/`gig` typo confusion.

---

## P0.2 — Annotation-derived CLI

### Question

Can workflow input annotations produce good CLI help without per-workflow Click
code?

### Required input shapes

The prototype must cover:

- required string;
- optional string with a default;
- enum/literal;
- boolean flag;
- optional boolean with positive and negative forms;
- repeatable list;
- integer;
- `Path`;
- one declared positional primary input;
- validation errors;
- help text from field descriptions.

### Method

Build one generic Click command factory over a strict Pydantic input model.
The workflow contributes only:

- the input model;
- an optional primary positional field name.

Snapshot `--help`, parse one valid invocation, and assert invalid input fails
before the workflow would run.

### Evidence

Fixture:
`research/phase0_spike/annotation_cli.py`

The generic factory produced this workflow help without workflow-specific Click
code:

```text
Usage: python -m research.phase0_spike.annotation_cli [OPTIONS] TARGET

Options:
  --kind [code|design|plan|db-schema]  Review surface.  [required]
  --workspace PATH                    Target workspace.  [default: .]
  --since TEXT                        Diff base for code review.
  --challenge / --no-challenge        Add an independent challenger.
  --references / --no-references      Use fresh references when available.
  --context PATH                      Additional context path; repeatable.
  --max-findings INTEGER              Maximum findings to return.  [default: 20]
```

`test_annotation_cli.py` also proves valid conversion into the Pydantic model
and rejection of `max_findings=0` before workflow execution.

### Decision

Use one generic annotation-derived CLI builder. A strict Pydantic input model
is sufficient for v1's flat input contract. Workflows supply their model,
descriptions, defaults, and optionally one primary positional field; they do
not write Click code.

Nested objects and ambiguous unions are not mapped to bespoke flags. Accept
them through a JSON/file input if they become necessary, or revisit the
contract.

### Revisit when

- nested input objects need first-class flags rather than JSON/file input;
- unions become ambiguous;
- a workflow needs ordering or mutually-exclusive groups that annotations cannot
  express clearly.

---

## P0.3 — Python tool execution boundary

### Question

Is a callable-first `PythonTool` sufficient, or should every tool cross a
subprocess boundary from day one?

This is two decisions, not one:

1. What should authors write?
2. Where should live execution occur?

### Method

Compare:

- direct in-process callable;
- importable callable executed by a one-shot subprocess worker.

Measure and verify:

- typed JSON input/output;
- stdout/stderr capture;
- exception classification;
- timeout and termination;
- process crash containment;
- environment/cwd control;
- repeated-call overhead.

### Evidence

Fixture:
`research/phase0_spike/tool_boundary.py`

Both paths capture typed output, stdout/stderr, and ordinary exceptions. Only
the subprocess path also:

- contained an `os._exit(17)` crash;
- timed out and terminated a process group;
- controlled the child cwd and environment;
- prevented one tool's interpreter state from corrupting the runner.

Twenty no-op-sized calls on the current machine measured:

```json
{
  "in_process_median_ms": 0.000875,
  "subprocess_median_ms": 38.753354,
  "subprocess_p95_ms": 43.179083
}
```

### Decision

Authors write an importable Python callable with typed JSON-compatible input and
output. Every live tool invocation crosses a one-shot subprocess boundary from
day one.

The roughly 39 ms median startup cost is immaterial beside repository commands
and model calls. This buys real crash, timeout, cwd, and environment containment
without a plugin system. A future persistent worker is an optimization behind
the same tool contract, not a v1 prerequisite.

### Revisit when

- measured process startup dominates a real deterministic workflow;
- a persistent worker is needed;
- pack-level dependency isolation becomes a real requirement.

---

## P0.4 — Planning placeholders

### Question

Can planning placeholders produce a useful plan for `review`, or should `plan`
require a case whenever a model/tool result is consumed?

### Method

Execute two ordinary Python workflows against `PlanRun`:

1. `review`: branches only on input (`challenge`) and passes unresolved tool
   output into a model call without inspecting it;
2. result-driven workflow: branches on `checks.failed`.

A planning value may flow into another recorded call. Boolean conversion,
iteration, comparison, length, or field inspection must raise a typed
`CaseRequired` error naming the producing call.

### Evidence

Fixture:
`research/phase0_spike/planning.py`

The review fixture produced this complete plan without a case:

```text
01 tool  project-checks   depends on []
02 model reviewer         depends on [01-project-checks]
03 model challenger       depends on [02-reviewer]
04 tool  recommendations  depends on [02-reviewer, 03-challenger]
```

The same placeholder raises:

```text
field access 'failed' requires the result of 01-project-checks;
run rehearsal with a case
```

when a workflow evaluates `checks.failed`.

### Decision

`plan` is useful without a case when branches depend only on declared inputs or
configuration and unresolved outputs merely flow into later calls. Placeholders
record dependencies and are otherwise opaque.

Require a rehearsal case at the first result inspection: field access, boolean
conversion, comparison, iteration, or length. Do not invent a value to continue
the plan. `plan` is a zero-effect structural preview; `rehearse` remains the
authoritative execution proof.

### Revisit when

- real workflows frequently branch on deterministic outputs;
- placeholder dependency tracking makes normal Python unreadable;
- cost planning requires output-dependent token estimates.

---

## P0.5 — Dirty Python source bundle

### Question

What minimum source bundle is sufficient to explain or replay a dirty Python
workflow run?

### Candidate bundle

```text
source/
  manifest.json
  workflow/                 exact bytes of the workflow package
  tools/                    exact bytes of registered tools actually called
  resources/                declared prompt/schema/resource files
  pyproject.toml
  uv.lock
```

`manifest.json` records:

- workpad root identity;
- Git HEAD full SHA when available;
- dirty status summary;
- relative path, role, size, and SHA-256 for every copied file;
- GigAI version;
- Python version;
- resolved workflow and tool names.

### Method

Create a bundle containing modified and untracked fixture files, copy it to a
new directory, and verify every byte from the manifest. Demonstrate that a Git
SHA plus `git diff` alone is insufficient for untracked files.

### Evidence

Fixture:
`research/phase0_spike/source_bundle.py`

The tests create a bundle from dirty and untracked fixture bytes, extract it
elsewhere, and compare every replayed byte. They also prove:

- a file outside the workpad is rejected;
- changing bundled source without updating its hash is detected;
- an untracked workflow can be replayed even though Git has no blob for it.

The spike also bundled its own dirty source against repository HEAD
`a3048f6a8832d2ea2ad63dd59acffd0ba2ab76eb`, recording the workflow, used tool
files, this decision resource, `pyproject.toml`, `uv.lock`, the dirty status,
Python 3.13.9, sizes, roles, and SHA-256 values.

### Decision

Store exact bytes, not only a source SHA and diff.

The minimum replay/explanation bundle is:

1. the resolved workflow package;
2. definitions of tools actually called;
3. declared prompt, schema, and resource files;
4. dependency metadata and lockfile when present;
5. a manifest with relative paths, roles, sizes, SHA-256 hashes, Git HEAD and
   dirty status, runtime version, workflow name, and tool names.

Do not copy the entire repository. The run's rendered prompts, model responses,
tool envelopes, and final output remain ordinary run artifacts outside this
source bundle.

### Revisit when

- workflows use dynamic files not declared as resources;
- native dependencies affect reproducibility;
- bundle sizes become material compared with ordinary run artifacts.

---

## P0.6 — Installed provider capabilities

### Question

Which provider capabilities remain true on the installed Codex and Claude CLI
versions?

### Installed versions

Captured 2026-07-30:

```text
codex-cli 0.146.0-alpha.2
Claude Code 2.1.220
```

### Zero-token observations

Codex `exec --help` currently exposes:

- `--sandbox read-only`;
- `--cd`;
- `--output-schema <file>`;
- `--json`;
- `--ephemeral`;
- `--ignore-user-config`;
- `exec resume`.

Claude currently exposes:

- `--permission-mode plan`;
- `--safe-mode` and the newer `--bare`;
- `--tools`;
- `--json-schema <schema>`;
- JSON and stream-JSON output;
- `--session-id` and `--resume`;
- `--no-session-persistence`;
- `--max-budget-usd`.

The native Claude `--json-schema` flag is newer than the assumptions in the LMH
plan. If the live probe validates it, the earlier prompt-plus-validation-only
design is obsolete.

### Live probe matrix

Live fixture:
`research/experiments/resume/resume-spike --phase0-capabilities`

The 2026-07-30 live run made two calls per provider in a throwaway workspace:
a structured start that attempted a file write, then a resume that had to
recall a nonce without receiving the transcript.

| Capability | Codex 0.146.0-alpha.2 | Claude Code 2.1.220 |
|---|---|---|
| Minimal read-only invocation | pass | pass |
| Native structured output | pass via `--output-schema` | pass via `--json-schema` |
| Expected structured value | `{"ack":"ACK","write_status":"BLOCKED"}` | same |
| Session ID capture | pass | pass |
| Resume carries context | pass | pass |
| Attempted file absent | pass | pass |
| Machine-readable usage | pass | pass |
| Process-group timeout support | harness fixture passes | harness fixture passes |

Codex reports input, cached input, cache-write input, output, and reasoning
output tokens through JSONL events. Claude reports input, cache creation/read,
output, service-tier, iteration, model, and cost data in its JSON envelope.
The two Claude calls reported a combined cost of $0.128916.

One useful schema-dialect finding came from the first Codex attempt: a property
using `const` or `enum` must still declare its JSON `type`. After adding the
type, the same probe passed. GigAI must validate provider schemas before paying
for a call.

Mode widening during resume was deliberately not made a GigAI contract and was
not rerun. Every invocation must receive an explicit effect policy; GigAI must
not depend on undocumented permission inheritance.

### Decision

The installed Codex and Claude CLIs provide the v1 capabilities GigAI needs:
read-only invocation, native structured output, native session capture/resume,
machine-readable results, and usage reporting.

Replace the old Claude prompt-plus-JSON-only assumption with native
`--json-schema` plus local validation. Keep prompt-and-validate only as an
explicit fallback for older installed versions. Stamp CLI version and
capability-probe result into diagnostics; do not infer support from provider
name.

### Revisit when

- either installed CLI version changes;
- a provider changes its structured-output or session format;
- a new adapter relies on a capability not covered by this matrix.

---

## P0.7 — OpenAI-compatible adapter

### Question

Does one OpenAI-compatible adapter cover the intended OpenRouter and custom/local
API paths without provider-name branches?

### Method

Separate capability from provider identity:

```text
base_url
endpoint dialect: chat_completions | responses
model
API-key environment variable
extra headers
structured-output capability
streaming capability
usage fields
```

Run one generic adapter against local fixture servers for:

- Chat Completions response;
- Responses API response;
- structured JSON output;
- missing usage;
- reasoning-token usage;
- standard HTTP error;
- malformed response.

Then compare the configuration surface with current official OpenRouter and
OpenAI documentation. A real OpenRouter smoke call is optional and must not
print or persist the API key.

### Evidence

Fixture:
`research/phase0_spike/openai_compat.py`

Mock-transport tests prove the same adapter can:

- construct and normalize Chat Completions;
- construct and normalize Responses;
- send the correct structured-output shape for each dialect;
- preserve the raw response;
- tolerate missing usage;
- normalize reasoning tokens;
- reject an undeclared structured-output capability;
- classify HTTP and malformed-response failures.

This matches current official OpenRouter documentation:

- its [quickstart](https://openrouter.ai/docs/quickstart) calls Chat
  Completions OpenAI-compatible, documents `/api/v1/chat/completions`, and shows
  the OpenAI SDK configured with a base URL and optional headers;
- its [structured-output guide](https://openrouter.ai/docs/guides/features/structured-outputs)
  uses Chat Completions `response_format.json_schema` and makes support
  model/provider-specific;
- its [Responses documentation](https://openrouter.ai/docs/api_reference/responses/basic-usage)
  documents `/api/v1/responses`, nested output text, and usage fields.

`OPENROUTER_API_KEY` was not present in the spike environment, so no real
OpenRouter request was made. That is a missing optional smoke fixture, not a
reason to add provider branches.

### Decision

One capability-configured adapter is sufficient for v1's non-streaming model
calls across OpenRouter and custom/local OpenAI-compatible endpoints. Configure
`base_url`, `chat_completions` versus `responses`, API-key environment variable,
extra headers, and supported features. Never branch on the string
`"openrouter"`.

This decision does not claim universal OpenAI compatibility. Streaming, tool
calling, provider routing, generation IDs/cost lookup, and nonstandard error
metadata are outside this fixture. Add a generic capability or raw-metadata
extension only when a workflow consumes one.

### Revisit when

- exact provider routing or generation accounting is required;
- an endpoint cannot express its differences as capabilities/configuration;
- Responses and Chat Completions normalization lose information needed by
  workflows or evals.

---

## Commands

Offline spike suite:

```bash
uv run pytest research/phase0_spike/tests -q
```

Result:

```text
17 passed
```

Additional evidence commands:

```bash
python -m research.phase0_spike.annotation_cli --help
python -m research.phase0_spike.tool_boundary --benchmark 20
python -m research.phase0_spike.source_bundle ...
python research/experiments/resume/resume-spike --phase0-capabilities --provider both \
  --output /private/tmp/gigai-phase0-provider-capabilities.json
```

Live provider probes are never part of the default pytest suite.
