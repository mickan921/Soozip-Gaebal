"""JSON-lines process boundary for desktop jobs; no Qt dependencies."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys

from .archive import extract_archive, verify_sidecar
from .build import build
from .inputs import read_packages
from .install import install, preview, verify_bundle
from .models import BuildRequest
from .models import PipBuildRequest, PipInstallTarget
from .pip_inputs import read_requirements
from .pip_build import build_pip
from .pip_install import preview_pip, install_pip, discover_interpreters
from .runner import redact


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, default=str), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['build', 'inspect', 'preview', 'install'])
    parser.add_argument('--request', required=True)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        request = json.loads(args.request)
        if args.operation == 'build':
            if request.get('kind', 'apt') == 'pip':
                result = build_pip(PipBuildRequest(read_requirements(request['requirements'], request.get('files', [])),
                                   Path(request['output']), request.get('python_version', '3.12')),
                                   progress=lambda e: emit('progress', **asdict(e)),
                                   emit_log=lambda line: emit('log', message=line))
            else:
                result = build(BuildRequest(read_packages(request['packages'], [Path(p) for p in request.get('files', [])]),
                                        Path(request['output']), with_recommends=request.get('with_recommends', False)),
                           progress=lambda e: emit('progress', **asdict(e)),
                           emit_log=lambda line: emit('log', message=line))
            emit('result', **asdict(result))
        elif args.operation in {'inspect', 'preview'}:
            if 'archive' in request:
                archive = Path(request['archive'])
                verify_sidecar(archive)
                emit('progress', stage='verify', message='Verifying and extracting the archive…')
                bundle = extract_archive(archive, Path(request['workspace']))
            else:
                bundle = Path(request['bundle'])
            manifest = verify_bundle(bundle)
            kind = manifest.get('bundle_type', 'apt')
            if 'archive' in request and bundle.name != 'offline-' + kind:
                raise ValueError('Bundle root and manifest type disagree')
            emit('bundle', manifest=manifest, bundle=str(bundle), integrity='Verified',
                 validation=(json.loads((bundle / 'test-report.json').read_text()) if kind == 'apt' else None),
                 interpreters=discover_interpreters(manifest['target']['python_version']) if kind == 'pip' and args.operation == 'inspect' else [])
            if args.operation == 'inspect':
                emit('result', status='VERIFIED')
            else:
                plan = preview_pip(bundle, pip_target(request), emit) if kind == 'pip' else preview(bundle, emit)
                emit('plan', **asdict(plan))
                emit('result', status='READY')
        else:
            bundle = Path(request['bundle'])
            manifest = verify_bundle(bundle)
            if manifest.get('bundle_type') == 'pip':
                target = pip_target(request)
                install_pip(bundle, target, request['approval_token'], request.get('upgrades', False), request.get('downgrades', False), emit)
                emit('result', status='INSTALLED', venv=str(target.venv))
            else:
                install(bundle, request['approval_token'], request.get('upgrades', False),
                    request.get('downgrades', False), emit)
                emit('result', status='INSTALLED')
        return 0
    except KeyboardInterrupt:
        emit('error', message='Operation cancelled.')
        return 130
    except Exception as exc:
        diagnostics = getattr(exc, 'diagnostics', None)
        emit('error', message=redact(str(exc)), **({'diagnostics': diagnostics} if diagnostics else {}))
        return 1


def pip_target(request):
    value = request['target']
    if not isinstance(value['create'], bool):
        raise ValueError('Invalid virtualenv mode')
    venv = Path(value['venv']).expanduser()
    python = Path(value['python']).expanduser()
    if not venv.is_absolute() or not python.is_absolute():
        raise ValueError('Select absolute interpreter and virtualenv paths')
    return PipInstallTarget(python, venv, value['create'])


if __name__ == '__main__':
    raise SystemExit(main())
