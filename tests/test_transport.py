"""Actual 7z transport and trust boundary regressions."""
from dataclasses import asdict
from importlib.resources import files
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from offline_apt_builder.archive import extract_archive, parse_listing, verify_sidecar, run7
from offline_apt_builder.install import install, preview, verify_bundle
from offline_apt_builder.models import BuildError, InstallPlan, PackageAction
from offline_apt_builder.packaging import package_bundle
from offline_apt_builder.repository import write_checksums, write_json
from test_core import repository_fixture


def valid_bundle(root):
    manifest = repository_fixture(root)
    manifest.update(schema_version=2, validation_status='VALIDATED', base_image={'id': 'sha256:test'})
    write_json(root / 'manifest.json', manifest)
    write_json(root / 'test-report.json', {'status': 'VALIDATED', 'network_mode': 'none', 'exit_code': 0,
        'base_image': 'sha256:test', 'dependency_check': True, 'installed_versions': {'example': '1'}})
    for name, text in {'architecture.txt': 'amd64\n', 'packages.txt': 'example\n', 'with-recommends.txt': '0\n',
                       'build-info.txt': 'test', 'README-offline.txt': 'test'}.items():
        (root / name).write_text(text)
    (root / 'install.sh').write_bytes(files('offline_apt_builder').joinpath('resources/install.sh').read_bytes())
    write_checksums(root)
    return manifest


class TransportTests(unittest.TestCase):
    def test_actual_encrypted_and_symlink_archives_are_rejected(self):
        for kind in ('encrypted', 'symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / 'offline-apt'
                source.mkdir()
                (source / 'file').write_text('payload')
                options = ['-psecret'] if kind == 'encrypted' else ['-snl']
                if kind == 'symlink':
                    (source / 'link').symlink_to('/etc/passwd')
                archive = root / 'bundle.7z'
                run7(['a', '-t7z', *options, str(archive), 'offline-apt'], cwd=root)
                destination = root / 'empty'
                destination.mkdir()
                with self.assertRaises(BuildError):
                    extract_archive(archive, destination)
                self.assertFalse(list(destination.iterdir()))

    def test_roundtrip_optional_sidecar_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            valid_bundle(source)
            archive, checksum, _ = package_bundle(source, root, 'bundle.7z')
            verify_sidecar(archive)
            checksum.unlink()
            verify_sidecar(archive)
            restored = root / 'extracted'
            restored.mkdir()
            self.assertEqual(verify_bundle(extract_archive(archive, restored))['schema_version'], 2)
            checksum.write_text('0'*64 + '  bundle.7z\n')
            with self.assertRaises(BuildError):
                verify_sidecar(archive)
            archive.write_bytes(archive.read_bytes()[:40])
            empty = root / 'empty'
            empty.mkdir()
            with self.assertRaises(BuildError):
                extract_archive(archive, empty)

    def test_listing_rejects_unsafe_entries(self):
        template = 'Type = 7z\n----------\nPath = {}\nSize = 1\nAttributes = A -rw-r--r--\nEncrypted = -\n'
        for name in ('../escape', '/etc/passwd', 'offline-apt/../escape', 'other/a', 'offline-apt/./file', 'offline-apt/a\\b'):
            with self.subTest(name=name), self.assertRaises(BuildError):
                parse_listing(template.format(name))
        base = template.format('offline-apt/file')
        for listing in (base + '\n' + base.split('----------\n')[1],
                        base.replace('-rw-r--r--', 'lrwxrwxrwx'),
                        base.replace('-rw-r--r--', 'prw-r--r--'),
                        base.replace('Encrypted = -', 'Encrypted = +'),
                        base.replace('Type = 7z', 'Type = Split'),
                        base.replace('Type = 7z', 'Type = 7z\nVolumes = 2')):
            with self.assertRaises(BuildError):
                parse_listing(listing)

    def test_extraction_space_check_precedes_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid_bundle(root / 'source')
            archive, _, _ = package_bundle(root / 'source', root, 'bundle.7z')
            (root / 'empty').mkdir()
            with patch('offline_apt_builder.archive.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(BuildError, 'free bytes'):
                    extract_archive(archive, root / 'empty')
            self.assertFalse(list((root / 'empty').iterdir()))

    def test_validation_evidence_and_installer_must_match(self):
        for change in ('schema', 'status', 'evidence', 'installer', 'unrecorded', 'symlink'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = valid_bundle(root)
                if change == 'schema':
                    manifest['schema_version'] = 1
                if change == 'status':
                    manifest['validation_status'] = 'UNTESTED'
                write_json(root / 'manifest.json', manifest)
                if change == 'evidence':
                    write_json(root / 'test-report.json', {'status': 'VALIDATED'})
                if change == 'installer':
                    (root / 'install.sh').write_text('exit 0')
                write_checksums(root)
                if change == 'unrecorded':
                    (root / 'extra').write_text('unrecorded')
                if change == 'symlink':
                    (root / 'link').symlink_to('/etc/passwd')
                with self.assertRaises(BuildError):
                    verify_bundle(root)

    def test_preview_parses_machine_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid_bundle(root)
            output = 'PLAN\texample\tupgrade\t0\t1\nAPPROVAL\t' + 'a'*64 + '\n'
            with patch('offline_apt_builder.install.run_installer', return_value=output):
                plan = preview(root, lambda *a, **kw: None)
            self.assertEqual(plan.actions[0], PackageAction('example', 'upgrade', '0', '1'))
            self.assertEqual(plan.approval_token, 'a'*64)

    def test_stale_approval_never_runs_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid_bundle(root)
            import tempfile as tempmodule
            original = tempmodule.TemporaryDirectory
            with patch('offline_apt_builder.install.os.geteuid', return_value=0), \
                 patch('offline_apt_builder.install.tempfile.TemporaryDirectory', side_effect=lambda **kw: original(prefix=kw['prefix'])), \
                 patch('offline_apt_builder.install.preview', return_value=InstallPlan((), (), 'b'*64)), \
                 patch('offline_apt_builder.install.run_installer') as run:
                with self.assertRaisesRegex(BuildError, 'changed'):
                    install(root, 'a'*64, False, False, lambda *a, **kw: None)
                run.assert_not_called()

    def test_invalid_preview_records_are_rejected(self):
        records = ('PLAN\texample\tkeep\t1\t2\n',
                   'PLAN\texample\tunknown\t1\t1\n',
                   'PLAN\texample\tupgrade\n',
                   'PLAN\texample\tkeep\t1\t1\n' * 2,
                   'APPROVAL\t' + 'b'*64 + '\n')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid_bundle(root)
            for record in records:
                with self.subTest(record=record), patch('offline_apt_builder.install.run_installer',
                        return_value=record + 'APPROVAL\t' + 'a'*64 + '\n'):
                    with self.assertRaises(BuildError):
                        preview(root, lambda *a, **kw: None)
