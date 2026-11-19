import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from offline_apt_builder.install import removal_diagnostics, removal_summary, run_installer
from offline_apt_builder.models import BuildError
from offline_apt_builder.resources.apt_compatibility import PackageVersion, expand_companions, parse_simulation
from offline_apt_builder.worker import main


def package(name, version='2', source='family', dependencies=(), source_version=None):
    return PackageVersion(name, version, 'all', source, source_version or version, 10, dependencies)


class CompanionTests(unittest.TestCase):
    def test_apt_simulation_accepts_broken_state_annotations(self):
        value = package('libc6', '2.39-0ubuntu8.12')
        available = {(value.name, value.version): value}
        for line in ('Inst libc6 (2.39-0ubuntu8.12 Ubuntu:24.04/noble-updates [amd64]) []',
                     'Inst libc6:amd64 [2.39-0ubuntu8] (2.39-0ubuntu8.12 local [amd64]) [other:amd64 ]'):
            self.assertEqual(parse_simulation(line, ['libc6=2.39-0ubuntu8.12'], available), [value])
        for line in ('Inst libc6 invalid', 'Inst libc6 (999 local [amd64])', 'Remv libc6 [2]', ''):
            with self.assertRaises(ValueError):
                parse_simulation(line, ['libc6=2.39-0ubuntu8.12'], available)

    def test_recursive_forward_and_reverse_closure_keeps_alternatives_independent(self):
        runtime = package('runtime')
        headers = package('headers', dependencies=(('runtime', '2'),))
        shared = package('shared')
        daemon = package('daemon', dependencies=(('runtime', '2'), ('shared', '2')))
        plugin = package('plugin', dependencies=(('shared', '2'),))
        alternative = package('alternative', dependencies=(('runtime', '2'),))
        # These stand for separate APT transactions: daemon and alternative
        # conflict with each other but both can be available in the repository.
        plans = {'daemon': (daemon, runtime, shared), 'plugin': (plugin, shared),
                 'alternative': (alternative, runtime)}
        calls = []
        def resolve(peer):
            calls.append(peer.name)
            return plans[peer.name]
        result, reasons = expand_companions((headers, runtime),
                                           (headers, runtime, shared, daemon, plugin, alternative), resolve)
        self.assertEqual({p.name for p in result}, {'headers', 'runtime', 'shared', 'daemon', 'plugin', 'alternative'})
        self.assertCountEqual(calls, ['daemon', 'plugin', 'alternative'])
        self.assertIn('exact-version companion of shared=2', reasons['plugin'])
        self.assertIn('dependency of compatibility package daemon=2', reasons['shared'])

    def test_source_version_and_exact_dependency_must_match(self):
        runtime = package('runtime')
        unrelated = (package('other-source', source='elsewhere', dependencies=(('runtime', '2'),)),
                     package('other-version', '3', dependencies=(('runtime', '2'),)),
                     package('old-dependency', dependencies=(('runtime', '1'),)))
        result, _ = expand_companions((runtime,), (runtime, *unrelated), lambda _: self.fail('Unexpected companion'))
        self.assertEqual(result, (runtime,))

    def test_cycles_are_deduplicated_and_order_is_stable(self):
        first = package('first', dependencies=(('second', '2'),))
        second = package('second', dependencies=(('first', '2'),))
        calls = []
        def resolve(peer):
            calls.append(peer.name)
            return (second, first)
        result, _ = expand_companions((first,), (second, first, second), resolve)
        self.assertEqual(result, (first, second))
        self.assertEqual(calls, ['second'])

    def test_incompatible_dependency_version_fails_instead_of_overwriting(self):
        runtime = package('runtime')
        peer = package('peer', dependencies=(('runtime', '2'),))
        with self.assertRaisesRegex(ValueError, 'conflicting versions of runtime'):
            expand_companions((runtime,), (runtime, peer), lambda _: (peer, package('runtime', '1')))

    def test_missing_companion_is_not_silently_accepted(self):
        runtime = package('runtime')
        peer = package('peer', dependencies=(('runtime', '2'),))
        with self.assertRaisesRegex(ValueError, 'APT omitted'):
            expand_companions((runtime,), (runtime, peer), lambda _: (runtime,))


class RemovalDiagnosticTests(unittest.TestCase):
    def test_wrapped_removal_list_and_machine_records_are_deduplicated(self):
        result = removal_diagnostics('The following packages will be REMOVED:\n  gdm3 udev\n  libexample:i386\n'
                                     'The following packages will be upgraded:\n  other\nRemv udev [1]\n')
        self.assertEqual(result, {'category': 'removal-required', 'removed_packages': ['gdm3', 'udev', 'libexample:i386']})

    def test_other_failures_are_not_classified_as_removals(self):
        self.assertIsNone(removal_diagnostics('E: Unmet dependencies\n'))
        self.assertEqual(removal_diagnostics('E: Packages need to be removed but remove is disabled')['removed_packages'], [])

    def test_full_removal_list_survives_error_tail_truncation(self):
        names = [f'package-{index:03d}' for index in range(120)]
        output = ('The following packages will be REMOVED:\n  ' + ' '.join(names) + '\n'
                  'The following packages will be upgraded:\n  updated\n' + 'details\n' * 1000
                  + 'E: Packages need to be removed but remove is disabled\n')
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            (bundle / 'install.sh').write_text("cat <<'APT_OUTPUT'\n" + output + 'APT_OUTPUT\nexit 100\n')
            with patch('offline_apt_builder.install.trusted_installer', return_value=bundle / 'install.sh'), self.assertRaises(BuildError) as caught:
                run_installer(bundle, ['--preview'], lambda *args, **kwargs: None)
            error = caught.exception
            self.assertEqual(error.diagnostics['removed_packages'], names)
            summary = str(error).splitlines()[0]
            self.assertIn('120 installed', summary)
            self.assertIn('and 110 more', summary)
            self.assertNotIn('package-010', summary)
            self.assertIn('package-119', str(error))
            # A failed real installation must not claim its earlier work was undone.
            with patch('offline_apt_builder.install.trusted_installer', return_value=bundle / 'install.sh'), self.assertRaises(BuildError) as installed:
                run_installer(bundle, ['--yes'], lambda *args, **kwargs: None)
            self.assertIsNone(installed.exception.diagnostics)
            self.assertNotIn('No packages were changed', str(installed.exception))

    def test_worker_transports_optional_diagnostics(self):
        diagnostic = {'category': 'removal-required', 'removed_packages': ['gdm3']}
        error = BuildError(removal_summary(diagnostic), diagnostics=diagnostic)
        output = io.StringIO()
        with patch('offline_apt_builder.worker.verify_bundle', side_effect=error), \
                patch('offline_apt_builder.worker.signal.signal'), contextlib.redirect_stdout(output):
            result = main(['preview', '--request', '{"bundle":"/fixture"}'])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())['diagnostics'], diagnostic)


if __name__ == '__main__':
    unittest.main()
