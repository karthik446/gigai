# TD-0003 — GitHub Actions Node.js runtime warnings

- Status: In verification
- Discovered during: `0.1.3` release workflow and pull-request runs
- Affected surfaces: `actions/checkout@v4`, `actions/setup-python@v5`,
  `astral-sh/setup-uv@v5`
- Ownering lane: G25 alpha release readiness / CI maintenance

## Observation

GitHub Actions previously reported that the pinned actions targeted Node.js 20
and were being forced onto Node.js 24 by the runner. The workflows now use
Node 24-compatible action releases (`checkout@v6`, `setup-python@v6`, and
`setup-uv@v8.0.0`); hosted workflow confirmation remains pending.

## Proposed resolution

Track upstream action releases that natively target the supported runner
runtime. Upgrade each action deliberately, review its changelog and permission
behavior, then run both `pull_request.yaml` and `compatibility_job.yaml` before
updating the release workflow references.

Do not suppress the warning or set an insecure compatibility override as the
resolution.

## Exit evidence

- Pull-request, scheduled compatibility, and release workflow logs no longer
  emit the Node.js 20 deprecation warning for these actions.
- The upgraded actions preserve checkout depth, cache behavior, artifact
  handling, permissions, and matrix results.
- The action versions and the reason for each upgrade are recorded in the
  internal changelog.
