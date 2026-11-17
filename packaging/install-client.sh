#!/usr/bin/env bash
# Shared installer. Soozip prepares Docker; Gaebal needs no network.
set -Eeuo pipefail
app_name=$1
app_command=$2
app_source=$3
shift 3
fail() { printf 'Installation error: %s\n' "$*" >&2; exit 1; }
case "$app_name:$app_command" in Soozip:soozip|Gaebal:gaebal) ;; *) fail 'Unknown client.' ;; esac
install_prefix="$HOME/.local"
remove_client=0
docker_setup=no
[[ $app_name != Soozip ]] || docker_setup=auto
while (($#)); do
    case "$1" in
        --prefix) (($# >= 2)) || fail '--prefix needs a directory.'; install_prefix=$2; shift 2 ;;
        --uninstall) remove_client=1; shift ;;
        --setup-docker) [[ $app_name == Soozip ]] || fail 'Docker setup is only available for Soozip.'; docker_setup=yes; shift ;;
        --skip-docker-setup) [[ $app_name == Soozip ]] || fail 'Docker setup is only available for Soozip.'; docker_setup=no; shift ;;
        -h|--help)
            printf 'Usage: bash install.sh [--prefix DIR] [--uninstall]'
            [[ $app_name != Soozip ]] || printf ' [--setup-docker|--skip-docker-setup]'
            printf '\nInstalls %s for the current user (default: ~/.local), including an application-menu entry.\n' "$app_name"
            [[ $app_name != Soozip ]] || printf 'Docker setup runs by default when access is unavailable. Use --skip-docker-setup to manage Docker yourself.\n'
            exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
