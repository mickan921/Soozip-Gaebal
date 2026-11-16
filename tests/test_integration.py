"""Opt-in real Ubuntu/Docker tests. These are never represented by mocks."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from offline_apt_builder.archive import extract_archive
import tempfile
import unittest
import uuid

from offline_apt_builder.build import build, check_host
from offline_apt_builder.models import BuildError, BuildRequest

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get("OFFLINE_APT_INTEGRATION") == "1"
ACCEPTANCE = os.environ.get("OFFLINE_APT_ACCEPTANCE") == "1"


def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@unittest.skipUnless(ENABLED, "Set OFFLINE_APT_INTEGRATION=1 on Ubuntu 24.04 amd64 with Docker")
class InstallerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        check_host("amd64")
        cls.workspace = Path(tempfile.mkdtemp(prefix="offline-apt-fixtures-", dir="/tmp"))
        cls.image = "offline-apt-fixtures:" + uuid.uuid4().hex[:12]
        cls.addClassCleanup(shutil.rmtree, cls.workspace, ignore_errors=True)
        cls.addClassCleanup(docker, "image", "rm", cls.image, check=False)
        docker("pull", "--platform", "linux/amd64", "ubuntu:24.04")
        image = json.loads(docker("image", "inspect", "--format", "{{json .}}", "ubuntu:24.04").stdout)
        cls.base = image["Id"]
        context = ROOT / "src/offline_apt_builder/resources"
        docker("build", "--platform", "linux/amd64", "--build-arg", "BASE_IMAGE=" + image["RepoDigests"][0],
               "--file", str(context / "Dockerfile.builder"), "--tag", cls.image, str(context))
        docker("run", "--rm", "--network", "none", "--volume", f"{ROOT}:/source:ro",
               "--volume", f"{cls.workspace}:/work", "--env", "PYTHONPATH=/source/src",
               "--env", f"BUILDER_UID={os.getuid()}", "--env", f"BUILDER_GID={os.getgid()}",
               "--entrypoint", "python3", cls.image, "/source/tests/fixtures/build_fixtures.py")

    def install(self, bundle="valid", flags="--yes --allow-upgrades", setup="", after="", expected=0):
        # All shell fragments below are fixed test code, not package/user input.
        script = f"""set -eu
{setup}
set +e
bash '/bundle with spaces #/install.sh' {flags}
code=$?
set -e
[ "$code" {'-eq 0' if expected == 0 else '-ne 0'} ] || exit 91
{after}
"""
        result = docker("run", "--rm", "--pull", "never", "--network", "none",
                        "--volume", f"{self.workspace}:/fixtures:ro", "--volume",
                        f"{self.workspace / bundle}:/bundle with spaces #:ro", self.base, "bash", "-c", script, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def test_unprivileged_preview_and_approved_install(self):
        script = """set -eu
su nobody -s /bin/bash -c "bash '/bundle with spaces #/install.sh' --preview" > /tmp/preview
! dpkg-query -W offline-test-tool 2>/dev/null
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/preview)
test -n "$token"
bash '/bundle with spaces #/install.sh' --yes --approval-token="$token"
"""
        result = docker("run", "--rm", "--network", "none", "--volume",
                        f"{self.workspace / 'valid'}:/bundle with spaces #:ro", self.base,
                        "bash", "-c", script, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_changed_policy_invalidates_approval(self):
        script = """set -eu
