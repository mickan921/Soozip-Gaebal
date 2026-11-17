#!/usr/bin/env bash
# Optional Soozip setup using Docker's official Ubuntu repository:
# https://docs.docker.com/engine/install/ubuntu/
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive
unset APT_CONFIG BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG
fail() { printf 'Docker setup error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 && $# -eq 1 ]] || fail 'Run Soozip install.sh --setup-docker as your normal user.'
target_user=$1
target_uid=$(id -u -- "$target_user")
[[ $target_uid -ne 0 && ${SUDO_UID:-} == "$target_uid" ]] || fail 'The target account must be the user invoking sudo.'
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == amd64 ]] || fail 'Requires Ubuntu 24.04 amd64.'
[[ -d /run/systemd/system ]] || fail 'A running Ubuntu systemd desktop is required.'
installed() { [[ $(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null || true) == installed ]]; }
if installed podman-docker; then fail 'podman-docker conflicts with Docker Engine. Review it manually; no packages were removed.'; fi
if installed docker-ce || installed docker.io; then
    printf 'Using the existing Docker Engine installation.\n'
else
    for package in docker-compose docker-compose-v2 docker-doc docker-buildx containerd runc; do
        if installed "$package"; then fail "Conflicting package $package requires manual review; no packages were removed."; fi
    done
    stage=$(mktemp -d)
    trap 'rm -rf -- "$stage"' EXIT
    cat > "$stage/docker.sources" <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [[ -f $file ]] || continue
        if grep -q 'download.docker.com' "$file" && [[ $file != /etc/apt/sources.list.d/docker.sources ]]; then
            fail "Existing Docker repository in $file requires manual review."
        fi
    done
    if [[ -e /etc/apt/sources.list.d/docker.sources ]]; then
        cmp -s "$stage/docker.sources" /etc/apt/sources.list.d/docker.sources || fail 'Existing docker.sources differs; review it manually.'
    fi
    apt-get -o APT::Update::Error-Mode=any update
    apt-get install -y --no-install-recommends --no-remove ca-certificates curl
    curl --fail --silent --show-error --location --proto '=https' --connect-timeout 30 --max-time 120 \
        https://download.docker.com/linux/ubuntu/gpg -o "$stage/docker.asc"
    test -s "$stage/docker.asc"
    install -d -m 0755 /etc/apt/keyrings
    install -m 0644 "$stage/docker.asc" /etc/apt/keyrings/docker.asc
    install -m 0644 "$stage/docker.sources" /etc/apt/sources.list.d/docker.sources
    apt-get -o APT::Update::Error-Mode=any update
    apt-get --simulate --no-remove install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    apt-get install -y --no-install-recommends --no-remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker.service
groupadd --force docker
usermod -aG docker -- "$target_user"
[[ $(docker info --format '{{.OSType}}') == linux ]] || fail 'Docker did not start as a Linux engine.'
printf '\nDocker setup complete. Log out and back in before opening Soozip so the docker group takes effect.\n'
