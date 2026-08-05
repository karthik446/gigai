# GigAI cheat sheet

GigAI Phase 1 is a local, contract-first proposal workflow. It can configure a
machine-local workpad, bind a target repository, create and review offline Gig
Proposals, approve an immutable Gig version, and inspect the resulting private
workpad history.

An approved Gig is a frozen, reviewable plan—not an executing job. GigAI Phase
1 does **not** schedule or execute Runs, invoke models to do Gig work, or
modify the bound target. `improve`, `run`, and `continue` are planned commands,
not current product behavior.

## Install

GigAI requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).
Until a release distribution is published, install from a local checkout:

```bash
git clone https://github.com/karthik446/gigai.git
cd gigai
uv tool install --editable .
gigai --version
gigai --help
```

To work on the repository itself rather than install a global tool:

```bash
uv sync --extra test
uv run gigai --help
```

To update a checkout-based tool installation:

```bash
git pull --ff-only
uv tool install --reinstall --editable .
```

## First local workflow

Choose a workpad location you control and an editor executable. GigAI stores
the editor as structured arguments, never as a shell command.

```bash
gigai setup --non-interactive \
  --workpad-root "$HOME/.gigai/workpads" \
  --editor /absolute/path/to/your-editor

gigai doctor
```

Bind a Git repository. This preserves tracked files and Git status; it creates
only an ignored `.gigai/project.toml` binding plus a local Git exclude entry.

```bash
cd /path/to/your/repository
gigai init
gigai init --json
```

Create an offline proposal, then inspect it. Save the `gp_...` proposal ID
returned by `create --json`; use its full value in automation.

```bash
gigai create security-review --json
gigai status --json
gigai plan
gigai check --json
gigai open
```

The proposal is reviewable but non-executable. Continue its local review
lifecycle with the returned proposal ID:

```bash
gigai feedback gp_<proposal-id> --text "Add failure-mode tests."
gigai revise gp_<proposal-id> --change "Add failure-mode tests." --json
gigai approve gp_<new-proposal-id> --json
```

Approval validates and seals a Gig version in the private workpad. It does not
start a Run, call a model, or modify the bound target.

To reject rather than approve a pending proposal:

```bash
gigai reject gp_<proposal-id> --reason "The scope needs revision."
```

## Inspect work that already exists

All of these commands work against the active Gig when you omit the optional
Gig ID. They remain local and do not invoke a provider.

```bash
gigai gigs --json
gigai proposals --json                  # active Gig
gigai status --json                     # active Gig
gigai show --json                       # active Gig
gigai history --json                    # active Gig
gigai plan --json                       # active Gig
gigai workpad path                      # active Gig

gigai proposals gig_<gig-id> --json     # explicit Gig
gigai status gig_<gig-id> --json
gigai show gig_<gig-id> --json
gigai history gig_<gig-id> --json
gigai plan gig_<gig-id> --json
gigai workpad path gig_<gig-id>
```

Open existing locations in the configured editor:

```bash
gigai open                         # active Gig workpad
gigai open gig_<gig-id>            # specific workpad
gigai open --target                # bound target only
gigai open --with-target           # active workpad and bound target
```

If no Gig is active, create and approve one first, or pass an explicit
`gig_...` ID. GigAI fails closed rather than guessing a latest workpad.

## Optional live provider diagnostic

Ordinary `gigai doctor` is offline and uses no credential value. A provider
call requires a separately configured endpoint and model target, followed by
both `--live` and `--model-target`.

For example, configure a credential **reference**—never put the key on the
GigAI command line:

```bash
export OPENAI_API_KEY="..."

gigai setup --non-interactive \
  --workpad-root "$HOME/.gigai/workpads" \
  --editor /absolute/path/to/your-editor \
  --credential-ref openai=environment:OPENAI_API_KEY \
  --endpoint openai=openai_api:openai \
  --model-target openai-luna=openai:gpt-5.6-luna \
  --target-output-limit openai-luna=4096 \
  --target-reasoning-effort openai-luna=high

gigai doctor --live --model-target openai-luna --json
```

The configured output-token limit bounds output length; it is not a USD spend
ceiling. Live diagnostics are local, opt-in, redacted, and excluded from CI.
The currently implemented remote adapters are OpenAI API and OpenRouter API;
Anthropic API, Codex CLI, and Claude CLI are deferred.

## What works today

| Capability | Current behavior |
|---|---|
| Machine setup | Local config, structured editor argv, workpad mount checks, credential references only |
| Diagnostics | Offline by default; one explicitly requested, redacted live model check |
| Target binding | Git and explicit non-Git targets, with no tracked-source changes |
| Proposal lifecycle | Create, feedback, revise, approve, reject; all durable in a private local Git workpad |
| Read/open surface | List and inspect Gigs, proposals, history, plans, validation, workpad paths, and editor opens |
| Integrity | Locked configuration, canonical IDs, proposal/graph validation, private journal recovery, rebuildable index |

## What is deliberately not implemented

- Run scheduling or execution
- Target mutation beyond the narrow `init` binding
- `improve` and `continue`
- Anthropic API, Codex CLI, and Claude CLI adapters
- Any automatic provider fallback or background network activity

For the complete current CLI surface, use `gigai --help`. For the intended V14
operator design—including planned commands—see the
[command sheet](command-sheet.md). The [Phase 1 completion audit](../development/evidence/phase-1/G10/completion-audit.md)
records the cross-platform and offline verification evidence behind this
cheat sheet.
