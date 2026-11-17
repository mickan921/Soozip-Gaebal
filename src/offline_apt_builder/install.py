"""Offline bundle verification and shared shell transaction interface."""
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

from .archive import inventory
from .models import BuildError, InstallPlan, PackageAction
from .repository import sha256, verify_repository


def verify_bundle(bundle: Path) -> dict:
    actual = inventory(bundle)
    sums = bundle / 'SHA256SUMS'
    if not sums.is_file():
        raise BuildError('Missing bundle checksums.')
    expected = {}
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  \./(.+)', line)
        if not match or match[2] in expected:
            raise BuildError('Malformed or duplicate bundle checksum.')
        expected[match[2]] = match[1]
    payload = {name: value for name, value in actual.items() if value != 'directory' and name != 'SHA256SUMS'}
    if expected != payload:
        raise BuildError('Bundle checksum coverage or integrity check failed.')
    required = {'install.sh', 'architecture.txt', 'with-recommends.txt', 'packages.txt',
                'install-targets.txt', 'manifest.json', 'build-info.txt', 'README-offline.txt', 'test-report.json'}
    if not required.issubset(payload):
        raise BuildError('Bundle is missing required files.')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    report = json.loads((bundle / 'test-report.json').read_text())
    if manifest.get('schema_version') != 2:
        raise BuildError('Unsupported manifest version; rebuild using the current online client.')
    if manifest.get('validation_status') != 'VALIDATED' or report.get('status') != 'VALIDATED':
        raise BuildError('This client requires a VALIDATED bundle. Rebuild with offline testing enabled.')
    expected_versions = {s['name']: s['version'] for s in manifest['selections']}
    if (report.get('network_mode') != 'none' or report.get('exit_code') != 0
            or report.get('dependency_check') is not True
            or report.get('installed_versions') != expected_versions
            or report.get('base_image') != manifest.get('base_image', {}).get('id')):
        raise BuildError('Bundle validation evidence is incomplete or inconsistent.')
    if (bundle / 'architecture.txt').read_text().strip() != 'amd64':
        raise BuildError('Only amd64 bundles are supported.')
    if (bundle / 'install.sh').read_bytes() != files('offline_apt_builder').joinpath('resources/install.sh').read_bytes():
        raise BuildError('Installer version does not match this client; transfer the matching offline client.')
    verify_repository(bundle, manifest)
    return manifest


def run_installer(bundle: Path, arguments: list[str], emit) -> str:
    # Never inherit caller APT, Bash startup, or dynamic-library configuration.
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'HOME': '/root' if os.geteuid() == 0 else str(Path.home())}
    process = subprocess.Popen(['/bin/bash', str(bundle / 'install.sh'), *arguments], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace', start_new_session=True)
    output = []
    try:
        for line in process.stdout:
            output.append(line)
            emit('log', message=line.rstrip())
        code = process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except ProcessLookupError:
            process.wait()
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise
    finally:
        process.stdout.close()
    if code:
        details = ''.join(output)
        errors = [line.removeprefix('ERROR: ').strip() for line in details.splitlines() if line.startswith('ERROR: ')]
        if 'Could not get lock' in details or 'Unable to acquire' in details:
            summary = 'Another package manager is busy. Wait for it to finish, then reopen the bundle and retry.'
        elif 'dpkg was interrupted' in details:
            summary = 'A previous package installation is incomplete. Resolve the dpkg error shown in Details, then reopen the bundle.'
        else:
            summary = errors[0] if errors else f'Installer failed (exit {code}). Review Details before retrying.'
        raise BuildError(summary + '\n' + details[-3000:])
    return ''.join(output)


def preview(bundle: Path, emit) -> InstallPlan:
    verify_bundle(bundle)
    output = run_installer(bundle, ['--preview'], emit)
    actions = []
    names = set()
    token = ''
    for line in output.splitlines():
        if line.startswith('PLAN\t'):
            parts = line.split('\t')
            if len(parts) != 5:
                raise BuildError('Installer returned an invalid package plan. Reopen the bundle to retry.')
            _, name, action, old, new = parts
            if (not name or name in names or not new
                    or action not in {'install', 'reinstall', 'upgrade', 'downgrade', 'configure', 'keep'}
                    or (action == 'keep' and old != new)):
                raise BuildError('Installer returned an inconsistent package plan. Reopen the bundle to retry.')
            names.add(name)
            actions.append(PackageAction(name, action, old, new))
        elif line.startswith('APPROVAL\t'):
            if token:
                raise BuildError('Installer returned multiple approval tokens. Reopen the bundle to retry.')
            token = line.removeprefix('APPROVAL\t')
    if not actions:
        raise BuildError('Installer did not produce a package plan. Reopen the bundle to retry.')
    if not re.fullmatch('[a-f0-9]{64}', token):
        raise BuildError('Installer did not produce an approval token.')
    return InstallPlan(tuple(actions), (), token)


def install(bundle: Path, token: str, upgrades: bool, downgrades: bool, emit) -> None:
    if os.geteuid() != 0:
        raise BuildError('Administrator authentication is required.')
    if not re.fullmatch('[a-f0-9]{64}', token):
        raise BuildError('Invalid approval token.')
    verify_bundle(bundle)
    required = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) + 64 * 1024**2
    if shutil.disk_usage('/var/tmp').free < required:
        raise BuildError(f'Installation staging requires {required:,} free bytes in /var/tmp.')
    # Copy without following links, then reject links and verify before running code.
    with tempfile.TemporaryDirectory(prefix='offline-apt-private-', dir='/var/tmp') as temp:
        staged = Path(temp) / 'offline-apt'
        shutil.copytree(bundle, staged, symlinks=True)
        plan = preview(staged, emit)
        if plan.approval_token != token:
            raise BuildError('Bundle or target state changed. Open the bundle again to review a fresh plan.')
        if any(a.action == 'upgrade' for a in plan.actions) and not upgrades:
            raise BuildError('Approve the listed upgrades before installing.')
        if any(a.action == 'downgrade' for a in plan.actions) and not downgrades:
            raise BuildError('Approve the listed downgrades before installing.')
        args = ['--yes', '--approval-token=' + token]
        if upgrades:
            args.append('--allow-upgrades')
        if downgrades:
            args.append('--allow-downgrades')
        emit('progress', stage='install', message='Installing packages; keep this window open.')
        run_installer(staged, args, emit)
