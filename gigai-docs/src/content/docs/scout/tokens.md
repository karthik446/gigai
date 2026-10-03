---
title: Token usage
description: What Scout's model calls cost in tokens and time, per step, on Codex and on Claude Code, and how to see your own numbers.
---

Scout's model calls run on your own Codex or Claude Code login, so they count against that
subscription's usage allowance. This page gives measured token counts and times, not prices.
Every number below says where it comes from and when it was measured. Yours will differ: run
`gigai scout metrics` for your own.

## What an agent costs

Two different things use tokens when you drive Scout from an agent:

- **Scout's own model calls**: ranking, assessing, tagging titles, tailoring. This page is about
  these. Scout records each one on your computer (kind, model, tokens, seconds; no text).
- **Your agent's own conversation**: the grid it reads, your questions and its replies. That is
  your agent's usage. GigAI does not see or measure it.

What keeps Scout's side under your control:

- **It asks before it assesses.** `gigai scout new` (and **Assess these** in the UI) gives the
  count and an estimate from your own past calls, and assesses only on a yes. One model call per
  posting.
- **`gigai scout metrics` shows the averages** once the first calls are recorded: tokens,
  seconds and error rate per kind of call and per model. A token estimate is shown only after a
  call has been measured.
- **Daily caps for background work.** The background pipeline (tailor, then assess the tailored
  resume) makes at most **40** model calls a day. Background ranking makes at most **100** calls
  a day, with a warning past 60. Both are in **Settings > Background pipeline**. A call that
  would go over the cap is not made.
- **The first run is the expensive one.** The first `gigai scout new --yes` catches up on every
  new posting at once. After that, a day is a few dozen new postings at most.

## Tokens per step

One call each, on the same made-up resume and two short made-up postings, through GigAI's own
adapters on a scratch install. "Tokens" is input plus output; "cached" is the part of the input
the provider served from its prompt cache.

| Step | What one call covers | Codex CLI | Claude Code CLI |
| --- | --- | --- | --- |
| Rank | 50 postings | 11,092 tokens (9,446 in, 0 cached; 1,646 out), 45 s | 7,100 tokens (4,709 in, 0 cached; 2,391 out), 13 s |
| First assessment | 1 posting (average of 2 calls) | 11,630 tokens (10,948 in; 682 out), 21 s | 11,977 tokens (10,465 in, 531 cached; 1,512 out), 11 s |
| Tag titles | 50 titles | 8,836 tokens (8,125 in, 3,840 cached; 711 out), 26 s | 3,586 tokens (2,547 in, 0 cached; 1,039 out), 6 s |
| Tailor | 1 resume for 1 posting | 10,338 tokens (9,974 in, 0 cached; 364 out), 14 s | 9,652 tokens (8,929 in, 531 cached; 723 out), 6 s |
| Re-assess after tailoring | 1 posting, the tailored resume | 11,838 tokens (11,016 in, 3,840 cached; 822 out), 24 s | 12,197 tokens (10,633 in, 531 cached; 1,564 out), 11 s |
| Scout ATS score | 1 resume | 0 tokens: computed on your computer, no model | 0 tokens: computed on your computer, no model |

Source: GigAI 0.1.10.8 development build, 2026-10-03, codex-cli 0.159.3 and Claude Code
2.1.288, each CLI's default model, six calls per CLI, read with `gigai scout metrics --json`.
Of the two Codex first assessments, one had no cached input and one had 3,840 cached tokens.
The Claude Code CLI ran the rank and tag calls on `claude-sonnet-5-5`.

How to read it:

- **These postings were short.** A real posting is several times longer, so a real assessment
  uses more input tokens than this table shows. See the next section.
- **Most of an assessment's input is not your text.** Each CLI adds its own instructions to
  every call, and Scout's prompt carries the rules and the answer format. That part repeats on
  every call, which is why a provider can cache it.
- **A job you answered a question for costs two more calls**: one tailoring and one
  re-assessment. The Scout ATS score and the Scout label after them are local.
- **One rank call covers 50 postings**, so ranking is cheap per posting, and a posting is ranked
  once for a given resume and settings.
- **Six calls are a sample, not an average.** Time per call moves with the provider's load.

The Claude Code CLI also reports an API price for each call (0.02 to 0.06 US dollars per call
in this sample). On a subscription that is not what you are billed; it counts against your
allowance. The Codex CLI reports no price.

## A real run: the first catch-up and a normal day

Real numbers from the maintainer's own install: GigAI 0.1.10.7, Codex CLI on a ChatGPT
subscription, default model, real postings, read on 2026-10-03 with `gigai scout metrics --json`.

| Kind | Calls | Average per call | Cached share | Time per call |
| --- | --- | --- | --- | --- |
| Assess | 322 (1 failed) | 23,526 tokens (22,027 in; 1,499 out) | 13,014 of the 22,027 input tokens (59%) | 42 s |
| Rank | 30 calls for 1,433 postings | 21,079 tokens (19,431 in; 1,648 out) | 12,105 of the 19,431 input tokens (62%) | 45 s |

What that meant for the subscription, as that user read it on the same day (examples, not a
promise for your plan):

- About **180 assessments and 4 rank calls used about 3%** of the Codex allowance: roughly
  0.017% per assessment.
- **The first catch-up, 333 postings, used about 5 to 6%** and took about an hour (42 seconds
  each, 4 at a time).
- **A normal day** is a few dozen new postings. Worked out from the numbers above, 30
  assessments are about 0.7 million tokens, about 5 minutes, and about half a percent of that
  allowance.

Two things changed in 0.1.10.8 that make these 0.1.10.7 numbers an upper guide for Codex:
Scout now turns every Codex tool off, so Codex no longer sends its tool definitions with each
call. On a made-up prompt during development (2026-10-03) the input of one Codex call fell from
about 33,800 tokens to about 7,000. How much a real assessment falls has not been measured yet.

## Codex or Claude Code

From the six-call sample above, for the same input:

- **Tokens per assessment were about the same** (11,630 on Codex, 11,977 on Claude Code).
- **Claude Code answered faster** (11 s against 21 s for an assessment, 13 s against 45 s for a
  rank call).
- **Rank and tag calls used fewer tokens on Claude Code** (7,100 against 11,092, and 3,586
  against 8,836): Scout calls Claude Code in a lean mode for those, with a one-line system
  prompt.
- **What a token is worth differs per plan.** A share of an allowance is only known for the
  Codex example above. No Claude Code allowance share has been measured.

## See your own numbers

```sh
gigai scout metrics                  # averages per kind of call and model
gigai scout metrics --kind assess    # one kind: assess, rank, tag, tailor, extract or interview
gigai scout metrics --json           # the same, for a script or your agent
```

In the UI the same table is under **Settings > Model usage**. Everything is read from a file on
your computer; nothing is sent anywhere to show it.

`gigai scout pipeline status` shows today's background calls against the daily cap.
