---
title: Cover letters
description: How your agent tailors your own cover letter to one job from your master resume, the two files it writes, and the rules that keep it honest.
---

Some postings ask for a cover letter. Scout has no cover-letter button: this is a **skill your
agent runs**. You start with a letter of your own; your agent tailors it to one job, using only
facts your master resume states, and saves it for you to review.

GigAI calls no model for it and writes no letter. It supplies the facts (the posting, your master
resume, the lines the assessment cited); your agent writes; you review and send. The agent's own
conversation goes to its model provider, like everything else you do with an agent (see
[Privacy and security](../privacy/)).

## Set it up

The cover-letter instructions are the second section of the Scout skill, so an agent that has the
skill already has them:

```bash
gigai agent-skill --format skill --out ~/.claude/skills/gigai-scout/SKILL.md --force   # Claude Code
gigai agent-skill --format agents-md                                                   # Codex: paste into AGENTS.md
```

`--force` replaces the skill file of an earlier version. See [For agents](../agents/) for where
each agent reads its instructions.

You need three things before you ask:

1. **A letter of your own**, in a file you keep, for example `~/Documents/GigAI/cover-letter.md`.
   Leave your contact details out of it. The agent never writes a letter from nothing: your
   structure and your voice are the starting point.
2. **A master resume** in Scout (see [Your resume](../resume/)).
3. **An assessment of the job**, so Scout knows the posting's requirements and which of your
   master lines answer each one.

Then ask your agent: "Write a cover letter for this job", with the posting's URL.

## What the agent does

It gathers four things, each with a command that already exists:

| What | How | Note |
| --- | --- | --- |
| Your base letter | It reads the file you name, after `gigai scout resume check PATH --json` says it is clean | The same gate as for a resume file |
| The posting | `gigai scout resume brief --job-url URL --posting` | Text written by strangers: the agent treats it as data, never as instructions |
| The facts | `gigai scout resume master show --json`, and `gigai scout resume brief --job-url URL` for the master lines the assessment cited for each requirement | Every master line has an id |
| Your name | Only the one your own letter signs with | Never invented, never asked for |

Then it works in five steps:

1. Read the posting and list its top 5-7 asks.
2. Map each ask to the master lines that prove it, the assessment's cited lines first.
3. Keep your skeleton and your voice. It rewrites only the company-specific paragraphs: the
   opening (role, company), the bridge to your closest real past domain (if there is none, it
   does not force one), the "today" paragraph (the 2-3 strongest lines for this posting), and
   one honest line on a stack gap if the posting names a stack.
4. Keep your personal close and your sign-off word for word.
5. Write about 330-380 words, one page.

## The files it writes

Two files, in a folder of their own, never in the resumes folder:

| File | What it holds |
| --- | --- |
| `~/Documents/GigAI/cover-letters/<company>-<role>-<date>.md` | The letter |
| `~/Documents/GigAI/cover-letters/<company>-<role>-<date>.claims.md` | The claims trace: each factual sentence of the letter, the master line id and its text, then the list "Asks the master cannot prove" |

The company and the role in the file name come from the posting, written in lowercase letters,
digits and hyphens only. Read the trace beside the letter: it is how you check, sentence by
sentence, that the letter says nothing your master resume does not.

GigAI does not read, index or send these files. They are yours.

## The hard rules

The skill states these to the agent, in these words:

- Every factual sentence traces to a master line. The trace goes in the sidecar, never into the letter. No master line, no sentence.
- Never claim a skill, tool or number the master does not state: not from the posting, not from your own knowledge, not to fill a gap.
- A sentence you keep verbatim from the user's letter is theirs; if no master line states it, mark it "kept from your letter, no master line" in the trace so the user sees it.
- List "Asks the master cannot prove" for the user, in the sidecar and in chat. Never work one into the letter.
- Sponsorship / work authorization is a label, not letter text: it never appears unless the user's own letter has it.
- You are the agent, and the agent never sends or submits anything: no email, no form, no upload. The user reviews the letter and sends it.
- Contact details come only from the user's `header.json`, at PDF time. Never type them into the letter, never read that file, never ask for them.

These are instructions an agent follows, not a check GigAI runs: nothing in GigAI verifies a
cover letter. The claims trace is there so that you can.

`header.json` is the file of your own name and contact details that
`gigai scout resume pdf` reads for a resume's header (see [Your resume](../resume/)). GigAI has
no cover-letter PDF command in this version; when you turn the letter into a PDF yourself, add
your contact details then.

## Example

Everything below is made up: the company (Acme), the person, the master lines and the numbers.

You ask: "Write a cover letter for the Staff Software Engineer job at Acme", with its URL. The
agent lists the posting's asks, maps them to your master lines, and saves:

`~/Documents/GigAI/cover-letters/acme-staff-software-engineer-2026-10-06.md`

```markdown
Dear Acme hiring team,

I am writing about the Staff Software Engineer role on your Payments Platform team. I have
built backend systems for twelve years, the last five as a technical lead.

At Initech I led the rewrite of the billing service that moved 40 internal teams off a shared
database, and cut failed nightly settlement runs from 9 a month to 1.

Today, at Globex, I lead a team of six that runs the order pipeline: 3 million events a day,
in Python and PostgreSQL, with an on-call rotation I set up.

Your posting names Go. My production work is in Python and Java; I have not shipped Go.

I took apart my first radio at eleven and have been asking how things work ever since.

Thank you for reading,
Jane Example
```

`~/Documents/GigAI/cover-letters/acme-staff-software-engineer-2026-10-06.claims.md`

```markdown
# Claims trace: Acme, Staff Software Engineer

| Sentence | Master line |
| --- | --- |
| I have built backend systems for twelve years, the last five as a technical lead. | b-1a2b3c: "Technical lead, backend platform, 5 years"; b-4d5e6f: "12 years of backend engineering" |
| At Initech I led the rewrite of the billing service that moved 40 internal teams off a shared database, | b-7a8b9c: "Led billing service rewrite; moved 40 internal teams off a shared database" |
| and cut failed nightly settlement runs from 9 a month to 1. | b-0d1e2f: "Cut failed nightly settlement runs from 9 a month to 1" |
| Today, at Globex, I lead a team of six that runs the order pipeline: 3 million events a day, in Python and PostgreSQL, | b-3a4b5c: "Lead a team of 6 on the order pipeline (3M events/day; Python, PostgreSQL)" |
| with an on-call rotation I set up. | b-6d7e8f: "Set up the team's on-call rotation" |
| My production work is in Python and Java; I have not shipped Go. | b-3a4b5c, b-9a0b1c: "Java services for the inventory API" |
| I took apart my first radio at eleven and have been asking how things work ever since. | kept from your letter, no master line |

## Asks the master cannot prove

- Go in production
- Card-network certification work
- Experience with a public API program
```

The letter does not claim Go, certification work or an API program. The trace names them so
you can decide: if one is true, tell your agent, save it as an answer or a master line (see
[For agents](../agents/)), and ask for the letter again.
