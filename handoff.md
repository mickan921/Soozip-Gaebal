# Soozip-Gaebal — Ubuntu testing handoff

Updated: 2026-11-16 (Asia/Seoul).

## Start here

This project implements two portable desktop clients for Ubuntu 24.04 amd64:

- **Online builder:** accepts Ubuntu package names or package-list text files, downloads the complete dependency set, generates installation scripts, verifies installation in a fresh Docker container with networking disabled, and publishes a `.7z` package bundle.
- **Offline installer:** opens the `.7z`, verifies and extracts it, previews package changes without root, then installs after review and administrator authentication through `pkexec`.

The implementation is present. **The next task is to test it on real Ubuntu, fix failures, and establish release acceptance. Do not treat the existing macOS test results as proof that Linux builds or offline installation work.**

Repository: <https://github.com/mickan921/Soozip-Gaebal>

- Branch: `main`.
- Implementation baseline: `a8b0f6064e4ef79387c6cb3f829baa95624a7e82` (`Add online and offline Ubuntu package desktop clients`). This document is a later documentation commit; clone the latest `main`.
- Python project version: `0.2.0`.
- Original development folder was renamed to `/Users/mickan/Developer/Soozip-Gaebal`. That macOS path is historical; use the new checkout's path on Ubuntu.
- The package/module names remain `offline-apt-builder` / `offline_apt_builder`. The folder/repository rename did not change command names.

Suggested first prompt for the next Codex session:

> Read handoff.md and README.md, inspect the current checkout and Ubuntu environment, then continue Ubuntu validation of the two desktop clients. Run the core and Qt tests, real Docker installer tests, real package builds, portable release build, and portable-worker acceptance in that order. Fix failures without weakening validation or package-change protections. Test graphical authentication and disconnected installation on a disposable clean desktop VM when available. Record commands, results, remaining limitations, and any changes in handoff.md. Do not label the release validated until the actual Ubuntu tests pass.

## Current evidence and remaining work

The last local run on macOS used Python 3.14.6, PySide6 6.10.1, and the available system `7z`:

- `51` tests discovered: **33 passed, 18 skipped**.
- The skipped tests were the opt-in Ubuntu/Docker integration and acceptance tests.
- Shell entrypoints and the static shell snippets in integration tests passed syntax checks.
- Both Qt windows were rendered and visually inspected using Qt's offscreen platform.
- Real local `.7z` round trips, checksum checks, corruption, encrypted archives, and symlink rejection were exercised.
- The official Linux 7-Zip 25.01 archive was downloaded and its SHA-256 recorded in the release script. Its Linux executable was not run on macOS.

**Not yet verified:** actual Ubuntu package downloads/resolution, APT installs, unprivileged APT previews, Linux frozen executables, their runtime dependencies, `pkexec` authentication, Wayland/X11 interaction, and installation on a disconnected Ubuntu Desktop VM. No portable Linux release binaries have been produced or uploaded. There is no configured GitHub Actions workflow.

## Environment and checkout

Use **Ubuntu Desktop 24.04 amd64**, with a local Linux Docker Engine and a regular desktop user who can use sudo. The builder checks the guest OS and architecture: `uname -m` must report `x86_64`, and `dpkg --print-architecture` must report `amd64`. An arm64 guest will be rejected even if Docker can emulate amd64 containers.

A practical starting VM allocation is 4 vCPUs, 8 GiB RAM, and 60 GiB disk. These are planning defaults, not measured minimums. Leave room in `/tmp`, `/var/tmp`, Docker storage, and the output filesystem; packaging stages and extracts additional copies of the bundle.

Keep the Codex/development VM online for setup and builds. For final offline acceptance, use a second clean Ubuntu Desktop VM or a clone of a clean snapshot taken before developer tooling was installed. Installing Docker, Python dependencies, and 7-Zip on the target would hide portable-runtime dependency problems. If only one VM is available initially, complete the container tests first and leave the clean-desktop acceptance explicitly pending.

Run the following from a normal user terminal in the online VM:

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv 7zip
mkdir -p "$HOME/Developer"
cd "$HOME/Developer"
git clone https://github.com/mickan921/Soozip-Gaebal.git
cd Soozip-Gaebal
git status --short
git log -2 --oneline
cat /etc/os-release
uname -m
dpkg --print-architecture
python3 --version
command -v 7zz
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[desktop]'
```

Create fresh virtual environments on Ubuntu. Do not copy `.venv` or `.release-venv` from the Mac.

Set up Docker Engine using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/) and [Linux post-installation instructions](https://docs.docker.com/engine/install/linux-postinstall/). Complete any required logout/login before opening Codex. Confirm these work **without sudo**:

```bash
docker info
docker run --rm --platform linux/amd64 ubuntu:24.04 cat /etc/os-release
```

The application will not install Docker, change Docker permissions, or automatically elevate the online build. Run Codex and the desktop apps as the normal desktop user. Codex installation/authentication itself is outside this repository's setup; keep it on the online development machine.

## Validation sequence

Run all commands below from the repository root. Diagnose and fix a failed stage before proceeding to later stages. Preserve useful logs in the ignored `offline-output/` directory; if using `tee`, enable `set -o pipefail` so failures are not hidden.

### 1. Core, transport, and Qt tests

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -v
bash -n make-offline-apt.sh offline-apt-online offline-apt-offline \
  src/offline_apt_builder/resources/install.sh tests/test_end_to_end.sh
```

