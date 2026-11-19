#!/bin/bash
# Disposable container only; the bundle remains read-only.
set -Eeuo pipefail
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
kind=$1
shift
if [[ "$kind" == dependency-simulation ]]; then
  cp "/bundle/$1" /var/lib/dpkg/status
  rm -f /var/lib/apt/extended_states
elif (($#)); then
  mkdir -p /tmp/seed/empty /tmp/seed/lists/partial /tmp/seed/archives/partial
  printf 'deb [trusted=yes] file:/bundle/repo ./\n' >/tmp/seed/sources.list
  cat >/tmp/seed/apt.conf <<'CONF'
Dir::Etc::parts "/tmp/seed/empty";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "/tmp/seed/sources.list";
Dir::Etc::sourceparts "/tmp/seed/empty";
Dir::State::lists "/tmp/seed/lists";
Dir::Cache::archives "/tmp/seed/archives";
APT::Install-Recommends "false";
APT::Sandbox::User "root";
CONF
  APT_CONFIG=/tmp/seed/apt.conf apt-get update
  APT_CONFIG=/tmp/seed/apt.conf apt-get --yes --no-remove install "$@"
  for seed in "$@"; do apt-mark auto "${seed%%=*}"; done
fi
apt-get check
state() {
  for path in /var/lib/dpkg/status /var/lib/apt/extended_states; do
    if [[ -f "$path" ]]; then sha256sum "$path"; else echo "absent $path"; fi
  done
  apt-mark showhold | sort
}
names() { dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' | awk '$2 == "installed" {print $1}' | sort; }
state >/tmp/state-before
names >/tmp/names-before
apt-mark showauto | sed 's/:amd64$//' | sort >/tmp/auto-before
apt-mark showmanual | sed 's/:amd64$//' | sort >/tmp/manual-before
set +e
bash /bundle/install.sh --preview >/tmp/preview 2>&1
code=$?
set -e
cat /tmp/preview
state >/tmp/state-preview
cmp /tmp/state-before /tmp/state-preview
echo VALIDATION_PREVIEW_UNCHANGED
((code == 0)) || exit "$code"
! grep -q '^Remv ' /tmp/preview
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/preview)
test -n "$token"
if [[ "$kind" == dependency-simulation ]]; then
  echo VALIDATION_SCENARIO_OK
  exit 0
fi
bash /bundle/install.sh --yes --allow-upgrades --approval-token="$token"
apt-get check
test -z "$(dpkg --audit)"
names >/tmp/names-after
test -z "$(comm -23 /tmp/names-before /tmp/names-after)"
echo VALIDATION_NO_REMOVALS
cut -d= -f1 /bundle/install-targets.txt | sort >/tmp/requested
comm -23 /tmp/auto-before /tmp/requested >/tmp/auto-expected
apt-mark showauto | sed 's/:amd64$//' | sort >/tmp/auto-after
apt-mark showmanual | sed 's/:amd64$//' | sort >/tmp/manual-after
test -z "$(comm -23 /tmp/auto-expected /tmp/auto-after)"
test -z "$(comm -23 /tmp/manual-before /tmp/manual-after)"
echo VALIDATION_FLAGS_PRESERVED
state >/tmp/state-installed
bash /bundle/install.sh --yes
state >/tmp/state-repeat
cmp /tmp/state-installed /tmp/state-repeat
echo VALIDATION_REPEAT_UNCHANGED
echo VALIDATION_SCENARIO_OK
