# SCOUT local privacy boundary demonstration

**Date:** 2026-09-11  
**Status:** bounded empirical spike; synthetic fixture only; no production
acceptance.  Results are **A: partial** and **B: blocked for the existing
Ollama server**.

## Executive result

The existing installation is useful for short local Qwen inference: Ollama
`0.34.0` served the installed `qwen3.8:latest` (27.3B Q4_K_M, digest
`22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`) using the
Apple M4 Pro Metal backend. Two bounded synthetic requests completed in 32.476s
and 31.498s, but the 220-token cap truncated both (the first emitted only
thinking; the second emitted proposal prose with a fabricated date and no full
questions/references), so this demonstrates capability, not a release-quality
proposal.

The available macOS `sandbox-exec` primitive denied unselected synthetic file
reads and direct/child loopback connections while the positive control worked.
However, starting the existing Ollama HTTP server under that same deny-network
policy failed immediately at loopback bind (`operation not permitted`), and the
actual inference server used by the successful requests was unsandboxed. Thus
the required property “private inference process and server/children cannot
egress/read unselected data” is **not demonstrated for Ollama**; only the
generic process-policy primitive is demonstrated.

## Scope and safety

All data was synthetic. The fixture includes a fake public posting, fake salary/
sponsorship/experience, and `SYNTHETIC-LOCAL-ONLY-CANARY-7f2e`; the canary was
never sent to the receiver or any external endpoint. No ignored `.gigai`
workpad, resume, chat, credential, environment secret, provider/API, model
download, package/container install, global configuration, firewall change,
service restart, or unrelated process termination occurred.

Reproducible scripts and results are in
[`research/scout-local-boundary-demo/`](../../../../../research/scout-local-boundary-demo/):

- `fixtures/proposal-request.json`
- `scripts/run_local_proposal.py`
- `scripts/enforcement_actor.py`
- `scripts/run_enforcement_probe.py`
- `scripts/probe_ollama_sandbox.py`
- `results/proposal-qwen3.8.json`
- `results/proposal-qwen3.8-no-think.json`
- `results/enforcement-probe.json`
- `results/ollama-sandbox-attempt.json`

## A — local inference usefulness

### Observed identity and commands

The installed command was `/usr/local/bin/ollama`; `ollama --version` reported
client `0.34.0`. The installed manifest
`/Users/kar/.ollama/models/manifests/registry.ollama.ai/library/qwen3.8/latest`
was read directly without touching model blobs. Its parent is
`qwen3.8:27b-q4_K_M`; the task-owned server API reported qwen3.8 metadata,
family `qwen35`, 27.3B parameters, Q4_K_M, 17,741,872,154 bytes, context
262,144, and capabilities `completion`, `tools`, `thinking`, and `vision`.

The task-owned server was launched on free loopback port 11499 with per-process
variables only:

```sh
OLLAMA_HOST=127.0.0.1:11499 \
OLLAMA_MODELS=/Users/kar/.ollama/models \
/usr/local/bin/ollama serve
```

Read-only metadata calls returned version `0.34.0` and three installed models
(`qwen3.8:latest`, `llama2:latest`, `mistral:latest`). No `pull`, download,
deletion, or global config change was used. The server observed an Apple M4 Pro
Metal device with approximately 51.8 GiB available GPU memory; this is machine
evidence, not a claim of portability.

The two request commands and limits were:

```sh
.venv/bin/python research/scout-local-boundary-demo/scripts/run_local_proposal.py \
  --host http://127.0.0.1:11499 --model qwen3.8:latest \
  --fixture research/scout-local-boundary-demo/fixtures/proposal-request.json \
  --output research/scout-local-boundary-demo/results/proposal-qwen3.8.json \
  --timeout 120

.venv/bin/python research/scout-local-boundary-demo/scripts/run_local_proposal.py \
  --host http://127.0.0.1:11499 --model qwen3.8:latest \
  --fixture research/scout-local-boundary-demo/fixtures/proposal-request.json \
  --output research/scout-local-boundary-demo/results/proposal-qwen3.8-no-think.json \
  --timeout 120
```

