---
title: What GigAI provides
description: A local, user-controlled agent runtime, and Scout, its first Gig.
---

GigAI is a local, user-controlled agent runtime. It keeps configuration,
credentials, and work state under a home directory you choose, and makes every
model call, review, and result inspectable rather than treating one model
response as proof of correctness.

A **Gig** is a self-contained goal-graph package built on top of GigAI core.
The boundary is fixed in one direction only: **a Gig imports GigAI core;
GigAI core never imports a Gig.** Gigs are portable, reviewable units of work,
not plugins the runtime depends on.

## What you get

| | GigAI core | Scout (the first Gig) |
| --- | --- | --- |
| What it is | The runtime: setup, config, secrets, journal, proposal/approval lifecycle, model targets | A job-search workflow: `find-jobs` |
| You use it to | Bind a project, register and run Gigs, read runs and failures | Find postings, rank and assess them against your resume, tailor a resume, download a PDF |
| Runs on | Your machine, with the model you already use (Codex CLI, Claude Code CLI, Ollama, OpenRouter) | Same, plus public job boards (Greenhouse, Lever, Ashby) |
| Interfaces | `gigai` CLI | `gigai scout ...` CLI, a localhost API and web UI |

## Scout in four verbs

- **Acquire**: pull public postings from Greenhouse, Lever and Ashby boards through
  an auto-managed watchlist (plus Exa search, if you turn it on).
- **Rank**: every posting that passes your filters is ranked by your model target.
  The order is honest but coarse: likely fits first, likely no-matches last.
- **Assess**: a requirements-by-resume matrix for the top-ranked postings, in the background.
- **Present**: a localhost API and web UI; tailored resumes where every line shows its sources.

## Gigs

Each Gig has its own section, with its own version line. Core pages link to a Gig
only from this table (the docs mirror the code: core never imports a Gig).

| Gig | What it does | Version | Docs |
| --- | --- | --- | --- |
| Scout | Find jobs, assess them against your resume, tailor a resume | `find-jobs` 1, ships with GigAI | [Scout](scout/) |

Next: [Install GigAI](install/), then the [Scout quickstart](scout/quickstart/).
