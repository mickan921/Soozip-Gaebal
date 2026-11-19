"""Real transport, resolver failures, and process cancellation with cleanup."""
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from offline_apt_builder.models import BuildError, PipBuildRequest
from offline_apt_builder.pip_build import build_pip, download
from pip_test_support import cached_assets

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('OFFLINE_PIP_FAILURES') == '1', 'Set OFFLINE_PIP_FAILURES=1 for transport and Docker failure QA')
class PipFailureIntegrationTests(unittest.TestCase):
    def test_failed_real_resolution_never_publishes_archive(self):
        cases = [('3.12', ('idna==3.10', 'idna==3.11'), 'ResolutionImpossible'),
                 ('3.14', ('numpy==1.21.0',), 'No matching distribution found')]
        for version, requirements, diagnostic in cases:
            with self.subTest(python=version, requirements=requirements):
                evidence = Path(tempfile.mkdtemp(prefix='pip-resolution-failure-', dir=ROOT / 'build'))
                print('Resolution failure evidence: ' + str(evidence), flush=True)
                with cached_assets(), self.assertRaises(BuildError):
                    build_pip(PipBuildRequest(requirements, evidence, version))
                self.assertFalse(list(evidence.glob('*.7z*')))
                report = json.loads(next(evidence.glob('*.report.json')).read_text())
                self.assertEqual(report['status'], 'FAILED')
                self.assertIn(diagnostic, next(evidence.glob('*.build.log')).read_text())
                build_id = report['build_id']
                for command in (['docker', 'container', 'inspect', 'offline-pip-resolve-' + build_id],
                                ['docker', 'image', 'inspect', 'offline-pip-runtime:' + build_id]):
                    self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_truncated_http_download_fails_closed_and_can_retry(self):
        payload = b'Pinned download test payload\n' * 4096
        expected = hashlib.sha256(payload).hexdigest()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload if self.server.complete else payload[:256])
                self.close_connection = True

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.complete = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        evidence = Path(tempfile.mkdtemp(prefix='pip-download-', dir=ROOT / 'build'))
        print('Download interruption evidence: ' + str(evidence), flush=True)
        url = f'http://127.0.0.1:{server.server_port}/pinned-runtime'
        workspace = []

        def interrupted_runtime(_url, destination, _expected):
            workspace.append(destination.parent.parent)
            download(url, destination, expected)

        try:
            with patch('offline_apt_builder.pip_build.download', interrupted_runtime):
                with self.assertRaisesRegex(BuildError, 'checksum mismatch'):
                    build_pip(PipBuildRequest(('requests',), evidence))
            self.assertFalse(workspace[0].exists())
            self.assertFalse(list(evidence.glob('*.7z*')))
            self.assertEqual(json.loads(next(evidence.glob('*.report.json')).read_text())['status'], 'FAILED')
            server.complete = True
            retry = evidence / 'retried-download'
            download(url, retry, expected)
            self.assertEqual(retry.read_bytes(), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_cancel_active_resolver_cleans_container_workspace_and_output(self):
        evidence = Path(tempfile.mkdtemp(prefix='pip-cancellation-', dir=ROOT / 'build'))
        output = evidence / 'output'
        output.mkdir()
        logfile = evidence / 'worker.log'
        print('Cancellation evidence: ' + str(evidence), flush=True)
        with logfile.open('w') as stream:
            process = subprocess.Popen([sys.executable, str(ROOT / 'tests/fixtures/pip_cancel_build.py'), str(output)],
                                       stdout=stream, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 240
                job = None
                while time.monotonic() < deadline:
                    lines = logfile.read_text().splitlines()
                    for line in lines:
                        if line.startswith('QA_JOB='):
                            job = json.loads(line.removeprefix('QA_JOB='))
                    if 'QA_RESOLVER_STARTED' in lines:
                        break
                    if process.poll() is not None:
                        self.fail('Build exited before cancellation:\n' + logfile.read_text())
                    time.sleep(0.1)
                else:
                    self.fail('Timed out waiting for resolver:\n' + logfile.read_text())
                self.assertIsNotNone(job)
                process.terminate()
                self.assertEqual(process.wait(timeout=30), 130, logfile.read_text())
                inspect = subprocess.run(['docker', 'inspect', job['container']], capture_output=True)
                self.assertNotEqual(inspect.returncode, 0, 'Cancelled resolver container was left behind')
                self.assertFalse(Path(job['workspace']).exists())
                self.assertFalse(list(output.glob('*.7z*')))
                report = json.loads(next(output.glob('*.report.json')).read_text())
                self.assertEqual(report['status'], 'FAILED')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
