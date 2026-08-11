# G20 Fresh-Wheel Installed Replay

- Status: Accepted implementation evidence
- Recorded: 2026-08-10

G20 was verified from a freshly built wheel in a disposable virtual
environment, rather than only from the source checkout.

Commands:

```text
uv build
<fresh-venv>/bin/python tools/verify_installed_schemas.py
<fresh-venv>/bin/python tools/verify_installed_g20.py
```

Observed results:

```text
verified 25 installed GigAI schemas
verified installed GigAI G20 improve lifecycle
```

The installed replay proves that the packaged distribution contains the two
new resources, can publish a learning record against exact source and active
pointer bytes, validate the improvement manifest, stage it, open an explicit
improve interview, and advance the existing active version exactly once. The
replay uses local fixtures only; no provider, credential, or target effect is
invoked.

