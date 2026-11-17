"""Gaebal: the offline package-bundle installer."""
import tempfile
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
        if self.workspace:
            self.workspace.cleanup()
        self.workspace = tempfile.TemporaryDirectory(prefix='offline-apt-import-')
        self.plan = None
        self.bundle = None
        self.table.setRowCount(0)
        self.summary.setText(filename)
        for checkbox in (self.upgrades, self.downgrades):
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
        self.install_button.setEnabled(False)
        self.start('preview', {'archive': filename, 'workspace': self.workspace.name})

    def on_event(self, event):
        super().on_event(event)
        if event.get('type') == 'bundle':
            self.bundle = event['bundle']
            m = event['manifest']
            self.summary.setText(f"Build: {m.get('build_id', 'unknown')} • VALIDATED • Integrity verified\nRequested: {', '.join(m['requested'])}")
        elif event.get('type') == 'plan':
            self.plan = event
            self.table.setRowCount(len(event['actions']))
            for row, action in enumerate(event['actions']):
                for column, key in enumerate(('name', 'action', 'old_version', 'new_version')):
                    self.table.setItem(row, column, QTableWidgetItem(action[key]))
            self.table.resizeColumnsToContents()

    def finished(self, code):
        was_installing = self.installing
        super().finished(code)
        if code or not self.succeeded or was_installing:
            self.plan = None
        actions = self.plan['actions'] if self.plan else []
        self.upgrades.setEnabled(any(a['action'] == 'upgrade' for a in actions))
        self.downgrades.setEnabled(any(a['action'] == 'downgrade' for a in actions))
        self.update_install()

    def update_install(self):
        permitted = bool(self.plan and not self.plan['blockers'] and not self.busy)
        if permitted:
            actions = self.plan['actions']
            permitted = (not any(a['action'] == 'upgrade' for a in actions) or self.upgrades.isChecked()) and (not any(a['action'] == 'downgrade' for a in actions) or self.downgrades.isChecked())
        self.install_button.setEnabled(permitted)

    def install_bundle(self):
        if not self.plan or self.busy:
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
        self.load_bundle(event.mimeData().urls()[0].toLocalFile())

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted() and self.workspace:
            self.workspace.cleanup()

def main():
    return launch(GaebalWindow, "Gaebal")
