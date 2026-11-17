"""Shared GUI worker, progress controls, and launch behavior for both clients."""
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, Signal
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from offline_apt_builder import __version__


class Worker(QProcess):
    message = Signal(dict)
    completed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buffer = b''
        self.completed_once = False
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self.consume)
        self.readyReadStandardError.connect(self.consume_errors)
        self.finished.connect(self.done)
        self.errorOccurred.connect(self.failed)

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.message.emit({'type': 'error', 'message': self.errorString()})
            self.complete(127)

    def complete(self, code):
        if not self.completed_once:
            self.completed_once = True
            self.completed.emit(code)

    def consume_errors(self):
        message = bytes(self.readAllStandardError()).decode(errors='replace')
        if message:
            self.message.emit({'type': 'log', 'message': message})

    def dispatch(self, line):
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            event = None
        if isinstance(event, dict) and isinstance(event.get('type'), str):
            self.message.emit(event)
        else:
            self.message.emit({'type': 'log', 'message': line.decode(errors='replace')})

    def consume(self):
        self.buffer += bytes(self.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            self.dispatch(line)

    def done(self, code, status):
        self.consume()
        if self.buffer:
            self.dispatch(self.buffer)
            self.buffer = b''
        self.consume_errors()
        if status == QProcess.ExitStatus.CrashExit:
            self.message.emit({'type': 'error', 'message': 'Worker exited unexpectedly. Review Details and retry.'})
            code = code or 1
        self.complete(code)

    def launch(self, operation, request, privileged=False):
        self.buffer = b''
        self.completed_once = False
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
        self.worker.message.connect(self.receive_event)
        self.worker.completed.connect(self.finished)
        widget = QWidget()
        self.setCentralWidget(widget)
        self.layout = QVBoxLayout(widget)
        self.version_label = QLabel(f'v{__version__}')
        self.version_label.setContentsMargins(0, 0, 8, 2)
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self.version_label)
        heading = QLabel(title)
        heading.setStyleSheet('font-size: 22px; font-weight: 600; margin: 8px 0;')
        self.layout.addWidget(heading)
        self.status = QLabel('Ready')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
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
        if self.busy:
            return
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
        try:
            self.worker.launch(operation, request, privileged)
        except (OSError, ValueError, RuntimeError) as exc:
            self.on_event({'type': 'error', 'message': f'Cannot start worker: {exc}'})
            self.finished(1)

    def receive_event(self, event):
        # Exceptions raised in Qt slots otherwise bypass the job's error path,
        # potentially leaving stale approvals or permanently disabled controls.
        try:
            self.on_event(event)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            BaseWindow.on_event(self, {'type': 'error', 'message': 'Invalid worker response. Review Details and retry.'})

    def on_event(self, event):
        kind = event.get('type')
        if kind == 'log':
            self.logs.appendPlainText(event['message'])
        elif kind == 'progress':
            self.status.setText(event['message'])
        elif kind == 'error':
            self.had_error = True
            self.succeeded = False
            message = event['message'] or 'Operation failed. Review Details and retry.'
            self.status.setText(message.splitlines()[0])
            self.logs.appendPlainText(message)
        elif kind == 'result':
            if not self.had_error:
                self.status.setText(event['status'])
                self.succeeded = True

    def finished(self, code):
        self.succeeded = bool(code == 0 and self.succeeded and not self.had_error)
        self.busy = False
        self.installing = False
        self.cancel.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(int(code == 0 and self.succeeded))
        for widget in self.inputs:
            widget.setEnabled(True)
        if code == 126 and not self.had_error:
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


def launch(window_class, application_name):
    app = QApplication(sys.argv)
    app.setApplicationName(application_name)
    app.setApplicationVersion(__version__)
    if os.geteuid() == 0:
        QMessageBox.critical(None, 'Launch as your desktop user', 'Run the desktop client without sudo. Installation requests administrator authentication when needed.')
        return 1
    window = window_class()
    window.show()
    return app.exec()