bash /bundle/install.sh --preview > /tmp/preview
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/preview)
printf 'Package: offline-test-tool\nPin: version 2\nPin-Priority: 700\n' > /etc/apt/preferences.d/changed
if bash /bundle/install.sh --yes --approval-token="$token"; then exit 91; fi
! dpkg-query -W offline-test-tool 2>/dev/null
"""
        result = docker("run", "--rm", "--network", "none", "--volume",
                        f"{self.workspace / 'valid'}:/bundle:ro", self.base,
                        "bash", "-c", script, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("transaction changed", result.stdout)

    def test_readonly_spaces_predepends_alternatives_and_repeat_install(self):
        output = self.install(after="bash '/bundle with spaces #/install.sh' --yes; dpkg-query -W -f='${db:Status-Status}' offline-test-pre | grep -qx installed")
        self.assertIn("INSTALLED\toffline-test-tool\t2", output)

    def test_dry_run_installs_nothing(self):
        self.install(flags="--dry-run", after="! dpkg-query -W offline-test-tool 2>/dev/null")

    def test_recommendations_are_optional(self):
        self.install(after="! dpkg-query -W offline-test-recommend 2>/dev/null")
        self.install(bundle="recommended", after="dpkg-query -W offline-test-recommend")

    def test_virtual_selection_installs_concrete_provider(self):
        self.install(bundle="virtual", after="dpkg-query -W -f='${Provides}' offline-test-tool | grep -q offline-test-virtual")

    def test_remote_sources_hooks_pinning_and_configuration_preservation(self):
        setup = r"""printf 'deb http://example.invalid/ubuntu noble main\n' > /etc/apt/sources.list
printf 'APT::Update::Pre-Invoke {"touch /tmp/hook-ran";};\nDPkg::Pre-Invoke {"touch /tmp/hook-ran";};\n' > /etc/apt/apt.conf.d/99offline-test
find /etc/apt -type f -exec sha256sum {} + | sort > /tmp/before-config
"""
        after = "find /etc/apt -type f -exec sha256sum {} + | sort > /tmp/after-config; cmp /tmp/before-config /tmp/after-config; test ! -e /tmp/hook-ran"
        output = self.install(setup=setup, after=after)
        self.assertNotIn("example.invalid", output)

    def test_target_pinning_is_respected(self):
        setup = "printf 'Package: offline-test-tool\nPin: version 2\nPin-Priority: -1\n' > /etc/apt/preferences.d/offline-test"
        self.install(setup=setup, expected=1)

    def test_upgrades_need_separate_approval(self):
        setup = "dpkg -i /fixtures/pool/offline-test-tool_1_all.deb"
        output = self.install(flags="--yes", setup=setup, expected=1,
                              after="test \"$(dpkg-query -W -f='${Version}' offline-test-tool)\" = 1")
        self.assertIn("--allow-upgrades", output)
        self.install(setup=setup)

    def test_downgrades_need_separate_approval(self):
        setup = "dpkg -i /fixtures/pool/offline-test-pre_1_all.deb /fixtures/pool/offline-test-dep_1_all.deb /fixtures/pool/offline-test-tool_2_all.deb"
        output = self.install(bundle="old", flags="--yes", setup=setup, expected=1)
        self.assertIn("--allow-downgrades", output)
        self.install(bundle="old", flags="--yes --allow-downgrades", setup=setup)

    def test_holds_are_always_protected(self):
        setup = "dpkg -i /fixtures/pool/offline-test-tool_1_all.deb; apt-mark hold offline-test-tool"
        self.install(setup=setup, expected=1,
                     after="test \"$(dpkg-query -W -f='${Version}' offline-test-tool)\" = 1")

    def test_removals_are_always_protected(self):
        self.install(bundle="removal", setup="dpkg -i /fixtures/pool/offline-test-existing_1_all.deb", expected=1,
                     after="dpkg-query -W -f='${db:Status-Status}' offline-test-existing | grep -qx installed")

    def test_wrong_platform_and_missing_dependencies_fail(self):
        self.install(bundle="wrong-arch", expected=1)
        self.install(setup="sed -i 's/VERSION_ID=\"24.04\"/VERSION_ID=\"22.04\"/' /etc/os-release", expected=1)
        self.install(bundle="missing", expected=1)

    def test_corruption_stops_before_install(self):
        setup = "cp -a /fixtures/valid /tmp/corrupt; printf corrupt >> /tmp/corrupt/repo/offline-test-tool_2_all.deb"
        result = docker("run", "--rm", "--network", "none", "--volume", f"{self.workspace}:/fixtures:ro", self.base,
                        "bash", "-c", setup + "; if bash /tmp/corrupt/install.sh --yes --allow-upgrades; then exit 91; fi; ! dpkg-query -W offline-test-tool 2>/dev/null", check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Checksum verification failed", result.stdout)

    def test_failed_maintainer_script_retains_diagnostics(self):
        output = self.install(bundle="broken", expected=1, after="find /tmp -path '*/offline-apt-log.*/install.log' -type f | grep -q .")
        self.assertIn("intentional fixture postinst failure", output)


@unittest.skipUnless(ACCEPTANCE, "Set OFFLINE_APT_ACCEPTANCE=1 on Ubuntu 24.04 amd64 for real downloads/builds")
class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        check_host("amd64")
        cls.output = Path(os.environ.get("OFFLINE_APT_TEST_OUTPUT", "offline-output/acceptance")).resolve()
        cls.output.mkdir(parents=True, exist_ok=True)

    def test_real_small_and_development_bundles(self):
        for packages in (("jq",), ("git", "cmake", "build-essential", "libssl-dev")):
            with self.subTest(packages=packages):
                result = build(BuildRequest(packages, self.output))
                self.assertEqual(result.status, "VALIDATED")
                with tempfile.TemporaryDirectory() as temp:
                    extracted = extract_archive(result.archive, Path(temp))
                    manifest = json.loads((extracted / "manifest.json").read_text())
                    report = json.loads((extracted / "test-report.json").read_text())
                    self.assertIn("libc6", {p["name"] for p in manifest["packages"]})
                    self.assertEqual(manifest["requested"], list(packages))
                    self.assertTrue(report["dependency_check"])
                    self.assertEqual(report["network_mode"], "none")
                docker("run", "--rm", "--network", "none", "ubuntu:24.04", "dpkg-query", "-W", "libc6")

    @unittest.skipUnless(os.environ.get("OFFLINE_APT_PORTABLE") == "1", "Build portable clients and set OFFLINE_APT_PORTABLE=1")
    def test_portable_worker_import_preview_install_without_runtime_dependencies(self):
        client = ROOT / "dist/offline-apt-offline"
        self.assertTrue((client / "offline-apt-worker").is_file(), "Build portable clients first")
        result = build(BuildRequest(("jq",), self.output))
        # Use the actual reference base, which contains neither Python nor 7-Zip.
        image = json.loads(docker("image", "inspect", "--format", "{{json .}}", "ubuntu:24.04").stdout)
        script = r"""set -eu
