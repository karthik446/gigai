# G23 Two-Home Reinstallation Record

- Status: Accepted
- Source: `tests/test_g23_portability.py::test_g23_reinstalls_from_manifest_and_source_on_second_home`
- Installed replay: `tools/verify_installed_g23.py`

Machine A contained a canonical capability manifest and a pinned local source
artifact. The fixture transported those two byte sets to a fresh Machine B
home. Before installation, B had no `tools/<capability-id>/` directory. The
source digest and identity were then checked by the unchanged G17
`install_local_capability` path, which produced an `installed` record and the
expected artifact bytes under B's isolated tool root.

The fixture also asserts that A's installed root remains absent. G23 never
reads or copies installed tool bytes; it transports the manifest and source
artifact only.
