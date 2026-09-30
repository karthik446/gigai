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
