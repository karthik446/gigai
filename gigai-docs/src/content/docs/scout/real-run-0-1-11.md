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
one call to the model CLI. Besides `scout new`, one posting found on the web and not in the person's store was assessed by its URL
(`gigai scout assess --job-url`, and from the job page's paste/URL box). Numbers and role kinds only.

## What was installed

| | |
| --- | --- |
| Version | [FILL] |
| Commit | [FILL] |
| How | from git, `uv tool install` of the release candidate |

## The machine

| | |
| --- | --- |
| Model | [FILL] |
| OS | [FILL] |
| Cores | [FILL] |
| RAM | [FILL] |

## The size of the home

Counts only.

| | Count |
| --- | --- |
| Postings | [FILL] |
| Boards | [FILL] |
| Profiles | [FILL] |
| Jobs | [FILL] |
| Master lines | [FILL] |

## The steps

| Step | What it does | Seconds | Model calls | Result |
| --- | --- | --- | --- | --- |
| Install | `uv tool install` of the candidate from git | [FILL] | 0 | [FILL] |
| First start | Opens the home; upgrades the 0.1.10 pipeline to 0.1.11 and writes its backup | [FILL] | [FILL] | [FILL] |
| Sources update | Fetches the boards and stores new postings | [FILL] | [FILL] | [FILL] |
| `scout new` (a page of 50) | Lists the newest 50 postings | [FILL] | [FILL] | [FILL] |
| Open the job list | Loads the list in the UI | [FILL] | [FILL] | [FILL] |
| Open a job | Loads one job page | [FILL] | [FILL] | [FILL] |
| Assess one job on Opus | One assessment on `claude-opus-5-5` (tokens: [FILL] in, [FILL] out) | [FILL] | [FILL] | [FILL] |
| The picked resume | Builds the resume from the assessment | [FILL] | [FILL] | [FILL] |
| The job page with the model notice | Opens the job page for an assessment made with a model that carries a notice | [FILL] | [FILL] | [FILL] |
| Resume brief | Writes the brief for the agent | [FILL] | [FILL] | [FILL] |
| Resume store | Stores the agent's resume through the hand-back check | [FILL] | [FILL] | [FILL] |
| Apply / PDF | Makes the PDF | [FILL] | [FILL] | [FILL] |
| Assess a job found on the web by URL | Fetches the posting by its URL, then assesses it (fetch [FILL] s, assess [FILL] s, tokens: [FILL] in, [FILL] out) | [FILL] | [FILL] | [FILL] |
| The same job: picked resume, job page, store, PDF | The picked resume, the job page, the resume store and the PDF for that job (seconds per step: [FILL]) | [FILL] | [FILL] | [FILL] |
| Master line edit | Edits one master line | [FILL] | [FILL] | [FILL] |
| Save preferences | Saves the preferences | [FILL] | [FILL] | [FILL] |
| `gigai doctor` | Checks the install | [FILL] | [FILL] | [FILL] |

## What was checked by eye

[FILL]

## What did not work

[FILL]
