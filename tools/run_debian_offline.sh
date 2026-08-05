#!/bin/sh
set -eu

python tools/verify_debian_offline.py
python -m pytest -q