[[ $EUID -ne 0 ]] || fail 'Run without sudo. Only Docker setup needs administrator access.'
[[ $remove_client == 0 || $docker_setup != yes ]] || fail 'Do not combine --uninstall with --setup-docker.'
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || fail 'Requires Ubuntu 24.04 amd64.'
[[ $install_prefix == /* ]] || fail '--prefix must be an absolute path.'
[[ $install_prefix != *$'\n'* && $install_prefix != *$'\r'* && $install_prefix != *$'\t'* ]] || fail 'The installation path cannot contain control characters.'
install_prefix=$(realpath -m -- "$install_prefix")
app_source=$(realpath -- "$app_source")
app_target="$install_prefix/share/$app_command"
launcher="$install_prefix/bin/$app_command"
menu_file="$install_prefix/share/applications/$app_command.desktop"
marker='.soozip-gaebal-install'
[[ $app_target != "$app_source/"* ]] || fail 'Choose an installation prefix outside the extracted app folder.'
mkdir -p -- "$install_prefix/share" "$install_prefix/bin" "$install_prefix/share/applications"
exec 9>"$install_prefix/share/.$app_command-install.lock"
flock -n 9 || fail 'Another installation or removal of this app is running.'
# Never replace unrelated folders, launchers, or desktop entries.
if [[ -e $app_target || -L $app_target ]]; then
    [[ -d $app_target && ! -L $app_target && -f $app_target/$marker && ! -L $app_target/$marker ]] || fail "Unmanaged destination: $app_target"
    [[ $(cat -- "$app_target/$marker") == "$app_name" ]] || fail "Unmanaged destination: $app_target"
fi
if [[ -e $launcher || -L $launcher ]]; then
    [[ -f $launcher && ! -L $launcher ]] && grep -Fxq "# Managed by $app_name installer" "$launcher" || fail "Refusing to replace $launcher"
fi
if [[ -e $menu_file || -L $menu_file ]]; then
    [[ -f $menu_file && ! -L $menu_file ]] && grep -Fxq "X-Soozip-Gaebal-App=$app_name" "$menu_file" || fail "Refusing to replace $menu_file"
fi
if ((remove_client)); then
    rm -f -- "$launcher" "$menu_file"
    [[ ! -d $app_target ]] || rm -rf -- "$app_target"
    command -v update-desktop-database >/dev/null && update-desktop-database "$install_prefix/share/applications" >/dev/null 2>&1 || true
    printf '%s removed. Installed Ubuntu packages and Docker were left unchanged.\n' "$app_name"
    exit 0
fi
for file in "$app_name" offline-apt-worker _internal/bin/7zz release.json CLIENT-SHA256SUMS; do
    [[ -f $app_source/$file ]] || fail "Missing $file. Run install.sh from the complete portable $app_name archive, not the source checkout."
done
printf 'Verifying %s application files…\n' "$app_name"
(cd -- "$app_source" && sha256sum --check --quiet CLIENT-SHA256SUMS) || fail 'Application files are damaged or incomplete. Extract a fresh archive.'
prerequisites=(apt-get dpkg dpkg-query)
[[ $app_name != Gaebal ]] || prerequisites+=(pkexec)
for command in "${prerequisites[@]}"; do
    command -v "$command" >/dev/null || fail "Missing Ubuntu prerequisite: $command. Prepare the standard desktop prerequisites before installing this client."
done
if [[ $docker_setup != no ]]; then
    if command -v docker >/dev/null && [[ $(docker info --format '{{.OSType}}' 2>/dev/null) == linux ]]; then
        printf 'Docker is already accessible; no Docker changes needed.\n'
    else
        printf 'Preparing Docker access: install Docker Engine if needed, enable its service, and add your account to the docker group (administrator-equivalent access). Installation may use the internet.\n'
        command -v sudo >/dev/null || fail 'Docker setup requires sudo.'
        sudo /bin/bash "$app_source/_internal/setup-docker.sh" "$(id -un)"
    fi
fi
# Copy the entire new runtime before replacing an existing managed installation.
stage=$(mktemp -d "$install_prefix/share/.$app_command-stage.XXXXXX")
app_published=0
launcher_published=0
menu_published=0
installation_complete=0
cleanup() {
    code=$?
    trap - EXIT
    if (( ! installation_complete )); then
        if ((app_published)); then rm -rf -- "$app_target"; fi
        if [[ -d $stage/previous-app ]]; then mv -T -- "$stage/previous-app" "$app_target"; fi
        if ((launcher_published)); then
            rm -f -- "$launcher"
            [[ ! -f $stage/previous-launcher ]] || cp -p -- "$stage/previous-launcher" "$launcher"
        fi
        if ((menu_published)); then
            rm -f -- "$menu_file"
            [[ ! -f $stage/previous-menu ]] || cp -p -- "$stage/previous-menu" "$menu_file"
        fi
    fi
    rm -rf -- "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -- "$stage/app"
cp -a -- "$app_source/." "$stage/app/"
printf '%s\n' "$app_name" > "$stage/app/$marker"
chmod u+x -- "$stage/app/$app_name" "$stage/app/offline-apt-worker" "$stage/app/_internal/bin/7zz" "$stage/app/install.sh"
{
    printf '#!/bin/bash\n# Managed by %s installer\n' "$app_name"
    printf 'exec %q "$@"\n' "$app_target/$app_name"
} > "$stage/launcher"
chmod 755 -- "$stage/launcher"
# Exec quoting follows the freedesktop Desktop Entry Specification. The first
# escaping pass handles its quoted command argument, the second its string value.
exec_path=$launcher
exec_path=${exec_path//\\/\\\\}
exec_path=${exec_path//\"/\\\"}
exec_path=${exec_path//\$/\\\$}
exec_path=${exec_path//\`/\\\`}
exec_path=${exec_path//%/%%}
exec_path=${exec_path//\\/\\\\}
cat > "$stage/menu" <<EOF
[Desktop Entry]
Type=Application
Name=$app_name
Comment=$([[ $app_name == Soozip ]] && printf 'Build Ubuntu packages for offline installation' || printf 'Install validated Ubuntu package bundles offline')
Exec="$exec_path"
Icon=system-software-install
Terminal=false
Categories=System;PackageManager;
StartupWMClass=$app_name
X-Soozip-Gaebal-App=$app_name
EOF
[[ ! -f $launcher ]] || cp -p -- "$launcher" "$stage/previous-launcher"
[[ ! -f $menu_file ]] || cp -p -- "$menu_file" "$stage/previous-menu"
[[ ! -d $app_target ]] || mv -T -- "$app_target" "$stage/previous-app"
app_published=1
mv -T -- "$stage/app" "$app_target"
launcher_published=1
mv -T -- "$stage/launcher" "$launcher"
menu_published=1
mv -T -- "$stage/menu" "$menu_file"
installation_complete=1
command -v update-desktop-database >/dev/null && update-desktop-database "$install_prefix/share/applications" >/dev/null 2>&1 || true
printf '\n%s installed.\nTerminal command: %s\n' "$app_name" "$launcher"
docker_ready=1
if [[ $app_name == Soozip ]]; then
    if ! command -v docker >/dev/null || [[ $(docker info --format '{{.OSType}}' 2>/dev/null) != linux ]]; then
        docker_ready=0
        if [[ $docker_setup != no ]]; then
            printf '\nBefore opening Soozip: fully log out of Ubuntu and log back in, or reboot. Opening a new terminal is not enough to refresh desktop Docker group permissions.\n'
        else
            printf '\nDocker setup was skipped and Docker is not accessible in this session. To configure it, run: bash %q --prefix %q --setup-docker\n' "$app_target/install.sh" "$install_prefix"
            printf 'After group changes, fully log out of Ubuntu and log back in, or reboot.\n'
        fi
        printf 'After signing back in, run docker info without sudo. Open Soozip when that succeeds.\n'
    fi
fi
((docker_ready == 0)) || printf 'Open %s from the application menu.\n' "$app_name"
printf 'To remove this client: bash %q --prefix %q --uninstall\n' "$app_target/install.sh" "$install_prefix"
