"""Opt-in SDL upgrade acceptance on an actual older systemd/Mesa installation.

OFFLINE_APT_SDL=1 OFFLINE_APT_SDL_BUNDLE=/absolute/unpacked/offline-apt
Run on the online builder; only disposable containers are modified.
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
ENABLED = os.environ.get('OFFLINE_APT_SDL') == '1'
REQUESTED = {'libsdl2-dev', 'libsdl2-image-dev', 'libsdl2-mixer-dev', 'libsdl2-ttf-dev'}


@unittest.skipUnless(ENABLED, 'Set OFFLINE_APT_SDL=1 and OFFLINE_APT_SDL_BUNDLE for real SDL upgrade acceptance')
class SDLAcceptanceTests(unittest.TestCase):
    def test_old_systemd_and_mesa_upgrade_offline_without_removals(self):
        bundle = Path(os.environ['OFFLINE_APT_SDL_BUNDLE']).resolve()
        manifest = verify_bundle(bundle)
        self.assertEqual(set(manifest['requested']), REQUESTED)
        evidence = Path(tempfile.mkdtemp(prefix='sdl-acceptance-', dir=ROOT / 'build'))
        print(f'\nSDL acceptance evidence: {evidence}', flush=True)
        suffix = uuid.uuid4().hex[:12]
        seed, target, image = 'sdl-seed-' + suffix, 'sdl-target-' + suffix, 'soozip-sdl-seed:' + suffix
        prepared_image = os.environ.get('OFFLINE_APT_SDL_SEED_IMAGE')
        if prepared_image:
            image = prepared_image
        def docker(*args, check=True):
            result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if check and result.returncode:
                self.fail(result.stdout[-6000:])
            return result
        if not prepared_image:
            self.addCleanup(docker, 'image', 'rm', image, check=False)
        self.addCleanup(docker, 'rm', '--force', target, check=False)
        self.addCleanup(docker, 'rm', '--force', seed, check=False)
        # Noble's original package versions are retained in the release pocket.
        # No desktop inventory is imported from an offline machine.
        seed_script = '''set -eu
export DEBIAN_FRONTEND=noninteractive
cp /ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources
apt-get update
apt-get install -y --no-install-recommends --no-remove --allow-downgrades \\
  systemd=255.4-1ubuntu8 systemd-dev=255.4-1ubuntu8 systemd-sysv=255.4-1ubuntu8 \\
  libsystemd0=255.4-1ubuntu8 libsystemd-shared=255.4-1ubuntu8 \\
  udev=255.4-1ubuntu8 libudev1=255.4-1ubuntu8 libpam-systemd=255.4-1ubuntu8 \\
  libnss-systemd=255.4-1ubuntu8 systemd-resolved=255.4-1ubuntu8 \\
  systemd-timesyncd=255.4-1ubuntu8 systemd-oomd=255.4-1ubuntu8 \\
  mesa-va-drivers=24.0.5-1ubuntu1 mesa-vdpau-drivers=24.0.5-1ubuntu1 \\
  mesa-vulkan-drivers=24.0.5-1ubuntu1 libgl1-mesa-dri=24.0.5-1ubuntu1 \\
  libglx-mesa0=24.0.5-1ubuntu1 libegl-mesa0=24.0.5-1ubuntu1 \\
  libgbm1=24.0.5-1ubuntu1 libglapi-mesa=24.0.5-1ubuntu1 alsa-base xserver-xorg-core
apt-get check
test -z "$(dpkg --audit)"
'''
        if not prepared_image:
            docker('create', '--name', seed, '--pull', 'never', '--volume',
                   f'{ROOT}/src/offline_apt_builder/resources/ubuntu.sources:/ubuntu.sources:ro',
                   manifest['base_image']['id'], 'bash', '-c', seed_script)
            preparation = docker('start', '--attach', seed, check=False)
            (evidence / 'prepare.log').write_text(preparation.stdout)
            code = int(docker('inspect', '--format', '{{.State.ExitCode}}', seed).stdout)
            self.assertEqual(code, 0, preparation.stdout[-6000:])
            docker('commit', seed, image)
        baseline = docker('run', '--rm', '--network', 'none', '--pull', 'never', image, 'dpkg-query', '-W',
                          '-f=${Package} ${Version}\n', 'systemd', 'udev', 'mesa-va-drivers').stdout
        (evidence / 'baseline-versions.txt').write_text(baseline)
        self.assertEqual(set(baseline.splitlines()), {'systemd 255.4-1ubuntu8', 'udev 255.4-1ubuntu8',
                                                    'mesa-va-drivers 24.0.5-1ubuntu1'})
        script = r'''set -eu
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' > /tmp/before-packages
sha256sum /var/lib/dpkg/status > /tmp/before-status
bash /bundle/install.sh --preview > /tmp/preview
cat /tmp/preview
sha256sum --check /tmp/before-status
! grep -q '^Remv ' /tmp/preview
token=$(awk -F '\t' '$1 == "APPROVAL" {print $2}' /tmp/preview)
test -n "$token"
bash /bundle/install.sh --yes --allow-upgrades --approval-token="$token"
apt-get check
test -z "$(dpkg --audit)"
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' > /tmp/after-packages
awk -F '\t' '$2 == "installed" {print $1}' /tmp/before-packages | sort > /tmp/before-names
awk -F '\t' '$2 == "installed" {print $1}' /tmp/after-packages | sort > /tmp/after-names
test -z "$(comm -23 /tmp/before-names /tmp/after-names)"
sha256sum /var/lib/dpkg/status > /tmp/installed-status
bash /bundle/install.sh --yes
sha256sum --check /tmp/installed-status
echo SDL_OFFLINE_UPGRADE_AND_REPEAT_OK
'''
        docker('create', '--name', target, '--network', 'none', '--pull', 'never', '--volume', f'{bundle}:/bundle:ro',
               image, 'bash', '-c', script)
        network = docker('inspect', '--format', '{{.HostConfig.NetworkMode}}', target).stdout.strip()
        self.assertEqual(network, 'none')
        result = docker('start', '--attach', target, check=False)
        (evidence / 'offline-install.log').write_text(result.stdout)
        code = int(docker('inspect', '--format', '{{.State.ExitCode}}', target).stdout)
        for filename in ('before-packages', 'after-packages', 'preview'):
            docker('cp', f'{target}:/tmp/{filename}', str(evidence / filename), check=False)
        (evidence / 'report.json').write_text(json.dumps({'network_mode': network, 'exit_code': code,
            'build_id': manifest['build_id'], 'requested': sorted(REQUESTED),
            'status': 'PASSED' if code == 0 else 'FAILED'}, indent=2) + '\n')
        self.assertEqual(code, 0, result.stdout[-8000:])
        self.assertIn('SDL_OFFLINE_UPGRADE_AND_REPEAT_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
