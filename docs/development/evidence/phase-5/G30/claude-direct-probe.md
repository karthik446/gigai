# G30 — Direct Claude CLI probe checkpoint

- Status: Evidence checkpoint; not an accepted support claim
- Captured: 2026-08-18
- Scope: operator-shell verification of Claude Code's non-interactive command
  surface

## Command surface

The operator ran Claude Code directly with its bounded print/JSON flags and a
minimal readiness prompt:

```sh
printf 'Return exactly READY. Do not use tools.' | \
  claude -p \
    --output-format json \
    --no-session-persistence \
    --permission-mode plan \
    --tools ""
```

## Observed result

The command returned exit code `0` and a structured JSON result with:

- `is_error: false`;
- `type: "result"`;
- `result: "READY"`;
- `stop_reason: "end_turn"`; and
- approximately 3.3 seconds elapsed.

The provider reported a successful authenticated Claude model response. Secret
values, session identifiers, and full usage metadata are intentionally omitted
from this record.

## Boundary and interpretation

This proves that the installed Claude CLI and its provider-owned authentication
work in the operator's normal shell. It does not by itself prove that GigAI's
restricted child environment can access the same authentication context.

Under GigAI's current explicit environment allowlist, the same readiness probe
returns `authentication_required: Not logged in · Please run /login`. That is a
distinct environment/auth-context integration gap, not evidence that Claude is
unsupported or that the executable is missing.

G30 therefore remains open. The next implementation decision is to expose the
provider-owned authentication context through an explicit, documented, and
non-secret boundary, without passing the full host environment to the child.
