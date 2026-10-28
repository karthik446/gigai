---
title: Known limitations
description: What Scout does not do yet.
---

- Alpha: expect rough edges (see [Status](../../#status)).
- macOS and Linux only.
- Alpine/musl Linux isn't supported yet: the PDF renderer (Typst) has no musl wheel, so installing there fails.
- Filters default to the US (`countries` starts as `["US"]`); other countries
  can be set in the setup wizard or `find-jobs.json`.
- Assessments saved before the Lever fix stay as they were until you
  re-assess them (an old "Matched" on a Lever posting may not hold).
- The run dialog does not save "All new postings" as your default; set it in
  `find-jobs.json` (`"default_assess_cap": "all"`) if you want it to stick.
- A Workday posting listed as "30+ days ago" shows as undated until its
  detail is read, which happens only for titles that match your profiles.
- A Workday board is read up to 100 list pages (2,000 postings); a larger
  board is stored as far as read and `gigai scout sources status` prints a
  `partial:` line for it.
- A Workday board gets at most 200 detail requests in one update; the rest
  are read in a later update, so the first update after upgrading to
  0.1.11.12 can take more than one pass.
- For the 49 Workday boards in the catalog, the H-1B figure beside a posting
  is the employer's certified H-1B LCA count for software occupations, FY2026
  through Q3, from the public US Department of Labor data, not a USCIS
  approvals count, and it has no denials number; the chip's tooltip still
  reads "(USCIS)" for these boards.
- A posting that decides sponsorship case by case is recorded as
  `case_by_case`, and the Jobs list marks it with that label as written.
- After the upgrade every stored fit is counted again once (the shown fit is
  now capped by the must-have rows), and a posting's stored requirement list
  is read again on its next assessment.
