import contextlib
import gzip
import io
import json
import lzma
from pathlib import Path
import runpy
import sys
from offline_apt_builder.archive import extract_archive
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from offline_apt_builder.build import build, check_host
from offline_apt_builder.cli import main
from offline_apt_builder.inputs import read_packages, validate_packages
from offline_apt_builder.models import BuildError, BuildRequest, BuildResult
from offline_apt_builder.packaging import package_bundle, publish_file
from offline_apt_builder.repository import parse_index, sha256, verify_repository, write_checksums, write_json
from offline_apt_builder.runner import Runner, redact
from offline_apt_builder.validator import validate

ROOT = Path(__file__).resolve().parents[1]
SELECT = runpy.run_path(str(ROOT / "src/offline_apt_builder/resources/resolve.py"))["select_packages"]


def repository_fixture(bundle):
    repo = bundle / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    deb = repo / "example_1_amd64.deb"
    deb.write_bytes(b"unit-test package payload")
    package = {"name": "example", "version": "1", "architecture": "amd64", "filename": deb.name,
               "size_bytes": deb.stat().st_size, "sha256": sha256(deb)}
    fields = {"Package": "example", "Version": "1", "Architecture": "amd64", "Filename": "./" + deb.name,
              "Size": str(package["size_bytes"]), "SHA256": package["sha256"]}
    index = ("\n".join(f"{key}: {value}" for key, value in fields.items()) + "\n\n").encode()
    (repo / "Packages").write_bytes(index)
    (repo / "Packages.gz").write_bytes(gzip.compress(index))
    (repo / "Packages.xz").write_bytes(lzma.compress(index))
    (bundle / "install-targets.txt").write_text("example=1\n")
    selection = {"requested": "example", "name": "example", "version": "1", "architecture": "amd64", "virtual": False}
    return {"schema_version": 1, "requested": ["example"], "packages": [package], "selections": [selection]}


class InputTests(unittest.TestCase):
    def test_comments_whitespace_and_stable_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "packages.txt"
            path.write_text("# comment\n git\tcmake # inline\n\nlibssl-dev\ngit\n")
            self.assertEqual(read_packages(["jq", "git"], [path]), ("jq", "git", "cmake", "libssl-dev"))

    def test_rejects_shell_and_apt_selectors(self):
        for value in ("--yes", "git=1", "git:amd64", "git/noble", "git;id", "$(id)", "git cmake", "*", "foo?", "a", "Git", "gít"):
            with self.subTest(value=value), self.assertRaises(BuildError):
                validate_packages([value])
        self.assertEqual(validate_packages(["g++", "libstdc++6", "python3.12"]), ("g++", "libstdc++6", "python3.12"))

    def test_empty_or_missing_file_fails(self):
        with self.assertRaises(BuildError):
            validate_packages([])
        with self.assertRaises(BuildError):
            read_packages([], [Path("/nonexistent/offline-apt-packages")])


