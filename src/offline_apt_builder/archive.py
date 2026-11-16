"""Strict 7z transport handling shared by publishing and importing."""
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

from .models import BuildError
from .repository import sha256


def sevenzip() -> str:
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'bin' / '7zz'
    if bundled.is_file():
        return str(bundled)
    for name in ('7zz', '7z'):
        if executable := shutil.which(name):
            return executable
    raise BuildError('7-Zip is missing. Use the portable client or install the Ubuntu 7zip package online.')


def run7(args: list[str], *, cwd=None) -> str:
    process = subprocess.Popen([sevenzip(), *args], cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               errors='replace', env={**os.environ, 'LC_ALL': 'C'})
    try:
        output, _ = process.communicate()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if process.returncode:
        raise BuildError(f'7-Zip failed (exit {process.returncode}): {output[-4000:]}')
    return output


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BuildError(f'Unsafe bundle entry: {path.name}')
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r'[A-Za-z0-9_+.,@%:= /#~-]+', relative):
            raise BuildError(f'Unsupported bundle filename: {relative!r}')
        result[relative] = sha256(path) if path.is_file() else 'directory'
    return result


def parse_listing(output: str) -> dict[str, tuple[bool, int]]:
    try:
        header, body = output.split('----------\n', 1)
    except ValueError as exc:
        raise BuildError('Unrecognized 7-Zip listing.') from exc
    if not re.search(r'^Type = 7z$', header, re.M) or re.search(r'^(Volumes|Volume Index|Total Physical Size) =', header, re.M):
        raise BuildError('Only single-volume 7z archives are supported.')
    entries = {}
    for block in body.strip().split('\n\n'):
        if not block.strip():
            continue
        fields = {}
        for line in block.splitlines():
            if ' = ' not in line:
                raise BuildError('Malformed archive entry.')
            key, value = line.split(' = ', 1)
            if key in fields:
                raise BuildError('Duplicate archive entry field.')
            fields[key] = value
        name = fields.get('Path', '')
        path = PurePosixPath(name)
        if (not re.fullmatch(r'[A-Za-z0-9_+.,@%:= /#~-]+', name) or path.is_absolute()
                or '..' in path.parts or str(path) != name or not path.parts
                or path.parts[0] != 'offline-apt' or name in entries):
            raise BuildError(f'Unsafe or duplicate archive path: {name!r}')
        attrs = fields.get('Attributes', '')
        unix_modes = re.findall(r'(?<!\S)([bcdlps-][rwxstST-]{9})(?!\S)', attrs)
        if (fields.get('Encrypted') == '+' or any('link' in k.lower() for k in fields)
                or any(mode[0] not in '-d' for mode in unix_modes)):
            raise BuildError('Encrypted archives, links, and special files are unsupported.')
        directory = fields.get('Folder') == '+' or attrs.startswith('D') or any(m[0] == 'd' for m in unix_modes)
        try:
            size = int(fields.get('Size', '0'))
        except ValueError as exc:
            raise BuildError('Invalid archive size.') from exc
        if size < 0 or (directory and size):
            raise BuildError('Invalid archive size.')
        entries[name] = (directory, size)
    if not entries or entries.get('offline-apt', (True, 0))[0] is False:
        raise BuildError('Missing bundle directory.')
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if str(parent) in entries and not entries[str(parent)][0]:
                raise BuildError('Conflicting archive paths.')
    return entries


def extract_archive(archive: Path, destination: Path, *, check_suffix=True) -> Path:
    archive = archive.resolve()
    if check_suffix and archive.suffix != '.7z':
        raise BuildError('Select a single .7z bundle.')
    with archive.open('rb') as stream:
        magic = stream.read(6)
    if magic != b'7z\xbc\xaf\x27\x1c':
        raise BuildError('The selected file is not a 7z archive.')
    entries = parse_listing(run7(['l', '-slt', '-p', '--', str(archive)]))
    if any(destination.iterdir()):
        raise BuildError('Extraction requires an empty private directory.')
    required = sum(size for _, size in entries.values()) + 64 * 1024**2
    if shutil.disk_usage(destination).free < required:
        raise BuildError(f'Extraction requires at least {required:,} free bytes.')
    run7(['t', '-p', '--', str(archive)])
    run7(['x', '-y', '-p', '-o' + str(destination), '--', str(archive)])
    actual = inventory(destination)
    expected_files = {name for name, (directory, _) in entries.items() if not directory}
    if {name for name, value in actual.items() if value != 'directory'} != expected_files:
        raise BuildError('Extracted contents disagree with the archive listing.')
    for name in expected_files:
        if (destination / name).stat().st_size != entries[name][1]:
            raise BuildError('Extracted file size mismatch.')
    return destination / 'offline-apt'


def verify_sidecar(archive: Path) -> None:
    sidecar = Path(str(archive) + '.sha256')
    if sidecar.exists():
        match = re.fullmatch(r'([0-9a-f]{64})  ([^\n]+)\n?', sidecar.read_text())
        if not match or match[2] != archive.name or match[1] != sha256(archive):
            raise BuildError('Companion SHA-256 checksum mismatch; transfer the bundle again.')
