# SCOUT R5/R6 correction acceptance review

Date: 2026-09-20 (America/Denver)  
Reviewer: Luna-high, independent acceptance review  
Status: bounded correction acceptance with release blockers remaining; not installed-wheel, live-provider, publication, or R7 acceptance.

## Scope and evidence boundary

I read the original `SCOUT-release-execution-graph.md`, `RUNTIME-01`,
`SCOUT-R5-contract.md`, `SCOUT-R6-contract.md`, the prior consolidated review,
the 2026-09-20 correction wave, and all three correction reports. I inspected
the current implementation and tests rather than accepting author reports as
proof. All probes used disposable synthetic workpads/journals; no private
user data, provider/model/network call, activation, publication, commit,
reset, or broad suite was used.

## Verification performed

The one requested combined verification was run once:

```text
rtk proxy /usr/bin/time -p .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py \
  tests/test_scout_r5_transfer_corrections.py \
  tests/test_runtime_comparison.py
```

Result: **19 passed, 1 warning in 49.96s**; `/usr/bin/time`: `real 50.10`,
`user 26.05`, `sys 21.36`. The warning is ZipFile's expected duplicate-name
warning from the duplicate-member negative fixture; the test itself passes.

Additional bounded checks:

```text
rtk proxy .venv/bin/python tools/verify_installed_schemas.py
verified 81 installed GigAI schemas

rtk proxy ruff check src/gigai/scout_interview_records.py src/gigai/run.py \
  src/gigai/private_transfer.py src/gigai/private_transfer_cli.py \
  src/gigai/portability.py src/gigai/scout_template.py \
  src/gigai/scout_materialization.py src/gigai/runtime_comparison.py \
  src/gigai/cli.py tests/test_scout_r5_interview_transfer.py \
  tests/test_scout_r5_transfer_corrections.py tests/test_runtime_comparison.py \
  tools/verify_installed_schemas.py
All checks passed!
```

## Disposition of corrected findings

### R5 interview

The following prior findings are demonstrated corrected in the current bounded
slice:

* Selected native experience references are resolved from committed bytes and
  exact digest/size identity in `scout_interview_records.py:233-284`; a foreign
  family/path spoof is refused without a journal-head change.
* Selection, source hydration, content claim checks, parent CAS, schema
  validation, operation-key replay, and publication run inside `_publish`'s
  one writer snapshot (`scout_interview_records.py:466-538`).
* Factual stories and claim/metric/skill entries must cite selected experience
  evidence (`scout_interview_records.py:288-354`); hypothetical entries must
  carry `label: hypothetical` and feedback is not admitted as factual evidence.
* The central preparation schema/inventory is present and the combined suite
  exercises immutable feedback/revision replay and stale-parent refusal.
* The supported main CLI path now invokes `scout-interview prepare --run
  --confirm`; the test constructs an approved `prepare-interview` Graph, then
  verifies a succeeded Run and `runs/<run>/scout-interview/result.json`.

The public Run proof is truthful but narrower than a model-backed claim:
`run.py:1102-1136` requires a selected approved Graph and a deterministic/local
capability, while `run.py:1153-1188` passes caller-supplied selection/content
to the journal-backed record service. This proves supplied-content processing
through an approved Graph and completed Run/evidence; it does not prove local
model generation, external agent execution, or a provider path. Positive
opportunity-posting, research, discovery, and prior-feedback source-family
flows are not covered by the combined tests, so those remain evidence gaps,
not accepted whole-R5 proof.

### R5 transfer and continuation

The combined transfer correction tests and source inspection establish:

* duplicate/colliding members, traversal, link markers, member/total/ratio
  limits, manifest mismatch, absolute operational links, secret/config paths,
  and existing-destination refusal are guarded before or during extraction
  (`private_transfer.py:394-520`, `536-586`);
* definition export and private history transfer remain separate, and private
  restore rejects active machine authority paths (`private_transfer.py:430-458`);
* journaled history is captured with a pinned head, and
  `bind_restored_private` records exact history in a fresh local journal while
  keeping active selection/consent absent (`private_transfer.py:603-732`);
* native-reader continuation, explicit fresh selection refusal, source
  inventory read through one journal head, and customization-safe adopt/defer
  are reported by the lane's 42 focused transfer checks plus its
  source-inventory helper check; the combined run above independently covers
  the correction test files included in this checkout.

Imported historical prose/bytes are retained; the implementation does not
delete old approval text merely because it is historical. The demonstrated
second-home slice is inert history plus explicit local binding. Fresh approval
and fresh execution after binding remain outside this review.

### R6 comparison

The current bounded slice demonstrates the important corrections:

* `_load_authenticated_pack` requires the selected approved Graph Set's
  journaled evaluation contract and pack bytes, owner Gig, selected graph,
  and approved Goal (`runtime_comparison.py:347-417`); a handcrafted/global
  pack is no longer accepted.
* The grader rejects empty evidence, wrong criterion kind, duplicate evidence,
  duplicate criteria, unsupported claims, and malformed output. The combined
  suite covers the requested negative cases.
