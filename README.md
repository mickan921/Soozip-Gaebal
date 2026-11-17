# Soozip & Gaebal

**Soozip** builds a portable local APT repository on an **online Ubuntu 24.04 amd64 workstation**, tests the shipped installer in a fresh container with **networking disabled**, and produces one `.7z` archive (with an optional checksum sidecar). Transfer it into an air-gapped Ubuntu 24.04 amd64 machine and install it with **Gaebal**.

The target needs standard Ubuntu APT/dpkg/Bash tools and sufficient disk space. It does not need Docker, Python additions, GitHub access, or an internet connection. Nothing needs to leave the air-gapped network. The application never uploads bundles, reports, or logs.

The online builder assumes none of the requested software or its dependencies
are installed on the destination. It bundles required dependencies recursively,
including dependencies of dependencies and pre-dependencies, even when they are
already installed on the online machine. For example, a `jq` bundle includes
`libjq1`, `libonig5`, `libc6`, and their required dependencies. Optional
`Recommends` are included when selected; `Suggests` are excluded. A minimal
installed Ubuntu system is still required; the bundle is not an OS installer.

Two native PySide6 desktop clients and the Python CLI share the build and installation engines. macOS, arm64, cross builds, PPAs, and vendor repositories are unsupported.

Both clients display their application version in the bottom-right corner.

## Download and transfer the portable apps

Use **Ubuntu 24.04 LTS, amd64 (x86_64)** on both machines. Other Ubuntu releases,
ARM machines, Windows, and macOS are not supported. The GUI requires an existing
Ubuntu desktop with Wayland or X11; these archives do not install an operating
system or desktop environment.

The final app archives are in `dist/`. Soozip is the online builder; Gaebal is the offline installer:

| File | Where to use it |
| --- | --- |
| `Soozip-ubuntu24.04-amd64.tar.gz` | Internet-connected Ubuntu machine with local Docker Engine |
| `Soozip-ubuntu24.04-amd64.tar.gz.sha256` | Checksum for the online app archive |
| `Gaebal-ubuntu24.04-amd64.tar.gz` | Disconnected Ubuntu destination machine |
| `Gaebal-ubuntu24.04-amd64.tar.gz.sha256` | Checksum for the offline app archive |

Each app archive includes its Python/Qt runtime, worker, standalone 7-Zip
extractor, `install.sh`, this README, licenses, and `release.json`. **No Python, pip, PySide6,
or 7-Zip installation is required to run the portable apps.** The offline app
does not need Docker. The online app requires a separately installed local
Docker Engine and permission to use it.

Keep these two kinds of archive distinct:

- `.tar.gz`: the reusable online or offline **application**.
- `.7z`: the **packages and dependencies** produced by the online app. The
  offline app alone does not contain the software you want to install.

Keep each extracted application folder intact, including `_internal/` and
`offline-apt-worker`. Do not copy only the main executable.

## Install and use Soozip (online)

