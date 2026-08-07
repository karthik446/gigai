# G14 terminal handoff

G14 is complete and ready to merge.

The implementation is confined to `src/gigai/run.py`, the G14 regression test,
the installed-wheel verifier, and CI wiring. The graph remains immutable;
unsupported parallel capacity, non-fail-gig policies, operator gates, manual
edges, and recovery edges fail at Run scope before any Goal executor starts.

Hosted source, wheel, and Debian verification is green on the exact goal
commit. The pull-request event required one rerun of a pre-existing flaky G04
concurrency scenario; the rerun passed without a source change.
