"""Deliberately small input language: Debian package names, not APT selectors."""

import re
from pathlib import Path

from .models import BuildError

PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]+\Z", re.ASCII)


def validate_packages(names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for name in names:
        if not PACKAGE_NAME.fullmatch(name):
            raise BuildError(f"Invalid package name {name!r}; use names only, without versions, options, or architecture qualifiers.")
        if name not in result:
            result.append(name)
    if not result:
        raise BuildError("Specify at least one package or a nonempty --packages-file.")
    return tuple(result)


def read_packages(names: list[str], files: list[Path]) -> tuple[str, ...]:
    combined = list(names)
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BuildError(f"Cannot read package file {path}: {exc}") from exc
        for line in content.splitlines():
            combined.extend(line.split("#", 1)[0].split())
    return validate_packages(combined)
