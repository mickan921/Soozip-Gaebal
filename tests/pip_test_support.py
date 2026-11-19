"""Optional checksum-verified asset reuse for slow, opt-in Docker tests."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

from offline_apt_builder.pip_build import download
from offline_apt_builder.repository import sha256
from offline_apt_builder.resources.pip_engine import TOOLS


@contextmanager
def cached_assets():
    # Set to a directory containing prior build .report.json files. Package
    # resolution remains live; only pinned runtime/tooling bytes may be reused.
    location = os.environ.get('OFFLINE_PIP_CACHE_REPORTS')
    cache = {}
    if location:
        for report in sorted(Path(location).glob('*.report.json')):
            try:
                data = json.loads(report.read_text())
                if data.get('status') != 'VALIDATED':
                    continue
                work = Path(data['workdir'])
                runtime = data['runtime']
                candidates = [(runtime['sha256'], work / 'context/python.tar.gz')]
                candidates += [(item['sha256'], work / 'offline-pip/tooling' / name) for name, item in TOOLS.items()]
                for expected, source in candidates:
                    if expected not in cache and source.is_file() and sha256(source) == expected:
                        cache[expected] = source
            except (OSError, KeyError, ValueError):
                continue

    def fetch(url, destination, expected):
        source = cache.get(expected)
        if source:
            if sha256(source) != expected:
                raise ValueError('Cached asset checksum changed')
            shutil.copyfile(source, destination)
            if sha256(destination) != expected:
                raise ValueError('Copied asset checksum mismatch')
        else:
            download(url, destination, expected)

    with patch('offline_apt_builder.pip_build.download', fetch):
        yield
