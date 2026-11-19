"""Real portable-client acceptance: keep JACK2 with an unchanged SDL bundle.

OFFLINE_APT_JACK2_AUTO=1 OFFLINE_APT_SDL_BUNDLE=/unpacked/offline-apt
Requires current dist/Gaebal. Only a disposable offline container is modified.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from offline_apt_builder import __version__
from offline_apt_builder.archive import inventory
from offline_apt_builder.install import verify_bundle

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('OFFLINE_APT_JACK2_AUTO') == '1', 'Set OFFLINE_APT_JACK2_AUTO=1 and SDL bundle path')
class AutomaticJack2AcceptanceTests(unittest.TestCase):
    def test_packaged_gaebal_installs_sdl_and_keeps_jack2_without_preparation(self):
        bundle = Path(os.environ['OFFLINE_APT_SDL_BUNDLE']).resolve()
        manifest = verify_bundle(bundle)
        before_bundle = inventory(bundle)
        self.assertEqual(set(manifest['requested']),
                         {'libsdl2-dev', 'libsdl2-image-dev', 'libsdl2-mixer-dev', 'libsdl2-ttf-dev'})
        client = ROOT / 'dist/Gaebal'
        self.assertEqual(json.loads((client / 'release.json').read_text())['version'], __version__)
        evidence = Path(tempfile.mkdtemp(prefix='jack2-automatic-', dir=ROOT / 'build'))
        print(f'\nAutomatic JACK2 evidence: {evidence}', flush=True)
        name = 'jack2-auto-' + uuid.uuid4().hex[:12]

        def docker(*args, check=True):
            result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if check and result.returncode:
                self.fail(result.stdout[-8000:])
            return result

        self.addCleanup(docker, 'rm', '--force', name, check=False)
        docker('run', '-d', '--name', name, '--network', 'none', '--pull', 'never',
               '--volume', f'{bundle}:/sdl:ro', '--volume', f'{client}:/client:ro',
               manifest['base_image']['id'], 'sleep', 'infinity')
        self.assertEqual(docker('inspect', '--format', '{{.HostConfig.NetworkMode}}', name).stdout.strip(), 'none')
        setup = r'''set -eu
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
mkdir -p /tmp/seed/empty /tmp/seed/lists/partial /tmp/seed/archives/partial
printf 'deb [trusted=yes] file:/sdl/repo ./\n' >/tmp/seed/sources.list
cat >/tmp/seed/apt.conf <<'CONF'
Dir::Etc::parts "/tmp/seed/empty";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "/tmp/seed/sources.list";
Dir::Etc::sourceparts "/tmp/seed/empty";
Dir::State::lists "/tmp/seed/lists";
Dir::Cache::archives "/tmp/seed/archives";
APT::Install-Recommends "false";
APT::Sandbox::User "root";
CONF
export APT_CONFIG=/tmp/seed/apt.conf
apt-get update
apt-get -y --no-remove install libjack-jackd2-0
apt-mark auto libjack-jackd2-0
test "$(dpkg-query -W -f='${db:Status-Status}' libjack-jackd2-dev 2>/dev/null || true)" != installed
apt-get check
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' >/tmp/before-packages
sha256sum /var/lib/dpkg/status /var/lib/apt/extended_states >/tmp/before-state
'''
        (evidence / 'setup.log').write_text(docker('exec', name, 'bash', '-c', setup).stdout)

        def worker(operation, request, log, unprivileged=False):
            args = ['exec']
            if unprivileged:
                args += ['--user', 'nobody']
            result = docker(*args, name, '/client/offline-apt-worker', operation, '--request', json.dumps(request))
            (evidence / log).write_text(result.stdout)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertFalse([event for event in events if event['type'] == 'error'])
            return events

        events = worker('preview', {'bundle': '/sdl'}, 'preview.jsonl', unprivileged=True)
        self.assertEqual(events[-1]['status'], 'READY')
        plan = next(event for event in events if event['type'] == 'plan')
        self.assertTrue(any(a['name'].split(':')[0] == 'libjack-jackd2-0' and a['action'] == 'keep'
                            for a in plan['actions']))
        self.assertTrue(any(a['name'].split(':')[0] == 'libjack-jackd2-dev' and a['action'] == 'install'
                            for a in plan['actions']))
        self.assertFalse(any(a['name'].split(':')[0] in {'libjack0', 'libjack-dev'} for a in plan['actions']))
        docker('exec', name, 'sha256sum', '--check', '/tmp/before-state')
        installed = worker('install', {'bundle': '/sdl', 'approval_token': plan['approval_token'],
                                      'upgrades': True, 'downgrades': False}, 'install.jsonl')
        self.assertEqual(installed[-1]['status'], 'INSTALLED')
        verify = r'''set -eu
apt-get check
test -z "$(dpkg --audit)"
test "$(dpkg-query -W -f='${db:Status-Status}' libjack-jackd2-0)" = installed
test "$(dpkg-query -W -f='${db:Status-Status}' libjack-jackd2-dev)" = installed
test "$(dpkg-query -W -f='${db:Status-Status}' libjack0 2>/dev/null || true)" != installed
apt-mark showauto libjack-jackd2-0 | grep -qx libjack-jackd2-0
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' >/tmp/after-packages
awk -F '\t' '$2 == "installed" {print $1}' /tmp/before-packages | sort >/tmp/before-names
awk -F '\t' '$2 == "installed" {print $1}' /tmp/after-packages | sort >/tmp/after-names
test -z "$(comm -23 /tmp/before-names /tmp/after-names)"
sha256sum /var/lib/dpkg/status /var/lib/apt/extended_states >/tmp/installed-state
'''
        (evidence / 'verify.log').write_text(docker('exec', name, 'bash', '-c', verify).stdout)
        repeated = worker('preview', {'bundle': '/sdl'}, 'repeat-preview.jsonl', unprivileged=True)
        plan = next(event for event in repeated if event['type'] == 'plan')
        self.assertTrue(all(action['action'] == 'keep' for action in plan['actions']))
        worker('install', {'bundle': '/sdl', 'approval_token': plan['approval_token'],
                           'upgrades': False, 'downgrades': False}, 'repeat-install.jsonl')
        docker('exec', name, 'sha256sum', '--check', '/tmp/installed-state')
        for filename in ('before-packages', 'after-packages'):
            docker('cp', f'{name}:/tmp/{filename}', str(evidence / filename))
        self.assertEqual(before_bundle, inventory(bundle))
        (evidence / 'report.json').write_text(json.dumps({
            'status': 'PASSED', 'client_version': __version__, 'network_mode': 'none',
            'sdl_build_id': manifest['build_id'], 'preparation_bundle_used': False,
            'original_bundle_unchanged': True, 'package_removals': 0,
            'automatic_flag_preserved': True, 'dependency_check': True,
            'repeat_package_state_unchanged': True
        }, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
