# G31 machine verification

- Candidate source commit: `c0b95ae`
- Package metadata on this branch: `0.1.5`
- Target release: `0.1.6`

## Source suite

Command:

```text
uv run --locked pytest
```

Result on 2026-08-18:

```text
604 passed, 1 skipped in 295.50s
```

The one skip is `tests/test_g30_live_cli.py`, which is deliberately skipped
unless `GIGAI_G30_UAT=1` is set. Ordinary CI does not invoke provider CLIs.

## Installed wheel

A fresh wheel was built and installed into a disposable Python 3.11
environment. The following passed:

- 31 packaged schemas and hash inventory;
- canonical identity API;
- installed CLI help/version/setup/doctor/init/create surface;
- G03–G09 and G11 installed replays;
- G13–G17 installed replays;
- G19–G23 installed replays;
- G26–G28 installed replays; and
- installed G22 adaptive create interview, including model-selected questions,
  explicit proposal build, and approval.

The installed command reported `gigai 0.1.5`. This is installed-boundary
evidence for the release candidate code, not evidence that v0.1.6 has already
been published.

## Behavioral evaluation

The G28 corpus contract and all three splits passed:

- development: 4 cases;
- calibration: 3 cases; and
- final held-out acceptance: 3 cases.

The reports correctly say `candidate_judge_scored: false`: these are
deterministic evaluation-plumbing and fixture results, not evidence of an
accurate production judge.

## Live CLI checkpoint

The operator ran the opt-in `g30_live` check with explicitly configured
`codex-default` and `claude-default` targets after exporting the Claude token
through the documented boundary. Result: `1 passed in 9.21s`. No token,
transcript, session identifier, or provider payload is stored here.
