SHELL := /bin/sh

UV ?= uv
WHEEL_PYTHON ?= .wheel-venv/bin/python
TEST_XDIST_WORKERS ?= auto
TEST_XDIST_MAX_WORKERS ?= 14
TEST_XDIST_DIST ?= worksteal

.PHONY: test test-source test-behavior test-wheel test-installed test-live test-debian-offline

# Complete portable offline coverage: one source discovery pass, the existing
# deterministic behavior evaluation, and a fresh wheel plus every installed
# verifier and installed test node.  Live/provider/UAT and Debian-container
# gates are deliberately separate targets below.
test: test-source test-behavior test-wheel

# The unfiltered invocation is authoritative for source, unit, integration,
# behavior-directory, CLI, and source-installed test discovery.  It uses a
# capped resource-aware xdist plan by default; override TEST_XDIST_WORKERS for
# measurement, e.g. TEST_XDIST_WORKERS=14.  The cap matches the measured
# 14-CPU host while the runner clamps actual workers to os.cpu_count().  Do not
# add overlapping G28 file selectors here; they were previously run and then
# repeated by this full invocation in the default PR lane.
test-source:
	$(UV) run --locked --extra test python tools/run_ci_tests.py source \
		--xdist-workers "$(TEST_XDIST_WORKERS)" \
		--xdist-max-workers "$(TEST_XDIST_MAX_WORKERS)" \
		--xdist-dist "$(TEST_XDIST_DIST)"

# G28's deterministic evaluator is not a pytest test and therefore remains an
# explicit offline phase in the aggregate command.
test-behavior:
	$(UV) run --locked --extra test python tools/run_ci_tests.py behavior

# Build into a disposable directory and run the wheel-installed lane once.
# The runner enumerates every tools/verify_installed_*.py script, including
# newly added verifiers, then executes direct AST-derived installed-test ids.
# xdist options are intentionally source-lane-only and do not alter this lane.
test-wheel:
	$(UV) run --locked --extra test python tools/run_ci_tests.py wheel --wheel-python "$(WHEEL_PYTHON)"

# CI's wheel job can call this after its setup/install steps without rebuilding
# the wheel.  It still runs the complete installed verifier/test selection.
test-installed:
	$(UV) run --locked --extra test python tools/run_ci_tests.py installed --wheel-python "$(WHEEL_PYTHON)"

# Real local-model/provider/UAT execution requires explicit operator consent
# and configuration.  It never runs from `make test`.
test-live:
	@if [ "$${GIGAI_G30_UAT:-}" != "1" ]; then \
		echo "refusing live/provider UAT: set GIGAI_G30_UAT=1 explicitly" >&2; \
		exit 2; \
	fi
	$(UV) run --locked --extra test pytest -m g30_live

# Debian's direct-mount/read-only container contract is a platform-specific
# CI gate.  It remains explicit because it cannot be truthfully run on every
# developer host and is not silently treated as a portable local pass.
test-debian-offline:
	docker build --tag gigai-debian-offline:local --file containers/debian-offline/Dockerfile .
	docker run --rm \
		--network none \
		--read-only \
		--user 10001:10001 \
		--tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
		--tmpfs /audit/home:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--tmpfs /audit/target:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--tmpfs /audit/workpad:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--env HOME=/audit/home \
		--env GIGAI_AUDIT_HOME=/audit/home \
		--env GIGAI_AUDIT_TARGET=/audit/target \
		--env GIGAI_AUDIT_WORKPAD=/audit/workpad \
		gigai-debian-offline:local
