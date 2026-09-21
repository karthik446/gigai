# SCOUT R6 final corrections — bounded synthetic evidence

Date: 2026-09-20 (America/Denver)  
Status: bounded synthetic correction implementation; not installed-wheel, live-provider, publication, or R7 acceptance.

## Scope

This pass implements the three owned R6 findings from the correction review:

1. an interrupted comparison now seals an immutable intent before execution,
   records immutable per-case result checkpoints, exposes status before final
   publication, and resumes the same comparison and Run IDs in a later session;
2. comparison readers use the approved Graph/pack authority commit for approved
   inputs and the comparison artifact's publication commit for Run/results,
   preventing later journal-head drift without pretending approved resources were
   first published in the approval commit; and
3. real model-execution records must match configured adapter, endpoint, and
   model, with local Ollama requiring an observed matching model digest. A
   synthetic injected transport with no transport identity is recorded as
   `status: unknown` with an explicit `synthetic_injection` reason; a supplied
   mismatched identity is a structured terminal failure and cannot be graded as
   successful.

R5 feedback subtype and private-transfer nested-ancestor findings remain owned
by the R5 lanes. Existing R4 acceptance and unrelated dirty work were preserved.

An OS-level per-Run lock bounds concurrent resume attempts: a second active
resume refuses before invoking a provider, while a crashed process releases the
lock through the operating system and leaves unresolved case state explicit.

## Focused verification

Commands run in this checkout:

```text
rtk .venv/bin/pytest -q tests/test_runtime_comparison.py
9 passed in 29.90s

rtk .venv/bin/python tools/verify_installed_schemas.py
verified 82 installed GigAI schemas

rtk ruff check src/gigai/runtime_comparison.py src/gigai/cli.py \
  src/gigai/journal.py tests/test_runtime_comparison.py \
  tools/verify_installed_schemas.py
All checks passed!

rtk git diff --check
passed
```

The focused tests cover positive completion/reload, retry preservation,
authority-bound setup failure, grader negatives, fresh-session-style resume
after a saved case and interrupted case, same Run IDs, no rerun of the saved
case, later nested-result replacement read from the original publication head,
and wrong observed adapter identity as a preserved structured failure.

No broad suite was repeated. The prior combined review measurement of 19 passed
and 81 schemas was treated as input evidence; this pass added nine focused R6
tests and one intent schema, bringing the installed schema inventory to 82.

## Limits

The resume test uses a new invocation/session call with injected transports and
disposable workpads; it does not claim a process-kill or live socket proof.
Unknown identity is admitted only through the explicitly injected test seam.
No live Ollama/Codex/Luna provider, network, model download, private user data,
installed wheel, activation, publication approval, commit, reset, or R7
acceptance was performed.
