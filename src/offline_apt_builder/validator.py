"""Required offline installation and desktop dependency validation matrix."""
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import shlex

from .models import BuildError
from .repository import sha256, write_json
from .runner import Runner, redact
from .resources.validation_plan import POLICY

BASE_SCENARIOS = {'fresh': 'installation', 'older-core': 'installation',
                  'desktop-release': 'dependency-simulation', 'desktop-current': 'dependency-simulation'}
INSTALL_CHECKS = ['preview_unchanged', 'no_removals', 'flags_preserved', 'repeat_unchanged']


def scenario_label(scenario):
    labels = {'fresh': 'Fresh Ubuntu', 'older-core': 'Older system libraries',
              'desktop-release': 'Original Ubuntu desktop dependencies',
              'desktop-current': 'Current Ubuntu desktop dependencies'}
    return labels.get(scenario['id'], 'Installed provider: ' + ', '.join(
        value.split('=', 1)[0] for value in scenario.get('seed', [])))


def definition_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_plan(bundle):
    try:
        plan = json.loads((bundle / 'validation-plan.json').read_text())
        scenarios = plan['scenarios']
        ids = [s['id'] for s in scenarios]
        if plan['policy'] != POLICY or len(ids) != len(set(ids)):
            raise ValueError('unknown policy or duplicate scenarios')
        by_id = {s['id']: s for s in scenarios}
        if any(by_id.get(name, {}).get('kind') != kind for name, kind in BASE_SCENARIOS.items()):
            raise ValueError('missing required reference scenarios')
        for scenario in scenarios:
            if scenario['kind'] not in {'installation', 'dependency-simulation'}:
                raise ValueError('unknown test type')
            if scenario['kind'] == 'dependency-simulation':
                archive = 'noble' if scenario['id'] == 'desktop-release' else 'current-pockets'
                if scenario.get('reference_archive') != archive:
                    raise ValueError('missing or incorrect desktop reference archive')
                path = Path(scenario['status_file'])
                if path.parts != ('validation-references', scenario['id'] + '.status'):
                    raise ValueError('invalid reference path')
                if sha256(bundle / path) != scenario['reference_sha256']:
                    raise ValueError('reference state checksum mismatch')
        return plan
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise BuildError(f'Invalid compatibility validation plan: {exc}') from exc


def verify_evidence(bundle, manifest, report):
    policy = manifest.get('validation_policy')
    if policy is None:
        if report.get('validation_policy') is not None:
            raise BuildError('Validation policy is missing from the manifest.')
        return 'Legacy coverage: clean Ubuntu installation.'
    if policy != POLICY or report.get('validation_policy') != policy:
        raise BuildError('Unsupported or inconsistent compatibility validation policy.')
    plan = read_plan(bundle)
    expected = plan['scenarios']
    actual = report.get('scenarios', [])
    if (report.get('plan_sha256') != sha256(bundle / 'validation-plan.json')
            or len(actual) != len(expected)):
        raise BuildError('Compatibility validation evidence is incomplete.')
    versions = {s['name']: s['version'] for s in manifest['selections']}
    for scenario, result in zip(expected, actual):
        checks = INSTALL_CHECKS if scenario['kind'] == 'installation' else ['preview_unchanged']
        if (result.get('id') != scenario['id'] or result.get('kind') != scenario['kind']
                or result.get('status') != 'PASSED' or result.get('network_mode') != 'none'
                or result.get('exit_code') != 0 or result.get('checks') != checks
                or not result.get('reference_image')
                or result.get('definition_sha256') != definition_hash(scenario)
                or (scenario['kind'] == 'installation' and result.get('installed_versions') != versions)):
            raise BuildError(f'Incomplete compatibility evidence for {scenario["id"]}.')
    installs = sum(s['kind'] == 'installation' for s in expected)
    models = len(expected) - installs
    return f'Compatibility coverage: {installs} offline installation tests and {models} desktop dependency simulations passed.'


