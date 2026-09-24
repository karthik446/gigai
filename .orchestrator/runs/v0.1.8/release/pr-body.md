# GigAI v0.1.8: Scout find-jobs graph (M1)

## Summary

Ships the first working GigAI Gig: **Scout find-jobs**, an acquire → assess → present
graph runnable end to end from a browser button. Acquire pulls postings via Exa
query-based discovery plus direct ATS board clients (Greenhouse/Lever/Ashby) with
an auto watchlist; assess builds an explicit requirements × pinned-resume matrix
under a sealed per-run model target (local by default, no silent provider
fallback); present serves a localhost API and a Vite/React UI with an explicit
consent step before any network or model call.

Alongside the feature: the test suite moved to behavior-lane packages under
`tests/behaviors/` (from the old flat `tests/test_*.py` layout) with a single
aggregate `make test` contract and a bounded, resource-aware xdist runner;
**all of Scout's code, data, and UI moved from a flat `src/gigai/scout_*.py` +
`src/gigai/data/scout/` + root `ui/` layout into its own `src/gigai/scout/`
package** (`find_jobs/` subpackage, `data/`, `ui/`) — a physical move plus
import-path fixes only, no behavior change beyond paths/names; the v0.1.7
release blocker (installed G03/G22 verifiers not passing an explicit `env`
into subprocess calls under the new runner's stricter isolation) is fixed;
two polish fixes landed (a safe JSON-500 error boundary on the present API,
and UI waiting/poll-stop states); a golden-digest test now pins the six
pre-existing Scout graphs' compiled-artifact digests as a permanent,
git-history-independent regression guard; and `docs/development/v0.1.9/`
gained four planning spikes (S16–S19) for the 0.2.0 core/gig-decoupling
ledger. It also publishes the Orca coordinator's workpad (`.orchestrator/`,
itself reorganized into `runs/v0.1.8/` archive + a `README.md` + helper
scripts moved into `.claude/skills/gigai-orchestrator/`) as the project's
committed coordination record.

This PR is commit-prep only: the branch is organized into the logical commits
listed in `.orchestrator/runs/v0.1.8/release/commit-plan.md`; **nothing has
been committed, pushed, or deployed**. The operator commits after reviewing
this plan.

## Architecture rule

**Gigs import core; core never imports a gig.** Scout (`src/gigai/scout/`,
including its `find_jobs/` subpackage) depends on `gigai`'s core modules
(`run.py`, `graph_node_registry.py`, canonical/materialization helpers, etc.);
no core module imports anything under `gigai.scout`. This is enforced by
convention today and is the subject of the S16 core/gig-decoupling spike
queued for v0.1.9 (see "0.2.0 ledger" below) — the goal there is a registry
seam so core can discover and run a gig without ever naming it.

## What works (evidence)

- **Final full test run, post package-reorg**
  (`.orchestrator/runs/v0.1.8/logs/111655-test-make-test-final-post-reorg.log`,
  confirmed by reading the log): source lane — **1885 passed, 0 failed, 1
  skipped**, 514.24s wall time (`-n 14 --dist=worksteal`); installed lane — 23
  installed verifiers pass (canonical, CLI, G03–G28, schemas) plus **38**
  AST-selected installed tests pass against a built wheel; overall `=== EXIT
  0 ===`.
- **find-jobs graph end to end, offline**:
  `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` drives the real
  `ScoutFindJobsBackend` HTTP server against the real `run.py`/
  `scout/find_jobs/bindings.py` wiring with a deterministic test model
  (`TEST_MODEL_NAME`/`TEST_MODEL_DIGEST`), covering config → run start → poll
  → results through the real acquire/assess/present nodes, without live
  Exa/ATS network calls. Included and passing in the 1885 above.
- **UI dry run passed** (Chrome, fixture backend + real handler + real UI
  under `src/gigai/scout/ui/`): every screen renders from the contract DTOs
  end to end (`.orchestrator/status.md`, "Done" log).
