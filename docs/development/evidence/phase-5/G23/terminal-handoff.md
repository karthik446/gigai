# G23 Terminal Handoff

- Status: Accepted after post-closeout repair
- Completed goal: G23 Gig Self-Containment and Portability
- Next consumer: G24 Alpha Release Readiness and Final Repository Cleanup

The post-closeout review findings were repaired and independently verified. The
implementation now
ensures that an approved active Gig version can optionally name its
content-addressed capability manifest, verify that pointer against its sealed
journal publication, resolve its proposal lineage from sealed history, and
reinstall a pinned local capability on a fresh home through G17's existing
installer.

Downstream consumers may rely on:

- `verify_active_version_portability` for read-only pointer and manifest
  verification;
- `resolve_proposal_lineage` for ordered sealed proposal history; and
- the optional active-pointer field's backward-compatible omission behavior.
- stable refusal outcomes including `refused_cycle`,
  `refused_lineage_authority`, and `refused_ambiguous_publication`;
- publication-child identity checks and capability-manifest revalidation on
  implicit re-approval.

Downstream consumers must not infer from G23 that installed capabilities are
executable, that `resolved_tools` has changed, that target effects are broader,
or that portability authorizes a Run. No automatic source fetching, network
access, credential lookup, or installed-byte transport was added.

G24 must treat G23's repaired completion evidence as a prerequisite for alpha-readiness
review, while keeping alpha/public-release declaration in the release lane.
