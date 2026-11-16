"""Construct real, tiny .debs for installer safety tests inside Ubuntu tooling."""

from importlib.resources import files
import gzip
import lzma
import os
from pathlib import Path
import shutil
import subprocess

from offline_apt_builder.repository import sha256, write_checksums, write_json

ROOT = Path("/work")
POOL = ROOT / "pool"
POOL.mkdir()


def deb(name, version="1", extra="", broken=False):
    tree = ROOT / f"source-{name}-{version}"
    (tree / "DEBIAN").mkdir(parents=True)
    control = f"Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Offline APT tests <nobody@example.invalid>\nDescription: Disposable offline installer test fixture\n{extra}"
    (tree / "DEBIAN/control").write_text(control)
    if broken:
        script = tree / "DEBIAN/postinst"
        script.write_text("#!/bin/sh\necho 'intentional fixture postinst failure' >&2\nexit 17\n")
        script.chmod(0o755)
    path = POOL / f"{name}_{version}_all.deb"
    subprocess.run(["dpkg-deb", "--build", str(tree), str(path)], check=True)
    return path


pre = deb("offline-test-pre")
dep = deb("offline-test-dep")
rec = deb("offline-test-recommend")
old = deb("offline-test-tool", "1")
tool = deb("offline-test-tool", "2", "Pre-Depends: offline-test-pre (>= 1)\nDepends: offline-test-dep | offline-test-alternative\nRecommends: offline-test-recommend\nProvides: offline-test-virtual\n")
existing = deb("offline-test-existing")
conflict = deb("offline-test-conflict", extra="Conflicts: offline-test-existing\n")
broken = deb("offline-test-broken", broken=True)


def bundle(name, target, packages, recommends=False, requested=None, arch="amd64"):
    destination = ROOT / name
    repo = destination / "repo"
    repo.mkdir(parents=True)
    records = []
    for path in packages:
        shutil.copyfile(path, repo / path.name)
        fields = subprocess.check_output(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"], text=True)
        values = dict(line.split(": ", 1) for line in fields.splitlines())
        records.append({"name": values["Package"], "version": values["Version"], "architecture": values["Architecture"],
                        "filename": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size})
    with (repo / "Packages").open("wb") as stream:
        subprocess.run(["dpkg-scanpackages", ".", "/dev/null"], cwd=repo, stdout=stream, check=True)
    index = (repo / "Packages").read_bytes()
    (repo / "Packages.gz").write_bytes(gzip.compress(index, mtime=0))
    (repo / "Packages.xz").write_bytes(lzma.compress(index))
    resources = files("offline_apt_builder").joinpath("resources")
    (destination / "install.sh").write_bytes(resources.joinpath("install.sh").read_bytes())
    (destination / "install.sh").chmod(0o755)
    (destination / "architecture.txt").write_text(arch + "\n")
    (destination / "with-recommends.txt").write_text(str(int(recommends)) + "\n")
    actual, version = target.split("=", 1)
    (destination / "packages.txt").write_text((requested or actual) + "\n")
    (destination / "install-targets.txt").write_text(target + "\n")
    (destination / "build-info.txt").write_text("Disposable integration fixture. UNTESTED.\n")
    (destination / "README-offline.txt").write_text("Disposable test fixture only.\n")
    write_json(destination / "manifest.json", {"schema_version": 1, "requested": [requested or actual], "packages": records,
                                             "selections": [{"requested": requested or actual, "name": actual, "version": version}]})
    write_json(destination / "test-report.json", {"status": "UNTESTED", "reason": "disposable fixture"})
    write_checksums(destination)


bundle("valid", "offline-test-tool=2", [pre, dep, rec, tool])
bundle("recommended", "offline-test-tool=2", [pre, dep, rec, tool], recommends=True)
bundle("virtual", "offline-test-tool=2", [pre, dep, tool], requested="offline-test-virtual")
bundle("old", "offline-test-tool=1", [old])
bundle("removal", "offline-test-conflict=1", [conflict])
bundle("broken", "offline-test-broken=1", [broken])
bundle("wrong-arch", "offline-test-tool=2", [pre, dep, tool], arch="arm64")
bundle("missing", "offline-test-tool=2", [tool])

uid, gid = int(os.environ["BUILDER_UID"]), int(os.environ["BUILDER_GID"])
for root, directories, filenames in os.walk(ROOT):
    for name in directories + filenames:
        os.chown(os.path.join(root, name), uid, gid)
os.chown(ROOT, uid, gid)
