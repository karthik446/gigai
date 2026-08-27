# G41 Terminal Handoff

**Status:** Complete
**Next consumers:** G42 and G44

G41 leaves v0.1.7 with these accepted inputs:

- portable packages live under `.gigai/packages/<package-id>/`;
- `package.json` is the strict manifest and package digest authority;
- `.gigai/project.toml` remains the private project-binding authority;
- `local/`, `runs/`, `cache/`, `snapshots/`, and `locks/` remain private;
- `gigai init --adopt-package --confirm` is the only tracked-package adoption
  path;
- package installation preserves package identity while creating only a fresh
  private installation record; and
- v0.1.6 upgrade recovery preserves predecessor configuration, registry,
  workpad, journal, and authority evidence.

G42 must consume the package validator and normal approval authority. It must
not place credentials, private Run history, executable hooks, or automatic
installation behavior into the package.

G44 must use package material as portable authoring input and keep proposal,
approval, version, and Run authority in the existing private lifecycle. It
must not treat a package digest as approval or readiness evidence.

No G42, G43, or G44 runtime behavior is included in this handoff.
