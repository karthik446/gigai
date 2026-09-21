# SCOUT-07 discovery packet and bridge review (independent, review-only)

Date: 2026-09-10. Independent review of the unregistered SCOUT-07 candidate:
the pure renderer and schema at
`src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/`
(`discovery.py`, `discovery.schema.json`), the bridge
`src/gigai/scout_discovery.py`, and the packet/bridge tests.

Reviewed against
[SCOUT-07-discovery-packet-implementation.md](SCOUT-07-discovery-packet-implementation.md)
and
[SCOUT-07-discovery-bridge-implementation.md](SCOUT-07-discovery-bridge-implementation.md).

Review only — no source, schema, test, or fixture was modified; no provider
ran; nothing was committed or published. Probes were pure, in-process, and
wrote nothing outside the session scratchpad.

## Verdict

**No blocking defect. The candidate's stated properties hold as described.**

Source/claims separation, known-vs-unknown honesty, exact profile + optional
reference binding, frozen bytes, safe rendering, stable dedup identity, strict
bridge behaviour and honest no-match are all verified by direct probing, not
only by the supplied tests.

One documentation-only defect (a wrong path in the packet implementation doc's
own evidence block) and two non-blocking observations are recorded. None
require source changes.

## Registration and blast radius — verified

The candidate is genuinely unregistered, which is the property that bounds
everything else.

- `scout_source_files()` (`scout_template.py:88-119`) maps in only the `.071`
  research resources. `.074` is absent from the inventory, so it is not part of
  the Gig-owned source set and cannot be pinned by an output contract.
- `grep -rn "000000000074" src/ pyproject.toml` outside the tool directory
  returns exactly one hit: the bridge's own literal import string
  (`scout_discovery.py:108`). No capability inventory, validator table, schema
  `SHA256SUMS` entry, or catalog entry references it. (The `SHA256SUMS` and
  `validators.py` hits for `gig-discovery-manifest.schema.json` are a
  pre-existing, unrelated schema.)
- `scout_catalog_candidate()` remains explicitly non-default.

So "no persistence, no default promotion, not in the source inventory" holds.

## Renderer purity — verified

`discovery.py` imports only `collections.abc`, `dataclasses`, `datetime`,
`html`, `re`, `urllib.parse.urlsplit`, and three in-project canonical/validator
helpers. Grep for `open(`, `Path(`, `requests`, `urlopen`, `subprocess`,
`socket`, `os.`, `exec(`, `eval(`, `__import__` returns **no** hits. There is
no filesystem, network, subprocess, or dynamic-execution primitive in the
renderer at all. The "pure, no I/O" claim is literally true, not merely
by convention.

## Dedup identity — the correction is real and verified

The bridge doc claims two false-equality collisions were found and fixed.
Both are genuinely fixed. `_opportunity_id` (`discovery.py:327-336`) hashes a
canonical JSON object with an explicit `identity_kind` namespace rather than a
newline-joined string:

```
collision 1 (delimiter): employer="a\nb",id="c"  vs  employer="a",id="b\nc"  -> DISTINCT
collision 2 (ID-vs-URL): posting_id=<url>        vs  locator fallback=<url>  -> DISTINCT
employer casefold stable:      True
same inputs deterministic:     True
different posting_id distinct: True
default port normalizes:       True   (https://Ex.com:443/j == https://ex.com/j)
distinct paths distinct:       True
```

Canonical JSON preserves field boundaries structurally, so no amount of
embedded delimiter text can move bytes between `employer` and the identity
field — the root cause the doc names is addressed at the root, not patched at
the symptom. `identity_kind` separates the posting-ID and locator namespaces,
closing the second collision. `_normalized_locator` (130-136) lowercases host,
drops only default ports, and preserves path and query, so equivalent URLs
collapse while distinct ones stay distinct.

Identity uses employer plus supplied posting ID, falling back only to a
normalized safe HTTP(S) locator, never a title — confirmed at 363, and the
tests include the same-title/distinct-locator case.

## Safe rendering — verified

