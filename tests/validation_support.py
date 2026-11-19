"""Small reference definitions for orchestration tests, not real APT evidence."""
import hashlib
import json

from offline_apt_builder.validator import BASE_SCENARIOS, INSTALL_CHECKS, POLICY

MARKERS = ''.join('VALIDATION_' + value.upper() + '\n' for value in INSTALL_CHECKS) + 'VALIDATION_SCENARIO_OK\n'


def plan_fixture(bundle):
    scenarios = []
    (bundle / 'validation-references').mkdir(exist_ok=True)
    for name, kind in BASE_SCENARIOS.items():
        value = {'id': name, 'kind': kind, 'seed': []}
        if kind == 'dependency-simulation':
            path = bundle / 'validation-references' / (name + '.status')
            path.write_text('fixture reference\n')
            value.update(status_file=path.relative_to(bundle).as_posix(),
                         reference_archive='noble' if name == 'desktop-release' else 'current-pockets',
                         reference_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        scenarios.append(value)
    (bundle / 'validation-plan.json').write_text(json.dumps({'policy': POLICY, 'scenarios': scenarios}))
    return scenarios


def validate_fixture(runner, bundle, base_image, container, selections, report_path, *, keep_container=False):
    """Exercise synthetic repositories; do not claim Ubuntu reference coverage."""
    from importlib.resources import files
    script = files('offline_apt_builder').joinpath('resources/validate-scenario.sh').read_text()
    try:
        runner.run(['docker', 'create', '--name', container, '--pull', 'never', '--network', 'none',
                    '--volume', f'{bundle}:/bundle:ro', base_image, 'bash', '-c', script, 'fixture', 'installation'])
        network = runner.run(['docker', 'inspect', '--format', '{{.HostConfig.NetworkMode}}', container], capture=True).strip()
        output = runner.run(['docker', 'start', '--attach', container], capture=True)
        code = int(runner.run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', container], capture=True))
        (report_path.parent / 'validation.log').write_text(output)
        assert network == 'none' and code == 0 and 'VALIDATION_SCENARIO_OK' in output
        installed = dict(line.split('\t')[1:] for line in output.splitlines() if line.startswith('INSTALLED\t'))
        assert installed == {s['name']: s['version'] for s in selections}
        report = {'status': 'VALIDATED', 'base_image': base_image, 'network_mode': network,
                  'exit_code': code, 'installed_versions': installed, 'dependency_check': True}
        report_path.write_text(json.dumps(report))
        return report
    finally:
        if not keep_container:
            runner.run(['docker', 'rm', '--force', container], check=False)
