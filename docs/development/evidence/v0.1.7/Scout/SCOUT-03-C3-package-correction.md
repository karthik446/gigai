# SCOUT-03 C3 package correction

## Finding corrected

F1 from `SCOUT-03-C3-package-review.md` is corrected by adding `manifests/` to
the canonical private provenance roots in `src/gigai/package_privacy.py`. This
keeps the existing narrow provenance boundary: it does not add a blanket
`docs/` or Python ban, and it preserves the explicitly bounded claim that
unrecognized transformed or renamed private prose/HTML without a canonical
path or valid persisted contract is outside automatic detection.

## Regression coverage

`tests/test_scout03_package_privacy.py` now covers all seven authoritative
manifest paths named by F1:

- `manifests/proposal-interview.json`
- `manifests/gig-proposal.json`
- `manifests/gig-builder-session.json`
- `manifests/active-gig-version.json`
- `manifests/proposal-draft-manifest.json`
- `manifests/improvement-manifest.json`
- `manifests/gig-discovery-manifest.json`

Each case uses a valid proposal-interview payload and a correctly recomputed
package inventory/content digest. It exercises actual `inspect_package`,
`export_package`, and `install_package`; each refuses with
`private_provenance_refused`, export leaves no destination, and install leaves
no `.gigai/packages/<package-id>` destination. The existing positive inert
Scout template export roundtrip remains covered.

## Focused verification

Command:

```text
.venv/bin/pytest -q tests/test_scout03_package_privacy.py
```

Result: **20 passed in 3.09s**.

Additional scoped check: `ruff check src/gigai/package_privacy.py
tests/test_scout03_package_privacy.py` — **All checks passed!**

No full suite or wheel rebuild was run. No changes were made to `package.py` or
central schema inventories; no providers, user `.gigai` state, commits, or
release actions were used.