class ResolverTests(unittest.TestCase):
    def package(self, name):
        return SimpleNamespace(name=name, candidate=SimpleNamespace(version="1:2.0-1", architecture="amd64"))

    def cache(self, packages, providers=None):
        class Cache(dict):
            def get_providing_packages(self, name, candidate_only=True):
                return (providers or {}).get(name, [])
        return Cache({p.name: p for p in packages})

    def test_exact_unknown_name_does_not_become_a_pattern(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            SELECT(self.cache([self.package("fooxbar")]), ["foo.bar"])

    def test_virtual_provider_and_concrete_selection(self):
        package = self.package("provider")
        result = SELECT(self.cache([package], {"virtual": [package]}), ["virtual"])
        self.assertEqual(result[0]["name"], "provider")
        self.assertTrue(result[0]["virtual"])

    def test_ambiguous_virtual_package_requires_concrete_provider(self):
        first, second = self.package("first"), self.package("second")
        cache = self.cache([first, second], {"virtual": [first, second]})
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            SELECT(cache, ["virtual"])
        self.assertEqual(SELECT(cache, ["virtual", "second"])[0]["name"], "second")


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name) / "offline-apt"
        self.manifest = repository_fixture(self.bundle)

    def test_valid_and_corrupted_payloads(self):
        verify_repository(self.bundle, self.manifest)
        (self.bundle / "repo/example_1_amd64.deb").write_bytes(b"corrupt")
        with self.assertRaisesRegex(BuildError, "mismatch"):
            verify_repository(self.bundle, self.manifest)

    def test_rejects_omissions_collisions_and_foreign_architectures(self):
        for change in ("duplicate", "foreign", "target", "extra", "index"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                bundle = Path(temp)
                manifest = repository_fixture(bundle)
                if change == "duplicate": manifest["packages"] *= 2
                if change == "foreign": manifest["packages"][0]["architecture"] = "arm64"
                if change == "target": (bundle / "install-targets.txt").write_text("other=1\n")
                if change == "extra": (bundle / "repo/extra.deb").write_bytes(b"extra")
                if change == "index": (bundle / "repo/Packages.gz").write_bytes(gzip.compress(b"different"))
                with self.assertRaises(BuildError): verify_repository(bundle, manifest)

    def test_rejects_traversal_and_symlinks(self):
        self.manifest["packages"][0]["filename"] = "../escape.deb"
        with self.assertRaises(BuildError): verify_repository(self.bundle, self.manifest)
        (self.bundle / "escape").symlink_to("/etc/passwd")
        with self.assertRaises(BuildError): write_checksums(self.bundle)

    def test_checksum_manifest_is_complete_and_relative(self):
        write_json(self.bundle / "manifest.json", self.manifest)
        write_checksums(self.bundle)
        sums = (self.bundle / "SHA256SUMS").read_text()
        self.assertNotIn(str(self.bundle), sums)
        self.assertNotIn("./SHA256SUMS", sums)
        for path in self.bundle.rglob("*"):
            if path.is_file() and path.name != "SHA256SUMS":
                self.assertIn(f"{sha256(path)}  ./{path.relative_to(self.bundle).as_posix()}\n", sums)

    def test_duplicate_index_fields_are_rejected(self):
        with self.assertRaises(BuildError): parse_index("Package: first\nPackage: second\n")


class PackagingTests(unittest.TestCase):
    def test_atomic_publication_never_clobbers_an_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            source, destination = Path(temp) / "source", Path(temp) / "destination"
            source.write_text("new")
            destination.write_text("existing")
            with self.assertRaises(FileExistsError): publish_file(source, destination)
            self.assertEqual(destination.read_text(), "existing")
            self.assertEqual(source.read_text(), "new")

    def test_archive_layout_checksum_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            bundle = output / "source"
            repository_fixture(bundle)
            archive, checksum, digest = package_bundle(bundle, output, "bundle.7z")
            self.assertEqual(digest, sha256(archive))
            self.assertEqual(checksum.read_text(), f"{digest}  bundle.7z\n")
            restored = output / "restored"
            restored.mkdir()
            self.assertTrue((extract_archive(archive, restored) / "repo/Packages").is_file())
            with self.assertRaises(FileExistsError): package_bundle(bundle, output, "bundle.7z")

    def test_interruption_and_failed_checksum_publication_leave_no_archive(self):
        for failure in ("archive", "checksum"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                bundle = output / "source"
                repository_fixture(bundle)
                if failure == "archive":
                    target = "offline_apt_builder.packaging.run7"
                    effect = KeyboardInterrupt()
                else:
                    target = "offline_apt_builder.packaging.publish_file"
                    def effect(src, dst):
                        if str(dst).endswith(".sha256"): raise OSError("disk failure")
                        return publish_file(src, dst)
                with patch(target, side_effect=effect), self.assertRaises((KeyboardInterrupt, OSError)):
                    package_bundle(bundle, output, "bundle.7z")
                self.assertFalse((output / "bundle.7z").exists())
                self.assertFalse(list(output.glob("*.part")))


class RunnerTests(unittest.TestCase):
    def test_streams_and_redacts_failure_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "build.log"
            runner = Runner(log)
            self.addCleanup(runner.close)
            with self.assertRaises(BuildError) as caught:
                runner.run([sys.executable, "-c", "print('https://user:password@proxy.example token=private'); raise SystemExit(3)"])
            self.assertNotIn("user:password", str(caught.exception))
            self.assertNotIn("token=private", log.read_text())
            self.assertIn("[REDACTED]", log.read_text())

    def test_redacts_proxy_credentials(self):
        self.assertEqual(redact("http://user:pass@proxy:80"), "http://[REDACTED]@proxy:80")


class ValidatorTests(unittest.TestCase):
    def test_checks_container_exit_code_even_when_docker_start_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            class FakeRunner:
                def run(self, args, **kwargs):
                    if "{{.HostConfig.NetworkMode}}" in args: return "none\n"
                    if "{{.State.ExitCode}}" in args: return "17\n"
                    return "maintainer script failed\n" if "start" in args else ""
            report_path = Path(temp) / "test-report.json"
            with self.assertRaisesRegex(BuildError, "exit 17"):
                validate(FakeRunner(), Path(temp), "sha256:test", "test", [], report_path)
            self.assertEqual(json.loads(report_path.read_text())["status"], "FAILED")
            self.assertTrue((Path(temp) / "validation.log").is_file())

    def test_missing_requested_provider_is_not_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            class FakeRunner:
                def run(self, args, **kwargs):
                    if "{{.HostConfig.NetworkMode}}" in args: return "none\n"
                    if "{{.State.ExitCode}}" in args: return "0\n"
                    return "SUCCESS: requested packages and providers installed; APT dependency check passed.\n"
            with self.assertRaisesRegex(BuildError, "every requested"):
                validate(FakeRunner(), Path(temp), "sha256:test", "test", [{"name": "provider", "version": "1"}], Path(temp) / "test-report.json")


class CliAndBuildTests(unittest.TestCase):
    def test_json_failure_contains_no_progress_on_stdout(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch("offline_apt_builder.cli.build", side_effect=BuildError("Docker unavailable")):
            code = main(["build", "--json", "jq"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "FAILED")
        self.assertIn("Docker unavailable", stderr.getvalue())

    def test_json_result_and_skip_test_are_passed_to_engine(self):
        result = BuildResult("UNTESTED", Path("/a"), Path("/b"), "abc", 1, 10, Path("/report"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch("offline_apt_builder.cli.build", return_value=result) as engine:
            self.assertEqual(main(["build", "--skip-test", "--json", "jq"]), 0)
        self.assertTrue(engine.call_args.args[0].skip_test)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "UNTESTED")

    def test_unsupported_host_and_architecture_fail_early(self):
        with patch("offline_apt_builder.build.platform.system", return_value="Darwin"), self.assertRaises(BuildError):
            check_host("amd64")
        with self.assertRaises(BuildError): check_host("arm64")

    def test_docker_failure_preserves_diagnostics_without_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            request = BuildRequest(("jq",), Path(temp))
            with patch("offline_apt_builder.build.check_host"), patch.object(Runner, "run", side_effect=BuildError("permission denied")):
                with self.assertRaises(BuildError) as caught: build(request)
            self.assertIsNotNone(caught.exception.report)
            report = json.loads(caught.exception.report.read_text())
            self.assertEqual(report["status"], "FAILED")
            self.assertIn("Docker is unavailable", report["error"])
            self.assertFalse(list(Path(temp).glob("*.7z")))

    def test_complete_engine_statuses_and_cleanup_with_simulated_docker(self):
        # This tests orchestration only; it is deliberately not real validation.
        for status in ("VALIDATED", "UNTESTED", "FAILED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                calls = []
                def fake_run(runner, args, **kwargs):
                    calls.append(args)
                    if args[1] == "info": return "linux\n"
                    if args[1:3] == ["image", "inspect"]:
                        if args[-1] == "ubuntu:24.04":
                            return json.dumps({"Architecture": "amd64", "Os": "linux", "Id": "sha256:base", "RepoDigests": ["ubuntu@sha256:base"]})
                        return "sha256:tooling\n"
                    if args[1] == "run":
                        work = Path(args[args.index("--volume") + 1].removesuffix(":/work"))
                        manifest = repository_fixture(work / "offline-apt")
                        write_json(work / "offline-apt/manifest.json", manifest)
                    if "{{.HostConfig.NetworkMode}}" in args: return "none\n"
                    if "{{.State.ExitCode}}" in args: return "17\n" if status == "FAILED" else "0\n"
                    if args[1] == "start":
                        return "INSTALLED\texample\t1\nSUCCESS: requested packages and providers installed; APT dependency check passed.\n"
                    return ""
                request = BuildRequest(("example",), output, skip_test=status == "UNTESTED")
                with patch("offline_apt_builder.build.check_host"), patch.object(Runner, "run", fake_run):
                    if status == "FAILED":
                        with self.assertRaises(BuildError): build(request)
                        self.assertFalse(list(output.glob("*.7z")))
                        continue
                    result = build(request)
                self.assertEqual(result.status, status)
                self.assertEqual(any(call[1] == "create" for call in calls), status == "VALIDATED")
                with tempfile.TemporaryDirectory() as restored:
                    extracted = extract_archive(result.archive, Path(restored))
                    manifest = json.loads((extracted / "manifest.json").read_text())
                    self.assertEqual(manifest["validation_status"], status)
                    information = (extracted / "build-info.txt").read_text()
                    self.assertNotIn("PENDING", information)
                    self.assertIn("Status: " + status, information)


if __name__ == "__main__":
    unittest.main()
