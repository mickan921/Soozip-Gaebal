"""Opt-in real PyPI/Docker matrix, including offline worker round trips."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
from contextlib import contextmanager

from offline_apt_builder.models import PipBuildRequest
from offline_apt_builder.pip_build import build_pip
from offline_apt_builder.pip_inputs import PYTHON_VERSIONS
from pip_test_support import cached_assets

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def build_umask(mask):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)

# Runs inside an unprivileged network=none container. Only its /tmp is mutated.
OFFLINE = r'''
import json, os, pathlib, shutil, subprocess, sys
workspace = pathlib.Path('/tmp/imported')
workspace.mkdir()
def worker(operation, request, expected=0):
    command = ['/client/offline-apt-worker'] if pathlib.Path('/client/offline-apt-worker').exists() else [sys.executable, '-m', 'offline_apt_builder.worker']
    p = subprocess.run([*command, operation, '--request', json.dumps(request)], text=True, capture_output=True)
    print(p.stdout, flush=True)
    assert p.returncode == expected, p.stdout + p.stderr
    return [json.loads(line) for line in p.stdout.splitlines()]
events = worker('inspect', {'archive': '/input/bundle.7z', 'workspace': str(workspace)})
bundle = next(e['bundle'] for e in events if e['type'] == 'bundle')
target = {'python': sys.executable, 'venv': '/tmp/reviewed environment', 'create': True}
events = worker('preview', {'bundle': bundle, 'target': target})
assert not pathlib.Path(target['venv']).exists()
plan = next(e for e in events if e['type'] == 'plan')
assert all(a['action'] == 'install' for a in plan['actions'])
worker('install', {'bundle': bundle, 'target': target, 'approval_token': plan['approval_token']})
python = target['venv'] + '/bin/python'
checks = 'import requests,numpy,httpx,h2,rich,click,importlib.util,sys; assert numpy.arange(3).sum() == 3; assert bool(importlib.util.find_spec("tomli")) == (sys.version_info[:2] < (3,12))'
subprocess.run([python, '-I', '-c', checks], check=True)
subprocess.run([target['venv'] + '/bin/httpx', '--help'], check=True)
target['create'] = False
events = worker('preview', {'bundle': bundle, 'target': target})
plan = next(e for e in events if e['type'] == 'plan')
assert all(a['action'] == 'keep' for a in plan['actions'])
worker('install', {'bundle': bundle, 'target': target, 'approval_token': plan['approval_token']})
if pathlib.Path('/limited').is_dir():
    # Start with a complete, isolated environment so the failure exercises pip
    # writes, rather than interrupting creation of the Python executable itself.
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', '/limited/environment'], check=True)
    # Exhaust only a disposable, bounded tmpfs, never the host filesystem.
    with open('/limited/filler', 'wb') as filler:
        os.posix_fallocate(filler.fileno(), 0, shutil.disk_usage('/limited').free - 2 * 1024**2)
    limited = {'python': '/limited/environment/bin/python', 'venv': '/limited/environment', 'create': False}
    events = worker('preview', {'bundle': bundle, 'target': limited})
    plan = next(e for e in events if e['type'] == 'plan')
    events = worker('install', {'bundle': bundle, 'target': limited, 'approval_token': plan['approval_token']}, expected=1)
    assert any(e['type'] == 'error' for e in events), events
    assert not any(e.get('status') == 'INSTALLED' for e in events), events
    diagnostics = next(e['message'].removeprefix('Local diagnostics: ') for e in events
                       if e['type'] == 'log' and e['message'].startswith('Local diagnostics: '))
    failure_log = pathlib.Path(diagnostics).read_text()
    assert 'No space left on device' in failure_log, failure_log
    print('DISK_FULL_DIAGNOSTICS=' + failure_log, flush=True)
    pathlib.Path('/limited/filler').unlink()
    events = worker('preview', {'bundle': bundle, 'target': limited})
    plan = next(e for e in events if e['type'] == 'plan')
    worker('install', {'bundle': bundle, 'target': limited, 'approval_token': plan['approval_token']})
    subprocess.run([limited['venv'] + '/bin/python', '-I', '-c', checks], check=True)
    print('DISK_FULL_RECOVERY_ACCEPTED', flush=True)
print('OFFLINE_WORKER_ACCEPTED', flush=True)
'''


@unittest.skipUnless(os.environ.get('OFFLINE_PIP_ACCEPTANCE') == '1', 'Set OFFLINE_PIP_ACCEPTANCE=1 for the four-version PyPI/Docker matrix')
class PipAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(cached_assets())

    def test_python_matrix_and_offline_workers(self):
        output = Path(tempfile.mkdtemp(prefix='soozip-pip-matrix-'))
        print(f'Acceptance outputs: {output}', flush=True)
        selected = os.environ.get('OFFLINE_PIP_VERSIONS', ','.join(PYTHON_VERSIONS)).split(',')
        self.assertTrue(selected and set(selected) <= set(PYTHON_VERSIONS), selected)
        for version, mask in zip(PYTHON_VERSIONS, (0o022, 0o077, 0o027, 0o077)):
            if version not in selected:
                continue
            with self.subTest(python=version, umask=oct(mask)):
                with build_umask(mask):
                    result = build_pip(PipBuildRequest(('requests', 'numpy', 'httpx[http2,cli]',
                                        'tomli; python_version < "3.12"'), output, version, keep_workdir=True),
                                       progress=lambda e: print(f'{version} umask={mask:03o}: {e.stage}: {e.message}', flush=True))
                report = json.loads(result.report.read_text())
                image = 'pip-acceptance:' + uuid.uuid4().hex[:12]
                try:
                    subprocess.run(['docker', 'build', '--pull=false', '--build-arg',
                                    'BASE_IMAGE=' + report['base_image']['repo_digests'][0],
                                    '-t', image, str(result.workdir / 'context')], check=True, capture_output=True)
                    input_dir = result.workdir / 'input'
                    input_dir.mkdir()
                    shutil.copyfile(result.archive, input_dir / 'bundle.7z')
                    args = ['docker', 'run', '--rm', '--network', 'none', '--user', '65534:65534',
                            '--volume', f'{input_dir}:/input:ro', '--volume', f'{ROOT}:/source:ro',
                            '--volume', f'{ROOT / "build/release-assets"}:/tools:ro',
                            '--volume', f'{result.workdir / "offline-pip/tooling"}:/tooling:ro',
                            '--env', 'PATH=/tools:/opt/pip-python/bin:/usr/bin:/bin',
                            '--env', 'PYTHONPATH=/source/src:/tooling/packaging-26.3-py3-none-any.whl']
                    # When available, exercise the frozen Gaebal worker as well.
                    if os.environ.get('OFFLINE_PIP_PORTABLE') == '1':
                        args += ['--volume', f'{ROOT / "dist/Gaebal"}:/client:ro']
                    if version == '3.12':
                        args += ['--tmpfs', '/limited:rw,exec,size=128m,mode=1777']
                    process = subprocess.run([*args, '--entrypoint', '/opt/pip-python/bin/python3', image, '-c', OFFLINE],
                                             text=True, capture_output=True)
                    (output / f'python-{version}-worker.log').write_text(process.stdout + process.stderr)
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertIn('OFFLINE_WORKER_ACCEPTED', process.stdout)
                    if version == '3.12':
                        self.assertIn('DISK_FULL_RECOVERY_ACCEPTED', process.stdout)
                finally:
                    subprocess.run(['docker', 'image', 'rm', image], capture_output=True)
