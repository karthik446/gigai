# S10 — Ollama invocation and harness onboarding research

**Date:** 2026-09-21  
**Scope:** research only; v0.1.8 spike, explicitly off the v0.1.7 release path.  
**Evidence boundary:** no model inference, model download, daemon startup, settings change, credential/private-workpad inspection, paid API call, or full-suite run was performed.

## Bottom line

For an already-installed model, the smallest understandable path is: verify the
Ollama runtime and exact model identity, invoke the native local API with
`stream:false`, validate the response, and record endpoint/version/model/digest
with the result. Use the native API for GigAI's bounded adapter; treat
OpenAI-compatible endpoints as an interoperability option, not as proof of the
same feature set or local routing. An agent harness adds tools, file access,
execution policy, iteration, cancellation, persistence, and authority gates;
an inference endpoint alone is not a coding-agent workflow.

The screenshot's “ChatGPT” menu entry and “0 requests this session” are
observations only. I found no authoritative Ollama documentation in the
existing repo evidence that identifies that desktop entry, proves which backend
it uses, or proves model identity/locality. Keep it **unknown** until the
specific product/version documentation or a captured request trace establishes
otherwise; do not recommend it as the GigAI path.

## Task-routing recommendation

| Work | Smallest suitable route | Why / hard boundary |
|---|---|---|
| Run `pytest`, installed verifiers, schema checks, package checks | Deterministic subprocess runner; capture argv, start/end, stdout/stderr, exit code, timeout, and receipt | No LLM at all. A model must not report verifier success or replace the exit code. |
| Summarize bounded logs, classify a known failure, suggest initial triage | Explicitly selected local Ollama model through the adapter | Bound bytes/tokens/time; validate structured output; no hosted fallback. A local label is not privacy proof. |
| Uncertain code, authority, privacy, provenance, or release decisions | Luna/higher review with the applicable evidence and authority gates | Escalate uncertainty; model selection never authorizes effects or private-data export. |

When a deterministic job is dispatched, record one rough ETA, return control to
the agent, and wake on a durable completion event containing the process receipt.
If the event is missing after the ETA, allow one scheduled fallback check; do
not spin in agent turns or repeated sleep/status polling. A revised estimate is
acceptable after bounded inspection. This follows S07's event/yield direction;
it is not a claim that the current harness already provides durable wakeups.

## Invocation matrix

| Route | Minimum path | Supports / does not establish | Status |
|---|---|---|---|
| Ollama CLI | `ollama --version`; `ollama list`; `ollama ps`; `ollama run <tag>`; stop the interactive run with `Ctrl-C` | Human onboarding and inventory. CLI success does not expose a stable structured-result receipt or prove a harness stayed local. | Official CLI surface; exact installed version and tags must be captured. |
| Native Ollama API | `GET /api/version`, `GET /api/tags`, then `POST /api/chat` with selected `model`, `messages`, `stream:false`; follow the current API documentation's cancellation mechanism for the selected endpoint | Native chat, structured outputs via `format`, tools/tool calls where the selected model supports them, and runtime metrics documented by Ollama. Response validation, cancellation, and policy remain caller responsibilities. | Official API shape reused by existing evidence; not executed in this turn. |
| OpenAI-compatible API | Point an OpenAI client at the documented Ollama compatibility base URL and use a selected model | Useful migration surface, but compatibility is not native-feature parity, not a GigAI adapter, and not proof of local routing or no cloud fallback. | Documented compatibility option; no compatibility call performed. |
| GigAI adapter | Resolve an `ollama_local` target with explicit `http://127.0.0.1:<port>`, model tag, digest, bounds, and policy; adapter verifies version/tags before and after chat | Loopback-only native transport, identity race check, bounded timeout/response, cloud-marker refusal, structured result/error mapping. It owns no daemon lifecycle, tools, credentials, or hosted fallback. | Repo-verified source/tests and prior synthetic probes; installed/live caller proof remains separate. |
| Agent harness (Luna/Orca/etc.) | Configure the harness's provider/model explicitly, then separately verify tool, file, subprocess, context, cancellation, and persistence behavior | A harness may add capabilities and telemetry outside inference. A compatible HTTP endpoint alone proves none of those capabilities or locality/privacy properties. | Harness-specific onboarding is untested here; do not infer it from the screenshot. |

## Minimal first-use recipes

These are documentation-grounded examples, **not commands run in this research
turn**. Capture the exact installed version and model metadata in any later
authorized proof.

```sh
ollama --version
ollama list
ollama ps
curl --fail --silent http://127.0.0.1:11434/api/version
curl --fail --silent http://127.0.0.1:11434/api/tags
```

The native request should select a concrete tag (not an unpinned “latest” in a
durable receipt), set `stream:false`, bound `options.num_predict`, and validate
the returned assistant message before accepting it:

```sh
curl --fail --silent http://127.0.0.1:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"<selected-tag>","messages":[{"role":"user","content":"<bounded task>"}],"stream":false,"options":{"num_predict":256}}'
```

