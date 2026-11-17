"""Opt-in, real Gaebal UI/portable-worker tests in disposable offline containers.

The Qt window and QProcess event delivery are real. Only worker launch is
redirected from pkexec to Docker so package changes never reach the host.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QEventLoop, QProcess, QTimer
    from PySide6.QtWidgets import QApplication
    from gaebal.app import GaebalWindow
except ImportError:
    QApplication = None

from offline_apt_builder import __version__
from offline_apt_builder.install import verify_bundle
from offline_apt_builder.packaging import package_bundle
from offline_apt_builder.repository import write_checksums, write_json
from offline_apt_builder.runner import Runner
from offline_apt_builder.validator import validate
import test_integration as integration

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('OFFLINE_APT_COMPLEX') == '1'
EXPECTED = {'foundation': '1', 'common': '1', 'pre': '1', 'runtime': '2',
            'engine': '2', 'core': '2', 'plugin': '1', 'service': '2',
            'cli': '2', 'compat': '1', 'suite': '2'}
SEED = '''set -eu
dpkg -i /fixtures/pool/offline-complex-foundation_1_all.deb /fixtures/pool/offline-complex-common_1_all.deb
dpkg -i /fixtures/pool/offline-complex-pre_1_all.deb
dpkg -i /fixtures/pool/offline-complex-runtime_1_all.deb /fixtures/pool/offline-complex-engine_1_all.deb
dpkg -i /fixtures/pool/offline-complex-core_1_all.deb
dpkg -i /fixtures/pool/offline-complex-service_3_all.deb /fixtures/pool/offline-complex-compat_2_all.deb
: > /var/log/offline-complex-order.log
'''


@unittest.skipUnless(ENABLED and QApplication,
                     'Set OFFLINE_APT_COMPLEX=1 with Docker, desktop dependencies, and built Gaebal')
class ComplexBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = ROOT / 'dist/Gaebal'
        metadata = json.loads((cls.client / 'release.json').read_text())
        if metadata['version'] != __version__:
            raise AssertionError('Rebuild the portable clients before this test.')
        resource = 'offline_apt_builder/resources/install.sh'
        if (cls.client / '_internal' / resource).read_bytes() != (ROOT / 'src' / resource).read_bytes():
            raise AssertionError('Packaged installer is stale; rebuild the portable clients.')
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.evidence = Path(tempfile.mkdtemp(prefix='gaebal-complex-', dir=ROOT / 'build'))
        cls.evidence.chmod(0o755)
        print(f'\nComplex bundle evidence: {cls.evidence}', flush=True)
        integration.prepare_installer_fixtures(cls)
        cls.workspace.chmod(0o755)
        bundle = cls.workspace / 'complex'
        manifest = json.loads((bundle / 'manifest.json').read_text())
        manifest.update(schema_version=2, validation_status='PENDING',
                        base_image={'id': cls.base}, build_id='complex-ordering-fixture', tool_version=__version__)
        write_json(bundle / 'manifest.json', manifest)
        write_checksums(bundle)
        container = 'gaebal-complex-validate-' + uuid.uuid4().hex[:12]
        runner = Runner(cls.evidence / 'validation-build.log')
        try:
            report = validate(runner, bundle, cls.base, container, manifest['selections'],
                              cls.evidence / 'validation-report.json')
            integration.docker('cp', container + ':/var/log/offline-complex-order.log',
                               str(cls.evidence / 'fresh-install-order.log'))
        finally:
            runner.close()
            integration.docker('rm', '--force', container, check=False)
        manifest['validation_status'] = 'VALIDATED'
        write_json(bundle / 'manifest.json', manifest)
        write_json(bundle / 'test-report.json', report)
        write_checksums(bundle)
        verify_bundle(bundle)
        cls.archive, _, _ = package_bundle(bundle, cls.evidence, 'complex-bundle.7z')

    def setUp(self):
        self.name = self._testMethodName
        self.container = 'gaebal-complex-target-' + uuid.uuid4().hex[:12]
        integration.docker('run', '-d', '--name', self.container, '--pull', 'never', '--network', 'none',
                           '--volume', f'{self.workspace}:/fixtures:ro',
                           '--volume', f'{self.evidence}:/inputs:ro',
                           '--volume', f'{self.client}:/client:ro', self.base, 'sleep', 'infinity')
        self.addCleanup(integration.docker, 'rm', '--force', self.container, check=False)
        network = integration.docker('inspect', '--format', '{{.HostConfig.NetworkMode}}', self.container).stdout.strip()
        self.assertEqual(network, 'none')
        self.shell(SEED)
        self.window = GaebalWindow()
        self.window.show()
        self.events = []
        self.exceptions = []
        self.window.worker.message.connect(self.events.append)
        self.preview_number = 0
        self.launch_patch = patch.object(self.window.worker, 'launch', side_effect=self.launch_in_container)
        self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        exception_patch = patch.object(sys, 'excepthook',
                                      lambda *exc: self.exceptions.append(''.join(traceback.format_exception(*exc))))
        exception_patch.start()
        self.addCleanup(exception_patch.stop)
        self.addCleanup(self.close_window)

    def close_window(self):
        if self.window.worker.state() != QProcess.ProcessState.NotRunning:
            integration.docker('rm', '--force', self.container, check=False)
            self.window.worker.kill()
            self.window.worker.waitForFinished(5000)
        self.window.busy = False
        self.window.close()

    def shell(self, script, check=True):
        result = integration.docker('exec', self.container, 'bash', '-c', script, check=check)
        with (self.evidence / (self.name + '.container.log')).open('a') as stream:
            stream.write(result.stdout)
        return result.stdout

    def snapshot(self):
        return self.shell('sha256sum /var/lib/dpkg/status; cat /var/log/offline-complex-order.log')

    def launch_in_container(self, operation, request, privileged=False):
        request = dict(request)
        if operation == 'preview':
            self.preview_number += 1
            request['archive'] = '/inputs/complex-bundle.7z'
            request['workspace'] = f'/work/import-{self.preview_number}'
            self.shell(f"mkdir -p {request['workspace']}; chmod 777 {request['workspace']}")
        self.assertEqual(privileged, operation == 'install')
        worker = self.window.worker
        worker.buffer = b''
        worker.completed_once = False
        worker.start('docker', ['exec', '--user', '0' if privileged else '65534', self.container,
                               '/client/offline-apt-worker', operation, '--request', json.dumps(request)])

    def run_ui(self, action, label, expected=0):
        loop = QEventLoop()
        codes, ticks = [], []
        self.events.clear()

        def complete(code):
            codes.append(code)
            loop.quit()

        self.window.worker.completed.connect(complete)
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: ticks.append(1))
        heartbeat.start(5)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(60000)
        try:
            action()
            if self.window.busy:
                loop.exec()
        finally:
            heartbeat.stop()
            deadline.stop()
            self.window.worker.completed.disconnect(complete)
            prefix = self.evidence / f'{self.name}-{label}'
            prefix.with_suffix('.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.events))
            write_json(prefix.with_suffix('.ui.json'), {'codes': codes, 'event_loop_ticks': len(ticks),
                       'status': self.window.status.text(), 'busy': self.window.busy,
                       'succeeded': self.window.succeeded, 'qt_exceptions': self.exceptions})
            self.window.grab().save(str(prefix.with_suffix('.png')))
        self.assertEqual(codes, [expected], self.window.logs.toPlainText())
        self.assertEqual(self.window.worker.exitStatus(), QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.window.busy)
        self.assertTrue(ticks, 'Qt event loop did not remain responsive')
        self.assertEqual(self.exceptions, [])
        self.assertEqual(self.window.succeeded, expected == 0)
        self.assertTrue(self.window.open_button.isEnabled())
        if expected:
            self.assertIsNone(self.window.plan)
            self.assertFalse(self.window.install_button.isEnabled())
            self.assertTrue(self.window.had_error)

    def preview(self, label='preview', expected=0):
        self.run_ui(lambda: self.window.load_bundle(str(self.archive)), label, expected)

    def approve(self):
        self.assertFalse(self.window.install_button.isEnabled())
        self.window.upgrades.click()
        self.assertFalse(self.window.install_button.isEnabled())
        self.window.downgrades.click()
        self.assertTrue(self.window.install_button.isEnabled())

    def assert_order(self, trace, fresh=False):
        lines = trace.splitlines()
        edges = [('runtime', 'core'), ('engine', 'core'), ('runtime', 'plugin'),
                 ('core', 'service'), ('plugin', 'service'), ('core', 'cli'),
                 ('service', 'suite'), ('cli', 'suite'), ('compat', 'suite')]
        if fresh:
            edges += [('foundation', 'pre'), ('foundation', 'runtime'), ('common', 'core'), ('common', 'plugin')]
            self.assertLess(lines.index('postinst pre 1'), lines.index('preinst runtime 2'))
        for dependency, dependent in edges:
            with self.subTest(dependency=dependency, dependent=dependent):
                self.assertLess(lines.index(f'postinst {dependency} {EXPECTED[dependency]}'),
                                lines.index(f'postinst {dependent} {EXPECTED[dependent]}'))

    def test_fresh_install_obeys_dependency_and_predependency_order(self):
        self.assert_order((self.evidence / 'fresh-install-order.log').read_text(), fresh=True)

    def test_mixed_versions_install_in_order_and_repeat_without_changes(self):
        before = self.snapshot()
        self.preview()
        self.assertEqual(before, self.snapshot())
        actions = {a['name'].removeprefix('offline-complex-'): a for a in self.window.plan['actions']}
        for name in ('runtime', 'engine', 'core'):
            self.assertEqual((actions[name]['action'], actions[name]['old_version'], actions[name]['new_version']),
                             ('upgrade', '1', '2'))
        for name, old, new in (('service', '3', '2'), ('compat', '2', '1')):
            self.assertEqual((actions[name]['action'], actions[name]['old_version'], actions[name]['new_version']),
                             ('downgrade', old, new))
        for name in ('foundation', 'common'):
            self.assertEqual(actions[name]['action'], 'keep')
        for name in ('plugin', 'cli', 'suite'):
            self.assertEqual(actions[name]['action'], 'install')
        self.approve()
        self.run_ui(self.window.install_bundle, 'install')
        self.assertEqual(self.window.status.text(), 'INSTALLED')
        versions = self.shell("dpkg-query -W -f='${Package} ${db:Status-Status} ${Version}\\n' 'offline-complex-*'")
        # dpkg also knows uninstalled alternative/virtual names from Depends.
        # Retain every active/partial state so an unpacked package cannot pass.
        active = {line for line in versions.splitlines() if line.split(' ', 2)[1] != 'not-installed'}
        self.assertEqual(active, {f'offline-complex-{n} installed {v}' for n, v in EXPECTED.items()})
        self.shell('apt-get check; test -z "$(dpkg --audit)"')
        trace = self.shell('cat /var/log/offline-complex-order.log')
        (self.evidence / 'mixed-install-order.log').write_text(trace)
        self.assert_order(trace)
        installed = self.snapshot()
        self.preview('repeat')
        self.assertEqual(installed, self.snapshot())
        self.assertIn('Already installed', self.window.status.text())
        self.assertFalse(self.window.install_button.isEnabled())

    def test_incompatible_installed_dependency_fails_and_ui_can_retry(self):
        self.shell('dpkg -i /fixtures/pool/offline-complex-blocker_1_all.deb')
        before = self.snapshot()
        self.preview('conflict', expected=1)
        self.assertEqual(before, self.snapshot())
        self.assertIn('offline-complex-blocker', self.window.logs.toPlainText())
        self.assertIn('Cannot safely install', self.window.status.text())
        self.shell('dpkg --remove offline-complex-blocker')
        self.preview('recovered')
        self.approve()

    def test_held_dependency_fails_without_changes_and_ui_can_retry(self):
        self.shell('apt-mark hold offline-complex-runtime')
        before = self.snapshot()
        self.preview('held', expected=1)
        self.assertEqual(before, self.snapshot())
        self.assertIn('held', self.window.logs.toPlainText().lower())
        self.shell('apt-mark unhold offline-complex-runtime')
        self.preview('recovered')

    def test_forbidden_provider_version_fails_without_changes_and_ui_can_retry(self):
        self.shell("printf 'Package: offline-complex-engine\\nPin: version 2\\nPin-Priority: -1\\n' > /etc/apt/preferences.d/complex-test")
        before = self.snapshot()
        self.preview('pinned', expected=1)
        self.assertEqual(before, self.snapshot())
        self.assertIn('offline-complex-engine-api', self.window.logs.toPlainText())
        self.shell('rm /etc/apt/preferences.d/complex-test')
        self.preview('recovered')

    def test_backend_refuses_each_missing_version_approval(self):
        self.preview()
        before = self.snapshot()
        for upgrades, downgrades, message in ((False, True, 'Approve the listed upgrades'),
                                              (True, False, 'Approve the listed downgrades')):
            with self.subTest(upgrades=upgrades, downgrades=downgrades):
                request = {'bundle': self.window.bundle, 'approval_token': self.window.plan['approval_token'],
                           'upgrades': upgrades, 'downgrades': downgrades}
                result = integration.docker('exec', self.container, '/client/offline-apt-worker', 'install',
                                            '--request', json.dumps(request), check=False)
                (self.evidence / f'{self.name}-{upgrades}-{downgrades}.jsonl').write_text(result.stdout)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stdout)
                self.assertEqual(before, self.snapshot())

    def test_stale_approval_fails_without_changes_and_ui_can_retry(self):
        self.preview()
        old_token = self.window.plan['approval_token']
        self.approve()
        self.shell('apt-mark hold offline-complex-common')
        before = self.snapshot()
        self.run_ui(self.window.install_bundle, 'stale-install', expected=1)
        self.assertEqual(before, self.snapshot())
        self.assertIn('state changed', self.window.status.text())
        self.preview('recovered')
        self.assertNotEqual(self.window.plan['approval_token'], old_token)


if __name__ == '__main__':
    unittest.main()