- **The v0.1.7 release blocker root cause is fixed**: `verify_installed_g03.py`
  and `verify_installed_g22.py` were the only installed verifiers that didn't
  pass an explicit `env` (with `--create-model-target`, pinned `HOME`/`PATH`)
  into their subprocess calls; both now do, and both pass in the final
  installed-lane run above (`.orchestrator/runs/v0.1.8/workers/ci-audit.md`).
- **Scout package move verified byte-identical for pre-existing graphs**:
  `tests/behaviors/scout_find_jobs/test_integration_graph.py::
  test_existing_scout_graphs_match_pinned_pre_i1_digests` pins the six
  pre-existing Scout graphs' compiled-artifact digests (verified against the
  pre-reorg `HEAD` baseline before being pinned) so the move itself, and any
  future one, can't silently change existing graph output
  (`.orchestrator/runs/v0.1.8/workers/scout-reorg.md`).

## What's unproven

- **The live M1 click**: an operator-driven run from the UI against real Exa
  and ATS network calls has not happened. The operator will test this from
  the published v0.1.8, after merge and tag.
- **The full CI matrix run**: the fixes above are verified locally
  (`make test`, the wheel-lane installed verifiers); the actual GitHub
  Actions matrix (`pull_request.yaml` source-tests across Python
  3.11/3.12/3.13 × OS, plus `release.yml`) has not run end to end on this
  branch.

## Release flow

```
merge → git tag -a v0.1.8 -m "GigAI v0.1.8" && git push origin v0.1.8 (annotated tag required: release.yml:31-33) → release workflow (~1h45m)
```

## Known follow-ups (0.2.0 / v0.1.9 ledger)

- **S16 — core/gig decoupling**: a registry/plugin-discovery seam so core
  (`cli.py`, `run.py`, the graph node registry) can find and run a gig
  without importing it by name.
- **S17 — gig module structure, classes**: move Scout (and future gigs) from
  a loose module tree toward a class-based, self-contained package shape.
- **S18 — Scout interview-prep graphs**: the `prepare-interview` goal-graph
  work deferred past M1.
- **S19 — test suite diet**: trim/rebalance `tests/behaviors/` now that the
  S11 reorg and the M1 feature both landed.
- The g04 two-process init flake (`test_two_installed_init_processes_
  converge_without_lock_or_duplicate` — pre-existing race, not a v0.1.8
  regression, not fixed here).
- HiringCafe sitemap acquisition (deferred past M1; unproven board-token
  yield).
- OpenRouter API key setup/wiring (target exists in contracts; no live
  credential flow yet).
- Hourly ATS polling (button-triggered acquisition only for M1).

## Commit plan

See `.orchestrator/runs/v0.1.8/release/commit-plan.md` for the ordered,
disjoint, 14-commit breakdown with every changed/deleted/untracked path
assigned to exactly one commit — verified with
`.orchestrator/runs/v0.1.8/release/verify_coverage.py`: **0 unassigned, 0
duplicates, all 715 entries accounted for** against
`git status --porcelain --untracked-files=all` on the final tree (including
five files from concurrent worker activity caught mid-scan — a `release-docs`
worker's version bump, CHANGELOG, and README-rewrite task, still landing
during this scan; see the plan's drift note and its Commit 14).

## Secret scan

No secrets found anywhere in the final tree (all non-deleted changed/untracked
files scanned as text; deleted files can't be scanned but can't leak either).
Checked for: `dcap_` Orca dispatch-capability tokens, `sk-`/`ghp_`/`gho_`
API-key shapes, `-----BEGIN ... PRIVATE KEY-----` blocks, `Bearer <token>`
patterns, generic `api_key`/`API_KEY` assignments to real-looking values, and
the operator's email (`[operator-email]`) — zero hits on any of the above. Any
API-key-shaped or `Bearer`-shaped strings that do appear in the tree are test
fixtures with synthetic values (e.g. `"super-secret-value"`) or references to
environment-variable *names*, not values. `.orchestrator/**` needed no
redaction (nothing to redact).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
