#!/bin/bash
set -Eeuo pipefail
mkdir -p /tmp/qa-runtime
chmod 700 /tmp/qa-runtime
export XDG_RUNTIME_DIR=/tmp/qa-runtime
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!
trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT
for attempt in {1..50}; do
    if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
    sleep 0.1
done
exec dbus-run-session -- bash -c 'openbox >/tmp/openbox.log 2>&1 & exec python3 /qa/desktop_driver.py "$@"' -- "$@"
