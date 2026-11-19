"""Run the real Docker build lifecycle with a cancellable resolver fixture."""
import json
from pathlib import Path
import signal
import sys
from unittest.mock import patch

from offline_apt_builder.models import PipBuildRequest
from offline_apt_builder.pip_build import build_pip
from offline_apt_builder.runner import Runner
from pip_test_support import cached_assets

original_run = Runner.run

def run(runner, command, **kwargs):
    if command[:2] == ['docker', 'run'] and 'resolve' in command:
        name = command[command.index('--name') + 1]
        workspace = Path(command[command.index('--volume') + 1].split(':')[0]).parent
        print('QA_JOB=' + json.dumps({'container': name, 'workspace': str(workspace)}), flush=True)
        script = command.index('/bundle/install-pip.py')
        command = command[:script] + ['-c', 'import time;print("QA_RESOLVER_STARTED",flush=True);time.sleep(120)']
    return original_run(runner, command, **kwargs)

signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
try:
    with cached_assets(), patch.object(Runner, 'run', run):
        build_pip(PipBuildRequest(('requests',), Path(sys.argv[1])), emit_log=lambda line: print(line, flush=True))
except KeyboardInterrupt:
    raise SystemExit(130)
