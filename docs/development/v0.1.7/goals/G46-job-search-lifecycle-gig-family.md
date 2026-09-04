# G46 — Job Search Lifecycle Gig Family and Evaluation Packs

**Version:** v0.1.7
**Status:** Proposed — contract defined; not activated
**Depends on:** G43.2, G44, and G45
**Delivers:** private Job Search Lifecycle Gig v1 and explicitly approved v2

## Outcome

G46 defines one private `job-search-lifecycle` Gig whose v0.1.7 commission is
strictly *operator-started agent job research and application tailoring*.
“Lifecycle” does not mean interview scheduling, outreach, application
submission, recurring execution, or application-pipeline automation.

```text
v1: tailor-application
v2: tailor-application + find-jobs-for-requirements
```

The Gig owns the shared private evidence namespace, privacy/effect ceiling,
operator-visible job-search requirement vocabulary, version lineage, and
evaluation vocabulary. It grants no graph access by itself. Each graph narrows
the shared policy and declares its own inputs, outputs, sources, effects,
budget, review, and evaluation.

## Graph boundary and handoff

`tailor-application` reads selected private résumé evidence and one sealed
job-description input. `find-jobs-for-requirements` reads one explicitly
supplied requirements profile and one approved G45.2 Exa research policy. Its
assigned Codex and Claude research roles may direct bounded, mediated Exa
queries within the sealed Plan; tailoring may not perform research reads.

The only v0.1.7 graph handoff is explicit:

```text
Find Run -> immutable private lead snapshot -> operator selects LEAD_ID
  -> sealed lead-to-job-description input binding -> Tailor Run Plan
```

The binding is created by direct operator confirmation, is idempotent, and does
not start a Tailor Run, grant provider permission, or make other Find output
ambient input. A user may instead paste/import a job description through G45.

## Evaluation ownership

The Tailor pack evaluates factuality, source preservation, material-requirement
coverage, gap disclosure, output validity, and operator usefulness. The Find
pack evaluates research-policy compliance, role-separated query evidence,
citation/snapshot provenance completeness, freshness, lead normalization,
within-Run deduplication, requirement-grounded relevance explanations, no-match
behavior, and honest source/provider failure.

No Tailor result proves Find quality and no Find ranking result proves tailored
document factuality. Both packs freeze their development/held-out split,
rubric, human adjudication, cost/latency accounting, failure treatment, and
reproducibility rule before held-out execution.

## v0.1.8 boundary

Scheduling and application pipeline state are deferred. v0.1.8 may add an
append-only private application journal with system evidence `discovered` and
operator-only transitions `saved`, `rejected`, `applied`, `interviewing`, and
`closed`. A scheduled discovery graph may consult its rebuildable projection
for suppression/deduplication but must not infer or mutate operator-owned
states. Scheduled tailoring and application submission remain off by default.

## Stop conditions

Stop if research occurs outside the sealed Exa policy or budget, tailoring
receives a lead without an explicit binding, either graph inherits the other
graph's effect or provider permission, a graph's evaluation evidence is
borrowed by the other, or lifecycle wording is used to imply unimplemented
automation.
