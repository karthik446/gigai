---
title: Scout
description: The first Gig. Find jobs, assess them against your resume, tailor a resume.
---

**Scout** implements `find-jobs`, a job-search workflow. It ships with GigAI and
gets no special runtime treatment: it is one Gig among others GigAI can host.

- **Acquire**: pull public postings from the Greenhouse/Lever/Ashby
  applicant-tracking boards through an auto-managed watchlist, plus Exa
  search if you turn it on (it is off for a new setup).
- **Rank**: every posting that passes your filters (titles, location, the
  publication window, visa sponsorship, work mode) is ranked by the model
  target you already use, and results stream in as batches finish. The order
  is honest but coarse: *likely fits first, likely no-matches last*. It
  pre-filters hard blockers (a no-sponsorship or citizenship line, a
  clearance requirement) by moving those postings down, never by hiding
  them. It does not claim to put the best match first.
- **Assess**: build a requirements-by-resume matrix for the top-ranked
  postings, in the background, while you browse. Defaults to a local model
  target; hosted targets are only used when explicitly configured.
- **Present**: a localhost API and a small web UI show acquired and assessed
  postings. What your model target sees is
  exactly what leaves your machine for ranking and assessment; the other
  network traffic is listed in [Privacy and security](privacy/).

Everything Scout writes stays under your configured GigAI home and the bound
project's workpad. Tailored resumes (`gigai scout resume tailor`, `POST
/api/tailored-resumes`) are stored under the gig too (`scout/<project>/resumes/`,
ephemeral pasted-resume runs under `ephemeral/`) and contain resume-derived
text by design. Tailored resumes are drafts: review each line; every line shows its sources.
Two confirming live runs on the release candidate (155 and 151 lines) found no fabricated facts
once one judge-flagged plural ("Kubernetes platforms" for the source's "Kubernetes platform") was
reviewed as a wording difference, not a new fact; 1 minor precision flag in each run (0.6% and 0.66% of lines).

## Where to go next

- [Quickstart](quickstart/): from zero to a running Scout.
- [Privacy and security](privacy/): read this before you add a resume.
- [Resume and PDF](resume/): preparing a resume, tailoring, Resume display.
- [Update sources](sources/) and [Configuration](configuration/): the company store, `find-jobs.json`, Exa.
- [For agents](agents/): scripting Scout through the CLI and the local API.
- [Known limitations](limitations/) and [Roadmap](roadmap/).
