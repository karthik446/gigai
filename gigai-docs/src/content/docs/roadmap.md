---
title: Roadmap
description: What is planned for GigAI, Scout and future Gigs. No dates.
---

This is what is planned, in rough priority order, with no dates. Anything already released is in the [changelog](../changelog/) and is not listed here. Plans change.

## Next

**Scout**

- Preparing for interviews: research the job, plan the preparation, produce it and review it, using your own model CLI. It stays hidden until it has been tested on real postings.
- Punchier tailored summaries: up to two short sentences, most relevant first, without losing anything from your resume.
- The posting's own keywords in the tailored summary.
- Catch personal details the name and contact-line removal misses (a first line that holds a title and a name, contact details inside a sentence).
- Import PDF and DOCX resumes directly.

**GigAI core**

- Setup that works with only Ollama or OpenRouter, with no Codex or Claude CLI installed.
- The setup wizard checks that the model CLI you chose is installed and logged in.
- Faster settings load.
- More consistent tailor judging.

## Later

- A firm boundary between the core and Gigs, enforced by an automated check.
- Scout in its own repository, with its docs.
- A second Gig.
- A command that reviews your stored runs and improves Scout's prompts and context, without changing the software.
- An MCP server generated from the API description, for clients without a shell.
- Docs: a configuration reference generated from the schemas, and a weekly external-link check.

## Ideas, not scheduled

- Cover letters using the PDF renderer, and resume themes with a safe fallback.
- Cheaper local ranking.
- Interviewer research (opt-in, public professional information only), round debriefs and calendar support.
- Alpine and other musl Linux support (the PDF renderer has no musl wheel).

Known gaps today are on the Scout Known limitations page.
