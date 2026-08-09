# S16-EVAL ground truth and adjudication protocol

## Label unit

Each case is labeled against the exact source bytes, Review Contract, and
expected evidence properties. A label contains:

- expected findings, including criterion, evidence support, severity,
  confidence band, and whether the finding must be emitted or may be marked
  `unanswerable`;
- acceptable alternative findings, with the evidence and rationale required to
  count them as equivalent;
- forbidden findings, including invented claims, unsupported citations, and
  unjustified high-confidence output;
- the expected abstention decision when evidence is insufficient;
- the category labels and behavior rows covered by the case.

Seeded-defect presence is not itself ground truth. A case is passing only when
the emitted finding set, evidence support, severity/confidence, and abstention
behavior agree with its labels.

## Independent labeling

Two independent reviewers label every Calibration and Final Held-Out Acceptance
case from the committed source bytes and contract. They record evidence spans,
not just a verdict. Development labels may be iterated while the taxonomy and
harness are built, but every revision is versioned.

The reviewers do not see the other reviewer's labels while labeling. A label
revision records the case ID, label version, changed field, source/contract
digests, and rationale. Changing a Calibration or Final label after judge
tuning invalidates the affected calibration or final result and requires a new
label version.

## Disagreement and adjudication

Any disagreement about expected findings, evidence support, severity,
confidence, or abstention is preserved as two independent labels. A named
adjudicator reviews both labels and the underlying bytes, records the decision
and rationale, and produces the accepted ground-truth label version. The
adjudicator may choose one label, define an allowed alternative, or mark the
case unresolved and therefore ineligible to certify a numeric bar.

The adjudicator does not silently delete the independent labels. The final
quality report retains label IDs, adjudicator ID, rationale, and the accepted
ground-truth version.

## Contamination rules

- Development cases may tune taxonomy, prompts, harness code, and assertion
  behavior.
- Calibration cases may tune judge thresholds and output-to-label mappings,
  but may not alter the normative acceptance bar.
- Final Held-Out Acceptance cases are immutable during tuning and are the only
  cases used to report that the fixed bar was met.
- A case used to tune anything cannot be used to certify the final bar without
  a new held-out label and evidence version.
