# R1 integration patch request

This patch directory is intentionally a handoff, not a change to shared R4
files. Apply it only in the grouped R4 integration pass.

1. Add `scout-proposal-revision.schema.json`,
   `scout-answer-association.schema.json`, and
   `scout-proposal-discovery-job.schema.json` to the strict schema registry,
   with their exact package-byte hashes in `src/gigai/schemas/SHA256SUMS` and
   source inventory. Existing v1 schema bytes remain unchanged.
2. Add one versioned local proposal source-descriptor extension to the model
   invocation contract. Its selected entries must carry the already
   authenticated discovery/native family, identity, artifact ref, purpose and
   exact digest; it must not reinterpret a native revision as G45 `ref_...`.
   Until this extension is registered, native-only calls remain fail-closed.
3. Register `scout_proposal_cli.run_saved_proposal` under the existing
   approved proposal Run entry after R4 verifies graph/Goal authority and
   explicit local permission. No Tailor graph substitution, automatic
   scheduler, application transition or hosted fallback is implied.
4. If independent acquisition-result persistence is required, add a named
   discovery acquisition transition to `journal.py` and projection ownership
   in R4. The current adapter reads the completed packet and deliberately does
   not mislabel it as a private proposal record.
