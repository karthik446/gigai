---
title: Configuration
description: find-jobs.json, work mode, model targets and Exa search.
---

## find-jobs.json

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
pick the target for one run).

`hiringcafe` is defined in the schema but not a live source
in this release; leave it `false`. `countries` is a list of ISO-3166 alpha-2
codes to filter postings by; a posting whose location resolves to a
region-only label (e.g. `AMER`, `EMEA`) with no specific country never
matches. `visa_sponsorship_required` excludes postings whose sponsorship
read comes back "not offered" when `true`. Acquire also caps results to at
most 2 postings per company for diversity, and results appear as cards on
the UI as the run progresses.

## Exa search

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

## Background updates

**Settings > Background updates** holds what Scout does by itself while it is
open. Nothing is saved until you press **Save**. The same settings are at
`GET` / `PUT /api/settings/background`, and are stored per project in
`<home>/scout/<project_id>/settings.json`.

| Setting | Default | What it does |
|---|---|---|
| Check the company boards automatically (`sources.auto_refresh`) | on | The hourly check of the company boards. Off also pauses tagging titles with the model in the background. |
| Tag titles with the model (`tagging.model_enabled`) | on | Sends titles the built-in rules cannot place to the model Scout is set up with, only for the roles your profiles search for. |
| Tag the rest in the background (`tagging.backfill_enabled`) | off | Also tags every other stored title, a few at a time. `tagging.tag_backfill_model` picks the model: `configured` (the model Scout is set up with) or `haiku` (Claude Haiku through the Claude CLI). |
| Use the shared starter snapshot (`snapshot.enabled`) | on | **Update sources** first downloads the [starter snapshot](../sources/#the-starter-snapshot). `snapshot.manifest_url` (under "Advanced") is where it is read from; leave it empty for the default address. |

An environment variable overrides the saved value while it is set, and the
page says so beside the setting: `GIGAI_SCOUT_AUTO_REFRESH`,
`GIGAI_SCOUT_MODEL_TAGS` and `GIGAI_SCOUT_SNAPSHOT` take `0` or `1`;
`GIGAI_SCOUT_SNAPSHOT_MANIFEST_URL` takes an address. If the settings file
cannot be read, every background job is off and the page will not save over
it: fix or remove the file.

## Where Scout lives

Scout always lives in `<home>/scout` (`~/.gigai/scout` by default): every
`gigai scout ...` command uses it, whichever folder you run the command from,
and creates it the first time. `--target <dir>` is the only way to use another
folder. A Scout project you set up somewhere else with an earlier version is
left untouched; Scout names it once and you open it with `--target <dir>`.
