# GigAI

GigAI is a local, user-controlled agent runtime. It keeps configuration,
credentials, and work state under an operator-selected home directory, and
makes every model call, review, and result inspectable rather than treating
one model response as proof of correctness.

A **Gig** is a self-contained goal-graph package built on top of GigAI core.
The boundary is fixed in one direction only — **a Gig imports GigAI core;
GigAI core never imports a Gig.** Gigs are portable, reviewable units of
work, not plugins the runtime depends on.

## Scout, the first Gig

Scout ships with GigAI and implements `find-jobs`, a job-search workflow:

- **Acquire** — pull public postings from Exa search and the
  Greenhouse/Lever/Ashby applicant-tracking boards through an auto-managed
  watchlist.
- **Assess** — build a requirements-by-resume matrix for each posting, with
  suggestions and open questions. Defaults to a local model target; hosted
  targets are only used when explicitly configured.
- **Present** — a localhost API and a small web UI show acquired and assessed
  postings. Nothing leaves the machine, and no hosted model is called,
  without an explicit consent step in the UI first.

Everything Scout writes stays under your configured GigAI home and the bound
project's workpad. Scout is one Gig among others GigAI can host; it gets no
special runtime treatment.

## Install

Requires Python 3.11+.

```bash
uv tool install gigai
gigai --version
gigai --help
```

## Quickstart for humans

```bash
gigai setup        # interactive: workspace, access, models, roles
gigai doctor        # confirm the install is healthy
cd /path/to/a/git/repo
gigai init          # bind this repository as a GigAI project
gigai gigs          # list Gigs registered for this project
```

## For agents

It's mostly agents — Claude, Codex, and similar — driving GigAI, so these
commands are non-interactive and scriptable. Every one below was run against
this build.

### Setup

```bash
gigai setup --non-interactive \
  --home ~/.gigai --workpad-root ~/gigai-workpads --editor /usr/bin/true \
  --json
```

To add a hosted model target, include credential, endpoint, and
model-target flags:

```bash
gigai setup --non-interactive \
  --home ~/.gigai --workpad-root ~/gigai-workpads --editor /usr/bin/true \
  --credential-ref provider=environment:MY_PROVIDER_TOKEN \
  --endpoint remote=openai_api:provider:https://api.example.com \
  --model-target remote=remote:some-model-name \
  --create-model-target remote \
  --json
```

`--credential-ref` records only an environment-variable *name*, never a
secret value. Run `gigai setup --help` for the full reference, including
Ollama loopback endpoints and per-target reasoning-effort options.

> **0.1.8.x limit.**
> Scout's find-jobs resolves a model target by its *name*, using the target
> name `codex_cli` (or `ollama_local`) literally — an auto-named target like
> `codex-default` will not be found. Name the target explicitly and raise its
> output limit (512 is too small for an assessment):
>
> ```bash
> gigai setup --non-interactive --home ~/.gigai --workpad-root ~/gigai-workpads --editor /usr/bin/true \
>   --endpoint codex=codex_cli \
>   --model-target codex_cli=codex:default \
>   --target-output-limit codex_cli=4096 \
>   --json
> ```

### Bind a project

```bash
cd /path/to/target/repo
gigai init --username "agent" --json
```

`--target` accepts a path instead of the current directory. `gigai init` is
idempotent for an already-bound project.

### Scout: install and run

Everything below runs from an installed package (`uv tool install gigai`) —
no source checkout needed.

```bash
gigai scout install --json                    # bind, approve, and activate Scout for this project
gigai secrets add exa                         # store EXA_API_KEY locally — see "Exa search" below
gigai scout resume add ./resume.txt --json    # import + wrap your resume for find-jobs
```

`gigai scout install` writes a starter `<target_root>/find-jobs.json` the
first time it runs (never overwrites an existing one). Edit it before
starting Scout:

```json
{
  "schema_version": "find-jobs-config:1",
  "roles": ["software engineer", "data engineer"],
  "merged_queries": ["software engineer OR data engineer"],
  "location": "Denver, CO",
  "remote": true,
  "published_after": "2026-09-15T00:00:00Z",
  "sources": { "exa": true, "ats": true, "hiringcafe": false },
  "default_assess_cap": 10,
  "default_model_target": "ollama_local"
}
```

`default_model_target` accepts `ollama_local`, `codex_cli`, or
`openrouter_api`. `hiringcafe` is defined in the schema but not a live source
in this release; leave it `false`.

Then start it:

```bash
gigai scout run --json     # installs/activates if needed, starts the API + UI, opens a browser tab
gigai scout status --json  # running / stopped / crashed, with url/pid/log path
gigai scout stop --json    # stop it; safe to rerun
```

`gigai scout run` is backgrounded by default — it prints the URL and log
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

> **0.1.8.x limit:** `gigai scout resume add` only accepts `.txt`/`.md`/
> `.markdown` files up to 1 MB; a `.pdf` or `.docx` resume is rejected
> (`media_type_unsupported`). Convert it first, e.g.:
>
> ```bash
> pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
> textutil -convert txt resume.docx        # macOS built-in, .docx only
> ```

### Exa search

```bash
gigai secrets add exa
```

Or export it directly instead (environment takes precedence over a stored
secret):

```bash
export EXA_API_KEY=...
```

Without either, Exa discovery refuses to run; ATS-board acquisition is
unaffected.

### From a source checkout (contributors)

The Scout UI ships prebuilt inside the wheel (`gigai scout run` serves it
directly — nothing above needs a source checkout or a Vite dev server). If
you're editing `src/gigai/scout/ui/` itself, run its dev server against a
`gigai scout run` (or standalone `present_api`) instance for hot reload:

```bash
cd src/gigai/scout/ui
yarn install
yarn dev
```

Point it at the running API's loopback URL (`gigai scout status` prints it).
Rebuild the shipped bundle with `yarn build` before committing UI changes —
CI's `scout-ui-freshness` job fails the PR if `src/gigai/scout/ui/dist` is
stale.

### Reading results and failures

- `gigai run-details <run-id> --json` reads durable Run state.
- `gigai workpad path` prints the canonical path of a registered Gig
  workpad, where journal, proposal, and Run state live.
- Any command that supports `--json` prints a structured result on
  **stdout** on both success and failure (see `gigai doctor --json`). A
  malformed invocation — a bad flag or missing argument — is a Click usage
  error and goes to **stderr** as plain text; check the exit code, don't
  assume JSON is always present.

## Development

```bash
uv sync --locked --extra test
make test              # source + behavior + wheel-resource suites
make test-source       # source, unit, integration, and CLI tests only
uv run --locked pytest tests/behaviors/scout_find_jobs -q   # a focused slice
```

- `src/gigai/` — core runtime: setup, config, journal, proposal/approval
  lifecycle, catalog, package boundary. Never imports a Gig.
- `src/gigai/scout/` — the Scout Gig: `find_jobs/` (acquire/assess/present),
  bundled goal-graph data, and `ui/` (the separate Vite/React checkout above).
- `.orchestrator/` — this project's coordination record: worker reports,
  decisions, and status. Committed, not a scratch directory.

## Status

This is v0.1.8.1. Scout's `find-jobs` workflow is implemented and covered by
the test suite above, including a deterministic end-to-end path from
acquisition through the present API. It has not yet had a live-provider or
human user-acceptance pass; treat what's proven by tests as proven, and
everything else as unverified until it has been.

## License

Apache-2.0. See [LICENSE](https://github.com/karthik446/gigai/blob/main/LICENSE).
