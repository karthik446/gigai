---
title: Scout
description: The first Gig. Find jobs, assess them against your resume, tailor a resume.
---

**Scout** implements `find-jobs`, a job-search workflow. It ships with GigAI and
gets no special runtime treatment: it is one Gig among others GigAI can host.

- **Acquire**: pull public postings from Greenhouse, Lever and Ashby boards through
  an auto-managed watchlist (plus Exa search, if you turn it on).
- **Rank**: every posting that passes your filters is ranked by your model target.
  The order is honest but coarse: likely fits first, likely no-matches last.
- **Assess**: a requirements-by-resume matrix for the top-ranked postings, in the background.
- **Present**: a localhost API and web UI; tailored resumes where every line shows its sources.

Start with the [quickstart](quickstart/), and read [Privacy and security](privacy/)
before you add a resume.
