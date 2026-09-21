# SCOUT-06 completed-research input integration review

Date: 2026-09-10. This is an independent, bounded review of completed
research input integration. No production files, source assets, registry
entries, or schema resources were changed.

## Accepted subset

The v2-only closed selector and normalized resolved-input shape are strict and
preserve the earlier guarantees. `scout_inputs.py` resolves the exact
`run_id`/`receipt_id`/`output_kind` tuple from committed journal artifacts,
hydrates the complete Run history under the caller's writer, rejects recursive
input ancestry, and revalidates exact normalized equality. `external_recording`
calls resolution and revalidation from the same writer at Plan, Start,
Checkpoint, and Submit boundaries. The v1 path remains closed to the additive
research family.

The existing integration coverage also demonstrates that a completed
historical Run remains selectable after a newer Run is created, and that
native/G45 input families retain their existing pathways by source inspection
and the previously recorded focused evidence. This review does not rerun the
prior 26 role/replay tests or the earlier 2-test/33.78-second integration
checkpoint.

The candidate's approved output contract maps the research domain to the
inventoried `.073` resources:

    tools/cap_00000000-0000-4000-8000-000000000073/research.schema.json
    tools/cap_00000000-0000-4000-8000-000000000073/research.py

The probe authenticated those exact committed refs and their digests through
the external recording authority helper. The fixed packaged broker remains
closed and points at the reviewed packaged bridge/resource identity; it does
not trust arbitrary Gig code.

## Executed second-Run probe and blocker

`test_second_run_reuses_completed_research_through_checkpoint_and_submit`
creates a real disposable initialized workpad, completes the first research
Run, creates and starts a second v2 Run selecting the first Run's exact
research selector, and verifies the normalized historical output refs are
preserved. It then asks the inventoried research renderer to build second-Run
output using the actual second-Run identity and selected inputs, which is the
required producer step before the public `checkpoint_v2`/`submit_v2` broker
boundary.

The probe reproducibly stops at that producer step with:

    ResearchPacketError: selected input family is invalid
    callsite: research.py:_selected_input

The renderer accepts `role_request`, G45, and native `scout_record` families,
but rejects the normalized `family: scout_research` item emitted by
`scout_inputs.resolve_external_input`. Consequently the real checkpoint and
submit validator/broker cannot consume historical research bytes yet. This is
an integration blocker, not a production fix opportunity in this review. The
test records it as a bounded `pytest.xfail` only after asserting the exact
error and unchanged journal HEAD; if the renderer later accepts the family,
the same test continues through real `checkpoint_v2` and `submit_v2` assertions.
No source asset or registry was modified to force acceptance.

## Negative-path verification

The missing historical receipt refusal remains `external_record_not_found`;
the cancelled source refusal also remains the existing generic
`external_record_not_found` surface. The frozen contract requires refusal and
does not require inventing a new domain-specific code for these unavailable
records. New assertions confirm both refusal paths leave the journal HEAD
unchanged and publish no new evidence.

## Verification and scope

Bounded command:

    .venv/bin/pytest -q tests/test_scout06_research_input_integration.py \
      -k 'second_run or historical_refusal' --disable-warnings --maxfail=1

Result: **1 passed, 1 xfailed, 1 deselected** in 29.34s. The xfail is the
reproducible renderer seam described above, not acceptance of checkpoint or
submit. Ruff was run on the owned Python test file and passed. No full suite,
wheel, provider, network, private-root, activation, or commit operation was
performed.

Accepted here: strict v2 selector/resolver behavior, exact historical refs,
same-writer hydration/revalidation source pathway, and negative refusal
nonpublication. Not accepted: a completed second research Run through actual
checkpoint/output/check/submit, because the inventoried renderer cannot yet
consume the normalized historical research input. This is not a whole-feature
acceptance claim.
