# G43 Terminal Handoff

**Status:** Complete

G43 provides the public preparation workflow:

```text
gigai run-plan create --gig GIG_ID --input ARTIFACT
gigai run-plan show RUN_PLAN_ID --gig GIG_ID
gigai run --plan RUN_PLAN_ID --confirm
```

The plan is immutable evidence, not a Run. `run` still owns Run allocation,
fresh direct operator consent, manifest sealing, and execution. A Run created
from a plan contains the exact plan digest and materializes the run-scoped
review/verification bridge only after `run_started` is durable.

G45 consumes this path for private stable references and pasted job input.
G46.1 consumes it for the private résumé-tailoring Gig. Do not infer provider
quality or authentication from this deterministic bridge; use explicit model
probes and the later foreground UAT for that evidence.
