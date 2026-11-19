"""Opt-in acceptance of the JACK2 preparation followed by the released SDL bundle.

OFFLINE_APT_JACK2=1 OFFLINE_APT_SDL_BUNDLE=/unpacked/sdl/offline-apt
OFFLINE_APT_JACK2_BUNDLE=/unpacked/preparation/offline-apt
Only a disposable, network-disabled container is modified.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from offline_apt_builder.install import verify_bundle

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('OFFLINE_APT_JACK2') == '1', 'Set OFFLINE_APT_JACK2=1 and both bundle paths')
class Jack2PreparationAcceptanceTests(unittest.TestCase):
    def test_prepare_existing_jack2_then_install_original_sdl_without_removals(self):
        sdl = Path(os.environ['OFFLINE_APT_SDL_BUNDLE']).resolve()
        preparation = Path(os.environ['OFFLINE_APT_JACK2_BUNDLE']).resolve()
        manifest = verify_bundle(sdl)
        helper = verify_bundle(preparation)
        self.assertEqual(set(manifest['requested']),
                         {'libsdl2-dev', 'libsdl2-image-dev', 'libsdl2-mixer-dev', 'libsdl2-ttf-dev'})
        self.assertEqual(helper['requested'], ['libjack-jackd2-dev'])
        evidence = Path(tempfile.mkdtemp(prefix='jack2-acceptance-', dir=ROOT / 'build'))
        print(f'\nJACK2 acceptance evidence: {evidence}', flush=True)
        name = 'jack2-acceptance-' + uuid.uuid4().hex[:12]

        def docker(*args, check=True):
            result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if check and result.returncode:
                self.fail(result.stdout[-6000:])
            return result

        self.addCleanup(docker, 'rm', '--force', name, check=False)
        script = r'''set -eu
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
apt-get check
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' >/tmp/before-packages
sha256sum /var/lib/dpkg/status >/tmp/before-status
if bash /sdl/install.sh --preview >/tmp/original-preview 2>&1; then
  echo 'Expected the released SDL bundle to reproduce the JACK2 removal conflict.' >&2
  exit 91
fi
cat /tmp/original-preview
grep -q '^  libjack-jackd2-0$' /tmp/original-preview
grep -q 'Packages need to be removed but remove is disabled' /tmp/original-preview
sha256sum --check /tmp/before-status

bash /preparation/install.sh --preview >/tmp/preparation-preview
sha256sum --check /tmp/before-status
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/preparation-preview)
test -n "$token"
bash /preparation/install.sh --yes --allow-upgrades --approval-token="$token"
apt-get check
test "$(dpkg-query -W -f='${db:Status-Status}' libjack-jackd2-dev)" = installed
sha256sum /var/lib/dpkg/status >/tmp/prepared-status
bash /preparation/install.sh --yes
sha256sum --check /tmp/prepared-status

bash /sdl/install.sh --preview >/tmp/prepared-preview
sha256sum --check /tmp/prepared-status
! grep -q '^Remv ' /tmp/prepared-preview
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/prepared-preview)
test -n "$token"
bash /sdl/install.sh --yes --allow-upgrades --approval-token="$token"
apt-get check
test -z "$(dpkg --audit)"
test "$(dpkg-query -W -f='${db:Status-Status}' libjack-jackd2-0)" = installed
test "$(dpkg-query -W -f='${db:Status-Status}' libjack0 2>/dev/null || true)" != installed
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' >/tmp/after-packages
awk -F '\t' '$2 == "installed" {print $1}' /tmp/before-packages | sort >/tmp/before-names
awk -F '\t' '$2 == "installed" {print $1}' /tmp/after-packages | sort >/tmp/after-names
test -z "$(comm -23 /tmp/before-names /tmp/after-names)"
sha256sum /var/lib/dpkg/status >/tmp/installed-status
bash /sdl/install.sh --yes
sha256sum --check /tmp/installed-status
echo JACK2_PREPARATION_AND_ORIGINAL_SDL_OFFLINE_OK
'''
        docker('create', '--name', name, '--network', 'none', '--pull', 'never',
               '--volume', f'{sdl}:/sdl:ro', '--volume', f'{preparation}:/preparation:ro',
               manifest['base_image']['id'], 'bash', '-c', script)
        self.assertEqual(docker('inspect', '--format', '{{.HostConfig.NetworkMode}}', name).stdout.strip(), 'none')
        result = docker('start', '--attach', name, check=False)
        (evidence / 'installation.log').write_text(result.stdout)
        code = int(docker('inspect', '--format', '{{.State.ExitCode}}', name).stdout)
        for filename in ('before-packages', 'after-packages', 'original-preview', 'preparation-preview', 'prepared-preview'):
            docker('cp', f'{name}:/tmp/{filename}', str(evidence / filename), check=False)
        (evidence / 'report.json').write_text(json.dumps({
            'status': 'PASSED' if code == 0 else 'FAILED', 'exit_code': code, 'network_mode': 'none',
            'sdl_build_id': manifest['build_id'], 'preparation_build_id': helper['build_id'],
            'checks': ['original removal failure reproduced', 'failed preview leaves dpkg state unchanged',
                       'preparation and SDL previews leave dpkg state unchanged',
                       'JACK2 retained', 'zero package removals', 'apt-get check and dpkg audit',
                       'repeat preparation and SDL installation leave dpkg state unchanged']
        }, indent=2) + '\n')
        self.assertEqual(code, 0, result.stdout[-8000:])
        self.assertIn('JACK2_PREPARATION_AND_ORIGINAL_SDL_OFFLINE_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
