"""Wheel input, integrity, dependency, transaction, and desktop regressions."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from offline_apt_builder.archive import extract_archive, parse_listing
from offline_apt_builder.cli import main as cli
from offline_apt_builder.models import BuildError, BuildResult, PipInstallTarget, PipBuildRequest
from offline_apt_builder.pip_build import build_pip, prepare_bundle_permissions
from offline_apt_builder.packaging import package_bundle
from offline_apt_builder.pip_inputs import read_requirements
from offline_apt_builder.pip_install import preview_pip, install_pip, verify_pip_bundle
from offline_apt_builder.repository import write_checksums, write_json
from offline_apt_builder.resources import pip_engine as engine

ROOT = Path(__file__).resolve().parents[1]
TOOLING = os.environ.get('OFFLINE_PIP_TOOLING')


def wheel(folder, name, version='1.0', dependencies=(), requires_python='>=3.11', tags='py3-none-any'):
    filename = f'{name.replace("-", "_")}-{version}-{tags}.whl'
    info = f'{name.replace("-", "_")}-{version}.dist-info'
    contents = {f'{name.replace("-", "_")}/__init__.py': f'__version__ = "{version}"\n',
                f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: {requires_python}\n' + ''.join('Requires-Dist: ' + d + '\n' for d in dependencies),
                f'{info}/WHEEL': f'Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: {tags}\n',
                f'{info}/RECORD': ''}
    contents[f'{info}/RECORD'] = ''.join(f'{p},,\n' for p in contents)
    with zipfile.ZipFile(folder / filename, 'w') as archive:
        for path, content in contents.items():
            archive.writestr(path, content)
    return folder / filename


def fixture(bundle, packages=None, requested=None, tools=False):
    (bundle / 'wheelhouse').mkdir(parents=True)
    (bundle / 'tooling').mkdir()
    for args in packages or [('sample', '1.0', ['foundation>=1']), ('foundation', '1.0', [])]:
        wheel(bundle / 'wheelhouse', *args)
    requested = requested or ['sample']
    if tools:
        for name in engine.TOOLS:
            shutil.copyfile(Path(TOOLING) / name, bundle / 'tooling' / name)
    (bundle / 'install-pip.py').write_bytes((ROOT / 'src/offline_apt_builder/resources/pip_engine.py').read_bytes())
    (bundle / 'requirements.txt').write_text('\n'.join(requested) + '\n')
    records = engine.wheels(bundle)
    (bundle / 'requirements.lock').write_text(engine.lock_text(records))
    manifest = {'bundle_type': 'pip', 'schema_version': 1, 'build_id': 'fixture', 'requested': requested,
                'target': {'os': 'ubuntu', 'release': '24.04', 'arch': 'amd64',
                           'python_version': '3.12', 'python_full_version': '3.12.3'},
                'packages': records, 'validation_status': 'VALIDATED',
                'base_image': {'id': 'sha256:base'}, 'runtime_image': 'sha256:runtime'}
    write_json(bundle / 'manifest.json', manifest)
    write_json(bundle / 'test-report.json', {'status': 'VALIDATED', 'network_mode': 'none', 'exit_code': 0,
                'base_image': 'sha256:base', 'runtime_image': 'sha256:runtime', 'python_full_version': '3.12.3',
                'dependency_check': True, 'installed_versions': {p['name']: p['version'] for p in records}})
    write_checksums(bundle)
    return manifest


class PipInputTests(unittest.TestCase):
    def test_requirement_semantics_and_line_numbers(self):
        self.assertEqual(read_requirements(['Requests>=2\nhttpx[http2]; python_version >= "3.11"\n# comment\nRequests>=2']),
                         ('Requests>=2', 'httpx[http2]; python_version >= "3.11"'))
        for value in ('--index-url=https://evil.invalid', '-r other.txt', 'foo @ https://evil.invalid/foo.whl',
                      'git+https://evil.invalid/repo', './project', 'requests numpy'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Requirements:2:'):
                read_requirements(['requests\n' + value])
        with self.assertRaises(ValueError):
            read_requirements([' # nothing'])

    def test_cli_contract_and_always_validates(self):
        result = BuildResult('VALIDATED', Path('/a.7z'), Path('/a.sha256'), 'a'*64, 2, 30, Path('/r.json'))
        with patch('offline_apt_builder.cli.build_pip', return_value=result) as build, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli(['build-pip', '--python-version', '3.14', '--json', 'requests>=2']), 0)
        self.assertEqual(build.call_args.args[0].python_version, '3.14')
        self.assertFalse(hasattr(build.call_args.args[0], 'skip_test'))
        self.assertEqual(json.loads(output.getvalue())['status'], 'VALIDATED')


class PipPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'offline-pip'
        self.manifest = fixture(self.bundle)

    def test_integrity_schema_evidence_installer_and_roundtrip(self):
        with patch.object(engine, 'load_tools'):
            self.assertEqual(verify_pip_bundle(self.bundle)['bundle_type'], 'pip')
            archive, _, _ = package_bundle(self.bundle, self.root, 'pip.7z')
            destination = self.root / 'extracted'
            destination.mkdir()
            self.assertEqual(extract_archive(archive, destination).name, 'offline-pip')
            self.assertEqual(verify_pip_bundle(destination / 'offline-pip'), self.manifest)
            report = json.loads((self.bundle / 'test-report.json').read_text())
            for key, value in [('network_mode', 'bridge'), ('exit_code', 1), ('dependency_check', False), ('installed_versions', {})]:
                with self.subTest(key=key):
                    write_json(self.bundle / 'test-report.json', {**report, key: value})
                    write_checksums(self.bundle)
                    with self.assertRaises(BuildError):
                        verify_pip_bundle(self.bundle)
            write_json(self.bundle / 'test-report.json', report)
            (self.bundle / 'install-pip.py').write_text('print("changed")')
            write_checksums(self.bundle)
            with self.assertRaisesRegex(BuildError, 'installer version'):
                verify_pip_bundle(self.bundle)

    def test_corrupt_omitted_or_unrecorded_files(self):
        (self.bundle / 'extra').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            engine.verify_checksums(self.bundle)
        (self.bundle / 'extra').unlink()
        (self.bundle / 'requirements.lock').write_text('sample==2\n')
        write_checksums(self.bundle)
        with patch.object(engine, 'load_tools'), self.assertRaisesRegex(ValueError, 'lock disagree'):
            engine.verify_payload(self.bundle)

    def test_archive_mixed_roots_rejected(self):
        listing = 'Type = 7z\n----------\nPath = offline-apt/a\nSize = 1\n\nPath = offline-pip/b\nSize = 1\n'
        with self.assertRaises(BuildError):
            parse_listing(listing)

    def test_metadata_remote_dependency_and_duplicate_versions_rejected(self):
        wheel(self.bundle / 'wheelhouse', 'remote', dependencies=['foo @ https://example.org/x.whl'])
        with self.assertRaisesRegex(ValueError, 'Remote dependency'):
            engine.wheels(self.bundle)
        (self.bundle / 'wheelhouse/remote-1.0-py3-none-any.whl').unlink()
        wheel(self.bundle / 'wheelhouse', 'sample', version='2.0')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            engine.wheels(self.bundle)

    def test_closure_extras_markers_and_reverse_dependencies(self):
        packages = {p['name']: p for p in self.manifest['packages']}
        environment = engine.python_info()['environment']
        self.assertEqual(engine.check_requirements(packages, ['sample'], environment), {'sample', 'foundation'})
        packages['sample']['requires_dist'] = ['foundation>=2; extra == "fast"']
        self.assertEqual(engine.check_requirements(packages, ['sample'], environment), {'sample'})
        with self.assertRaisesRegex(ValueError, 'Dependency conflict'):
            engine.check_requirements(packages, ['sample[fast]'], environment)
        with self.assertRaisesRegex(ValueError, 'Dependency conflict'):
            engine.check_requirements(packages, ['sample', 'foundation<1'], environment)

    def test_target_abi_and_requires_python_rejected(self):
        info = engine.python_info()
        for changed in ({'version': '3.11.9'}, {'free_threaded': True}, {'tags': []}, {'machine': 'aarch64'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                engine.check_compatibility(self.manifest, {**info, **changed}, {})
        self.manifest['packages'][0]['requires_python'] = '>=4'
        with self.assertRaisesRegex(ValueError, 'Python version'):
            engine.check_compatibility(self.manifest, info, {})


class PipPermissionTests(unittest.TestCase):
    def test_restrictive_umasks_make_only_payload_readable(self):
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory() as temp:
                workspace = Path(temp)
                old_mask = os.umask(mask)
                try:
                    bundle = workspace / 'offline-pip'
                    fixture(bundle)
                    before = engine.verify_checksums(bundle)
                    prepare_bundle_permissions(bundle)
                    self.assertEqual(engine.verify_checksums(bundle), before)
                    self.assertEqual(stat.S_IMODE(workspace.stat().st_mode), 0o700)
                    for path in [bundle, *bundle.rglob('*')]:
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644)
                    probe = workspace / 'private-log'
                    probe.write_text('The caller retains its original permissions.')
                    self.assertEqual(stat.S_IMODE(probe.stat().st_mode), 0o666 & ~mask)
                finally:
                    os.umask(old_mask)

    def test_permission_preparation_never_follows_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / 'private'
            outside.write_text('private')
            outside.chmod(0o600)
            bundle = root / 'offline-pip'
            bundle.mkdir()
            (bundle / 'link').symlink_to(outside)
            with self.assertRaises(BuildError):
                prepare_bundle_permissions(bundle)
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)


class PipBuildFailureTests(unittest.TestCase):
    def test_interrupted_download_and_disk_full_keep_no_payload(self):
        failures = [ConnectionResetError('Download connection interrupted'), OSError(errno.ENOSPC, 'No space left on device')]
        for failure in failures:
            with self.subTest(failure=str(failure)), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                workspace = []

                def broken_download(url, path, expected):
                    workspace.append(path.parent.parent)
                    path.write_bytes(b'incomplete download')
                    raise failure

                with patch('offline_apt_builder.pip_build.check_host'), patch('offline_apt_builder.pip_build.sevenzip'), \
                     patch('offline_apt_builder.pip_build.Runner.run', return_value='linux'), \
                     patch('offline_apt_builder.pip_build.download', side_effect=broken_download):
                    with self.assertRaises(BuildError):
                        build_pip(PipBuildRequest(('requests',), output))
                self.assertFalse(workspace[0].exists())
                self.assertFalse(list(output.glob('*.7z*')))
                report = json.loads(next(output.glob('*.report.json')).read_text())
                self.assertEqual(report['status'], 'FAILED')
                self.assertIn(str(failure), report['error'])

    def test_failed_resolution_and_cancellation_publish_only_diagnostics(self):
        for failure in (BuildError('No compatible wheel for requested package'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                calls = []

                def run(runner, command, **kwargs):
                    calls.append(command)
                    if command[1] == 'info':
                        return 'linux'
                    if command[1:3] == ['image', 'inspect']:
                        if command[-1] == 'ubuntu:24.04':
                            return json.dumps({'Architecture': 'amd64', 'Os': 'linux', 'Id': 'sha256:base', 'RepoDigests': ['ubuntu@sha256:base']})
                        return 'sha256:runtime'
                    if command[1] == 'run':
                        raise failure
                    return ''

                with patch('offline_apt_builder.pip_build.check_host'), patch('offline_apt_builder.pip_build.sevenzip'), \
                     patch('offline_apt_builder.pip_build.download', side_effect=lambda url, path, checksum: path.write_bytes(b'fixture')), \
                     patch('offline_apt_builder.pip_build.Runner.run', run):
                    with self.assertRaises(type(failure)):
                        build_pip(PipBuildRequest(('missing-package',), output))
                self.assertFalse(list(output.glob('*.7z')))
                reports = list(output.glob('*.report.json'))
                self.assertEqual(len(reports), 1)
                self.assertEqual(json.loads(reports[0].read_text())['status'], 'FAILED')
                self.assertTrue(any(c[1:3] == ['rm', '--force'] for c in calls))
                self.assertTrue(any(c[1:3] == ['image', 'rm'] for c in calls))


@unittest.skipUnless(TOOLING and platform.system() == 'Linux', 'Set OFFLINE_PIP_TOOLING to pinned downloaded wheels for real virtualenv tests')
class PipInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pip tests # ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'offline-pip'
        fixture(self.bundle, tools=True)
        self.target = PipInstallTarget(Path('/usr/bin/python3'), self.root / 'new venv', True)
        self.emit = lambda *args, **kwargs: None

    def install_new(self):
        plan = preview_pip(self.bundle, self.target, self.emit)
        self.assertFalse(self.target.venv.exists())
        install_pip(self.bundle, self.target, plan.approval_token, False, False, self.emit)
        return replace(self.target, create=False)

    def test_new_existing_missing_pip_and_repeat_noop(self):
        target = self.install_new()
        plan = preview_pip(self.bundle, target, self.emit)
        self.assertEqual({a.action for a in plan.actions}, {'keep'})
        before = {str(p): p.stat().st_mtime_ns for p in target.venv.rglob('*') if p.is_file()}
        install_pip(self.bundle, target, plan.approval_token, False, False, self.emit)
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in target.venv.rglob('*') if p.is_file()})
        self.assertFalse((target.venv / 'lib/python3.12/site-packages/pip').exists())

    def test_stale_target_or_bundle_cannot_install(self):
        plan = preview_pip(self.bundle, self.target, self.emit)
        self.target.venv.mkdir()
        with self.assertRaises(BuildError):
            install_pip(self.bundle, self.target, plan.approval_token, True, True, self.emit)
        self.assertFalse((self.target.venv / 'bin').exists())

    def test_upgrades_and_downgrades_need_separate_approval(self):
        target = self.install_new()
        second = self.root / 'second/offline-pip'
        fixture(second, [('sample', '2.0', ['foundation>=1']), ('foundation', '1.0', [])], tools=True)
        plan = preview_pip(second, target, self.emit)
        self.assertEqual(next(a.action for a in plan.actions if a.name == 'sample'), 'upgrade')
        with self.assertRaisesRegex(BuildError, 'Approve.*upgrades'):
            install_pip(second, target, plan.approval_token, False, True, self.emit)
        install_pip(second, target, plan.approval_token, True, False, self.emit)
        plan = preview_pip(self.bundle, target, self.emit)
        with self.assertRaisesRegex(BuildError, 'Approve.*downgrades'):
            install_pip(self.bundle, target, plan.approval_token, True, False, self.emit)
        install_pip(self.bundle, target, plan.approval_token, False, True, self.emit)

    def test_reverse_conflict_and_system_site_packages(self):
        target = self.install_new()
        second = self.root / 'second/offline-pip'
        fixture(second, [('foundation', '0.5', [])], ['foundation'], tools=True)
        with self.assertRaisesRegex(BuildError, 'Dependency conflict'):
            preview_pip(second, target, self.emit)
        config = target.venv / 'pyvenv.cfg'
        config.write_text(config.read_text().replace('include-system-site-packages = false', 'include-system-site-packages = true'))
        with self.assertRaisesRegex(BuildError, 'system site-packages'):
            preview_pip(self.bundle, target, self.emit)

    def test_same_version_metadata_change_invalidates_plan(self):
        target = self.install_new()
        second = self.root / 'second/offline-pip'
        fixture(second, [('sample', '2.0', ['foundation>=1']), ('foundation', '1.0', [])], tools=True)
        plan = preview_pip(second, target, self.emit)
        metadata = next((target.venv / 'lib/python3.12/site-packages').glob('sample-*.dist-info/METADATA'))
        metadata.write_text(metadata.read_text() + 'Summary: Changed after preview\n')
        with self.assertRaisesRegex(BuildError, 'state changed'):
            install_pip(second, target, plan.approval_token, True, True, self.emit)

    def test_equivalent_pep440_versions_are_kept_during_other_changes(self):
        target = self.install_new()
        second = self.root / 'second/offline-pip'
        fixture(second, [('sample', '2.0', ['foundation>=1']), ('foundation', '1', [])], tools=True)
        plan = preview_pip(second, target, self.emit)
        foundation = next(a for a in plan.actions if a.name == 'foundation')
        self.assertEqual(foundation.action, 'keep')
        self.assertEqual((foundation.old_version, foundation.new_version), ('1.0', '1'))
        install_pip(second, target, plan.approval_token, True, False, self.emit)

    def test_concurrent_environment_lock_refuses_then_allows_retry(self):
        target = self.install_new()
        second = self.root / 'second/offline-pip'
        fixture(second, [('sample', '2.0', ['foundation>=1']), ('foundation', '1.0', [])], tools=True)
        plan = preview_pip(second, target, self.emit)
        lock_dir = Path(tempfile.gettempdir()) / f'gaebal-pip-locks-{os.getuid()}'
        lock_name = hashlib.sha256(str(target.venv.resolve()).encode()).hexdigest()
        script = 'import fcntl,sys;f=open(sys.argv[1],"w");fcntl.flock(f,fcntl.LOCK_EX);print("LOCKED",flush=True);sys.stdin.read()'
        holder = subprocess.Popen([sys.executable, '-c', script, str(lock_dir / lock_name)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'LOCKED')
            with self.assertRaisesRegex(BuildError, 'Another Gaebal installation'):
                install_pip(second, target, plan.approval_token, True, False, self.emit)
            self.assertEqual(preview_pip(second, target, self.emit).approval_token, plan.approval_token)
        finally:
            holder.communicate(timeout=5)
        install_pip(second, target, plan.approval_token, True, False, self.emit)
