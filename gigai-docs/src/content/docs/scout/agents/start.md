---
title: Start here
description: Give this page to your AI agent before anything is installed. It sets GigAI up step by step, checks your resume for contact details first, and asks before anything that uses the network or a model.
---

**Remove your name, email, phone, address and links from your resume before you give it to an agent.**

This page is written for an AI agent (Claude Code, Codex or a similar one) that a user asked to
"set up GigAI". Follow the steps in order. If you are the user: paste the starter prompt from the
[README](https://github.com/karthik446/gigai#let-your-agent-set-it-up) into your agent, and read
the next section yourself. [Your first 10 minutes](../../first-10-minutes/) is the same path by
hand.

## What you should know first

Tell the user these before you start. They are GigAI's own words; do not reword them.

- **GigAI never stores your name, email, phone, address or links.** You type them only when you make a PDF, and GigAI forgets them right after.
- **Anything GigAI gives your agent is sent to that agent's model provider. Agents get no contact data from GigAI, but an agent with shell access can read local files.**
- **Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.** Scout checks about 10,000 public job boards (Greenhouse, Lever, Ashby): thousands of requests, and it keeps checking 8 times a day. An employer can see that traffic.

What the agent will see: the public job postings, the questions a posting leaves open, the
user's answers and stories, the body of the resume if the agent reads the file, and everything
the `gigai` command prints. All of that goes to the agent's model provider.

What the agent will not get from GigAI: the user's name, email, phone, address or links. GigAI
has none to give. For a PDF, the agent hands over an "open in Scout" link, and the user types
those details in their own browser, in a form with six fields (name, email, phone, location,
LinkedIn, one more link). The form fills that one PDF and nothing is kept.

**If a user pastes a resume with contact details straight into the chat, the agent's model
provider has them. That is outside GigAI's control.** GigAI's check below works on a file, before
the agent reads it. It can't take back what was already pasted.

[Privacy and security](../../privacy/) has the full list of what is stored and what is sent.

## Rules for the agent

- Ask the user before any command that needs `sudo`. None of the steps below needs it.
- Ask the user before **Update sources** (thousands of network requests) and before any step
  that spends model calls (assessing, tailoring, "process now").
- Never read a resume file before the check in step 5 says it is clean.
- Never ask for, type or send the user's name, email, phone, address or links.
- Never read `~/.gigai` directly. Use the `gigai` command.
- Tell the user what you installed and where.

## 1. Find out what is already there

```sh
uname -s            # Darwin = macOS, Linux = Linux or WSL
command -v uv       # is uv installed?
command -v gigai    # is GigAI installed?
command -v codex claude    # which model CLI is there?
```

GigAI runs on macOS and Linux. On Windows, use WSL and follow the Linux steps inside it; GigAI
does not claim native Windows support.

## 2. Install uv, if it is missing

[`uv`](https://docs.astral.sh/uv/getting-started/installation/) is the installer GigAI uses.

| System | Command |
| --- | --- |
| macOS with Homebrew (Apple Silicon or Intel) | `brew install uv` |
| macOS without Homebrew, Linux, WSL | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Neither needs `sudo`: the installer script puts `uv` in `~/.local/bin`. Do not install Homebrew
just for this (installing Homebrew itself can ask for an administrator password); use the
installer script instead. Open a new shell afterwards, or use `~/.local/bin/uv`, so that `uv` is
found.

GigAI needs Python 3.11 or newer. You do not have to install Python first: uv downloads one when
it finds no suitable Python. If the install in step 3 says it found no Python, run
`uv python install 3.12` and try again.

## 3. Install GigAI

```sh
uv tool install gigai
gigai --version
```

If `gigai` is not found, its folder is not on the `PATH` yet. Run `uv tool update-shell`, then
open a new shell. Until then, `~/.local/bin/gigai` works.

To assess postings later, the user needs one model CLI installed and logged in: Codex
(`codex login status` says whether it is) or Claude Code (`claude`, then `/login`). Scout starts
without one. Do not log in for the user; tell them the command.

## 4. Start Scout

```sh
gigai scout run --no-browser
gigai scout status --json
```

The first command writes GigAI's default settings on a new machine, starts Scout's local server
in the background and prints its address (`http://127.0.0.1:8765` unless the port is taken; then
add `--port`). `--no-browser` keeps it from opening a browser tab from inside an agent session.
Everything GigAI stores goes under `~/.gigai` on this computer, except the resume files it makes
for the user: each job's tailored resume (markdown) and the PDFs without a header go to the
resumes folder, `~/Documents/GigAI/resumes` unless the user chose another (`gigai scout status`
shows it). That folder never holds a name or contact details.

`--no-browser` is a choice, not a requirement: without it Scout opens the browser itself. When
the user wants to look at something, give them the address the command printed, or one job's
page: that address plus `#/jobs/` and the posting's URL percent-encoded. Inside a restricted
sandbox `gigai scout status` may answer `unreachable` (`process: running (pid N); API: not
reachable from here`). That is the sandbox blocking localhost, not Scout stopping: do not
restart it; check from outside the sandbox or ask the user to reload the page.

## 5. The resume: check first, read second

**Before you read the user's resume, run GigAI's contact-details check on the file.**

1. Ask the user for the **path** of the resume file (`.md` or `.txt`). Do not ask them to paste
   the resume. A PDF or DOCX is converted to a file first, without printing it:

   ```sh
   uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md
   ```

2. Run the check. It is local: no model, no network. It reports the kind and the line number of
   each finding, never the text itself.

   ```sh
   gigai scout resume check resume.md --json
   ```

3. Exit code 0 and `"clean": true`: nothing was found. Continue with item 5 of this list.
4. Exit code 2: **stop.** Do not read the file. Tell the user the kinds and line numbers (for
   example "line 1: name, line 2: email and phone") and ask them to remove those lines. Or, if
   they say "strip it", write a cleaned copy and check that copy:

   ```sh
   gigai scout resume clean resume.md --out resume-clean.md
   gigai scout resume check resume-clean.md --json
   ```

   The original file is not changed. From here on use only `resume-clean.md`.
5. Only now may you read the clean file. Import it:

   ```sh
   gigai scout resume add resume-clean.md --json
   ```

The check works on patterns: a name line, an email address, a phone number, a street address, a
link, a work-authorization line. It can't catch personal details elsewhere in the text (a first
line that holds both a title and your name, or contact details inside a sentence). Ask the user
to look at the cleaned file once.

## 6. Set up the profile

Ask the user a few questions:

- Which job titles are you looking for? (two or three, as postings word them)
- Which countries can you work from? (`US`, `CA`, ...)
- Remote, hybrid or on-site? For hybrid or on-site: which city?
- Do you need visa sponsorship?
- Which model CLI should Scout use: Codex (`codex_cli`) or Claude Code (`claude_cli`)?

Save the answers through Scout's local API (it is running since step 4):

```sh
curl -s -X PUT http://127.0.0.1:8765/api/setup -H 'Content-Type: application/json' \
  -d '{"roles": ["Staff Software Engineer", "Staff Backend Engineer"], "countries": ["US"], "work_mode": "remote", "visa_sponsorship_required": false, "model_target": "codex_cli"}'
gigai scout profile list --json
```

`work_mode` is `remote`, `hybrid` or `onsite`; add `"city": "Denver, CO"` for hybrid or on-site.
The second command shows the profile that was created, with the resume from step 5 attached. If
it prints a warning that a title alone matches very many postings (a generic title such as
"Staff Engineer"), tell the user and offer a more specific title.

## 7. Update sources: tell the user first, then ask

Before the first update, say this to the user, in these words, and wait for a yes:

> **Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.** Scout checks about 10,000 public job boards (Greenhouse, Lever, Ashby): thousands of requests, and it keeps checking 8 times a day. An employer can see that traffic.

On a yes:

```sh
gigai scout sources update
```

It uses the network and no model. One pass stops after 20 minutes; the first update over all
boards can take more than one pass, so run the command again until it says it is done.

While Scout is running it then checks the boards by itself (8 times a day on weekdays, twice a
day at weekends). The user can turn that off in the Scout UI under **Settings > Background
updates**, or you can, on their word:

```sh
curl -s -X PUT http://127.0.0.1:8765/api/settings/background -H 'Content-Type: application/json' \
  -d '{"sources": {"auto_refresh": false}}'
```

## 8. Show what is new

```sh
gigai scout new --json
```

It lists the new postings, ranked, and asks before it assesses: it gives the count and an
estimate. Tell the user both, and say that **the first run is the expensive one**: it catches up
on everything that is new, and later days are a few dozen postings at most.
[Token usage](../../tokens/) has measured numbers. Only on a yes:

```sh
gigai scout new --yes --json
```

Each assessment is one model call. Progress lines go to the error stream while it runs, so the
JSON on standard output stays clean. Then show the grid. [For agents](../) describes the rest of
the daily loop: the open questions, answers and stories, the background pipeline, the PDF.

## 9. Teach the agent the daily loop

So that tomorrow the user only has to ask "what's new on Scout?". Ask before you write either
file.

**Claude Code:**

```sh
gigai agent-skill --format skill --out ~/.claude/skills/gigai-scout/SKILL.md
gigai agent-permissions
```

The first command writes the skill file and creates the folders. The second prints a
permissions snippet. Show it to the user: they merge it into `~/.claude/settings.json` (all
projects) or `.claude/settings.local.json` (this project only). GigAI prints it and never
applies it, and you must not edit the user's agent settings yourself. Tell the user to start a
new Claude Code session for the skill to be listed.

**Codex:**

```sh
gigai agent-skill --format agents-md
```

This prints a section for an `AGENTS.md`. Codex reads `~/.codex/AGENTS.md` (every project) and
the `AGENTS.md` at the root of a repository. Ask the user which one, and add the section to the
end of it after a blank line. If Codex refuses to write the file, show the text and ask the user
to paste it.

**Any other agent:** the same `AGENTS.md` section, or paste what `gigai agent-context` prints.
[Use it from your agent](../#use-it-from-your-agent) has the table.

## 10. Later: upgrade

```sh
uv tool upgrade gigai
gigai scout run --no-browser
```

The second command restarts a Scout server that was left running from the older version. If the
upgrade says there is nothing newer and the user is sure there is, `uv tool install --reinstall
gigai` installs it again from scratch. Read "After you upgrade" in the
[Changelog](../../../changelog/) first.
