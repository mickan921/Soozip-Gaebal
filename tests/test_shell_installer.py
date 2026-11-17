"""Run the shipped shell installer with isolated APT command fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_transport import valid_bundle


SUPPORTED = (Path('/etc/os-release').is_file()
             and 'VERSION_ID="24.04"' in Path('/etc/os-release').read_text()
             and 'ID=ubuntu\n' in Path('/etc/os-release').read_text())


@unittest.skipUnless(SUPPORTED, 'Requires Ubuntu 24.04 shell tools')
class ShellInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='installer-pipeline-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle with spaces #'
        valid_bundle(self.bundle)
        self.bin = self.root / 'commands'
        self.bin.mkdir()
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH']}
        self.command('apt-get', 'printf "APT fixture completed\\n"')
        self.command('apt-mark', 'exit 0')
        self.command('dpkg-query', "printf 'installed\\t1'")

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)

    def run_preview(self):
        return subprocess.run(['bash', str(self.bundle / 'install.sh'), '--preview'],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def test_large_policy_output_is_drained_without_sigpipe(self):
        for row in ('     1 500', ' *** 1 500'):
            with self.subTest(row=row):
                # The producer must write much more than a pipe buffer after the
                # match. Early awk exit used to kill it with SIGPIPE (exit 141).
                self.command('apt-cache', f'''printf '%s\\n' '{row}'
for ((i=0; i<20000; i++)); do printf '        500 file:/fixture ./ Packages\\n'; done
printf '     1 -1\\n'
''')
                result = self.run_preview()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('APPROVAL\t', result.stdout)

    def test_missing_or_forbidden_policy_still_stops_preview(self):
        for row, message in (('     2 500', 'Cannot determine target APT policy'),
                             (' *** 1 -1', 'pinning forbids')):
            with self.subTest(row=row):
                self.command('apt-cache', f"printf '%s\\n' '{row}'")
                result = self.run_preview()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn('APPROVAL\t', result.stdout)

    def test_policy_command_failure_is_not_hidden(self):
        self.command('apt-cache', "printf '     1 500\\n'; exit 42")
        result = self.run_preview()
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn('stage: policy', result.stderr)
        self.assertNotIn('APPROVAL\t', result.stdout)

    def test_checksum_corruption_still_stops_before_apt(self):
        (self.bundle / 'packages.txt').write_text('changed\n')
        result = self.run_preview()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum verification failed', result.stderr)
        self.assertNotIn('APT fixture completed', result.stdout)

    def test_existing_versions_are_classified_without_failure(self):
        self.command('apt-cache', "printf ' *** 1 500\\n'")
        for status, old, action in (('', '', 'install'), ('installed', '0.9', 'upgrade'),
                                    ('installed', '1', 'keep'), ('installed', '2', 'downgrade'),
                                    ('installed', '1:0.1', 'downgrade'),
                                    ('unpacked', '2', 'downgrade'), ('half-configured', '0.9', 'upgrade')):
            with self.subTest(old=old, action=action):
                self.command('dpkg-query', f"printf '{status}\\t{old}'")
                simulation = '' if action == 'keep' else 'Inst example (1 local [amd64])\\nConf example (1 local [amd64])\\n'
                self.command('apt-get', f'''if [[ "$1" == --simulate ]]; then printf '{simulation}'; fi''')
                result = self.run_preview()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'PLAN\texample\t{action}\t{old}\t1\n', result.stdout)
                self.assertEqual(result.stdout.count('PLAN\t'), 1)

    def test_configuration_only_transaction_is_not_reported_as_already_installed(self):
        self.command('apt-cache', "printf ' *** 1 500\\n'")
        self.command('dpkg-query', "printf '1'")
        self.command('apt-get', '''if [[ "$1" == --simulate ]]; then printf 'Conf example (1 local [amd64])\\n'; fi''')
        result = self.run_preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PLAN\texample\tconfigure\t1\t1', result.stdout)
        self.assertNotIn('\tkeep\t', result.stdout)

    def test_dependency_conflict_has_actionable_message_and_no_approval(self):
        self.command('apt-cache', "printf '     1 500\\n'")
        self.command('apt-get', '''if [[ "$1" == --simulate ]]; then echo 'E: Unmet dependencies' >&2; exit 100; fi''')
        result = self.run_preview()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot safely install these bundle versions', result.stderr)
        self.assertIn('No packages were changed', result.stderr)
        self.assertIn('Unmet dependencies', result.stdout)
        self.assertNotIn('APPROVAL\t', result.stdout)

    def test_held_matching_version_is_kept_but_changes_are_refused(self):
        self.command('apt-cache', "printf ' *** 1 500\\n'")
        self.command('apt-mark', "echo example")
        result = self.run_preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PLAN\texample\tkeep\t1\t1', result.stdout)
        self.command('apt-get', '''if [[ "$1" == --simulate ]]; then printf 'Inst example (1 local [amd64])\\n'; fi''')
        result = self.run_preview()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Changing held package', result.stderr)
        self.assertNotIn('APPROVAL\t', result.stdout)

    def test_missing_target_action_is_not_mistaken_for_no_changes(self):
        self.command('apt-cache', "printf '     1 500\\n'")
        self.command('dpkg-query', 'exit 1')
        result = self.run_preview()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('APT did not plan installation', result.stderr)
        self.assertNotIn('APPROVAL\t', result.stdout)


if __name__ == '__main__':
    unittest.main()