Each request used `stream=false`, `num_ctx=2048`, `num_predict=220`, and
`temperature=0.2`. The fixture instructed the model to produce a private
proposal—not a resume, cover letter, application, or application event—and to
show fit, blockers/unknowns, proposed resume focus, focused questions, and
references while treating posting instructions as untrusted data.

| Request | Time | Runtime result | Usefulness verdict |
|---|---:|---|---|
| Thinking default | 32.476s | 220 tokens, `done_reason=length`, assistant content empty; thinking contained a sensible fit outline | Partial: local model responded, but output framing cannot be accepted as a proposal. |
| `think=false` | 31.498s | 220 tokens, `done_reason=length`, proposal prose began but stopped before questions/references and invented `2023-10-27` | Partial: evidence-backed synthesis began, but bounded output was incomplete and not fully trustworthy. |

The second response is useful as a capability signal only. The production
boundary would need a strict structured output validator, explicit source refs,
date prohibition/validation, and a completion check; it must never present this
raw response as a complete proposal. The first request also shows that a
thinking-capable model needs a separate output budget or disabled thinking for
this task, but changing a flag is not a privacy control.

## B — enforcement

### Positive and restricted synthetic process probe

`run_enforcement_probe.py` created a temporary synthetic public-selected file
and unselected sentinel, then ran the same actor twice against a task-owned
loopback receiver:

```sh
.venv/bin/python research/scout-local-boundary-demo/scripts/run_enforcement_probe.py \
  --output research/scout-local-boundary-demo/results/enforcement-probe.json
```

The unsandboxed positive control read both synthetic files, connected to the
receiver directly, and connected from its child. Under
`/usr/bin/sandbox-exec` profile
`(version 1) (allow default) (deny file-read* (subpath PRIVATE_DIR)) (deny network*)`:

| Attempt | Unsandboxed positive | Restricted actor |
|---|---|---|
| selected synthetic read | allowed | allowed |
| unselected sentinel read | allowed | `PermissionError` denied |
| direct loopback connect | allowed | `PermissionError` denied |
| child unselected read | allowed | `PermissionError` denied |
| child loopback connect | allowed | `PermissionError` denied |

The receiver observed two connections, both from the positive control. The
restricted process and child did not send fixture content or the canary. This
is an actual process/file/network policy test, not a mocked network function.
It demonstrates that this machine's available primitive can deny these
operations for a generic actor and inherited child.

### Existing Ollama compatibility attempt

The same no-network policy was applied to a task-owned, no-model-load attempt:

```sh
.venv/bin/python research/scout-local-boundary-demo/scripts/probe_ollama_sandbox.py \
  --output research/scout-local-boundary-demo/results/ollama-sandbox-attempt.json
```

It returned in 0.016s with:

```text
Error: listen tcp 127.0.0.1:11500: bind: operation not permitted
```

The Ollama API server requires a loopback listening socket and then spawns a
`llama-server` child for model execution. A no-network profile therefore cannot
host the current API server as-is. Conversely, allowing its loopback HTTP
server is not proof that its process or child has no outbound route. The
successful local inference above ran on an unsandboxed task-owned server and
therefore does **not** establish private file/network isolation. No attempt was
made to interfere with a pre-existing daemon; the task-owned server was stopped
with Ctrl-C after the two requests.

