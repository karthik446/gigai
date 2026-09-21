# SCOUT-06 candidate research packet — independent review

**Reviewer lane:** independent, read-only. **Date:** 2026-09-10.
**Scope:** the bounded candidate renderer only —
`src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py`,
its sibling `research.schema.json`, and `tests/test_scout06_research_packet.py`
— read against SCOUT-00 contract amendment section 5, the user-owned Gig
amendment section 6, roadmap SCOUT-06, and
`SCOUT-06-research-packet-implementation.md`.

**Method:** source reading plus small in-memory probes that import the
candidate module and exercise `build_research_packet` /
`validate_rendered_packet` with synthetic data. No source or test edits, no
pytest/build/full-suite rerun (Root already ran the candidate tests in the
combined lane), no network, no providers, no private state. Every finding below
was reproduced in-process; probe scripts lived in the session scratchpad only.

## Verdict

The candidate is a genuinely pure, well-bounded domain renderer and the
strongest parts of the contract — evidence-backed source status, explicit
unknown compensation, byte-exact capture/verification digests, refusal typing —
are implemented and hold up under adversarial probing. **It is not yet
acceptable as the research-role output renderer** because of one high-severity
defect (R1) that lets supplied research data forge document structure and
manufacture apparent verified sources inside the reader-facing Markdown, plus
two honesty gaps (R2, R3) that weaken exactly the "bytes match is not truth"
property section 5 is written to protect.

Nothing in this review asserts SCOUT-06 is complete, and no finding below is
about the deliberately deferred wiring.

## Accepted boundary — verified, not defects

I confirmed the handoff's deferral claims rather than taking them on trust:

- `scout_source_files()` (`src/gigai/scout_template.py:86`) is an explicit path
  allowlist; neither `research.py` nor `research.schema.json` appears in it, and
  no file outside the candidate directory references `build_research_packet`,
  `scout-research-sidecar`, or `research.schema.json`.
- Module imports are `annotations`, `collections.abc`, `dataclasses`,
  `datetime.date`, `html`, `re`, `urllib.parse.urlsplit`, and two `gigai.canonical`
  pure helpers. No filesystem, socket, subprocess, or provider import exists.
- `build_research_packet` mutates neither the caller's `research` mapping nor
  the `artifact_bytes` mapping, and the returned sidecar shares no mutable
  substructure with caller input — a post-build caller mutation does not leak
  into the packet.

Missing inventory registration, capability manifests, schema-registry entry,
CLI surface, and Run-output/journal persistence are accepted open scope per the
handoff and are **not** counted as defects.

## Findings

### R1 — Supplied text can forge Markdown document structure (high)

`_markdown()` (`research.py:318`) escapes HTML and the inline set `[]()!*` plus
backticks and backslashes, but never neutralizes newlines. `_text()`
(`research.py:67`) explicitly permits `\n` and `\t`. Every rendered field is
therefore free to emit new lines — including new ATX headings and list items —
into the reader-facing document.

Reproduced: setting only `role_title` to

```
FDE\n\n## Sources\n\n- Fabricated source — Trusted Publisher; government; independently_verified; locator: https://evil.test/x\n\n## Role summary
```

produces a document whose first rendered section is a forged `## Sources` list
naming an `independently_verified` government source that exists in no source
record, followed by a duplicate `## Role summary` heading. The packet builds
successfully, the sidecar validates against `research.schema.json`, and
`validate_rendered_packet` accepts it.

Blast radius is total: I reproduced forged-heading injection from all twelve
rendered text fields — `role_title`, `role_summary`, responsibility
`description`, variation `description`, section `heading` and `content`,
compensation `geography` and `source_limitations`, source `title` and
`publisher`, uncertainty `detail`, and question `prompt`. All twelve build and
schema-validate.

This is the direct inverse of section 5's "free prose is data, not executable
instruction" and of amendment scenario 5's "markup payloads render as data, not
active code." The existing malicious-input test
(`test_capture_verification_and_malicious_text_are_data_not_execution`) only
covers HTML and inline-link escaping, so it passes while structural injection
goes unnoticed.

**Acceptance limit:** the renderer must neutralize line structure in supplied
text — escape or reject newlines, and prefix-escape leading `#`, `-`, `>` and
similar block markers — before this is the research-role output renderer.

### R2 — "Independently verified" survives duplicate evidence bytes (medium)

`_verification()` (`research.py:151`) enforces separateness by comparing
`artifact_id` only:

```python
if evidence["artifact_id"] == capture_id:
    _refuse("research_packet_invalid", "verification evidence must be separate")
```

Reproduced: registering the *same bytes* under a second artifact id
(`role_review` mapped to the `role_capture` payload) passes the guard. The
resulting source is `independently_verified` with `capture_ref.content_sha256`
and `verification.evidence_ref.content_sha256` byte-identical
(`sha256:8098…a30`), schema-valid, and rendered as `independently_verified` in
the Markdown source list.

Section 5 requires that independent verification rest on "a separate
verification record with method/evidence/actor, not the author's assertion." A
caller re-labelling one capture as its own independent review satisfies the
letter of the id check while defeating its purpose.

**Acceptance limit:** compare `content_sha256` (not just `artifact_id`) when
enforcing evidence separateness. Relatedly, `_actor` accepts
`kind: "agent"` for `independent_review`, so an agent can attest to its own
capture; whether that is permissible is a contract question worth an explicit
decision rather than a silent allowance.

