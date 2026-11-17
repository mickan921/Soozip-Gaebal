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


def deb(name, version="1", extra="", broken=False, conffile=False, scripts=None):
    tree = ROOT / f"source-{name}-{version}"
    (tree / "DEBIAN").mkdir(parents=True)
    control = f"Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Offline APT tests <nobody@example.invalid>\nDescription: Disposable offline installer test fixture\n{extra}"
    (tree / "DEBIAN/control").write_text(control)
    if conffile:
        (tree / 'etc').mkdir()
        (tree / 'etc/offline-test.conf').write_text(f'default version {version}\n')
        (tree / 'DEBIAN/conffiles').write_text('/etc/offline-test.conf\n')
    if broken:
        script = tree / "DEBIAN/postinst"
        script.write_text("#!/bin/sh\necho 'intentional fixture postinst failure' >&2\nexit 17\n")
        script.chmod(0o755)
    for phase, body in (scripts or {}).items():
        script = tree / 'DEBIAN' / phase
        script.write_text('#!/bin/sh\nset -eu\n' + body)
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
config_old = deb('offline-test-config', '1', conffile=True)
config_new = deb('offline-test-config', '2', conffile=True)


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
    targets = [target] if isinstance(target, str) else target
    selections = []
    for value in targets:
        actual, version = value.split('=', 1)
        selections.append({'requested': requested or actual, 'name': actual, 'version': version})
    requests = [s['requested'] for s in selections]
    (destination / "packages.txt").write_text('\n'.join(requests) + "\n")
    (destination / "install-targets.txt").write_text('\n'.join(targets) + "\n")
    (destination / "build-info.txt").write_text("Disposable integration fixture. UNTESTED.\n")
    (destination / "README-offline.txt").write_text("Disposable test fixture only.\n")
    write_json(destination / "manifest.json", {"schema_version": 1, "requested": requests, "packages": records,
                                             "selections": selections})
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
bundle('config', 'offline-test-config=2', [config_new])


def ordered_deb(name, version='1', extra='', configured=(), preconfigured=()):
    """Fail inside dpkg if it configures/unpacks a dependent too early."""
    def phase_script(phase, dependencies):
        body = ''
        for dependency, expected in dependencies:
            body += (f'test "$(dpkg-query -W -f=\'${{db:Status-Status}} ${{Version}}\' '
                     f'offline-complex-{dependency})" = "installed {expected}"\n')
        body += f'echo "{phase} {name} {version}" >> /var/log/offline-complex-order.log\n'
        return body
    return deb('offline-complex-' + name, version, extra, scripts={
        'preinst': phase_script('preinst', preconfigured),
        'postinst': phase_script('postinst', configured),
    })


foundation = ordered_deb('foundation')
common = ordered_deb('common')
preflight = ordered_deb('pre', extra='Depends: offline-complex-foundation (= 1)\n', configured=[('foundation', '1')])
runtime_old = ordered_deb('runtime', '1', 'Pre-Depends: offline-complex-pre (= 1)\n', preconfigured=[('pre', '1')])
runtime_new = ordered_deb('runtime', '2', 'Pre-Depends: offline-complex-pre (= 1)\nDepends: offline-complex-foundation (= 1)\n',
                          preconfigured=[('pre', '1')], configured=[('foundation', '1')])
engine_old = ordered_deb('engine', '1', 'Provides: offline-complex-engine-api (= 1)\n')
engine_new = ordered_deb('engine', '2', 'Provides: offline-complex-engine-api (= 2)\n')
core_old = ordered_deb('core', '1', 'Depends: offline-complex-runtime (>= 1)\n', configured=[('runtime', '1')])
core_new = ordered_deb('core', '2', 'Depends: offline-complex-runtime (= 2), offline-complex-common (= 1), offline-complex-engine-api (>= 2) | offline-complex-fallback\n',
                       configured=[('runtime', '2'), ('common', '1'), ('engine', '2')])
plugin = ordered_deb('plugin', extra='Depends: offline-complex-runtime (= 2), offline-complex-common (= 1)\n',
                     configured=[('runtime', '2'), ('common', '1')])
service_old = ordered_deb('service', '3', 'Depends: offline-complex-core (>= 1)\n', configured=[('core', '1')])
service_new = ordered_deb('service', '2', 'Depends: offline-complex-core (= 2), offline-complex-plugin (= 1)\n',
                          configured=[('core', '2'), ('plugin', '1')])
cli = ordered_deb('cli', '2', 'Depends: offline-complex-core (= 2)\n', configured=[('core', '2')])
compat_old = ordered_deb('compat', '2')
compat_new = ordered_deb('compat')
suite = ordered_deb('suite', '2', 'Depends: offline-complex-service (= 2), offline-complex-cli (= 2), offline-complex-compat (= 1)\n',
                    configured=[('service', '2'), ('cli', '2'), ('compat', '1')])
blocker = deb('offline-complex-blocker', extra='Depends: offline-complex-runtime (= 1)\n')
# Dependents come first deliberately: input/archive order must not drive dpkg.
bundle('complex', ['offline-complex-suite=2', 'offline-complex-service=2', 'offline-complex-compat=1',
                   'offline-complex-common=1', 'offline-complex-foundation=1'],
       [suite, service_new, cli, core_new, plugin, runtime_new, engine_new, compat_new, common, preflight, foundation])

uid, gid = int(os.environ["BUILDER_UID"]), int(os.environ["BUILDER_GID"])
for root, directories, filenames in os.walk(ROOT):
    for name in directories + filenames:
        os.chown(os.path.join(root, name), uid, gid)
os.chown(ROOT, uid, gid)
