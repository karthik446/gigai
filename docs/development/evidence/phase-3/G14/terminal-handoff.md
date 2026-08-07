# G14 terminal handoff

G14 is ready for independent review and hosted CI confirmation.

The implementation is confined to `src/gigai/run.py`, the G14 regression test,
the installed-wheel verifier, and CI wiring. The graph remains immutable;
unsupported parallel capacity, non-fail-gig policies, operator gates, manual
edges, and recovery edges fail at Run scope before any Goal executor starts.

Next step: review the diff, run the hosted matrix, and merge the goal commit
only after the exact commit is green.
