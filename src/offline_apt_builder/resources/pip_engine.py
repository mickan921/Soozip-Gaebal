"""Versioned, standalone wheel installer. Compatible with CPython 3.11–3.14.

Invoked with the target interpreter, never with the frozen client's Python.
The exact same file performs container validation and destination installation.
"""
import argparse
from email.parser import BytesParser
import fcntl
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

TOOLS = {
    'pip-26.3-py3-none-any.whl': {
        'sha256': 'f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13',
        'url': 'https://files.pythonhosted.org/packages/07/25/ee8c38ae72d66b844ce8eb9711ba905dba21ca1a8a0c960057c41cd23337/pip-26.3-py3-none-any.whl'},
    'packaging-26.3-py3-none-any.whl': {
        'sha256': 'd7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c',
        'url': 'https://files.pythonhosted.org/packages/63/34/ba1c580383c9eada3711951fef0795c80b829a078d72188184bcab9dd527/packaging-26.3-py3-none-any.whl'},
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def load_tools(bundle):
    for name, record in TOOLS.items():
        path = bundle / 'tooling' / name
        if path.is_symlink() or digest(path) != record['sha256']:
            raise ValueError('Bundled installer tooling checksum mismatch: ' + name)
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def verify_checksums(bundle):
    expected = {}
    for line in (bundle / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  \./(.+)', line)
        if not match or match[2] in expected:
            raise ValueError('Malformed or duplicate bundle checksum')
        expected[match[2]] = match[1]
    actual = {}
    for path in bundle.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('Unsafe bundle entry')
        if path.is_file() and path != bundle / 'SHA256SUMS':
            actual[path.relative_to(bundle).as_posix()] = digest(path)
    if expected != actual:
        raise ValueError('Bundle checksum coverage or integrity check failed')
    return digest(bundle / 'SHA256SUMS')


def wheels(bundle):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version
    result = []
    for path in sorted((bundle / 'wheelhouse').iterdir()):
        if not path.is_file() or path.suffix != '.whl':
            raise ValueError('Only wheel files may appear in the wheelhouse')
        name, version, _, tags = parse_wheel_filename(path.name)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate wheel entries: ' + path.name)
            for entry in names:
                p = PurePosixPath(entry)
                if p.is_absolute() or '..' in p.parts or '\\' in entry:
                    raise ValueError('Unsafe wheel path: ' + entry)
            candidates = [n for n in names if n.endswith('.dist-info/METADATA')]
            if len(candidates) != 1:
                raise ValueError('Wheel has ambiguous metadata: ' + path.name)
            info = BytesParser().parsebytes(archive.read(candidates[0]))
        if canonicalize_name(info['Name']) != name or Version(info['Version']) != version:
            raise ValueError('Wheel filename and metadata disagree: ' + path.name)
        version = Version(info['Version'])
        dependencies = info.get_all('Requires-Dist', [])
        for requirement in dependencies:
            if Requirement(requirement).url:
                raise ValueError('Remote dependency references are unsupported: ' + requirement)
        result.append({'name': name, 'version': str(version), 'filename': path.name,
                       'sha256': digest(path), 'size_bytes': path.stat().st_size,
                       'tags': sorted(str(tag) for tag in tags),
                       'requires_python': info.get('Requires-Python', ''),
                       'requires_dist': dependencies})
    if not result or len({p['name'] for p in result}) != len(result):
        raise ValueError('Empty wheelhouse or duplicate distribution versions')
    return result


def lock_text(packages):
    return ''.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in packages)


def verify_payload(bundle, require_validated=True):
    verify_checksums(bundle)
    load_tools(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest.get('bundle_type') != 'pip' or manifest.get('schema_version') != 1:
        raise ValueError('Unsupported pip bundle schema')
    target = manifest['target']
    if target.get('os') != 'ubuntu' or target.get('release') != '24.04' or target.get('arch') != 'amd64' or target.get('python_version') not in ('3.11', '3.12', '3.13', '3.14'):
        raise ValueError('Unsupported pip bundle target')
    packages = wheels(bundle)
    if packages != manifest['packages'] or (bundle / 'requirements.lock').read_text() != lock_text(packages):
        raise ValueError('Wheelhouse, manifest, and lock disagree')
    if (bundle / 'requirements.txt').read_text().splitlines() != manifest['requested']:
        raise ValueError('Original requirements disagree with manifest')
    if require_validated:
        report = json.loads((bundle / 'test-report.json').read_text())
        expected = {p['name']: p['version'] for p in packages}
        if (manifest.get('validation_status') != 'VALIDATED' or report.get('status') != 'VALIDATED'
                or report.get('network_mode') != 'none' or report.get('exit_code') != 0
                or report.get('dependency_check') is not True or report.get('installed_versions') != expected
                or report.get('base_image') != manifest.get('base_image', {}).get('id')
                or report.get('runtime_image') != manifest.get('runtime_image')
                or report.get('python_full_version') != target.get('python_full_version')):
            raise ValueError('Pip validation evidence is incomplete or inconsistent')
    return manifest


def python_info():
    from packaging.markers import default_environment
    from packaging.tags import sys_tags
    return {'executable': sys.executable, 'executable_sha256': digest(sys.executable),
            'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
            'version': platform.python_version(), 'implementation': platform.python_implementation(),
            'machine': platform.machine(), 'environment': default_environment(),
            'tags': sorted(str(tag) for tag in sys_tags()),
            'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
            'debug': bool(sysconfig.get_config_var('Py_DEBUG'))}


def installed_packages():
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    result = {}
    # Tooling wheels are on sys.path to run without an installed pip. They are
    # not distributions installed in the destination environment.
    paths = sorted({sysconfig.get_path('purelib'), sysconfig.get_path('platlib')})
    for dist in metadata.distributions(path=paths):
        name = canonicalize_name(dist.metadata['Name'] or '')
        if not name or name in result:
            raise ValueError('Ambiguous installed distribution metadata: ' + name)
        result[name] = {'name': name, 'version': str(Version(dist.version)),
                        'requires_dist': dist.requires or [], 'requires_python': dist.metadata.get('Requires-Python', ''),
                        'metadata_hash': hashlib.sha256(''.join(dist.read_text(f) or '' for f in
                            ('METADATA', 'RECORD', 'direct_url.json', 'INSTALLER')).encode()).hexdigest()}
    return result


def check_requirements(packages, requirements, environment):
    """Validate roots and their complete extras-aware closure on the actual target."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    pending = list(requirements)
    extras = {}
    visited = set()
    while pending:
        raw = pending.pop()
        req = Requirement(raw)
        if req.url:
            raise ValueError('Remote dependency references are unsupported: ' + raw)
        if req.marker and not req.marker.evaluate({**environment, 'extra': ''}):
            continue
        name = canonicalize_name(req.name)
        package = packages.get(name)
        if not package or not req.specifier.contains(package['version'], prereleases=True):
            raise ValueError('Dependency conflict or missing wheel: ' + raw)
        active = extras.setdefault(name, set())
        active.update(req.extras)
        state = (name, tuple(sorted(active)))
        if state in visited:
            continue
        visited.add(state)
        for value in package['requires_dist']:
            dep = Requirement(value)
            if dep.url:
                raise ValueError('Remote dependency references are unsupported: ' + value)
            if dep.marker is None or any(dep.marker.evaluate({**environment, 'extra': extra}) for extra in {'', *active}):
                dep.marker = None
                pending.append(str(dep))
    return set(extras)


def check_compatibility(manifest, info, current):
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    target = manifest['target']
    if (info['implementation'] != 'CPython' or '.'.join(info['version'].split('.')[:2]) != target['python_version']
            or info['machine'] not in ('x86_64', 'amd64') or info['free_threaded'] or info['debug']):
        raise ValueError('Choose standard CPython ' + target['python_version'] + ' for amd64 (not debug or free-threaded)')
    bundled = {p['name']: p for p in manifest['packages']}
    for package in bundled.values():
        if not set(package['tags']).intersection(info['tags']):
            raise ValueError('Incompatible wheel: ' + package['filename'])
    prospective = {**current, **bundled}
    for package in prospective.values():
        if package['requires_python'] and not SpecifierSet(package['requires_python']).contains(info['version'], prereleases=True):
            raise ValueError('Python version is incompatible with ' + package['name'])
    reachable = check_requirements(bundled, manifest['requested'], info['environment'])
    if reachable != set(bundled):
        raise ValueError('Requirement markers differ on this Python patch version; rebuild for a compatible target')
    # pip's resolver can miss reverse dependencies of unrelated installed packages.
    # Check their prospective metadata as well, before making any changes.
    check_requirements(prospective, [f"{p['name']}=={p['version']}" for p in prospective.values()]
                       + manifest['requested'], info['environment'])


def target_state(bundle, target, manifest):
    from packaging.version import Version
    if platform.system() != 'Linux' or platform.freedesktop_os_release().get('ID') != 'ubuntu' or platform.freedesktop_os_release().get('VERSION_ID') != '24.04':
        raise ValueError('Pip installations require Ubuntu 24.04 amd64')
    destination = Path(target['venv']).expanduser().absolute()
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError('Choose an absolute, non-symlink virtualenv directory')
    info = python_info()
    if target['create']:
        if destination.exists():
            raise ValueError('New virtualenv destination already exists; select Use existing virtualenv or another path')
        parent = destination.parent
        while not parent.exists():
            parent = parent.parent
        if not os.access(parent, os.W_OK | os.X_OK):
            raise ValueError('Virtualenv destination is not writable')
        try:
            import venv
        except ImportError as exc:
            raise ValueError('Prepare the selected Python version with venv support before going offline') from exc
        current, config = {}, ''
    else:
        config_path = destination / 'pyvenv.cfg'
        if sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != destination.resolve() or not config_path.is_file():
            raise ValueError('Select an existing isolated virtualenv, not system Python or Conda')
        config = config_path.read_text()
        if re.search(r'^include-system-site-packages\s*=\s*true\s*$', config, re.I | re.M):
            raise ValueError('Virtualenvs with system site-packages are unsupported')
        if (destination / 'conda-meta').exists() or not os.access(destination, os.W_OK):
            raise ValueError('Select a writable, non-Conda virtualenv')
        for path in (Path(sysconfig.get_path('purelib')), Path(sysconfig.get_path('scripts'))):
            if not os.access(path, os.W_OK | os.X_OK):
                raise ValueError('Virtualenv package directories are not writable')
        current = installed_packages()
    check_compatibility(manifest, info, current)
    actions = []
    for package in manifest['packages']:
        old = current.get(package['name'], {}).get('version', '')
        new = package['version']
        action = 'install' if not old else ('keep' if Version(old) == Version(new) else ('upgrade' if Version(old) < Version(new) else 'downgrade'))
        actions.append({'name': package['name'], 'action': action, 'old_version': old, 'new_version': new})
    state = {'bundle': digest(bundle / 'SHA256SUMS'), 'destination': str(destination.resolve()),
             'create': target['create'], 'interpreter': info, 'config': config, 'installed': current}
    token = hashlib.sha256(canonical_json(state).encode()).hexdigest()
    return {'actions': actions, 'blockers': [], 'approval_token': token}


def clean_env():
    return {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C.UTF-8',
            'PIP_CONFIG_FILE': os.devnull, 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
            'PYTHONDONTWRITEBYTECODE': '1'}


def run_pip(python, bundle, args, capture=False):
    wheel = bundle / 'tooling' / next(n for n in TOOLS if n.startswith('pip-'))
    bootstrap = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("pip",run_name="__main__")'
    command = [str(python), '-I', '-B', '-c', bootstrap, str(wheel), '--disable-pip-version-check',
               '--no-input', '--no-cache-dir', *args]
    process = subprocess.run(command, env=clean_env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None,
                             text=True)
    if process.returncode:
        raise ValueError('pip failed (exit %s). %s' % (process.returncode, process.stdout or 'Review the installation log.'))
    return process.stdout or ''


def install(bundle, target, token, upgrades, downgrades, validation=False):
    # Lock outside the environment so a preview never has to create its target.
    destination = Path(target['venv']).expanduser().absolute()
    lock_id = hashlib.sha256(str(destination.resolve()).encode()).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / f'gaebal-pip-locks-{os.getuid()}'
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    if lock_dir.is_symlink() or lock_dir.stat().st_uid != os.getuid() or stat.S_IMODE(lock_dir.stat().st_mode) != 0o700:
        raise ValueError('Unsafe environment lock directory')
    fd = os.open(lock_dir / lock_id, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another Gaebal installation is using this virtualenv') from exc
        manifest = verify_payload(bundle, require_validated=not validation)
        plan = target_state(bundle, target, manifest)
        if token != plan['approval_token']:
            raise ValueError('Bundle or target state changed. Review a fresh plan.')
        if any(a['action'] == 'upgrade' for a in plan['actions']) and not upgrades:
            raise ValueError('Approve the listed upgrades before installing')
        if any(a['action'] == 'downgrade' for a in plan['actions']) and not downgrades:
            raise ValueError('Approve the listed downgrades before installing')
        if all(a['action'] == 'keep' for a in plan['actions']):
            return
        if os.geteuid() == 0:
            raise ValueError('Install pip packages as your normal user, without sudo')
        if target['create']:
            import venv
            # Never relocate a venv: console scripts contain absolute paths.
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.mkdir()  # exclusive creation after state recheck
            venv.EnvBuilder(with_pip=False).create(destination)
        python = destination / 'bin/python'
        run_pip(python, bundle, ['install', '--no-index', '--find-links', str(bundle / 'wheelhouse'),
                '--only-binary=:all:', '--require-hashes', '-r', str(bundle / 'requirements.lock')])
        run_pip(python, bundle, ['check'])
        actual = json.loads(run_pip(python, bundle, ['list', '--format=json'], capture=True))
        from packaging.utils import canonicalize_name
        from packaging.version import Version
        versions = {canonicalize_name(p['name']): p['version'] for p in actual}
        if any(p['name'] not in versions or Version(versions[p['name']]) != Version(p['version']) for p in manifest['packages']):
            raise ValueError('Installed versions differ from the reviewed lock')
        print('PIP_INSTALLED=' + canonical_json({p['name']: p['version'] for p in manifest['packages']}), flush=True)


def resolve(bundle):
    load_tools(bundle)
    requirements = (bundle / 'requirements.txt').read_text().splitlines()
    # Download runs in a fresh interpreter, with no host packages or pip settings.
    run_pip(sys.executable, bundle, ['download', '--index-url', 'https://pypi.org/simple',
            '--only-binary=:all:', '--dest', str(bundle / 'wheelhouse'), '-r', str(bundle / 'requirements.txt')])
    packages = wheels(bundle)
    info = python_info()
    manifest = {'bundle_type': 'pip', 'schema_version': 1, 'requested': requirements,
                'target': {'os': 'ubuntu', 'release': '24.04', 'arch': 'amd64',
                           'python_version': f'{sys.version_info.major}.{sys.version_info.minor}',
                           'python_full_version': platform.python_version()}, 'packages': packages}
    check_compatibility(manifest, info, {})
    (bundle / 'requirements.lock').write_text(lock_text(packages))
    (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['resolve', 'preview', 'install', 'validate', 'probe'])
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--request', default='{}')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.operation == 'probe':
            print(json.dumps({'version': platform.python_version(), 'implementation': platform.python_implementation(),
                              'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
                              'debug': bool(sysconfig.get_config_var('Py_DEBUG'))}))
            return 0
        bundle = args.bundle.absolute()
        if args.operation == 'resolve':
            resolve(bundle)
            return 0
        request = json.loads(args.request)
        validation = args.operation == 'validate'
        manifest = verify_payload(bundle, require_validated=not validation)
        target = request['target']
        plan = target_state(bundle, target, manifest)
        if args.operation == 'preview':
            print('PIP_PLAN=' + canonical_json(plan), flush=True)
        else:
            install(bundle, target, plan['approval_token'] if validation else request['approval_token'],
                    request.get('upgrades', False), request.get('downgrades', False), validation=validation)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + (str(exc) or 'Operation cancelled'), file=sys.stderr, flush=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())
