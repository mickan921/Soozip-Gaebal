# Soozip & Gaebal

**Soozip** builds a portable local APT repository or Python wheel bundle on an **online Ubuntu 24.04 amd64 workstation**, tests the shipped installer in a fresh container with **networking disabled**, and produces one `.7z` archive (with an optional checksum sidecar). Transfer it into an air-gapped Ubuntu 24.04 amd64 machine and install it with **Gaebal**. Select APT or pip in Soozip; Gaebal detects the bundle type.

For APT bundles, the target needs standard Ubuntu APT/dpkg/Bash tools and sufficient disk space. It does not need Docker, Python additions, GitHub access, or an internet connection. Nothing needs to leave the air-gapped network. The application never uploads bundles, reports, or logs.

For **pip bundles**, the destination additionally needs the selected standard
CPython version (3.11–3.14) and its `venv` module prepared before disconnection.
Gaebal installs Python packages as your normal user into an isolated virtualenv.
Pip itself does not need to be installed: each bundle carries pinned offline
installer tooling. The portable client's embedded Python is separate from the
Python interpreter used for your packages.

The online builder assumes none of the requested software or its dependencies
are installed on the destination. It bundles required dependencies recursively,
including dependencies of dependencies and pre-dependencies, even when they are
already installed on the online machine. For example, a `jq` bundle includes
`libjq1`, `libonig5`, `libc6`, and their required dependencies. Optional
`Recommends` are included when selected; `Suggests` are excluded. A minimal
installed Ubuntu system is still required; the bundle is not an OS installer.

APT bundles also include **matching compatibility packages by default**. A
library update may require matching updates for software already on the offline
machine: for example, SDL development libraries can require updating the
systemd/udev family or Mesa drivers together. Soozip follows exact-version
dependencies between binaries from the same Ubuntu source-package version,
resolves each companion's dependencies independently, and repeats this until
the repository is complete. These extra packages can make archives substantially
larger; their additional download size appears during the build. They are
repository options, not extra installation targets. Gaebal installs only what
APT needs for the reviewed transaction, with the usual version-change approvals.
No machine inventory needs to leave the offline network.

The installer also preserves installed dependency providers automatically. If an
initial APT plan would remove a package, it retries while requiring that package
to stay installed. For example, a desktop with JACK2 can install the SDL development
packages using the bundled JACK2 development provider in the same transaction.
No preparation bundle is needed. The final plan must still contain zero removals;
holds, pinning, and version-change approvals remain enforced.

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

If APT proposes removing packages, Gaebal displays the count and first ten names
in a selectable summary, with the complete list in Details. The Install button
stays disabled. Rebuild older bundles with current Soozip to include compatibility
packages. Do not disable removal protection or manually uninstall desktop
packages to make the bundle fit. Genuine package conflicts, holds, or unsupported
vendor versions may still need local diagnosis.

Gaebal 0.5.3 accepts current APT bundles and the exact known installer shipped in
0.4.2–0.5.1 bundles. It verifies the original archive, checksums and validation
evidence without rewriting them, then uses its own updated installer for the
fresh local preview and installation. Updating Gaebal alone therefore fixes the
JACK2 selection problem with the existing 0.5.1 SDL bundle. Soozip 0.5.2 embeds
the updated installer in newly built bundles. Unknown or modified installers
remain rejected. Pip bundles retain their separate matching-installer checks.

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

## Build and install Python packages (pip)

### Online: Soozip

1. Select **Python packages (pip)** and the destination's CPython minor version:
   **3.11, 3.12, 3.13, or 3.14**. The default is 3.12. Each bundle targets one version.
2. Enter one PyPI requirement per line or use **Import requirements.txt…**:

   ```text
   requests>=2.32
   numpy
   httpx[http2]
   typing-extensions; python_version < "3.13"
   ```

   Blank lines and `#` comments are supported. Requirements may include version
   constraints, extras, and environment markers. Pip options (`-r`, `-c`,
   `--index-url`, `--hash`, etc.), URLs, editable installs, Git dependencies, and
   local projects are rejected with line-specific errors. This is a requirements
   specifier import, not support for every directive in pip's file format.
3. Choose the output folder and click **Build and Validate Bundle**. Soozip
   resolves the complete dependency closure from PyPI using the selected Python
   inside Ubuntu 24.04 Docker containers. Host-installed packages are not used.
4. Wait for **VALIDATED**, then transfer the `.7z` and matching checksum inward.

