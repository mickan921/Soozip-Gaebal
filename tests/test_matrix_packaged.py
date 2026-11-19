"""Portable Gaebal acceptance for a newly produced matrix-validated archive."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from offline_apt_builder import __version__
from offline_apt_builder.validator import POLICY

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('OFFLINE_APT_MATRIX') == '1', 'Set OFFLINE_APT_MATRIX=1 and OFFLINE_APT_MATRIX_ARCHIVE')
class MatrixBundleAcceptanceTests(unittest.TestCase):
    def test_portable_worker_verifies_matrix_previews_and_installs_offline(self):
        client = ROOT / 'dist/Gaebal'
        self.assertEqual(json.loads((client / 'release.json').read_text())['version'], __version__)
        archive = Path(os.environ['OFFLINE_APT_MATRIX_ARCHIVE']).resolve()
        script = r'''set -eu
mkdir -m 777 /work
su nobody -s /bin/bash -c '/client/offline-apt-worker preview --request "{\"archive\":\"/input.7z\",\"workspace\":\"/work\"}"' >/tmp/preview
cat /tmp/preview
token=$(sed -n 's/.*"approval_token": "\([a-f0-9]*\)".*/\1/p' /tmp/preview)
test -n "$token"
/client/offline-apt-worker install --request "{\"bundle\":\"/work/offline-apt\",\"approval_token\":\"$token\",\"upgrades\":true}"
apt-get check
test -z "$(dpkg --audit)"
'''
        result = subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
            '--volume', f'{client}:/client:ro', '--volume', f'{archive}:/input.7z:ro',
            'ubuntu:24.04', 'bash', '-c', script], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        bundle = next(event for event in events if event['type'] == 'bundle')
        self.assertEqual(bundle['manifest']['validation_policy'], POLICY)
        self.assertGreaterEqual(len(bundle['validation']['scenarios']), 4)
        self.assertTrue(all(s['status'] == 'PASSED' for s in bundle['validation']['scenarios']))
        self.assertTrue(any(event.get('status') == 'INSTALLED' for event in events))
        (ROOT / 'build/matrix-packaged-worker.jsonl').write_text(result.stdout)


if __name__ == '__main__':
    unittest.main()
