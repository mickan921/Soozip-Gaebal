"""Exercise the real portable-client shell installers with disposable payloads."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = Path('/etc/os-release')
SUPPORTED = (hasattr(os, 'geteuid') and os.geteuid() != 0 and RELEASE.is_file()
             and 'ID=ubuntu\n' in RELEASE.read_text() and 'VERSION_ID="24.04"' in RELEASE.read_text())


@unittest.skipUnless(SUPPORTED, 'Requires a non-root Ubuntu 24.04 test user')
class ClientInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='client-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / 'Install space # % $ ` " \\ test'
        self.bin = self.root / 'commands'
        self.bin.mkdir()
        self.calls = self.root / 'unexpected-calls'
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH']}
        # These are forbidden for an ordinary app installation, including Gaebal.
        for name in ('curl', 'wget', 'sudo', 'apt-get', 'pip', 'pip3'):
            self.command(name, f'printf "{name}\\n" >> "{self.calls}"; exit 97')
        self.command('docker', 'exit 1')
        self.command('pkexec', 'exit 98')

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)

    def payload(self, name):
        folder = self.root / ('Extracted # ' + name)
        (folder / '_internal/bin').mkdir(parents=True, exist_ok=True)
        for filename in (name, 'offline-apt-worker', '_internal/bin/7zz'):
            p = folder / filename
            p.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            p.chmod(0o755)
        (folder / 'release.json').write_text('{}\n')
        shutil.copy2(ROOT / name / 'install.sh', folder / 'install.sh')
        shutil.copyfile(ROOT / 'packaging/install-client.sh', folder / '_internal/install-client.sh')
        if name == 'Soozip':
            shutil.copyfile(ROOT / 'packaging/setup-docker.sh', folder / '_internal/setup-docker.sh')
        self.checksums(folder)
        return folder

    def checksums(self, folder):
        sums = []
        for p in sorted(folder.rglob('*')):
            if p.is_file() and p.name != 'CLIENT-SHA256SUMS':
                sums.append(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(folder)}\n')
        (folder / 'CLIENT-SHA256SUMS').write_text(''.join(sums))

    def run_installer(self, folder, *args, success=True):
        result = subprocess.run(['bash', str(folder / 'install.sh'), '--prefix', str(self.prefix), *args],
                                env=self.env, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_both_apps_install_update_and_remove_without_network(self):
        for name in ('Soozip', 'Gaebal'):
            with self.subTest(name=name):
                source = self.payload(name)
                (source / 'obsolete').write_text('old runtime')
                self.checksums(source)
                self.run_installer(source)
                target = self.prefix / 'share' / name.lower()
                launcher = self.prefix / 'bin' / name.lower()
                menu = self.prefix / 'share/applications' / (name.lower() + '.desktop')
                self.assertTrue((target / '_internal/bin/7zz').is_file())
                self.assertIn(f'Name={name}', menu.read_text())
                if shutil.which('desktop-file-validate'):
                    validated = subprocess.run(['desktop-file-validate', str(menu)], text=True, capture_output=True)
                    self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
                args = ['space # argument', '$(not-a-command)', '%F', '"quoted"']
                launched = subprocess.check_output([str(launcher), *args], text=True)
                self.assertEqual(launched.splitlines(), args)
                (source / 'obsolete').unlink()
                (source / 'new-runtime').write_text('new')
                self.checksums(source)
                self.run_installer(source)
                self.assertFalse((target / 'obsolete').exists())
                self.assertTrue((target / 'new-runtime').exists())
                # Rerunning from the installed folder is supported too.
                self.run_installer(target)
                self.run_installer(target, '--uninstall')
                self.assertFalse(target.exists())
                self.assertFalse(launcher.exists())
                self.assertFalse(menu.exists())
        self.assertFalse(self.calls.exists())

    def test_corrupt_update_preserves_installed_app(self):
        source = self.payload('Gaebal')
        self.run_installer(source)
        target = self.prefix / 'share/gaebal'
        original = (target / 'Gaebal').read_bytes()
        (source / 'Gaebal').write_text('damaged')
        self.run_installer(source, success=False)
        self.assertEqual((target / 'Gaebal').read_bytes(), original)

    def test_unmanaged_destinations_and_launchers_are_preserved(self):
        source = self.payload('Gaebal')
        target = self.prefix / 'share/gaebal'
        target.mkdir(parents=True)
        sentinel = target / 'user-data'
        sentinel.write_text('keep me')
        self.run_installer(source, success=False)
        self.run_installer(source, '--uninstall', success=False)
        self.assertEqual(sentinel.read_text(), 'keep me')
        shutil.rmtree(target)
        launcher = self.prefix / 'bin/gaebal'
        launcher.write_text('unrelated command')
        self.run_installer(source, success=False)
        self.assertEqual(launcher.read_text(), 'unrelated command')

    def test_failed_publication_rolls_back_app_and_launcher(self):
        source = self.payload('Gaebal')
        self.run_installer(source)
        target = self.prefix / 'share/gaebal'
        before = (target / 'Gaebal').read_bytes()
        (source / 'Gaebal').write_text('#!/bin/bash\necho updated\n')
        self.checksums(source)
        self.command('mv', 'for arg in "$@"; do if [[ $arg == */menu ]]; then exit 17; fi; done\nexec /usr/bin/mv "$@"')
        self.run_installer(source, success=False)
        self.assertEqual((target / 'Gaebal').read_bytes(), before)
        self.assertEqual(subprocess.check_output([str(self.prefix / 'bin/gaebal'), 'still works'], text=True), 'still works\n')
        self.assertFalse(list((self.prefix / 'share').glob('.gaebal-stage.*')))

    def test_docker_setup_is_opt_in_and_for_soozip_only(self):
        gaebal = self.payload('Gaebal')
        self.run_installer(gaebal, '--setup-docker', success=False)
        self.assertFalse(self.calls.exists())
        soozip = self.payload('Soozip')
        self.run_installer(soozip)
        self.assertFalse(self.calls.exists())
        self.run_installer(soozip, '--setup-docker', success=False)
        self.assertEqual(self.calls.read_text(), 'sudo\n')
        self.calls.unlink()
        self.command('docker', 'printf "linux\\n"')
        self.run_installer(soozip, '--setup-docker')
        self.assertFalse(self.calls.exists())

    def test_interrupted_publication_restores_previous_app(self):
        source = self.payload('Gaebal')
        self.run_installer(source)
        target = self.prefix / 'share/gaebal'
        original = (target / 'Gaebal').read_bytes()
        (source / 'Gaebal').write_text('#!/bin/bash\necho updated\n')
        self.checksums(source)
        # Interrupt after the new directory is moved but before mv returns.
        # Only this temporary installer subprocess receives the signal.
        self.command('mv', '/usr/bin/mv "$@"\nfor arg in "$@"; do if [[ $arg == */app ]]; then kill -TERM "$PPID"; fi; done')
        self.run_installer(source, success=False)
        self.assertEqual((target / 'Gaebal').read_bytes(), original)
        self.assertEqual(subprocess.check_output([str(self.prefix / 'bin/gaebal'), 'restored'], text=True), 'restored\n')
        self.assertFalse(list((self.prefix / 'share').glob('.gaebal-stage.*')))

    def test_source_checkout_fails_with_packaging_instructions(self):
        result = self.run_installer(ROOT / 'Gaebal', success=False)
        self.assertIn('complete portable Gaebal archive', result.stderr)


if __name__ == '__main__':
    unittest.main()
