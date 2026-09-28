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
project's workpad. Tailored resumes (`gigai scout resume tailor`, `POST
/api/tailored-resumes`) are stored under the gig too (`scout/<project>/resumes/`,
ephemeral pasted-resume runs under `ephemeral/`) and contain resume-derived
text by design. Scout is one Gig among others GigAI can host; it gets no
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

Scout's find-jobs resolves a sealed model target (`ollama_local`, `codex_cli`,
`openrouter_api`) to whichever enabled configured target uses that adapter —
`gigai setup`'s auto-named target (e.g. `codex-default`) just works, no
special naming needed. If you followed an older 0.1.8.x version of this
README and already have a target literally named `codex_cli` (or
`ollama_local`/`openrouter_api`), that still resolves correctly too. Only
having *two* enabled targets on the same adapter with neither named exactly
the sealed value is an error — disable or remove one (`gigai setup` or edit
`config.toml`).

`gigai setup`'s auto-created targets default to a 4096-token output
allowance, enough headroom for a real assessment's JSON output (5-12
requirement-matrix rows plus suggestions/questions); `codex-default` just
works here too, no `--target-output-limit` needed. If you configure a
target by hand with a small custom limit, raise it with `gigai setup
--target-output-limit codex-default=4096` (or a higher value) if assess
responses come back truncated.

### Bind a project

```bash
cd /path/to/target/repo
gigai init --username "agent" --json
```

`--target` accepts a path instead of the current directory. `gigai init` is
idempotent for an already-bound project.

### Scout: install and run

Everything below runs from an installed package (`uv tool install gigai`) —
no source checkout needed, and Scout runs from anywhere: no `cd` into a
target repo and no `gigai init` step first.

```bash
gigai scout install --json                    # bind, approve, and activate Scout
gigai secrets add exa                         # store EXA_API_KEY locally — see "Exa search" below
gigai scout resume add ./resume.txt --json    # import + wrap your resume for find-jobs
```

Scout always lives in `<home>/scout` (`~/.gigai/scout` by default): every
`gigai scout ...` command uses it, whichever folder you run the command from,
and creates it the first time. `--target <dir>` is the only way to use another
folder. A Scout project you set up somewhere else with an earlier version is
left untouched; Scout names it once and you open it with `--target <dir>`.

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
  "default_model_target": "ollama_local",
  "countries": ["US"],
  "visa_sponsorship_required": false
}
```

`default_model_target` accepts `ollama_local`, `codex_cli`, or
`openrouter_api`. `hiringcafe` is defined in the schema but not a live source
in this release; leave it `false`. `countries` is a list of ISO-3166 alpha-2
codes to filter postings by; a posting whose location resolves to a
region-only label (e.g. `AMER`, `EMEA`) with no specific country never
matches. `visa_sponsorship_required` excludes postings whose sponsorship
read comes back "not offered" when `true`. Acquire also caps results to at
most 2 postings per company for diversity, and results appear as cards on
the UI as the run progresses.

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

### Scout: update sources

Checking the company boards is its own step, separate from searching:

```bash
gigai scout sources update          # check every company board on the watchlist
gigai scout sources status --json   # the last update, and whether the stored postings are current
```

`gigai scout sources update` (the UI's **Update sources**, `POST
/api/sources/update`) makes one polite conditional request per board on your
watchlist: the catalog companies your setup admits plus the ones you added.
An unchanged board costs one small request and changes nothing. It prints
what it found, e.g. `120 companies with new postings: 412 new, 95 changed,
230 removed`. An update stops at its time budget (20 minutes by default;
`--budget-seconds 0` for no limit) and the next one continues with the boards
it has not reached yet, so the first update over the full catalog takes a few
passes and later ones a single short pass.

What it learns is stored on this machine only, one plain JSON file per
company:

```
<home>/cache/scout/companies/<ats>:<slug>.json
```

Each file lists the company's postings with when each was first seen, last
seen, changed or removed. It is a cache, not a record: it is safe to delete
(one file or the whole folder), and the next update rebuilds it from the
board responses already cached under `<home>/cache/scout/ats-boards/`.
Nothing in it leaves the machine.

**Find jobs reads that store; it does not check the boards itself.** A search
takes your watchlist companies' stored postings, applies your titles, the
publication window and the country rule, ranks them and assesses the top
ones, with no board request, so it takes seconds, not minutes. Run **Update
sources** first, and again whenever you want fresh postings:

- nothing stored yet: the search says `Run Update sources` instead of
  fetching;
- the last update is more than a day old: the search still uses what is
  stored and says the postings are out of date.

The one exception: a company Exa discovers during a search that is not in
the store yet is fetched then and added to the store, so its postings can be
assessed in that same run. At most 20 such companies are fetched per search.
The limit counts companies, not requests: one company can take more than one
request (on Greenhouse, one for the list and one per posting whose title
matches yours). Companies over the limit wait for the next **Update
sources**.

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
make api-e2e           # Scout's HTTP API end to end, against a real server
uv run --locked pytest tests/behaviors/scout_find_jobs -q   # a focused slice
```

`make api-e2e` drives the Scout find-jobs API only through HTTP, against the
real supervised server, a temp home, and a real managed workpad; only the
network edges (Exa/ATS, the local model) are faked. It's localhost-only,
makes no live provider calls, and takes a couple of minutes.

- `src/gigai/` — core runtime: setup, config, journal, proposal/approval
  lifecycle, catalog, package boundary. Never imports a Gig.
- `src/gigai/scout/` — the Scout Gig: `find_jobs/` (acquire/assess/present),
  bundled goal-graph data, and `ui/` (the separate Vite/React checkout above).
- Coordinator workpad (worker reports, decisions, status) lives outside this
  repo in the orchestrator home's `orchestrator/` folder; never committed.

## Status

This is v0.1.8.1. Scout's `find-jobs` workflow is implemented and covered by
the test suite above, including a deterministic end-to-end path from
acquisition through the present API. It has not yet had a live-provider or
human user-acceptance pass; treat what's proven by tests as proven, and
everything else as unverified until it has been.

## License

Apache-2.0. See [LICENSE](https://github.com/karthik446/gigai/blob/main/LICENSE).