* Each setup receives a distinct standard Run manifest, immutable per-case
  input/result references, preserved errors, retry count, and configured plus
  observed identity fields (`runtime_comparison.py:487-581`). The focused
  injected comparison produces two durable attempts and the reader rejects a
  forged nested result digest.
* Main CLI comparison start/run/show/status and R5 interview/transfer groups
  are registered; the schema verifier reports 81 installed resources.

This is injected-transport synthetic evidence. It is not proof of Ollama or
Codex readiness, installed entry points, observed live model identity, socket
behavior, or provider execution.

## Remaining implementation blockers

These are current code findings, not speculative R7 concerns.

1. **P1 — Genuine interrupted comparison resume is not implemented.**
   `_run_attempt` catches an in-call `KeyboardInterrupt`, persists an
   interrupted attempt, and returns (`runtime_comparison.py:526-581`), but
   `run_comparison` always allocates fresh attempts and the only status API
   requires an already-published comparison (`runtime_comparison.py:218-306`,
   `326-334`). There is no resume operation that reuses the interrupted Run,
   skips terminal cases, and publishes the same comparison. The existing
   `retry_failed` test is an in-call retry, not crash/restart resume.

   Minimal fix/probe: persist a comparison intent before attempt execution and
   add `comparison resume` that authenticates the pinned Graph Set/pack/head,
   reuses each existing Run ID/checkpoint, and finalizes without rerunning
   terminal cases. Add an injected probe that interrupts after one case,
   starts a fresh process, resumes, and asserts the same Run ID plus preserved
   first-case bytes/errors.

2. **P1 — Comparison readers do not use the recorded single authority head.**
   The published payload records `authority.journal_commit`, but
   `_authenticate_comparison` reads graph-set, graph, pack, Run, and case
   references without passing that head to `read_committed_artifact`
   (`runtime_comparison.py:431-484`). A later journal state can therefore be
   consulted while authenticating an older comparison. This is narrower than
   the already-correct write-side pack/Goal pinning.

   Minimal fix/probe: require a canonical commit in the outer authority,
   pass `head=authority["journal_commit"]` to every nested read, and verify
   the returned commit is that same head. Add a later-head fixture that tries
   to alter a nested reference and assert `show` reads the original pinned
   bytes or refuses.

3. **P1 — Observed adapter/model identity is recorded but not checked.**
   Setup validation checks configured adapter/digest/readiness
   (`runtime_comparison.py:501-505`), then stores whatever invocation record
   supplies as `observed_identity` (`runtime_comparison.py:566-580`) without
   requiring it or comparing adapter/model/digest to configured identity. A
   successful invocation with a mismatched observed identity could therefore
   be graded and published. Synthetic injected transports may explicitly
   remain unknown, but the real adapter contract must reject a mismatch.

   Minimal fix/probe: require destination-specific observed identity for real
   adapters, compare it to configured target/model/digest, preserve the
   mismatch as a structured terminal error, and add a negative injected
   execution record with a wrong adapter/model identity.

4. **P1 — Interview feedback/preparation source kinds are not fully
   distinguished.** The source ref branch accepts `kind: feedback` whenever a
   `scout_interview` revision has a list-valued `feedback` field
   (`scout_interview_records.py:269-284`). Every preparation revision has that
   field, including the initial empty list, so a preparation revision can be
   relabelled as feedback and accepted. A disposable helper probe returned
   `feedback` for an initial preparation-shaped revision. This does not turn
   feedback into factual story evidence because stories still require
   `experience`, but it violates exact selected-source family/kind
   authentication.

   Minimal fix/probe: persist an explicit immutable revision subtype (or a
   narrowly defined nonempty-feedback predicate with a regression test), then
   reject an initial preparation revision supplied as feedback and accept a
   real feedback revision.

5. **P2 — Nested destination ancestor publication remains a path TOCTOU.**
   `_extract_new` checks each parent directory and then opens the target by
   pathname (`private_transfer.py:553-570`); `O_NOFOLLOW` protects the final
   file, not a nested parent swapped to a symlink after the check. The current
   tests cover static symlink ancestors and concurrent existing-destination
   refusal, not an adversarial nested ancestor swap.

   Minimal fix/probe: publish through an O_DIRECTORY/O_NOFOLLOW directory-fd
   chain (or an equivalent locked parent operation), then add a disposable
   symlink-swap extraction probe. Keep final no-replace semantics.

## Bounded acceptance and release-next recommendation

Accept the correction wave as **bounded synthetic implementation evidence**:
R5 native interview/public Run/revision and transfer/history/second-home
helpers are substantially corrected; R6 authority-bound pack, grader
negatives, independent injected attempts, retry accounting, and nested result
reader checks are demonstrated. Do not call this release-ready: the four P1
items above include missing implementation/authority gates, and green focused
tests do not establish them.

Release-next should be one small correction pass for pinned-head readers,
observed-identity enforcement, exact feedback subtype, and genuine restart
resume, followed by focused negative/positive probes. Then R7 may separately
cover exact-wheel installation, supported installed entry points, live/consented
Qwen/Ollama versus Luna/Codex runs, hardware/setup identity, and the final
full matrix. Those R7 activities are deferred evidence; they do not excuse
the missing resume or authority checks now.
