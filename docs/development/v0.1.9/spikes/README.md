# GigAI v0.1.9 — Research spikes

**Status:** Backlog. Four research spikes recorded 2026-09-23 (S19 added
mid-dispatch as a 4th spike, same conventions); none started, none authorized
to implement. v0.1.9's roadmap is not decided — these spikes inform it, they
are not it.

## Ticket format for new work

Following [the v0.1.8 spikes convention](../../v0.1.8/spikes/README.md#ticket-format-for-new-work):
start new work documents with a short Jira-style Markdown ticket — problem,
intended system-behavior change, tasks, acceptance criteria and attached
evidence — then put conversation notes and granular detail below that
summary. Mark undefined scope and proposed experiments explicitly; a ticket
here is not an execution receipt.

## Architecture rule

> GigAI is a platform; a Gig is a self-contained package built on the
> platform. Scout is a Gig (later: trader, shopper, not now). A gig depends
> on and imports gigai core; core never imports any gig. Core provides
> registration and discovery points; gigs plug in.

This is the operator's stated, non-negotiable rule. S16 and S17 both treat it
as the fixed target; S18 treats it as the boundary any new Scout capability
must respect (a new goal graph and its records live in `src/gigai/scout/`,
not in core).

## Spike 16 — Core/gig decoupling: inventory and registration seam

[S16](S16-core-gig-decoupling.md) inventories every import of `gigai.scout`
(and gig-specific strings/paths) from core modules, using AST parsing plus a
broader string-mention pass, and proposes a Gig registration mechanism
(protocol/manifest, entry points or a bundled-gig list, an enforcement test)
so core can stop naming Scout directly. Requested and researched 2026-09-23.
Documentation only; no import is removed and no registration mechanism is
implemented by this ticket.

## Spike 17 — Gig module structure: classes instead of loose functions

[S17](S17-gig-module-structure-classes.md) inventories Scout's repeated
module families (`documents*`, `interview*`, `proposal*`, `report*`,
`research*`, `tailor*`, `acquisition*`, …), finds the duplicated
validate → build record → publish to journal → read-back pattern with
file:line examples (including a byte-identical duplicated helper block
between `research.py` and `research_v3.py`), and sketches a class-based
structure for one family. It says explicitly where classes would not help.
Requested and researched 2026-09-23. Documentation only; no module is
refactored by this ticket.

## Spike 18 — Scout interview-prep goal graphs

[S18](S18-scout-interview-prep-graphs.md) records the operator's idea for a
Scout workflow — research the role, research the company's interview
process, predict likely questions with typed probabilities, prep the
candidate — usable by an agent (Claude, Codex, or other) as a tool. It
inventories what v0.1.8 Scout pieces already exist to build on (pinned
resume, posting snapshots, requirements matrix, research/research_inputs,
proposals, the existing `prepare-interview` goal graph and interview
records), surveys how an agent could call Scout (CLI, the localhost
`find_jobs.present_api`, an MCP server, a Claude Code skill), and maps the
Jev/TypeSafe `noul`/`choice`/`score` primitives and confidence/abstention
contract onto question prediction. Requested and researched 2026-09-23. This
is a captured idea for review, not a decision to build; no graph, schema, or
tool is added by this ticket.

## Spike 19 — Test-suite diet

[S19](S19-test-suite-diet.md) measures `make test`'s real cost (159 test
files, 1,209 test functions, 74/159 files (47%) doing subprocess/git/server/
multiprocessing work, 24 `verify_installed_*.py` scripts), finds that
`pyproject.toml`'s already-declared `fast_unit`/`integration`/`cli`/
`installed` markers are applied to almost no files today, and proposes an
additive `make unit-tests` target (pure unit tests only, under 60s) selected
by marker, plus a duplication review of the `*_installed_scenarios.py`/
`verify_installed_*.py` pairs and CLI-subprocess-vs-`CliRunner` candidates.
Added mid-dispatch as a 4th spike (with a same-day correction fixing the
target's exact name) on 2026-09-23. Reports an unresolved per-invocation
timing discrepancy as an open blocker rather than certifying the 60s goal.
This is a measurement and lane-addition proposal, not a test purge.

## Spike 23 — Scout setup interview + Exa Agent company discovery (stage 1)

[S23](S23-scout-setup-interview-exa-agent.md) verifies Exa's Agent API
(`POST /agent/runs`, `outputSchema`, effort levels, `budget.maxCostDollars`,
async polling) against the docs plus one live correction
(`input.exclusion` entries must be objects, not bare strings), drafts the
Scout setup-interview question list with the operator's UAT answers as
defaults, and runs 5 live, spend-guarded Agent API calls (actual spend
$0.759 of a $30 cap, no run over $2) varying query phrasing x schema shape
x effort. Finds a decisive primary-metric spread: a query that explicitly
asks for the ATS board root URL plus a strict typed schema at `low` effort
scores $0.0042 per new usable board, 30x-plus better than the weakest
tested combination, and free ATS polling (Scout's existing
`ats_board_clients`) is enough to verify board usability with no extra Exa
spend. Also finds a live 404'd grounding source cited as sponsorship
evidence and a pre-existing gap in `list_greenhouse_board`'s country
parsing. Requested and researched 2026-09-24. Documentation + scripts under
`research/exa_agent_spike/` only; no product code added. Stage 2
(implementation) requires a separate operator review.

## How these spikes finish

Each spike produces a reviewable research record and a set of proposals, not
a promise to implement any of them. The operator reviews before any v0.1.9
implementation is scheduled. None of these five spikes claims v0.1.8 shipped,
authorizes a code change, or commits to a v0.1.9 scope.