At the baseline commit, expect 33 passing tests and 18 explicit skips unless opt-in test variables were already set. Qt tests must actually run; a missing PySide6 installation must not be mistaken for a successful GUI test run.

### 2. Disposable real-package installer tests

```bash
OFFLINE_APT_INTEGRATION=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p test_integration.py -v
```

This builds tiny `.deb` fixtures in Docker and tests installation with networking disabled, including preview, stale approval, idempotence, holds, pinning, upgrades/downgrades, removals, missing dependencies, corrupt payloads, and failed maintainer scripts. Fixture transactions run inside containers, not on the development VM's package database. The three full-build acceptance tests remain skipped at this stage.

### 3. Real downloaded package bundles

```bash
./tests/test_end_to_end.sh
```

This enables both fixture integration and full acceptance tests, builds `jq` and `git cmake build-essential libssl-dev` bundles, and checks failure behavior for an unknown package. The portable-worker test is still skipped until releases exist and `OFFLINE_APT_PORTABLE=1` is set. Artifacts default to `offline-output/acceptance/`.

For a focused build with retained diagnostics:

```bash
.venv/bin/offline-apt-builder build --json --verbose --keep-workdir \
  --output ./offline-output/manual jq
```

Require a `VALIDATED` result, `.7z` plus matching `.sha256`, report JSON, and build log. Inspect `manifest.json` and `test-report.json` inside the archive: schema 2, successful disconnected validation, matching installed versions, and a dependency check must be recorded. The acceptance tests also verify that `libc6` was bundled even though it exists in the base image.

### 4. Source GUI workflows

From the graphical desktop session, without `QT_QPA_PLATFORM=offscreen`:

```bash
./offline-apt-online
./offline-apt-offline
```

Check package-list import, output selection, recommendations, progress/log updates, cancellation, and completed bundle details. In the offline app, opening a valid bundle must produce a transaction table before Install becomes available. Use a disposable target for actual installation; opening and previewing do not install packages.

### 5. Build portable clients on Ubuntu

```bash
python3 -m venv .release-venv
.release-venv/bin/python -m pip install -r packaging/requirements-release.txt
.release-venv/bin/python scripts/build_clients.py
```

Expected outputs:

```text
dist/offline-apt-online/                 # GUI, worker, runtime, extractor, notices
dist/offline-apt-offline/
dist/offline-apt-online-ubuntu24.04-amd64.tar.gz
dist/offline-apt-online-ubuntu24.04-amd64.tar.gz.sha256
dist/offline-apt-offline-ubuntu24.04-amd64.tar.gz
dist/offline-apt-offline-ubuntu24.04-amd64.tar.gz.sha256
```

The release script pins Python dependencies, checks the upstream 7-Zip archive hash, and packages static `7zzs` under the executable name `7zz`. Inspect both folders for their worker executable, `_internal/bin/7zz`, Qt platform plugins, and resource files. Launch the packaged online app and build a bundle as well as testing the source app.

Client distribution archives use `.tar.gz` so a fresh Ubuntu desktop can unpack them. The package bundles created by the online app use `.7z`. Keep those two artifact types distinct.

### 6. Packaged worker in a clean disconnected container

After stage 5, run the dedicated test without repeating all other full builds:

```bash
OFFLINE_APT_ACCEPTANCE=1 OFFLINE_APT_PORTABLE=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p test_integration.py \
  -k test_portable_worker_import_preview_install_without_runtime_dependencies -v
```

It builds a real `jq` bundle, mounts the portable offline client into a fresh Ubuntu base container, disables networking, runs preview as `nobody`, and installs through the packaged worker as root. The intended baseline has no separate Python, Docker CLI, or 7-Zip installation in that container. This tests the worker/runtime; it does not test the graphical polkit flow.

### 7. Clean offline desktop acceptance

Transfer the portable **offline client** `.tar.gz` and a `VALIDATED` `.7z` package bundle into the clean target VM. The bundle's `.sha256` sidecar is optional. Extract the client with `tar -xzf` and keep its entire folder together. Copy it to an executable local filesystem if the transfer medium is mounted `noexec`.

Disconnect the target's virtual network interfaces using the hypervisor before testing. Do not install developer dependencies, Docker, or 7-Zip to make the client start; fix missing portable runtime dependencies on the online builder instead. Standard Ubuntu system Python may already exist; the requirement is no separately installed Python packages or developer runtime setup.

Acceptance checklist:

