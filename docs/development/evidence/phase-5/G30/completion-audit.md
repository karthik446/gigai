# G30 completion audit

- Status: Complete — evidence accepted; G31 is the next consumer
- Scope: bounded Codex/Claude CLI adapters, browser-first setup, and ordinary
  browser-first Gig creation entry
- Release position: release-candidate evidence for v0.1.6; no publication or
  alpha claim is made here

## Evidence summary

The accepted adapter contract is recorded in
[`adapter-contract-decision.md`](adapter-contract-decision.md). It distinguishes
`detected`, `configured`, `usable`, `authentication_required`, and `selected`;
it does not treat executable detection as invocation authority.

The implementation provides:

- bounded `codex exec --json` and `claude -p --output-format json` adapters;
- explicit argv, `shell=False`, private child working directories, timeout and
  cancellation handling, malformed-output refusal, and no fallback/retry;
- an explicit `CLAUDE_CODE_OAUTH_TOKEN` handoff only when the operator exports
  it, with no token persistence or browser/config exposure;
- browser-first setup for folder selection, CLI/API access, multiple enabled
  models, machine-wide reviewer/verifier/researcher/Gig-creator defaults, and
  explicit apply; and
- browser-first adaptive Gig creation without implementation-facing flags.

## Machine verification

- Focused G30 tests: 23 passed after the final token-boundary and setup fixes.
- Full source suite on 2026-08-18: 604 passed, 1 skipped. The skip is the
  explicitly opt-in live CLI test when `GIGAI_G30_UAT=1` is absent.
- Operator live probe: `g30_live` passed for the explicitly configured Codex
  and Claude targets after Claude's token boundary was configured. The result
  was 1 passed in 9.21 seconds; no credential value is recorded.
- Fresh installed wheel: schema inventory, canonical API, CLI, G03–G09,
  G11, G13–G17, G19–G23, and G26–G28 replay verifiers passed from an isolated
  Python 3.11 environment. The packaged G22 verifier was repaired to follow
  the adaptive question and explicit proposal-build transitions.

## Boundaries and remaining work

G30 does not claim Anthropic API support, automatic fallback, target mutation,
Gig-specific machine defaults, or alpha readiness. G31 owns fresh-release
installation/upgrade, the complete human UAT record, exact-tag CI, and the
v0.1.6 publication decision.
