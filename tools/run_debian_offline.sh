#!/bin/sh
set -eu

# The container sees the host runner's CPU count (4 vCPU on GitHub-hosted
# ubuntu-latest), so the same bounded xdist plan used by `make test-source`
# applies here.  Running the 1886-test source suite single-threaded (plain
# `python -m pytest -q`) took ~1h19m in the v0.1.7 release CI; xdist cuts
# that to roughly wall-clock/4.
python tools/verify_debian_offline.py
python tools/run_ci_tests.py source \
    --xdist-workers "${TEST_XDIST_WORKERS:-auto}" \
    --xdist-max-workers "${TEST_XDIST_MAX_WORKERS:-4}" \
    --xdist-dist "${TEST_XDIST_DIST:-worksteal}"
