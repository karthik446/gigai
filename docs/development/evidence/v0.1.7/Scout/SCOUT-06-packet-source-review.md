# SCOUT-06 packet source review (independent, review-only)

Date: 2026-09-10. Independent source/schema review of the settled SCOUT-06
research packet, fixed bridge, role-input helpers, and inert source/compiler
additions. Review only: no source, schema, test, or fixture was modified, and
no workpad, provider, journal, or release write was performed.

Reviewed against [persistence decisions](SCOUT-06-persistence-decisions.md)
(the explicit inline `role_request` refinement, **not** the superseded native
record-kind recommendation), [bridge implementation](SCOUT-06-research-bridge-implementation.md),
and [coordinator integration](SCOUT-06-coordinator-integration.md).

## Verdict

**No blocking correctness defect found.** The packet, bridge, role-input
helpers, and source/compiler mapping implement the refined contract as
described. Two minor schema-strictness parity gaps and one document-fidelity
observation are recorded below as deferred; none is exploitable, because every
one is closed by an executable layer that runs unconditionally.

Per the dispatch, generic `external_recording` / CLI public Plan integration is
an active separate lane and is excluded from this verdict; its known unfinished
caller is not treated as a packet blocker and was not modified.

## Method

Focused source reading plus a small number of pure, disposable in-memory
probes. The prohibited suites (root combined 95 tests / 0.59s, 24 schema
transport / 203 subtests / 0.67s), the full suite, lint, wheel builds, and
providers were **not** rerun. Probes constructed values in memory only and
wrote nothing; `git status` confirms no tracked file changed.

## What was verified

### Selected refs, origin, and identity

* Schema `$id` is `urn:gigai:scout:research-packet:2`; sidecar
  `schema_version` is `scout-research-sidecar:2`; validator ID is
  `scout-role-research:2`; validator source is
  `gigai.scout_research:validate_research_domain`. All four literals agree
  between `scout_research.py`, the compiled contract, and the packet docs.
* Schema file SHA-256 is
  `1b23fbae6e85576b37876c2bad6cd77b191e75bc490a791b8b5f90b5db26d0c3`,
  matching the constant published in the bridge implementation doc exactly.
* `_trusted_origin` pins `graph_selector == "research-role"` and uses strict
  `type(x) is not int`, so `bool` versions are refused (probed: `True` for
  either version refuses `research_domain_invalid`). Origin is compared
  canonically against the caller-supplied sealed identity; a wrong
  `graph_version` or selector refuses `research_domain_origin_mismatch`.
* The bridge takes the exact selected graph ID, selector, and graph version as
  trusted parameters and never infers a Gig version, as the decision requires.

### Role-input refinement (inline `role_request`, no synthetic record)

* `_selected_input` accepts a closed `{family, role_title, role_context}` with
  `role_title` 1–300 and `role_context` null or 1–4096, and manufactures no
  `record_id`, posting, or confirmation claim. A repo-wide grep found **no**
  residual synthetic `kind: role` record/revision projection anywhere in the
  candidate renderer, bridge, or input helpers — the earlier native-kind
  recommendation is fully superseded.
* `_selected_inputs` requires **exactly one** `role_request`
  (`sum(...) != 1` refuses `research_packet_incomplete`); probed with zero and
  with two — both refuse.
* `validate_role_request` preserves the role text verbatim (no strip, no
  normalization) and uses `set(raw) != {...}` closedness, so extra authority
  fields refuse.
* `resolve_external_input` and `revalidate_external_input` both default
  `allow_role_request=False`, so v1 callers refuse the new family by default —
  matching "old callers refuse the new family by default".
* `build_research_packet` binds `research.role_title` byte-exactly to the
  sealed request title (`!=` on unstripped text), satisfying "must repeat the
  role title exactly".

### Malformed-data redaction

All refusals are typed and content-free. Probed with a distinctive secret
embedded in `role_title`, in an unknown nested field, and in a non-string
(unhashable `list`/`dict`) `family`:

* `ScoutResearchError` / `ScoutInputError` messages carry no payload text.
* The **full formatted traceback** contains no payload either; the only
  chained cause (`_canonical`) reports the offending *type name*, never a
  value `repr` (probed with a `__repr__` that embeds a secret — not leaked).
* The malformed-`family` case refuses as a typed error rather than escaping as
  an unhashable-`TypeError`.

### Source / capture relationships

Renderer and schema agree on all four status invariants, checked in both
layers:

* `reported` → capture bytes forbidden and verification forbidden;
* `captured` → capture required, verification forbidden;
* `independently_verified` → capture **and** verification required;
* verification evidence must be a **separate** artifact from the capture
  (distinct `artifact_id` *and* distinct `content_sha256`).

The schema's three `if`/`then` blocks mirror these. They omit
`required: ["status"]`, which is normally a vacuous-`if` hazard, but `status`
is in the `source` object's own `required` list, so no bypass exists.

Byte bindings are cryptographically enforced: probed swapping the capture and
verification bytes between their two IDs, and a single appended byte — both
refuse. Supporting coverage is exact in both directions: a missing ID and an
unused ID each refuse `research_domain_supporting_mismatch`.

### No executable user-source imports

