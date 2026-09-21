# Scout development model rotation

Operator decision, 2026-09-10. This is the current development workflow, not
implementation of v0.1.8 execution-mode flags.

- Luna is the default implementation worker and bounded quick reviewer. Use
  independent context for review; implementation completion is not acceptance.
- Use stronger models only for high-risk authority/privacy/storage changes,
  unresolved findings, or a concrete failure of the cheaper route. Do not
  automatically run every change through an expensive reviewer.
- Bring actual local Ollama Qwen3.8 into bounded synthetic tasks and low-risk
  review assistance. Keep raw model outputs and predefined acceptance checks;
  distinguish Qwen output from Luna's harness work and assessment. No assumed
  parity, hosted fallback, downloads, or private-input forwarding.
- Dispatch, provide a rough ETA, and yield. Resume on notification or user
  request; do not keep an LLM waiting or duplicate workers' suites.

## First assignments

- `task_159d9e1126dc`: Luna, real SCOUT-07 discovery Run integration. Exclusive
  core/compiler/source-inventory ownership described in the task. Estimate
  30–45 minutes after successful dispatch; independent acceptance remains open.
- `task_2ca9f56b6b21`: Luna runs and assesses a three-case actual local Qwen
  review exercise. Output limited to `research/local_model_eval/qwen38_rotation/`
  and `QWEN-rotation-trial.md`; estimate 10–20 minutes after successful dispatch.
  Initial launch blocked by a Codex update prompt; no Qwen call claimed yet.

Fresh Luna starts encountered `codex-update-prompt`. Do not retry startup in a
loop or silently switch to a larger model. Reuse an available settled Luna
session where safe; otherwise report the blocked task until the prompt is
resolved. Earlier SCOUT-08 tailoring completion was recovered from its final
handoff after worker IPC failed; it remains unreviewed candidate implementation.

## 2026-09-10 19:34 UTC — next dispatch

SCOUT-07 integration finished; coordinator read the report, final transcript
and flow test. Worker evidence: 57 focused tests in 18.49s, plus five research
regressions reported in 15.33s. Its failed completion delivery was explicitly
recovered; no independent acceptance or coordinator suite rerun is implied.

The available settled Luna session now owns the Qwen trial:
`task_2ca9f56b6b21` / `ctx_f9b2d235d06e`, confirmed `ready/input_accepted`.
Actual Qwen invocation/results still await that worker's evidence. ETA10–20min.

Independent Luna integration review `task_63166d3382df` is ready but not
dispatched: the separate fresh Luna sessions still show Codex update prompts.
Do not use the implementation session to claim independent review, launch more
prompt-blocked sessions, or substitute a stronger model without reason. Once a
fresh Luna session is usable, dispatch the prepared bounded review (15–25min).
