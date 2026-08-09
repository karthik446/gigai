# G22 question-quality corpus report

- Status: Accepted evidence for G22 criterion 13
- Run date: 2026-08-09
- Execution: `rtk uv run pytest -q tests/test_g22_question_quality.py`
- Result: `1 passed`
- Provider access: forbidden; the deterministic G18 factory target was used
- Target effects: forbidden; the protocol was exercised in memory only

## Case results

| Case | Domain request | Result | Required question coverage |
|---|---|---|---|
| `repository-feature` | Review selected repository source | `approved` | scope, references, effect, privacy, capability |
| `resume-tailoring` | Tailor a resume to a selected job description | `approved` | scope, references, effect, privacy, capability |
| `reference-synchronization` | Compare selected references for a synchronized workpad draft | `approved` | scope, references, effect, privacy, capability |
| `tabular-finance` | Analyze local tabular data without provider transfer | `approved` | scope, references, effect, privacy, capability |

Each case also received the deterministic `operator-confirmation` question
after explicit reference selection. The test verifies typed question
construction, selected-reference input, boundary choices, operator approval,
and proposal readiness for every named S22-01 domain. It does not claim live
provider question quality, semantic scoring, or Review Loop quality; those
remain outside this offline corpus run.
