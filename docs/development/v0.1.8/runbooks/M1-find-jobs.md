# M1 find-jobs operator runbook

This runbook starts the source-checkout M1 workflow: a fresh project, an
approved Scout graph, a committed resume, one project-local `find-jobs.json`,
the loopback API, and the UI's **Run workflow** button. It is a local workflow;
the assess target is the explicitly configured Ollama model and there is no
silent hosted-model fallback.

## 1. Create a fresh project and configure the local runtime

Run these commands from the GigAI source checkout. Replace the model and digest
with the exact model identity installed in your local Ollama. The digest must
be the lowercase 64-hex `sha256:` value reported by Ollama; it is part of the
sealed run identity.

The target, home, and workpad directories must be **persistent**, not
`mktemp` temporary directories: the watchlist and dedup history must survive
reboots so repeated Runs can detect `unchanged`/`edited` postings. Skip the
`git init`/`gigai setup` steps below if these directories already exist from a
prior session.

```bash
export TARGET_DIR="$HOME/scout-search"
export GIGAI_HOME_DIR="$HOME/.gigai-scout"
export WORKPAD_ROOT="$HOME/.gigai-scout/workpads"
mkdir -p "$TARGET_DIR" "$GIGAI_HOME_DIR" "$WORKPAD_ROOT"

if [ ! -d "$TARGET_DIR/.git" ]; then
  git init --quiet --initial-branch=main "$TARGET_DIR"
  git -C "$TARGET_DIR" config user.name "Scout operator"
  git -C "$TARGET_DIR" config user.email "scout-operator@gigai.invalid"
  printf '# Scout M1 target\n' > "$TARGET_DIR/README.md"
  git -C "$TARGET_DIR" add README.md
  git -C "$TARGET_DIR" commit --quiet -m 'Initialize Scout M1 target'
fi

ollama serve                         # keep this running in its own terminal
ollama pull muse-glimmer:latest      # the operator's evaluated default local model
export OLLAMA_MODEL="muse-glimmer:latest"   # alternative: qwen3.8:latest
curl -s http://127.0.0.1:11434/api/tags | jq --arg model "$OLLAMA_MODEL" \
  '.models[] | select(.name == $model) | {name, digest}'
export OLLAMA_DIGEST="sha256:<copy-the-64-hex-digest-from-the-response>"

if [ ! -f "$GIGAI_HOME_DIR/config.toml" ]; then
  uv run --locked gigai setup --non-interactive \
    --home "$GIGAI_HOME_DIR" \
    --workpad-root "$WORKPAD_ROOT" \
    --editor /usr/bin/true \
    --no-open-with-target \
    --endpoint "ollama_local=ollama_local:http://127.0.0.1:11434" \
    --model-target "ollama_local=ollama_local:${OLLAMA_MODEL}@${OLLAMA_DIGEST}" \
    --create-model-target ollama_local \
    --json
fi
```

The target must have an initial commit. Run sealing compares the target
observation before and after every node, so an uncommitted or moving target can
make an otherwise healthy traversal fail closed.

The current source-checkout Scout candidate path is the same helper used by the
Scout behavior tests. It materializes the bundled Scout source and proposal
without running a provider or a model. Capture the printed `proposal_id`.

```bash
uv run --locked python - "$TARGET_DIR" "$GIGAI_HOME_DIR" <<'PY'
import json
import sys
from pathlib import Path

from gigai.default_init import initialize_defaults
from gigai.scout.template import scout_candidate_inventory

target = Path(sys.argv[1])
home = Path(sys.argv[2])
result = initialize_defaults(
    home_root=home,
    requested_target=target,
    username="local-operator",
    inventory=scout_candidate_inventory(),
)
print(json.dumps({
    "gig_id": result.instances[0].gig_id,
    "proposal_id": result.instances[0].proposal_id,
    "scout_status": result.scout_status,
}, sort_keys=True))
PY

export SCOUT_PROPOSAL_ID="<proposal_id from the previous command>"
uv run --locked gigai approve "$SCOUT_PROPOSAL_ID" \
  --target "$TARGET_DIR" --home "$GIGAI_HOME_DIR" --json
```

Approval is the local graph authority step; it does not start a Run. If a
checkout provides a different approved Scout candidate installer, it must
produce the same active workpad artifacts before continuing.

## 2. Commit the resume and write the search configuration

Use a real local resume file. `reference add` copies immutable private evidence
into the authenticated workpad; it does not call a provider or start a Run.

```bash
export RESUME_FILE="/absolute/path/to/resume.md"
uv run --locked gigai reference add \
  --kind resume --file "$RESUME_FILE" \
  --target "$TARGET_DIR" --home "$GIGAI_HOME_DIR" --json
```

Create `<target>/find-jobs.json` with the roles, merged queries, source toggles,
assessment cap, and local model target you intend to seal. This is a filled
example:

