"""Soozip: the online package-bundle builder."""
from pathlib import Path
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton)
from offline_apt_builder.desktop.base import BaseWindow, launch


class SoozipWindow(BaseWindow):
    def __init__(self):
        super().__init__('Soozip')
        self.layout.addWidget(QLabel('Ubuntu 24.04 amd64 • Local Docker Engine required'))
        self.kind = QComboBox()
        self.kind.addItem('APT packages', 'apt')
        self.kind.addItem('Python packages (pip)', 'pip')
        self.layout.addWidget(self.kind)
        self.python_version = QComboBox()
        self.python_version.addItems(['3.11', '3.12', '3.13', '3.14'])
        self.python_version.setCurrentText('3.12')
        self.python_label = QLabel('Target CPython version (already installed on the offline machine)')
        self.layout.addWidget(self.python_label)
        self.layout.addWidget(self.python_version)
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
        self.compatibility_note = QLabel('Matching system compatibility packages are included automatically. '
                                        'Required tests cover fresh Ubuntu, older libraries, desktop dependencies and alternative providers. '
                                        'Testing uses temporary containers and can take substantial time and disk space.')
        self.compatibility_note.setWordWrap(True)
        self.layout.addWidget(self.compatibility_note)
        self.build_button = QPushButton('Build and Validate Bundle')
        self.build_button.clicked.connect(self.build_bundle)
        self.layout.addWidget(self.build_button)
        self.result = QLabel('')
        self.result.setTextFormat(Qt.TextFormat.PlainText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.layout.addWidget(self.result)
        self.open_output = QPushButton('Open Output Folder')
        self.open_output.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.output.text())))
        self.layout.addWidget(self.open_output)
        self.inputs = [self.packages, self.import_button, self.output, self.browse, self.recommends, self.build_button, self.kind, self.python_version]
        self.drafts = {'apt': '', 'pip': ''}
        self.previous_kind = 'apt'
        self.kind.currentIndexChanged.connect(self.change_kind)
        self.change_kind()
        self.add_status()

    def change_kind(self):
        kind = self.kind.currentData()
        self.drafts[self.previous_kind] = self.packages.toPlainText()
        self.packages.setPlainText(self.drafts[kind])
        self.previous_kind = kind
        pip = kind == 'pip'
        self.python_label.setVisible(pip)
        self.python_version.setVisible(pip)
        self.recommends.setVisible(not pip)
        self.compatibility_note.setVisible(not pip)
        self.import_button.setText('Import requirements.txt…' if pip else 'Import Package List…')
        self.packages.setPlaceholderText('One PyPI requirement per line. Prebuilt wheels only.\ne.g. requests>=2.32\nhttpx[http2]' if pip else
            'Package names separated by whitespace. # comments supported.\ne.g. git cmake build-essential libssl-dev')
        self.result.clear()

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
        if self.busy:
            return
        names = [name for line in self.packages.toPlainText().splitlines() for name in line.split('#', 1)[0].split()]
        from offline_apt_builder.inputs import validate_packages
        try:
            if self.kind.currentData() == 'pip':
                from offline_apt_builder.pip_inputs import read_requirements
                requirements = read_requirements([self.packages.toPlainText()])
            else:
                validate_packages(names)
            if not self.output.text().strip():
                raise ValueError('Choose an output folder.')
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.result.clear()
        if self.kind.currentData() == 'pip':
            self.start('build', {'kind': 'pip', 'requirements': requirements, 'output': self.output.text(),
                                 'python_version': self.python_version.currentText()})
        else:
            self.start('build', {'packages': names, 'output': self.output.text(), 'with_recommends': self.recommends.isChecked()})

    def on_event(self, event):
        super().on_event(event)
        if event.get('type') == 'result':
            self.result.setText(f"Archive: {event['archive']}\nPackages: {event['package_count']} • Bytes: {event['size_bytes']:,}\nSHA-256: {event['sha256']}\nChecksum: {event['checksum']}\nReport: {event['report']}")

def main():
    return launch(SoozipWindow, "Soozip")
