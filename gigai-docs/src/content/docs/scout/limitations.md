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
