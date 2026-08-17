# G28 Completion Audit — v0.1.5 Readiness Foundation

- Status: Complete — implementation and candidate evidence accepted
- Recorded: 2026-08-16
- Candidate: v0.1.5 source candidate; no public release or human-UAT claim

## Verdict

G28's implementation boundary is complete. The candidate now has separate
unit/contract, integration, installed-E2E, and behavioral-evaluation commands;
a namespaced role registry with an additive packaged role-reference schema;
truthful setup model-readiness presentation; and a browser-first create path
that works after normal setup and target initialization without implementation
flags.

This closes the technical-readiness gate for the next goal. It does not claim
that a deterministic fixture is a production-model judge, that detected Codex
or Claude executables are supported adapters, or that human UAT has passed.

## Acceptance evidence

| Criterion | Evidence | Result |
| --- | --- | --- |
| Accepted S27 prerequisites | Three accepted decision records cited by the G28 goal | Pass |
| Four separate tiers | [`tier-reports/`](tier-reports/) and `tools/run_g28_tier.py` | Pass |
| Labeled behavioral corpus | G26/G27 manifest with development, calibration, and final-held-out splits | Pass |
| Plumbing distinct from behavior | Reports carry `methodology_plumbing`, `behavior_scored`, and `candidate_judge_scored: false` separately | Pass |
| Central roles | `role-reference.schema.json`, owner-aware namespaces, compatibility decoder, extension API | Pass |
| Legacy replay | Explicit legacy classification; unresolved values are not guessed across namespaces | Pass |
| Truthful setup | Configured API/fixture readiness plus detected-but-unsupported executable display; references only, no credential values | Pass |
| Browser-first create | Fresh installed replay launches `gigai create installed-g28` without request/reference/open/model flags | Pass |
| Installed candidate | 31-schema verifier and `verify_installed_g28.py` pass | Pass |
| Handoff readiness | This audit and [`terminal-handoff.md`](terminal-handoff.md) are committed | Pass |

## Executed verification

- Unit/contract tier: 14 passed.
- Integration tier: 9 passed; loopback permission was required for the local
  HTMX server.
- Installed-E2E tier: fresh installed replay passed, including setup, role
  resolution, behavior reporting, non-Git target binding, and browser launch.
- Behavioral tier: development, calibration, and final-held-out reports passed
  against the deterministic fixture observation set.
- Installed schema verifier: 31 packaged resources verified.
- Focused CLI/setup regression: 24 passed.

The exhaustive repository suite was not rerun in this closeout; it remains in
the scheduled/manual Compatibility workflow. The pull-request workflow now
runs the focused G28 tiers and the installed G28 replay, while compatibility
retains the exhaustive source/platform/installed matrix.

## Known limitations

1. `candidate_judge_scored` remains false. The behavior reports validate the
   evaluation plumbing and fixture scoring, not model-judge accuracy.
2. Codex and Claude executable discovery is detection-only. GigAI does not
   invoke or advertise either executable as a supported adapter.
3. The v0.1.5 package/tag/release and human UAT remain downstream work.

