"""Publish archives without exposing partial output as a finished bundle."""

import ctypes
import os
import sys
import shutil
import tempfile
from pathlib import Path

from .archive import extract_archive, inventory, run7
from .repository import sha256
from .models import BuildError


def publish_file(source: Path, destination: Path) -> None:
    """Atomic no-clobber rename on Ubuntu, including removable filesystems."""
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
    else:
        # Development/test fallback. Production builds require Ubuntu/glibc.
        os.link(source, destination)
        source.unlink()


def package_bundle(bundle: Path, output: Path, name: str) -> tuple[Path, Path, str]:
    if not name.endswith(".7z"):
        raise ValueError("Bundle archives must use .7z")
    archive = output / name
    checksum = output / (name + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError(f"Refusing to overwrite {archive}")
    fd, temporary = tempfile.mkstemp(prefix=".offline-apt-", suffix=".part", dir=output)
    os.close(fd)
    partial = Path(temporary)
    partial_sum = partial.with_suffix(".sha256.part")
    published = False
    try:
        required = 2 * sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()) + 64 * 1024**2
        if shutil.disk_usage(tempfile.gettempdir()).free < required:
            raise BuildError(f"Archive verification requires {required:,} temporary free bytes.")
        partial.unlink()  # 7zz must create a new archive, not update an empty file.
        with tempfile.TemporaryDirectory(prefix="offline-apt-pack-") as temporary_root:
            staging = Path(temporary_root)
            root_name = 'offline-pip' if bundle.name == 'offline-pip' else 'offline-apt'
            source = staging / root_name
            before = inventory(bundle)
            shutil.copytree(bundle, source)
            run7(["a", "-t7z", "-m0=lzma2", "-mx=5", "-ms=off", str(partial), root_name], cwd=staging)
            extracted = staging / "roundtrip"
            extracted.mkdir()
            restored = extract_archive(partial, extracted, check_suffix=False)
            if inventory(restored) != before:
                raise ValueError("Archive round-trip verification failed")
        digest = sha256(partial)
        partial_sum.write_text(f"{digest}  {name}\n", encoding="utf-8")
        for path in (partial, partial_sum):
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        publish_file(partial, archive)
        published = True
        publish_file(partial_sum, checksum)
        return archive, checksum, digest
    except BaseException:
        if published:
            archive.unlink(missing_ok=True)
        raise
    finally:
        partial.unlink(missing_ok=True)
        partial_sum.unlink(missing_ok=True)
