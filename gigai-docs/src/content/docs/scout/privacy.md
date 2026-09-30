---
title: Privacy and security
description: Exactly what leaves your machine, and to whom.
---

**Remove your personal info before adding a resume:** name, email, phone, street
address and links. Scout sends your resume text to the model you pick (Codex to
OpenAI, Claude to Anthropic, OpenRouter to your provider) and does not yet remove
personal info for you.

## Scout has no service of its own

Ranking and assessment both run on the model target you configured
(`ollama_local`, `codex_cli`, `claude_cli` or `openrouter_api`). There is no
ranking service, no extra key, and no extra third party.

| Step | What is sent | To |
| --- | --- | --- |
| Ranking | One short line per posting + a compact digest of your resume (titles, skills, years), batch by batch | Your model target |
| Assessment | The posting text and your resume, per assessed posting | Your model target |
| Tailoring | The posting text and your resume | Your model target |
| Update sources | Plain public requests, no key or login (your IP is visible) | Greenhouse, Lever, Ashby |
| Exa search (off by default) | Your target roles, a start date, a country code, your Exa key | Exa |

**With a local Ollama target nothing leaves the machine.** Scout only talks to
Ollama on a numeric loopback address (`127.0.0.1`).

## What stays local

- Everything Scout writes lives under your GigAI home (`~/.gigai` by default).
- Ranking scores are cached under `<home>/cache/scout/rank/scores/`, with no resume text.
- The **Resume display** fields (name, contact line) are stored only on your
  computer and added to the PDF locally.
