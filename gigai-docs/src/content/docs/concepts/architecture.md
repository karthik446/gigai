---
title: Architecture
description: GigAI core, Gigs, and the one-way boundary between them.
---

GigAI is two layers with a one-way dependency: **a Gig imports GigAI core; GigAI
core never imports a Gig.** Scout ships with GigAI today but gets no special
runtime treatment.

```mermaid
flowchart LR
  subgraph machine["Your machine"]
    CLI["gigai CLI"] --> Core
    subgraph Core["GigAI core"]
      Setup["setup / config / secrets"]
      Journal["journal + proposal/approval"]
      Targets["model targets"]
    end
    subgraph Scout["Scout Gig (find-jobs)"]
      Acquire --> Rank --> Assess --> Present
    end
    Scout -- imports --> Core
    Present --> UI["localhost API + web UI"]
    Home[("GigAI home ~/.gigai")]
    Core --- Home
  end
  Targets --> Model["Your model: Codex / Claude / Ollama / OpenRouter"]
  Acquire --> Boards["Public job boards"]
```

## Where things live

| Path | Owner | What |
| --- | --- | --- |
| `src/gigai/` | core | setup, config, journal, proposal/approval lifecycle, catalog, package boundary |
| `src/gigai/scout/` | Scout | `find_jobs/` (acquire, assess, present), goal-graph data, `ui/` |
| `~/.gigai/config.toml` | core | settings, model targets, credential references (names, never values) |
| `~/.gigai/scout/` | Scout | the Scout project: `find-jobs.json`, resumes, tailored resumes |

## Model targets

A model target names an adapter (`codex_cli`, `claude_cli`, `ollama_local`,
`openrouter_api`) and a model. `gigai setup` creates one per CLI it finds
(`codex-default`, `claude-default`); Scout's searches resolve the sealed adapter
name to whichever enabled target uses it.
