"""Two native clients sharing an asynchronous JSON-lines worker controller."""
import json
import os
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


class Worker(QProcess):
    message = Signal(dict)
    completed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buffer = b''
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self.consume)
        self.readyReadStandardError.connect(lambda: self.message.emit({'type': 'log', 'message': bytes(self.readAllStandardError()).decode(errors='replace')}))
        self.finished.connect(self.done)
        self.errorOccurred.connect(self.failed)

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.message.emit({'type': 'error', 'message': self.errorString()})
            self.completed.emit(127)

    def consume(self):
        self.buffer += bytes(self.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                self.message.emit(json.loads(line))
            except (ValueError, UnicodeError):
                self.message.emit({'type': 'log', 'message': line.decode(errors='replace')})

    def done(self, code, _status):
        self.consume()
        self.completed.emit(code)

    def launch(self, operation, request, privileged=False):
        self.buffer = b''
        if getattr(sys, 'frozen', False):
            command = [str(Path(sys.executable).with_name('offline-apt-worker'))]
        else:
            command = [sys.executable, '-m', 'offline_apt_builder.worker']
        command += [operation, '--request', json.dumps(request)]
        if privileged:
            if not getattr(sys, 'frozen', False):
                # Module search paths are intentionally cleared by pkexec. The source
                # helper uses its own absolute package location instead.
                helper = Path(__file__).resolve().parents[1] / 'helper_entry.py'
                command = [sys.executable, str(helper), operation, '--request', json.dumps(request)]
            command = ['/usr/bin/pkexec', '--disable-internal-agent', *command]
        env = QProcessEnvironment.systemEnvironment()
        if not getattr(sys, 'frozen', False):
            env.insert('PYTHONPATH', str(Path(__file__).resolve().parents[2]))
        else:
            # Avoid leaking frozen Qt library search paths to host executables.
            env.remove('LD_LIBRARY_PATH')
        self.setProcessEnvironment(env)
        self.start(command[0], command[1:])


class BaseWindow(QMainWindow):
    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(860, 700)
        self.busy = False
        self.installing = False
        self.had_error = False
        self.succeeded = False
        self.worker = Worker(self)
        self.worker.message.connect(self.on_event)
        self.worker.completed.connect(self.finished)
        widget = QWidget()
        self.setCentralWidget(widget)
        self.layout = QVBoxLayout(widget)
        heading = QLabel(title)
        heading.setStyleSheet('font-size: 22px; font-weight: 600; margin: 8px 0;')
        self.layout.addWidget(heading)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(10000)
        self.cancel = QPushButton('Cancel')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.worker.terminate)
        self.inputs = []

    def add_status(self):
        self.layout.addWidget(self.status)
        self.layout.addWidget(self.progress)
        self.layout.addWidget(QLabel('Details'))
        self.layout.addWidget(self.logs, 1)
        self.layout.addWidget(self.cancel)

    def start(self, operation, request, privileged=False):
        self.busy = True
        self.had_error = False
        self.succeeded = False
        self.installing = privileged
        self.logs.clear()
        self.status.setText('Waiting for administrator authentication…' if privileged else 'Starting…')
        self.progress.setRange(0, 0)
        self.cancel.setEnabled(not privileged)
        for widget in self.inputs:
            widget.setEnabled(False)
        self.worker.launch(operation, request, privileged)

    def on_event(self, event):
        kind = event.get('type')
        if kind == 'log':
            self.logs.appendPlainText(event['message'])
        elif kind == 'progress':
            self.status.setText(event['message'])
        elif kind == 'error':
            self.had_error = True
            self.status.setText(event['message'].splitlines()[0])
            self.logs.appendPlainText(event['message'])
        elif kind == 'result':
            self.succeeded = True
            self.status.setText(event['status'])

    def finished(self, code):
        self.busy = False
        self.installing = False
        self.cancel.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(int(code == 0 and self.succeeded))
        for widget in self.inputs:
            widget.setEnabled(True)
        if code == 126:
            self.status.setText('Administrator authentication was cancelled. No packages were installed.')
        elif code and not self.had_error:
            self.status.setText('Operation failed. Check Details and confirm desktop authentication is available.')
        elif not self.succeeded and not self.had_error:
            self.status.setText('Worker stopped without a completion result. Review Details.')

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, 'Operation in progress',
                'Keep this window open until installation finishes.' if self.installing else 'Cancel the current operation before closing.')
            event.ignore()
        else:
            event.accept()


