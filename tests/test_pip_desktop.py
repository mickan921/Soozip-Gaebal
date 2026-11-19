"""Pip-specific native UI lifecycle and approval behavior."""
import unittest
from unittest.mock import patch
from test_desktop import QApplication, SoozipWindow, GaebalWindow


@unittest.skipUnless(QApplication, 'Install desktop dependencies')
class PipDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mode_drafts_and_python_request(self):
        w = SoozipWindow()
        w.packages.setPlainText('git cmake')
        w.kind.setCurrentIndex(1)
        w.packages.setPlainText('requests>=2\nhttpx[http2]')
        w.python_version.setCurrentText('3.14')
        with patch.object(w.worker, 'launch') as launch:
            w.build_bundle()
            operation, request, privileged = launch.call_args.args
            self.assertEqual(request['kind'], 'pip')
            self.assertEqual(request['python_version'], '3.14')
            self.assertEqual(request['requirements'], ('requests>=2', 'httpx[http2]'))
            self.assertNotIn('with_recommends', request)
            self.assertFalse(privileged)
        w.finished(0)
        w.kind.setCurrentIndex(0)
        self.assertEqual(w.packages.toPlainText(), 'git cmake')
        w.kind.setCurrentIndex(1)
        self.assertIn('httpx[http2]', w.packages.toPlainText())
        w.close()

    def test_target_changes_clear_approval_and_install_is_unprivileged(self):
        w = GaebalWindow()
        w.kind = 'pip'
        w.bundle = '/bundle'
        w.python.setCurrentText('/usr/bin/python3')
        w.venv.setText('/tmp/test-venv')
        plan = {'type': 'plan', 'approval_token': 'a'*64, 'blockers': [], 'actions': [
            {'name': 'example', 'action': 'upgrade', 'old_version': '1', 'new_version': '2'}]}
        w.on_event(plan)
        w.on_event({'type': 'result', 'status': 'READY'})
        w.finished(0)
        w.upgrades.setChecked(True)
        self.assertTrue(w.install_button.isEnabled())
        w.venv.setText('/tmp/another-venv')
        self.assertIsNone(w.plan)
        self.assertFalse(w.install_button.isEnabled())
        w.on_event(plan)
        w.on_event({'type': 'result', 'status': 'READY'})
        w.finished(0)
        w.upgrades.setChecked(True)
        with patch.object(w.worker, 'launch') as launch:
            w.install_bundle()
            self.assertFalse(launch.call_args.args[2])
            self.assertTrue(w.installing)
            self.assertFalse(w.cancel.isEnabled())
            self.assertFalse(w.pip_controls.isEnabled())
        w.finished(1)
        self.assertIsNone(w.plan)
        w.close()

    def test_inspect_pip_waits_for_target_and_apt_previews_automatically(self):
        for kind in ('apt', 'pip'):
            with self.subTest(kind=kind):
                w = GaebalWindow()
                w.operation = 'inspect'
                manifest = {'build_id': 'fixture', 'requested': ['example'], 'bundle_type': kind,
                            'target': {'python_version': '3.12'}}
                with patch.object(w.worker, 'launch') as launch:
                    w.on_event({'type': 'bundle', 'bundle': '/bundle', 'manifest': manifest,
                                'interpreters': [{'path': '/usr/bin/python3', 'version': '3.12.3'}]})
                    w.on_event({'type': 'result', 'status': 'VERIFIED'})
                    w.finished(0)
                    if kind == 'pip':
                        launch.assert_not_called()
                        self.assertFalse(w.install_button.isEnabled())
                        w.preview_environment()
                    self.assertEqual(launch.call_args.args[0], 'preview')
                w.finished(1)
                w.close()

    def test_equivalent_pip_versions_display_as_already_installed(self):
        w = GaebalWindow()
        w.kind = 'pip'
        w.bundle = '/bundle'
        w.on_event({'type': 'plan', 'approval_token': 'a'*64, 'blockers': [], 'actions': [
            {'name': 'example', 'action': 'keep', 'old_version': '1.0', 'new_version': '1'}]})
        w.on_event({'type': 'result', 'status': 'READY'})
        w.finished(0)
        self.assertFalse(w.install_button.isEnabled())
        self.assertEqual(w.table.item(0, 1).text(), 'Already installed')
        w.close()
