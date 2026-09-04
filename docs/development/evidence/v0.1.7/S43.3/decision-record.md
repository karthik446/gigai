# S43.3 — Closed Agent Runtime and Tool-Broker Feasibility Decision

**Status:** Complete — Reject

**Date:** 2026-09-03

**Scope:** the configured GigAI `codex_cli` and `claude_cli` adapters only.
This is not a claim about future adapter designs, the provider APIs, or the
installed CLIs outside the invocation shape GigAI currently uses.

## Decision

Reject the proposed Codex-plus-Claude `research-standard@1` path for the
current adapters. Neither adapter is a closed participant runtime with an
authenticated, GigAI-only broker channel and complete durable lifecycle
evidence. Therefore S43.3 does not unblock G43.3, G45.2, or G46.2.

No provider model call, Exa call, credential inspection, or target mutation was
performed for this decision.

## Evidence

| Requirement | Codex CLI adapter | Claude Code adapter | Result |
|---|---|---|---|
| Shell/browser/native bypass is absent or fails closed | The installed CLI's `--sandbox` option explicitly governs *model-generated shell commands*; it does not remove that tool. A local `codex sandbox -- /bin/sh -lc 'printf shell-surface-open'` probe exited successfully and emitted its fixed marker. The current adapter also does not pass `--ignore-user-config`. | The current adapter uses `--permission-mode plan --tools ''`, but does not use `--restricted`, a strict MCP configuration, or an authenticated broker. It has no broker-only research tool to offer. | Failed |
| Ambient configuration and credentials cannot expand the surface | `run_json_process` allows `HOME`, `CODEX_HOME`, and XDG configuration/data paths; the Codex adapter inherits them and does not suppress user configuration. | The same process allowlist exposes home/XDG paths. The adapter may pass the configured OAuth token to the provider process and has no broker-specific credential isolation. | Failed |
| Authenticated, role-bound broker actions | No broker channel, request protocol, or participant authentication is implemented. | No broker channel, request protocol, or participant authentication is implemented. | Failed |
| Every model turn and broker lifecycle event is durable and ordered | The adapter parses a completed JSONL response into final text and aggregate usage only. It does not materialize turn/tool/request/response events. | The adapter parses one final JSON result only. It does not expose turn/tool/request/response events. | Failed |
| Deterministic cancellation, idempotency, and reconciliation | Process timeout/cancellation exists, but there is no broker action reservation, idempotency key, dispatch event, or post-cancellation terminal reconciliation. | The same gap applies. | Failed |

The local runtime facts observed during the review were `codex-cli 0.153.0`
and `Claude Code 2.1.258`. The Codex command-line help exposes its sandbox as a
policy for model-generated shell commands and exposes live web search as an
optional native feature. The installed Claude help advertises possible future
hardening flags such as `--restricted` and `--strict-mcp-config`, but the
current GigAI adapter does not use them and the spike did not prove an
end-to-end authenticated broker path with a deterministic fake provider.

## Why this is Reject, not Adopt or Narrow

S43.3 requires both participants to meet every closed-runtime requirement. The
current Codex adapter fails the closed-shell/configuration requirement; the
current Claude adapter fails the broker and observability requirements. No
adapter meets the whole boundary, so there is no safe single-adapter research
profile to contract from this evidence. Existing G43 `standard@1` remains the
only supported profile: it authorizes zero tool calls and is not a research
profile.

## Contract and roadmap impact

- `standard@1`, `run-plan:1`, and completed G43 behavior remain unchanged.
- G43.3 remains a proposed contract and must not be activated.
- G43.1 provider-review dogfood and G43.2 multi-graph implementation remain
  independently required before any future G43.3 activation.
- A future reconsideration needs a new feasibility gate with a new, concrete
  closed-runtime design. It must prove the five S43.3 requirements using
  deterministic fake-provider and fake-broker fixtures before it can amend or
  replace this decision; a prompt, shared API key, or unconfined CLI subprocess
  is insufficient.

## Commands and safety boundary

```text
codex --version
claude --version
codex exec --help
claude --help
codex sandbox -- /bin/sh -lc 'printf shell-surface-open'
```

The final command was a local, non-provider sandbox probe. It only emitted the
literal marker shown above; it did not read a credential, access a network, or
write a repository artifact.
