# ADR 0001: Test supported Python versions before setting a ceiling

- Status: Accepted
- Date: 2026-08-02
- Supersedes: the Python `<3.12` ceiling in the approved V14 implementation plan

## Context

The V14 plan declared `requires-python = ">=3.11,<3.12"`. The Phase 0 evidence
was executed with Python 3.11.9, but it did not identify a Python 3.12
incompatibility. The available Python 3.12.8 environment lacked the project test
dependencies; that is an environment gap, not compatibility evidence.

Keeping the unproven ceiling would prevent the repository from testing the
forward-compatibility claim it needs to make responsibly.

## Decision

GigAI declares `requires-python = ">=3.11"` and continuously tests Python 3.11,
3.12, and 3.13. The published compatibility claim is limited to versions with a
green CI lane.

If a newer interpreter fails because of a genuine runtime incompatibility, the
upper bound will be reinstated in the same change that records the failing test
evidence and remediation path.

## Consequences

- Contributors are not rejected by an unsupported ceiling.
- CI, rather than the development machine's installed packages, determines the
  evidence-backed compatibility range.
- A future ceiling is a deliberate compatibility decision, not a precautionary
  guess.
- This decision changes packaging and platform support only. It does not change
  any frozen schema or canonical-vector bytes.
