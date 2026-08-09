# G22 installed-wheel replay

- Status: Accepted evidence for G22 criteria 2 and 14
- Run date: 2026-08-09
- Artifact: freshly built `gigai-0.1.3-py3-none-any.whl` installed into a
  disposable Python 3.13 environment
- Network/provider access: none; `offline-default` supplied the deterministic
  question response

## Results

The installed `gigai` console script was run against a fresh non-Git target and
fresh home/workpad roots. It launched a `127.0.0.1` token URL, accepted the
request/reference/boundary answers with revision and sequence guards, and
reached `approved`. The terminal result contained a session ID and proposal ID.
The workpad contained no `runs` directory.

The checked-in verifier `tools/verify_installed_g22.py` drives the same flow
through the installed console script. It reports:

```text
verified installed GigAI G22 create interview
```

The same installed environment also ran:

```text
/private/tmp/gigai-g22-installed/bin/python tools/verify_installed_schemas.py
verified 22 installed GigAI schemas
```

The disposable environment and temporary roots are not repository evidence;
only this sanitized result is retained.
