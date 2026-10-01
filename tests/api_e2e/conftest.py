"""0110-028: no API e2e journey leaves its Scout server running.

Every journey stops its server in a ``finally``. This finalizer is the net
under that: after each test it stops whatever ``harness.start_server``
started and ``harness.stop_server`` did not see gone (a failure between the
start and the ``try``, a supervisor ``stop`` that did not take), and fails
the test that left it, by name, instead of leaving a detached process behind.
"""

from __future__ import annotations

import pytest

from tests.api_e2e import harness


@pytest.fixture(autouse=True)
def _no_server_outlives_its_journey():
    yield
    if not harness._STARTED:
        return
    left = harness.stop_leftover_servers()
    if left:
        pytest.fail("this journey left its Scout server running; it was stopped here:\n" + "\n".join(left))
