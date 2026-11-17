#!/bin/bash
# Run only inside a disposable Ubuntu container with /bundle mounted read-only.
# Downloading against an empty status file proves the base image cannot satisfy
# dependencies on behalf of an incomplete bundle. No packages are installed.
set -euo pipefail
recommends=$1
omitted=$2
shift 2
mkdir -p /tmp/closure/{repo,lists/partial,archives/partial,empty-parts}
: > /tmp/closure/status
ln -s /bundle/repo/*.deb /tmp/closure/repo/
awk -v omitted="$omitted" 'BEGIN { RS=""; ORS="\n\n" }
    omitted == "" || index("\n" $0 "\n", "\nPackage: " omitted "\n") == 0
' /bundle/repo/Packages > /tmp/closure/repo/Packages
printf 'deb [trusted=yes] file:/tmp/closure/repo ./\n' > /tmp/closure/sources.list
cat > /tmp/closure/apt.conf <<EOF
Dir::Etc::parts "/tmp/closure/empty-parts";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "/tmp/closure/sources.list";
Dir::Etc::sourceparts "/tmp/closure/empty-parts";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "/tmp/closure/empty-parts";
Dir::State::status "/tmp/closure/status";
Dir::State::lists "/tmp/closure/lists";
Dir::State::extended_states "/tmp/closure/extended_states";
Dir::Cache::archives "/tmp/closure/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
APT::Architecture "amd64";
APT::Install-Recommends "$recommends";
APT::Install-Suggests "false";
APT::Sandbox::User "root";
APT::Update::Error-Mode "any";
Acquire::Languages "none";
EOF
export APT_CONFIG=/tmp/closure/apt.conf LC_ALL=C
sha256sum /var/lib/dpkg/status > /tmp/closure/host-status.sha256
apt-get update
apt-get --assume-yes --no-remove --download-only install "$@"
test ! -s /tmp/closure/status
sha256sum --check /tmp/closure/host-status.sha256
printf 'EMPTY_STATUS_CLOSURE_OK\n'
