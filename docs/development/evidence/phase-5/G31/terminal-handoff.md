# G31 terminal handoff

- Status: Machine gate ready; final release gate open
- Next consumer: G12 release mechanics and the operator's final v0.1.6 UAT
- Later consumer: G32 public post-release documentation, then G25 alpha review

Before publication:

1. merge the release-candidate PR;
2. set package metadata and the release commit to `0.1.6`;
3. run the owner-controlled exact-tag workflow from merged `main`;
4. verify TestPyPI, PyPI, fresh install, and upgrade on the release artifact;
5. complete the remaining human UAT scenarios and append sanitized results; and
6. mark G31 complete only if the exact artifact and UAT record both pass.

G31 does not authorize a local tag, direct main-branch release, or alpha claim.
