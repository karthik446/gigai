---
title: What Scout's numbers and labels mean
description: What the rank, the verdict, the Scout label and the Scout ATS score are, who made each one, and what it is not.
---

Scout shows five things beside a job. Each one is made a different way, and none of them is a
promise about an employer. The "?" beside each one in Scout opens this page.

| | Made by | Costs a model call | Says |
| --- | --- | --- | --- |
| [Rank](#rank) | Your model, from a short line per posting | Yes, in batches | Where the posting goes in the list |
| [Verdict](#verdict) | Your model, from the full posting and your resume | Yes, one per posting | Matched, needs your answers, or not a match |
| [Fit](#fit) | Scout, from the assessment's table of requirements | No | How many of the posting's requirements are met |
| [Scout label](#scout-label) | Scout's own rule, no model | No | Recommended, or needs attention |
| [Scout ATS score](#scout-ats-score) | Scout's own local check, no model | No | How well the resume PDF reads and matches |

## Rank

A number from 0 to 100, with a band: **likely fit** (70 and up), **possible fit** (40 to 69) or
**likely no-match** (below 40). A posting your model named a blocker for (a clearance, a
no-sponsorship line) reads **blocker** and goes to the end of the list. It is never hidden.

> A rank is your model's guess from the posting and your resume. It orders the list; it does not
> say you match. Assess a posting for that.

How it is made: your model sees one short line per posting (title, company, location, level,
skills) and a compact digest of your resume, in batches. The full posting and the full resume are
not sent for ranking. [Privacy and security](../privacy/) lists exactly what is sent.

A posting with no rank yet reads "not ranked yet". The background ranks new postings by itself
(the ones posted in the last 7 days, or first stored in them when a posting has no date; an
older one stays "not ranked yet"), up
to 100 model calls a day for all your profiles together (Settings > Background pipeline).
Ranking has its own switch (`rank.enabled` in the settings file, on by default): it runs while
the background pipeline, which tailors, is off.

## Verdict

One of three, from an assessment:

- **Matched**: your model found the posting's requirements met by your resume and answers.
- **Needs your answers**: it could not tell for some requirements and asks you. Answer the
  questions and assess again.
- **Not a match**: a requirement that can't be worked around is not met. The reason is shown.

> Your model's reading of the posting against your resume and answers. Check the posting yourself.

How it is made: one model call per posting. Your model gets the posting text, your resume with
the name and contact lines removed, your answers, and the few stories that fit the posting. It
fills a table of the posting's requirements, each one met, not met or unclear, with the line of
your resume or the answer it relied on. The verdict is read from that table.

A model can misread a posting. The table shows what it relied on for each requirement, so you
can check it. An assessment made before your settings, your answers or Scout's prompt changed
reads **Assessed with older settings**; the verdict still shows, and nothing is assessed again
until you ask.

## Fit

On the Jobs page each job has two chips under its title. **Fit** ("Fit 92% · 19/22") is the
share of requirements met, with the must-haves counted twice, then met of total: 19 of the 22
rows of the assessment's table are marked met. **Rank** ("Rank 92") is the rank above, or
"Not ranked yet". An assessed job's page shows both in the box at the top right.

The Fit chip is green only for a matched job; on any other job it is grey, so a high fit
beside "Needs your answers" is not a match yet. A posting too thin to have a table of
requirements has no fit. Fit is counted by Scout from the assessment: no model call of its
own.

## Scout label

**Recommended** or **needs attention**, shown after the background pipeline has run on a job. The
pipeline is off by default in 0.1.11, so with the default settings no job gets this label; it
appears only in a home that explicitly enabled the pipeline (which tailors a resume, assesses it
and sets the label).

> Scout's own suggestion from your settings, resume and answers. Not a prediction of what an employer will decide.

How it is made: a fixed rule, no model call. A job reads **recommended** only when all of these
hold, and **needs attention** otherwise, with the reasons listed:

- the assessment of the tailored resume says Matched;
- no question is still open;
- the Scout ATS score is at or above your minimum (Settings > Background pipeline; the default
  minimum is 0, so the score is shown and never blocks the label);
- the job's first assessment is not one made with older settings.

"Recommended" means those four checks passed. It does not mean the employer will agree, and it
does not replace reading the posting.

Next to the label (pipeline homes only), "73 → 91 after tailoring" compares the share of requirements met before and
after tailoring.

## Scout ATS score

A number from 0 to 100 for one resume PDF against one posting, with a line such as
"Scout ATS 84: parses cleanly · 9/11 key skills · missing: Terraform, SOC 2". Click the chip for
the breakdown.

> GigAI's own local check of how well this resume reads and matches the posting. Not any real ATS's score.

How it is made: on your computer, with no model call and no network. Three parts:

- **Parse fidelity (40 points)**: Scout reads the text back out of the PDF it made and compares it
  with the resume. Missing headings, split role lines or lost words cost points.
- **Keyword coverage (40 points)**: the skills the posting names, found in the resume. The chip
  lists the ones that are missing.
- **Format rules (20 points)**: a text layer, a single column, no tables, no images, embedded
  fonts, plain characters, standard headings, one or two pages, and the file name. Some failed
  rules also cap the total (a PDF with no text layer scores at most 10).

An applicant tracking system at a real company uses its own parser and its own rules, which Scout
cannot see. A high Scout ATS score means the PDF is easy to read by a program and names the
posting's skills. It does not mean a real system will rank you well. The keyword list comes from
the posting's words, so check the "missing" list against the posting: a skill can be listed as
missing when the posting only mentions it in passing.

## A resume and its PDF

> Every line comes from your resume, answers or stories. Read it before you send it.

Each line of the picked resume is a line of your master, word for word; nothing rewrites it. (A
resume tailored on 0.1.10, or by a home that enabled the pipeline, can hold a rewritten line that
cites the sources it came from; the wording of a rewritten line is still your model's.) A line you
or your agent typed is marked "Your words" and cites nothing: it is your text.
[Resume and PDF](../resume/) has the details.
