# S16-EVAL calibration method

The accepted S16-EVAL decision record fixes the bar before G18 selects a
production judge. G18 may select a judge and report pass/fail; it may not alter
these thresholds.

## Metrics

Metrics are computed per case first and then aggregated by severity and corpus
split. Findings match on criterion, severity, and evidence-support label;
prose wording is not identity.

- Expected-finding recall: matched expected findings divided by all expected
  findings. An allowed `unanswerable` result counts only where the label allows
  it.
- Precision: matched emitted findings divided by emitted findings. Forbidden
  findings count as false positives even when a seeded defect was also found.
- False-positive rate: false-positive emitted findings divided by all emitted
  findings, with an explicit zero-emission rule for no-finding cases.
- Citation-support correctness: cited bytes support the specific claim, not
  merely exist or have a valid digest.
- Severity correctness: exact tier or an accepted adjacent-tier result,
  reported separately for high-severity findings.
- Confidence calibration: expected calibration error over labeled confidence
  bands; confidence is not treated as proof of correctness.
- Abstention sensitivity: insufficient-evidence cases correctly abstained.
- Abstention specificity: sufficient-evidence cases correctly not abstained.

## Normative acceptance vector

The final held-out result passes only when all entries pass:

| Metric | Minimum/maximum |
|---|---:|
| Expected-finding recall | >= 0.90 |
| Precision | >= 0.90 |
| False-positive rate | <= 0.10 |
| Citation-support correctness | >= 0.95 |
| Severity within one tier | >= 0.90 |
| Confidence expected calibration error | <= 0.10 |
| Abstention sensitivity | >= 0.90 |
| Abstention specificity | >= 0.90 |
| Critical forbidden findings | 0 |

In addition to the corpus-level vector, no individual Final Held-Out
Acceptance case may contain a fabricated citation, unsupported high-severity
finding, or an unjustified non-abstention on an insufficient-evidence case.

## Calibration procedure

1. Freeze the Development, Calibration, and Final Held-Out case identities and
   label versions.
2. Use Development to validate the harness and metric implementation.
3. Use Calibration to tune only judge thresholds and output-to-label mappings.
   The numeric acceptance vector above remains unchanged.
4. Freeze the judge configuration, prompt/rubric version, threshold values,
   and mapping identity.
5. Run Final Held-Out Acceptance exactly once for the reported result. No final
   case, label, or result is used to tune the judge.
6. Record judge/provider/model identity, evaluator version, case and label
   digests, metrics, per-case evidence, and any deterministic-check conflict.

When a deterministic check and judge disagree, the deterministic check wins for
schema, digest, citation-existence, and other mechanically provable facts. The
disagreement remains recorded and is escalated to human adjudication for
reference-grounded or high-severity claims.
