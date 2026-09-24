# GigAI v0.2.0 — Discussion space

**Status:** Discussion, not a plan and not a decision. This directory holds
spikes and working notes about what it takes to make GigAI's core a stable,
library-like foundation that gigs build on. It is not a v0.1.8 blocker: any
release work already in flight under `docs/development/v0.1.8/` proceeds
unaffected by anything here.

**Requested:** 2026-09-23 (operator).

## Purpose

The operator's stated intent, verbatim:

> 0.2.0 should be a stable gig core. After 0.2.0 we use versioning 0.2.1
> and so on, so we ship versions sooner. We need to figure out how to build
> this like a library. It's a 0.2.0 folder: spike, work and discussion. We
> don't know anything yet on how to stabilize. I mean we do, but there's a
> lot of core and a lot of cleanup to do before we can call it stable.

Two things follow from that directly:

1. **Versioning intent:** 0.2.0 is the release that makes GigAI's core
   stable enough to call it a foundation. Every release after that —
   0.2.1, 0.2.2, and so on — ships more frequently than v0.1.x did, because
   the core underneath them isn't supposed to move out from under a gig
   each time. 0.2.0 itself is the one release in this line that's allowed
   to be a bigger lift; the point of doing that lift once is so the 0.2.x
   line afterward doesn't need it again.
2. **We don't know how yet.** The operator is explicit that "a lot of core
   and a lot of cleanup" stands between today's codebase and something
   that can honestly be called stable. This directory exists to find out
   what that cleanup actually is, with evidence, before anyone commits to
   doing it.

## The gig architecture rule

Copied verbatim from
[`docs/development/v0.1.9/README.md`](../v0.1.9/README.md#architecture-rule-operators-non-negotiable),
where it is recorded as the operator's non-negotiable decision:

> GigAI is a platform; a Gig is a self-contained package built on the
> platform. Scout is a Gig (later: trader, shopper, not now). A gig depends
> on and imports gigai core; core never imports any gig. Core provides
> registration and discovery points; gigs plug in.

"Stable core as a library" is this rule made literal: a library is exactly
a thing other packages depend on and import, that never imports its own
dependents back. Every spike in this directory treats the rule as a fixed
constraint, the same way v0.1.9's spikes do — not a topic open for debate.

## How v0.1.9's S16–S19 feed into this

v0.1.9's backlog (`docs/development/v0.1.9/`) researched the decoupling and
duplication problems that sit directly underneath "stable core":

- **S16 (core/gig decoupling)** inventoried every core→`gigai.scout` import
  and string-literal coupling site (45 strict-pass sites, 118 broader
  string-mention sites) and proposed a `GigManifest` protocol plus
  entry-point/bundled-list discovery so core can stop naming a specific gig.
  This is the literal prerequisite for "core never imports a gig" to be
  true rather than aspirational — and therefore for any public API surface
  proposed under 0.2.0 to mean anything.
- **S17 (gig module structure)** inventoried Scout's own repeated module
  shapes (the validate→record→publish→read pattern, duplicated CLI
  helpers, a byte-identical helper block between `research.py` and
  `research_v3.py`) and proposed core base classes (`RecordRepository`,
  `GigCliGroup`) so a second gig reuses a shared implementation instead of
  copying Scout's pattern a third time. These base classes are effectively
  S16's registration seam given a concrete implementation.
- **S18 (Scout interview-prep graphs)** is a Scout-specific product idea,
  not core-stability research; it doesn't feed this directory directly.
- **S19 (test-suite diet)** measured the 159-file, ~1,200-test suite and
  proposed a fast `make unit-tests` lane. A frequent 0.2.x release cadence
  needs a cheap inner-loop test lane; S19's measurement is the starting
  evidence for whether that lane exists yet.

[S20](spikes/S20-stable-core-as-a-library.md) is where this directory picks
those threads up: it derives today's actual core↔gig API surface, lays out
what stability contracts (semver, deprecation, CLI-as-API, on-disk-format
versioning, the S16 registration interface) a 0.2.x cadence needs, inventories
core for cleanup ranked by risk, and describes what "library packaging"
(typed surface, gig-author docs, an example gig, distribution, compatibility
testing) requires.

## Roadmap (proposed, not accepted)

[`v0.2.0-gig-workbench-roadmap.md`](roadmaps/v0.2.0-gig-workbench-roadmap.md)
is a **proposed, not accepted** sequencing for v0.2.0 as a personal gig
workbench (operator decisions D-A..D-G, 2026-09-23), covering Workstreams
0-7 plus a slimmer S20. It sequences S16/S17/S19/S20/S21; it does not
replace their research, and nothing in it is authorized for implementation
or for pulling into v0.1.9 without a separate operator decision.

## Open questions

S20 collects the specific decisions this research surfaces (what "private"
means mechanically, how strict a semver contract 0.2.x commits to, whether
the CLI's JSON conventions get frozen as-is or revised once before being
declared stable, what compatibility testing requires, which release gates a
patch can skip, and how strictly sequential the proposed decouple→declare→
class→type→prove→document→cut path needs to be). See
[S20's "Open questions for the operator"](spikes/S20-stable-core-as-a-library.md#open-questions-for-the-operator)
for the full list. None of them are answered here; this README only routes
to where they're asked.

## Non-claims

- No v0.2.0 release scope or implementation is authorized by this
  directory or any document inside it. A roadmap document exists
  (linked above) but is explicitly proposed, not accepted.
- No code, schema, or test changes were made while writing these documents.
- This is not a blocker for v0.1.8; v0.1.8's roadmap and release work
  continue independently of anything recorded here.
- The versioning intent and architecture rule above are the operator's
  stated decisions; how to get from today's codebase to a stable 0.2.0 is
  explicitly unknown and is what this directory researches.