- Launch the portable offline GUI as a normal user under both Wayland and X11 desktop sessions.
- Open a `.7z` by file picker and by drag/drop; repeat with spaces and `#` in the path.
- Verify archive identity, VALIDATED evidence, checksums, package preview, and a responsive interface. Preview must not ask for administrator authentication or install packages.
- Cancel the polkit prompt: no package transaction may start.
- Approve the installation: the worker must reverify private staging and the reviewed transaction, install from local APT data only, and report dependency consistency and expected versions.
- Reopen and rerun the same bundle successfully when the target is already at the requested versions.
- Check separate upgrade/downgrade approvals and refusal of held-package changes/removals using disposable fixtures or a restorable target snapshot.
- Check changed target state/pinning after preview: installation must demand a fresh review.
- Check corrupt archives, mismatched optional sidecars, UNTESTED bundles, unsupported manifest versions, and incompatible installer versions. These must not reach installation.
- Once the privileged workflow begins, GUI close/cancel controls must not terminate an active package transaction.
- Confirm the VM stayed disconnected throughout. `apt` source isolation alone is not a sandbox for arbitrary package maintainer scripts.

## Implementation map and review priorities

| Area | Files |
| --- | --- |
| Desktop screens and QProcess controller | `src/offline_apt_builder/desktop/app.py` |
| JSON-lines worker and source privilege entrypoint | `src/offline_apt_builder/worker.py`, `helper_entry.py` |
| Build orchestration and Docker validation | `src/offline_apt_builder/build.py`, `validator.py` |
| Resolution and Ubuntu repositories | `src/offline_apt_builder/resources/resolve.py`, `Dockerfile.builder`, `ubuntu.sources` |
| 7z handling and atomic publication | `src/offline_apt_builder/archive.py`, `packaging.py` |
| Offline verification and privileged staging | `src/offline_apt_builder/install.py` |
| Shared APT preview/install transaction | `src/offline_apt_builder/resources/install.sh` |
| Portable builds | `scripts/build_clients.py`, `packaging/clients.spec`, `packaging/requirements-release.txt` |
| Tests | `tests/test_core.py`, `test_transport.py`, `test_desktop.py`, `test_integration.py`, `fixtures/build_fixtures.py` |

Focus review on these boundaries when Ubuntu reveals failures:

1. Non-root APT preview: temporary directory permissions, APT acquisition identity, readability of target state/pinning, and identical approval tokens between normal-user preview and root staging.
2. Frozen GUI/worker: sibling executable lookup, bundled resource paths, Qt plugins/shared libraries, and subprocess environment handling after `pkexec` clears the environment.
3. Transaction review: the token binds checksums, actions, and package/policy state. Recompute it before applying changes; do not bypass mismatches to get a test passing.
4. Archive safety: native 7-Zip technical-listing compatibility, links/special files, path traversal/duplicates, encrypted or multipart files, complete checksum coverage, and free-space checks.
5. Test assertions: some integration shell snippets use `! command ...` under `set -e`. Audit those negative assertions explicitly; a negated command alone is not a reliable fail-fast assertion in Bash. Ensure missing-runtime and no-install assertions actually fail the test when violated.
6. Pin availability and native compatibility: release dependency versions were installed on the Mac, but their Ubuntu Python 3.12 combination is unverified. If a pin needs changing, record the reason and rerun the relevant package/runtime tests.

The offline verifier compares `install.sh` byte-for-byte with the client's resource. If that script changes, rebuild the package bundle and the matching portable offline client before testing them together.

Preserve agreed behavior: Ubuntu 24.04 amd64 only; package names/text files; `.7z` alone accepted with optional matching sidecar; required desktop validation; no removals/held changes; explicit version-change approval; temporary local-only APT configuration. The CLI's existing `--skip-test` may produce UNTESTED bundles, but the desktop importer refuses them.

## Evidence, cleanup, and finishing the work

Build reports/logs live next to archives in the chosen output directory. `--keep-workdir` prints a workspace path for deeper investigation. Installer failures retain local `/tmp/offline-apt-log.*/install.log` diagnostics; administrator access may be needed to read root-owned logs. Keep target diagnostics local where outbound transfer is prohibited.

There is no transactional rollback for completed package changes or maintainer scripts. Use target VM snapshots for recovery. Avoid broad Docker pruning or deleting unrelated workspaces; clean only named test/build resources.

Do not commit `.venv/`, `.release-venv/`, `build/`, `dist/`, `offline-output/`, package archives, or local diagnostic logs. Existing `.gitignore` excludes them. Update this handoff with the tested commit, Ubuntu version, commands, pass/fail/skip counts, artifact paths, graphical session results, and remaining blockers. Keep observed failures separate from suspected causes.

Completion means real Ubuntu fixture/full-build tests, portable-worker acceptance, and disconnected graphical installation have passed with evidence. If the clean desktop VM is unavailable, finish the other checks and explicitly leave that acceptance item pending.

The initial source upload used authenticated local Git after the GitHub connector returned a write-permission 403. A fresh machine will need its own GitHub authentication for pushing changes; public cloning does not require credentials. Do not copy credentials from the old machine or put them in this repository.
