# SCOUT-R7 Debian verification — 2026-09-21

## Result

This is a bounded, baseline-only Debian verification of the pre-scratch-correction candidate. The corrected disposable Docker tag was lowercase (`gigai-debian-offline-r7-final-kkbodn`), the image build completed, and the existing Debian offline workflow started in an immutable, copied context. The workflow passed its Debian/non-root/network-isolated preflight, then emitted pytest failure markers and was stopped at 64% under the instruction to stop on a real product failure; this report does **not** claim Debian test or release acceptance.

The run did not emit pytest's final summary. The wrapper status file was not written because the process group was terminated after the observed failures, so no numeric Docker/test exit code is claimed. No retry, source correction, macOS matrix rerun, installed verifier/scenario rerun, model/provider call, daemon restart, cleanup, publication, tag, or commit was performed.

## Candidate and source provenance

- Repository: `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7`
- Branch: `karthik446/gigai-v0.1.7`
- Source HEAD recorded before staging: `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`
- Candidate wheel: `/private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl`
- Wheel size/SHA256: `760539` bytes / `0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7`
- Candidate sdist: `/private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7.tar.gz`
- Sdist size/SHA256: `645787` bytes / `11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5`
- Build-input record: `/private/tmp/gigai-r7-debian-verification-kkbODn/build-inputs.stdout.log`

The candidate hashes matched the requested frozen values before the Docker build. The staged context was copied from the checkout and was not a source fix or candidate rebuild.

## Docker context and image

The disposable context was `/private/tmp/gigai-r7-debian-verification-kkbODn/context`. Its safety manifest records 473 files and 5,416,600 bytes, no banned path hits, no credential-pattern hits, no symlinks, and `safe_for_docker_context: true`: `/private/tmp/gigai-r7-debian-verification-kkbODn/context-safety.json`. The context excluded `.git`, `.gigai`, credentials/environment files, shared virtual environments, generated caches, and unrelated data; Docker build/run used no checkout or source bind mount.

The corrected existing workflow Dockerfile was `containers/debian-offline/Dockerfile`. The single build used the lowercase tag and a 1,500-second bound; it completed with exit 0 in approximately 30 seconds. The resulting image digest was `sha256:a155e8abfc8cfa7b801f082fe82d6599af523b5ddec223ab3b522744c86e6d68`.

Raw build provenance and logs:

- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-build.provenance`
- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-build.stdout.log`
- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-build.stderr.log`
- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-image-inspect.stdout.log`

## Offline workflow invocation and observations

The existing image command was run once, with a 4,500-second bound:

```text
docker run --rm --network none --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
  --tmpfs /audit/home:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
  --tmpfs /audit/target:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
  --tmpfs /audit/workpad:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
  --env HOME=/audit/home \
  --env GIGAI_AUDIT_HOME=/audit/home \
  --env GIGAI_AUDIT_TARGET=/audit/target \
  --env GIGAI_AUDIT_WORKPAD=/audit/workpad \
  gigai-debian-offline-r7-final-kkbodn
```

The image's existing command is `tools/run_debian_offline.sh`, which runs `verify_debian_offline.py` and then `python -m pytest -q`. The preflight emitted:

```text
verified Debian offline non-root, network-isolated audit mounts
```

The raw pytest progress reached 64% and contained at least ten visible `F` markers. No test names or final summary were emitted before the bounded process group was terminated. Raw run evidence is retained at:

- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-run.provenance`
- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-run.stdout.log`
- `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-run.stderr.log`

The stderr termination record is `got 3 SIGTERM/SIGINTs, forcefully exiting`. This was an intentional stop after failure markers, not a pass, retry, or fix-forward. Because the wrapper was terminated before it could write its status file, `/private/tmp/gigai-r7-debian-verification-kkbODn/docker-run.status` is absent and no numeric exit value is inferred.

## Scope and remaining gates

This evidence validates only the pre-scratch-correction Debian attempt using the copied disposable context and immutable image. The Debian lane remains unresolved/failing in this bounded observation; the failure names and root cause were not available without an additional run, which was out of scope. The prior macOS source matrix and installed verifier/scenario evidence remain historical evidence and were not converted by this run; other platform/tag gates, cross-platform exact-tag CI, and publication/UAT gates remain unproven here.
