# G14 terminal handoff

G14 review fixes are complete and ready to merge.

The implementation is confined to `src/gigai/run.py`, the G14 regression test,
the installed-wheel verifier, and CI wiring. The graph remains immutable;
unsupported parallel capacity, non-fail-gig policies, operator gates, manual
edges, and recovery edges fail at Run scope before any Goal executor starts.

Hosted push run `31196683483` and pull-request run `31196683861` both passed
every source, wheel, and Debian lane on exact commit `30d080a`.
