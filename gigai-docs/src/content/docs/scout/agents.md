---
title: For agents
description: Drive Scout from scripts and agents, through the CLI and the local API.
---

It's mostly agents (Claude, Codex, and similar) driving GigAI, so these
commands are non-interactive and scriptable. The general CLI discovery commands
are on [For agents](../../agents/).

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
`127.0.0.1:<port>` or `localhost:<port>` (curl sets it), and every write (POST/PUT)
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
