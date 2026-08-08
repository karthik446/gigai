# G17 option and installation state table

## Inspection states

| Precedence | State | Deterministic condition |
| ---: | --- | --- |
| 1 | `security_rejected` | Installer permission/path or security review violates the v1 allowlist |
| 2 | `incompatible` | Requested compatibility or pinned identity cannot be satisfied |
| 3 | `credential_missing` | Required credential metadata is present but no value is inspected |
| 4 | `available` | Existing isolated bytes match the pinned source |
| 5 | `installable` | A matching pinned local artifact is present and the isolated root is absent |
| 6 | `missing` | No permitted local artifact or installed bytes are available |

## Installation outcomes

The only legal transitions are `pending` → one terminal state. Terminal
records are immutable and each attempt receives its own `installation_id`.

| Outcome | Meaning | Tool-root write |
| --- | --- | --- |
| `installed` | New pinned bytes were exposed atomically | Yes |
| `already_available` | Exact bytes already exist; no write was needed | No |
| `refused` | Operator or policy declined before installation | No |
| `failed` | Pre-write failure left the root unchanged | No |
| `rolled_back` | A mutation was attempted and the before snapshot was restored | Attempted, then removed |
