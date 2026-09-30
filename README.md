# GigAI

GigAI is a local, user-controlled agent runtime. It keeps configuration,
credentials, and work state under an operator-selected home directory, and
makes every model call, review, and result inspectable rather than treating
one model response as proof of correctness.

A **Gig** is a self-contained goal-graph package built on top of GigAI core.
The boundary is fixed in one direction only — **a Gig imports GigAI core;
GigAI core never imports a Gig.** Gigs are portable, reviewable units of
work, not plugins the runtime depends on.

## Quickstart (Scout)

One path from zero to a running Scout: find jobs, assess them, tailor a resume.

**1. Requirements**

- macOS or Linux, and Python 3.11+.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (one line: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **One model CLI, installed and logged in.** Codex: run `codex login`
  (check it with `codex login status`). Or Claude Code: run `claude`, then
  `/login`. A local Ollama model or an OpenRouter key also work, but are optional.
- Exa search is optional and off; you do not need it.
- Internet access for **Update sources** (it reads public job boards).

**2. Prepare your resume, with your personal info removed**

Use a Markdown (`.md`) or plain-text (`.txt`) file, **with your name, email,
phone, street address and links/URLs removed** (and anything else you would not
paste into Codex or Claude). Assessment and tailoring send your resume text to
the model provider you picked, and Scout does not yet remove personal info for
you. A PDF or DOCX resume must be converted first:

```bash
pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
textutil -convert txt resume.docx        # macOS built-in, .docx only
```

**3. Install**

```bash
uv tool install gigai
gigai --version
```

Or install from the release tag (this works once the `v0.1.9` tag exists):

```bash
uv tool install "git+https://github.com/karthik446/gigai@v0.1.9"
```

**4. Run**

```bash
gigai scout run      # starts Scout and opens the browser
```

The first run on a new machine creates GigAI's settings with their defaults
(`~/.gigai/config.toml`) and says so; there is nothing else to set up first.
In the browser, the setup wizard asks you to pick the model ("Model for
Scout"), add your resume (paste it or upload the `.md`/`.txt` file), and set
your roles, location and work mode. Then:

1. **Update sources** (in Settings): fills the company store from the public
   job boards. The first update over the whole catalog runs in passes of up to
   20 minutes each (the default time budget) and continues where it stopped;
   later updates are a single short pass.
2. **Run find jobs** (Jobs page): ranks the stored postings that pass your
   filters and assesses the top ones.
3. **Assess all new**: on the finished run, assesses the rest in the background.
4. **Tailor resume**: on a posting's page, drafts a resume for that posting.
   Review every line; each shows its sources.

`gigai scout stop` stops Scout; `gigai scout run --port 9000` picks another
port if 8765 is taken. More detail for scripts and agents is under
[For agents](#for-agents) below.

## Scout, the first Gig

Scout ships with GigAI and implements `find-jobs`, a job-search workflow:

- **Acquire** — pull public postings from the Greenhouse/Lever/Ashby
  applicant-tracking boards through an auto-managed watchlist, plus Exa
  search if you turn it on (it is off for a new setup).
- **Rank** — every posting that passes your filters (titles, location, the
  publication window, visa sponsorship, work mode) is ranked by the model
  target you already use, and results stream in as batches finish. The order
  is honest but coarse: *likely fits first, likely no-matches last*. It
  pre-filters hard blockers (a no-sponsorship or citizenship line, a
  clearance requirement) by moving those postings down, never by hiding
  them. It does not claim to put the best match first.
- **Assess** — build a requirements-by-resume matrix for the top-ranked
  postings, in the background, while you browse. Defaults to a local model
  target; hosted targets are only used when explicitly configured.
- **Present** — a localhost API and a small web UI show acquired and assessed
  postings. What your model target sees is
  exactly what leaves your machine for ranking and assessment; the other
  network traffic is listed in
  [Privacy and security](#privacy-and-security).

Everything Scout writes stays under your configured GigAI home and the bound
project's workpad. Tailored resumes (`gigai scout resume tailor`, `POST
/api/tailored-resumes`) are stored under the gig too (`scout/<project>/resumes/`,
ephemeral pasted-resume runs under `ephemeral/`) and contain resume-derived
text by design. Tailored resumes are drafts: review each line; every line shows its sources.
Two confirming live runs on the release candidate (155 and 151 lines) found no fabricated facts
once one judge-flagged plural ("Kubernetes platforms" for the source's "Kubernetes platform") was
reviewed as a wording difference, not a new fact; 1 minor precision flag in each run (0.6% and 0.66% of lines).
Scout is one Gig among others GigAI can host; it gets no
special runtime treatment.

## Privacy and security

**Remove your personal info before adding a resume:** name, email, phone,
street address and links. Scout sends your resume text to the model you pick
(Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider) to assess
postings and tailor your resume, and it does not remove personal info for you
yet. With Ollama it stays on this machine, but keep it out anyway if you might
switch models. The setup wizard, the Assessments page, Settings > Profiles and
`gigai scout resume add` all show this warning, and the wizard also runs a
local check (no model) that lists any email, phone, linkedin.com/github.com
link or street address it spots; it can miss things.
**Scout has no service of its own.** Ranking and assessment both run on
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
  of the ranking digest where it can recognize them, but do not rely on it:
  remove personal info from the resume you add. The full resume and the
  full posting text are not sent for ranking.
- **Assessment and tailoring** send your resume text: an assessment sends the posting text and your resume for each posting
  being assessed, including each posting "Assess all new" assesses. A pasted resume is used for that assessment only: its full text is never saved and never sent anywhere but your assessment model; the stored result keeps short evidence quotes on your machine.
- **With a local Ollama target nothing leaves the machine.** Scout only
  talks to Ollama on a numeric loopback address (`127.0.0.1`).
- With `codex_cli` (the Codex CLI sends it to OpenAI), `claude_cli` (the
  Claude Code CLI sends it to Anthropic) or `openrouter_api`, that provider
  sees what is listed above under its own terms. The two CLIs add their own
  instructions to each call; for an assessment the Claude Code CLI also loads
  your own Claude Code settings, as any `claude -p` call does.

Ranking scores are cached on disk under `<home>/cache/scout/rank/scores/`
(a score, up to two short reasons and any blockers per posting, no resume
text); the cache is safe to delete. What ranking costs is whatever your
model target charges; a run makes a bounded number of ranking calls, and
postings past that bound stay unranked and keep date order.

**Assess all new.** A run assesses its top-ranked postings automatically (the
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

**Network traffic besides your model.** Scout makes these other requests:

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

## For agents

It's mostly agents — Claude, Codex, and similar — driving GigAI, so these
commands are non-interactive and scriptable. Every one below was run against
this build.

### Setup, projects and Gigs (advanced)

`gigai setup` is interactive by default (workspace, access, models, roles).
Scout users do not need it: the first `gigai scout run` (or `gigai scout
install`) writes the same settings `gigai setup` writes when you accept every
default, and never changes an existing config. Run `gigai setup` to change
them.
`gigai doctor` confirms the install is healthy. `gigai init` binds a git
repository as a GigAI project and `gigai gigs` lists the Gigs registered for
it; Scout needs neither.

```bash
gigai doctor
cd /path/to/a/git/repo
gigai init          # bind this repository as a GigAI project
gigai gigs          # list Gigs registered for this project
```

Non-interactive setup:

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
`claude_cli`, `openrouter_api`) to whichever enabled configured target uses that adapter —
`gigai setup`'s auto-named target (e.g. `codex-default` or `claude-default`) just works, no
special naming needed. If you followed an older 0.1.8.x version of this
README and already have a target literally named `codex_cli` (or
`ollama_local`/`openrouter_api`), that still resolves correctly too. Only
having *two* enabled targets on the same adapter with neither named exactly
the sealed value is an error — disable or remove one (`gigai setup` or edit
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
no source checkout needed (the first `gigai scout run` or `gigai scout install` writes GigAI's
default settings), and Scout runs from anywhere: no `cd` into a
target repo and no `gigai init` step first.

```bash
gigai scout install --json                    # bind, approve, and activate Scout
gigai secrets add exa                         # optional: only if you turn on Exa — see "Exa search" below
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
  "work_mode": "hybrid",
  "remote": false,
  "published_after": "2026-09-15T00:00:00Z",
  "sources": { "exa": false, "ats": true, "hiringcafe": false },
  "default_assess_cap": 10,
  "default_model_target": "ollama_local",
  "countries": ["US"],
  "visa_sponsorship_required": false
}
```

`work_mode` is `remote`, `hybrid`, `onsite` or `any`, and it is a real
filter: **Remote** keeps remote postings (and any whose mode can't be told); **Hybrid** with an area (your
`location`, e.g. "Denver, CO") keeps remote and hybrid postings in that
area; **Onsite** with an area also keeps on-site ones there; **Any** keeps
everything the other filters allow. A posting's mode comes from the job
board's own field when it has one, else it is read from the location text
and labelled as derived. A posting whose location says nothing usable (for
example just "United States") is kept and labelled rather than dropped.
Without a `work_mode`, the answer saved in setup is used, else Any.
`default_model_target` accepts `ollama_local`, `codex_cli`, `claude_cli`, or
`openrouter_api`. A new file starts on `ollama_local`; the setup wizard's
"Model for Scout" choice is saved here when you press Finish, and it is also
the model that reads your resume in the wizard. You can set `codex_cli` or
`claude_cli` here yourself (the run dialog can also
pick the target for one run). `hiringcafe` is defined in the schema but not a live source
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
publication window, the country rule and your work-mode preference, ranks
every posting that passes, and fully assesses the top-ranked ones (the run's
"Full assessments" setting) in the background, with no board request, so it takes seconds, not minutes. **Assess all new** on the finished run assesses the rest. Run **Update
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

> **Resume input:** `gigai scout resume add` accepts `.txt`, `.md` and
> `.markdown` files up to 1 MB (the setup wizard's upload takes the same three
> types); a `.pdf` or `.docx` resume is rejected (`media_type_unsupported`).
> Convert it first, e.g.:
>
> ```bash
> pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
> textutil -convert txt resume.docx        # macOS built-in, .docx only
> ```
>
> Remove your personal info from the file first: see the
> [Quickstart](#quickstart-scout).

### Exa search

You need one model target to start: Codex (`codex_cli`) or Claude
(`claude_cli`); `ollama_local` and `openrouter_api` are optional alternatives.
Exa is an optional extra, off for a new setup.
To turn it on, store a key with the command below, then tick **Also search the
open web with Exa (needs an Exa key)** in Settings.

```bash
gigai secrets add exa
```

Or export it directly instead (environment takes precedence over a stored
secret):

```bash
export EXA_API_KEY=...
```

Without either, Exa refuses to run; ATS-board acquisition is
unaffected. An existing `find-jobs.json` keeps the Exa setting it already has.

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

## Roadmap / TODO

- [ ] Download PDF for tailored resumes (replaces Download .md) (0.1.10)
- [ ] Resume display settings, kept on your machine (0.1.10): fill in your name, title and contact line once (location | work authorization | LinkedIn | GitHub | email | phone) on your profile. These fields are stored only on your computer and are never sent to Codex, Claude, Ollama, OpenRouter, Exa or any other service; Scout adds them to your PDF locally, after the model has finished. A test will check that none of them appear in anything sent to a model or the network.
- [ ] Remove personal info from resumes automatically before any model call (until then: add a resume without it)
- [ ] Import PDF/DOCX resumes directly
- [ ] Interview prep (0.1.10): research the company and likely interview questions through your own Codex or Claude CLI (no separate API key)
- [ ] The setup wizard checks the chosen CLI is installed and logged in
- [ ] Mark an assessment "posting changed, re-assess" when a posting's text changes
- [ ] Show the posting's own keywords on the tailored resume summary
- [ ] Faster settings load (workpad cache); consistent tailor judging

### Known limitations

- Alpha: expect rough edges (see [Status](#status)).
- macOS and Linux only.
- Filters default to the US (`countries` starts as `["US"]`); other countries
  can be set in the setup wizard or `find-jobs.json`.
- Assessments saved before the Lever fix stay as they were until you
  re-assess them (an old "Matched" on a Lever posting may not hold).
- The run dialog does not save "All new postings" as your default; set it in
  `find-jobs.json` (`"default_assess_cap": "all"`) if you want it to stick.

Contributions and bug reports are welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[issues](https://github.com/karthik446/gigai/issues).

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

This is v0.1.9. Scout's `find-jobs` workflow is implemented and covered by
the test suite above, including a deterministic end-to-end path from
acquisition through the present API. 0.1.9 is an alpha: it has been through hands-on testing by the maintainer (two UAT rounds, 2026-09-27 and 2026-09-29); expect rough edges.

## License

Apache-2.0. See [LICENSE](https://github.com/karthik446/gigai/blob/main/LICENSE).
