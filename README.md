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

> **0.1.8.x workaround; v0.1.9 replaces this with `gigai scout setup/run`.**
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

### Scout: install and approve

> **0.1.8.x workaround; v0.1.9 replaces this with `gigai scout setup/run`.**

Binding a project does **not** auto-materialize Scout as a candidate Gig, and
Scout is not in `gigai catalog list` either. The only current path is the
package's own Python interpreter running the internal helper, then approving
the resulting proposal:

```bash
"$(uv tool dir)/gigai/bin/python" -c '
from gigai.default_init import initialize_defaults
from gigai.scout.template import scout_candidate_inventory
initialize_defaults(inventory=scout_candidate_inventory())
'
gigai approve <proposal-id> --gig <gig-id> --json
```

Run `gigai approve --help` for the exact required IDs, and `gigai gigs
--json` to confirm Scout is active once approved.

### Add a resume reference

> **0.1.8.x limit:** only `.txt`/`.md`/`.markdown` files up to 1 MB are
> accepted; a `.pdf` or `.docx` resume is rejected (`media_type_unsupported`).
> Convert it first, e.g.:
>
> ```bash
> pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
> textutil -convert txt resume.docx        # macOS built-in, .docx only
> ```

```bash
gigai reference add --kind resume --file ./resume.txt --json
```

`--kind` also accepts `project_evidence`, `role_history`, `cover_letter`.

### Setting the active Gig

> **0.1.8.x workaround; v0.1.9 replaces this with `gigai scout setup/run`.**

The Scout API resolves the bound project's active Gig, but no CLI command
sets it after approval. Until v0.1.9, edit
`<target_root>/.gigai/project.toml` directly and add:

```toml
active_gig_id = "<scout gig id>"
```

### Wrapping an imported resume for find-jobs

> **0.1.8.x workaround; v0.1.9 replaces this with `gigai scout setup/run`.**

`gigai reference add` alone is not enough: find-jobs only reads a resume
wrapped as a private record. After adding the reference, wrap it:

```bash
gigai record create --kind imported_reference --content-family g45_reference \
  --content-id <reference-id> --operation-key <operation-key> \
  --gig <gig-id> --json
```

### The `find-jobs.json` config

Scout's find-jobs run reads an operator-authored config from
`<target_root>/find-jobs.json`:

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

### Exa search

```bash
export EXA_API_KEY=...
```

Without it, Exa discovery refuses to run; ATS-board acquisition is
unaffected.

### Start the API

Works from an installed package (`uv tool install gigai`) — no source checkout needed:

```bash
uv tool run --from gigai python -m gigai.scout.find_jobs.present_api --home ~/.gigai --target /path/to/target/repo
```

Loopback-only HTTP server (it refuses any non-loopback peer) that the Scout
UI talks to.

### The UI is not in the wheel

The Scout web UI is **not bundled in the published package**. It runs from a
source checkout only:

```bash
git clone https://github.com/karthik446/gigai.git
cd gigai/src/gigai/scout/ui
yarn install
yarn dev
```

Point the dev server at the localhost API started above. PyPI gives you the
CLI and API server, not a built UI.

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
