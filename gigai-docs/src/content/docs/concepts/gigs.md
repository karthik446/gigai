---
title: Gigs and goal graphs
description: What a Gig is, and the one-way boundary with GigAI core.
---

A **Gig** is a self-contained goal-graph package built on top of GigAI core.
The boundary is fixed in one direction only: **a Gig imports GigAI core;
GigAI core never imports a Gig.** Gigs are portable, reviewable units of
work, not plugins the runtime depends on.

Scout is one Gig among others GigAI can host; it gets no special runtime
treatment. It ships with GigAI and has its own bundled goal-graph data under
`src/gigai/scout/`.

## Registering and activating a Gig

- `gigai init` binds a git repository as a GigAI project and `gigai gigs` lists
  the Gigs registered for it.
- If a project has more than one installed, approved Gig, `gigai gig use <gig-id>`
  switches which one is active.
- Installing a Gig goes through the proposal/approval lifecycle: an install
  binds, approves, and activates it (for Scout that is one command).

See [Architecture](../architecture/) for where the pieces live and the
[CLI reference](../../reference/cli/) for every command.
