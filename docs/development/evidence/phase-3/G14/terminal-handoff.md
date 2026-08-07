# G14 terminal handoff

G14 review fixes are ready for fresh hosted confirmation.

The implementation is confined to `src/gigai/run.py`, the G14 regression test,
the installed-wheel verifier, and CI wiring. The graph remains immutable;
unsupported parallel capacity, non-fail-gig policies, operator gates, manual
edges, and recovery edges fail at Run scope before any Goal executor starts.

The prior implementation commit was hosted-green; this follow-up changes the
scheduler and regression corpus, so merge remains gated on the fresh exact-head
matrix.
