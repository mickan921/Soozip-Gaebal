"""Gaebal: the offline package-bundle installer."""
import re
import tempfile
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem)
from offline_apt_builder.desktop.base import BaseWindow, launch


class GaebalWindow(BaseWindow):
    def __init__(self):
        super().__init__('Gaebal')
        self.setAcceptDrops(True)
        self.workspace = None
        self.plan = None
        self.bundle = None
        self.layout.addWidget(QLabel('Open or drop a validated .7z bundle to review its package changes.'))
        self.open_button = QPushButton('Open .7z Bundle…')
        self.open_button.clicked.connect(self.choose_bundle)
        self.layout.addWidget(self.open_button)
        self.summary = QLabel('No bundle selected')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)
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
        self.inputs = [self.open_button]
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
        self.start('preview', {'archive': filename, 'workspace': self.workspace.name})

    def on_event(self, event):
        super().on_event(event)
        if event.get('type') == 'bundle':
            self.bundle = event['bundle']
            m = event['manifest']
            self.summary.setText(f"Build: {m.get('build_id', 'unknown')} • VALIDATED • Integrity verified\nRequested: {', '.join(m['requested'])}")
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
                        or (action['action'] == 'keep' and action['old_version'] != action['new_version'])):
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
        super().finished(code)
        if code or not self.succeeded or was_installing:
            self.plan = None
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
        self.start('install', {'bundle': self.bundle, 'approval_token': self.plan['approval_token'],
            'upgrades': self.upgrades.isChecked(), 'downgrades': self.downgrades.isChecked()}, privileged=True)

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
