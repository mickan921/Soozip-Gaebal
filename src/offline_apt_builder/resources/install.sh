#!/usr/bin/env bash
# Shipped standalone installer. Requires only standard Ubuntu 24.04 tools.
set -Eeuo pipefail
export LC_ALL=C

usage() {
  echo 'Usage: sudo ./install.sh [--dry-run|--preview] [--yes] [--allow-upgrades] [--allow-downgrades]'
}
dry_run=0
preview=0
approval_token=""
assume_yes=0
allow_upgrades=0
allow_downgrades=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --preview) dry_run=1; preview=1 ;;
    --approval-token=*) approval_token="${arg#*=}" ;;
    --yes) assume_yes=1 ;;
    --allow-upgrades) allow_upgrades=1 ;;
    --allow-downgrades) allow_downgrades=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
((EUID == 0 || preview == 1)) || { echo 'Run this installer with sudo.' >&2; exit 1; }
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# A separate worker retains errexit semantics while tee records diagnostics.
# This avoids process-substitution logging and keeps all APT state temporary.
if [[ "${_OFFLINE_APT_LOGGED:-}" != 1 ]] && ((preview == 0)); then
  log_dir="$(mktemp -d /tmp/offline-apt-log.XXXXXX)"
  finish_log() {
    code=$?
    if ((code == 0)); then
      rm -rf -- "$log_dir"
    else
      echo "Installation failed (exit $code). Diagnostics retained: $log_dir/install.log" >&2
    fi
  }
  trap finish_log EXIT
  _OFFLINE_APT_LOGGED=1 bash "$root/install.sh" "$@" 2>&1 | tee "$log_dir/install.log"
  exit 0
fi

fail() { echo "ERROR: $*" >&2; exit 1; }
for command in apt-get apt-cache apt-mark dpkg dpkg-query sha256sum awk grep tee mktemp; do
  command -v "$command" >/dev/null || fail "Required Ubuntu tool missing: $command"
done
[[ -r /etc/os-release ]] || fail 'Missing /etc/os-release'
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || fail "Requires Ubuntu 24.04; found ${ID:-unknown} ${VERSION_ID:-unknown}."

required=(install.sh architecture.txt with-recommends.txt packages.txt install-targets.txt manifest.json build-info.txt README-offline.txt test-report.json repo/Packages repo/Packages.gz repo/Packages.xz)
[[ -f "$root/SHA256SUMS" ]] || fail 'Missing SHA256SUMS'
for file in "${required[@]}"; do
  [[ -f "$root/$file" && ! -L "$root/$file" ]] || fail "Missing or unsafe bundle file: $file"
  # Every operational input must itself be covered by checksum verification.
  awk -v path="./$file" '$2 == path && NF == 2 && length($1) == 64 { found++ } END { exit(found != 1) }' "$root/SHA256SUMS" || fail "Checksum entry missing or duplicated for $file"
