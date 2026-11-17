#!/usr/bin/env python3
"""Build two portable Ubuntu clients, including a checksum-pinned extractor."""
import hashlib
import ctypes
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '25.01'
URL = 'https://www.7-zip.org/a/7z2501-linux-x64.tar.xz'
DIGEST = '4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4'


def verify_runtime(folder):
    internal = folder / '_internal'
    if not (internal / 'libxcb-cursor.so.0').is_file():
        raise SystemExit('Portable Qt runtime is missing libxcb-cursor.so.0; install libxcb-cursor0 on the build host and rebuild.')
    # Audit the collected files without borrowing libraries from a custom build path.
    env = {**os.environ, 'LD_LIBRARY_PATH': str(internal), 'LC_ALL': 'C'}
    paths = [folder / folder.name, folder / 'offline-apt-worker', *internal.rglob('*.so*')]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        result = subprocess.run(['ldd', str(path)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        missing = [line.strip() for line in result.stdout.splitlines() if 'not found' in line]
        if result.returncode or missing:
            raise SystemExit(f'Portable runtime dependency check failed for {path}:\n{result.stdout}')


def main():
    release = platform.freedesktop_os_release() if sys.platform.startswith('linux') else {}
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04' or platform.machine() != 'x86_64':
        raise SystemExit('Release clients must be built on Ubuntu 24.04 amd64.')
    for command in ('objdump', 'objcopy', 'ldd'):
        if not shutil.which(command):
            raise SystemExit(f'Missing build tool {command}; install binutils and libc-bin on the build host.')
    try:
        ctypes.CDLL('libxcb-cursor.so.0')
    except OSError as exc:
        raise SystemExit('Install libxcb-cursor0 on the build host before freezing the portable Qt clients.') from exc
    for line in (ROOT / 'packaging/requirements-release.txt').read_text().splitlines():
        name, version = line.split('==')
        if metadata.version(name) != version:
            raise SystemExit(f'Install pinned release dependencies: {name}=={version} is required.')
    assets = ROOT / 'build/release-assets'
    assets.mkdir(parents=True, exist_ok=True)
    archive = assets / '7zip.tar.xz'
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DIGEST:
        raise SystemExit(f'7-Zip checksum mismatch. Remove {archive} and retry.')
    with tarfile.open(archive) as tar:
        for source, dest in [('7zzs', '7zz'), ('License.txt', '7zip-LICENSE.txt')]:
            # Only extract the two explicitly named regular files.
            member = tar.getmember(source)
            if not member.isfile():
                raise SystemExit('Unexpected release archive entry.')
            (assets / dest).write_bytes(tar.extractfile(member).read())
    (assets / '7zz').chmod(0o755)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', str(ROOT / 'packaging/clients.spec')], cwd=ROOT, check=True)
    # Validate both folders before publishing either release archive.
    for name in ('Soozip', 'Gaebal'):
        verify_runtime(ROOT / 'dist' / name)
    for name in ('Soozip', 'Gaebal'):
        folder = ROOT / 'dist' / name
        notices = folder / 'licenses'
        notices.mkdir(exist_ok=True)
        shutil.copyfile(assets / '7zip-LICENSE.txt', notices / '7zip-LICENSE.txt')
        for distribution in metadata.distributions():
            name = distribution.metadata['Name']
            for item in distribution.files or []:
                if 'license' in str(item).lower() or 'copying' in str(item).lower():
                    source = Path(distribution.locate_file(item))
                    if source.is_file():
                        target = notices / name / str(item)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
        shutil.copyfile(ROOT / 'README.md', folder / 'README.md')
        shutil.copy2(ROOT / folder.name / 'install.sh', folder / 'install.sh')
        shutil.copyfile(ROOT / 'packaging/install-client.sh', folder / '_internal/install-client.sh')
        if folder.name == 'Soozip':
            shutil.copyfile(ROOT / 'packaging/setup-docker.sh', folder / '_internal/setup-docker.sh')
        (folder / 'THIRD-PARTY.txt').write_text(
            'Python: https://www.python.org/psf/license/\n'
            'Qt/PySide6: https://code.qt.io/pyside/pyside-setup/ (LGPL/GPL; see licenses/)\n'
            'Qt source: https://download.qt.io/archive/qt/6.10/6.10.1/submodules/\n'
            'PyInstaller: https://pyinstaller.org/ (GPL with bootloader exception)\n'
            f'7-Zip {VERSION}: https://www.7-zip.org/ (LGPL; bundled license)\n'
            '7-Zip source: https://www.7-zip.org/a/7z2501-src.tar.xz\n')
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        (folder / 'release.json').write_text(json.dumps({'application': folder.name,
            'version': project['version'], 'built_utc': datetime.now(timezone.utc).isoformat(),
            'platform': 'Ubuntu 24.04 amd64', '7zip': VERSION,
            '7zip_source_sha256': DIGEST, 'dependencies': (ROOT / 'packaging/requirements-release.txt').read_text().splitlines()}, indent=2))
        checksums = []
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.name != 'CLIENT-SHA256SUMS':
                relative = path.relative_to(folder).as_posix()
                if '\n' in relative or '\\' in relative:
                    raise SystemExit(f'Unsupported release filename: {relative}')
                with path.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                checksums.append(f'{checksum}  ./{relative}\n')
        (folder / 'CLIENT-SHA256SUMS').write_text(''.join(checksums))
        destination = ROOT / 'dist' / f'{folder.name}-ubuntu24.04-amd64.tar.gz'
        with tarfile.open(destination, 'w:gz') as tar:
            tar.add(folder, arcname=folder.name)
        with destination.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        Path(str(destination) + '.sha256').write_text(f'{digest}  {destination.name}\n')
        print(destination)


if __name__ == '__main__':
    main()
