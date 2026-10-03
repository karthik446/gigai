---
title: For agents
description: Use Scout from your own AI agent. The daily workflow, setup, the security model, and the CLI and local API behind it.
---

Scout is built to be driven by your own AI agent (Claude Code, Codex and similar): you ask
"what are the new jobs?", the agent asks Scout, shows you a grid, asks you the open questions and
saves what you say. The browser UI is there when you want it: for reading a job, for tailoring
and for the PDF.

**Anything GigAI gives your agent is sent to that agent's model provider. Agents get no contact data from GigAI, but an agent with shell access can read local files.**

Read [The security model](#the-security-model) before you let an agent use Scout. The general
CLI discovery commands are on [For agents](../../agents/).

New here? [Use it from your agent](#use-it-from-your-agent) says what to install and what to
type for Claude Code, Codex and other agents. To have the agent do the whole setup, give it
[Start here](start/).

## The daily workflow

Every command below is local. A step that spends a model call says so, and none is made until
you say yes.

### 1. What is new

```sh
gigai scout new --json
```

This reads the stored postings (the background checks keep them fresh; no job board is asked)
and lists what is new since your last check, across all your active profiles. The first time it
looks back 7 days. At most 50 new postings are listed; the count covers all of them.

**The order of the grid.** Postings with a current assessment come first, then postings whose
only assessment is old, then postings not assessed yet. Inside each group: Scout's
`recommended` label first, then the verdict (matched, needs your answers, other, not a match),
then the rank score, then the newest. `gigai scout jobs list` and the Jobs page use the same
order.

**It asks before it assesses.** When new postings are not assessed yet, the reply carries a
question with the count and an estimate from your own past calls, for example "12 new postings.
Assess them? ~12 calls". The postings are shown ranked, not assessed. Nothing is assessed until
you answer:

```sh
gigai scout new --yes --json          # yes: assess the new postings (one model call each), then show the grid
gigai scout new --no-assess --json    # no: show the grid with ranks only
```

The agent's job is to tell you the count and the estimate, and wait for your word. When the
question was asked by an earlier call, the reply gives the exact command for a yes, with
`--since`, so the yes covers the postings you were shown.

**Old assessments are a second question.** A posting whose only assessment was made with an
older prompt, other settings or an old run is not "new", so `--yes` never touches it. The reply
asks about those separately, with their own count and estimate, for example "497 have only an
old assessment; re-assess? ~497 calls". The yes to that question is its own flag:

```sh
gigai scout new --reassess-stale --json         # yes to the old ones only (one model call each)
gigai scout new --yes --reassess-stale --json   # yes to both questions
```

In the JSON, `counts.to_assess` is the new postings that no matching profile has assessed, and
`counts.only_stale` is the postings that have only an old assessment. After a full `--yes`,
`to_assess` is the ones that failed, if any.

**Progress while it assesses.** A batch prints lines like `assessed 120 of 333 · ~18 min left`
to the error stream (stderr), so with `--json` the standard output is still the reply alone. A
batch of 20 or fewer prints every result; a larger one at most one line every 10 seconds, and
always the last. The reply's `ranking` block says how far the background ranking is, per
profile (`ranked` of `total`).

With nothing new, the reply reads "Nothing new since your last check" and lists the 10 postings
that still need your attention.

The grid has four columns: the posting (company, role, work mode, salary if stated, and the
profiles it matches), its score, whether it needs tailoring (with the requirements not yet met),
and its open questions. The score column is words, never a bare percent: the verdict, how many
requirements are met, and the rank, for example `Matched · 9 of 11 requirements · rank 96`,
`Matched (old assessment: older prompt) · 3 of 3 requirements · rank 95` or
`rank 97 · not assessed`.

A plain `gigai scout new` moves the "new since" mark to now. `--peek` and `--profile ID` look
without moving it.

### 2. Public and private are separate calls

`gigai scout new` never mixes text written by strangers with text about you. The default reply
holds only posting data (public, untrusted) and your open questions. "What matches", which is
built from your own resume, answers and stories, is a second call:

```sh
gigai scout new --yours --json
```

Its reply holds your evidence only and names each posting by its link, never by its text. An
agent should not paste the two side by side: a posting can contain text meant to steer an agent,
and keeping your own text out of that context is the point of the split. Over the API the same
two calls are `GET /api/new` and `GET /api/new/yours`, and every reply says what it holds in the
`X-GigAI-Labels` header (`public-untrusted`, `user-private`, both for a route that mixes them,
or `none`).

### 3. Answers, and "make this a story?"

The agent asks you the open questions. What you say is kept once and reused by every later
assessment, for every profile.

- A short fact is saved as an **answer**:

  ```sh
  gigai scout answers save cloud:gcp --question "Do you have GCP experience?" --answer-text "Yes, 4 years, GKE and BigQuery" --actor agent --json
  ```

- A reply with substance (a project, a problem, an outcome) gets the question "Want me to make
  this a story?". On a yes the agent drafts a short narrative from your own words, shows it to
  you, and saves it when you agree:

  ```sh
  gigai scout story save --title "Cut CI time 60% at Acme" --raw-text "Our builds took forty minutes, so I moved the runners to Kubernetes and cached the layers." --situation "Builds took forty minutes and blocked every merge." --action "Moved the runners to Kubernetes and cached the image layers." --result "Build time fell 60 percent." --tag ci --answers "Tell me about a time you improved a slow process" --actor agent --json
  ```

Every write is checked on your computer: text that looks like an email address, a phone number, a
link or a street address is refused (`personal_info_refused`). The check works on shapes. It does
not recognise a name, so don't put yours in an answer or a story.
[Answers and stories](#answers-and-stories-kept-once-reused) below has the full reference.

### 4. Process waiting work

Saving an answer puts the jobs that asked that question into the **background pipeline**: tailor
a resume for the job, assess the tailored resume, compute the Scout ATS score, set the Scout
label. The pipeline is on by default and works only on jobs you engaged with (you answered one of
their questions, or you said "process now"). Its limits:

- **10 jobs per trigger.** One answer can concern many jobs. The first 10 run; the rest wait for
  your approval.
- **40 model calls a day** for the pipeline. The call that would go over the limit is not made;
  the job waits until the next day.
- **First assessments are never automatic.** A posting is assessed for the first time only when
  you say yes (step 1, or **Assess these** on the Jobs page).

While Scout runs, the pipeline works by itself. `gigai scout new` tells you when work waits
("3 waiting (2 need your approval), process now? ~6 calls"). On your yes:

```sh
gigai scout new --process --json                 # approve what waits, run it now
gigai scout pipeline status --json               # what is queued, running, done; today's counts
gigai scout pipeline approvals list --json       # what waits for a yes, with the estimate
gigai scout pipeline process <job-url> --json    # one job, now
```

`gigai scout pipeline approvals approve <approval-id>` and `approvals deny <approval-id>` decide
one approval. An agent approves only on your word.

**A tailored resume you edited is never replaced by the background.** If you tailored a job's
resume yourself, or changed a line of it, the pipeline keeps it and works with your text. To get a
new tailoring for that job, tailor it yourself (`gigai scout resume tailor`, or **Tailor resume**
on the job page).

### 5. Tailor and the PDF

```sh
gigai scout resume tailor --job-url <job-url> --json           # one model call
gigai scout resume pdf --tailored --job-url <job-url> --json   # local, no model call
```

**The PDF an agent makes has no name and no contact details**, because GigAI has none to give
it. The command prints a line like

```text
Open in Scout to add your name and contact details and download: http://127.0.0.1:8765/#/pdf/<profile>/<job>
```

(`finish_url` in the JSON). The agent gives you that link. You open it, type your details in the
Generate PDF form in your own browser, and download the finished PDF. The details go into that
one PDF and are not kept. An agent can't do this step for you unless it controls your browser,
and it should not ask you for those details.

Every line comes from your resume, answers or stories. Read it before you send it.

## Use it from your agent

Three ways to teach an agent the daily loop. They carry the same instructions; pick the one
your agent reads.

| Your agent | Use | How it is picked up |
| --- | --- | --- |
| Claude Code | The skill file: `gigai agent-skill --format skill` | Claude Code loads it by itself when you ask about Scout or job postings, or when you type `/gigai-scout` |
| Codex | An `AGENTS.md` section: `gigai agent-skill --format agents-md` | Codex reads `AGENTS.md` at the start of every run |
| Any other agent | The same `AGENTS.md` section, or paste what `gigai agent-context` prints | Whatever instruction file your agent reads; or the chat itself |

`gigai agent-context` prints a one-line summary of the CLI, and with `--json` the full manual of
every command. It teaches the commands, not the loop: use it when your agent has no instruction
file at all.

### Claude Code

1. Install the skill. The command creates the folders it needs:

   ```sh
   gigai agent-skill --format skill --out ~/.claude/skills/gigai-scout/SKILL.md
   ```

   That folder is for all your projects. For one project only, write it to
   `.claude/skills/gigai-scout/SKILL.md` inside that project instead. `--out` refuses to replace
   a file that exists unless you add `--force`.
2. Print the permissions snippet and merge it into your settings yourself:

   ```sh
   gigai agent-permissions
   ```

   Put it in `~/.claude/settings.json` (all projects) or `.claude/settings.local.json` (this
   project only), merged into the `permissions` you already have. It allows the `gigai` command
   and Scout's local API at `http://127.0.0.1:8765/`, and denies reading `~/.gigai` directly.
   **GigAI prints it and never applies it.** It does not read or write your agent's settings.
3. Start a new Claude Code session, so that the skill is listed.
4. Type:

   > What's new on Scout?

What a first session should look like (an illustration with a made-up user and made-up
postings, not a recording):

> **You:** What's new on Scout?
>
> **Claude Code:** *(loads the gigai-scout skill, runs `gigai scout new --json`)* 14 new postings
> across your 2 profiles. They are ranked, not assessed. Assessing them is about 14 model calls
> on your Codex login. Go ahead?
>
> **You:** Yes.
>
> **Claude Code:** *(runs `gigai scout new --yes --since 2026-10-03T14:02:00Z --json`)* Assessed
> 14 of 14. The top one: Northwind, Staff Backend Engineer, remote: needs your answers, 9 of 11
> requirements, rank 96. It asks: have you run Kafka in production?

The snippet is a guard against accidents, not a security boundary: see
[The security model](#the-security-model). The curl rules cover only a command written exactly
as `curl http://127.0.0.1:8765/...`; the agent should prefer the `gigai` command. Without the
snippet everything still works: Claude Code asks you before each command.

If your project already has a `CLAUDE.md`, Claude Code does not read an `AGENTS.md` there; the
skill file is the simpler route.

### Codex

1. Print the instructions as an `AGENTS.md` section:

   ```sh
   gigai agent-skill --format agents-md
   ```

2. Add the section to the end of the file Codex reads, after a blank line:
   `~/.codex/AGENTS.md` for every project, or the `AGENTS.md` at the root of one repository.
   (If you keep an `AGENTS.override.md` there, Codex reads that one instead.)
3. Start Codex and type "What's new on Scout?".

What to expect from Codex's own safety settings. By default Codex runs commands in a sandbox
that can write only inside the folder you started it in (`workspace-write`), with network access
off, and asks you when a command needs more (`on-request`). GigAI keeps its data in `~/.gigai`,
outside that folder, and installing or **Update sources** needs the network. So expect Codex to
ask for approval for those commands. We have not tested every case; approve what you recognise.

Do not turn the sandbox off for GigAI (`danger-full-access`, or bypassing approvals). Nothing
here needs it.

To give Codex a docs page such as [Start here](start/): by default Codex's web search reads a
cached index, not the live page, so it may not find a new page. Either start it with live web
search (`codex --search "<your prompt>"`), or put "run `curl -fsSL <the page address>` and read
it" in the prompt. The starter prompt in the
[README](https://github.com/karthik446/gigai#let-your-agent-set-it-up) does the second.

### Any other agent

- Paste the `AGENTS.md` section (`gigai agent-skill --format agents-md`) into the instruction
  file your agent reads.
- While Scout runs, the local API describes itself: `GET http://127.0.0.1:8765/llms.txt` is a
  short plain-text guide, and `GET http://127.0.0.1:8765/api/openapi.json` is the full OpenAPI
  spec. See [Discover the API](#discover-the-api).
- The docs site has a plain-text entry point too: `llms.txt` beside these pages, with the setup
  steps and links.

### What it costs

`gigai scout new` asks before it assesses, with the count and an estimate from your own past
calls. `gigai scout metrics` shows the averages after the first calls. Background work has
daily caps: 40 pipeline model calls and 100 rank calls. The first run is the expensive one; a
normal day is a few dozen new postings. [Token usage](../tokens/) has measured numbers for
Codex and Claude Code.

## The security model

**Anything GigAI gives your agent is sent to that agent's model provider. Agents get no contact data from GigAI, but an agent with shell access can read local files.**

What that means, plainly:

- **Your agent's provider sees what your agent reads.** When Claude Code or Codex reads a job
  grid, your answers or a tailored resume from Scout, that text goes to Anthropic or OpenAI as
  part of the conversation. GigAI can't stop that. The only way to keep everything on your
  computer is to run both Scout and your agent on a local model.
- **GigAI has no contact data to give.** It never stores your name, email, phone, address or
  links, so no command and no API route can return them. See
  [Privacy and security](../privacy/) for the limits of that promise.
- **An agent that can run commands can read GigAI's files.** It runs as you, so file permissions
  do not separate it from your answers, stories and resume under `~/.gigai`. The permissions
  snippet lowers the chance of an accident. It can't stop an agent that decides to read the
  files another way.
- **Job postings are written by strangers and can contain text meant to trick your agent.**
  Scout marks posting text as untrusted, keeps it apart from your own text in `gigai scout new`,
  and fences it in its own prompts with a rule that it is data, never instructions. That lowers
  the risk. It does not remove it. Keep your agent's permission prompts on.
- **Scout checks what it sends out, as a backstop.** Every JSON reply from the local API is
  scanned, and text shaped like an email address, a phone number, a profile link or a street
  address in your own fields is replaced by a marker such as `[removed: email]`. Posting text is
  left alone (a recruiter's address in a posting is public). The scan works on shapes: it can miss
  an unusual phone format, it does not know your name, and it does not cover what the `gigai`
  command prints. Don't rely on it; keep contact details out of your answers and stories.
- **The local API answers this computer only.** Every request must come from this machine and
  carry a `Host` of `127.0.0.1:<port>` or `localhost:<port>`, so a web page you visit can't read
  it through your browser. There is no login: any program you run on this computer can call it.

## Example session

A fictional user, Robin, with two profiles ("Backend" and "Platform"). Robin's words are plain
text; what the agent runs is shown under each step.

> **Robin:** What's new in Scout?

```sh
gigai scout new --json
```

> **Agent:** 6 new postings across 2 profiles (Backend 4, Platform 2). They are ranked but not
> assessed. Assessing them is about 6 model calls on your Codex login. Go ahead?
>
> **Robin:** Yes.

```sh
gigai scout new --yes --since 2026-10-01T14:02:00Z --json
gigai scout new --yours --json
```

> **Agent:** Assessed 6 of 6. The top three:
>
> | Posting | Score | Needs tailoring | Open questions |
> | --- | --- | --- | --- |
> | Northwind: Staff Backend Engineer (remote) | Needs your answers · 9 of 11 requirements · rank 96 | yes: Terraform | Have you run Kafka in production? |
> | Globex: Platform Engineer (hybrid) | Needs your answers · 8 of 11 requirements · rank 91 | yes: SOC 2, on-call lead | Do you have GCP experience? |
> | Initech: Senior SRE (remote) | Matched · 7 of 11 requirements · rank 84 | yes: Go | none |
>
> Two questions. Have you run Kafka in production?
>
> **Robin:** Yes, three years at my last job. We moved the order pipeline off a nightly batch
> onto Kafka and cut the delay from a day to under a minute.
>
> **Agent:** That is more than a yes. Want me to make this a story? Draft: "Order pipeline moved
> from a nightly batch to Kafka. Situation: orders were processed once a day. Action: moved the
> pipeline onto Kafka. Result: delay fell from a day to under a minute."
>
> **Robin:** Yes, save it. And GCP: four years, GKE and BigQuery.

```sh
gigai scout answers save streaming:kafka --question "Have you run Kafka in production?" --answer-text "Yes, three years" --actor agent --json
gigai scout story save --title "Order pipeline moved from nightly batch to Kafka" --raw-text "We moved the order pipeline off a nightly batch onto Kafka and cut the delay from a day to under a minute." --situation "Orders were processed once a day." --action "Moved the pipeline onto Kafka." --result "Delay fell from a day to under a minute." --tag streaming --answers "Tell me about a time you improved a slow process" --actor agent --json
gigai scout answers save cloud:gcp --question "Do you have GCP experience?" --answer-text "Yes, 4 years, GKE and BigQuery" --actor agent --json
gigai scout new --peek --json
```

> **Agent:** Saved. Two jobs asked those questions and now wait in the pipeline: about 4 model
> calls, inside today's limit of 40. Process them now?
>
> **Robin:** Go.

```sh
gigai scout new --process --json
gigai scout pipeline status --json
```

> **Agent:** Done. Northwind: requirements met went from 9 to 10 of 11 after tailoring, Scout ATS 88,
> Scout label: recommended. That label is Scout's own suggestion from your settings, resume and
> answers, not a prediction of what an employer will decide. Globex still needs attention: the
> SOC 2 requirement is not met.
>
> **Robin:** Make me the Northwind PDF.

```sh
gigai scout resume pdf --tailored --job-url https://jobs.lever.co/northwind/1a2b3c --json
```

> **Agent:** The PDF is written without your name and contact details. Open this link to add
> them and download the finished one: `http://127.0.0.1:8765/#/pdf/default/https%3A%2F%2Fjobs.lever.co%2Fnorthwind%2F1a2b3c`.
> Read the resume before you send it.

The agent never asked Robin for a name, an email or a phone number, and never read `~/.gigai`.

## Screenshots

<!-- 0110-049: the images are rebuilt every release by `make media` from a synthetic demo home
(made-up companies and persona, fixture model) and live at fixed paths under public/media/.
Raw <img> tags with a path relative to this page, so the versioned docs base (/gigai/<version>/)
needs no handling and the build's link check covers them. Each image links to itself at full size. -->

Your agent runs `gigai scout new`: what is new, and what assessing it would cost, before any model call.

<a href="../../media/terminal-new.png"><img src="../../media/terminal-new.png" alt="An agent runs `gigai scout new`: what is new, and the cost, before anything is assessed." loading="lazy" /></a>

It asks your open questions, saves your answer, and offers to turn an experience into a story.

<a href="../../media/terminal-answer.png"><img src="../../media/terminal-answer.png" alt="The agent asks a question, saves your answer, and offers to make it a story." loading="lazy" /></a>

The same jobs in the Scout UI, with the Scout label and Scout ATS score once the background pipeline has run.

<a href="../../media/jobs-dark.png"><img class="light:sl-hidden" src="../../media/jobs-dark.png" alt="The Jobs page: every stored posting your profiles match, with filter chips, profile tags and Scout's chips." loading="lazy" /></a>
<a href="../../media/jobs-light.png"><img class="dark:sl-hidden" src="../../media/jobs-light.png" alt="The Jobs page: every stored posting your profiles match, with filter chips, profile tags and Scout's chips." loading="lazy" /></a>

<a href="../../media/job-pipeline-dark.png"><img class="light:sl-hidden" src="../../media/job-pipeline-dark.png" alt="The same job page further down: the background pipeline's steps, the Scout label and the Scout ATS score, then the tailored resume." loading="lazy" /></a>
<a href="../../media/job-pipeline-light.png"><img class="dark:sl-hidden" src="../../media/job-pipeline-light.png" alt="The same job page further down: the background pipeline's steps, the Scout label and the Scout ATS score, then the tailored resume." loading="lazy" /></a>

Jobs over the per-trigger limit wait for your approval.

<a href="../../media/background-dark.png"><img class="light:sl-hidden" src="../../media/background-dark.png" alt="The Background pipeline panel, with jobs waiting for your approval." loading="lazy" /></a>
<a href="../../media/background-light.png"><img class="dark:sl-hidden" src="../../media/background-light.png" alt="The Background pipeline panel, with jobs waiting for your approval." loading="lazy" /></a>

What you told your agent is kept once, on this machine, and reused.

<a href="../../media/answers-dark.png"><img class="light:sl-hidden" src="../../media/answers-dark.png" alt="Answers: what you told Scout or your agent, kept once and reused." loading="lazy" /></a>
<a href="../../media/answers-light.png"><img class="dark:sl-hidden" src="../../media/answers-light.png" alt="Answers: what you told Scout or your agent, kept once and reused." loading="lazy" /></a>

The agent's PDF has no name or contact details. It hands you a link, and you add yours in the browser.

<a href="../../media/terminal-pdf.png"><img src="../../media/terminal-pdf.png" alt="The agent renders the tailored resume as a headerless PDF and hands you the link to finish it in Scout." loading="lazy" /></a>

<a href="../../media/pdf-dark.png"><img class="light:sl-hidden" src="../../media/pdf-dark.png" alt="Generate PDF: you add your own name and contact details in the browser; GigAI stores none." loading="lazy" /></a>
<a href="../../media/pdf-light.png"><img class="dark:sl-hidden" src="../../media/pdf-light.png" alt="Generate PDF: you add your own name and contact details in the browser; GigAI stores none." loading="lazy" /></a>

The companies, postings and person in these images are made up.

## Discover the API

While Scout runs (default `http://127.0.0.1:8765`):

- `GET /api` lists every route.
- `GET /api/openapi.json` is the OpenAPI 3.1 spec (rendered in the [API reference](../reference/api/)).
- `GET /llms.txt` is a short plain-text guide.
- `GET /api/jobs?url=` returns one job with everything known about it.

```sh
B=http://127.0.0.1:8765; J='https://boards.greenhouse.io/acme/jobs/101'
curl -s -G "$B/api/jobs" --data-urlencode "url=$J"     # one job: posting, assessments, open questions, tailored resumes, links
curl -s "$B/api/runs"                                  # every run, newest first
curl -s -X POST "$B/api/assess" -H 'Content-Type: application/json' \
  -d "{\"job\": {\"job_url\": \"$J\"}}"                # assess (model call, blocks until done)
curl -s -X POST "$B/api/tailored-resumes" -H 'Content-Type: application/json' \
  -d "{\"job\": {\"job_url\": \"$J\"}}"                # tailor the resume (model call)
curl -s -X POST "$B/api/tailored-resumes/pdf" -H 'Content-Type: application/json' \
  -d "{\"profile_id\": \"<profile_id>\", \"job_identity\": \"$J\"}" -o resume.pdf
```

The PDF body takes the `profile_id` and `job_identity` of a stored tailored resume; both
are in the `tailored_resumes` entries and `links.pdf` of the `GET /api/jobs` response.
Errors are `{"error": {"code", "message"}}`; an `unknown_key` 422 lists the allowed keys.

The API answers this computer only (loopback peers): `Host` must be
`127.0.0.1:<port>` or `localhost:<port>` (curl sets it), and every write (POST/PUT/DELETE)
needs `Content-Type: application/json`. Each route and command carries an **effect**
(`read` changes nothing, `write` may change GigAI state) and an **external** marker
(`none` offline, `model` spends a model call and sends text to your model target,
`network` reads the public internet).

## Change a resume and render a new PDF

All of this is local: no model call, no network. GigAI stores no name and no contact details,
so a PDF made by an agent, the CLI or the API has no header: a blank block keeps the page layout.
The reply carries `X-GigAI-Finish-Url` (the CLI prints it as "Open in Scout to add your name and
contact details and download"), the local Scout page where you type your details in the Generate
PDF form and download the finished PDF. The saved Resume display settings hold only the title
under the name and the layout (spacing, auto fit).

A worked example, **an agent changes two bullets and renders a new PDF**, for a job that
already has a tailored resume:

```sh
B=http://127.0.0.1:8765; J='https://boards.greenhouse.io/acme/jobs/101'; P='<profile_id>'
H='Content-Type: application/json'

# 1. Read the resume: every body line has an id (L<n>); note updated_at.
curl -s -G "$B/api/tailored-resumes" --data-urlencode "profile_id=$P" --data-urlencode "job_identity=$J"

# 2. Change two bullets (one PUT per line; updated_at is the one you read and does not change).
curl -s -X PUT "$B/api/tailored-resumes/lines" -H "$H" -d "{\"profile_id\": \"$P\", \"job_identity\": \"$J\",
  \"updated_at\": \"<updated_at>\", \"line_id\": \"L7\", \"use\": \"custom\",
  \"text\": \"Rebuilt the scheduling service on Python and Postgres for 4 teams.\"}"
curl -s -X PUT "$B/api/tailored-resumes/lines" -H "$H" -d "{\"profile_id\": \"$P\", \"job_identity\": \"$J\",
  \"updated_at\": \"<updated_at>\", \"line_id\": \"L8\", \"use\": \"custom\",
  \"text\": \"Ran the release calendar and the on-call rota.\"}"

# 3. Render the new PDF (no header; -D - prints the response headers, X-GigAI-Finish-Url among them).
curl -s -D - -X POST "$B/api/tailored-resumes/pdf" -H "$H" -d "{\"profile_id\": \"$P\", \"job_identity\": \"$J\"}" -o resume.pdf
```

The same with the CLI, which needs no running server for the render:

```sh
gigai scout resume pdf --tailored --job-url "$J" --out resume.pdf --json    # the stored resume, edits included; prints finish_url

# or work on the markdown yourself: edit the two "- " lines in a file, then render it
gigai scout resume pdf --in resume.md --out resume.pdf --json
```

What to know:

- An edited line is marked `kind: custom` with `origin: user`. It cites no source (`refs` is
  empty) and the no-loss check does not cover it: it is your text. `edited_from` keeps the line it
  replaced, so `"use": "original"` or `"use": "rewritten"` brings that back. The UI shows the line as
  edited, with the same way back.
- `text` is one line of at most 400 characters. Body lines only (a summary, skills or other line, or
  an entry's bullet), never an entry heading.
- A text that looks like a name line or a contact detail (email, phone, link, street address) is
  refused with `422 personal_info_refused`: GigAI stores none of those. You add them in the
  Generate PDF form.
- Both PDF routes accept an optional `header` object (`name`, `email`, `phone`, `location`,
  `linkedin`, `link`) that fills this one PDF's header and is never stored, logged or returned.
  It is there for the Generate PDF form. Details an agent sends in it went through that agent and
  its model provider first, so an agent should leave it out and hand over the finish link.
- Without `--out`, the CLI names the file `<company>-<role>-<date>.pdf` (or `resume-<date>.pdf`
  for markdown) in the current folder. Your name is never in a file name.
- `POST /api/resume/pdf` renders markdown you send: `{"markdown": "...", "spacing_scale": 0.9,
  "auto_fit": false, "profile_id": "..."}` (only `markdown` is required) answers `application/pdf`,
  with the page count in `X-GigAI-Pages`. The markdown is rendered and dropped: not stored, not
  logged, not sent to a model.
- The markdown format is the one a tailored resume's `markdown` field uses: `## Summary`,
  `## Experience`, `## Skills`, `## Education`, `## Projects`, `## Other`; in Experience, Projects and
  Education an entry starts with `### <employer, project or school>`, may continue with plain
  heading lines (`Staff Engineer | Jun 2020 - Present`), and lists `- ` bullets. Trailing
  `<!-- ... -->` comments are dropped and lines above the first `## ` are not printed. Text prints
  as written (inline markdown such as `**bold**` is not interpreted). Summary lines print as one
  paragraph and Skills as tags, so a summary or other line that was a bullet in your original
  resume can print differently from the stored tailored resume's PDF; use `--tailored` (or
  `POST /api/tailored-resumes/pdf`) when you want exactly that PDF.
- Errors are 422s that say what is wrong: `resume_markdown_invalid` names the line number and the
  rule, `resume_markdown_too_large` the limit (65536 bytes), `invalid_value` a `spacing_scale`
  outside 0.7 to 1.4.

## Answers and stories: kept once, reused

The reference for step 3 of the daily workflow. Scout keeps two things you tell your agent, for
every profile at once (they are yours, not one profile's):

- An **answer** is a short fact a posting asked for: "Do you have GCP experience?" -> "Yes, 4 years,
  GKE + BigQuery".
- A **story** is an experience worth telling: a project, a problem, an outcome. It has a `title`,
  where and when (`company`, `role`, `period`), your own words (`raw`), a loosely STAR `narrative`
  (`situation`, `task`, `action`, `result`; every part optional), `tags`, the interview questions
  it answers (`answers_questions`) and what triggered it (`sources`).

Both are local (no model call, no network) and both are what the next assessment uses. An answer
is reused for the same question, and for the same fact worded differently, instead of asking
again. The stories are searched on your machine for each job, and only the few that match the
posting go into that assessment's prompt as evidence. `answers_questions`, pooled across your
stories, is the list of questions your stories answer (`GET /api/stories/prep`).

The usual flow in a chat: the agent asks a job's open questions. A factual reply is saved as an
answer. A reply with substance gets "Want me to make this a story?": the agent drafts the
narrative from your words, shows it, and saves it when you say yes.

You and an agent write the same answers and stories, so every write says who it is (`actor`:
`operator`, the default, or `agent`; a body field, or the `X-GigAI-Actor` header) and an edit or
a delete sends the `revision` it read. If it changed since, the reply is `409 revision_conflict`
with the current `answer` or `story` in the error: read it, merge, send again. Scout's own page
(Settings > Answers and stories) is read-only: it lists them, shows which jobs used each, and
deletes one that is wrong. It has no form; writing is the agent's job.

A worked example, **an agent saves an answer and a story from a conversation, and later
assessments use them**:

```sh
B=http://127.0.0.1:8765; H='Content-Type: application/json'

# 1. A factual reply: save it as an answer.
curl -s -X POST "$B/api/answers" -H "$H" -d '{
  "question_id": "cloud:gcp", "question": "Do you have GCP experience?",
  "answer": "Yes, 4 years, GKE + BigQuery", "actor": "agent"}'
# -> 201 {"answer": {"question_id": "cloud:gcp", "written_by": "agent", "revision": 1, "jobs": [], ...}}

# 2. A reply with substance, after "Want me to make this a story?" and a yes: save the story.
curl -s -X POST "$B/api/stories" -H "$H" -d '{
  "title": "Cut CI time 60% at Acme", "company": "Acme", "role": "Staff Engineer", "period": "2023",
  "raw": "Our builds took forty minutes, so I moved the runners to Kubernetes and cached the layers.",
  "narrative": {"situation": "Builds took forty minutes and blocked every merge.",
                "action": "Moved the runners to Kubernetes and cached the image layers.",
                "result": "Build time fell 60 percent."},
  "tags": ["ci", "delivery"],
  "answers_questions": ["Tell me about a time you improved a slow process"],
  "sources": [{"question_id": "tooling:kubernetes"}],
  "actor": "agent"}'
# -> 201 {"story": {"story_id": "story:60_acme_ci_cut_time", "written_by": "agent", "revision": 1, ...}}

# 3. Later, assess another posting that wants GCP and Kubernetes (one model call).
curl -s -X POST "$B/api/assess" -H "$H" -d '{"job": {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}}'
# -> no question for GCP; that row's resume_evidence reads "Story bank cloud:gcp: Yes, 4 years, ...".
#    The story matched the posting, so it was in the prompt too: a row may read
#    "Story bank story:60_acme_ci_cut_time: Cut CI time 60% at Acme ...".

# 4. Each now lists that job: the answer with kind "reused", the story with kind "used".
curl -s "$B/api/answers/cloud%3Agcp"
curl -s "$B/api/stories/story%3A60_acme_ci_cut_time"

# 5. Improve the story later: send the revision you just read.
curl -s -X PUT "$B/api/stories/story%3A60_acme_ci_cut_time" -H "$H" -d '{
  "revision": 1, "period": "2022-2023", "actor": "agent"}'
```

The same with the CLI (no running server needed):

```sh
gigai scout answers save cloud:gcp --question "Do you have GCP experience?" --answer-text "Yes, 4 years, GKE + BigQuery" --actor agent --json
gigai scout answers list --json                        # every answer, with tags and the jobs that used it
gigai scout answers show cloud:gcp --json
gigai scout answers delete cloud:gcp --confirm --revision 1 --json
gigai scout story save --file story.json --actor agent --json   # the fields of POST /api/stories
gigai scout story list --json
gigai scout story show story:60_acme_ci_cut_time --json
gigai scout story save story:60_acme_ci_cut_time --period 2022-2023 --revision 1 --actor agent --json
gigai scout story delete story:60_acme_ci_cut_time --confirm --revision 2 --json
gigai scout story prep --json                          # the interview questions your stories answer
```

The routes: `GET` / `POST /api/answers`, `GET /api/answers/match`, `GET` / `PUT` / `DELETE
/api/answers/{question_id}`, `GET` / `POST /api/stories`, `GET /api/stories/prep`, `GET` / `PUT` /
`DELETE /api/stories/{story_id}`. The CLI prints the same bodies.

What to know:

- **Answers from a job page land there too.** `POST /api/answers` with `reassess` (and `gigai
  scout answer --reassess`, and the job page's question box) saves the answer and assesses that
  job again. Send `question` (the question's own words) with it; they are kept with the answer.
- **Reuse costs nothing extra.** The exact same `question_id` is reused as before. A question
  worded differently is settled from the answers in the assessment's own model call; the row then
  cites `Story bank <id>: ...` and the job is listed on the answer or the story.
- **A run is no longer the way in (0.1.10.7).** `POST /api/run` is deprecated: it still works for
  one release, for scripts. Search the stored postings with `GET /api/postings`
  (`gigai scout jobs list`: live, no run, no model call), assess the ones you pick with
  `POST /api/postings/assess` (`gigai scout jobs assess`: it asks first, with the count and the
  estimate, and assesses only on approval), and let the background rank them. Old runs stay
  readable, and `POST /api/runs/import` (`gigai scout jobs import-runs`) puts what they assessed
  beside the newer assessments.
- **A search run reuses it too.** The assessments a find-jobs run makes (`POST /api/run`, a
  background check) get what `POST /api/assess` gets: your answers and matching stories, and the
  run's own search settings (whether you need sponsorship, the countries you can work from,
  your location, your target titles). The run's sealed output says what it used: the assess
  prompt (`prompt_version`), a digest of those settings (`constraints_digest`) and the bank
  (`story_bank`: a mark per answer and story, never their text). A posting that did not
  change is normally not assessed again. It is when the prompt version or those settings
  changed, when its assessment left a question open and there is an answer or a story about it
  the run had not seen, or when it cites an answer or story that was edited or deleted.
  `GET /api/runs/{run_id}/results` and `.../posting` carry `bank_suggestions` for the questions
  that remain.
- **A run says why it did not assess a posting.** Each row a run kept but did not assess has a
  reason: `payload.not_assessed[].reason` on `GET /api/runs/{run_id}/results` (for the rows of
  that page), `not_assessed_reason` on `.../posting`, and `not_assessed_counts` on
  `.../progress`. `over_cap` is a posting the run's assess cap left out, `duplicate` a copy
  of one it kept (same company, title and country), `unchanged` one whose earlier assessment
  still stands. The reason never changes which postings the run assessed.
- **A stored assessment says when it was made with older settings.** An assessment made by
  `POST /api/assess`, the job page or "Assess all new" is stored once and read many times. It
  records what it was made with, as a run does: `prompt_version`, `constraints_digest`
  (sponsorship need, countries, location, work mode) and `story_bank` (digests and ids, never
  the settings or the answers). `GET /api/assessments` items and the `source: "quick"`
  assessments of `GET /api/jobs` carry `basis_stale` (true or false) and, when true,
  `basis_stale_reason`: `older_prompt`, `settings_changed` (the profile's work mode, countries,
  location or sponsorship need changed) or `story_bank_changed` (an answer or story added or edited
  since answers one of ITS OWN open questions, by the same id, the near match behind
  `bank_suggestions` or a story about it, or it cites an answer or story that was edited or deleted;
  answering one question does not flag assessments that asked something else). A `story_bank_changed` item
  also carries `basis_stale_bank`: the entries that made it stale, each `{match: "exact" |
  "near" | "cited", bank_question_id, bank_question, question_id, question}` (ids and question
  words, never an answer). `job_state.assessment_stale.reason` carries the same reason when that assessment
  gives the job's state. The verdict still reads. Each profile is compared with its own
  settings.
- **Every assessment stored before 0.1.10.7 reads `older_prompt`.** The assess prompt changed in
  0.1.10.7 (posting text is fenced as untrusted), so an assessment made by an earlier version,
  with or without a recorded basis, is flagged as made with older settings. The verdict still
  reads, and nothing is assessed again until you ask.
- **Nothing is re-assessed until you ask.** No read calls a model. Re-assess one job with
  `POST /api/assess` (`{"job": {"job_url": "..."}}`), or all of a run's with
  `POST /api/runs/{run_id}/assess-all` `{"start": true}`: its queue is the run's new postings
  plus the ones with a stale stored assessment, and `{}` reads the plan
  (`count` = `new_count` + `stale_count`) without starting anything. A current stored
  assessment is skipped.
- **A near match is offered, not assumed.** When an assessment still asks something close to
  an entry, the response carries `bank_suggestions` (also on `GET /api/jobs?url=`, and for one
  question on `GET /api/answers/match?question_id=&question=`): the answer, the question it
  came from and a score. It is word overlap, no model. To accept it, save it as the
  answer: `POST /api/answers {"question_id": "<the new question>", "answer": "<the suggested
  answer, or your edit>", "from_bank": "<bank_question_id>", "reassess": {"job_identity": "..."}}`.
  The UI shows it as "We already know: ..., use it?".
- **Yours, not a profile's.** Since 0.1.10.7 every profile reads the same answers and stories.
  The per-profile story bank of 0.1.10.5 and its sharing setting are gone; what it held was moved
  once (`gigai scout answers migrate --json` prints the counts; a second run changes nothing).
  Two profiles that answered the same question: the newest write is the answer, and a different
  older one is kept in that answer's `history`. The old file is kept as `story_bank/bank.v1.json`.
- **Tags** are model-free: `technical`, `experience-level`, `eligibility`, `education`, `domain`,
  `leadership`, `conflict`, `failure`, `collaboration`, `system-design`, `delivery`, `skill`, `other`,
  from the id's category and the question's words. Set your own with `tag`.
- **No contact details.** Every write is checked locally: an email, a phone number, a link or a
  street address is refused with `422 personal_info_refused` (the message names what was found,
  never the text). The check works on shapes; GigAI has no saved name to compare with, so it does
  not recognise a name. What a model sees of your answers is a one-line summary per entry, at
  most 40 entries, redacted again.
- **Delete** (`DELETE /api/answers/<id>?revision=` or `DELETE /api/stories/<id>?revision=`, with
  `Content-Type: application/json` like every write) takes it out of use for good. For an answer,
  the project's journal keeps the older revision of the record it was in.

## The background pipeline

The reference for step 4 of the daily workflow. For a job you engaged with, the pipeline runs
four steps: tailor a resume, assess the tailored resume (kept beside the first assessment, never
in its place), compute the Scout ATS score, set the Scout label.
[What Scout's numbers and labels mean](../numbers/) explains the last two.

| Setting (Settings > Background pipeline) | Default |
| --- | --- |
| Pipeline | on |
| Jobs started by one trigger | 10; the rest wait for your approval |
| Pipeline model calls a day | 40 |
| Rank model calls a day, all profiles together | 100, with a warning past 60 |
| Minimum Scout ATS score for "recommended" | 0 (the score is shown, never a gate) |

```sh
gigai scout pipeline status --json                  # jobs, steps, today's counts
gigai scout pipeline status --job <job-url> --json  # one job's steps and outputs
gigai scout pipeline process <job-url> --json       # "process now" for one assessed job
gigai scout pipeline cancel <job-url> --json
gigai scout pipeline retry <job-url> --json
gigai scout pipeline approvals list --json
gigai scout pipeline run --once --json              # run what waits once, with no Scout server
gigai scout metrics --json                          # what your model calls cost, per kind and model
```

The routes: `GET /api/pipeline`, `GET /api/pipeline/job`, `GET /api/pipeline/approvals`,
`POST /api/pipeline/approvals/{approval_id}`, `POST /api/pipeline/process`, and the `pipeline` and
`rank` blocks of `GET` / `PUT /api/settings/background`.

What to know:

- **What starts it.** Saving or editing an answer or a story (for the jobs whose assessment left
  that question open), "process now", and a change to a profile's resume or search settings (for
  jobs whose pipeline had finished). Deleting an answer or a story starts nothing.
- **A job must be assessed first.** The pipeline works from the posting text stored with the
  job's assessment and never fetches a posting. A job assessed from pasted text can't enter it.
- **Nothing runs twice.** A step whose inputs did not change is skipped: no model call.
- **A failed call still counts** toward the day's 40.
- **It waits for other work.** While a batch of first assessments or a find-jobs run is going,
  the pipeline waits.
- **A resume that is yours is kept.** A stored tailored resume the pipeline did not write itself
  (you tailored it, or changed a line) is never replaced, not even with `--force`. The step
  finishes with `tailor_kept_user_edits`, and the assessment, the ATS score and the label use your
  text.
- **No contact data.** A tailored resume, an ATS line or a keyword that holds a contact shape
  fails its step (`contact_data_found`), and the pipeline's own file holds ids, codes and numbers
  only, no text.

## Install and run

Everything below runs from an installed package (`uv tool install gigai`),
with no source checkout needed (the first `gigai scout run` or `gigai scout install` writes GigAI's
default settings), and Scout runs from anywhere: no `cd` into a
target repo and no `gigai init` step first.

```bash
gigai scout install --json                    # bind, approve, and activate Scout
gigai secrets add exa                         # optional: only if you turn on Exa, see Configuration
gigai scout resume add ./resume.txt --json    # import + wrap your resume for find-jobs
```

See [Configuration](../configuration/) for the starter `find-jobs.json` and where Scout lives. Then start it:

```bash
gigai scout run --json     # installs/activates if needed, starts the API + UI, opens a browser tab
gigai scout status --json  # running / stopped / crashed, with url/pid/log path
gigai scout stop --json    # stop it; safe to rerun
```

`gigai scout run` is backgrounded by default: it prints the URL and log
path and returns. Pass `--foreground` to run it in the current process
instead (Ctrl-C stops it), `--no-browser` to skip opening a tab, and `--port`
if 8765 is taken. Logs live at `<home>/logs/scout-<project_id>.log`; run
state at `<home>/run/scout/<project_id>.json`.

If a project has more than one installed, approved Gig, switch which one is
active with:

```bash
gigai gig use <gig-id> --json
```

`gigai scout install` already activates Scout when it's the only Gig bound,
so this is only needed when switching between Gigs.

## Model targets for Scout

Scout's find-jobs resolves a sealed model target (`ollama_local`, `codex_cli`,
`claude_cli`, `openrouter_api`) to whichever enabled configured target uses that adapter:
`gigai setup`'s auto-named target (e.g. `codex-default` or `claude-default`) just works, no
special naming needed. If you followed an older version of this
documentation and already have a target literally named `codex_cli` (or
`ollama_local`/`openrouter_api`), that still resolves correctly too. Only
having *two* enabled targets on the same adapter with neither named exactly
the sealed value is an error: disable or remove one (`gigai setup` or edit
`config.toml`).

With `claude_cli`, `gigai setup` finds `claude` on your `PATH` the way it
finds `codex`, and names the target `claude-default`. Ranking calls
`claude -p` in a lean mode (a one-line system prompt; no settings, MCP
servers, slash commands or tools) and passes the target's model. An
assessment calls it in Claude Code's plan mode, which ignores `--model`, so
assessments run Claude Code's default model whatever the target names.
Without `claude` on your `PATH`, ranking is skipped (`model_target_unavailable:
claude executable is not available on PATH`, the same as for a missing
`codex`) and assessments fail with `model_target_unavailable`.

## Reference

- [Scout CLI reference](../reference/cli/): every `gigai scout` command, with its effect and what
  it may call.
- [API reference](../reference/api/): every route of the local API, from the same spec Scout
  serves at `GET /api/openapi.json`.
- [What Scout's numbers and labels mean](../numbers/): rank, verdict, Scout label, Scout ATS score.
- [Privacy and security](../privacy/): what is stored, what is sent, and to whom.
