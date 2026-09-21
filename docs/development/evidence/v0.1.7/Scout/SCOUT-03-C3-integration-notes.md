# SCOUT-03 C3 — Integration notes

**Status:** Coordinator preparation, not implementation or acceptance evidence.
Read alongside the [bounded correction plan](SCOUT-03-correction-plan.md).
Prepared while C1 owns the storage implementation; these observations concern
untouched capability, scheduler, package and schema surfaces only. Recheck them
against the settled C1/C2 handoffs before implementation.

## Existing boundaries to preserve

- `src/gigai/capabilities.py:install_local_capability` installs a pinned local
  artifact **without executing it**. Its manifest inspection and installation
  receipt are not an existing generic Python execution engine or authorization
  for arbitrary record mutations.
- `src/gigai/adapters/capabilities.py` concerns model capabilities, not Gig-owned
  domain-tool dispatch.
- `src/gigai/run.py:_validate_scheduler_policy` restricts deterministic G14 Goals
  to `gigai.offline`/`gigai.deterministic` and `write_workpad`. Do not loosen that
  scheduler into an arbitrary Python executor as a shortcut for C3.
- The accepted workspace amendment's reference to an "existing capability
  executor" is therefore an integration obligation, not proof that such a
  general executor already exists. C3 needs a narrow, tested supported dispatch
  boundary and publication-time validation. SCOUT-04 remains the external
  agent execution-recording goal.
- `active-gig-version-v2.schema.json` already permits a pinned
  `capability_manifest` artifact reference. Determine the complete approved
  closure through the settled lifecycle implementation before choosing how
  the software inventory is attached. An ignored working inventory, an
  independently computed digest, or an installation receipt alone cannot stand
  in for approved Gig-version authority.

## Required tool-path acceptance story

Use a disposable approved Gig fixture with a tiny local domain tool. Exercise
the real supported entry point, not a helper fed a caller-invented digest:

1. Prepare inventoried source snapshots and register the narrowly declared
   capability/effects through the supported proposal/approval path.
2. Dispatch the selected canonical entry with the approved Gig/version and
   explicit operation/agent origin. Resolve its actual source inventory.
3. Let the tool construct a typed operation for the shared in-process service.
   The service checks the same binding again under publication authority and
   records it with the operation receipt. Domain code never owns journal or SQL
   publication.
4. Change the tool bytes between dispatch and publication: refuse without a
   record commit. Also exercise extra executable members, changed schema code,
   foreign Gig/version, unregistered effects and caller-supplied false digests.
5. Show that direct built-in operations remain distinct from a tool invocation;
   neither imported code nor an external agent envelope manufactures direct
   user consent. Same-account arbitrary Python is not sandboxed by this service.

This does not ship default tools or initialize users' Gigs; SCOUT-05 owns that
scaffolding. The fixture must nevertheless prove the actual service seam.

## Clean package/export guard

`src/gigai/package.py:inspect_package` currently verifies the exact file
inventory, refuses symlinks/executable permission bits and top-level
`hooks`/`install`/`scripts`, but does not classify Scout private record families.
`export_package` relies on that inspection. Manifest completeness alone does
not make a private record safe to export: a manifest can faithfully inventory
private content.

C3 must explicitly reject recognizable private imports, record revisions,
operation receipts, personalized context and rendered reports through package
inspection/export. Prove refusal even with matching manifest hashes. Preserve
legitimate inert template documentation and frontend source; a blanket ban on
all `docs/` or Python source is not the accepted design. No automatic source
execution or private transfer is introduced. Arbitrary renamed/transformed
private prose cannot be universally identified; state the supported provenance
boundary honestly.

## Work deliberately left until C1/C2 settle

Inspect the current Run Plan producer, typed review ingress, Run allocation,
selection resolver and package callers then. Map each supported ingress to a
concrete regression. The selected revision must survive content deduplication,
saved-default changes and archive; a sealed Plan cannot be rewritten to follow
the newest record. Do not amend the older invocation protocol's forbidden-key
boundary to smuggle private references into a provider request.

The final C3 handoff must identify the supported command/library paths and
tests, not merely list new helper functions or schema files.

## Managed Plan input seams inspected during the second C1 pass

The following files are outside that C1 writer's scope and were read without
inspecting its in-flight storage changes:

| Boundary | Current behavior | C3 work |
| --- | --- | --- |
| `run_plan.py:create_run_plan` | Checks only generic `input_paths` for workpad private-root prefixes, then copies ordinary input bytes | Add explicit selected-record/G45 input resolution and seal exact record/snapshot refs; preserve generic-input privacy refusals |
| `run_plan.py:_typed_review_inputs` | Reads `review_subject` and the approved baseline snapshot through a separate path | Apply the same known-private-provenance rule; baseline approval is not private disclosure consent |
| `run_plan.py:_verify_plan_sources` and `_validate_g431_typed_inputs` | Validate sealed bytes and existing role contracts on read | Revalidate selected private revision/content bindings with the shared resolver without following current defaults |
| `run.py:_validate_run_plan_binding` | Checks sealed source bytes and record/snapshot membership before Run allocation | Add selected-record scope/chain/content revalidation and explicit private managed-execution refusal before allocation |
| `provider_review.py:execute_provider_review` and `_sealed_text_inputs` | Resolve sealed text snapshots and construct provider references | Reject known private inputs even through a direct library/alternate ingress; do not rely only on the producer's path check |

Both existing Plan schemas already give each input a closed
`{input_id, role, record_ref, snapshot_ref}` envelope with strict artifact
references. Start with those seams: `record_ref` can pin an exact revision or
G45 record and `snapshot_ref` its canonical immutable content. A successful
storage-only seal must keep explicit revision associations even when multiple
inputs share a content blob; deduplicate `sealed_sources` separately. Do not
add broad optional JSON or loosen old nested schemas as a shortcut.

The [implementation plan's caller-audit disposition](SCOUT-03-implementation-plan.md)
explicitly permits existing managed Plans to pin private identities for
storage/binding proof **only with fail-closed provider ingress**. This is not
successful managed execution or an external recording Plan. SCOUT-04 still
owns its separate Plan family and execution lifecycle. C3 must demonstrate
both successful exact-input sealing and refusal to allocate/execute a managed
private-input Run, rather than treating a helper-only resolver as integration.

Also test known private bytes copied unchanged into a generic review input or
typed subject/baseline. Path-only checks lose that provenance. Unknown arbitrary
transformed external prose remains outside any universal detection claim.
