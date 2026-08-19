# G30 — v0.1.6 local UAT runbook

- Status: Operator-run UAT; not part of ordinary CI
- Scope: verify configured local model adapters and the browser-first setup
  entry point before the v0.1.6 release

## Safety boundary

The live test invokes only the explicitly named local CLI targets. It does not
print model responses, credentials, session identifiers, or usage payloads.
The normal test suite never invokes a provider. Do not set `GIGAI_G30_UAT=1`
in CI.

## Readiness UAT

From the GigAI checkout, after `gigai setup` has configured the local targets:

For Claude CLI in a bounded GigAI process, create the token with
`claude setup-token` and export it only in the shell running setup/UAT. Do not
paste it into GigAI or commit it:

```sh
read -r -s 'CLAUDE_CODE_OAUTH_TOKEN?Paste Claude setup token: '
export CLAUDE_CODE_OAUTH_TOKEN
uv run gigai setup
```

Setup reports whether the variable is present; its value is never displayed or
stored. Keep the variable exported through the readiness probe and unset it
after UAT.

```sh
GIGAI_G30_UAT=1 \
GIGAI_G30_TARGETS=codex-default,claude-default \
uv run pytest -q -m g30_live tests/test_g30_live_cli.py
```

Use `GIGAI_G30_HOME=/absolute/path/to/.gigai` when the configuration is not in
the default home. To test one provider only, set one target, for example:

```sh
GIGAI_G30_UAT=1 \
GIGAI_G30_TARGETS=claude-default \
uv run pytest -q -m g30_live tests/test_g30_live_cli.py
```

An `authentication_required` result is a truthful UAT failure for that target;
it is not silently converted to unavailable or usable.

## Browser setup and create UAT

Run the setup browser and verify the five visible steps:

```sh
uv run gigai setup
```

Verify that the operator can choose the private folder, select CLI/API access,
select multiple models, assign reviewer/verifier/researcher/Gig-creator
defaults, and review the final summary before applying. Then verify the create
entry point opens the local Gig-definition flow without implementation flags:

```sh
uv run gigai create tailor-resume-for-job
```

Stop and record the result if setup requires a model flag, if a detected CLI is
shown as usable without a readiness probe, if a secret appears in the page or
config, or if create opens a fixed workflow instead of asking for the Gig
definition.
