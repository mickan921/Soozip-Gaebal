"""Thin terminal UI over the build engine."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys

from . import __version__
from .build import build
from .inputs import read_packages
from .models import BuildError, BuildRequest
from .runner import redact
from .pip_build import build_pip
from .pip_inputs import PYTHON_VERSIONS, read_requirements
from .models import PipBuildRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build tested Ubuntu 24.04 amd64 offline APT and pip bundles.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Download, test without networking, and package a local APT repository")
    command.add_argument("packages", nargs="*")
    command.add_argument("--packages-file", action="append", type=Path, default=[])
    command.add_argument("-o", "--output", type=Path, default=Path("offline-output"))
    command.add_argument("--arch", default="amd64", choices=["amd64"])
    command.add_argument("--with-recommends", action="store_true")
    command.add_argument("--skip-test", action="store_true", help="Produce a prominently UNTESTED bundle")
    command.add_argument("--keep-workdir", action="store_true")
    command.add_argument("--verbose", action="store_true")
    command.add_argument("--json", action="store_true", help="Write final result JSON to stdout; progress remains on stderr")
    pip_command = commands.add_parser('build-pip', help='Build and validate an offline Python wheel bundle')
    pip_command.add_argument('requirements', nargs='*')
    pip_command.add_argument('--requirements-file', action='append', type=Path, default=[])
    pip_command.add_argument('--python-version', choices=PYTHON_VERSIONS, default='3.12')
    pip_command.add_argument('-o', '--output', type=Path, default=Path('offline-output'))
    pip_command.add_argument('--keep-workdir', action='store_true')
    pip_command.add_argument('--verbose', action='store_true')
    pip_command.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.command == 'build-pip':
            request = PipBuildRequest(read_requirements(args.requirements, args.requirements_file), args.output,
                                      args.python_version, args.keep_workdir)
            engine = build_pip
        else:
            packages = read_packages(args.packages, args.packages_file)
            request = BuildRequest(packages, args.output, args.arch, args.with_recommends, args.skip_test, args.keep_workdir)
            engine = build
        result = engine(request,
                       progress=lambda event: print(f"[{event.stage}] {event.message}", file=sys.stderr),
                       emit_log=(lambda line: print(line, file=sys.stderr)) if args.verbose else None)
        if args.json:
            print(json.dumps(asdict(result), default=str, sort_keys=True))
        else:
            print(f"{result.status}\nArchive: {result.archive}\nChecksum: {result.checksum}\nSHA-256: {result.sha256}\nPackages: {result.package_count}\nBytes: {result.size_bytes}\nReport: {result.report}")
            if result.workdir:
                print(f"Workspace: {result.workdir}")
        return 0
    except KeyboardInterrupt:
        if args.json:
            print(json.dumps({"status": "FAILED", "error": "Build cancelled"}))
        print("Build cancelled. Containers were stopped; see the output directory for diagnostics.", file=sys.stderr)
        return 130
    except (BuildError, OSError, ValueError) as exc:
        message = redact(str(exc))
        report = getattr(exc, "report", None)
        if args.json:
            print(json.dumps({"status": "FAILED", "error": message, "report": str(report) if report else None}))
        print(f"ERROR: {message}", file=sys.stderr)
        if report:
            print(f"Report: {report}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