def prepare_core(runner, bundle, base, name, scenario):
    metadata = json.loads((bundle / 'manifest.json').read_text())['repositories']['release_metadata']
    key = definition_hash({'policy': POLICY, 'base': base, 'scenario': scenario, 'indexes': metadata})
    tag = 'soozip-validation-core:' + key[:32]
    image = runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], check=False, capture=True).strip()
    if image.startswith('sha256:') and '\n' not in image:
        return image
    script = ('set -Eeuo pipefail\nexport DEBIAN_FRONTEND=noninteractive LC_ALL=C\n'
              'cp /sources /etc/apt/sources.list.d/ubuntu.sources\n'
              'apt-get -o APT::Update::Error-Mode=any update\n'
              'apt-get --yes --no-remove --no-install-recommends --allow-downgrades install '
              + shlex.join(scenario['seed']) + '\napt-get check\ntest -z "$(dpkg --audit)"\n'
              'rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb\n')
    try:
        runner.run(['docker', 'create', '--name', name, '--pull', 'never', '--platform', 'linux/amd64',
                    '--volume', f'{bundle}/repository-metadata/ubuntu.sources:/sources:ro', base, 'bash', '-c', script])
        runner.run(['docker', 'start', '--attach', name])
        code = int(runner.run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', name], capture=True).strip())
        if code:
            raise BuildError('Required older Ubuntu reference could not be prepared. See the build log.')
        runner.run(['docker', 'commit', '--change', 'LABEL org.soozip.validation-cache=' + key, name, tag])
        return runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], capture=True).strip()
    finally:
        runner.run(['docker', 'rm', '--force', name], check=False)


def validate(runner: Runner, bundle: Path, base_image: str, container: str,
             selections: list[dict], report_path: Path, progress=None) -> dict:
    report = {'status': 'FAILED', 'network_mode': 'none', 'base_image': base_image,
              'installer': 'install.sh', 'installed_versions': {}, 'dependency_check': False,
              'validation_policy': POLICY, 'scenarios': []}
    log = report_path.parent / 'validation.log'
    script = files('offline_apt_builder').joinpath('resources/validate-scenario.sh').read_text()
    expected = {s['name']: s['version'] for s in selections}
    try:
        plan = read_plan(bundle)
        report['plan_sha256'] = sha256(bundle / 'validation-plan.json')
        for index, scenario in enumerate(plan['scenarios']):
            identity = scenario['id']
            if progress:
                progress(f'Compatibility test {index + 1}/{len(plan["scenarios"])}: {scenario_label(scenario)}.')
            result = {'id': identity, 'label': scenario_label(scenario), 'kind': scenario['kind'], 'status': 'FAILED',
                      'definition_sha256': definition_hash(scenario)}
            report['scenarios'].append(result)
            image = base_image
            args = scenario.get('seed', [])
            if identity == 'older-core':
                image = prepare_core(runner, bundle, base_image, container + '-seed', scenario)
                args = []
            elif scenario['kind'] == 'dependency-simulation':
                args = [scenario['status_file']]
            result['reference_image'] = image
            name = container + '-' + str(index)
            try:
                runner.run(['docker', 'create', '--name', name, '--pull', 'never', '--network', 'none',
                            '--platform', 'linux/amd64', '--volume', f'{bundle}:/bundle:ro', image,
                            'bash', '-c', script, 'validate-scenario', scenario['kind'], *args])
                network = runner.run(['docker', 'inspect', '--format', '{{.HostConfig.NetworkMode}}', name], capture=True).strip()
                result['network_mode'] = network
                if network != 'none':
                    raise BuildError('Validation refused: test container has network access.')
                output = runner.run(['docker', 'start', '--attach', name], check=False, capture=True)
                code = int(runner.run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', name], capture=True).strip())
                result['exit_code'] = code
                with log.open('a') as stream:
                    stream.write(f'\n=== {identity} ({scenario["kind"]}) ===\n' + redact(output))
                if code or 'VALIDATION_SCENARIO_OK' not in output:
                    raise BuildError(f'Compatibility test {scenario_label(scenario)} failed (exit {code}). '
                                     'No validated bundle was produced. See validation.log for APT details.\n' + output[-3000:])
                checks = INSTALL_CHECKS if scenario['kind'] == 'installation' else ['preview_unchanged']
                if any('VALIDATION_' + check.upper() not in output for check in checks):
                    raise BuildError(f'Missing compatibility checks in {identity}.')
                if scenario['kind'] == 'installation':
                    installed = dict(line.split('\t')[1:] for line in output.splitlines() if line.startswith('INSTALLED\t'))
                    if installed != expected:
                        raise BuildError(f'Requested versions were not confirmed in {identity}.')
                    result['installed_versions'] = installed
                result.update(status='PASSED', checks=checks)
            finally:
                runner.run(['docker', 'rm', '--force', name], check=False)
        report.update(status='VALIDATED', exit_code=0, installed_versions=expected, dependency_check=True)
        return report
    except BaseException as exc:
        report['error'] = redact(str(exc)) or type(exc).__name__
        raise
    finally:
        write_json(report_path, report)
