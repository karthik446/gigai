---
title: Real-run check and timings (0.1.11)
description: A real install of the 0.1.11 release candidate on a real home. What was installed, the machine, the size of the home, and the seconds and model calls of each step.
---

The [accuracy page](../accuracy-0-1-11/) says how accurate the picked resume is on postings the
product had never seen. This page is the other check: the release candidate installed from git on a
real machine, run on a real home, step by step, with the time each step took. GigAI 0.1.11 is an alpha.

Nothing on this page is posting text, resume text, an answer, a company name or a path. It holds
numbers and kinds of role only.

## How this check was run

A real home (not a scratch one), the candidate installed from git on the person's own machine, run by
the model that coordinates the project, step by step. It is not a CI job and it is not a fixture: the
home is the one the person uses. Seconds are wall time as the person would feel them. A model call is
one call to the model CLI. Two postings found on the web and not in the person's store were assessed by their URL: a careers-site
link and a job-board link. Numbers and role kinds only.

The check ran in two parts on the same build, the second with the pipeline off. The upgrade, the
sources update, `scout new`, the resume store and the master line edit were **not run** on this home
for this release: there is no migration in 0.1.11, and the rest are covered by the test suite, not by this page.

## What was installed

| | |
| --- | --- |
| Version | 0.1.11 (the release candidate, before the fix described below) |
| Commit | 48a2db1c |
| How | from git, `uv tool install` of the release candidate |

## The machine

| | |
| --- | --- |
| Model | Mac mini, Apple M4 Pro |
| OS | macOS 26.5.2 |
| Cores | 14 (10 performance, 4 efficiency) |
| RAM | 64 GB |

## The size of the home

Counts only.

| | Count |
| --- | --- |
| Files | 85,091 (2.1 GB) |
| Postings matched for the first profile | 404 |
| Boards (companies stored) | 10,349 |
| Profiles | 2 (404 postings matched for the first, 398 for the second) |
| Jobs | 2 carried a stored 0.1.10 tailored resume |
| Master lines | 32 bullet lines (revision 5) |

## The steps

The home was copied first (35.2 s, 85,091 files on both sides). Every row was run on the person's own
home. The background pipeline was off for the last five rows, as it is by default in this version.

| Step | What it does | Seconds | Model calls | Result |
| --- | --- | --- | --- | --- |
| Install | `uv tool install` of the candidate from git | 3.4 | 0 | 0.1.10.11 to 0.1.11 |
| `gigai doctor` | Checks the install | 0.57 | 0 | Pass; names `claude-opus-5-5` for Claude and shows a notice for Codex |
| Start the server | `gigai scout run` until the server answers | 2.5 | 0 | ok |
| Open the job list (first load) | Loads the list in the UI | 2.47 | 0 | 50 rows; the list's own call took 2.2 s; over the 1 s bar |
| Open the job list (warm reload) | Reloads the list | 1.75 | 0 | The list's own call took 1.6 s (0.50 s when called alone); over the 1 s bar |
| Open a job | Loads one stored job page | 0.36 (slowest call) | 0 | All sections shown; the notice shows on a Codex assessment |
| Assess a job found on the web: a careers-site link (first build) | Fetches the posting by its link, then assesses it | 17.3 | 1 (Opus) | **Wrong**: see "What did not work" |
| Assess a job found on the web: a job-board link | Fetches the posting by its link, then assesses it | 31.5 | 1 (Opus) | Right: 10 requirement rows, held on 3 questions; model asked and model used were both `claude-opus-5-5` |
| Assessments view and that job's page | Lists the assessed jobs and opens the page | under 1 | 0 | Both listed; the page shows questions, requirements, suggestions and the agent brief button |
| Save preferences | Saves the preferences | 1.42 | 0 | Saved (200); over the 1 s bar |
| Assess one stored job again on Opus | One assessment on `claude-opus-5-5` | 49.1 | 1 (Opus) | Matched, 6 of 6 requirements; no tailoring call, pipeline 0 steps |
| The picked resume | Builds the resume from the assessment | 0.62 | 0 | Picked by the assessment: 20 lines, 2 pages, gate ready; lines word for word (Skills reordered) |
| Apply / PDF | Makes the PDF of the stored job resume | 0.44 | 0 | 2 pages, 52 KB |
| Resume brief | Writes the brief for the agent | 0.77 | 0 | 159 lines |
| The job page for that job | Opens the page in the browser | about 1 | 0 | Matched; "Picked by the assessment from your master"; Apply gives the PDF; the pipeline panel says off |

Not measured on this home: the first start with an upgrade, the sources update, `scout new` (a page of
50), the resume store, and the master line edit.

## What is slow

Measured on this home, as measured, not fixed in this release:

| What | Seconds |
| --- | --- |
| Job list | 1.75 to 2.5 |
| Save preferences | 1.42 |
| Assess one job (wall time, one model call) | 49 |

Everything else in the table was under 1 s except the two web-link assessments (17.3 s and 31.5 s),
which include fetching the posting and one model call. The job list's own call is 0.50 s when called
alone, so most of its time was spent while the page loaded. The job list is the one that matters most;
its fix is in 0.1.11.1.

## What was checked by eye

No console errors in Chrome on any page visited. The job page showed the Matched state, what was picked
from the master, the notice on a Codex assessment, and the questions held on the job-board link
assessment.

## What did not work

- **The first build read the careers-site link wrong.** It fetched the site's menu and footer instead of
  the posting and answered "matched" on nothing. This was fixed before release: a generic page is refused
  before any model call unless the page names its job board, and is then resolved through that board. The
  fix was re-checked on the same link against the same home: the posting was fetched through its job board's API (company and location filled, 6.2k characters), 11 requirement rows were read, one Opus call took 39.1 s, and the job was held on 2 sensible questions; no false match.
- **The job list is over 1 s** (1.75 to 2.5 s); saving preferences is over 1 s (1.42 s).
- **The page for a job assessed by its link** lives under the Assessments view; the Jobs address for it
  showed "not found", and also when the address differed by a trailing slash.
- **The job page's "what one assessment sends" line** names the default model target, not the model that
  made the assessment shown.
- **The pipeline panel** was still drawn when the pipeline was off.
- **`gigai scout resume tailor`** still exists in this release and is switched off by default.
