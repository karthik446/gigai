# GigAI v0.2.0 — Spikes index

**Status:** Discussion space, not a plan. Nothing in this directory is
authorized implementation.

## Ticket format for new work

Same convention as
[v0.1.8's spikes README](../../v0.1.8/spikes/README.md#ticket-format-for-new-work):
Status / Problem / Intended change (proposed, not decided) / Tasks /
Acceptance / Investigate / Non-claims / Change log, with an added Open
questions section listing every decision the ticket needs from the
operator. Every claim cites `file:line` or a command actually run. Mark
undefined scope and proposed experiments explicitly; a spike is not an
execution receipt. READ vs EXECUTED is stated for every claim.

## Spikes

- [S20 — Stable core as a library](S20-stable-core-as-a-library.md):
  derives today's core→gig API surface from Scout's actual imports (cross-
  referenced against v0.1.9's S16 inventory), lays out what a semver/
  deprecation/CLI-as-API/on-disk-format stability contract for 0.2.x would
  need, inventories core for cleanup (size, duplication, legacy modules,
  dead-code candidates) ranked by risk, describes what library packaging
  (typed surface, docs, example gig, distribution, compatibility testing)
  requires, and proposes a themed (undated) path from v0.1.9 to v0.2.0.
- [S21 — Gigs in their own repos; gigai as a facilitation library](S21-gig-repos-and-data-ownership.md):
  records the operator's 2026-09-23 decisions (gigs get their own repos,
  gigai facilitates run/db/local tracking, gig domain data lives in the
  gig's own store not gigai's, Python only, PII-by-default is later); traces
  the resulting authority reversal in today's journal/projection code, the
  shared `state.sqlite` constraint with core's G22 interview trace, and
  core's current hosting of Scout's execution; gives execution-ownership
  and repo/packaging alternatives with a stated lean; and collects the open
  questions the decisions don't yet resolve.

## Relationship to v0.1.9

v0.1.9's S16 (core/gig decoupling) and S17 (gig module classes) are
prerequisites this directory's discussion assumes, not scope it repeats.
S20 cites both directly rather than re-deriving their findings. See
[the v0.2.0 README](../README.md#how-v0.1.9s-s16-s19-feed-into-this) for how
each of S16–S19 feeds into the stable-core question.

## Non-claims

- No v0.2.0 roadmap, release scope, or implementation is authorized by this
  directory.
- No code, schema, or test changes were made while writing these documents.
- Nothing here overrides or blocks v0.1.8 or v0.1.9 work.
