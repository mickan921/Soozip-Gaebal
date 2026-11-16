"""UI-independent requests, progress events, and build results."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class BuildError(Exception):
    """An actionable build failure, optionally accompanied by saved diagnostics."""

    def __init__(self, message: str, report: Path | None = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class BuildRequest:
    packages: tuple[str, ...]
    output: Path
    arch: str = "amd64"
    with_recommends: bool = False
    skip_test: bool = False
    keep_workdir: bool = False


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str


@dataclass(frozen=True)
class BuildResult:
    status: str
    archive: Path
    checksum: Path
    sha256: str
    package_count: int
    size_bytes: int
    report: Path
    workdir: Path | None = None


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True)
class PackageAction:
    name: str
    action: str
    old_version: str
    new_version: str


@dataclass(frozen=True)
class InstallPlan:
    actions: tuple[PackageAction, ...]
    blockers: tuple[str, ...]
    approval_token: str