! command -v python3
! command -v 7zz
! command -v docker
mkdir -m 777 /work
su nobody -s /bin/bash -c '/client/offline-apt-worker preview --request "{\"archive\":\"/input/bundle.7z\",\"workspace\":\"/work\"}"' > /tmp/preview.jsonl
token=$(sed -n 's/.*"approval_token": "\([a-f0-9]*\)".*/\1/p' /tmp/preview.jsonl)
test -n "$token"
/client/offline-apt-worker install --request "{\"bundle\":\"/work/offline-apt\",\"approval_token\":\"$token\",\"upgrades\":true}"
dpkg-query -W -f='${db:Status-Status}' jq | grep -qx installed
"""
        result = docker("run", "--rm", "--pull", "never", "--network", "none",
                        "--volume", f"{client}:/client:ro", "--volume", f"{result.archive}:/input/bundle.7z:ro",
                        image["Id"], "bash", "-c", script, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_unknown_package_produces_failure_report_without_archive(self):
        before = set(self.output.glob("*.7z"))
        with self.assertRaises(BuildError) as caught:
            build(BuildRequest(("offline-apt-deliberately-nonexistent-package",), self.output))
        self.assertTrue(caught.exception.report.is_file())
        self.assertEqual(set(self.output.glob("*.7z")), before)


if __name__ == "__main__":
    unittest.main()