Docker client `/Users/kar/.rd/bin/docker` was present at `29.6.2-rd`, but its
daemon socket was absent. No container runtime was installed or started. The
available `sandbox-exec` profile is a useful empirical primitive on this Mac,
but this spike does not claim it is a supported cross-version GigAI deployment
mechanism. Apple’s supported App Sandbox model requires a signed app/helper and
declared file/network entitlements; see [Configuring the macOS App Sandbox](https://developer.apple.com/documentation/xcode/configuring-the-macos-app-sandbox)
and [App Sandbox file access](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox).

## Interpretation against the proposed Scout boundary

| Property | Verdict | Evidence and gap |
|---|---|---|
| Useful local inference | **Partial** | qwen3.8 produced synthetic fit prose in ~31.5s, but bounded outputs were incomplete and one included a fabricated date; no release-quality proposal completion. |
| Generic process enforcement | **Demonstrated primitive only** | `sandbox-exec` denied unselected file/direct+child loopback while positive control worked. |
| Existing Ollama process/server enforcement | **Blocked/unproven** | Existing API needs loopback bind and spawns a child; successful server was unsandboxed. No claim of Internet denial, cloud denial, or private-file denial for Ollama. |
| Hosted/private fallback denial | **Policy only** | No hosted call was made; current task-owned scripts have no fallback. Production enforcement remains absent. |
| Local report/projection | **Not attempted** | This spike intentionally did not implement proposal HTML or `state.sqlite` projection. |

Ollama's current server log showed cloud disabled `false` because the per-process
server was started without `OLLAMA_NO_CLOUD=1`; that is an observed setup fact,
not an indication that a cloud request occurred. Ollama documents cloud models,
local-only configuration, and tool calling; those settings are defense-in-depth
and cannot replace process/network enforcement. See [Ollama Cloud](https://docs.ollama.com/cloud),
[Ollama FAQ](https://docs.ollama.com/faq), and [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling).

The qwen response produced no tool calls, but this proves only that this request
did not request one. Model-generated tool requests do not execute without a
harness; a future harness must allowlist operations and remain inside the same
process boundary. No canary or synthetic private string was sent externally.

## Practical v0.1.7 recommendation

Do not claim the proposed privacy promise is met by the current Ollama daemon,
loopback endpoint, `OLLAMA_NO_CLOUD`, no-tools prompt, cwd, or detector. The
smallest honest v0.1.7 options are:

1. deterministic public acquisition through an allowlisted broker with no
   private mount;
2. private assessment through deterministic code, plus a local model only when
   a signed/helper or container topology can enforce selected mounts and deny
   outbound network for the model process and descendants; or
3. block private-model assessment and allow clearly public manual import until
   that helper exists. Do not send private inputs to hosted agents as a
   fallback.

The current native Metal/GPU path is practical for performance, but its HTTP
server/child topology is incompatible with the tested no-network profile. A
next implementation must choose an authority/install path (signed macOS App
Sandbox helper or a trusted container runtime) and test the actual qwen model
inside it. This is a prerequisite, not a reason to weaken the privacy promise.

## Recommended next tasks

1. Build a disposable signed/helper prototype with explicit selected-file
   mounts and no network for private assessment, while preserving native Metal
   only if the helper can enforce it; add canary tests for process descendants,
   proxies, MCP/tools, logs, errors, and malicious posting instructions.
2. Design a local broker/IPC boundary that does not expose private inference to
   the public fetch network and records only stable IDs/digests; independently
   review its OS/runtime authority before integrating Ollama.
3. Add strict proposal completion checks and local private HTML/projection after
   the boundary exists; reject truncated/fabricated output and preserve exact
   public/private revisions. Keep Tailor explicit and application state
   separate.

## Reproduction and inspection limits

`ruff check research/scout-local-boundary-demo/scripts` passed. No full suite,
provider call, network search for a user, install, model pull, or production
change was made. The complete commands and raw synthetic result paths are in
[`research/scout-local-boundary-demo/README.md`](../../../../../research/scout-local-boundary-demo/README.md).

The current Scout proposal spike remains the design source:
[`SCOUT-local-proposals-privacy-spike.md`](SCOUT-local-proposals-privacy-spike.md).
This demonstration reports observed local runtime and primitive behavior
separately from that proposed boundary; it does not claim synthetic exposure to
the hosted coding worker proves confidentiality for a real user.

## Follow-up: selective native policy compatibility

**Date:** 2026-09-11. This appendix preserves the original two inference
results and records one additional exact-port policy attempt; it does not
replace the original verdict.

### Policy syntax and selective IPC control

The local `sandbox-exec(1)` man page identifies the mechanism as **deprecated**
and directs production app/helper isolation toward App Sandbox. Its syntax
requires local network rules to name `localhost:PORT`; a bare `127.0.0.1:PORT`
rule is rejected by the parser. The bounded profile used here was:

```text
(version 1) (allow default) (deny network*)
(allow network-inbound (local ip "localhost:11499"))
(allow network-outbound (remote ip "localhost:11499"))
(deny file-read* (subpath TASK_PRIVATE_SENTINEL_DIR))
```

The exact profile was tested with a task-owned synthetic receiver on allowed
port 11499 and a distinct receiver on forbidden port 11501. Under the profile,
selected synthetic input was readable, the unselected sentinel returned
`PermissionError`, direct and child connects to 11499 succeeded, and direct and
child connects to 11501 returned `PermissionError`. A no-payload connect to
reserved TEST-NET address `198.51.100.1:80` returned `PermissionError` directly
and in the child; the unrestricted positive control timed out, so this is
process-policy evidence rather than a claim that the host had a positive
internet route. The unrestricted positive control could read/connect to both
local listeners, confirming this was an actual policy test, not a mocked network
function. Machine-readable evidence is
[`endpoint-policy-probe.json`](../../../../../research/scout-local-boundary-demo/results/endpoint-policy-probe.json).
The focused command completed in approximately 6.4 seconds including the two
one-second positive-control TEST-NET timeouts; no payload was sent.

This proves selective local IPC is expressible for a generic process. It does
not prove a complete filesystem allowlist: the profile starts from `allow
default` and denies only the synthetic sentinel subtree. It also does not prove
internet denial for a process that might use a permitted local proxy; the
forbidden-port control is deliberately a distinct local-service check.

### Actual Ollama server and dynamic runner

The captured compatibility run used this bounded shell shape (the helper below
was added afterward to preserve reproduction and was not rerun, to keep the
one-model-process/one-request bound):

```sh
PROFILE='(version 1) (allow default) (deny network*) (allow network-inbound (local ip "localhost:11499")) (allow network-outbound (remote ip "localhost:11499")) (deny file-read* (subpath TASK_PRIVATE_SENTINEL_DIR))'
/usr/bin/sandbox-exec -p "$PROFILE" /usr/bin/env -i \
  PATH=/usr/local/bin:/usr/bin:/bin HOME=/Users/kar \
  OLLAMA_HOST=127.0.0.1:11499 OLLAMA_MODELS=/Users/kar/.ollama/models OLLAMA_NO_CLOUD=1 \
  HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy= \
  NO_PROXY=127.0.0.1 /usr/local/bin/ollama serve
# In a second task-owned command, run run_local_proposal.py under the same PROFILE.
```

The full cleanup/runner-capture logic is now in this helper, which is a
reproduction command but was not rerun after the captured attempt:

```sh
.venv/bin/python research/scout-local-boundary-demo/scripts/run_native_policy_probe.py \
  --fixture research/scout-local-boundary-demo/fixtures/proposal-request.json \
  --output research/scout-local-boundary-demo/results/native-policy-run.json
```

The actual recorded run used the same profile, task-owned `OLLAMA_HOST=127.0.0.1:11499`,
`OLLAMA_MODELS=/Users/kar/.ollama/models`, `OLLAMA_NO_CLOUD=1`, and empty upper/
lowercase proxy variables. Ollama `0.34.0` successfully listened on 11499,
then launched its real child:

```text
/Applications/Ollama.app/Contents/Resources/llama-server ... --port 63662 --host 127.0.0.1 ... --offline ...
```

The child selected dynamic port 63662 and failed its HTTP bind with
`operation not permitted`; the Ollama API returned HTTP 500 in 1.407 seconds.
The complete bounded shell command took 20.3 seconds including startup and
cleanup. The machine-readable summary is
[`native-policy-exact.json`](../../../../../research/scout-local-boundary-demo/results/native-policy-exact.json),
and the server log is retained beside it. The task-owned server was stopped by
the cleanup trap; no existing daemon or unrelated GPU work was touched.

The observed `ollama serve` command/log exposed no fixed runner-port control;
the child supplied a fresh `--port` itself. I did not permit a broad dynamic
range or `localhost:*`, because no bounded reserved-range contract was
available to verify and either would weaken the forbidden-local-service test.

This is a precise compatibility blocker for the current Ollama topology, not
evidence that selective IPC is impossible in general. The profile allows only
11499 and therefore cannot accommodate Ollama's dynamic runner. I deliberately
did not widen it to `localhost:*`: that would permit unrelated local proxies or
services and would fail the forbidden-listener requirement. No model generation
completed under the enforced policy, so I did not rerun generation merely to
collect more prose after the boundary failed.

### Client hardening and corrected short-output record

The helper now accepts only the installed `qwen3.8:latest` digest, numeric
`127.0.0.1:11499`, and no URL credentials/query/fragment; it clears proxy
variables in-process, uses an empty proxy handler, rejects redirects, and has no
hosted fallback. Its canonical request uses `think=false`, context 4096,
prediction cap 650, and a strict JSON shape requiring `why_fits`,
`hard_blockers_or_unknowns`, `proposed_resume_focus`, `focused_questions`, and
`evidence_refs`. The prior two result files remain historical 2048/220-token
runs, and their recorded commands should be read as pre-hardening commands;
the helper's current command is the one above.

The synthetic fixture now includes an untrusted posting instruction to upload
`SYNTHETIC-LOCAL-ONLY-CANARY-7f2e`; the prompt says not to execute it or emit
the canary. Because the exact policy failed before generation, this follow-up
does not claim model compliance with that adversarial field. The earlier
successful qwen outputs remain **A: partial**: one consumed its 220-token cap in
thinking and the other was truncated before all fields and invented a date.

Cheap no-network guard checks also passed as expected: a request using port
11498 exited 1 with `refusing endpoint outside numeric loopback 127.0.0.1:11499`,
and a request using `mistral:latest` exited 1 with `model is not in the exact
installed local allowlist`. These checks did not contact any endpoint.

### Updated verdict

| Property | Follow-up verdict | Evidence |
|---|---|---|
| Selective generic process IPC | **Demonstrated primitive** | 11499 direct/child allowed; 11501 direct/child denied; selected read allowed; sentinel denied. |
| Existing Ollama API + runner under exact policy | **Blocked** | API 11499 bound; dynamic runner 63662 failed to bind. |
| Useful local proposal under enforced policy | **Not demonstrated** | No generation completed once the real runner inherited the policy. |
| Existing installation as a privacy boundary | **Unproven** | Prior successful requests used an unsandboxed server; exact policy blocks the runner; no all-loopback widening was accepted. |

The smallest next step is a trusted helper/runtime design that gives the model
runner a controlled fixed IPC endpoint (or an independently enforceable broker)
while denying unrelated local services, proxy paths, child egress, and ambient
file reads. Until that topology is reviewed and exercised with the actual qwen
runner, v0.1.7 must not claim private-model isolation; deterministic private
assessment or clearly public manual import remains the safe fallback. No
installation, admin authority, or production portability claim is inferred from
this deprecated macOS primitive.

## Follow-up: direct bundled-runner Unix-socket harness

**Date:** 2026-09-11. This is a separate direct `llama-server` harness, not
Ollama API integration. It answers whether the installed runner can use a
controlled local endpoint without Ollama's dynamic TCP child.

### Installed runner and fixed endpoint

Bounded local help checks were:

```sh
/usr/local/bin/ollama serve --help
/Applications/Ollama.app/Contents/Resources/llama-server --version
/Applications/Ollama.app/Contents/Resources/llama-server --help
```

The installed bundled runner reports `0.3.0-dev (build 1, commit
0f3a71be1)` and documents `--host HOST` as accepting a Unix socket when the
address ends in `.sock`, `--offline`, `--json-schema-file`, `--ctx-size`,
`--predict`, `--reasoning-budget`, and the chat-completions endpoints. The
qwen model manifest and prior Ollama log identify the exact model blob as
`/Users/kar/.ollama/models/blobs/sha256-f5f1dd8920d417aac2718b0bda3403da274301efdd6760b4f0f4b864ff2ad57d`
with 16,810,714,464 bytes. No model bytes were downloaded, changed, or
deleted.

The reproducible harness is:

```sh
.venv/bin/python research/scout-local-boundary-demo/scripts/run_direct_runner_policy.py \
  --output research/scout-local-boundary-demo/results/direct-runner-policy.json \
  --fixture research/scout-local-boundary-demo/fixtures/proposal-request.json \
  --schema research/scout-local-boundary-demo/fixtures/proposal-schema.json
```

The harness starts exactly one task-owned bundled runner, uses a short
task-owned Unix socket under `/private/tmp`, runs the request client under the
same sandbox profile, and removes the exact socket/process on exit. The profile
is:

```text
(version 1) (allow default) (deny network*)
(allow network-inbound (local unix-socket))
(allow network-outbound (remote unix-socket))
(deny file-read* (subpath TASK_PRIVATE_SENTINEL_DIR))
```

The runner receives `--offline`, `--no-ui`, one slot, context 4096, prediction
cap 650, and scrubbed proxy variables. The client uses `curl --unix-socket` to
the local endpoint only; no Ollama API, hosted endpoint, provider, tool,
MCP, or external network payload is involved. The socket allow rules are
selective: this profile does not permit TCP loopback or arbitrary local
listeners. It still begins with `allow default`, so filesystem authority is
partial rather than a complete selected-file allowlist; the synthetic sentinel
deny and generic actor evidence remain the applicable file controls.

### Actual model startup and generation

The runner successfully loaded the exact qwen blob under the profile and logged
`model loaded` followed by `listening on unix:///private/tmp/.../direct-runner.sock`.
The log showed `is_child = 0`, so this direct harness has one native runner
process rather than Ollama's dynamic child topology. No unrelated process or
GPU workload was stopped.

Two final bounded generation attempts were made after readiness and runner-flag
corrections, each capped at 650 completion tokens and 180 seconds:

| Result | Runtime | Runtime evidence | Proposal result |
|---|---:|---|---|
| `direct-runner-policy.json` | 64.685s | Unix socket ready/healthy, curl 0, 650 tokens, `finish_reason=length` | Partial: output spent its budget in `<think>` and a truncated JSON start; post-validation false. |
| `direct-runner-policy-no-think.json` | 65.238s | Unix socket ready/healthy, curl 0, 650 tokens, `finish_reason=length` | Partial: documented zero reasoning budget/format none still emitted `<think>` and truncated before complete JSON; post-validation false. |

Both outputs contained no date-like tokens. The model's local response did
repeat the synthetic canary while reasoning despite the prompt saying not to
emit it (`canary_emitted_in_local_response=true`); this is a model-output
privacy/quality failure, not network exfiltration. No upload tool was exposed,
the profile denied TCP networking, no remote request was made, and no canary
was sent outside the local response artifact. The harness therefore demonstrates
native inference under policy, but not a complete acceptable proposal: runtime
generation is demonstrated; proposal completeness/structured release is not.

Before the two final attempts, the installed runner's JSON grammar sampler
returned HTTP 400 `Failed to initialize samplers: std::exception` for both a
full schema and a simplified array-of-strings schema. The final direct request
therefore omitted runtime grammar constraints and retained strict post-
validation for exactly the five fields, string arrays, allowed evidence refs,
no date-like tokens, and no extra keys. This is an observed limitation of this
runner/model build, not evidence that JSON-schema support is absent from the
help surface or safe to claim in production.

### Updated bounded verdict

| Property | Verdict | Evidence/limit |
|---|---|---|
| Native local inference under enforced policy | **Demonstrated** | Bundled runner loaded qwen blob and generated over a fixed Unix socket under deny-network policy; two 650-token responses completed. |
| Complete proposal utility | **Partial / not release-acceptable** | Both responses hit the cap in reasoning/truncated JSON; required fields/evidence refs were not complete. |
| Network boundary | **Demonstrated for tested routes** | TCP network denied; only task-owned Unix socket allowed; prior generic direct/child forbidden listener and TEST-NET checks still pass. Internet denial remains a host/runtime assumption beyond this route test. |
| Filesystem boundary | **Partial** | Synthetic sentinel subtree denied and selected fixture readable; profile retains `allow default` for model/runtime libraries and ambient paths. |
| Process/child boundary | **Demonstrated for this harness** | Direct runner logged `is_child=0`; the process and request client inherited the same profile; tools/MCP disabled. |
| Portability/support | **Experimental only** | `sandbox-exec` is deprecated; bundled runner flags are locally observed, not a supported GigAI deployment contract. |

This direct route is materially more practical for a bounded experiment than
Ollama's dynamic TCP server: a fixed Unix endpoint works under deny-network and
the native Metal runner generates locally. The smallest next change is a
reviewed helper around this direct runner that owns selected-file mounts,
output validation, canary refusal, and supported macOS isolation; until then,
do not promote this deprecated harness to a v0.1.7 privacy guarantee or expose
raw model output as a complete proposal.