* `scout_research.py` calls `import_module` exactly once, with the
  module-level constant `_RENDERER_MODULE`; no caller-controlled value reaches
  it. The schema resource is likewise loaded from a module-level constant path.
* The renderer imports only `html`, `re`, `date`, `urllib.parse.urlsplit`,
  `dataclasses`, `collections.abc`, and `gigai.canonical`. There is no
  `exec`/`eval`/`compile`, no `open`, no subprocess, and no network. `urlsplit`
  is pure parsing; locators are validated, never fetched.
* Neither module touches a workpad, journal, checkpoint, submit path, or
  provider — the only matches for those words are docstrings.

### Markdown safety

`_markdown` folds newlines/tabs to visible glyphs, HTML-escapes, doubles
backslashes **before** per-character escaping (verified ordering, so escapes
stay unambiguous and reversible), then escapes `[]()!*`#>+-`. Probed with a
heading/list/link/code/`<script>` injection: no raw newline, no raw `<script>`,
no unescaped leading `#` — supplied text cannot open a Markdown block.

### Source identity, `.073` vs `.071`, and compiler mapping

* `scout_template.py` maps the Gig-owned research copies to
  `tools/cap_...0073/research.py` and `research.schema.json`, while the
  packaged resources stay at `cap_...0071`. This is the intended separation.
* The closed CRUD capability's inventory is unchanged at exactly **3** assets
  (`gig.py`, `.071/operation.schema.json`, `.071/record_tool.py`) and contains
  neither research file — verified by rendering the prepared manifest. Total
  source assets are **16**, matching the coordinator's claim.
* The compiled `research-role` output contract is `schema_version` `2.0` with
  `fields: ["research"]` and a `domains.research` entry pinning `schema_id`,
  `schema_ref`, `validator_id`, and `validator_source_ref` separately. Both
  refs resolve to `.073` paths, and both `content_sha256` values equal the
  digests of the actual inventoried source bytes (verified by recomputation);
  the schema ref digest equals the published `1b23fbae…` constant.

Per the dispatch, source/schema binding is the generic recorder's
responsibility. Because the compiled contract already pins exact bytes and the
generic layer compares exact bytes, **no redundant `validate_research_binding`
bridge helper is requested** — adding one would be unused duplication.

## Findings

### F1 — minor / deferred — schema does not constrain `role_request` cardinality

`properties.selected_inputs` is `{type: array, minItems: 1, uniqueItems: true,
items: {$ref: input_ref}}` with no `contains`/`minContains`/`maxContains`.

Repro (schema layer only):

```python
arr = Draft202012Validator({"$defs": defs, **schema["properties"]["selected_inputs"]})
two  = [{"family":"role_request","role_title":"A","role_context":None},
        {"family":"role_request","role_title":"B","role_context":None}]
list(arr.iter_errors(two))  # [] -> schema-valid
list(arr.iter_errors([g45_reference_only]))  # [] -> zero role_requests schema-valid
```

**Not exploitable.** `_selected_inputs` refuses both zero and two
(`research_packet_incomplete`), and `validate_research_domain` has **no early
return** — it always rebuilds through the renderer — so the executable path
closes the gap on every call.

Minimal requested change (deferred, schema-only, no behavior change):
add to `selected_inputs`
`"contains": {"$ref": "#/$defs/role_request"}, "minContains": 1, "maxContains": 1`.
Note this edits schema bytes, so it must be sequenced with the coordinator-owned
hash/golden/installed-inventory refresh and the published `1b23fbae…` constant.

### F2 — minor / deferred — schema accepts whitespace-only `role_title`

`role_request.role_title` uses `minLength: 1`, which admits `"   "`, while both
executable layers require non-blank text.

Repro: for `role_title="   "`, `validate_role_request` refuses, the renderer's
`_selected_input` refuses, but the schema validates. **Not exploitable** for
the same reason as F1 (both code paths refuse).

Minimal requested change (deferred): add
`"pattern": "\\S"` to `role_request.role_title`, sequenced with the same hash
refresh as F1. Same treatment would apply to `role_context`'s string branch.

### F3 — observation / non-blocking — `role_context` is bound but not rendered

The sealed `role_context` appears in `sidecar.selected_inputs` but never in the
Markdown. Two packets differing only in `role_context` render byte-identical
documents with equal `document_sha256`:

```python
p1 = packet(context="B2B implementation work")
p2 = packet(context="HEALTHCARE COMPLIANCE ONLY")
p1.markdown == p2.markdown          # True
p1.sidecar  != p2.sidecar           # True
```

**Not a binding break and not a contract violation.** The decision requires the
renderer to "repeat the role title exactly and bind every selected input"; the
context *is* bound in the sidecar, and `_input_label` does label the
`role_request`. Substitution is still refused — validating a ctx-A packet
against ctx-B sealed inputs refuses `research_domain_input_mismatch`.

Recorded only so the owner can decide deliberately: a reader of the Markdown
alone cannot see the context that scoped the research. If rendering it is
wanted, that is a renderer change plus a golden refresh, and should be a
separate decision rather than a fix folded into this packet.

## Explicitly out of scope

* Generic `external_recording` / CLI public Plan integration (separate active
  lane); its unfinished caller is **not** reported as a packet blocker.
* Prohibited suites, full suite, lint, wheel, and provider execution — not run.
* No source, schema, test, or fixture edits were made by this review.
