"""Online wheel resolution and network-disabled installation validation."""
from datetime import datetime, timezone
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import uuid

from . import __version__
from .archive import inventory, sevenzip
from .build import check_host, MIN_FREE_BYTES
from .models import BuildError, BuildResult, PipBuildRequest, ProgressEvent
from .packaging import package_bundle
from .pip_inputs import PYTHON_VERSIONS, read_requirements
from .repository import sha256, write_checksums, write_json
from .resources.pip_engine import TOOLS, verify_payload
from .runner import Runner, redact


def download(url, destination, expected):
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    if sha256(destination) != expected:
        raise BuildError('Downloaded tooling checksum mismatch: ' + destination.name)


def prepare_bundle_permissions(bundle: Path) -> None:
    """Make the read-only payload accessible to the unprivileged validator.

    The enclosing mkdtemp workspace stays private (0700). Only the payload
    mounted into the container is normalized; the caller's umask is unchanged.
    Reject links/special files before changing any permissions.
    """
    if bundle.is_symlink() or not bundle.is_dir():
        raise BuildError('Expected a real pip bundle directory')
    inventory(bundle)
    for path in [bundle, *bundle.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644, follow_symlinks=False)


def build_pip(request: PipBuildRequest, progress=None, emit_log=None) -> BuildResult:
    requirements = read_requirements(request.requirements)
    if request.python_version not in PYTHON_VERSIONS:
        raise BuildError('Supported Python versions: ' + ', '.join(PYTHON_VERSIONS))
    check_host('amd64')
    sevenzip()
    output = request.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(shutil.disk_usage(p).free < MIN_FREE_BYTES for p in (output, Path('/tmp'))):
        raise BuildError('At least 256 MiB free is required; large wheel sets need several GiB.')
    created = datetime.now(timezone.utc)
    build_id = created.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    stem = f'ubuntu24.04-amd64-pip-py{request.python_version}-{build_id}'
    report_path = output / (stem + '.report.json')
    runner = Runner(output / (stem + '.build.log'), emit_log)
    report = {'schema_version': 1, 'bundle_type': 'pip', 'build_id': build_id, 'status': 'FAILED',
              'requested': requirements, 'created_utc': created.isoformat(), 'tool_version': __version__}
    work = Path(tempfile.mkdtemp(prefix='offline-pip-'))
    image_tag = 'offline-pip-runtime:' + build_id
    containers, published = [], []
    image_started = False

    def event(stage, message):
        runner.write(f'[{stage}] {message}\n')
        if progress:
            progress(ProgressEvent(stage, message))

    try:
        if request.keep_workdir:
            report['workdir'] = str(work)
        event('preflight', 'Checking Docker and preparing pinned Python tooling.')
        if runner.run(['docker', 'info', '--format', '{{.OSType}}'], capture=True).strip() != 'linux':
            raise BuildError('A local Linux Docker Engine is required; verify docker info works without sudo.')
        resources = files('offline_apt_builder').joinpath('resources')
        runtime = json.loads(resources.joinpath('pip-runtimes.json').read_text())[request.python_version]
        context = work / 'context'
        context.mkdir()
        download(runtime['url'], context / 'python.tar.gz', runtime['sha256'])
        (context / 'Dockerfile').write_bytes(resources.joinpath('Dockerfile.pip').read_bytes())
        bundle = work / 'offline-pip'
        (bundle / 'tooling').mkdir(parents=True)
        (bundle / 'wheelhouse').mkdir()
        for name, tool in TOOLS.items():
            download(tool['url'], bundle / 'tooling' / name, tool['sha256'])
        (bundle / 'install-pip.py').write_bytes(resources.joinpath('pip_engine.py').read_bytes())
        (bundle / 'requirements.txt').write_text('\n'.join(requirements) + '\n')
        (bundle / 'README-offline.txt').write_text(
            'Open this VALIDATED pip bundle in matching Gaebal. Requires Ubuntu 24.04 amd64 and installed '
            f'CPython {request.python_version} with venv support. Select a new or existing isolated virtualenv.\n'
            'Only local wheels are used. Native system libraries are not included. Existing environment '
            'changes are not rolled back after installation errors.\n'
            'tooling/ contains unmodified pip and packaging wheels including their licenses.\n')
        event('prepare', 'Preparing Ubuntu 24.04 with the selected CPython runtime.')
        runner.run(['docker', 'pull', '--platform', 'linux/amd64', 'ubuntu:24.04'])
        base = json.loads(runner.run(['docker', 'image', 'inspect', '--format', '{{json .}}', 'ubuntu:24.04'], capture=True))
        if base['Architecture'] != 'amd64' or base['Os'] != 'linux' or not base.get('RepoDigests'):
            raise BuildError('Docker returned an unsupported or unpinned Ubuntu base')
        image_started = True
        runner.run(['docker', 'build', '--pull=false', '--platform', 'linux/amd64', '--build-arg',
                    'BASE_IMAGE=' + base['RepoDigests'][0], '-t', image_tag, str(context)])
        image_id = runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_tag], capture=True).strip()
        event('resolve', 'Downloading compatible PyPI wheels and all transitive dependencies.')
        resolver = 'offline-pip-resolve-' + build_id
        containers.append(resolver)
        runner.run(['docker', 'run', '--rm', '--pull', 'never', '--name', resolver, '--platform', 'linux/amd64',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--volume', f'{bundle}:/bundle', image_id,
                    '/bundle/install-pip.py', 'resolve', '--bundle', '/bundle'])
        containers.remove(resolver)
        manifest = json.loads((bundle / 'manifest.json').read_text())
        if manifest['requested'] != list(requirements) or manifest['target']['python_full_version'] != runtime['version']:
            raise BuildError('Resolver result does not match the requested Python or requirements')
        manifest.update(build_id=build_id, created_utc=created.isoformat(), tool_version=__version__,
                        base_image={'id': base['Id'], 'repo_digests': base['RepoDigests']},
                        runtime_image=image_id, runtime=runtime, validation_status='PENDING')
        write_json(bundle / 'manifest.json', manifest)
        write_json(bundle / 'test-report.json', {'status': 'PENDING'})
        write_checksums(bundle)
        prepare_bundle_permissions(bundle)
        verify_payload(bundle, require_validated=False)
        frozen = inventory(bundle)
        event('test', 'Installing the locked wheels in a fresh virtualenv with network=none.')
        validator = 'offline-pip-test-' + build_id
        containers.append(validator)
        # The validator runs as an unprivileged user against a read-only payload.
        request_json = json.dumps({'target': {'venv': '/tmp/validated-venv', 'create': True}})
        runner.run(['docker', 'create', '--name', validator, '--pull', 'never', '--network', 'none',
                    '--platform', 'linux/amd64', '--user', '65534:65534', '--volume', f'{bundle}:/bundle:ro',
                    image_id, '/bundle/install-pip.py', 'validate', '--bundle', '/bundle', '--request', request_json])
        if runner.run(['docker', 'inspect', '--format', '{{.HostConfig.NetworkMode}}', validator], capture=True).strip() != 'none':
            raise BuildError('Validation requires network=none')
        validation_log = runner.run(['docker', 'start', '--attach', validator], capture=True, check=False)
        (work / 'validation.log').write_text(redact(validation_log))
        code = int(runner.run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', validator], capture=True).strip())
        installed = [json.loads(line.removeprefix('PIP_INSTALLED=')) for line in validation_log.splitlines() if line.startswith('PIP_INSTALLED=')]
        expected = {p['name']: p['version'] for p in manifest['packages']}
        test = {'status': 'FAILED', 'network_mode': 'none', 'exit_code': code,
                'base_image': base['Id'], 'runtime_image': image_id, 'python_full_version': runtime['version'],
                'installed_versions': installed[0] if len(installed) == 1 else {}, 'dependency_check': False}
        write_json(work / 'test-report.json', test)
        if code or installed != [expected]:
            raise BuildError('Offline pip validation failed. Review the saved validation log.')
        if inventory(bundle) != frozen:
            raise BuildError('Tested payload changed during validation')
        test.update(status='VALIDATED', dependency_check=True)
        manifest['validation_status'] = 'VALIDATED'
        write_json(bundle / 'manifest.json', manifest)
        write_json(bundle / 'test-report.json', test)
        (bundle / 'logs').mkdir()
        shutil.copyfile(work / 'validation.log', bundle / 'logs/validation.log')
        shutil.copyfile(runner.log_path, bundle / 'logs/build.log')
        write_checksums(bundle)
        prepare_bundle_permissions(bundle)
        verify_payload(bundle)
        event('package', f"Packing {len(expected)} validated Python packages.")
        needed = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) + 64 * 1024**2
        if shutil.disk_usage(output).free < needed:
            raise BuildError(f'Publishing requires approximately {needed:,} free bytes')
        archive, checksum, digest = package_bundle(bundle, output, stem + '.7z')
        published = [archive, checksum]
        report.update(status='VALIDATED', test=test, archive=str(archive), checksum=str(checksum),
                      sha256=digest, package_count=len(expected), size_bytes=archive.stat().st_size,
                      runtime=runtime, base_image=manifest['base_image'], runtime_image=image_id)
        write_json(report_path, report)
        event('complete', f'VALIDATED: {archive}')
        return BuildResult('VALIDATED', archive, checksum, digest, len(expected), archive.stat().st_size,
                           report_path, work if request.keep_workdir else None)
    except BaseException as exc:
        for path in published:
            path.unlink(missing_ok=True)
        report.update(status='FAILED', error=redact(str(exc)) or type(exc).__name__)
        for name in ('validation.log', 'test-report.json'):
            if (work / name).exists():
                destination = output / (stem + '.' + name)
                shutil.copyfile(work / name, destination)
                report[name] = str(destination)
        write_json(report_path, report)
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise BuildError(redact(str(exc)), report_path) from exc
    finally:
        for container in containers:
            try:
                runner.run(['docker', 'rm', '--force', container], check=False)
            except BuildError as exc:
                runner.write(str(exc) + '\n')
        if image_started:
            try:
                runner.run(['docker', 'image', 'rm', image_tag], check=False)
            except BuildError as exc:
                runner.write(str(exc) + '\n')
        if not request.keep_workdir:
            try:
                shutil.rmtree(work)
            except OSError as exc:
                runner.write(f'Workspace cleanup failed: {work}: {exc}\n')
        runner.close()
