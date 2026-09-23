# Coordinator review: Amendment 02 (2026-09-22 22:55)

Verdict: **the direction is right; not ready to dispatch.** 3 blockers, 4 gaps and 1 sizing issue must go back to the W0 worker.

## Blockers (the goal fails as written)
B1 **The UI button cannot give consent.** The amendment reuses `direct_cli_confirm`, but `_validate_operator_consent` rejects any other source (`run.py:2198`). A Run workflow click therefore has no legal consent path. It needs a new loopback-only UI consent source (Integration owns run.py), with the scope (sources, queries, model target) shown before the operator confirms.
B2 **Hosted assess has no network effect.** Non-local adapters need `policy.network_allowed` (`model_execution.py:241-244`), which is separate from local permission (`:80-84`). The amendment defines a network effect for acquire only; assess with codex_cli (Luna) or openrouter would be blocked as `network_denied`. Assess needs a hosted-model effect plus consent, scoped to the chosen target.
B3 **No shared contract owner.** `NodeContext`, `AcquireInput`/`Output`, `AssessInput`/`Output` and `PresentInput`/`Output` are used by all four packets, but no file defines them and no packet owns them. Parallel work would invent 4 versions. Needs one contracts module owned by Integration, landing before the others (wave 1a).

## Gaps (missing decisions or contracts)
G1 **No cap on assessment.** Hourly board polls plus search can yield hundreds of postings. The amendment doesn't say which postings get assessed per run; at about 1 minute each locally, or Codex quota for Luna, this is the cost driver. Needs: only new postings that pass the role filter, a max N per run, and the rest shown as "not assessed".
G2 **Roles and queries have no home.** "Five expected roles" and the query config aren't stored or passed anywhere. Needs a source: run input from the UI, or a project config file.
G3 **Latest resume** (see A2-2). The ingestion path exists (`cli.py:217 --kind resume`, `private_records.py:308`), so resolving the newest resume at run start and pinning it is feasible.
G4 **Hourly polling has no driver** (A2-1); the backoff schedule is moot without one.

## Minor
M1 The Greenhouse domain filter lists only `boards.greenhouse.io`. Newer boards live at `job-boards.greenhouse.io`; include both.
M2 Tests for network nodes should say they use `httpx.MockTransport` (httpx is already a runtime dep, `pyproject.toml`), so there's no live network in focused tests.
M3 Link anchors like `file.md:40-79` don't resolve as links (cosmetic).

## Sizing (for the roadmap)
A is too big for a small agent: Exa, sitemap, 3 ATS clients, normalization/diff, watchlist and backoff. Integration is serialized on `run.py` (3.5k+ lines), a single owner. The roadmap must split A into about 5 items (one per source client, normalization, watchlist) and sequence the Integration items on run.py.

## Verified OK
Ownership disjoint; test files exist; 4/4 spot-checked citations accurate; docs-only diff; the resume "never latest" finding is correct (`scout_tailor_selection.py:3-4`).
