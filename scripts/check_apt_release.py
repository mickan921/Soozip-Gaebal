#!/usr/bin/env python3
"""Mandatory APT release gate. Required suites must execute without skips.

Build current portable clients first, and supply the completed current-policy SDL
matrix repository plus the unchanged legacy SDL repository. No host APT writes.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ('src', 'Soozip/src', 'Gaebal/src', 'tests')]
from offline_apt_builder import __version__
from offline_apt_builder.install import verify_bundle
from offline_apt_builder.validator import POLICY, read_plan
from build_clients import source_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdl-bundle', type=Path, required=True)
    parser.add_argument('--legacy-sdl-bundle', type=Path, required=True)
    parser.add_argument('--smoke-archive', type=Path, required=True,
                        help='Small archive produced by the current normal Soozip build')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    for name in ('sdl_bundle', 'legacy_sdl_bundle', 'smoke_archive', 'report'):
        setattr(args, name, getattr(args, name).resolve())
    os.chdir(ROOT)
    report = {'status': 'FAILED', 'version': __version__, 'suites': []}
    try:
        source_sha256 = source_fingerprint()
        report['source_sha256'] = source_sha256
        for app in ('Soozip', 'Gaebal'):
            metadata = json.loads((ROOT / 'dist' / app / 'release.json').read_text())
            if metadata['version'] != __version__ or metadata.get('source_sha256') != source_sha256:
                raise RuntimeError(f'Rebuild {app} before running the release gate.')
        manifest = verify_bundle(args.sdl_bundle.resolve())
        if manifest.get('validation_policy') != POLICY or manifest.get('tool_version') != __version__:
            raise RuntimeError('SDL acceptance needs the current complete validation matrix.')
        if set(manifest['requested']) != {'libsdl2-dev', 'libsdl2-image-dev', 'libsdl2-mixer-dev', 'libsdl2-ttf-dev'}:
            raise RuntimeError('The acceptance repository must contain all four SDL requests.')
        plan = read_plan(args.sdl_bundle.resolve())
        if not any(len(s.get('seed', [])) == 1 and s['seed'][0].startswith('libjack-jackd2-0=')
                   for s in plan['scenarios']):
            raise RuntimeError('Missing JACK2 runtime-only acceptance scenario.')
        report['sdl_scenarios'] = len(plan['scenarios'])
        report['sdl_build_id'] = manifest['build_id']
        os.environ.update(OFFLINE_APT_INTEGRATION='1', OFFLINE_APT_COMPLEX='1', OFFLINE_APT_JACK2_AUTO='1',
                          OFFLINE_APT_SDL_BUNDLE=str(args.legacy_sdl_bundle.resolve()), QT_QPA_PLATFORM='offscreen',
                          OFFLINE_APT_MATRIX='1', OFFLINE_APT_MATRIX_ARCHIVE=str(args.smoke_archive.resolve()))
        groups = [
            ('apt-safety', ['test_integration.ShellAssertionTests', 'test_integration.InstallerIntegrationTests']),
            ('packaged-desktop', ['test_complex_bundle.ComplexBundleTests']),
            ('packaged-matrix-bundle', ['test_matrix_packaged.MatrixBundleAcceptanceTests']),
            ('legacy-sdl-jack2', ['test_jack2_automatic.AutomaticJack2AcceptanceTests']),
        ]
        for name, tests in groups:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(tests))
            report['suites'].append({'name': name, 'executed': result.testsRun,
                'skipped': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors)})
            if not result.wasSuccessful() or result.skipped or not result.testsRun:
                raise RuntimeError(f'Required release suite {name} failed or skipped tests.')
        if source_fingerprint() != source_sha256:
            raise RuntimeError('Source changed during the release checks; rebuild and retest.')
        report['status'] = 'PASSED'
        return 0
    except Exception as exc:
        report['error'] = str(exc)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
