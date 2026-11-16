# Ubuntu 24.04 Offline APT Builder

Build a portable local APT repository on an **online Ubuntu 24.04 amd64 workstation**, test the shipped installer in a fresh container with **networking disabled**, and transfer one `.7z` archive (with an optional checksum sidecar) into an air-gapped Ubuntu 24.04 amd64 machine.

The target needs standard Ubuntu APT/dpkg/Bash tools and sufficient disk space. It does not need Docker, Python additions, GitHub access, or an internet connection. Nothing needs to leave the air-gapped network. The application never uploads bundles, reports, or logs.

Two native PySide6 desktop clients and the Python CLI share the build and installation engines. macOS, arm64, cross builds, PPAs, and vendor repositories are unsupported.

## Desktop clients

Both clients target **Ubuntu 24.04 amd64 with an existing graphical desktop**.
Launch them as your normal desktop user. The offline app requests administrator
authentication through the desktop's polkit agent when you click Install.

### Portable releases

Extract each client release with Ubuntu's standard archive tools:

```bash
tar -xzf offline-apt-online-ubuntu24.04-amd64.tar.gz
./offline-apt-online/offline-apt-online

# Transfer this reusable client folder to the offline machine once:
tar -xzf offline-apt-offline-ubuntu24.04-amd64.tar.gz
./offline-apt-offline/offline-apt-offline
```

Each folder includes its Python/Qt runtime, worker, and a pinned standalone 7-Zip
extractor. Keep the entire folder together. The offline machine needs no pip
installation, Docker, or system 7-Zip. It does need standard Ubuntu APT/dpkg/Bash,
`pkexec`, a desktop authentication agent, and sufficient local disk space. Copy
clients from removable media to a local executable filesystem before launching.

**Online:** enter package names or import text files, select an output folder,
optionally include recommendations, then click **Build and Validate Bundle**.
The app resolves and downloads packages, writes installation scripts and metadata,
validates installation with Docker networking disabled, and publishes a `.7z`.
Progress and detailed logs remain visible. Failed validation never publishes a
completed bundle. The desktop app has no skip-test option.

**Offline:** open or drop the `.7z`. The client verifies archive structure,
extracts into private temporary storage, checks all internal SHA-256 hashes,
validates the manifest and test evidence, and previews the local APT transaction
without administrator privileges. Review the package table, explicitly approve
any upgrades/downgrades, then click **Install Reviewed Packages**. The desktop
authentication dialog authorizes a separate worker. The worker rechecks a private
copy and the reviewed transaction before installing. Changed package state or APT
policy requires a new preview. Install may show a second transaction check in Details.

The `.7z` alone is sufficient. If `<archive>.sha256` is next to it, that sidecar
must match. The desktop importer accepts schema 2, VALIDATED bundles generated
with the same installer version as the client. Transfer the matching offline
client when upgrading the builder. UNTESTED bundles, old `.tar.gz` bundles,
password-protected archives, multipart archives, links, and unsafe paths are
refused. Older bundles remain usable through their own standalone installer.

Cancel is available during building and preview. After starting the privileged
installation workflow, close/cancel controls stay disabled until it finishes;
the system authentication dialog can still be dismissed before authorization.
A failed installation retains local diagnostics and may leave partial package
changes. Open the bundle again for a fresh preview after any failure or rerun.

### Run desktop clients from source

