#!/usr/bin/env bash
set -Eeuo pipefail
installer_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
helper="$installer_dir/_internal/install-client.sh"
[[ -f "$helper" ]] || helper="$installer_dir/../packaging/install-client.sh"
[[ -f "$helper" ]] || { echo 'Extract the complete application archive before installing.' >&2; exit 1; }
exec /bin/bash "$helper" Soozip soozip "$installer_dir" "$@"
