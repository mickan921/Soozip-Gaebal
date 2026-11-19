import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from offline_apt_builder.models import BuildError
from offline_apt_builder.repository import sha256
from offline_apt_builder.resources.apt_compatibility import expand_alternatives, native_provider_choices
from offline_apt_builder.resources.validation_plan import status_record
from offline_apt_builder.validator import (INSTALL_CHECKS, POLICY, definition_hash,
                                          read_plan, validate, verify_evidence)
from test_apt_compatibility import package
from validation_support import MARKERS, plan_fixture


class AlternativeTests(unittest.TestCase):
    def test_versioned_virtual_matches_candidates_and_native_architectures(self):
        owner = SimpleNamespace(name='provider', candidate=None)
        old, new = (SimpleNamespace(package=owner, version=str(v)) for v in (1, 2))
        owner.candidate = old
        available = {('provider', str(v)): package('provider', str(v)) for v in (1, 2)}
        foreign = SimpleNamespace(package=SimpleNamespace(name='provider:i386'), version='2')
        compare = lambda a, b: int(a) - int(b)
        for order in ((old, new, foreign), (new, foreign, old)):
            self.assertEqual(native_provider_choices(order, available, compare), (available['provider', '1'],))
        owner.candidate = new
        # A versioned virtual dependency can exclude the current candidate.
        self.assertEqual(native_provider_choices((old,), available, compare), (available['provider', '1'],))
        self.assertEqual(native_provider_choices((old, new), available, compare), (available['provider', '2'],))

    def test_recursive_choices_cycles_independent_solves_and_duplicates(self):
        root, first, second, runtime, third = [package(n) for n in ('root', 'first', 'second', 'runtime', 'third')]
        groups = {'root': [(first, second, second)], 'runtime': [(first, third)], 'third': [(first, second)]}
        calls = []
        def solve(peer):
            calls.append(peer.name)
            return (peer, runtime) if peer == second else (peer,)
        result, reasons, providers = expand_alternatives((root, first), lambda p: groups.get(p.name, []), solve)
        self.assertEqual({p.name for p in result}, {'root', 'first', 'second', 'runtime', 'third'})
        self.assertEqual(calls, ['second', 'third'])
        self.assertEqual({p.name for p in providers}, {'first', 'second', 'third'})
        self.assertIn('alternative provider', reasons['runtime'][0])

    def test_version_collision_and_missing_provider_rejected(self):
        root, one, two = [package(n) for n in ('root', 'one', 'two')]
        for closure, message in (((two, package('one', '1')), 'conflicting versions'), ((root,), 'omitted')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                expand_alternatives((root, one), lambda p: [(one, two)] if p == root else [], lambda _: closure)


class EvidenceTests(unittest.TestCase):
    def test_desktop_status_excludes_acquisition_fields_and_keeps_dependencies(self):
        record = status_record({'Package': 'sample', 'Version': '1', 'Architecture': 'all',
            'Filename': 'pool/sample.deb', 'Size': '123', 'SHA256': 'hash',
            'Depends': 'virtual-api (= 2) | fallback', 'Provides': 'other-api (= 3)',
            'Description': 'summary\n details'})
        self.assertNotIn('Filename:', record)
        self.assertNotIn('Size:', record)
        self.assertNotIn('SHA256:', record)
        self.assertIn('Depends: virtual-api (= 2) | fallback\n', record)
        self.assertIn('Description: summary\n details\n', record)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)
        self.scenarios = plan_fixture(self.bundle)
        self.manifest = {'validation_policy': POLICY, 'selections': [{'name': 'example', 'version': '1'}]}
        self.report = {'validation_policy': POLICY, 'plan_sha256': sha256(self.bundle / 'validation-plan.json'),
                       'scenarios': []}
        for scenario in self.scenarios:
            self.report['scenarios'].append({**scenario, 'status': 'PASSED', 'exit_code': 0,
                'network_mode': 'none', 'reference_image': 'sha256:fixture',
                'definition_sha256': definition_hash(scenario), 'installed_versions': {'example': '1'},
                'checks': INSTALL_CHECKS if scenario['kind'] == 'installation' else ['preview_unchanged']})

    def test_valid_matrix_and_legacy_coverage(self):
        self.assertIn('2 offline installation tests', verify_evidence(self.bundle, self.manifest, self.report))
        self.assertIn('Legacy', verify_evidence(self.bundle, {}, {}))

    def test_missing_failed_networked_or_incomplete_evidence_fails_closed(self):
        changes = [('status', 'FAILED'), ('exit_code', 17), ('network_mode', 'bridge'),
                   ('checks', []), ('installed_versions', {}), ('definition_sha256', 'changed')]
        for key, value in changes:
            report = copy.deepcopy(self.report)
            report['scenarios'][0][key] = value
            with self.subTest(key=key), self.assertRaises(BuildError):
                verify_evidence(self.bundle, self.manifest, report)
        self.report['scenarios'].pop()
        with self.assertRaises(BuildError):
            verify_evidence(self.bundle, self.manifest, self.report)

    def test_missing_duplicate_or_corrupt_reference_is_rejected(self):
        plan = {'policy': POLICY, 'scenarios': self.scenarios[:-1]}
        (self.bundle / 'validation-plan.json').write_text(json.dumps(plan))
        with self.assertRaises(BuildError): read_plan(self.bundle)
        plan['scenarios'] = self.scenarios + [self.scenarios[0]]
        (self.bundle / 'validation-plan.json').write_text(json.dumps(plan))
        with self.assertRaises(BuildError): read_plan(self.bundle)
        plan_fixture(self.bundle)
        (self.bundle / 'validation-references/desktop-release.status').write_text('changed')
        with self.assertRaises(BuildError): read_plan(self.bundle)

    def test_failed_test_and_cancellation_cleanup_and_save_report(self):
        for failure in (BuildError('broken Docker'), KeyboardInterrupt()):
            calls = []
            class FakeRunner:
                def run(self, args, **kwargs):
                    calls.append(args)
                    if args[1] == 'start': raise failure
                    if '{{.HostConfig.NetworkMode}}' in args: return 'none'
                    return ''
            report_path = self.bundle / 'test-report.json'
            with self.assertRaises(type(failure)):
                validate(FakeRunner(), self.bundle, 'sha256:test', 'test', [], report_path)
            self.assertIn(['docker', 'rm', '--force', 'test-0'], calls)
            self.assertEqual(json.loads(report_path.read_text())['status'], 'FAILED')

    def test_unavailable_reference_cannot_be_skipped(self):
        class FakeRunner:
            def run(self, args, **kwargs):
                if '{{.HostConfig.NetworkMode}}' in args: return 'none'
                if '{{.State.ExitCode}}' in args: return '0'
                return MARKERS
        with patch('offline_apt_builder.validator.prepare_core', side_effect=BuildError('missing reference')):
            with self.assertRaisesRegex(BuildError, 'missing reference'):
                validate(FakeRunner(), self.bundle, 'sha256:test', 'test', [], self.bundle / 'report.json')
        report = json.loads((self.bundle / 'report.json').read_text())
        self.assertEqual([r['status'] for r in report['scenarios']], ['PASSED', 'FAILED'])

