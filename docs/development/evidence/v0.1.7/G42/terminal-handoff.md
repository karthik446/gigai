# G42 Terminal Handoff

**Status:** Complete
**Next consumers:** G43 and G44

G42 leaves v0.1.7 with:

- three deterministic built-in catalog entries;
- one validated G41 package artifact per catalog entry/version;
- deterministic v4-compatible package IDs and golden vectors;
- CLI list, inspect, validate, and install operations;
- bootstrap install followed by explicit G41 package adoption;
- one installed catalog package maximum per project;
- no catalog installation-record authority; and
- no provider, network, credential, hook, approval, capability, journal,
  workpad, or Run side effects.

G43 must preserve the catalog/package boundary while adding adaptive review
profiles and sealed Run Plans. G44 must preserve catalog provenance while
adding clone/create-from authoring behavior.