`_safe_markdown` (`discovery.py:409-414`) folds CR/LF/tab into visible
sentinels, HTML-escapes, escapes backslashes, then escapes every Markdown
structural delimiter in `[]()!*`#>+-`. Probed against injection attempts:

```
forged heading   -> ' ↵ \# Independently verified ↵ '
fenced block     -> '\`\`\` ↵ malicious ↵ \`\`\`'
link injection   -> '\[click\]\(https://evil.example\)'
image            -> '\!\[x\]\(https://evil/x.png\)'
html             -> '&lt;script&gt;alert\(1\)&lt;/script&gt;'
list break       -> ' ↵ \- verified: yes'
blockquote       -> ' ↵ &gt; trusted'
```

No case emitted a raw newline or live HTML. Untrusted employer/title/locator
text cannot forge document structure — notably it cannot forge an
"Independently verified" heading, which is the claim that matters most here.

## Source, claims, and known-vs-unknown — verified

The separation between *reported*, *captured*, and *independently verified* is
enforced, not merely documented (`discovery.py:295-324`):

- `status` is closed to `{reported, captured, independently_verified}`.
- A `reported` source cannot claim evidence (301-303); a `captured` source
  cannot claim verification (305-307).
- `_artifact_ref` requires supplied bytes whose digest **and** size match, so
  capture-digest spoofing refuses.
- `reported_open` availability with no dated evidence refuses
  `discovery_packet_incomplete` (354-355).

Promotion to `agent_reported_match` is strict (`discovery.py:464-477`). It
requires *every* hard constraint plus all three of `sponsorship_need`,
`employer_sponsorship`, `eligibility` to be `known` on the preference side,
the matched reason sets to equal the required sets exactly, and
`unresolved_fields` to be empty. Each reason must additionally be tied to the
selected snapshot's own `source_id` (392-393) and requires the corresponding
posting fact to be `known` (394-406). So unknown or conflicting
compensation/sponsorship/eligibility cannot be laundered into a hard match;
the honest outcome is `partial`/`no_match` with an explicit unresolved
question or exclusion. `no_match` with a non-empty shortlist refuses (481).

This is the property the doc leans on most, and it is implemented at the
strength claimed.

## Exact profile + optional refs, frozen bytes — verified

Rebuild is deterministic and the packet is tamper-evident:

```
deterministic rebuild (markdown/sidecar):  True / True
exact packet (control)                     ACCEPTED
markdown appended space                    refused discovery_packet_digest_mismatch
markdown single char changed               refused discovery_packet_digest_mismatch
sidecar outcome tampered                   refused discovery_packet_invalid
profile preference bytes mutated           refused discovery_packet_artifact_mismatch
supporting evidence bytes dropped          refused discovery_packet_incomplete
```

Typed codes are well differentiated rather than collapsed into one generic
refusal. The schema hash claimed in both docs is correct byte-for-byte:

```
12f77c45c859192835c332869ef2e991bc3601b7e8577ba53e05ecc281c78b33  discovery.schema.json
$id: urn:gigai:scout:discovery-packet:2
root additionalProperties: false
```

`$id` matches the bridge's pin, and every object node in the schema is closed
except `$defs.profile.properties.soft_priorities`, which is an intentional
open-keyed map constrained by `maxProperties: 5` and an
`additionalProperties: {"$ref": "#/$defs/fact"}` value schema. That is a
value constraint, not a closedness gap — not a finding.

## Bridge strictness — verified

`scout_discovery.py` binds supplied bytes to independently authenticated
caller context. Adversarial probing beyond the supplied tests:

```
baseline (unmodified)                  ACCEPTED
markdown byte flipped                  refused discovery_domain_invalid
markdown as str not bytes              refused discovery_domain_invalid
sidecar outcome forced to 'matches'    refused discovery_domain_invalid
extra top-level sidecar key            refused discovery_domain_invalid
supporting evidence dropped            refused discovery_domain_invalid
preference bytes dropped               refused discovery_domain_invalid
selected_inputs truncated              refused discovery_domain_input_mismatch
selected_inputs reordered              refused discovery_domain_input_mismatch
graph_selector not find-jobs           refused discovery_domain_origin_mismatch
gig_version as bool True               refused discovery_domain_origin_mismatch
value is None                          refused discovery_domain_invalid
value has NaN (non-JSON float)         refused discovery_domain_invalid
```