Every package and dependency must have a compatible **prebuilt wheel**. Source
builds and private indexes are unsupported. The online builder needs access to
PyPI, its file host, Docker Hub, and GitHub's python-build-standalone releases.
Python runtime and installer tooling downloads are checksum-pinned. Container
runtime identities and Python patch versions are recorded in the manifest/report.
The downloaded Python runtime stays on the builder; it is not provisioned on the
offline destination. Frozen installer tooling wheels include their licenses.

### Offline: Gaebal

1. Open/drop the `.7z`. After integrity verification, Gaebal shows the required
   Python version and environment controls.
2. Choose **Create virtualenv**, select an installed interpreter, and choose a
   new directory. Compatible interpreters on PATH are listed; **Python
   executable…** can select another installation. The default environment is
   `~/.virtualenvs/gaebal-<build-id>`, separate from the portable client.
   Alternatively choose **Use existing virtualenv** and select its directory;
   its own interpreter is used.
3. Click **Review Python Package Changes**. Preview does not create the virtualenv
   or change installed packages. Review all dependencies as well as requested
   packages, and separately approve listed upgrades/downgrades.
4. Click **Install Reviewed Packages…**. Pip installations do not request sudo or
   polkit authentication. Keep the application open until installation finishes.
5. Use the environment path and activation command shown on success. An existing
   environment already matching every bundled version needs no installation.

The environment must be writable and isolated. System Python, Conda, PyPy,
debug/free-threaded builds, and virtualenvs with system site-packages are
unsupported. Missing Python or `venv` support must be provisioned separately;
Gaebal does not download them. Wheel tags, Python requirements, and environment
markers are checked against the actual interpreter. Patch-version-specific
marker differences can require rebuilding a compatible bundle.

Installs use only local wheels with exact version pins and SHA-256 hashes.
External indexes, inherited pip configuration, pip caches, and version checks
are disabled. Conflicts with existing packages block preview, and unrelated
packages are preserved. Any change to the bundle, environment selection,
interpreter, or installed metadata requires a fresh preview. Use one package
manager at a time; Gaebal serializes its own jobs for each environment.

Existing-environment changes are not rolled back after a failure. A failed new
installation may leave its newly created environment in place; inspect the local
diagnostic log, then select it as an existing environment to review/retry, or
choose a new directory. Environment directories are never moved or overwritten.
If creation stopped before `bin/python` was written (for example, because the
destination filled up), choose a new empty destination after freeing space.
No installation modifies the system Python environment.

**VALIDATED** confirms installation in a clean, unprivileged virtualenv with
Docker networking disabled, exact installed versions, and a successful `pip
check`. It does not guarantee every application-level behavior. Native system
libraries, GPU drivers, and other non-Python runtime dependencies are not bundled;
provision them separately, using APT bundles where appropriate.

### CLI and bundle format

```bash
.venv/bin/offline-apt-builder build-pip --python-version 3.12 \
  --output "$HOME/offline-bundles" 'requests>=2.32' numpy

.venv/bin/offline-apt-builder build-pip --python-version 3.14 \
  --requirements-file requirements.txt --json
```

`--requirements-file` is repeatable. Pip builds support `--verbose` and
`--keep-workdir`; they always validate and have no `--skip-test` option.
The result JSON and exit codes match the APT command.

Pip archives are named `ubuntu24.04-amd64-pip-py<version>-<build-id>.7z` and
contain one `offline-pip/` directory with `manifest.json` (pip schema 1),
`requirements.txt`, `requirements.lock`, `wheelhouse/`, `tooling/`,
`install-pip.py`, `SHA256SUMS`, `test-report.json`, and logs. APT archives retain
their `offline-apt/` layout and schema 2. Mixed roots are rejected.

For development, run the unit/Qt tests normally. Real virtualenv regression
tests use `OFFLINE_PIP_TOOLING=/path/to/validated/bundle/tooling`. Set
`OFFLINE_PIP_ACCEPTANCE=1` to run the four-version PyPI/Docker matrix; add
`OFFLINE_PIP_PORTABLE=1` after building clients to test the frozen Gaebal worker.
The matrix covers `requests`, native NumPy wheels, HTTPX extras and its CLI,
Python-version markers, restrictive umasks (022/027/077), repeat installs, and
disk-full recovery with retained diagnostics. To rerun one version, set
`OFFLINE_PIP_VERSIONS=3.12` (comma-separated selections are supported).

Run the remaining failure and packaged desktop checks explicitly:

```bash
PYTHONPATH=src:tests OFFLINE_PIP_FAILURES=1 \
  .venv/bin/python -m unittest discover -s tests -p test_pip_failure_integration.py -v
PYTHONPATH=src:tests OFFLINE_PIP_DESKTOP=1 \
  .venv/bin/python -m unittest discover -s tests -p test_pip_packaged_desktop.py -v
```

