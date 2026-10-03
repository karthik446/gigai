"""0.1.10.7: Scout's local background pipeline (tailor -> {reassess, ats} -> label).

``store`` is the queue core (PL1): one SQLite file per project holding steps,
leases, input digests, metrics, approvals, the "new" anchor and the daily cap
counters, never any text. The steps, the runner and the triggers come later
(PL3-PL5) and build on it.
"""
