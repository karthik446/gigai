# G21 Installed Replay

- Status: Accepted evidence
- Original verifier commit: `6541ca9`
- Corrective wheel verification: `2026-08-11`, after `702ff1c`, `e8e5269`,
  and `d654adc`
- Wheel: `gigai-0.1.3-py3-none-any.whl`

The fresh wheel was installed into a disposable Python 3.11 environment and
verified without importing the source checkout:

- `verify_installed_schemas.py`: `verified 27 installed GigAI schemas`;
- `verify_installed_g20.py`: `verified installed GigAI G20 improve lifecycle`;
- `verify_installed_g21.py`: `verified installed GigAI G21 daily, weekly, and monthly occurrences`.

The corrected wheel includes the refusal actor/outcome schema constraints and
the repaired runtime boundaries. Its occurrence-mark API/CLI requires an
explicit outcome actor; all three verifiers were rerun after the final
corrective commit.

The G21 verifier exercises daily comparison, weekly `missed`, and monthly
occurrence behavior from the installed package. The wheel CI job now runs the
G19, G20, and G21 installed verifiers.