1. Copy the Soozip `.tar.gz` and its `.sha256` file into `~/Downloads`.
   Verify and extract the archive, then run the installer as your normal user:

   ```bash
   cd "$HOME/Downloads"
   sha256sum -c Soozip-ubuntu24.04-amd64.tar.gz.sha256
   tar -xzf Soozip-ubuntu24.04-amd64.tar.gz
   bash Soozip/install.sh
   ```

   Continue only if the archive checksum reports `OK`. The installer checks
   the extracted application files, copies the complete runtime to
   `~/.local/share/soozip`, and creates an application-menu entry plus
   `~/.local/bin/soozip`. It replaces older installations made by this script
   without mixing runtime files.

   The installer checks Docker access by default. If Docker is already
   accessible, it makes no Docker changes. Otherwise it asks
   for sudo authentication, installs Docker Engine from its official repository
   when needed, enables the service, and adds your account to the `docker` group.
   This group grants administrator-equivalent access. **Fully log out of Ubuntu
   and log back in, or reboot, after group changes.** Opening another terminal
   alone does not refresh the desktop session's group permissions. Verify
   `docker info` works without sudo before opening Soozip.
   Existing conflicting runtimes or custom Docker repositories
   are reported for manual review; the script does not remove them.

   If you manage Docker yourself, use `bash Soozip/install.sh --skip-docker-setup`.
   The existing `--setup-docker` option is still accepted. Gaebal's installer
   never configures Docker or uses the network.

   Docker setup follows [Docker's Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/)
   and [non-root access guidance](https://docs.docker.com/engine/install/linux-postinstall/).
2. Open **Soozip** from Ubuntu's application menu, or run:

   ```bash
   "$HOME/.local/bin/soozip"
   ```

   The app itself always runs as your normal user. Do not run `install.sh` or
   the GUI with sudo; the installer elevates only Docker setup when needed.
3. Enter package names, for example `git cmake build-essential libssl-dev`, or
   use **Import Package List…** to select a text file. Use names only, without
   version selectors such as `git=...`. Text files allow whitespace-separated
   names, blank lines, and `#` comments.
4. Choose an output folder. Select **Include recommended packages** if desired.
   Required dependencies at every level are always included; this option only
   adds recommendations.
5. Click **Build and Validate Bundle**. Keep internet access available while
   the builder downloads packages. It then installs them in a fresh Docker
   container with networking disabled before publishing the archive.
6. Wait for **VALIDATED**. Transfer the resulting `.7z` to the destination,
   together with its `.7z.sha256` sidecar when available. The report and build
   log are optional diagnostic files. A failed build does not publish a finished
   bundle; the desktop app has no skip-validation option.

The online machine needs free space for Docker storage, temporary downloads,
validation containers, and the output archive. Allow several GiB; large package
sets require more. Dependencies already installed on the online machine are
still included in the bundle.

## Install Gaebal on a disconnected machine

These steps work without internet access and do not run APT or pip downloads.

### 1. Prepare the destination and transfer the files

The destination must already have Ubuntu 24.04 amd64, a graphical desktop,
standard APT/dpkg/Bash utilities, `tar`, `sha256sum`, `pkexec`, and a desktop
polkit authentication agent. Use an account authorized to install packages.
A standard Ubuntu Desktop installation provides the expected environment; a
minimal server or customized image may need these desktop prerequisites prepared
before disconnection. The app includes its own Python/Qt and 7-Zip runtime.

Copy these files inward using USB storage or your approved transfer method:

- `Gaebal-ubuntu24.04-amd64.tar.gz`
- `Gaebal-ubuntu24.04-amd64.tar.gz.sha256`
- One or more **VALIDATED** `.7z` package bundles from the online app.
- Each bundle's matching `.7z.sha256` file, if available.

The Gaebal archive is only needed when first setting up or updating the
app; later installations normally need only a new package bundle. Transfer the
matching offline client when changing to a builder release with a new installer
version. Keep the destination disconnected while installing packages.

### 2. Run the offline installation script

Copy the two app files from the transfer medium into `~/Downloads`, then run:

```bash
cd "$HOME/Downloads"
sha256sum -c Gaebal-ubuntu24.04-amd64.tar.gz.sha256
tar -xzf Gaebal-ubuntu24.04-amd64.tar.gz
bash Gaebal/install.sh
```

Continue only if the archive checksum reports `OK`. The script:

- Checks Ubuntu version, architecture, standard prerequisites, and application
  file checksums before replacing an existing installation.
- Copies the complete runtime to `~/.local/share/gaebal`.
- Adds **Gaebal** to the application menu and creates `~/.local/bin/gaebal`.
- Uses only local files: no APT downloads, pip, Docker, or internet access.

**No sudo is needed to install the client.** Administrator authentication is
requested later, only when you install packages through Gaebal. If a standard
Ubuntu desktop prerequisite is missing, the script explains which one to prepare
through your offline provisioning process. It does not try an online fallback.

Run `bash install.sh` even when the transfer medium does not preserve executable
permissions. The installed application must be on an executable local filesystem;
installing into a filesystem mounted with `noexec` will not allow the app to run.
After installation, you can remove the extracted download folder; keep your
package bundles wherever convenient.

### 3. Start Gaebal

Open **Gaebal** from Ubuntu's application menu, or run:

```bash
"$HOME/.local/bin/gaebal"
```

If `~/.local/bin` is already on your PATH, `gaebal` also works. Otherwise the full
command above works immediately. A normal graphical desktop login is required
for the authentication prompt; an SSH-only shell is not sufficient.

### 4. Review and install a package bundle

1. Click **Open .7z Bundle…**, or drag one `.7z` into the window. Do not extract
   the package bundle manually for GUI installation.
2. Wait for **READY**. The app verifies the bundle and previews the package
   transaction. This step does not install packages or request administrator
   authentication.
3. Review the package names, actions, and versions. If upgrades or downgrades
   are listed, select the corresponding approval checkbox only after reviewing
   them. Packages at the requested version show **Already installed**. If all
   requested versions match and no other changes are needed, installation is
   disabled and no administrator authentication is requested. An unpacked
   package that still needs configuration is listed as **configure**.
   Removals and changes to held packages are refused.
4. Click **Install Reviewed Packages…** and approve Ubuntu's authentication
   dialog locally. Cancelling that dialog leaves packages unchanged; reopen the
   bundle to obtain a fresh preview before retrying.
5. Keep the app open until **INSTALLED** appears. Details should end with a
   successful dependency check. Close/Cancel controls cannot stop an active
   installation. Package changes completed before an installation error are
   not automatically rolled back.
6. If the app says the bundle or target state changed, reopen the bundle and
   review the new plan. Do not reuse an older preview after other package changes.

The `.7z` alone is accepted. If a matching-name `.7z.sha256` file is present next
to it, it must match; the app also checks internal hashes. Reopening and installing
the same bundle again succeeds without changes when the requested versions are
already installed. No online repository is used as a fallback.

During version changes, the installer keeps locally edited dpkg configuration
files when a prompt would otherwise be required. It waits up to 30 seconds for
another APT/dpkg transaction to release its lock. Dependency or version conflicts
stop the preview with details and leave packages unchanged; resolve the conflict
or build a compatible bundle, then reopen it to retry. Errors during installation
can leave completed package changes in place, so review Details before retrying.

The 0.4.2 installer changes require bundles rebuilt with Soozip 0.4.2 and the
matching Gaebal client. Older bundles report an installer-version mismatch.

### Update, customize, or remove an app

Close the app after any package installation finishes. To update, extract the
new archive into a separate download folder and rerun its `install.sh`. The
installer verifies and stages the new runtime before replacing the old one;
a failed file publication restores the previous installation. It refuses to
overwrite unrelated folders, commands, or desktop entries.

The default installation prefix is `~/.local`. Advanced users can choose another
absolute path with `--prefix /absolute/path`; command launchers go in `bin/`, and
app files and menu entries go in `share/`. A custom prefix outside the desktop's
application search paths may require launching the command directly.

To uninstall an app installed with the default settings:

```bash
bash "$HOME/.local/share/soozip/install.sh" --uninstall
bash "$HOME/.local/share/gaebal/install.sh" --uninstall
```

For a custom prefix, pass the same `--prefix` when updating or uninstalling.
Uninstallation removes only the managed app, command launcher, and menu entry.
It does not uninstall Docker or Ubuntu packages previously installed with Gaebal.

Portable use is still supported: run `./Soozip/Soozip` or `./Gaebal/Gaebal`
directly from the extracted folder. Keep that folder intact in this mode.

## Project layout

Each client has a separate top-level folder and its own UI module and launcher:

```text
Soozip/
├── Soozip                 # Launch Soozip from the source checkout
├── install.sh             # Installer entry point copied into the release
├── main.py                # Portable application entry point
└── src/soozip/            # Online builder UI
Gaebal/
├── Gaebal                 # Launch Gaebal from the source checkout
├── install.sh             # Installer entry point copied into the release
├── main.py                # Portable application entry point
└── src/gaebal/            # Offline installer UI
src/offline_apt_builder/   # Shared package engine and GUI worker/base controls
packaging/                # Shared app installer, Docker setup, build config
scripts/build_clients.py  # Build both portable clients
dist/Soozip/              # Complete portable Soozip application
dist/Gaebal/              # Complete portable Gaebal application
```

Source clients use the shared engine in this repository. To transfer an app to
another machine, use its complete portable archive from `dist/`, not its source
folder. The source checkout installer reports that a portable build is needed;
build the clients first to create the complete installable payload. The installable Python distribution is `soozip-gaebal`; its GUI commands
are `soozip` and `gaebal`. The shared advanced CLI remains `offline-apt-builder`.

## Developer setup (not required for portable app users)

### Run desktop clients from source

On the online Ubuntu workstation, after the Docker setup below:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[desktop]'
./Soozip/Soozip
./Gaebal/Gaebal
```

Source desktop runs require Python and PySide6 to be installed already. Portable
releases are the supported way to transfer the client to a clean offline desktop.

### Build portable releases

Run on Ubuntu 24.04 amd64 with Python 3.12 and the standard Ubuntu Desktop
libraries. PyInstaller also requires `binutils` and the Qt XCB dependency
`libxcb-cursor0` on the build machine; the latter is included in the portable
clients. Release generation downloads dependencies only on this online machine:

```bash
sudo apt-get install -y binutils libxcb-cursor0
python3 -m venv .release-venv
.release-venv/bin/python -m pip install -r packaging/requirements-release.txt
.release-venv/bin/python scripts/build_clients.py
```

The script verifies the pinned 7-Zip download, freezes two separate folders with
PyInstaller, includes licenses and release metadata, and produces client `.tar.gz`
archives plus SHA-256 files in `dist/`. The release extractor is bundled as `7zz`
from upstream's static `7zzs` binary. No system-wide runtime installation occurs.
These client archives are distinct from the package bundles, which use `.7z`.

### Set up the source/CLI workstation

The following setup is for running the source code or CLI. Portable app users should use the steps above and do not need a system Python environment. Source/CLI users need Ubuntu 24.04 amd64, a **local Linux Docker Engine**, Python 3.12+, and several GiB of space in Docker storage, `/tmp`, and the output directory.

Install Docker Engine using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/), start its daemon, and verify your current user can run:

```bash
docker info
docker run --rm --platform linux/amd64 ubuntu:24.04 cat /etc/os-release
```

Follow [Docker's Linux post-installation instructions](https://docs.docker.com/engine/install/linux-postinstall/) if user access is unavailable. Docker access grants powerful host privileges. The builder does not invoke `sudo` or change Docker permissions automatically.

For `permission denied` at `unix:///var/run/docker.sock`, run these commands
from your normal user's terminal on the online machine:

```bash
sudo groupadd --force docker
sudo usermod -aG docker "$(id -un)"
```

Fully log out of Ubuntu and log back in, or reboot, then run `docker info`
without sudo. Desktop apps keep their original group permissions until the
login session is refreshed. The portable Soozip installer performs this group
setup automatically when Docker access is unavailable.

Soozip uses `http://kr.archive.ubuntu.com/ubuntu/` for Ubuntu `noble` and
`noble-updates` packages, and `http://security.ubuntu.com/ubuntu/` for
`noble-security`. These sources are used in its Docker tooling and package
resolver; the host machine's APT sources are not modified. Ubuntu archive
signatures are verified before downloads.

Install the CLI in a virtual environment on the online workstation:

```bash
sudo apt-get update
sudo apt-get install python3-venv 7zip
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/offline-apt-builder --help
```

Alternatively, run directly from a source checkout without installing the Python package:

```bash
./make-offline-apt.sh --output "$HOME/offline-bundles" \
  git cmake build-essential libssl-dev
```

## Build a bundle

```bash
.venv/bin/offline-apt-builder build \
  --output "$HOME/offline-bundles" \
  git cmake build-essential libssl-dev

.venv/bin/offline-apt-builder build \
  --packages-file examples/packages.txt \
  --output "$HOME/offline-bundles"
```

Package files support whitespace-separated names, blank lines, and `#` comments. Multiple package files and positional arguments can be combined; duplicates are removed in first-seen order. This release accepts package names only, without version selectors, architecture qualifiers, patterns, release selectors, or shell expressions. Exact-name lookup prevents unknown dotted names from becoming APT regular expressions. Ambiguous virtual packages require a concrete provider; an explicitly requested provider can disambiguate a virtual request.

| Option | Behavior |
| --- | --- |
| `--output DIR`, `-o DIR` | Destination; default `./offline-output` |
| `--packages-file FILE` | Read package names; repeatable |
| `--with-recommends` | Include recommendations; default off |
| `--skip-test` | Explicitly produce an **UNTESTED** bundle |
| `--keep-workdir` | Preserve intermediate files; path appears in the result/report |
| `--verbose` | Stream detailed, redacted subprocess logs to stderr |
| `--arch amd64` | Only supported architecture |
| `--json` | One final JSON result on stdout; progress stays on stderr |

```bash
.venv/bin/offline-apt-builder build --json --output ./offline-output jq
```

Successful JSON results contain `status`, `archive`, `checksum`, `sha256`, `package_count`, `size_bytes`, `report`, and optional `workdir`. Build failures return `status: FAILED`, an error, and a report path when available. Exit status is 0 for a completed VALIDATED or explicitly UNTESTED build, 1 for build/input failures, 2 for command-line syntax errors, and 130 for cancellation. Argument-parser errors use stderr.

The builder reports preparation → resolution/download → index verification → offline testing → packaging, including archive integrity and extracted round-trip verification. It checks initial free space, estimates download space before fetching, and checks output capacity before packaging. Docker's separate storage capacity may still cause an actionable Docker failure.

## Transfer and install

A successful build produces:

```text
ubuntu24.04-amd64-offline-<build-id>.7z
ubuntu24.04-amd64-offline-<build-id>.7z.sha256
ubuntu24.04-amd64-offline-<build-id>.report.json
ubuntu24.04-amd64-offline-<build-id>.build.log
```

For desktop installation, transfer the archive inward and open it in the offline app. The following standalone fallback requires an already available `7zz` extractor (the portable client includes `_internal/bin/7zz`). Substitute the actual filename:

```bash
sha256sum -c ubuntu24.04-amd64-offline-<build-id>.7z.sha256
7zz x ubuntu24.04-amd64-offline-<build-id>.7z
cd offline-apt
sudo ./install.sh --dry-run
sudo ./install.sh
```

The installer works from read-only bundles and extraction paths containing spaces or `#`. It verifies checksums, loads a temporary local repository, and simulates the installation before asking for approval. It does not edit permanent APT sources or configuration.

Existing package versions sometimes need to change. The installer prints the old and new versions and asks separately about upgrades and downgrades. For automation, review the dry run and explicitly authorize the required classes of change:

```bash
sudo ./install.sh --yes --allow-upgrades
# Include --allow-downgrades only after reviewing any proposed downgrades.
```

`--yes` alone does not authorize version changes. **Package removals and held-package changes are always refused.** Target APT preferences remain in use; versions with nonpositive pin priority are refused even when requested by exact version. An existing broken package database or conflicting target policy may prevent installation. Resolve those conflicts locally; the installer never relaxes them automatically.

Dry runs update only temporary APT metadata and do not install packages. Successful reruns are safe when the requested versions are already installed. Package maintainer scripts may still require operator input or fail; `--yes` does not add forced conffile or package-repair options. Failure diagnostics remain locally at the printed `/tmp/offline-apt-log.*/install.log` path. Inspect them on the target; do not export them if outbound transfer is prohibited.

## Bundle contents and guarantees

```text
offline-apt/
├── install.sh
├── README-offline.txt
├── packages.txt
├── install-targets.txt
├── architecture.txt
├── with-recommends.txt
├── manifest.json
├── build-info.txt
├── test-report.json
├── SHA256SUMS
├── logs/
├── repository-metadata/
└── repo/
    ├── Packages
    ├── Packages.gz
    ├── Packages.xz
    └── *.deb
```

- Only official Ubuntu `noble`, `noble-updates`, and `noble-security` repositories are used, with main, restricted, universe, and multiverse. Host repository settings are never imported.
- APT resolves against a real empty status database and isolated lists/caches. Dependencies already present in either the workstation or tooling container are still downloaded. APT handles dependencies, pre-dependencies, and alternatives.
- Downloaded package sizes and SHA-256 hashes are checked against authenticated Ubuntu indexes. The generated repository is checked independently against its manifest and compressed indexes.
- Manifest schema version 2 records requested names, selected concrete packages/providers and exact versions, package metadata, repository release files/hashes, image identifiers, tool version, and UTC build time. Metadata ordering is stable; changing upstream repositories means future builds need not be byte-identical.
- The tooling image derives from an immutable repository digest. Validation uses the original, fresh Ubuntu base by its immutable local image ID, without tooling additions or image pulls during testing.
- Installer configuration is loaded through `APT_CONFIG` before system APT configuration fragments. Sources, lists, caches, and logs are temporary; only local `file`, `copy`, and `store` acquisition handlers are available. Target installed state, holds, and preferences remain visible. No unsafe package-manager force flags are added.
- **VALIDATED** means the exact shipped installer succeeded with a read-only bundle and Docker `--network none`, verified requested concrete packages/providers, and passed `apt-get check`. The container's network setting and exit code are checked explicitly. Reports and descriptive status are finalized afterward; the tested installation payload is checked for changes before packaging.
- **UNTESTED** means `--skip-test` was used. **FAILED** builds preserve diagnostics and produce no completed archive. Finished archive/checksum files are published atomically without overwriting existing files; the checksum is published last. The builder completes publication only after both files are written. The desktop importer also accepts the archive alone and checks all internal hashes.

The local repository uses `trusted=yes` after bundle checksum verification. SHA-256 detects corruption; it is not an authenticity guarantee if an attacker can replace both an archive and its checksum. Protect the transfer through your approved process. Manifest signing is deferred.

Clean-container validation is a smoke/integration test, not proof of compatibility with an unknown target's held or vendor packages, policy, kernel, hardware, or installed versions. Kernel/DKMS/NVIDIA/CUDA and maintainer scripts requiring devices, systemd, or internet are outside guaranteed container validation. Packages can have network-using maintainer scripts: local-only APT acquisition does not sandbox every process those scripts execute. The target must remain air-gapped; full networking is disabled during the container test.

There is no rollback of package maintainer scripts or completed package changes after installation begins. A failure can leave packages unpacked or partially configured. Inspect diagnostics locally and resolve the cause using approved packages; do not enable online sources as an automatic fallback.

## Tests and release acceptance

Core tests require Python 3.12+ and a local `7zz` or `7z`. Install the desktop extra to include real Qt event-loop and UI-state tests:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src:Soozip/src:Gaebal/src .venv/bin/python -m unittest discover -s tests -v
bash -n make-offline-apt.sh src/offline_apt_builder/resources/install.sh tests/test_end_to_end.sh
```

Docker integration and acceptance tests are skipped unless explicitly enabled. On **online Ubuntu 24.04 amd64 with working Docker**, run:

```bash
# Real, disposable .deb fixtures: installer safety, ordering, and failure paths.
OFFLINE_APT_INTEGRATION=1 PYTHONPATH=src:Soozip/src:Gaebal/src \
  python3 -m unittest discover -s tests -p test_integration.py -v

# Complex 11-package graph with the Qt UI and the built portable Gaebal worker.
# Requires dist/Gaebal to match the current source version and installer.
OFFLINE_APT_COMPLEX=1 PYTHONPATH=src:Soozip/src:Gaebal/src \
  .venv/bin/python tests/test_complex_bundle.py -v

# Full acceptance: additionally download/build/test jq and the development set.
./tests/test_end_to_end.sh
```

The complex-bundle test records real maintainer-script ordering, simultaneous
upgrades and downgrades, a matching-version rerun, incompatible installed
dependencies, holds, pinning, missing approvals, and stale approvals. It checks
that rejected operations leave dpkg state unchanged and that the Qt event loop
remains responsive and can retry. The real worker runs inside disposable
containers with `--network none`; the test redirects worker launch from pkexec
to Docker and does not test interactive desktop authentication. Its validated
test bundle, transaction logs, screenshots, and UI-state records are retained
in the printed `build/gaebal-complex-*` directory.

The full suite creates real packages, checks path/read-only handling, optional recommendations, virtual-provider installation, pre-dependencies/alternatives, dry runs, idempotence, pinning, holds, removals, checksum corruption, wrong platforms, missing dependencies, failed maintainer scripts, and source/hook isolation. Acceptance bundles and reports remain in `offline-output/acceptance`, configurable through `OFFLINE_APT_TEST_OUTPUT`. The real builds assert that `libc6`, already present in the reference image, was nevertheless bundled. They also resolve and retrieve each bundle's full dependency set using an empty installed-package database and only its local repository in a network-disabled container. The `jq` check follows a multi-level dependency chain and must fail when `libc6` is removed from the repository index, even though the container has it installed. This checks dependency completeness separately from installing on the minimal Ubuntu base.

After building portable clients, also run:

```bash
OFFLINE_APT_ACCEPTANCE=1 OFFLINE_APT_PORTABLE=1 PYTHONPATH=src:Soozip/src:Gaebal/src \
  .venv/bin/python -m unittest discover -s tests -p test_integration.py -v
```

On a fresh Ubuntu Desktop VM, test the packaged apps under both Wayland and X11:
launch the online app with Docker, build a `jq` bundle, transfer the offline folder
and archive, disable all VM network interfaces, and open the archive. Check preview,
polkit authentication success/cancellation, installation, and successful rerun.
Confirm the offline VM has no separately installed Python packages, Docker, or
7-Zip. Also test paths containing spaces, checksum failure, and a stale preview
caused by changing target package state before installation.

### Validation status for this release

Version 0.4.2 displays the runtime version in both clients, recovers controls after
worker and workspace failures, and explicitly previews packages that are already
installed or still need configuration. Version changes, including changes to
partially installed packages, require approval. Configuration-file prompts are
handled without a terminal while preserving local edits.

Local Qt and shell regression tests and real Docker package-transaction tests
passed, including repeat installs, upgrades, downgrades, partially installed
packages, held-package protection, and modified configuration files. Both version
labels and the already-installed UI were checked with offscreen Qt rendering.
Fresh-desktop, Wayland/X11, and interactive polkit acceptance checks have not been
repeated for 0.4.2.

**Rebuild package bundles with Soozip 0.4.2 and transfer the matching Gaebal
0.4.2 client.** The installer embedded in each bundle has changed; updating
Gaebal alone does not update old bundles. Installer bytes must match the client
and the bundle's checksum and validation evidence. Do not edit an existing
bundle's `install.sh` or checksums to bypass the version check.

The following results describe earlier releases:

Version 0.4.1 makes Soozip's Docker setup the default (with
`--skip-docker-setup` available), selects the Korean Ubuntu archive mirror,
and fixes a Gaebal exit-141 failure after reading local package lists. The
policy reader now consumes all APT output before returning a priority, so
large policy output cannot terminate `apt-cache` with SIGPIPE. Unexpected
shell failures also report their stage and line number.

All 64 tests passed across the 0.4.1 local, Docker integration, and acceptance
runs, including the exit-141 regression. Real Korean-mirror builds of `jq`
(6 packages) and the development tools (121 packages) passed offline
installation and dependency-closure checks. The rebuilt Gaebal worker also
passed import, preview, and installation in a network-disabled container
without separately installed Python, 7-Zip, or Docker commands.

The following acceptance results describe the earlier 0.4.0 baseline:

On Ubuntu 24.04 amd64, the core/Qt tests, real Docker installer fixtures, real
package builds (`jq`: 6 packages; development tools: 121 packages), empty-status
recursive dependency checks, and portable worker installation without separate
Python/7-Zip/Docker commands passed. Packaged GUI checks passed on the development
VM, including native file selection, drag/drop with spaces and `#`, authentication
cancel/approve, stale-preview rejection and recovery, close protection during a
real installation, and normal closing. Startup was checked under Wayland and an
actual Ubuntu on Xorg login.

A recorded disconnected interval on this VM covered authentication cancellation,
hello installation, and a no-change rerun. Networking was restored after those
operations but before closing the GUI, so the recorder's stricter whole-session
offline criterion did not pass. The successful operation interval was reviewed
separately. Clean Ubuntu Desktop portability has not been independently tested;
these results are not a guarantee for every customized destination image.

## Troubleshooting and cleanup

| Failure | Action on the online builder or locally on the target |
| --- | --- |
| GUI will not launch / permission denied | Run the executable inside the complete extracted folder from a local executable filesystem, as a normal desktop user; launch from a terminal to read errors |
| No administrator authentication dialog | Use a local Ubuntu desktop session with `pkexec` and a running polkit agent; prepare missing standard desktop prerequisites through your offline provisioning process |
| Target state changed after preview | Reopen the bundle, review the current package plan, and retry |
| Unsupported builder | Use an Ubuntu 24.04 amd64 workstation; help/unit tests can run on macOS |
| Docker unavailable | Check installation, daemon state, current user access, and the active local Docker context |
| Docker socket permission denied | Rerun Soozip's installer to configure Docker access, fully log out and back in or reboot, then verify `docker info` without sudo before opening Soozip |
| Gaebal exit 141 after reading local package lists | Rebuild the bundle with Soozip 0.4.2 and use matching Gaebal 0.4.2; an old installer could close its APT policy pipe early. This is separate from checksum corruption |
| Package unavailable/ambiguous | Check the exact Ubuntu package name or specify a concrete provider; third-party repositories are not supported |
| Network or mirror failure | Check online connectivity/proxy configuration and rebuild; partial results are not published |
| Insufficient disk | Free space in `/tmp`, Docker storage, and the output filesystem, then rebuild |
| Offline test failed | Read the saved report, validation log, and build log; unsupported maintainer scripts may need VM validation |
| Checksum failure | Transfer a fresh archive/checksum pair through the approved process |
| Target version/policy conflict | Review the dry run, holds, and pinning locally; bring compatible packages inward if required |

Logs redact common URL credentials and secret assignments; do not pass secrets in package names or assume arbitrary program output can be perfectly sanitized. Online diagnostics remain in the selected output directory. `--keep-workdir` retains the printed workspace. Cancellation removes named containers and temporary state; if Docker becomes unavailable during cleanup, stop the named containers when it returns and remove only the specific workspace printed in the log. No broad Docker prune or host package changes are performed.
