"""0.1.10.7: Scout's local background pipeline (tailor -> {reassess, ats} -> label).

``store`` is the queue core (PL1): one SQLite file per project holding steps,
leases, input digests, metrics, approvals, the "new" anchor and the daily cap
counters, never any text. ``steps`` (PL3) is what each step does and what its
input digest is made of; ``runner`` (PL4) claims steps and runs them, as a
thread of the Scout server and as ``gigai scout pipeline run --once``;
``settings`` reads the ``pipeline`` block of the project's settings file. The
triggers come later (PL5) and call ``steps.enqueue_job``.
"""
