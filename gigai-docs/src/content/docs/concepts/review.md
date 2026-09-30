---
title: Review
description: How GigAI keeps model output reviewable instead of trusting one response.
---

GigAI does not treat one model response as proof of correctness. What that means today:

- **Proposal and approval.** A Gig's changes go through a proposal/approval
  lifecycle (`gigai proposals`, `gigai approve`, `gigai reject`, `gigai revise`), recorded in the journal.
- **Inspectable runs.** Model calls, reviews, and results are recorded as
  durable Run state you can read back (see [Runs and the journal](../runs/)).
- **Output you are asked to review.** A Gig can present model output as a draft.
  For example, a tailored resume is a draft: review each line; every line shows
  its sources.
