"""Gaebal: the offline package-bundle installer."""
import re
import tempfile
from pathlib import Path
import shlex
from packaging.version import Version
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget, QVBoxLayout,
    QTableWidget, QTableWidgetItem)
from offline_apt_builder.desktop.base import BaseWindow, launch


class GaebalWindow(BaseWindow):
    def __init__(self):
        super().__init__('Gaebal')
        self.setAcceptDrops(True)
        self.workspace = None
        self.plan = None
        self.bundle = None
        self.kind = 'apt'
        self.layout.addWidget(QLabel('Open or drop a validated .7z bundle to review its package changes.'))
        self.open_button = QPushButton('Open .7z Bundle…')
        self.open_button.clicked.connect(self.choose_bundle)
        self.layout.addWidget(self.open_button)
        self.summary = QLabel('No bundle selected')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.layout.addWidget(self.summary)
        self.pip_controls = QWidget()
        pip_layout = QVBoxLayout(self.pip_controls)
        self.environment_mode = QComboBox()
        self.environment_mode.addItem('Create virtualenv', True)
        self.environment_mode.addItem('Use existing virtualenv', False)
        pip_layout.addWidget(self.environment_mode)
        self.python = QComboBox()
        self.python.setEditable(True)
        self.python.setPlaceholderText('Select installed Python interpreter')
        self.python_browse = QPushButton('Python executable…')
        python_row = QHBoxLayout()
        python_row.addWidget(self.python, 1)
        python_row.addWidget(self.python_browse)
        pip_layout.addLayout(python_row)
        self.venv = QLineEdit()
        self.venv.setPlaceholderText('Absolute virtualenv directory')
        self.venv_browse = QPushButton('Virtualenv folder…')
        environment_row = QHBoxLayout()
        environment_row.addWidget(self.venv, 1)
        environment_row.addWidget(self.venv_browse)
        pip_layout.addLayout(environment_row)
        self.preview_button = QPushButton('Review Python Package Changes')
        pip_layout.addWidget(self.preview_button)
        self.layout.addWidget(self.pip_controls)
        self.pip_controls.hide()
        self.python_browse.clicked.connect(self.choose_python)
        self.venv_browse.clicked.connect(self.choose_venv)
        self.preview_button.clicked.connect(self.preview_environment)
        self.environment_mode.currentIndexChanged.connect(self.target_changed)
        self.python.currentTextChanged.connect(self.target_changed)
        self.venv.textChanged.connect(self.target_changed)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Package', 'Action', 'Installed version', 'Bundle version'])
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.layout.addWidget(self.table)
        self.upgrades = QCheckBox('Approve listed upgrades')
        self.downgrades = QCheckBox('Approve listed downgrades')
        for widget in (self.upgrades, self.downgrades):
            widget.setEnabled(False)
            widget.toggled.connect(self.update_install)
            self.layout.addWidget(widget)
        self.install_button = QPushButton('Install Reviewed Packages…')
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_bundle)
        self.layout.addWidget(self.install_button)
        self.inputs = [self.open_button, self.pip_controls]
        self.add_status()

    def choose_bundle(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Open bundle', '', '7-Zip bundles (*.7z)')
        if filename:
            self.load_bundle(filename)

    def load_bundle(self, filename):
        if self.busy:
            return
        self.plan = None
        self.bundle = None
        self.table.setRowCount(0)
        self.summary.setText(filename)
        for checkbox in (self.upgrades, self.downgrades):
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
        self.install_button.setEnabled(False)
        self.install_button.setText('Install Reviewed Packages…')
        try:
            if self.workspace:
                self.workspace.cleanup()
                self.workspace = None
            self.workspace = tempfile.TemporaryDirectory(prefix='offline-apt-import-')
        except OSError as exc:
            self.on_event({'type': 'error', 'message': f'Cannot prepare bundle workspace: {exc}. Check free space and folder permissions, then reopen the bundle.'})
            return
        self.pip_controls.hide()
        self.start('inspect', {'archive': filename, 'workspace': self.workspace.name})

    def choose_python(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Select installed Python executable')
        if filename:
            self.python.setCurrentText(filename)

    def choose_venv(self):
        directory = QFileDialog.getExistingDirectory(self, 'Virtualenv directory or parent for a new environment', self.venv.text())
        if directory:
            self.venv.setText(str(Path(directory) / 'gaebal-venv') if self.environment_mode.currentData() else directory)

    def target_changed(self, *_):
        self.plan = None
        self.table.setRowCount(0)
        for checkbox in (self.upgrades, self.downgrades):
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
        self.install_button.setText('Install Reviewed Packages…')
        self.install_button.setEnabled(False)
        create = self.environment_mode.currentData()
        self.python.setEnabled(create)
        self.python_browse.setEnabled(create)

    def target_request(self):
        path = self.venv.text().strip()
        if not path or not Path(path).expanduser().is_absolute():
            raise ValueError('Choose an absolute virtualenv path.')
        create = self.environment_mode.currentData()
        python = self.python.currentText().strip() if create else str(Path(path).expanduser() / 'bin/python')
        if not python or not Path(python).expanduser().is_absolute():
            raise ValueError('Choose an installed Python executable.')
        return {'python': str(Path(python).expanduser()), 'venv': str(Path(path).expanduser()), 'create': create}

    def preview_environment(self):
        if self.busy or not self.bundle:
            return
        try:
            target = self.target_request()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.target_changed()
        self.start('preview', {'bundle': self.bundle, 'target': target})

    def on_event(self, event):
        super().on_event(event)
        if event.get('type') == 'error':
            # Invalidate approvals immediately, including errors arriving before
            # worker completion. No stale plan may survive a failed preview.
            self.plan = None
            self.table.setRowCount(0)
            self.install_button.setEnabled(False)
            for checkbox in (self.upgrades, self.downgrades):
                checkbox.setChecked(False)
                checkbox.setEnabled(False)
        if event.get('type') == 'bundle':
            self.bundle = event['bundle']
            m = event['manifest']
            self.kind = m.get('bundle_type', 'apt')
            target = f" • CPython {m['target']['python_version']}" if self.kind == 'pip' else ''
            self.summary.setText(f"Build: {m.get('build_id', 'unknown')} • {self.kind.upper()}{target} • VALIDATED • Integrity verified\nRequested: {', '.join(m['requested'])}")
            if self.kind == 'apt':
                scenarios = (event.get('validation') or {}).get('scenarios', [])
                coverage = 'Legacy coverage: clean Ubuntu installation.'
                if m.get('validation_policy'):
                    count = sum(s['kind'] == 'installation' for s in scenarios)
                    coverage = f'Coverage: {count} offline installation tests; {len(scenarios) - count} desktop dependency simulations.'
                self.summary.setText(self.summary.text() + '\n' + coverage)
                for scenario in scenarios:
                    self.logs.appendPlainText(f"Validation: {scenario.get('label', scenario['id'])} — {scenario['kind']} — {scenario['status']}")
            self.pip_controls.setVisible(self.kind == 'pip')
            if self.kind == 'pip' and self.operation == 'inspect':
                self.environment_mode.setCurrentIndex(0)
                self.python.clear()
                for item in event.get('interpreters', []):
                    self.python.addItem(item['path'])
                if self.python.count():
                    self.python.setCurrentIndex(0)
                self.venv.setText(str(Path.home() / '.virtualenvs' / ('gaebal-' + m['build_id'])))
        elif event.get('type') == 'result' and event.get('venv'):
            path = event['venv']
            self.summary.setText(self.summary.text() + '\nEnvironment: ' + path + '\nActivate: source ' + shlex.quote(path + '/bin/activate'))
        elif event.get('type') == 'plan':
            actions = event['actions']
            blockers = event['blockers']
            token = event['approval_token']
            if (not isinstance(actions, list) or not actions
                    or not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers)
                    or not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token)):
                raise ValueError('Invalid package plan')
            for action in actions:
                if (not isinstance(action, dict)
                        or not all(isinstance(action.get(key), str) for key in ('name', 'action', 'old_version', 'new_version'))
                        or action['action'] not in {'keep', 'install', 'reinstall', 'upgrade', 'downgrade', 'configure'}
                        or not action['name'] or not action['new_version']
                        or (action['action'] == 'keep' and (
                            Version(action['old_version']) != Version(action['new_version']) if self.kind == 'pip'
                            else action['old_version'] != action['new_version']))):
                    raise ValueError('Invalid package action')
            self.plan = event
            self.table.setRowCount(len(event['actions']))
            for row, action in enumerate(event['actions']):
                for column, key in enumerate(('name', 'action', 'old_version', 'new_version')):
                    value = action[key]
                    if key == 'action' and value == 'keep':
                        value = 'Already installed'
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()

    def finished(self, code):
        was_installing = self.installing
        operation = self.operation
        super().finished(code)
        if code or not self.succeeded or was_installing:
            self.plan = None
        if operation == 'inspect' and self.succeeded and self.bundle:
            if self.kind == 'apt':
                self.start('preview', {'bundle': self.bundle})
                return
            self.status.setText('Verified — select a Python environment, then review package changes.')
        actions = self.plan['actions'] if self.plan else []
        self.upgrades.setEnabled(any(a['action'] == 'upgrade' for a in actions))
        self.downgrades.setEnabled(any(a['action'] == 'downgrade' for a in actions))
        if self.plan and not self.plan['blockers'] and actions and all(a['action'] == 'keep' for a in actions):
            self.status.setText('Already installed — all requested versions match. No changes are needed.')
            self.install_button.setText('Already Installed')
        self.update_install()

    def update_install(self):
        permitted = bool(self.bundle and self.plan and not self.plan['blockers'] and not self.busy)
        if permitted:
            actions = self.plan['actions']
            permitted = any(a['action'] != 'keep' for a in actions) and (not any(a['action'] == 'upgrade' for a in actions) or self.upgrades.isChecked()) and (not any(a['action'] == 'downgrade' for a in actions) or self.downgrades.isChecked())
        self.install_button.setEnabled(permitted)

    def install_bundle(self):
        self.update_install()
        if not self.install_button.isEnabled():
            return
        self.install_button.setEnabled(False)
        self.upgrades.setEnabled(False)
        self.downgrades.setEnabled(False)
        request = {'bundle': self.bundle, 'approval_token': self.plan['approval_token'],
                   'upgrades': self.upgrades.isChecked(), 'downgrades': self.downgrades.isChecked()}
        if self.kind == 'pip':
            request['target'] = self.target_request()
        self.start('install', request, privileged=self.kind == 'apt')

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile() and urls[0].toLocalFile().endswith('.7z'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile() and urls[0].toLocalFile().endswith('.7z'):
            self.load_bundle(urls[0].toLocalFile())
            event.acceptProposedAction()

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted() and self.workspace:
            try:
                self.workspace.cleanup()
            except OSError as exc:
                self.on_event({'type': 'error', 'message': f'Cannot remove temporary workspace {self.workspace.name}: {exc}'})

def main():
    return launch(GaebalWindow, "Gaebal")
