---
title: What GigAI provides
description: A local, user-controlled agent runtime, and Scout, its first Gig.
---

GigAI is a local, user-controlled agent runtime. It keeps configuration,
credentials, and work state under a home directory you choose, and makes every
model call, review, and result inspectable rather than treating one model
response as proof of correctness.

A **Gig** is a self-contained goal-graph package built on top of GigAI core.
The dependency is meant to run in one direction only: **a Gig imports GigAI
core, and GigAI core is meant not to import a Gig.** Today Scout ships in the
same package and core still imports some Scout modules (the `gigai scout`
command groups in `cli.py`, `run.py`, `default_init.py` and a few others), so
this is the direction Gigs are built for, not yet a guarantee. Gigs are
portable, reviewable units of work, not plugins the runtime depends on.

## What you get

| | GigAI core | Scout (the first Gig) |
| --- | --- | --- |
| What it is | The runtime: setup, config, secrets, journal, proposal/approval lifecycle, model targets | A job-search workflow: `find-jobs` |
| You use it to | Bind a project, register and run Gigs, read runs and failures | Find postings, rank and assess them against your resume, tailor a resume, download a PDF |
| Runs on | Your machine, with the model CLI you already use (Codex or Claude Code; for Ollama or OpenRouter see [Install](install/#requirements)) | Same, plus public job boards (Greenhouse, Lever, Ashby) |
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
only from this table (the docs follow the same direction: a Gig imports core, not the reverse).

| Gig | What it does | Version | Docs |
| --- | --- | --- | --- |
| Scout | Find jobs, assess them against your resume, tailor a resume | `find-jobs` 1, ships with GigAI | [Scout](scout/) |

## Status

GigAI is an alpha: it has been through hands-on testing by the maintainer (two UAT rounds, 2026-09-27 and 2026-09-29); expect rough edges. Scout's `find-jobs` workflow is implemented and covered by the test suite, including a deterministic end-to-end path from acquisition through the present API. These docs describe the release they are versioned with; see the [Changelog](changelog/) for version-specific notes.

## License

Apache-2.0. See [LICENSE](https://github.com/karthik446/gigai/blob/main/LICENSE).

Next: [Install GigAI](install/), then the [Scout quickstart](scout/quickstart/).
