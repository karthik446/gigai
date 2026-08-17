# G27 Completion Audit — Adaptive Gig Discovery

- Status: Complete — runtime implementation and machine evidence accepted
- Recorded: 2026-08-16
- Downstream: G29 owns post-0.1.5 human UAT; G25 owns alpha-readiness

## Verdict

G27 now makes the G26 loopback session a reusable Gig-definition canvas. The
configured model supplies a bounded, typed direction round; the browser
discloses capability and research boundaries; and the workpad records a
revisioned discovery manifest that remains subordinate to the existing
proposal, approval, active-version, and G20 improvement authorities.

This closes G27's runtime and machine-evidence boundary. It does not claim
that deterministic fixture questions prove model-question quality, that
Codex/Claude executables are supported adapters, or that human UAT or alpha
release has passed. Those are explicitly downstream.

## Acceptance evidence

| Criterion | Evidence | Result |
| --- | --- | --- |
| Accepted contract and additive amendment | G27 contract-impact and accepted amendment | Pass |
| Truthful capability disclosure | `tests/test_g27_runtime.py`, installed replay | Pass |
| Intent and optional references | G26 CLI builder integration and G27 runtime tests | Pass |
| Zero-to-five model-selected questions | pre-persistence and manifest ceiling tests | Pass |
| Typed, dependent, reasoned, provenance-tagged questions | generic Question/HTMX rendering and manifest vectors | Pass |
| Bounded research plan | discovery manifest schema and runtime assertions | Pass |
| Stable definition vs. Run input | review projection artifacts and browser assertions | Pass |
| Update/improve visibility | revisioned manifests and read-only improve context | Pass |
| G20 improve gates remain authoritative | bounded learning IDs/active-version checks in lifecycle | Pass |
| Named mutation guards | `tools/run_g27_mutation.py`: 8/8 killed, covering question ceiling, capability truthfulness, network classification, context filtering, reference integrity, journal publication, and duplicate approval | Pass |
| Interrupted discovery recovery | transaction interruption, reconciliation, and malformed-response tests | Pass |
| Fresh-wheel replay | disposable Python 3.11 environment; 31 schemas verified | Pass |
| Human UAT | explicitly owned by downstream G29 after v0.1.5 | Deferred |
| Handoff and release boundary | this audit and terminal handoff; no alpha claim | Pass |

## Executed verification

- Focused G22/G26/G27 interview and runtime regressions: 13 passed.
- G27 mutation harness: 8/8 named guards killed, including dedicated
  capability-truthfulness, network-boundary, and duplicate-approval mutants.
- Source-installed G27 replay: passed with 31 packaged schemas.
- Fresh-wheel G27 replay: passed from a newly built `gigai-0.1.4` wheel in a
  disposable Python 3.11 environment.
- Full repository suite after the guard-fixture closeout: 585 passed and 68
  subtests passed in 5m07s.
- `git diff --check`: clean at closeout.
- The pull-request workflow retains the full source suite and now runs the
  G27 installed replay alongside the existing G28 wheel verifier.

## Known limitations

1. The deterministic question fixture proves plumbing and contract bounds,
   not the quality of a production model's questions.
2. Research is represented as a bounded plan; arbitrary web browsing and
   provider support remain outside G27.
3. G29 must perform the real operator UAT after the v0.1.5 release candidate;
   its evidence remains local-only and sanitized.
