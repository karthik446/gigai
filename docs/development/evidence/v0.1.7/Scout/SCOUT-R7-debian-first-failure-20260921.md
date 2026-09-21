# SCOUT-R7 Debian first-failure recovery — 2026-09-21

## Result

One bounded diagnostic was run against the existing immutable baseline image `gigai-debian-offline-r7-final-kkbodn` (`sha256:a155e8abfc8cfa7b801f082fe82d6599af523b5ddec223ab3b522744c86e6d68`). It preserved the prior no-network, non-root, read-only, tmpfs-only isolation, ran the Debian preflight followed by unbuffered `pytest -x --tb=short`, and completed with exit 1 in 512.39 seconds, before the 600-second wall bound. No source checkout was mounted, no image was rebuilt, and no retry or provider/model call was made.

The first named failure is an offline model-runtime setup prerequisite, not a failure in installed Scout/core/privacy behavior:

```text
tests/test_g28_setup_create.py::test_initialized_non_git_target_resolves_implicitly_from_its_directory

E   AssertionError: Error: no usable model runtime is configured; install or configure Codex, Claude, or an API target, then rerun `gigai setup`
E   assert 1 == 0
E    +  where 1 = <Result SystemExit(1)>.exit_code
```

The completed first-failure summary was:

```text
1 failed, 404 passed, 1 skipped in 512.39s (0:08:32)
```

This is evidence about the pre-scratch-correction baseline image only. It does not make a claim about the corrected integration candidate or Debian release acceptance.

## Provenance and isolation

- Image tag: `gigai-debian-offline-r7-final-kkbodn`
- Image digest: `sha256:a155e8abfc8cfa7b801f082fe82d6599af523b5ddec223ab3b522744c86e6d68`
- Candidate wheel and sdist were not rebuilt or changed.
- Existing image command was overridden only with `/bin/sh -c 'python tools/verify_debian_offline.py && PYTHONUNBUFFERED=1 python -m pytest -x --tb=short'`.
- Docker run used `--network none`, `--read-only`, `--user 10001:10001`, and only the established `/tmp`, `/audit/home`, `/audit/target`, and `/audit/workpad` tmpfs mounts.
- No main source or checkout bind mount was used.
- The preflight emitted `verified Debian offline non-root, network-isolated audit mounts`.

The immutable image and isolation command are recorded in the disposable provenance from the earlier Debian verification at `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-run.provenance`; the recovery command is retained at `/private/tmp/gigai-r7-debian-first-failure-XfCTW5/run-command.txt`.

## First failure classification

The failing test is in `tests/test_g28_setup_create.py:84`. It creates an empty temporary home/workpad and invokes non-interactive `gigai setup` without a configured model target; the captured CLI error explicitly requires a configured Codex, Claude, or API runtime. Because this recovery intentionally uses no providers, models, credentials, global configuration, or private inputs, the observed failure is classified as an environment/fixture prerequisite for this optional G28 model-runtime setup test.

The traceback does not implicate installed Scout scenarios, core/privacy verifiers, or the Debian isolation preflight. It also is not evidence of a platform-specific Debian product defect; it is the expected limitation of running this model-runtime setup case in a provider-free offline image. No correction was made and no additional test was run.

## Raw completion evidence

All files below are disposable, uniquely named artifacts under `/private/tmp/gigai-r7-debian-first-failure-XfCTW5`:

- `docker.stdout.log` — preflight, pytest collection/progress, first traceback, and final summary
- `docker.stderr.log` — empty
- `completion.status` — actual `exit=1`, image digest, start/end timestamps, and `timeout_seconds=600`
- `completion.marker` — deterministic wrapper completion notification
- `run-command.txt` — exact diagnostic invocation
- `wrapper.stdout.log` and `wrapper.stderr.log` — wrapper streams (empty)

The diagnostic finished normally; it was not terminated on the progress `F` marker and did not time out. No full-suite rerun, source fix, cleanup, release command, or cross-platform claim follows from this evidence.
