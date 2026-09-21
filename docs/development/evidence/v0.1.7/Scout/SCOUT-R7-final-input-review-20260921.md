# SCOUT R7 final input and subprocess review — 2026-09-21

Status: **bounded independent recheck accepted the R7-CR-01 and batch-shell
corrections; no new defect found in this owned scope. R7/release is not
accepted.**

This read-only recheck covered the release execution graph, the R7 candidate,
core, media, verifier, corrections, and subprocess reports, plus the current
CLI, Run Plan, provider-review, and subprocess source. It did not edit product
source, tests, schemas, version metadata, or CI; it did not run the 53-test
suite, call a provider/model, access private user data, activate, publish,
commit, reset, or touch the checkout `.gigai` state.

## R7-CR-01 and ordinary input result

Accepted as corrected in the current source:

- The public `run-plan create` contract still exposes `code_review` and
  `code`, `structured_data`, and `mixed` (`src/gigai/cli.py:2799-2804`), and
  `create_run_plan` routes ordinary inputs through `_ordinary_artifact`
  (`src/gigai/run_plan.py:1693-1723`).
- `_ordinary_artifact` requires an explicit regular non-symlink leaf, reads
  exact bytes, maps `.md`/`.markdown`/`.txt` through the deterministic media
  table, and uses stable `application/octet-stream` for all other suffixes
  (`src/gigai/run_plan.py:189-237`). It does not decode or classify opaque
  bytes from suffix/content or consult the host MIME database.
- The text-only contract remains separate: `_text_artifact` rejects
  non-text suffixes and invalid UTF-8 (`src/gigai/run_plan.py:199-215`), and
  provider review rejects non-text media and invalid UTF-8 before the first
  invocation (`src/gigai/provider_review.py:551-579`, called before
  `run_model_invocation` at `:159-188`). This prevents generic opaque input
  acceptance from being misrepresented as provider-valid text.

The public synthetic probes exercised the actual Click `run-plan` commands in
fresh temporary workpads. Raw output and exit provenance are retained at:

```text
/private/tmp/gigai-r7-final-input-review-20260921/public-and-subprocess.log
sha256=c325397dc00a01e1a3b8a06a3c740945f2dbf832cb287a1d50ccfb9c99179b15
exit=0
```

Observed results:

```text
historical_plan_show=PASS sealed_sources_unchanged
ordinary_code=PASS inputs=1 opaque_media=application/octet-stream
ordinary_structured_data=PASS inputs=1 opaque_media=application/octet-stream
ordinary_mixed=PASS inputs=2 opaque_media=application/octet-stream
ordinary_leaf_symlink=PASS run_plan_input_mismatch
baseline_nontext=PASS requirements_baseline_invalid
review_subject_nontext=PASS review_input_roles_invalid
```

The first public Plan was then read through `run-plan show`; its content digest
and `sealed_sources` were identical, so historical sealed references were not
reclassified or rewritten. Existing read-time safeguards remain in place:
relative/absolute/path traversal checks, every workpad path component checked
for symlinks, regular-file checks, exact digest and exact byte-size checks
(`src/gigai/run_plan.py:681-742`). The ordinary input probe also refused a
leaf symlink before sealing.

## Fixed-argv subprocess recheck

The uniquely named helper `tools/scout_r7_final_input_review_20260921.py`
reproduced the repository diagnostic's AST contract over all `src/gigai/*.py`
`subprocess.run` calls. It found 28 calls, zero missing literal
`shell=False` sites, and zero `shell=True` sites:

```text
subprocess_run_call_count=28
subprocess_shell_false_violations=[]
subprocess_shell_true_sites=[]
overall=PASS
```

The three `application_events.py` additions preserve the exact `git log` and
`git show` argv, capture/text/check settings, journal path and sequence
authority (`src/gigai/application_events.py:86-118`). The capability-review
addition likewise preserves the committed `git show HEAD:<path>` argv, working
versus committed-byte comparison, and authority refusal behavior
(`src/gigai/capability_review.py:195-209`). Neither file adds `env`, `cwd`,
permissions, timeout, or other execution authority changes.

## Static verification and provenance

Only bounded static checks were run after the public probes; no broad test
suite was rerun:

```text
ruff check src/gigai/run_plan.py src/gigai/application_events.py \
  src/gigai/capability_review.py \
  tools/scout_r7_final_input_review_20260921.py
=> All checks passed! (exit 0)

python -m py_compile src/gigai/run_plan.py src/gigai/application_events.py \
  src/gigai/capability_review.py \
  tools/scout_r7_final_input_review_20260921.py
=> passed (exit 0)
```

Raw logs:

```text
/private/tmp/gigai-r7-final-input-review-20260921/ruff.log
sha256=b55a15a18a1ea234f7b205b754b1ce8f698d25d732bb90da1cdb1a19c52c634a
/private/tmp/gigai-r7-final-input-review-20260921/pycompile.log
sha256=19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061
```

Checkout HEAD at review time was `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`.
Current file identities used for this review:

```text
73dfacb8d8a8f26c935ebaa3fc48b90704d0511c655fd166e1f36a593c55e7af  src/gigai/run_plan.py
66c75b904e8c77b7d58bd8caafdd4fc547e98d8a5184699c85d3a9abc6995988  src/gigai/cli.py
3c8ab59c1ed235f0a4d7fb3979d04e0c79ecd5c5c7422c6be2f7c7c661bb5a9b  src/gigai/application_events.py
d291bdf305ceab54cb38b296b8a27261e0ffda37e3fa7daff93323f47717df33  src/gigai/capability_review.py
b2c6bcdadc6264356522dacd1de78d1cbb37d668a2231118093e63be848b8fdb  src/gigai/provider_review.py
436bb16f82cb1e537d4476b8c43d3eb8161dc353cb26b95536f18f43dcf418e4  tools/scout_r7_final_input_review_20260921.py
```

## Release boundary

This evidence accepts the bounded ordinary-input and fixed-argv source slice
only. It is not rebuilt-wheel proof and does not change the earlier candidate
truth: the required release matrix, installed-wheel/current-source
reconciliation, live provider/model comparisons, Linux/Windows, Docker,
publication authorization, and personal UAT remain pending or separately
non-green. No new product-wide review cycle is warranted from this recheck.
