# G23 Installed Replay

- Status: Accepted
- Wheel: freshly built `dist/gigai-0.1.3-py3-none-any.whl`
- Verifiers: `tools/verify_installed_schemas.py` and `tools/verify_installed_g23.py`

The fresh wheel was installed with dependencies disabled into a disposable
virtual environment. The installed package verified all 27 schema resources,
created and approved a Gig with an explicitly bound capability manifest,
verified the sealed pointer, resolved its proposal lineage, and completed the
two-home source/manifest reinstall replay. Result:

```text
verified 27 installed GigAI schemas
verified installed GigAI G23 portability replay
```

No source-checkout import was used by the verifiers.
