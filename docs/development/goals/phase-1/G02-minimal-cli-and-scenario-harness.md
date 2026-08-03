# G02 — Minimal CLI and Installed Scenario Harness

- Status: Approved; blocked by G00
- Depends on: G00
- Unblocks: G03, G07

## Outcome

Introduce the smallest real `gigai` console entry point and create the
black-box verification harness that invokes the installed distribution against
isolated target, workpad, and home roots without importing CLI internals.

## In scope

- Register the `gigai` console entry point in `pyproject.toml`.
- Expose only truthful `--help` and `--version` behavior; advertise no command
  whose product behavior does not exist.
- Resolve `--version` from installed package metadata rather than a second
  hard-coded version source.
- Move Click from the test extra into `project.dependencies` in the same change
  that introduces the console entry point.
- Build and install the package under test into an isolated environment.
- Run CLI scenarios through the installed console entry point and process
  boundary.
- Allocate independent temporary home, target, workpad, and fixture roots.
- Capture structured argv, environment, stdout, stderr, exit status, filesystem
  manifests, and process timing needed by later goals.
- Supply deterministic recording substitutes for editors, adapters, and other
  offline subprocess boundaries.
- Support Python and non-Python fixture repositories.
- Fail closed on unexpected target or network effects.

## Out of scope

- Implementing setup, initialization, validators, journals, or creation.
- Adding planned command names, command stubs, or success paths for behavior
  owned by later goals.
- Testing product behavior by importing Click command objects directly.
- Requiring real credentials, network access, or a native IDE for the default
  suite.
- Treating human presentation text as a machine identity contract.

## Acceptance criteria

1. Installing the wheel without test extras creates an executable `gigai`
   console script with Click available as a runtime dependency.
2. `gigai --help` and `gigai --version` succeed through the installed process
   boundary and describe only behavior implemented by G02.
3. No other command or operational success path is exposed.
4. A test scenario invokes the installed distribution rather than the source
   checkout’s internal command object.
5. Every scenario receives isolated, explicit roots and leaves the developer’s
   real home and configured workpads untouched.
6. The harness can prove exact before/after manifests for target and workpad
   trees, including file content and relevant Git state.
7. Unexpected network access and undeclared writes fail the scenario.
8. Recording editor tests assert structured argv; no shell-string construction
   is accepted.
9. Equivalent fixture mechanics work for Python and non-Python targets.
10. Failure output retains enough artifacts to reproduce the scenario without
   exposing workstation paths or credentials.

## Verification and evidence

- Wheel metadata and installed-command checks for the console script, runtime
  Click dependency, `--help`, and `--version`.
- One installed-command smoke fixture proving process isolation.
- One Python and one non-Python target-manifest fixture.
- Negative tests for real-home access, unexpected writes, network attempts, and
  malformed subprocess argv.
- Wheel-install log, scenario artifacts, and completion audit.

## Stop boundary

Stop with a reusable observation harness and a real console entry point limited
to `--help` and `--version`. Do not expose planned command names, implement
later command behavior, or hide product behavior inside fixtures or test
doubles.