class OnlineWindow(BaseWindow):
    def __init__(self):
        super().__init__('Offline APT Builder — Online')
        self.layout.addWidget(QLabel('Ubuntu 24.04 amd64 • Local Docker Engine required'))
        self.packages = QPlainTextEdit()
        self.packages.setPlaceholderText('Package names, separated by whitespace. # comments are supported.\ne.g. git cmake build-essential libssl-dev')
        self.packages.setMaximumHeight(140)
        self.layout.addWidget(self.packages)
        self.import_button = QPushButton('Import Package List…')
        self.import_button.clicked.connect(self.import_packages)
        self.layout.addWidget(self.import_button)
        row = QHBoxLayout()
        self.output = QLineEdit(str(Path.home() / 'offline-bundles'))
        self.browse = QPushButton('Output Folder…')
        self.browse.clicked.connect(self.choose_output)
        row.addWidget(self.output, 1)
        row.addWidget(self.browse)
        self.layout.addLayout(row)
        self.recommends = QCheckBox('Include recommended packages')
        self.layout.addWidget(self.recommends)
        self.build_button = QPushButton('Build and Validate Bundle')
        self.build_button.clicked.connect(self.build_bundle)
        self.layout.addWidget(self.build_button)
        self.result = QLabel('')
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.layout.addWidget(self.result)
        self.open_output = QPushButton('Open Output Folder')
        self.open_output.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.output.text())))
        self.layout.addWidget(self.open_output)
        self.inputs = [self.packages, self.import_button, self.output, self.browse, self.recommends, self.build_button]
        self.add_status()

    def import_packages(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Import package list', '', 'Text files (*.txt);;All files (*)')
        if filename:
            try:
                self.packages.appendPlainText(Path(filename).read_text())
            except (OSError, UnicodeError) as exc:
                self.status.setText(str(exc))

    def choose_output(self):
        if directory := QFileDialog.getExistingDirectory(self, 'Output folder', self.output.text()):
            self.output.setText(directory)

    def build_bundle(self):
        names = [name for line in self.packages.toPlainText().splitlines() for name in line.split('#', 1)[0].split()]
        from ..inputs import validate_packages
        try:
            validate_packages(names)
            if not self.output.text().strip():
                raise ValueError('Choose an output folder.')
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.result.clear()
        self.start('build', {'packages': names, 'output': self.output.text(), 'with_recommends': self.recommends.isChecked()})

    def on_event(self, event):
        super().on_event(event)
        if event.get('type') == 'result':
            self.result.setText(f"Archive: {event['archive']}\nPackages: {event['package_count']} • Bytes: {event['size_bytes']:,}\nSHA-256: {event['sha256']}\nChecksum: {event['checksum']}\nReport: {event['report']}")


class OfflineWindow(BaseWindow):
    def __init__(self):
        super().__init__('Offline APT Installer — Offline')
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


def launch(online):
    app = QApplication(sys.argv)
    app.setApplicationName('Offline APT')
    if os.geteuid() == 0:
        QMessageBox.critical(None, 'Launch as your desktop user', 'Run the desktop client without sudo. Installation requests administrator authentication when needed.')
        return 1
    window = OnlineWindow() if online else OfflineWindow()
    window.show()
    return app.exec()


def online_main():
    return launch(True)


def offline_main():
    return launch(False)
