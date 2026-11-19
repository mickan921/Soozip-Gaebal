"""Trusted client boundary around the standalone pip installer."""
from dataclasses import asdict
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
from .models import BuildError, InstallPlan, PackageAction, PipInstallTarget
from .resources import pip_engine
from .runner import redact


def discover_interpreters(version):
    result = []
    helper = files('offline_apt_builder').joinpath('resources/pip_engine.py')
    for name in (f'python{version}', 'python3', 'python'):
        path = shutil.which(name)
        if not path or any(item['path'] == path for item in result):
            continue
        try:
            process = subprocess.run([path, '-I', '-B', str(helper), 'probe'], env=pip_engine.clean_env(),
                                     capture_output=True, text=True, timeout=5, check=True)
            info = json.loads(process.stdout)
            if (info['implementation'] == 'CPython' and info['version'].startswith(version + '.')
                    and not info['free_threaded'] and not info['debug']):
                result.append({'path': path, 'version': info['version']})
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return result


def verify_pip_bundle(bundle):
    inventory(bundle)
    trusted = files('offline_apt_builder').joinpath('resources/pip_engine.py').read_bytes()
    if (bundle / 'install-pip.py').read_bytes() != trusted:
        raise BuildError('Pip installer version does not match this client; transfer the matching Gaebal client.')
    try:
        manifest = pip_engine.verify_payload(bundle)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', manifest.get('build_id', '')):
            raise ValueError('Invalid pip bundle build identifier')
        return manifest
    except (ValueError, KeyError, OSError) as exc:
        raise BuildError(str(exc)) from exc


def interpreter(target):
    # Preserve the virtualenv symlink path: resolving it would select system Python.
    return str((target.venv / 'bin/python' if not target.create else target.python).expanduser().absolute())


def run_helper(bundle, target, operation, emit, **request):
    args = [interpreter(target), '-I', '-B', str(bundle / 'install-pip.py'), operation,
            '--bundle', str(bundle), '--request', json.dumps({'target': asdict(target), **request}, default=str)]
    env = pip_engine.clean_env()
    output = []
    log_dir = Path(tempfile.mkdtemp(prefix='gaebal-pip-log-'))
    with (log_dir / 'install.log').open('w') as log:
        emit('log', message=f'Local diagnostics: {log_dir / "install.log"}')
        try:
            process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, errors='replace', start_new_session=True)
        except OSError as exc:
            raise BuildError(f'Cannot run the selected Python interpreter: {exc}. Prepare compatible Python with venv support offline.') from exc
        try:
            for line in process.stdout:
                output.append(line)
                log.write(redact(line))
                log.flush()
                emit('log', message=redact(line.rstrip()))
            code = process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            process.stdout.close()
    if code:
        error = next((line[7:].strip() for line in reversed(output) if line.startswith('ERROR: ')), f'Pip installer failed (exit {code})')
        raise BuildError(redact(error) + f'\nDiagnostics: {log_dir / "install.log"}')
    return ''.join(output)


def preview_pip(bundle: Path, target: PipInstallTarget, emit) -> InstallPlan:
    verify_pip_bundle(bundle)
    output = run_helper(bundle, target, 'preview', emit)
    records = [json.loads(line.removeprefix('PIP_PLAN=')) for line in output.splitlines() if line.startswith('PIP_PLAN=')]
    if len(records) != 1:
        raise BuildError('Installer returned an invalid pip plan')
    plan = records[0]
    return InstallPlan(tuple(PackageAction(**a) for a in plan['actions']), tuple(plan['blockers']), plan['approval_token'])


def install_pip(bundle: Path, target: PipInstallTarget, token, upgrades, downgrades, emit):
    if os.geteuid() == 0:
        raise BuildError('Install pip packages as your normal user, without sudo.')
    verify_pip_bundle(bundle)
    required = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) + 64 * 1024**2
    if shutil.disk_usage(tempfile.gettempdir()).free < required:
        raise BuildError(f'Pip staging requires {required:,} free bytes')
    with tempfile.TemporaryDirectory(prefix='gaebal-pip-private-') as temp:
        staged = Path(temp) / 'offline-pip'
        shutil.copytree(bundle, staged, symlinks=True)
        verify_pip_bundle(staged)
        emit('progress', stage='install', message='Installing into the reviewed virtualenv; keep this window open.')
        run_helper(staged, target, 'install', emit, approval_token=token, upgrades=upgrades, downgrades=downgrades)
