# Wave 1 first-pass review (coordinator, 2026-09-23 ~15:40 MDT)

## ci-docs-skip (report: workers/ci-docs-skip.md)
- Logic checked against the spec: incremental diff only when `before` is a valid ancestor; skips only when the incremental diff is non-code AND the pull_request.yaml run for `before` concluded `success`; otherwise uses the whole-PR diff. Chaining is sound: a skipped docs-only run concludes success, and that success rests on the earlier code run's success.
- **BLOCKER:** the `changes` job requests `actions: read`. Called workflows can't ask for more than their caller grants. The callers (release.yml `ci` + `post-release-compatibility`, top level `contents: read`; compatibility_job.yaml `contents: read`) don't grant it, so the release and the nightly would fail at startup. → r1 (compatibility_job.yaml) + release-pipeline-r1 (release.yml).
- Minor: the run lookup takes `workflow_runs[0]` for the SHA with no event filter, so a workflow_dispatch run could count. → r1 adds `event=pull_request`.
- Unverifiable locally: the live skip. Prove it with a docs-only push to PR #37 after a green code run.

## release-pipeline (report: workers/release-pipeline.md)
- Job graph correct: github-release needs [preflight, build, publish-pypi]; verify-pypi needs github-release; publish-pypi still needs verify-testpypi. Checkout comes before download-artifact in github-release (dist/ survives).
- **MAJOR:** it polls the JSON API, then installs once. uv reads the simple index, which can lag the JSON API: the same race, narrower. → r1 retries the `uv tool install --refresh` itself.
- **MAJOR:** a ~9.5 min poll inside `timeout-minutes: 10` leaves no headroom. → r1 raises it to 20.
- Note for the release checklist: future Release notes are the CHANGELOG section verbatim. The 0.1.8 CHANGELOG section is 4 terse bullets vs the hand-written notes, so write CHANGELOG sections for users (skill §9).