Failure checks exercise real resolver conflicts/missing wheels, a truncated HTTP
response, and cancellation of a running resolver container. The desktop test
requires built client archives, a local Docker socket and Buildx; it installs the
portable apps into fresh Ubuntu 24.04 X11 containers, builds through Soozip, then
installs through Gaebal with `--network none`. It checks interpreter selection,
preview, install protection, console scripts in paths with spaces, and an
existing-environment no-op. Screenshots and logs are retained under `build/`.
This checks the packaged desktop applications, but does not replace testing on a
physical desktop or VM with the intended graphics/session setup.

For repeated acceptance/failure runs, `OFFLINE_PIP_CACHE_REPORTS` may name a
directory of successful build reports retained with `--keep-workdir`. Tests
reuse runtime/tooling files only after verifying their pinned checksums; PyPI
package resolution remains live. Production builds always fetch their pinned
runtime/tooling. Payload permissions are normalized for the unprivileged offline
validator without changing the builder's umask or private workspace permissions.

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
- Compatibility expansion follows alternative providers in the requested dependency graph, then same-source exact `Depends`/`Pre-Depends` companions. Companions are resolved separately, so conflicting alternatives can coexist in the repository without being installed together. Conflicting versions of the same binary package fail the build.
- Downloaded package sizes and SHA-256 hashes are checked against authenticated Ubuntu indexes. The generated repository is checked independently against its manifest and compressed indexes.
- Manifest schema version 2 records requested names, selected concrete packages/providers and exact versions, package metadata, repository release files/hashes, image identifiers, tool version, and UTC build time. Metadata ordering is stable; changing upstream repositories means future builds need not be byte-identical.
- New APT manifests optionally record each binary's source name/version and inclusion reasons, plus the compatibility package count and bytes. Older schema-2 manifests remain supported.
- The tooling image derives from an immutable repository digest. Installation tests use the fresh Ubuntu base and prepared reference environments by immutable local image IDs, without image pulls during testing.
- Installer configuration is loaded through `APT_CONFIG` before system APT configuration fragments. Sources, lists, caches, and logs are temporary; only local `file`, `copy`, and `store` acquisition handlers are available. Target installed state, holds, and preferences remain visible. No unsafe package-manager force flags are added.
- **VALIDATED** requires fresh Ubuntu installation, an older systemd/Mesa/ALSA/Xorg installation, original-release and current desktop dependency simulations, and installation into discovered provider states (including runtime-only states). Each runs the shipped installer against a read-only bundle with Docker `--network none`. Real installations verify requested versions, unchanged previews, no removals, automatic/manual flags, `apt-get check`, `dpkg --audit`, and unchanged repeat installation. Required failures or unavailable references block packaging. Reports distinguish actual installs from dependency simulations and record reference image/definition hashes. The tested payload is frozen before validation and checked again before packaging.
- **UNTESTED** means `--skip-test` was used. **FAILED** builds preserve diagnostics and produce no completed archive. Finished archive/checksum files are published atomically without overwriting existing files; the checksum is published last. The builder completes publication only after both files are written. The desktop importer also accepts the archive alone and checks all internal hashes.

The local repository uses `trusted=yes` after bundle checksum verification. SHA-256 detects corruption; it is not an authenticity guarantee if an attacker can replace both an archive and its checksum. Protect the transfer through your approved process. Manifest signing is deferred.

Compatibility validation tests representative package states, not every possible combination. It does not prove compatibility with an unknown target's held or vendor packages, policy, kernel, hardware, or installed versions. Kernel/DKMS/NVIDIA/CUDA and maintainer scripts requiring devices, systemd, or internet are outside guaranteed container validation. Packages can have network-using maintainer scripts: local-only APT acquisition does not sandbox every process those scripts execute. The target must remain air-gapped; full networking is disabled during the container test.

There is no rollback of package maintainer scripts or completed package changes after installation begins. A failure can leave packages unpacked or partially configured. Inspect diagnostics locally and resolve the cause using approved packages; do not enable online sources as an automatic fallback.

### Validation environments and cleanup

Tests run inside Docker on the online computer. Reference package states come from authenticated Ubuntu metadata; neither computer's installed inventory is imported. Desktop models exercise APT dependencies without claiming to boot a desktop. Provider families are tested independently, not in every possible combination. Gaebal still previews the actual offline destination.

