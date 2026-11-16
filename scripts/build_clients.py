#!/usr/bin/env python3
"""Build two portable Ubuntu clients, including a checksum-pinned extractor."""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '25.01'
URL = 'https://www.7-zip.org/a/7z2501-linux-x64.tar.xz'
DIGEST = '4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4'


def main():
    release = platform.freedesktop_os_release() if sys.platform.startswith('linux') else {}
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04' or platform.machine() != 'x86_64':
        raise SystemExit('Release clients must be built on Ubuntu 24.04 amd64.')
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
    for role in ('online', 'offline'):
        folder = ROOT / 'dist' / f'offline-apt-{role}'
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
        (folder / 'THIRD-PARTY.txt').write_text(
            'Python: https://www.python.org/psf/license/\n'
            'Qt/PySide6: https://code.qt.io/pyside/pyside-setup/ (LGPL/GPL; see licenses/)\n'
            'Qt source: https://download.qt.io/archive/qt/6.10/6.10.1/submodules/\n'
            'PyInstaller: https://pyinstaller.org/ (GPL with bootloader exception)\n'
            f'7-Zip {VERSION}: https://www.7-zip.org/ (LGPL; bundled license)\n'
            '7-Zip source: https://www.7-zip.org/a/7z2501-src.tar.xz\n')
        (folder / 'release.json').write_text(json.dumps({'platform': 'Ubuntu 24.04 amd64', '7zip': VERSION,
            '7zip_source_sha256': DIGEST, 'dependencies': (ROOT / 'packaging/requirements-release.txt').read_text().splitlines()}, indent=2))
        destination = ROOT / 'dist' / f'offline-apt-{role}-ubuntu24.04-amd64.tar.gz'
        with tarfile.open(destination, 'w:gz') as tar:
            tar.add(folder, arcname=folder.name)
        with destination.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        Path(str(destination) + '.sha256').write_text(f'{digest}  {destination.name}\n')
        print(destination)


if __name__ == '__main__':
    main()