Every hostile variant refuses with a typed, content-free code; **no untyped
exception escaped**, which matters because a raw traceback would leak payload
content into the caller's diagnostic channel.

Specific properties confirmed:

- **Ordered optional refs are bound.** Reordering `selected_inputs` refuses,
  so the doc's claim that revalidation catches an altered optional reference
  "even though it was never used for fit inference" holds.
- **Bool is not an int.** `gig_version=True` refuses via `type(...) is not int`
  — the `bool`-subclass trap is handled.
- **Non-JSON values are rejected before traversal.** `canonical_json_bytes` is
  called first, so NaN cannot reach the schema validator or the renderer.
- **`graph_selector` is hard-pinned** to `find-jobs`.
- **The import is a literal.** `import_module` receives a fixed package string
  (108-110); no Gig path, agent callback, or editable module is consulted.
  Schema identity is re-checked against `DOMAIN_SCHEMA_ID` on load, so a
  swapped resource refuses `discovery_domain_validator_unavailable`.

**Error-code masking checked and clean.** `ScoutDiscoveryError` extends
`RuntimeError`, so the origin/input-mismatch raises inside the `try` are *not*
swallowed by `except (ValueError, TypeError, KeyError)` and keep their specific
codes — confirmed by probe (`issubclass(...) == False`). Conversely
`DiscoveryPacketError` extends `ValueError`, so renderer refusals *are* caught
and mapped to a typed bridge error. Both directions are deliberate and correct.

## Test evidence — reproduced

```
.venv/bin/pytest -q tests/test_scout07_discovery_packet.py \
                    tests/test_scout07_discovery_bridge.py -p no:randomly
38 passed in 0.35s
```

Matches the claimed "38 passed in 0.38s". These are pure tests using synthetic
supplied bytes, with no resume, live job lookup, journal, or provider call —
consistent with the docs' own scoping.

## Findings

**F1 — documentation defect (non-blocking, no source change).**
`SCOUT-07-discovery-packet-implementation.md` cites the capability path as
`cap_00000000-0000-8000-000000000074` in both its focused-evidence block (the
`py_compile` and `ruff` commands) and its prose. The real directory is
`cap_00000000-0000-4000-8000-000000000074` — the doc drops the `4000-` segment.
The commands as written would not have run against the reviewed file. The
bridge doc and the code use the correct path throughout. Worth fixing so the
recorded evidence is reproducible.

**O1 — observation.** The renderer trusts the caller to have authenticated
Plan/Run origin; the bridge re-checks exact equality but, as its own docstring
says, "does not authenticate a journal by itself." That is the correct
boundary for an unregistered candidate, but it means the security of the whole
lane still rests on trusted caller wiring that does not yet exist. Recorded so
the eventual integration is not assumed to inherit these guarantees for free.

**O2 — observation.** Optional envelope *contents* are deliberately never read,
so they cannot establish eligibility or a competence claim — verified: only
the profile's blob bytes are accepted as input bytes. The retained references
are still bound into the sidecar digest. This is a good design; noting it
explicitly because a future change that starts reading optional contents would
silently weaken the honesty property enforced at `discovery.py:394-406`.

## Explicitly not delivered, not claimed

Unchanged and open: source-inventory/compiled-contract inclusion, registered
schema, Plan-input integration, journal persistence, committed
preference-byte redemption, fixed generic domain dispatch, real public
recording, installed-workflow evidence, release or default promotion. Source
verification and fit remain external-agent declarations, not GigAI
attestations of truth, availability, eligibility, or permission to apply.

## What I did not do

No source edits, no broad suites, no wheel build, no provider call, no private
workpad access, no commits. All reviewed SCOUT-07 files are untracked
candidate work. Luna's `scout_research_inputs.py` and its tests were excluded;
neither the renderer, the bridge, nor their tests reference that module. No
claim is made that the worktree as a whole was unchanged — other owners were
concurrently active.