On the online Ubuntu workstation, after the Docker setup below:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[desktop]'
./offline-apt-online
./offline-apt-offline
```

Source desktop runs require Python and PySide6 to be installed already. Portable
releases are the supported way to transfer the client to a clean offline desktop.

### Build portable releases

Run on Ubuntu 24.04 amd64 with Python 3.12 and the standard Ubuntu Desktop
libraries. Release generation downloads dependencies only on this online machine:

```bash
python3 -m venv .release-venv
.release-venv/bin/python -m pip install -r packaging/requirements-release.txt
.release-venv/bin/python scripts/build_clients.py
```

The script verifies the pinned 7-Zip download, freezes two separate folders with
PyInstaller, includes licenses and release metadata, and produces client `.tar.gz`
archives plus SHA-256 files in `dist/`. The release extractor is bundled as `7zz`
from upstream's static `7zzs` binary. No system-wide runtime installation occurs.
These client archives are distinct from the package bundles, which use `.7z`.

## Set up the online workstation

Use Ubuntu 24.04 amd64 with a **local Linux Docker Engine**, Python 3.12+, and several GiB of available space in Docker storage, `/tmp`, and your output directory. Real space requirements depend on your packages.

Install Docker Engine using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/), start its daemon, and verify your current user can run:

```bash
docker info
docker run --rm --platform linux/amd64 ubuntu:24.04 cat /etc/os-release
```

Follow [Docker's Linux post-installation instructions](https://docs.docker.com/engine/install/linux-postinstall/) if user access is unavailable. Docker access grants powerful host privileges. The builder does not invoke `sudo` or change Docker permissions automatically.

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
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
bash -n make-offline-apt.sh src/offline_apt_builder/resources/install.sh tests/test_end_to_end.sh
```

Docker integration and acceptance tests are skipped unless explicitly enabled. On **online Ubuntu 24.04 amd64 with working Docker**, run:

```bash
# Real, disposable .deb fixtures: installer safety, ordering, and failure paths.
OFFLINE_APT_INTEGRATION=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -p test_integration.py -v

# Full acceptance: additionally download/build/test jq and the development set.
./tests/test_end_to_end.sh
```

The full suite creates real packages, checks path/read-only handling, optional recommendations, virtual-provider installation, pre-dependencies/alternatives, dry runs, idempotence, pinning, holds, removals, checksum corruption, wrong platforms, missing dependencies, failed maintainer scripts, and source/hook isolation. Acceptance bundles and reports remain in `offline-output/acceptance`, configurable through `OFFLINE_APT_TEST_OUTPUT`. The real builds assert that `libc6`, already present in the reference image, was nevertheless bundled.

After building portable clients, also run:

```bash
OFFLINE_APT_ACCEPTANCE=1 OFFLINE_APT_PORTABLE=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p test_integration.py -v
```

On a fresh Ubuntu Desktop VM, test the packaged apps under both Wayland and X11:
launch the online app with Docker, build a `jq` bundle, transfer the offline folder
and archive, disable all VM network interfaces, and open the archive. Check preview,
polkit authentication success/cancellation, installation, and successful rerun.
Confirm the offline VM has no separately installed Python packages, Docker, or
7-Zip. Also test paths containing spaces, checksum failure, and a stale preview
caused by changing target package state before installation.

**Release acceptance remains pending until those real Ubuntu tests pass.** Development on macOS and simulated Docker unit tests do not establish offline installation success.

## Troubleshooting and cleanup

| Failure | Action on the online builder or locally on the target |
| --- | --- |
| Unsupported builder | Use an Ubuntu 24.04 amd64 workstation; help/unit tests can run on macOS |
| Docker unavailable | Check installation, daemon state, current user access, and the active local Docker context |
| Package unavailable/ambiguous | Check the exact Ubuntu package name or specify a concrete provider; third-party repositories are not supported |
| Network or mirror failure | Check online connectivity/proxy configuration and rebuild; partial results are not published |
| Insufficient disk | Free space in `/tmp`, Docker storage, and the output filesystem, then rebuild |
| Offline test failed | Read the saved report, validation log, and build log; unsupported maintainer scripts may need VM validation |
| Checksum failure | Transfer a fresh archive/checksum pair through the approved process |
| Target version/policy conflict | Review the dry run, holds, and pinning locally; bring compatible packages inward if required |

Logs redact common URL credentials and secret assignments; do not pass secrets in package names or assume arbitrary program output can be perfectly sanitized. Online diagnostics remain in the selected output directory. `--keep-workdir` retains the printed workspace. Cancellation removes named containers and temporary state; if Docker becomes unavailable during cleanup, stop the named containers when it returns and remove only the specific workspace printed in the log. No broad Docker prune or host package changes are performed.
