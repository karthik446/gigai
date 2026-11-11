---
title: USD caps and per-gig keys
description: How a Gig declares its keys, gets its own key with a spend cap, and reads what it may still spend.
---

A Gig names the keys it uses in its `gig.toml`, one `[[keys]]` row each. A row
may carry an `alias` (the Gig's own key beside the shared one) and a
`spend_cap` (what the Gig may spend through it):

```toml
[[keys]]
service = "openrouter"            # env defaults to the catalog's OPENROUTER_API_KEY
required = false
alias = "scout"                   # the per-gig key: OPENROUTER_API_KEY_SCOUT
spend_cap = { usd_per_day = 1.0, usd_per_run = 0.25, calls_per_day = 20 }
```

## The per-gig key

An alias names a second variable: the shared variable with the alias appended
(`OPENROUTER_API_KEY` + `scout` → `OPENROUTER_API_KEY_SCOUT`; `-` becomes
`_`). Set it in the environment (`OPENROUTER_API_KEY_SCOUT=...`): the
`gigai secrets` commands only take catalog services, so there is no CLI path for
an alias secret yet. A Gig that resolves its key through `ctx.budget.resolver`
tries the alias variable first (environment, then the secrets store), then the
shared one in the usual order. A Gig with an alias but no alias secret runs on
the shared key exactly as before.

The alias key is meant to be a provider *runtime key with its own USD limit*
(OpenRouter: a key created with `limit`). With a key provider attached
(`with_provider`), a key the provider reports as spent refuses the Gig with the
provider's own message and nothing is retried. Every `ctx.model` call on a
paid target resolves its credential through `ctx.budget.resolver`, so the alias
key is used when it is set. Two honest limits of this release: the OpenRouter
`limit` / `/key` behaviour is **unverified** until the first live run, and no
step attaches a key provider yet (the eval book does); the local caps and the
per-click cap below are what `ctx.budget` enforces.

## Per-click caps (`cap_usd`)

A step may carry a per-click USD cap: `cap_usd` on its `[steps]` row in
`gig.toml`, `gigai <gig> steps set <step> --cap-usd 0.05` in `steps.toml`
(`[caps]`, over the manifest's; `--cap-usd -` removes it), or `cap_usd=` on the
`ctx.model` call itself (over both; `None` lifts it for that call). `steps
--json` shows the cap that applies as `cap_usd`. Before a capped call reaches
any adapter, core estimates its worst case from a price table: the prompt's
characters divided by four at the model's prompt price plus `max_output_tokens`
at its completion price. An estimate over the cap is refused with
`estimated $0.06 > cap $0.05` (`ClickCapExceeded`, a `BudgetExhausted`; exit 2
in `eval run`, which also refuses a step whose output alone is over its cap
before loading the suite or writing a run row); an equal estimate passes. A
`deterministic` target is free and never estimated. A capped paid call
reserves the estimate against the local caps and settles it to the provider's
reported cost after the call; a call that fails or reports no cost keeps the
whole estimate charged (fail-closed: the ledger never guesses lower). An
uncapped call on a Gig with a `[[keys]]` row is not estimated (no price table
is fetched for it) and charges the reported cost. The trace keeps that cost in
`traces.cost_usd` (`null` when the provider reports none) with
`usage.cost_status` and, when estimated, `usage.estimate_usd`.

The price table is `<home>/prices.json` (`schema_version: "gig-prices:1"`,
USD per token by model id). Core fetches it from OpenRouter's models list the
first time an estimate needs it and never refreshes it on its own; a table older
than seven days warns once per process (`StalePricesWarning`) and is still
used. A model the table lacks, a target without `max_output_tokens`, or a table
that cannot be fetched refuses the call naming what is missing
(`PriceUnknownError`): nothing is guessed. The live fetch is **unverified**
until the first run against OpenRouter; the tests use a synthetic table.

## What `ctx.budget` gives a Gig

`ctx.budget` (`gigai.sdk.budget.GigBudget`) is the Gig's view of its caps:

- `remaining()` — USD and calls left today (`None` where nothing caps it);
  the per-run cap and the per-day cap both count, the tighter wins.
- `check()` — refuses (`BudgetExhausted`) when a cap is reached, the key is
  missing or its alias unusable, or, with a key provider attached
  (`with_provider`), when the provider reports the key spent; otherwise what
  is left.
- `charge(usd, calls=1)` — records a call that happened.
- `reserve(usd, cap_usd=None)` — the fail-closed pre-charge: refuse `usd` over
  the per-click cap, check the caps, fit, charge, all under one lock, so
  concurrent reservations never pass a cap together.
- `estimate(request, target)` — a call's worst-case USD from the price table
  (`prices`); `settle(reserved, actual)` moves the ledger from the estimate to
  the provider's reported cost.
- `resolver` — a credential resolver that prefers the alias key.

Today's spend lives in `<home>/gigs/<gig>/spend.json` (one UTC day; a new day
starts at zero). The per-run share is kept in memory for the current process.
A `spend.json` that is not today's numbers (corrupt, negative, not a number)
fails closed: every check, reservation and charge is refused with
`SpendFileError` naming the file until it is fixed or removed, and `status`
reports the error instead of a spend. No secret is ever stored or printed by
any of this: `status` reports presence by variable name only.

## `status --json`

Every Gig's `status --json` (core's verb, or Scout's own) carries two more
fields:

- `budget` — `caps`, `spent`, `remaining` and the `window` (`day`,
  `timezone: UTC`, `resets_at`), plus `key` (the service and alias the caps
  apply to) and `error` (the unreadable-spend-file message, else `null`;
  `spent` and `remaining` are `null` then). `null` when the Gig declares no
  keys.
- `keys` — one entry per `[[keys]]` row: `service`, `env`, `alias`,
  `alias_env`, `required`, `present`, `spend_cap` and `error` (why an alias
  cannot name a variable, else `null`). `[]` without keys.

```sh
gigai scout status --json | python -c 'import json,sys; d=json.load(sys.stdin); print(d["budget"], d["keys"][0]["alias"])'
```

## Scout

Scout declares three optional keys (`openrouter` with alias `scout`, `openai`,
`exa`) and no `spend_cap`, so nothing new caps it. Its own caps stay as they
are — `budget_usd_per_session` per discovery run and the daily call caps of the
background settings (at most 1000) — and read `ctx.budget` when one is given:
a Gig cap below them lowers what Scout allows, never raises it. See
[Model targets](./model-targets/) for how Scout picks the model a key is used
with.
