# GigAI command sheet

This is the public operator reference for the commands shipped in GigAI
`0.1.5`. It intentionally lists supported commands only; development plans,
schemas, test harnesses, and release internals do not belong in the package
command reference.

Check the installed version with:

```bash
gigai --version
```

## Install and set up

```bash
uv tool install gigai
gigai setup
gigai doctor
gigai models
gigai models --probe <target>
```

`setup` opens the local browser flow for the private GigAI home, workpad
location, model access, enabled models, and machine-wide role defaults.
`doctor` checks local configuration and storage. `models` shows detected and
configured models; `--probe` performs one explicit readiness check.

## Bind a target and create a Gig

```bash
gigai init
gigai init --target /path/to/folder
gigai create <gig-name>
gigai create <gig-name> --target /path/to/folder
```

`init` binds a Git repository or explicit local folder to GigAI. `create`
opens the browser-based Gig-definition interview. It creates a proposal for
review; it does not approve the proposal or run work by itself.

Optional create inputs include `--request`, repeated `--reference`,
`--model-target`, `--max-rounds`, and `--open/--no-open`.

## Review, revise, and approve

```bash
gigai gigs
gigai proposals
gigai status
gigai show
gigai plan
gigai feedback <proposal-id> --text "..."
gigai revise <proposal-id> --change "..."
gigai approve <proposal-id>
gigai reject <proposal-id> --reason "..."
```

These commands inspect proposal state and preserve operator decisions. Approval
creates an approved Gig version; rejection does not create one.

## Improve an existing Gig

```bash
gigai improve <manifest> --request "..." --reference /path/to/evidence
```

`improve` opens the separate improvement interview for evidence-backed changes
to an existing Gig. It remains subject to explicit review and approval.

## Run and inspect work

```bash
gigai run
gigai run <gig-id>
gigai run-details <run-id>
gigai history
gigai workpad path
gigai open
gigai check
```

`run` invokes an approved Gig. The inspection commands read existing local
state; they do not create a new proposal or silently change the target.

## Recurring and comparative work

Recurring work is explicit and operator-controlled:

```bash
gigai occurrence declare <daily|weekly|monthly> <occurrence-key> --snapshot <path>
gigai occurrence trigger <occurrence-id>
gigai occurrence reconcile <occurrence-id>
gigai occurrence mark <occurrence-id> <state> --reason "..." --actor-id <id>
gigai occurrence close <occurrence-id>
gigai occurrence compare <current-occurrence-id>
```

An occurrence does not imply a schedule, retry, fallback, or automatic Gig
mutation.

## Evaluations

```bash
gigai eval contract --manifest <manifest.json>
gigai eval behavior --manifest <manifest.json> \
  --observations <observations.json> \
  --split <development|calibration|final_held_out_acceptance>
```

Evaluation commands validate a declared corpus and score supplied observations.
They are explicit tools for evaluation work, not hidden execution behavior.

## Version history

| GigAI version | Command surface |
|---|---|
| `0.1.5` | Browser-first setup, model discovery and probing, target initialization, adaptive Gig creation, improvement interviews, proposal review, approved Gig Runs, occurrence/comparison inspection, and explicit evaluation commands. |

The next version gets its own row after release. A command is documented here
only after it is available in that published version.
