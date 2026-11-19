OFFLINE APT INSTALLATION — UBUNTU 24.04 AMD64
Build status: @STATUS@

Open the .7z in the portable Gaebal app and review package changes.
The .sha256 sidecar is optional; when present it must match.
Transfer the archive inward through the approved process.
Nothing needs to be exported from the air-gapped machine.

Matching compatibility packages are included as repository options. Gaebal
installs only what the requested transaction needs. Existing system libraries
and their companion packages may need to be upgraded together; review these
changes before approving them. Package removals remain forbidden.

Standalone fallback (requires an existing 7zz extractor):

  sha256sum -c @ARCHIVE@.sha256
  7zz x @ARCHIVE@
  cd offline-apt
  sudo ./install.sh --dry-run
  sudo ./install.sh

Automation: --yes approves installation, but does NOT approve changes to existing
versions. Review the dry run, then explicitly add --allow-upgrades and/or
--allow-downgrades when needed. Removals and held-package changes are forbidden.

The installer needs standard Ubuntu APT/dpkg/Bash tools, root privileges, and
sufficient free disk space. It supports read-only bundles and paths with spaces.
It uses a temporary file repository and does not edit permanent APT sources.
It retains failure diagnostics locally in /tmp/offline-apt-log.*/install.log.

VALIDATED means the shipped installer passed in a fresh Ubuntu container with
networking disabled. UNTESTED means that test was explicitly skipped. Neither
status guarantees compatibility with a modified target, vendor packages, kernel
modules, hardware, or maintainer scripts needing network/systemd/devices.

The local repository is unsigned (trusted=yes). SHA-256 detects corruption; it
does not authenticate an archive if someone can replace both it and its checksum.
Protect the archive through the approved transfer process. Inspect manifest.json,
test-report.json, build-info.txt, and logs/ for build evidence. All logs stay local.
