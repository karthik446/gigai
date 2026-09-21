# SCOUT-R7 live corrections preparation — 2026-09-21

Status: **bounded correction preparation complete; release source remains frozen and R7 remains blocked.** This report owns the isolated patch/helper artifacts under \`research/scout-r7-live-corrections/\`; it does not claim main-fix-done, installed acceptance, model quality, publication, or a rerun.

## Boundary and provenance

- Checkout: \`/Users/kar/orca/workspaces/gigai/gigai-v0.1.7\`
- Frozen checkout HEAD observed at start: \`fda48574f8642e66c0e7d53e7303ec04f04d7bb8\`
- Existing worktree is broadly dirty from the concurrent release work; no existing dirty file was edited by this task.
- Exact candidate wheel (read-only): \`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7-py3-none-any.whl\`
- Candidate wheel SHA-256: \`ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3\`
- Real comparison workpad (read-only): \`/private/tmp/gigai-r7-live-comparison-20260921/workpads/\`
- Comparison: \`comparison_1455227f-fc9d-4893-87d4-cc86cb368422\`; Gig \`gig_a5ed1db7-715c-4329-86f4-987477806008\`

The required local instructions, RUNTIME-01, and the live-comparison report
were read. The installed CLI help was inspected without setup mutation:
\`setup --help\` documents only \`NAME=ADAPTER:CREDENTIAL[:HTTPS_BASE_URL]\` and
\`NAME=ENDPOINT:MODEL\`; the installed config parser and adapter factory already
accept an explicit \`ollama_local\` endpoint with loopback \`base_url\` and a
target \`model_digest\`. No alternate public local setup entry was found.

## Finding 1 — raw source versus canonical pack identity

Established defect in frozen \`src/gigai/runtime_comparison.py:658-684\`:
\`_authenticate_comparison\` reads the pack source at the pinned approval head,
then compares \`digest_imported_bytes(raw_source_bytes)\` with
\`pack.content_sha256\`. Publication stores \`pack.content_sha256\` as the
canonical parsed-pack digest while \`pack.source_ref.content_sha256\` is the
raw journal source digest, so a valid comparison is rejected by fresh
\`show\`/\`status\` with \`comparison pack digest is not authenticated\`.

Observed real values:

- Raw source: \`3031\` bytes,
  \`sha256:d958f818b7cc0274c11a53b0e5b8be9ee1149d6fbdf27d393b59674185483f16\`
- Canonical evaluation-pack identity:
  \`sha256:fe708376b8f2b4b1f8045f295574e8a7b02e1d7138769e77492e1b5a6c4ecc29\`

Prepared overlay: \`research/scout-r7-live-corrections/scratch/source/gigai/runtime_comparison.py\`.
It keeps the approval-head read for the exact raw source size/hash, parses and
validates that same source through \`validate_evaluation_pack\`, compares its
canonical digest to \`pack.content_sha256\` and \`authority.input_sha256\`, then
checks owner, pack ID/version, selected graph, goal, and consent scope. Run
manifest and result references continue to be checked at the publication
comparison head; the approval/publication head distinction is preserved.

The overlay does not remove a digest check and does not rewrite journal bytes.
It also rejects changed pack bytes, forged source references, foreign graph or
goal bindings, and consent with a different pack identity. Historical packs
without the new prompt field remain readable.

## Finding 2 — six invalid outputs and the proven instruction gap

The six real installed outputs are preserved under each run's result and
invocation response artifacts. The durable case inputs contain only prompts of
the form “Return JSON with exactly verdict, criteria, and unsupported_claims;
cite only supplied evidence IDs.” They do not state that \`criteria\` must be an
array, that each item must use the exact \`criterion_id\`/\`status\`/\`evidence_ids\`
fields, or which public criterion IDs/descriptors must be emitted.

Durable selected-reference evidence is present in each invocation record, and
the exact old provider-input bytes are reconstructable as the durable case
prompt plus \`[reference_id]\` and the selected \`input.json\`; there is no hidden
criterion contract in that payload. For example, old provider-input hashes
were:

| Setup | Case | selected input SHA-256 | reconstructed provider-input SHA-256 |
|---|---|---|---|
| Qwen/Ollama | \`posting_supported\` | \`sha256:94445dd2bb25c15569ea0c39c5f01161368c8f973ed65fffe21c8d705331c9\` | \`sha256:e5ba294fbd705de6dd03fc2809148186aa26054c687a98fa6e1badc943b687be\` |
| Qwen/Ollama | \`unsupported_duration\` | \`sha256:ff508609a37059f3d4425d662bb9d6142b17870de2705a089746d2149843b3de\` | \`sha256:a62d1031f5175d3712d6f19e8dc1caf312886ddb21f5531682ca63493b8d5be8\` |
| Qwen/Ollama | \`finalized_not_applied\` | \`sha256:8625a3bbf38defcff010702d2a4bb2d163ae0518cf2c8c1ad81f7da31343f7a9\` | \`sha256:206a7148f41f95cd96b448c8b34a0d4f0c1b164de14d6f16cfbcddabb96f3211\` |
| Luna/Codex | \`posting_supported\` | same as Qwen | \`sha256:303793d666fb99607835bb6afe245208e9d60c9b7a99b1303dc83b32634babba\` |
| Luna/Codex | \`unsupported_duration\` | same as Qwen | \`sha256:30e00b3df354b52804ebe5547cfc2057837c9f4569ad53a22bef3e775e366d9b\` |
| Luna/Codex | \`finalized_not_applied\` | same as Qwen | \`sha256:11b6e3e5d41d067afb8c2767957d8341da2d4f7eda882dc7223f32ae837086f5\` |

The output grader's strict shape is established at frozen
\`runtime_comparison.py:235-252\`, and every real case failed there as
\`invalid_output\`. This is an instruction-contract gap, not evidence that
either model is weak; no output was repaired and no model was asked to grade
itself.

Prepared overlay files:

- \`scratch/source/gigai/schemas/runtime-evaluation-pack.schema.json\` adds an
  optional, versioned public \`output_contract\` containing only criterion IDs
  and descriptions; old packs remain schema-valid.
- \`scratch/source/gigai/data/runtime-comparison-pack-v1.json\` supplies the
  three synthetic descriptors and updates the declared output-contract
  version. It does not expose expected statuses, allowed gold evidence, or
  known-good/known-bad vectors in the prompt.
- \`scratch/source/gigai/runtime_comparison.py\` appends the same frozen shape
  contract to both setup prompts, records the composed prompt in each case
  input/reference, and changes new setup metadata to
  \`r7-output-contract:1\`. A pack without the optional field retains its old
  prompt for historical reads.

The contract says exactly: top-level \`verdict\`, \`criteria\`,
\`unsupported_claims\`; exact verdict values; one criterion object per public
criterion; only \`criterion_id\`, \`status\`, \`evidence_ids\` in each criterion;
allowed status values; evidence IDs from supplied evidence; and a
non-empty-string unsupported-claims array. The deterministic grader remains
unchanged and still rejects malformed output.

## Finding 3 — installed setup parser local gap

Established installed parser gap in frozen \`src/gigai/cli.py:3904-3922\`:
\`_parse_endpoint_spec\` accepts only \`openai_api\` and \`openrouter_api\`, while
the already-supported config/factory path accepts \`ollama_local\`. The
installed live comparison therefore used a schema-valid disposable config,
not public setup syntax. This is not a loopback/readiness defect: config
validation, adapter factory, explicit model digest, and readiness semantics
already enforce those boundaries.

Prepared overlay: \`scratch/source/gigai/cli.py\` adds only explicit forms:

\`\`\`text
--endpoint local=ollama_local:http://127.0.0.1:11434
--model-target qwen=local:qwen3.8:latest@sha256:<64 lowercase hex digits>
\`\`\`

It leaves remote endpoint parsing/checks unchanged, rejects non-loopback local
URLs at the parser boundary, carries the digest into the existing \`ModelTarget\`
field, and refuses a local target without an explicit digest before setup can
write config. It does not infer a model, probe/download/start a daemon, create
global config, or weaken remote endpoint checks. The browser setup surface is
not broadened in this preparation; that is a separate UX decision if desired.

## Scratch evidence

Commands run, all bounded and offline:

\`\`\`text
rtk env PYTHONPATH=research/scout-r7-live-corrections/scratch/source .venv/bin/pytest -q research/scout-r7-live-corrections/scratch/tests/test_live_corrections.py
# 8 passed in 0.01s

rtk python -m py_compile research/scout-r7-live-corrections/scratch/source/scout_r7_corrections.py research/scout-r7-live-corrections/scratch/source/gigai/runtime_comparison.py research/scout-r7-live-corrections/scratch/source/gigai/cli.py
# AST/compile OK

rtk jq empty research/scout-r7-live-corrections/scratch/source/gigai/data/runtime-comparison-pack-v1.json
rtk jq empty research/scout-r7-live-corrections/scratch/source/gigai/schemas/runtime-evaluation-pack.schema.json
# JSON OK
\`\`\`

The eighth test is a read-only replay of the existing real failed comparison
through the scratch dual-identity reader: it passes while confirming raw and
canonical digests differ. No provider, \`gigai comparison show/status\`, replay
attempt, workpad mutation, or network call was made by this task.

## Exact source hashes and handoff

Frozen inputs observed before scratch edits:

| Path | SHA-256 |
|---|---|
| \`src/gigai/runtime_comparison.py\` | \`645ea6de662d003858ca3c643d64933bbdcf54ffa3ace63bb88bba9d3321e991\` |
| \`src/gigai/cli.py\` | \`66c75b904e8c77b7d58bd8caafdd4fc547e98d8a5184699c85d3a9abc6995988\` |
| \`src/gigai/schemas/runtime-evaluation-pack.schema.json\` | \`0fa9ca73a4d514de1b7b848f06bcf51a309c8383c5f43b2711dcf30b35e08ef5\` |
| \`src/gigai/data/runtime-comparison-pack-v1.json\` | \`154f528c87a44b910cf31bae27a65797f5f50e70f733c8e0fc9186768d894ced\` |

Scratch overlays:

| Path | SHA-256 |
|---|---|
| \`scratch/source/gigai/runtime_comparison.py\` | \`91873a67b07123eef1323db376d75f084d4623715b411c3e039f375098ba624c\` |
| \`scratch/source/gigai/cli.py\` | \`2de97fd13d83539b02af30e585bfd88b5fb0f273326a3fe0da21197e78374395\` |
| \`scratch/source/gigai/schemas/runtime-evaluation-pack.schema.json\` | \`b4fa1f6cc80f4a67ae579b734f174a4d486734044787636733b5c185b776b7eb\` |
| \`scratch/source/gigai/data/runtime-comparison-pack-v1.json\` | \`3624398d5aeaff93f640c60aef88aa1ece26a0d8319e47f916dfce6429a304bd\` |
| \`scratch/source/scout_r7_corrections.py\` | \`a3e9975a14f0c17fc22b1d4a2d13bbf124ab5cf19a7b088680ece6208ebbc9ff\` |

Safe next action: after the release worker reports the frozen matrix complete,
review the narrow overlays and apply them in the owning source/schema/data
files, update schema/resource inventories and the approved Gig-owned pack
contract/reference through the normal authority path, then rebuild and run
focused installed checks. A fresh authorized live comparison is optional
follow-up evidence; this preparation does not authorize or perform it.

## Explicit non-claims and blockers

- No main source, test, tool, pyproject, schema, wheel, shared venv, global
  config, \`.gigai\`, or comparison workpad was changed.
- No full suite was rerun and no release acceptance is claimed.
- No provider/model call, download, readiness probe, or live replay was made.
- Applying the pack contract requires updating its approved journaled source,
  canonical digest, graph output-contract version, and resource/schema hashes;
  do not patch the historical workpad or rewrite its raw bytes.
- The setup grammar overlay is intentionally a post-freeze candidate and needs
  owner review for CLI UX and installed roundtrip coverage before activation.