Temporary test containers are removed on completion, handled failure and cancellation. Build workspaces are removed unless `--keep-workdir` is explicitly set. Finished bundles, logs and reports remain. Ubuntu base images and prepared older-core images are cached; the latter use `soozip-validation-core` tags keyed by the base image, profile and authenticated index hashes. Cached images consume disk space and can be removed through Docker when no build is running. Abrupt power loss or forced termination can leave temporary resources requiring manual cleanup.

Schema-2 bundles declare the optional `ubuntu-desktop-providers-v1` validation policy, with a checksummed `validation-plan.json`, desktop reference states and per-scenario evidence in `test-report.json`. Gaebal verifies all declared scenarios and displays coverage. Known older bundles remain supported with their legacy clean-container coverage labeled explicitly. Pip validation is unchanged.

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

# SDL acceptance with a verified, unpacked four-package SDL bundle. Prepares
# an older systemd/Mesa installation online, then tests with networking disabled.
OFFLINE_APT_SDL=1 OFFLINE_APT_SDL_BUNDLE=/absolute/path/to/offline-apt \
  PYTHONPATH=src:Soozip/src:Gaebal/src \
  .venv/bin/python -m unittest discover -s tests -p test_sdl_acceptance.py -v

# Packaged Gaebal automatically keeps an existing JACK2 runtime, using the
# original 0.5.1 SDL bundle without a preparation bundle or archive modification.
OFFLINE_APT_JACK2_AUTO=1 OFFLINE_APT_SDL_BUNDLE=/absolute/path/to/offline-apt \
  PYTHONPATH=src:Soozip/src:Gaebal/src \
  .venv/bin/python -m unittest discover -s tests -p test_jack2_automatic.py -v

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

Version 0.5.3 makes compatibility validation mandatory in ordinary Soozip APT builds. New bundles include relevant alternative providers and record the required installation and desktop dependency scenarios. Gaebal displays this coverage while preserving the 0.5.2 provider-retention behavior and support for known legacy bundles. The SDL and JACK2 preparation downloads have been retired; existing local SDL copies remain usable.

The APT release gate below requires the completed current-policy SDL matrix and current portable clients. It fails on missing matrix evidence, failing tests, or skipped required suites:

```bash
python scripts/check_apt_release.py --sdl-bundle /path/to/current/offline-apt \
  --legacy-sdl-bundle /path/to/original/offline-apt \
  --smoke-archive /path/to/current-small-bundle.7z --report build/apt-release-check.json
```

The following paragraphs record earlier release behavior and validation:

Version 0.5.2 retries removal-blocked APT plans while retaining installed
packages, including the JACK2 runtime. Compatible development dependencies are
installed as part of the original reviewed transaction. The same constraints
are used for preview and installation, and approval tokens bind the installer
and selected constraints. Existing automatic/manual flags are preserved for
retained dependencies. Genuine conflicts, missing alternatives, held-package
changes and forbidden APT pins still block without changing package state.

Both portable clients must be updated for new 0.5.2 bundles. For the existing
0.5.1 SDL bundle, updating Gaebal is sufficient: its known legacy installer is
recognized by exact hash, and the current trusted client installer is used.
The original bundle and its validation evidence stay unchanged. The standalone
installer inside an old archive retains its old behavior; use updated Gaebal
to obtain automatic provider retention with that archive.

Version 0.5.1 adds default APT compatibility packages and concise removal
diagnostics. The standalone APT installer is unchanged. Compatibility enrichment
does not turn clean-container validation into a guarantee for every customized
desktop; Gaebal still performs a fresh local preview before any installation.

The four-package SDL bundle passed a fresh offline installation and an offline
upgrade from systemd `255.4-1ubuntu8` and Mesa `24.0.5-1ubuntu1`, with zero
removals, a successful `apt-get check`, and an unchanged package database on
repeat installation. A separate synthetic desktop reference containing GDM,
Firefox, ALSA, and Xorg also previewed without removals. Neither test used an
inventory from the offline destination. Unit, real APT integration, packaged
Qt/worker, held-package, rejected-preview, and legacy 0.4.2 bundle checks passed.
Fresh-desktop Wayland/X11 and interactive polkit checks were not repeated.

Rebuild old bundles with Soozip 0.5.1 to obtain compatibility packages, and use
Gaebal 0.5.1 for the improved removal diagnostics. Updating the client alone does
not add packages to an existing archive.

The following results describe earlier releases:

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

Version 0.4.2 changed the embedded installer; version 0.5.1 retains those APT
installer bytes. Gaebal 0.5.2 explicitly recognizes that known legacy installer
as described above. Do not edit an existing bundle's `install.sh` or checksums
to bypass the version check.

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
