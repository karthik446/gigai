# SCOUT-08 tailoring correction final review

Date: 2026-09-10. This is an independent, bounded closeout of the pure `.075`
tailoring candidate, its sidecar schema, and focused tests. It does not claim
caller, Plan/Run, persistence, activation, provider, or whole-feature
acceptance.

## Verdict

**Bounded correction accepted.** The implementation now enforces exact UTF-8
claim-statement-to-included-draft-span equality, source-role membership and
digest/bounds integrity, conservative raw HTML/declaration/processing/comment
rejection while retaining safe HTTP(S) Markdown links and URI autolinks, and
the schema's conditional supported/excluded evidence rules match Python's
accepted and excluded cases. These checks establish byte/provenance integrity,
not semantic factuality.

## Evidence reviewed

- Frozen SCOUT-00 tailoring rules and limits, including no score/pass
  guarantee, caller-reported requirements, and external semantic factuality.
- Prior finding and correction: `SCOUT-08-tailoring-corrections-review.md` and
  `SCOUT-08-tailoring-corrections.md`.
- Source: `.075/tailoring.py:170-215` (link/HTML gate), `:294-344`
  (draft/source span binding), `:347-400` and `:500-532` (conditional claim and
  requirement handling), and `:572-617` (sidecar revalidation).
- Schema: `.075/tailoring.schema.json`, especially conditional definitions at
  lines 40-41 and the closed sidecar/assessment-limits declarations.
- Existing focused tests: all 26 tests in
  `tests/test_scout08_tailoring_packet.py`, including the correction regressions
  listed in `SCOUT-08-tailoring-corrections.md:64-71`.

## Findings and disposition

Included claims are now deterministically linked to their draft text: each
draft span is UTF-8 boundary checked and its decoded bytes must equal the
claim statement (`tailoring.py:320-342`). The source span must be an exact
member of the claim's candidate-evidence refs and must pass source-role,
bounds, and digest checks (`:313-319`, `:285-291`). Different source wording is
therefore allowed as an association, while altered draft wording refuses; this
does not pretend to prove that the claim is factually true.

The document gate rejects element tags plus declarations, processing
instructions, comments, entity-obfuscated forms, and unsafe link destinations
(`:190-215`). It removes only validated HTTP(S) URI autolinks before the raw
HTML scan, so safe Markdown reference links and URI autolinks remain accepted.
The focused regression set covers these positive and negative forms.

Python and schema conditional behavior is aligned: supported requirements need
draft refs and gap/unknown requirements have none; supported claims are
included with nonempty draft refs, while unsupported/conflicted claims are
excluded with none (`tailoring.py:500-532`; schema lines 40-41). The schema
does not and cannot express cross-field source-span membership or byte equality;
the Python validator remains the authoritative boundary for those relations.
No defect was found in this bounded correction review.

## Verification boundary

The prior recorded focused lane is accepted as provenance but was not rerun:
`rtk .venv/bin/pytest -q tests/test_scout08_tailoring_packet.py` — **26 passed
in 0.08s**; Ruff was recorded passing on the `.075` module and focused test
file. Source/schema/test inspection independently confirmed the corrected
claims. No additional scratch probe was needed because the prior correction
review's probes and the 26-test lane cover the meaningful uncovered cases.

Remaining gates are caller and Plan/Run integration, central schema/hash
inventory, package/wheel proof, activation, providers, and semantic
factuality; none are claimed here.