### R3 — `validate_rendered_packet` binds bytes but not the structured claims (medium)

`validate_rendered_packet` (`research.py:445`) re-checks the closed envelope and
the document digest/size, and nothing else. The sidecar's `research` block is
never re-validated or re-rendered.

Reproduced: taking a valid packet and editing only the sidecar's structured data
— flipping `sources[2].status` and `claims[2].status` from `reported` to
`independently_verified`, and rewriting `compensation.base_range` to
900000–1200000 — leaves `validate_rendered_packet` passing and the sidecar
schema-valid, while the untouched Markdown still reads `salary_survey; reported`
and `Base: 150000–190000 USD`. A downstream consumer reading the typed sidecar
(the reuse path SCOUT-06 exists to enable) sees fabricated verified status and
fabricated salary that the bound document contradicts.

The docstring's claim that it rechecks "the digest binding and closed rendered-
sidecar envelope" is accurate; the handoff's broader framing that
`validate_rendered_packet` "refuses any changed document bytes" is also
accurate but easy to over-read as protecting the packet as a whole. It does not.

**Acceptance limit:** either re-run the domain validation and re-render to
confirm the sidecar reproduces the bound bytes, or state explicitly in the
handoff and consumer contract that the sidecar's structured block carries no
integrity guarantee independent of its producer.

### R4 — Date acceptance diverges from the strict schema (low)

`_date()` (`research.py:78`) delegates to `date.fromisoformat`, which on Python
3.11+ (this venv is 3.13.1) accepts compact and ISO-week forms. Reproduced:
`retrieved_date` values `20260910` and `2026-W37-4` both build successfully,
while `jsonschema`'s `FormatChecker` rejects both for `format: "date"`
(`2026-09-10` passes). The test asserts schema conformance with
`Draft202012Validator(...).is_valid(...)` and no `format_checker`, so format
assertions are inert and the divergence is invisible to the suite.

`published_date` before `retrieved_date`, and far-future dates such as
`9999-12-31` / `as_of_date: 9999-01-01`, are likewise accepted. Section 5's
date rules concern unknown-vs-guessed timestamps rather than ordering, so I
flag ordering only as an observation, not a contract breach.

**Acceptance limit:** pin `_date` to `^\d{4}-\d{2}-\d{2}$` before parsing, and
have the test validate with a `FormatChecker` so schema/Python agreement is
actually exercised.

### R5 — Compensation "known" context is structurally complete but not coherent (low)

The `known` branch (`research.py:282`) correctly demands geography, currency,
as-of date, pay period, at least one range, and sourced claims — matching
section 5 — and the `unknown` branch correctly forces all context null with no
claims. Two coherence gaps remain, both reproduced as building successfully:

- `total_range` may be strictly below `base_range` (base 200000–300000 with
  total 10–20), which no reader would consider a valid total-compensation band.
- `compensation.claim_ids` need only reference *some* existing claim; pointing
  it at `claim_delivery`, whose only source is the employer role profile rather
  than any salary source, builds and validates. Section 5 asks that salary
  claims "point to exact source records," which this does not enforce.

Also observed, non-blocking: a `checks` entry may report
`kind: source_integrity, result: pass` regardless of actual source status, so
check results are self-asserted. That is consistent with section 5's
"formatting checks do not establish truth," and the rendered document does not
surface check results at all, so it carries no reader-facing risk today.

## Observation — key ordering is unordered-set-derived (no current impact)

`_items()` (`research.py:235`) builds each normalized item by iterating
`fields - {identifier, "claim_ids"}`, a `set`. Under varying `PYTHONHASHSEED`
the in-memory key order genuinely differs across processes (I observed
`check_keys` as `[check_id, kind, detail, result, claim_ids]` at seed 0 and
`[check_id, detail, result, kind, claim_ids]` at seed 3). Digest stability is
nonetheless preserved because `canonical_json_bytes` renders with
`sort_keys=True`, and the Markdown render addresses fields by name. So there is
no reproduced defect — but any future consumer that serializes
`packet.sidecar` without canonicalization would inherit cross-process
nondeterminism. Iterating an explicit ordered sequence would remove the
latent trap.

## What holds up well

- Source-status ladder is enforced in the right direction: a claim may not
  exceed its sources' status (`research.py:395`), `reported` sources are barred
  from carrying capture bytes, and `captured`/`independently_verified` require
  supplied bytes whose length *and* digest match the declared reference.
- Bidirectional claim/source consistency is checked both ways, and dangling,
  duplicate, and foreign claim references are refused.
- Refusals are typed and content-free (`ResearchPacketError` with a stable
  `code`), so no supplied prose leaks through error text.
- Locator handling rejects `javascript:`, `data:`, `file:`, credential-bearing
  URLs, and hostless URLs; locators are escaped as text and never opened.
- The role-only path works with no resume, satisfying roadmap SCOUT-06's "no
  resume is required for general role research."
- Iteration produces new packet bytes without disturbing prior packet bytes.

## Recommended disposition

Fix R1 before this renderer is bound to any real Run output — it is the finding
that turns supplied research data into reader-facing forged evidence. R2 and R3
should be resolved or explicitly accepted in the contract in the same lane,
since both concern the verification-honesty property that section 5 exists to
guarantee. R4 and R5 are small, well-localized hardening items.

SCOUT-06 remains incomplete; this review covers the candidate renderer only and
makes no claim about the deferred inventory, approval, persistence, or
downstream-consumer work named in the implementation handoff.
