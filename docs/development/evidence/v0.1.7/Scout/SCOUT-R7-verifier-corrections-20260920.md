# SCOUT R7 verifier corrections — v0.1.7

Date: 2026-09-20 (America/Denver)  
Status: **corrections applied and bounded installed checks pass; R7 is not
accepted because the required full source matrix is not green and release
gates remain open.**

This report records the narrow release-check corrections made after
`SCOUT-R7-candidate-verification-20260920.md`. No `src/` runtime, domain, or
schema implementation was changed. No provider/model call, real user
`.gigai` data, activation, publication, commit, reset, Docker daemon start, or
model download was performed.

## Provenance and recovery boundary

The candidate wheel used for every installed check is unchanged:

```text
/private/tmp/gigai-r7-candidate-20260920/dist/gigai-0.1.7-py3-none-any.whl
sha256=bb7dd5873f7197f88006fb51351cafccf926c88362001dbda4b4d4754dc54ef4
environment=/private/tmp/gigai-r7-candidate-20260920/final-venv
```

The earlier full-source invocation had no durable stdout, stderr, or exit
code. Process-list inspection is unavailable in this sandbox (`pgrep` cannot
access the process service), so it was not treated as proof of completion or
success. A single captured full source matrix was subsequently run only after
that unresolved invocation; its evidence is listed below.

## Corrections made

The changes are limited to installed verifier scripts, installed/scenario
tests, and the scenario fixture harness:

- G03 no longer requires the removed `adapter.offline` doctor identifier; it
  retains the current doctor checks and their negative behavior.
- G04/G05/G22 fixtures now supply the accepted username and explicit synthetic
  remote endpoint/model-target configuration. G04 preserves Git/non-Git,
  package, exclude, and registry assertions; G05 asserts registry v3, the
  three default instances, and the explicit workpad row.
- G09 supplies an explicit target and validates the current structured
  `gigs --json` response rather than treating it as a legacy list.
- G11 configures a synthetic model target without inventing a usable runtime
  or making a provider call.
- G13/G14 pass the now-required direct `--confirm` consent argument.
- G20/G21/G23/G26/G27/G28 use a literal frozen 64-name legacy schema identity
  fixture. They do not replace identity checks with discovered-count checks;
  the separate installed resource verifier continues to validate the exact
  current 82 packaged schemas.
- G28 retains proposal-only behavior but uses the accepted explicit synthetic
  invocation input; the obsolete unexposed `eval` command assertion was
  removed. It still asserts `proposed` and no created authority.
- The CLI surface assertion and G04 scenario fixtures now match the accepted
  command surface, username gate, JSON error channel, and generated private /
  default-workpad paths. The harness permits only those contract-generated
  paths and the installed init interpreter subprocess; it does not weaken
  unrelated file or subprocess restrictions.

Changed paths owned by this correction pass:

```text
tools/installed_schema_expectations.py
tools/verify_installed_g03.py
tools/verify_installed_g04.py
tools/verify_installed_g05.py
tools/verify_installed_g09.py
tools/verify_installed_g11.py
tools/verify_installed_g13.py
tools/verify_installed_g14.py
tools/verify_installed_g20.py
tools/verify_installed_g21.py
tools/verify_installed_g23.py
tools/verify_installed_g26.py
tools/verify_installed_g27.py
tools/verify_installed_g28.py
tools/verify_installed_schemas.py
tests/scenarios/harness.py
tests/test_cli_and_scenario_harness.py
tests/test_g04_installed_scenarios.py
```

The worktree contained unrelated pre-existing changes, including source and
schema changes; those were preserved and not used as a reason to alter
production behavior.

## Verification evidence

All installed commands below used the dependency-complete `final-venv`,
`python -I`, and the exact wheel outside checkout import shadowing. The final
affected installed scenario lane was:

```text
GIGAI_TEST_EXECUTABLE=/private/tmp/gigai-r7-candidate-20260920/final-venv/bin/gigai \
  /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest \
  tests/test_cli_and_scenario_harness.py tests/test_g04_installed_scenarios.py \
  --junitxml=/private/tmp/gigai-r7-candidate-20260920/logs/corrected6-installed-scenarios.xml
```

Result: **36 passed in 33.66s**, exit 0. Durable stdout/status/JUnit are:

- `/private/tmp/gigai-r7-candidate-20260920/logs/corrected6-installed-scenarios.log`
- `/private/tmp/gigai-r7-candidate-20260920/logs/corrected6-installed-scenarios.status`
- `/private/tmp/gigai-r7-candidate-20260920/logs/corrected6-installed-scenarios.xml`

The affected standalone verifier logs and status files are retained in the
same directory under `corrected*-installed-*`; the final G04/G28 reruns are
`corrected7-installed-g04.log` and `corrected7-installed-g28.log`, and the
authoritative packaged-schema check reports `verified 82 installed GigAI
schemas`. The wheel hash was rechecked after the edits and remained identical.

## Required full source matrix

The full matrix was run exactly once with durable stdout, stderr, JUnit, and
exit capture:

```text
GIGAI_G30_UAT=0 \
  /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest \
  --junitxml=/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.junit.xml
```

Evidence:

- stdout: `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.stdout.log`
- stderr: `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.stderr.log`
- status: `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.status`
- JUnit: `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.junit.xml`

The status records exit code 1, 1551 collected, and:

```text
53 failed, 1482 passed, 1 skipped, 1 warning, 15 errors in 4123.03s (1:08:43)
```

The 15 errors are the contract-spike schema setup group reporting an
additional-schema resource-set mismatch. The remaining failures are grouped
in the captured output as canonical ownership, G08 lifecycle ownership, G17
inventory, G21 occurrence reconciliation, G22 HTTP/proposal interview, G26
review actions, G41 package boundary, G42 catalog adoption, G43 provider
review/run-status, JSL closeout regressions, Scout06 source-contract
inventory, setup-browser loopback binding, and the subprocess `shell=False`
diagnostic. These were not silently attributed to stale assumptions: they
were not fixed because they are outside this worker's ownership and/or are
environment/provider/production contract gates. In particular, the
`scout_materialization.py` subprocess diagnostic is a concrete source-contract
failure requiring a separately authorized production correction.

G30 remained disabled with `GIGAI_G30_UAT=0`; no provider or model was called.

## Release conclusion and remaining gates

The original verifier/scenario failures that were confirmed obsolete against
the accepted initialization, consent, runtime, target, registry, CLI, and
schema contracts are corrected while retaining their original positive and
negative coverage. The corrected installed lane is green, but this does not
make the product or release green: the full source matrix is non-green and
R7 must remain unaccepted.

Still open are the captured full-source failures, Debian/offline Docker
verification (Docker daemon unavailable and deliberately not started),
independent R5/R6 review beyond synthetic focused evidence, live Ollama/Luna
or other provider comparison, authorized publication, and personal install /
UAT. Linux, Windows, live provider, and publication behavior are not
established by this macOS run. No claim of release approval, active selection,
default promotion, provider execution, or whole-feature acceptance is made.

Orca IPC was unavailable while this worker was completing (`orca` reported
that Orca was not running), so the required coordinator completion signal may
not have been deliverable; this report is the durable handoff.
