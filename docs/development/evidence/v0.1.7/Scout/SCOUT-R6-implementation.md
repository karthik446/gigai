# R6 runtime comparison implementation

Status: Luna bounded implementation, 2026-09-12. This evidence records source
and synthetic/injected-transport scope only; it is not a release claim.

Implemented:

- `src/gigai/runtime_comparison.py`: packaged-pack loading, strict pack and
  grader validation, known-good/known-bad grader proof, source-grounded
  criterion grading, explicit Qwen/Ollama and Luna/Codex eligibility, durable
  independent Run attempts, preserved raw outputs/errors/timing/retries/usage,
  journal-authenticated comparison publication/reload, and Markdown/JSON
  rendering.
- `src/gigai/data/runtime-comparison-pack-v1.json`: three frozen synthetic
  cases, including a held-out finalized-vs-applied state case.
- `src/gigai/cli.py`: `comparison start`, `comparison run`, and `comparison
  show` with direct confirmation and explicit target selectors.
- Three additive schemas, validator registration, SHA256SUMS entries, and
  installed package-data inclusion.

Expected synthetic verification commands (run by the coordinator after the
combined wave):

```sh
PYTHONPATH=src python -c 'from gigai.runtime_comparison import load_evaluation_pack,validate_grader; p=load_evaluation_pack(); assert validate_grader(p)["status"] == "pass"'
.venv/bin/pytest -q tests/test_runtime_comparison.py
```

Observed in this worker checkout: `3 passed` for the focused R6 tests,
`10 passed` for the focused R6 plus G21/G28 regression set, Ruff passed for all
changed Python files, `git diff --check` passed, and the installed schema
verifier reported `verified 78 installed GigAI schemas`. No whole-repository
suite was rerun in this worker lane.

No live Qwen, Ollama, Luna, Codex, hosted provider, model, network, download,
daemon, private record, or application transition was used in this wave.

## R7 exact live commands and pack

After separate installed-artifact authorization and live-provider consent,
configure two explicit targets (names are examples and must match the actual
installed config), then run:

```sh
gigai comparison start --gig GIG_ID --local-target qwen-local --luna-target codex-luna --confirm --json
gigai comparison show COMPARISON_ID --gig GIG_ID --json
```

The live setup must first pass the existing local readiness check for the
numeric-loopback Ollama endpoint and pinned digest, and Codex readiness through
the existing CLI adapter. The exact wheel/install/runtime evidence and actual
target names remain R7 work; these commands do not authorize live execution by
this R6 implementation wave.
