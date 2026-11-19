"""Opt-in black-box X11 tests of installed portable clients on Ubuntu 24.04."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('OFFLINE_PIP_DESKTOP') == '1',
                     'Set OFFLINE_PIP_DESKTOP=1 for clean packaged X11 desktop QA')
class PackagedPipDesktopTests(unittest.TestCase):
    def test_online_build_and_offline_install_through_installed_guis(self):
        self.assertNotEqual(os.getuid(), 0, 'Run desktop QA as your normal user')
        for app in ('Soozip', 'Gaebal'):
            self.assertTrue((ROOT / 'dist' / (app + '-ubuntu24.04-amd64.tar.gz')).is_file(),
                            'Build both portable clients first')
        docker = Path(shutil.which('docker') or '/usr/bin/docker').resolve()
        endpoint = os.environ.get('DOCKER_HOST') or subprocess.check_output(
            ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
        self.assertTrue(endpoint.startswith('unix://'), 'Desktop QA requires a local Docker socket')
        socket = Path(endpoint.removeprefix('unix://'))
        plugins = json.loads(subprocess.check_output(
            ['docker', 'info', '--format', '{{json .ClientInfo.Plugins}}'], text=True))
        buildx = next(Path(p['Path']) for p in plugins if p['Name'] == 'buildx')
        evidence = Path(tempfile.mkdtemp(prefix='pip-desktop-qa-', dir=ROOT / 'build')).resolve()
        print('Desktop QA evidence: ' + str(evidence), flush=True)
        online, offline, shared = [evidence / name for name in ('online', 'offline', 'shared')]
        for directory in (online, offline, shared):
            directory.mkdir()
        suffix = uuid.uuid4().hex[:12]
        image = 'pip-desktop-qa:' + suffix
        fixture = ROOT / 'tests/fixtures/pip_desktop'

        def run(command, logfile, timeout=1200):
            with (evidence / logfile).open('w') as stream:
                process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            self.assertEqual(process.returncode, 0, (evidence / logfile).read_text()[-15000:])

        try:
            run(['docker', 'build', '--build-arg', 'QA_UID=' + str(os.getuid()),
                 '--build-arg', 'QA_GID=' + str(os.getgid()), '-t', image, str(fixture)], 'image.log')
            for app, directory in (('soozip', online), ('gaebal', offline)):
                name = 'pip-desktop-' + app + '-' + suffix
                args = ['docker', 'run', '--rm', '--name', name,
                        '-v', f'{ROOT / "dist"}:/releases:ro', '-v', f'{fixture}:/qa:ro',
                        '-v', f'{directory}:/evidence']
                if app == 'soozip':
                    # Builder paths must exist identically on the local daemon's
                    # host for the nested resolver's read-only bind mounts.
                    args += ['--group-add', str(socket.stat().st_gid),
                             '-v', f'{socket}:/var/run/docker.sock',
                             '-v', f'{docker}:/usr/local/bin/docker:ro',
                             '-v', f'{buildx}:/usr/libexec/docker/cli-plugins/docker-buildx:ro',
                             '-v', f'{shared}:{shared}', '-e', 'TMPDIR=' + str(shared)]
                else:
                    args += ['--network', 'none', '-v', f'{online / "bundles"}:/input:ro']
                try:
                    run([*args, image, 'bash', '/qa/session.sh', app + ('-build' if app == 'soozip' else '-install')],
                        app + '.log')
                    result = json.loads((directory / 'result.json').read_text())
                    self.assertEqual(result['status'], 'PASSED')
                    self.assertNotEqual(result['uid'], 0)
                finally:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True)
        finally:
            subprocess.run(['docker', 'image', 'rm', image], capture_output=True)