For structured output, use native `format` with a JSON schema where supported,
then parse and validate against the caller's schema. Do not treat syntactically
valid JSON as semantically authorized output. For a long-running native
generation request, use the documented cancellation endpoint/mechanism for that
endpoint and retain a cancellation receipt; do not claim that `Ctrl-C` on the
CLI cancels an arbitrary HTTP request.

Official primary references (version caveat: documentation and CLI behavior can
change; pin and record the runtime version in proof):

- Ollama API: <https://docs.ollama.com/api> (version, tags, chat, streaming,
  options, metrics, and request cancellation details).
- Ollama CLI: <https://docs.ollama.com/cli> (run/list/ps/stop and command
  surface).
- Structured outputs: <https://docs.ollama.com/capabilities/structured-outputs>.
- Tool calling: <https://docs.ollama.com/capabilities/tool-calling>.
- OpenAI compatibility: <https://docs.ollama.com/api/openai-compatibility>.

## Reuse and gaps in this repo

### Repo-verified reuse

- `src/gigai/adapters/ollama_local.py:1` deliberately owns no process
  lifecycle, credentials, cloud fallback, or tool execution. It requires a
  numeric loopback URL, selected model, and digest; it checks identity before
  and after the request.
- `src/gigai/adapters/ollama_local.py:70` restricts transport to explicit
  `http://127.0.0.1:<port>` and rejects credentials, paths, queries, fragments,
  and non-loopback hosts. The adapter bounds timeout, context, output, and
  response bytes and rejects cloud/remote model markers.
- `src/gigai/adapters/ollama_local.py:238` verifies `/api/version` and
  `/api/tags`; `:216` posts native `/api/chat`; `:280` onward validates response
  shape and normalizes usage. `src/gigai/adapters/factory.py:80` resolves the
  explicit `ollama_local` target and passes identity/bounds.
- `docs/development/evidence/v0.1.7/Scout/SCOUT-local-adapter-review.md:22`
  records the earlier bare-versus-prefixed digest defect and the need for
  assistant-role/output-bound validation. Current source contains the
  canonicalization and bounds fixes; preserve their offline negative coverage.
- `docs/development/v0.1.8/spikes/evidence/qwen38-scout-local-eval.md:33`
  records a prior installed proof at `http://127.0.0.1:11434`, with captured
  model digest, Ollama `0.33.3`, synthetic fixtures, no hosted inference, and
  explicit limitations. It is not proof for a different model/tag, harness, or
  current machine.

### Gaps / untested claims

- The current caller/harness path still needs an explicitly reviewed distinction
  between permitted loopback local transport and hosted network access; the
  adapter alone cannot prove caller policy, journal persistence, redaction, or
  authority. The prior review identifies this seam in
  `docs/development/evidence/v0.1.7/Scout/SCOUT-local-adapter-review.md:107`.
- No current evidence establishes Luna/Orca tool calls, file access, subprocess
  execution, context limits, cancellation, restart recovery, telemetry, or
  durable completion events when backed by Ollama.
- No evidence establishes what the screenshot's desktop “ChatGPT” integration
  is, whether it uses Ollama's native API or compatibility API, or whether its
  requests are local versus remote.

## Documented vs verified vs untested

**Documented:** Ollama's official API/CLI/structured-output/tool-calling and
compatibility surfaces at the links above; exact feature availability remains
model/runtime/version dependent.  
**Repo-verified:** the adapter's loopback/identity/bounds/error policy, factory
resolution, and prior synthetic/injected transport evidence cited above.  
**Untested in this turn:** every live command/API request, model inference,
current installed inventory, desktop menu route, harness workflow, and durable
background wakeup.

## Smallest separately authorized live proof

1. Read-only capture of `ollama --version`, `ollama list`, `ollama ps`, and
   `/api/version` plus `/api/tags`; select one already-installed exact tag/digest.
2. One synthetic, non-private native `/api/chat` request with `stream:false`,
   bounded output, no tools, and a short timeout; record request/response
   envelopes, HTTP status, elapsed time, and exit/error receipt. Do not download
   or start anything.
3. Run the existing adapter's focused injected tests plus one authorized local
   transport probe; verify model/digest/structured output/timeout and prove that
   a forbidden hosted fallback is refused. Do not call a paid provider.
4. Separately exercise the selected harness with a harmless synthetic task,
   checking tool/file/subprocess/cancellation behavior and recording the actual
   endpoint. This must not be generalized to private-data safety or verifier
   certification.

## Recommended implementation packet (not implemented)

Add one small, explicit local-task runner seam: (a) deterministic subprocess
jobs emit durable start/end/exit/log receipts and completion events; (b) local
Ollama summaries/classifiers require pinned tag+digest, loopback endpoint,
structured schema validation, byte/token/time bounds, and `no_hosted_fallback`;
(c) the caller records routing and yields after one ETA; (d) Luna review is
  required for uncertain authority/privacy/code decisions. Add focused offline
  tests for routing, malformed output, digest mismatch, timeout/cancellation,
  duplicate/missing completion, and forbidden fallback. Defer any desktop-menu
  integration until its product documentation or a trace identifies the route.
