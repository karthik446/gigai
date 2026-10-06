---
title: Privacy and security
description: What GigAI stores, what leaves your machine, and to whom.
---

**GigAI never stores your name, email, phone, address or links.**

The limits of that promise, plainly:

- **You type them only when you make a PDF, and GigAI forgets them right after.** The Generate
  PDF form sends them with that one request; they fill that PDF's header and are not written to a
  file, a log or a reply. Your browser may offer to remember them for autofill. That is your
  browser's own store, not GigAI's.
- **You may keep them in a file of your own instead.** `~/Documents/GigAI/header.json` is yours:
  GigAI only reads it to fill the Generate PDF form or a PDF you make with
  `gigai scout resume pdf --out`, and never copies it into its store, a log or a model prompt.
  It writes that file only when you press "Save these details" in the Generate PDF form.
  See [Your header file](../resume/#your-header-file).
- **A resume you add is stored without them.** The import removes the name line and the contact
  lines (email, phone, address, links) and discards them. It works on patterns. It can't catch
  personal details elsewhere in the text (a first line that holds both a title and your name, or
  contact details inside a sentence), so keep those out. The setup wizard also runs a local check
  (no model) that lists any email, phone, linkedin.com/github.com link or street address it
  spots; it can miss things.
- **Older copies can remain in GigAI's local history on your computer.** Versions before
  0.1.10.7 stored the contact lines of your resume and the name and contact line you saved under
  Resume display. The first start after you upgrade removes them from the current files, once,
  and tells you what it removed (`gigai scout privacy` prints the counts again). It does not
  rewrite history: Scout keeps earlier revisions of its records in a local history (a git
  journal in its workpad, on this computer), and the earlier copies of your resume are still in
  it. A tailored resume made by a version before 0.1.10 can also still hold the header it was
  made with; tailor that job again to replace it. None of this leaves your computer by itself.
- **Everything else you give GigAI stays on your computer unless you or your agent send it
  somewhere**: your answers and stories, your application notes, and the body of your resume.
- **GigAI's own model calls send your resume (without the contact lines) and your answers to the
  model you picked** (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider). With
  Ollama the resume stays on this machine. The sections below list exactly what is sent.
- **Your search settings are not contact data, and they are stored.** The city or area you set
  for the search, your work mode, the countries you can work from and whether you need
  sponsorship are saved as settings and go to your model with an assessment.

**Anything GigAI gives your agent is sent to that agent's model provider. Agents get no contact data from GigAI, but an agent with shell access can read local files.**

[For agents](../agents/#the-security-model) explains what that means when you let an AI agent
use Scout.

The setup wizard, the Assessments page, Settings > Profiles and `gigai scout resume add` all show
a short form of this note where a resume enters Scout.

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
- **Your answers and stories go with an assessment.** The answers you gave to earlier questions are part of what an assessment
  sends (one you start on a job, each one the background pipeline makes, and each one a search run makes), so a question is not asked twice: your answers to that job's own questions in full, a one-line summary of each of your other answers (at most 40), and the few stories that fit the posting (at most 3, one line each).
  They are yours, not one profile's: every profile's assessment sends them. A tailoring sends your answers and the stories that fit the posting too.
  An answer or a story that holds an email, a phone number, a link or a street address is refused when you save it, and
  anything of that kind in an older answer is removed again before it is sent. The check works on shapes; it does not recognise a name.
- **Posting text is fenced.** Every prompt Scout builds puts the posting's words inside a marked block with one rule: it is data to
  read, never instructions to follow. A posting is written by strangers; this lowers the chance that text in it steers your model. It
  does not remove it.
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
| Tailoring (switched off in 0.1.11) | The posting text and your resume (name and contact lines removed) | Your model target |
| Update sources | Plain public requests, no key or login (your IP is visible) | Greenhouse, Lever, Ashby |
| Exa search (off by default) | Your target roles, a start date, a country code, your Exa key | Exa |

Ranking scores are cached on disk under `<home>/cache/scout/rank/scores/`
(a score, up to two short reasons and any blockers per posting, no resume
text); the cache is safe to delete. What ranking costs is whatever your
model target charges; a run makes a bounded number of ranking calls, and
postings past that bound stay unranked and keep date order. Every model
call Scout makes is recorded on your computer (kind, model, tokens, time,
cost when the provider reports one; no text): Settings > Model usage and
`gigai scout metrics` show the averages.

## Assessing many postings

A posting is assessed for the first time only when you approve it. **Assess
these** on the Jobs page, `gigai scout new` and `gigai scout jobs assess` all ask
first, with the count and an estimate from your own past calls, and assess
only on a yes: one model call per posting, a few at a time. On a past run's
page, **Assess all new** assesses that run's remaining postings in the
background: one model call per posting, 4 at a time, with Cancel, and a second
click resumes without redoing finished ones. Calls on the `codex_cli` and
`claude_cli` targets use your own CLI login and its usage limits; Scout passes
them no API key. With a hosted model target each posting's assessment sends
your resume and that posting to that provider, exactly as any other assessment
does. A token estimate is shown only once a call has been measured.

The background pipeline (tailor, assess the tailored resume, Scout ATS score,
Scout label) is off by default in 0.1.11. In a home that explicitly enabled it, it makes
model calls by itself, only for jobs you engaged with, and at most 40 a day; ranking in the background makes at most 100 a day. Both
limits are in Settings > Background pipeline. Each of those calls sends what
the table above lists for its step.

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
project's workpad. Each job's resume is stored under the gig too
(`scout/<project>/resumes/`, ephemeral pasted-resume runs under `ephemeral/`)
and contain resume-derived text by design.

Your answers and stories stay local too: the text of each answer is a record in the project's workpad, its tag, dates,
writer and the jobs that used it are in `scout/<project>/story_bank/answers.json`, and the stories are in
`scout/<project>/story_bank/stories.json`. Deleting one takes it out of use; for an answer, the workpad's history keeps
the older revision of the record it was in.

The background pipeline's own file (`scout/<project>/pipeline/pipeline.sqlite`) holds ids, codes, numbers and times,
never posting, resume or answer text.

## The local API

Scout's API listens on this computer only, and every request must carry a `Host` of `127.0.0.1:<port>` or
`localhost:<port>`, so a web page you visit can't read it through your browser. There is no login: any program you run
on this computer can call it. As a backstop, every JSON reply is scanned and text shaped like an email address, a phone
number, a profile link or a street address in your own fields is replaced by a marker such as `[removed: email]`
(posting text is left as published). The scan works on shapes and can miss things.
