#!/usr/bin/env bash
# Real downloads and disposable Docker installations; online Ubuntu 24.04 amd64 only.
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_APT_INTEGRATION=1
export OFFLINE_APT_ACCEPTANCE=1
exec python3 -m unittest discover -s "$root/tests" -p test_integration.py -v
