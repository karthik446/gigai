---
title: Privacy and security
description: Exactly what leaves your machine, and to whom.
---

**Scout removes your name and contact lines (email, phone, address, links)
before sending your resume to the model you pick** (Codex -> OpenAI, Claude ->
Anthropic, OpenRouter -> your provider), and adds them back only in your PDF,
on this machine. It can't catch personal details elsewhere in the text (a first
line that holds both a title and your name, or contact details inside a
sentence), so keep those out. With Ollama the resume stays on this machine. The
contact line printed on your PDF lives in Settings > Resume display. The setup
wizard, the Assessments page, Settings > Profiles and `gigai scout resume add`
all show this note, and the wizard also runs a local check (no model) that lists
any email, phone, linkedin.com/github.com link or street address it spots; it
can miss things. The Resume display fields (name, title, contact line) are
never sent to a model or the network: only the PDF renderer and the
settings/PDF API read them.

## Scout has no service of its own

Ranking and assessment both run on
the model target you configured (`ollama_local`, `codex_cli`, `claude_cli` or
`openrouter_api`, whichever the search's `default_model_target` names). There
is no ranking service, no extra key, and no extra third party for ranking
or assessment. Scout runs `codex` with its shell tool and memories turned off, and `claude` with your settings, MCP servers and tools turned off, so a model call cannot read your local files or CLI memories. What your machine sends to the model target is exactly:

- **Ranking** sends the target, one batch at a time, one short line per
  posting (title, company, location and countries, seniority level, minimum
  years, the skills found in the posting's requirements section, and hints
  like "no sponsorship" or "clearance") plus a **compact digest of your
  resume**: your target titles, countries, visa need and location, the
  job titles, skills and domain found in your resume, and an experience-years
  figure. Scout leaves contact lines (name, email, phone, address, links) out
  of the ranking digest where it can recognize them; keep other personal
  details out of the resume you add. The full resume and the
  full posting text are not sent for ranking.
- **Assessment and tailoring** send your resume text with the name and contact lines removed: an assessment sends the posting text and your resume for each posting
  being assessed, including each posting "Assess all new" assesses, with whether you need sponsorship, the countries you can
  work from, your location as you wrote it, your work mode (remote, hybrid or on-site) and your target titles (a search run's assessments send the same). A pasted resume is used for that assessment only: its full text is never saved and never sent anywhere but your assessment model; the stored result keeps short evidence quotes on your machine.
- **Your answers go with an assessment, per profile.** The answers you gave to earlier questions are part of what an assessment
  sends (one you start on a job, and each one a search run makes), so a question is not asked twice: each answer in full, plus a one-line summary of each story bank entry (at most 40).
  Only the answers of the profile being assessed are sent, and those of the one profile it is set to share with; another
  profile's answers never are. An answer that holds an email, a phone number, a link, a street address or the name saved in
  Resume display is refused when you save it, and anything of that kind in an older answer is removed again before it is sent.
- **With a local Ollama target nothing leaves the machine.** Scout only
  talks to Ollama on a numeric loopback address (`127.0.0.1`).
- With `codex_cli` (the Codex CLI sends it to OpenAI), `claude_cli` (the
  Claude Code CLI sends it to Anthropic) or `openrouter_api`, that provider
  sees what is listed above under its own terms. The two CLIs add their own
  instructions to each call; for an assessment the Claude Code CLI also loads
  your own Claude Code settings, as any `claude -p` call does.

| Step | What is sent | To |
| --- | --- | --- |
| Ranking | One short line per posting + a compact digest of your resume (titles, skills, years), batch by batch | Your model target |
| Assessment | The posting text and your resume (name and contact lines removed), per assessed posting | Your model target |
| Tailoring | The posting text and your resume (name and contact lines removed) | Your model target |
| Update sources | Plain public requests, no key or login (your IP is visible) | Greenhouse, Lever, Ashby |
| Exa search (off by default) | Your target roles, a start date, a country code, your Exa key | Exa |

Ranking scores are cached on disk under `<home>/cache/scout/rank/scores/`
(a score, up to two short reasons and any blockers per posting, no resume
text); the cache is safe to delete. What ranking costs is whatever your
model target charges; a run makes a bounded number of ranking calls, and
postings past that bound stay unranked and keep date order.

## Assess all new

A run assesses its top-ranked postings automatically (the
run's "Full assessments" setting). On a finished run, **Assess all new**
assesses the rest in the background: one model call per posting, 4 at a time,
with Cancel, and a second click resumes without redoing finished ones. The run
dialog's "All new postings" choice does the same during the run and is the
starting choice for the `ollama_local`, `codex_cli` and `claude_cli` targets;
`openrouter_api` starts on a number. Runs on the `codex_cli` and
`claude_cli` targets use your own CLI login and its usage limits; Scout passes
them no API key. With a hosted model target each posting's assessment sends
your resume and that posting to that provider, exactly as any other assessment
does. A time estimate is shown only once a per-call time has been measured.

## Network traffic besides your model

Scout makes these other requests:

- Job boards (Greenhouse, Lever, Ashby) get plain public requests without a
  key or login and see your IP address; nothing else about you is sent.
- Exa, only if enabled in your sources, receives the search query (your
  target roles), a start date, a country code and your Exa key.
- The setup interview itself makes no network request; it saves your answers
  on your machine. The separate Discover companies button, only if an OpenAI
  key is set, sends OpenAI a search query built from your setup answers
  (roles, countries, work mode and city, whether you need sponsorship, titles
  and industries to avoid or prefer, company stage, stack, and the names of
  companies to exclude) with your OpenAI key. It also downloads a public US
  Department of Labor H-1B data file and sends public requests to guessed
  board addresses.
- Nothing else.

## What stays local

Everything Scout writes stays under your configured GigAI home and the bound
project's workpad. Tailored resumes are stored under the gig too
(`scout/<project>/resumes/`, ephemeral pasted-resume runs under `ephemeral/`)
and contain resume-derived text by design.

The story bank (every answered question and added story, per profile) stays local too: the answers are records in the
project's workpad, and who owns each one, its tag, dates and the jobs that used it are in
`scout/<project>/story_bank/bank.json`. Deleting an entry takes it out of use; the workpad's history keeps the older
revision of the record it was in.
