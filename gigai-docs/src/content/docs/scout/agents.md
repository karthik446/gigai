---
title: For agents
description: Drive Scout from scripts and agents, through the CLI and the local API.
---

It's mostly agents (Claude, Codex, and similar) driving GigAI, so these
commands are non-interactive and scriptable. The general CLI discovery commands
are on [For agents](../../agents/).

`gigai agent-skill` prints the instructions that teach your agent the daily Scout loop,
and `gigai agent-permissions` prints a recommended Claude Code permissions snippet.

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

All of this is local: no model call, no network. The name, title and contact line at
the top of every PDF come from the saved Resume display settings (`PUT /api/resume-display`),
never from the resume text.

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

# 3. Render the new PDF.
curl -s -X POST "$B/api/tailored-resumes/pdf" -H "$H" -d "{\"profile_id\": \"$P\", \"job_identity\": \"$J\"}" -o resume.pdf
```

The same with the CLI, which needs no running server for the render:

```sh
gigai scout resume pdf --tailored --job-url "$J" --out resume.pdf --json    # the stored resume, edits included

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
- A text that looks like your name or a contact detail (email, phone, link, street address) is
  refused with `422 personal_info_refused`: put those in Resume display.
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

Scout keeps two things you tell your agent, for every profile at once (they are yours, not one
profile's):

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
- **An assessment stored before that is stale only for what its prompt missed.** It has no
  recorded basis. It is `older_prompt` when the profile has a work mode now (the prompt had
  none before 0.1.10.5), and `settings_changed` when the sponsorship need or the countries it
  stored differ from the profile's now. Otherwise it stays current.
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
- **No personal details.** Every write is checked locally: an email, a phone number, a link, a
  street address or the name saved in Resume display is refused with `422 personal_info_refused`
  (the message names what was found, never the text). What a model sees of the bank is a one-line
  summary per entry, at most 40 entries, redacted again.
- **Delete** (`DELETE /api/answers/<id>?revision=` or `DELETE /api/stories/<id>?revision=`, with
  `Content-Type: application/json` like every write) takes it out of use for good. For an answer,
  the project's journal keeps the older revision of the record it was in.

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
