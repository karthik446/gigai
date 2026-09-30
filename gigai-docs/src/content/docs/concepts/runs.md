---
title: Runs and the journal
description: Where a run's state lives and how to read it.
---

GigAI keeps configuration, credentials, and work state under a home directory
you choose, and makes every model call, review, and result inspectable rather
than treating one model response as proof of correctness.

- A Gig's **workpad** is where its journal, proposals, and Run state live.
  `gigai workpad path` prints the canonical path of a registered Gig workpad.
- `gigai run-details <run-id> --json` reads durable Run state.
- Any command that supports `--json` prints a structured result on **stdout**
  on both success and failure (see `gigai doctor --json`). A malformed invocation
  is a Click usage error and goes to **stderr** as plain text; check the exit
  code.

`gigai doctor` confirms the install is healthy.