class ReferencePlanningTests(unittest.TestCase):
    def test_original_reference_uses_archive_pins_and_rejects_updated_versions(self):
        from offline_apt_builder.resources.validation_plan import OLD_CORE, create_plan
        from offline_apt_builder.resources.apt_compatibility import PackageVersion
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'offline-apt'
            bundle.mkdir()
            (root / 'apt').mkdir()
            def version(name, number, archive):
                return SimpleNamespace(version=number, architecture='amd64', source_name=name, source_version=number,
                    origins=[SimpleNamespace(archive=archive, trusted=True)],
                    record={'Package': name, 'Version': number, 'Architecture': 'amd64', 'Description': 'fixture'})
            cache = {name: SimpleNamespace(versions=[version(name, '1', 'noble')]) for name in OLD_CORE}
            cache['systemd'].versions.insert(0, version('systemd', '2', 'noble-updates'))
            selected = lambda v: PackageVersion('systemd', v, 'amd64', 'systemd', v, 1)
            options_seen = []
            def resolve(values, options, exact):
                options_seen.append(options)
                return [selected('1' if any('Dir::Etc::preferences=' in value for value in options) else '2')]
            plan = create_plan(bundle, cache, [], resolve)
            self.assertEqual(plan['scenarios'][2]['reference_archive'], 'noble')
            self.assertNotEqual(plan['scenarios'][2]['reference_sha256'], plan['scenarios'][3]['reference_sha256'])
            self.assertNotIn('-t', options_seen[0])
            self.assertIn('Pin: release a=noble-updates\nPin-Priority: -1', (root / 'apt/desktop-release.pref').read_text())
            import shutil
            shutil.rmtree(bundle / 'validation-references')
            with self.assertRaisesRegex(ValueError, 'non-release package'):
                create_plan(bundle, cache, [], lambda *args, **kwargs: [selected('2')])


if __name__ == '__main__':
    unittest.main()
