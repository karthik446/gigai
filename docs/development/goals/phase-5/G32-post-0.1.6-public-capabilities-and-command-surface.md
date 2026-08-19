# G32 — Post-0.1.6 Public Capabilities and Command Surface

- Status: Proposed for review; not activated
- Type: Post-release documentation and public-surface goal
- Depends on: the merged, published v0.1.6 release and G31's completion audit
- Unblocks: a clear public entry point for the later alpha-readiness review

## Outcome

After v0.1.6 is published, replace the stale public package explanation with a
short, accurate description of what GigAI enables today. The README shown on
GitHub and PyPI, the public command sheet, and the operator-facing changelog
must agree about the current version and supported command surface.

This goal is documentation work. It does not add runtime behavior, change
schemas, or make an alpha claim.

## Contract gate

Work starts only from the published v0.1.6 tag and must use the shipped command
surface as its authority. The documentation must not describe a command,
provider, adapter, lifecycle, or capability as available merely because it is
planned or present on an unreleased branch.

The public surface has three distinct responsibilities:

1. README — explain the problem GigAI addresses, what a Gig is, setup, current
   capabilities, representative use cases, and clearly marked construction
   areas.
2. Command sheet — list only commands supported by the stated package version,
   with a version history that is extended at release time.
3. Changelog — summarize operator-visible capability changes without exposing
   internal schema inventories, test commands, goal graphs, or implementation
   planning as product documentation.

## In scope

- Refresh the public README for GitHub and PyPI.
- Keep the README concise and capability-oriented rather than roadmap-oriented.
- Describe `gigai setup` accurately, including workspace, access, model, role,
  and confirmation steps, plus Claude CLI token setup where applicable.
- Document the current v0.1.6 command surface from `gigai --help` and the
  shipped package, not from internal plans.
- Add a versioned command-surface row for v0.1.6.
- Preserve the Under construction section, including separate Gig creation and
  Gig improvement browser lifecycles, multi-model review, observability,
  evaluation layers, extensible roles, portability, and UAT hardening.
- Ensure public links resolve and the README renders cleanly on PyPI.

## Out of scope

- Runtime implementation or CLI changes.
- Releasing, tagging, or publishing v0.1.6 itself.
- Documenting internal JSON schemas, evidence paths, mutation harnesses, or
  full-suite CI commands in the public README or command sheet.
- Calling future example Gigs supported products before their workflows exist.
- Declaring GigAI alpha-ready; that remains owned by the later release lane.

## Acceptance criteria

1. The work records the exact published v0.1.6 version used as its source.
2. README, command sheet, and external changelog agree on the current version.
3. Every command listed for v0.1.6 is present in the installed package and has
   user-facing help; no planned-only command is listed as supported.
4. The README explains GigAI as an open-source tool and defines a Gig as a
   repeatable set of Goals with stable instructions, changing inputs, and
   verifiable, reviewable results.
5. Setup, model access, role defaults, Gig creation, improvement, review, and
   local-state boundaries are described in plain language.
6. The README names current capabilities separately from under-construction
   capabilities and does not expose internal implementation plans.
7. The command sheet contains only versioned supported commands and its v0.1.6
   row matches the installed CLI.
8. PyPI rendering and GitHub rendering are manually inspected, all links pass,
   and `git diff --check` is clean.
9. A completion note records the source tag, changed public documents, and any
   capability intentionally deferred to a later release.

## Verification and evidence

Evidence consists of sanitized public-document screenshots or render checks,
the installed `gigai --help` command inventory, link checks, and a short
completion audit under `docs/development/evidence/phase-5/G32/`. No private UAT
prompts, credentials, model transcripts, or local workpad contents belong in
the repository.

## Stop boundary

Stop if v0.1.6 is not published, if the installed command surface cannot be
reproduced, if a public document describes unreleased behavior as supported, or
if README content would expose internal schemas, plans, or evidence machinery.
