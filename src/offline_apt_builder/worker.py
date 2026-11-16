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
from .runner import redact


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, default=str), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['build', 'preview', 'install'])
    parser.add_argument('--request', required=True)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        request = json.loads(args.request)
        if args.operation == 'build':
            result = build(BuildRequest(read_packages(request['packages'], [Path(p) for p in request.get('files', [])]),
                                        Path(request['output']), with_recommends=request.get('with_recommends', False)),
                           progress=lambda e: emit('progress', **asdict(e)),
                           emit_log=lambda line: emit('log', message=line))
            emit('result', **asdict(result))
        elif args.operation == 'preview':
            archive = Path(request['archive'])
            verify_sidecar(archive)
            emit('progress', stage='verify', message='Verifying and extracting the archive…')
            bundle = extract_archive(archive, Path(request['workspace']))
            manifest = verify_bundle(bundle)
            emit('bundle', manifest=manifest, bundle=str(bundle), integrity='Verified')
            plan = preview(bundle, emit)
            emit('plan', **asdict(plan))
            emit('result', status='READY')
        else:
            install(Path(request['bundle']), request['approval_token'], request.get('upgrades', False),
                    request.get('downgrades', False), emit)
            emit('result', status='INSTALLED')
        return 0
    except KeyboardInterrupt:
        emit('error', message='Operation cancelled.')
        return 130
    except Exception as exc:
        emit('error', message=redact(str(exc)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
