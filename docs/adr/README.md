# Architecture decision records

ADRs record decisions that change or supersede part of the approved V14 design.
They do not silently rewrite historical rationale.

- [ADR 0001](0001-python-version-range.md): test supported Python versions
  before setting a ceiling.
- [ADR 0002](0002-restricted-jcs-and-byte-ownership.md): restrict canonical
  JSON and make byte ownership explicit.
- [ADR 0003](0003-schema-distribution-versioning-and-extension.md): ship
  schemas in the wheel, version them by adding files, and keep them closed to
  user extension.
