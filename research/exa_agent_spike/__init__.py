"""S23 Exa Agent company-discovery spike: spend-guarded scripts, not a library.

See ``README.md`` in this directory and the S23 spike
(kept in the maintainers' local orchestrator docs)
for the research record. Deliberately no re-exports here -- each script
(``run_agent.py``, ``check_boards.py``, ``spend_guard.py``, ``redact.py``)
is invoked directly, not imported as a package API.
"""
