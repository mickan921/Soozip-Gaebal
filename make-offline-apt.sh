#!/usr/bin/env bash
# Compatibility entry point for source checkouts; no installation required.
set -Eeuo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONPATH="$script_dir/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m offline_apt_builder build "$@"
