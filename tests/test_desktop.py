"""Native UI state tests with real Qt event delivery; no package mutations."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from offline_apt_builder.desktop.base import Worker
    from soozip.app import SoozipWindow
    from gaebal.app import GaebalWindow
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
