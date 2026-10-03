---
title: Quickstart
description: From zero to a running Scout, in three steps.
---

One path from zero to a running Scout: find jobs, assess them, tailor a resume.

## 1. Requirements

- macOS or Linux, and Python 3.11+.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (one line: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **One model CLI, installed and logged in.** Codex: run `codex login`
  (check it with `codex login status`). Or Claude Code: run `claude`, then
  `/login`. A local Ollama model or an OpenRouter key also work, but are optional.
- Exa search is optional and off; you do not need it.
- Internet access for **Update sources** (it reads public job boards).

## 2. Prepare your resume

Use a Markdown (`.md`) or plain-text (`.txt`) file. Scout removes your name and
contact lines (email, phone, address, links) when it stores your resume, so they
are never kept and never sent to the model provider you picked, but it can't
catch personal details elsewhere in the text, so **keep those out** (anything
else you would not paste into Codex or Claude). A PDF or DOCX resume must be
converted first:

```bash
uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md
uvx --from 'markitdown[pdf,docx]' markitdown resume.docx > resume.md
```

This runs through `uv`, which you already have, so there is nothing else to
install. If you already have `pdftotext` (poppler), `pandoc` or macOS `textutil`,
those work too. Check the converted `resume.md` before you import it.

See [Resume and PDF](../resume/) and [Privacy and security](../privacy/).

## 3. Install

```bash
uv tool install gigai
gigai --version
```

To update later:

```bash
uv tool upgrade gigai
```

To install a specific release from git instead, use a tag from the
[Releases page](https://github.com/karthik446/gigai/releases) as the `@<tag>`
below; version-specific notes are in the [Changelog](../../changelog/).

```bash
uv tool install "git+https://github.com/karthik446/gigai@<tag>"
```

## 4. Run

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
   While Scout stays open it then re-checks the boards about once an hour by
   itself ([Background updates](../sources/#background-updates)).
2. **Jobs**: the page lists the stored postings that match your profiles,
   with no run to start. The background ranks them. Use the chips (profile,
   New since last check, 7 days, 30 days, state) and the search box to narrow
   the list.
3. **Assess these**: pick postings (or use the filter) and Scout asks first,
   with the count and an estimate; it assesses only when you approve.
4. **Tailor resume**: on a posting's page, drafts a resume for that posting.
   Review every line; each shows its sources. **Generate PDF** opens a small
   form for your name and contact details and saves the PDF; GigAI does not
   keep what you type there. Under Settings > Profiles, **Resume display**
   holds the title under your name and the PDF layout.

Prefer to work from your own AI agent? `gigai scout new` is the daily entry
point: see [For agents](../agents/).

`gigai scout stop` stops Scout; `gigai scout run --port 9000` picks another
port if 8765 is taken. More detail for scripts and agents is under
[For agents](../agents/). Every `gigai scout` command is in the
[Scout CLI reference](../reference/cli/).