done
echo '[1/4] Verifying bundle checksums...'
(cd -- "$root" && sha256sum --strict --check --quiet SHA256SUMS) || fail 'Checksum verification failed; transfer a fresh verified archive.'
[[ "$(cat "$root/architecture.txt")" == amd64 && "$(dpkg --print-architecture)" == amd64 ]] || fail 'Requires an amd64 bundle and amd64 target.'
recommends="$(cat "$root/with-recommends.txt")"
[[ "$recommends" == 0 || "$recommends" == 1 ]] || fail 'Invalid Recommends setting'
mapfile -t targets < "$root/install-targets.txt"
((${#targets[@]})) || fail 'Empty installation target list'
for target in "${targets[@]}"; do
  [[ "$target" =~ ^[a-z0-9][a-z0-9+.-]+=[a-zA-Z0-9.+:~_-]+$ ]] || fail "Invalid resolved installation target: $target"
done

tmp="$(mktemp -d /tmp/offline-apt-state.XXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$tmp/empty" "$tmp/lists/partial" "$tmp/archives/partial" "$tmp/methods" "$tmp/log"
# The alias keeps source-list syntax independent of spaces/# in extraction paths.
ln -s -- "$root/repo" "$tmp/repo"
# APT has no HTTP/HTTPS (or other remote) acquisition handler in this transaction.
for method in file copy store; do
  [[ -x "/usr/lib/apt/methods/$method" ]] || fail "Missing APT method: $method"
  ln -s "/usr/lib/apt/methods/$method" "$tmp/methods/$method"
done
printf 'deb [trusted=yes] file:%s/repo ./\n' "$tmp" > "$tmp/offline.list"
cat > "$tmp/apt.conf" <<CONFIG
Dir::Etc::parts "$tmp/empty";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "$tmp/offline.list";
Dir::Etc::sourceparts "$tmp/empty";
Dir::State::status "/var/lib/dpkg/status";
Dir::State::lists "$tmp/lists";
Dir::Cache::archives "$tmp/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Bin::Methods "$tmp/methods";
Dir::Log "$tmp/log";
APT::Install-Recommends "$recommends";
APT::Install-Suggests "false";
APT::Get::Always-Include-Phased-Updates "true";
APT::Sandbox::User "$(id -un)";
Acquire::Languages "none";
Acquire::Retries "0";
APT::Update::Error-Mode "any";
CONFIG
export APT_CONFIG="$tmp/apt.conf"
echo 'Integrity: verified SHA-256 checksums; the local repository uses trusted=yes.'
echo '[2/4] Loading the local-only APT repository...'
apt-get update
check_policy() {
  local name="$1" version="$2" priority
  priority="$(apt-cache policy "$name" | awk -v version="$version" '
    $1 == version && NF == 2 { print $2; exit }
    $1 == "***" && $2 == version { print $3; exit }')"
  [[ "$priority" =~ ^-?[0-9]+$ ]] || fail "Cannot determine target APT policy for $name=$version."
  ((priority > 0)) || fail "Target APT pinning forbids $name=$version (priority $priority)."
}
for target in "${targets[@]}"; do
  check_policy "${target%%=*}" "${target#*=}"
done
state_fingerprint() {
  {
    sha256sum /var/lib/dpkg/status
    for file in /var/lib/apt/extended_states /etc/apt/preferences; do
      if [[ -e "$file" ]]; then sha256sum "$file"; fi
    done
    if [[ -d /etc/apt/preferences.d ]]; then
      find -L /etc/apt/preferences.d -type f -print0 | sort -z | xargs -0 -r sha256sum
    fi
    apt-mark showhold | sort
  } | sha256sum | awk '{print $1}'
}
state_before="$(state_fingerprint)"
apt-mark showhold | sed 's/:.*//' > "$tmp/holds"
echo '[3/4] Planned changes:'
apt-get --simulate --no-remove install "${targets[@]}" | tee "$tmp/simulation"
if grep -Eq '^Remv[[:space:]]' "$tmp/simulation"; then
  fail 'Package removals are forbidden. Resolve the conflict without removing packages.'
fi
awk '$1 == "Inst" { ver=$0; sub(/^[^(]*\(/,"",ver); sub(/ .*/,"",ver); print $2 "\t" ver }' "$tmp/simulation" > "$tmp/changes"
: > "$tmp/upgrades"
: > "$tmp/downgrades"
: > "$tmp/actions"
while IFS=$'\t' read -r name new; do
  [[ -n "$name" ]] || continue
  [[ "$new" =~ ^[a-zA-Z0-9.+:~_-]+$ ]] || fail 'Cannot parse APT simulation; refusing installation.'
  check_policy "$name" "$new"
  current="$(dpkg-query -W -f='${db:Status-Status}\t${Version}' "$name" 2>/dev/null || true)"
  old=''
  if [[ "$current" == installed$'\t'* ]]; then old="${current#*$'\t'}"; fi
  if [[ "$old" != "$new" ]] && grep -Fxq "${name%%:*}" "$tmp/holds"; then
    fail "Changing held package $name is forbidden."
  fi
  action=install
  if [[ -n "$old" ]]; then
    action=reinstall
    if dpkg --compare-versions "$new" gt "$old"; then
      action=upgrade
      printf '%s %s -> %s\n' "$name" "$old" "$new" >> "$tmp/upgrades"
    elif dpkg --compare-versions "$new" lt "$old"; then
      action=downgrade
      printf '%s %s -> %s\n' "$name" "$old" "$new" >> "$tmp/downgrades"
    fi
  fi
  printf 'PLAN\t%s\t%s\t%s\t%s\n' "$name" "$action" "$old" "$new" >> "$tmp/actions"
done < "$tmp/changes"
[[ "$(state_fingerprint)" == "$state_before" ]] || fail 'Target state changed during preview; review again.'
computed_token="$({ printf '%s\n' "$state_before"; (cd -- "$root" && sha256sum SHA256SUMS); cat "$tmp/actions"; } | sha256sum | awk '{print $1}')"
if ((preview)); then
  cat "$tmp/actions"
  printf 'APPROVAL\t%s\n' "$computed_token"
fi
if [[ -n "$approval_token" && "$approval_token" != "$computed_token" ]]; then
  fail 'The approved bundle or target transaction changed. Review the bundle again.'
fi
if [[ -s "$tmp/upgrades" ]]; then echo 'Upgrades requiring explicit approval:'; cat "$tmp/upgrades"; fi
if [[ -s "$tmp/downgrades" ]]; then echo 'Downgrades requiring explicit approval:'; cat "$tmp/downgrades"; fi
if ((dry_run)); then echo 'Dry run complete; no packages were installed.'; exit 0; fi

approve() {
  local prompt="$1" answer
  [[ -t 0 ]] || fail "Noninteractive installation needs explicit approval: $prompt"
  read -r -p "$prompt [y/N] " answer || fail 'No approval received.'
  case "$answer" in y|Y|yes|YES) ;; *) fail 'Installation cancelled; no packages installed.' ;; esac
}
if [[ -s "$tmp/upgrades" ]] && ((allow_upgrades == 0)); then
  ((assume_yes == 0)) || fail 'Use --allow-upgrades to approve the listed upgrades; --yes does not approve them.'
  approve 'Approve all listed upgrades?'
  allow_upgrades=1
fi
if [[ -s "$tmp/downgrades" ]] && ((allow_downgrades == 0)); then
  ((assume_yes == 0)) || fail 'Use --allow-downgrades to approve the listed downgrades; --yes does not approve them.'
  approve 'Approve all listed downgrades?'
  allow_downgrades=1
fi
if ((assume_yes == 0)); then approve 'Install the displayed package transaction?'; fi
[[ "$(state_fingerprint)" == "$state_before" ]] || fail 'Installed package state changed during review. Run the installer again to obtain a fresh plan.'
options=(--assume-yes --no-remove)
if ((allow_downgrades)); then options+=(--allow-downgrades); fi
if ((assume_yes)); then export DEBIAN_FRONTEND=noninteractive; fi
echo '[4/4] Installing and checking dependency consistency...'
apt-get "${options[@]}" install "${targets[@]}"
apt-get check
for target in "${targets[@]}"; do
  name="${target%%=*}"
  expected="${target#*=}"
  current="$(dpkg-query -W -f='${db:Status-Status}\t${Version}' "$name")"
  [[ "$current" == "installed"$'\t'"$expected" ]] || fail "Requested package/provider $name is not fully installed at $expected."
  printf 'INSTALLED\t%s\t%s\n' "$name" "$expected"
done
echo 'SUCCESS: requested packages and providers installed; APT dependency check passed.'