```json
{
  "schema_version": "find-jobs-config:1",
  "roles": ["software engineer"],
  "merged_queries": ["software engineer"],
  "location": "Denver, CO",
  "remote": true,
  "published_after": "2026-09-15T00:00:00Z",
  "sources": {"exa": true, "ats": true, "hiringcafe": false},
  "default_assess_cap": 10,
  "default_model_target": "ollama_local"
}
```

For example:

```bash
cat > "$TARGET_DIR/find-jobs.json" <<'JSON'
{
  "schema_version": "find-jobs-config:1",
  "roles": ["software engineer"],
  "merged_queries": ["software engineer"],
  "location": "Denver, CO",
  "remote": true,
  "published_after": "2026-09-15T00:00:00Z",
  "sources": {"exa": true, "ats": true, "hiringcafe": false},
  "default_assess_cap": 10,
  "default_model_target": "ollama_local"
}
JSON
```

Export the Exa credential in the API process environment. The key is read by
the Exa client at request time and is never stored in the graph binding or in
run outputs.

```bash
export EXA_API_KEY="<your Exa API key>"
```

## 3. Start the API and UI

Use one terminal for the API and one for the UI. Leave the Ollama terminal
running as well.

### Before you click

* [ ] `ollama serve` is running (terminal from step 1).
* [ ] `EXA_API_KEY` is exported in the **API terminal's** environment (the
  API process reads it at request time; the UI and browser never see it).
* [ ] A resume was committed with `gigai reference add --kind resume ...`
  (step 2) — the UI disables **Run workflow** without one.
* [ ] `<target>/find-jobs.json` exists and matches the roles/sources you
  intend to run (step 2).
* [ ] Both the API and UI terminals below are started and left running.

```bash
# API terminal, from the GigAI source checkout
uv run --locked python -m gigai.scout.find_jobs.present_api \
  --home "$GIGAI_HOME_DIR" --target "$TARGET_DIR"
```

```bash
# UI terminal, from the GigAI source checkout
cd src/gigai/scout/ui
yarn dev
```

Open the Vite URL shown by `yarn dev`, verify that the config and pinned-resume
preview are present, redeem the direct local confirmation when prompted, and
click **Run workflow**. The POST returns `202` after a Run ID is allocated; it
does not wait for Exa, ATS, or model work to finish. The UI then polls
`GET /api/runs/{run_id}` and loads
`GET /api/runs/{run_id}/results` after the status is terminal.

## 4. Read failures and durable evidence

Start with the HTTP response, then inspect the corresponding workpad Run:

```text
<workpad>/runs/<run_id>/run-details.json
<workpad>/runs/<run_id>/operator-consent.json
<workpad>/runs/<run_id>/sealed/find-jobs-run-input.json
<workpad>/runs/<run_id>/outputs/{acquire,assess,present}.json
<workpad>/runs/<run_id>/receipts/{acquire,assess,present}.json
```

Common boundary responses are:

* `409 config_digest_mismatch`: reload `/api/config` and submit the current
  digest; the project-local config changed after the UI loaded it.
* `403 consent_required`: redeem the direct loopback UI confirmation. A Run
  cannot be started from a non-loopback peer or with a stale consent envelope.
* `422`: inspect the redacted error code, `find-jobs.json`, the selected resume,
  and the configured local model identity. Missing `EXA_API_KEY` with Exa as
  the *only* enabled source now makes the acquire node **fail** (redacted
  code `acquire_all_sources_failed`) rather than complete with an empty
  batch; if ATS is also enabled and succeeds, the Exa failure instead shows
  up as a `FailureRow` alongside the ATS rows.
* `504 run_start_timeout`: inspect the API process and its startup logs; the
  API did not receive a Run ID within its allocation window.

After allocation, a failed node is visible in its receipt and in
`run-details.json`; do not infer success from an HTTP `202`. A successful M1
Run has three complete node receipts, acquisition rows, a pinned resume, and
assessment matrix/suggestions/questions (or explicit `not_assessed` reasons).
On a second unchanged search, the visible rows remain `unchanged` and the
assessment output reports `not_assessed` with reason `unchanged`.

## Verification boundary

Verified in this checkout: the fresh Scout candidate helper, approval, committed
resume import, canonical `find-jobs.json`, the real API server on an ephemeral
loopback port, the real spawned Run child, all three real node callables, and
two offline Runs using `httpx.MockTransport` for Exa/Greenhouse and a
deterministic model adapter. The M1 behavior test also verified the pinned
resume, three receipts, assessment fields, and unchanged-row deduplication.

Not verified here: a live Exa request, a live ATS request, a live Ollama model
invocation, the visible Vite click path, an installed-wheel run, or a release
artifact. Those are operator/live or installed acceptance steps and must not be
claimed from the offline MockTransport proof.
