"""Native UI state tests with real Qt event delivery; no package mutations."""
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from offline_apt_builder.desktop.base import Worker
    from soozip.app import SoozipWindow
    from gaebal.app import GaebalWindow
    from offline_apt_builder import __version__
except ImportError:
    QApplication = None


@unittest.skipUnless(QApplication, 'Install the desktop extra to run Qt tests')
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_online_validates_input_and_never_requests_skip_test(self):
        window = SoozipWindow()
        with patch.object(window.worker, 'launch') as launch:
            window.packages.setPlainText('git=1')
            window.build_bundle()
            launch.assert_not_called()
            window.packages.setPlainText('git # comment\ncmake')
            window.build_bundle()
            operation, request, privileged = launch.call_args.args
            self.assertEqual(operation, 'build')
            self.assertEqual(request['packages'], ['git', 'cmake'])
            self.assertNotIn('skip_test', request)
            self.assertFalse(privileged)
            self.assertFalse(window.build_button.isEnabled())
            self.assertTrue(window.cancel.isEnabled())
        window.finished(1)
        window.close()

    def test_separate_version_approvals_and_install_lock(self):
        window = GaebalWindow()
        window.bundle = '/fixture'
        window.on_event({'type': 'plan', 'approval_token': 'a'*64, 'blockers': [], 'actions': [
            {'name': 'one', 'action': 'upgrade', 'old_version': '1', 'new_version': '2'},
            {'name': 'two', 'action': 'downgrade', 'old_version': '2', 'new_version': '1'}]})
        window.on_event({'type': 'result', 'status': 'READY'})
        window.finished(0)
        self.assertFalse(window.install_button.isEnabled())
        window.upgrades.setChecked(True)
        self.assertFalse(window.install_button.isEnabled())
        window.downgrades.setChecked(True)
        self.assertTrue(window.install_button.isEnabled())
        with patch.object(window.worker, 'launch') as launch:
            window.install_bundle()
            self.assertTrue(launch.call_args.args[2])
            self.assertFalse(window.cancel.isEnabled())
            self.assertFalse(window.install_button.isEnabled())
        window.finished(126)
        self.assertIn('cancelled', window.status.text())
        self.assertFalse(window.install_button.isEnabled())
        window.close()

    def test_real_worker_failure_does_not_block_event_loop(self):
        worker = Worker()
        events, ticks = [], []
        loop = QEventLoop()
        worker.message.connect(events.append)
        worker.completed.connect(loop.quit)
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(1)
        QTimer.singleShot(10000, loop.quit)
        worker.launch('preview', {'archive': '/missing.7z', 'workspace': '/missing'})
        loop.exec()
        timer.stop()
        if worker.state() != Worker.ProcessState.NotRunning:
            worker.kill()
            worker.waitForFinished()
            self.fail('Worker did not finish')
        self.assertTrue(ticks)
        self.assertTrue(any(e.get('type') == 'error' for e in events))

    def test_both_clients_show_runtime_version(self):
        for cls in (SoozipWindow, GaebalWindow):
            window = cls()
            window.show()
            self.assertEqual(window.version_label.text(), f'v{__version__}')
            self.assertTrue(window.version_label.isVisible())
            window.close()

    def test_removal_failure_immediately_clears_stale_approvals(self):
        from offline_apt_builder.install import removal_summary
        from PySide6.QtCore import Qt
        window = GaebalWindow()
        window.bundle = '/fixture'
        window.on_event({'type': 'plan', 'approval_token': 'a' * 64, 'blockers': [], 'actions': [
            {'name': 'example', 'action': 'upgrade', 'old_version': '1', 'new_version': '2'}]})
        window.on_event({'type': 'result', 'status': 'READY'})
        window.finished(0)
        window.upgrades.setChecked(True)
        self.assertTrue(window.install_button.isEnabled())
        diagnostics = {'category': 'removal-required', 'removed_packages': [f'package-{i}' for i in range(80)]}
        message = removal_summary(diagnostics) + '\n' + '\n'.join(diagnostics['removed_packages'])
        window.on_event({'type': 'error', 'message': message, 'diagnostics': diagnostics})
        self.assertIsNone(window.plan)
        self.assertFalse(window.install_button.isEnabled())
        self.assertFalse(window.upgrades.isChecked())
        self.assertIn('80 installed', window.status.text())
        self.assertIn('package-79', window.logs.toPlainText())
        self.assertTrue(window.status.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        window.finished(1)
        window.close()

    def test_worker_handles_non_object_json_and_final_line_without_newline(self):
        worker = Worker()
        events, codes = [], []
        worker.message.connect(events.append)
        worker.completed.connect(codes.append)
        worker.start(sys.executable, ['-c', '''import sys
sys.stdout.write('[]\\nnull\\n42\\n{"type":"result","status":"READY"}')
sys.stderr.write('last diagnostic')
'''])
        self.assertTrue(worker.waitForFinished(10000))
        self.assertEqual(codes, [0])
        self.assertIn({'type': 'result', 'status': 'READY'}, events)
        self.assertTrue(any('last diagnostic' in e.get('message', '') for e in events))
        self.assertTrue(any(e.get('message') == '[]' for e in events))

    def test_worker_failed_start_completes_once(self):
        worker = Worker()
        codes = []
        worker.completed.connect(codes.append)
        worker.start('/nonexistent/offline-apt-worker', [])
        self.assertFalse(worker.waitForStarted(1000))
        self.assertEqual(codes, [127])

    def test_crashed_worker_cannot_report_success(self):
        window = GaebalWindow()
        window.busy = True
        window.worker.start(sys.executable, ['-c', '''import os, signal
print('{"type":"result","status":"READY"}', flush=True)
os.kill(os.getpid(), signal.SIGKILL)
'''])
        self.assertTrue(window.worker.waitForFinished(10000))
        self.assertFalse(window.busy)
        self.assertFalse(window.succeeded)
        self.assertIn('exited unexpectedly', window.status.text())
        self.assertTrue(window.open_button.isEnabled())
        window.close()

    def test_worker_error_cannot_be_overridden_by_success_result(self):
        for cls in (SoozipWindow, GaebalWindow):
            window = cls()
            with patch.object(window.worker, 'launch'):
                window.start('preview', {})
            window.worker.message.emit({'type': 'error', 'message': ''})
            window.worker.message.emit({'type': 'result', 'status': 'READY'})
            window.worker.completed.emit(0)
            self.assertFalse(window.busy)
            self.assertFalse(window.succeeded)
            self.assertEqual(window.progress.value(), 0)
            self.assertTrue(window.inputs[0].isEnabled())
            window.close()

    def test_invalid_plan_invalidates_approval_and_allows_retry(self):
        window = GaebalWindow()
        with patch.object(window.worker, 'launch'):
            window.start('preview', {})
        window.worker.message.emit({'type': 'plan', 'actions': [{}]})
        window.worker.message.emit({'type': 'result', 'status': 'READY'})
        window.worker.completed.emit(0)
        self.assertIsNone(window.plan)
        self.assertFalse(window.install_button.isEnabled())
        self.assertTrue(window.open_button.isEnabled())
        self.assertIn('Invalid worker response', window.status.text())
        window.close()

    def test_matching_versions_need_no_privileged_install(self):
        window = GaebalWindow()
        window.bundle = '/fixture'
        window.on_event({'type': 'plan', 'approval_token': 'a'*64, 'blockers': [], 'actions': [
            {'name': 'one', 'action': 'keep', 'old_version': '1', 'new_version': '1'}]})
        window.on_event({'type': 'result', 'status': 'READY'})
        window.finished(0)
        self.assertIn('Already installed', window.status.text())
        self.assertEqual(window.table.item(0, 1).text(), 'Already installed')
        self.assertFalse(window.install_button.isEnabled())
        with patch.object(window.worker, 'launch') as launch:
            window.install_bundle()
            launch.assert_not_called()
        window.close()

    def test_workspace_failure_clears_old_plan_and_can_be_retried(self):
        window = GaebalWindow()
        window.plan = {'actions': [], 'blockers': []}
        window.bundle = '/old'
        with patch('gaebal.app.tempfile.TemporaryDirectory', side_effect=OSError('disk full')):
            window.load_bundle('/fixture.7z')
        self.assertIsNone(window.plan)
        self.assertIsNone(window.bundle)
        self.assertFalse(window.busy)
        self.assertIn('disk full', window.status.text())
        with patch.object(window.worker, 'launch') as launch:
            window.load_bundle('/fixture.7z')
            launch.assert_called_once()
        window.finished(1)
        window.close()

    def test_launch_failure_restores_controls_and_busy_job_cannot_restart(self):
        window = SoozipWindow()
        with patch.object(window.worker, 'launch', side_effect=OSError('unavailable')):
            window.start('build', {})
        self.assertFalse(window.busy)
        self.assertTrue(window.build_button.isEnabled())
        with patch.object(window.worker, 'launch') as launch:
            window.start('build', {})
            window.start('build', {})
            launch.assert_called_once()
        window.finished(1)
        window.close()
