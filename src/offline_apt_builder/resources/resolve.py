#!/usr/bin/env python3
"""Runs only in the controlled Ubuntu tooling container, with /work mounted."""

import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys


def run(args, **kwargs):
    print("+ " + shlex.join(args), flush=True)
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def select_packages(cache, names):
    """Resolve exact names or unambiguous providers, never APT expressions."""
    selections = []
    for name in names:
        if name in cache and cache[name].candidate is not None:
            provider = cache[name]
        else:
            providers = cache.get_providing_packages(name, candidate_only=True)
            explicitly_requested = [p for p in providers if p.name in names]
            candidates = explicitly_requested if len(explicitly_requested) == 1 else providers
            if len(candidates) != 1:
                choices = ", ".join(sorted(p.name for p in providers))
                if providers:
                    raise ValueError(f"Ambiguous virtual package {name!r}. Request a concrete provider: {choices}")
                raise ValueError(f"Unknown or unavailable package {name!r} in official Ubuntu noble repositories.")
            provider = candidates[0]
        selections.append({"requested": name, "name": provider.name,
                           "version": provider.candidate.version,
                           "architecture": provider.candidate.architecture,
                           "virtual": provider.name != name})
    return selections


def main():
    bundle = Path("/work/offline-apt")
    repo = bundle / "repo"
    request = json.loads(Path("/work/request.json").read_text())
    names = request["packages"]
    if not names or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", name, re.ASCII) for name in names):
        raise ValueError("Invalid package-name input")
    state = Path("/work/apt")
    (state / "lists/partial").mkdir(parents=True)
    (state / "empty-parts").mkdir()
    (state / "status").touch()
    (repo / "partial").mkdir(parents=True)
    configuration = {
        "Dir::Etc::parts": str(state / "empty-parts"),
        "Dir::Etc::main": "/dev/null",
        "Dir::Etc::sourcelist": "/etc/apt/sources.list.d/ubuntu.sources",
        "Dir::Etc::sourceparts": str(state / "empty-parts"),
        "Dir::Etc::preferences": "/dev/null",
        "Dir::Etc::preferencesparts": str(state / "empty-parts"),
        "Dir::State::status": str(state / "status"),
        "Dir::State::lists": str(state / "lists"),
        "Dir::State::extended_states": str(state / "extended_states"),
        "Dir::Cache::archives": str(repo),
        # Hundreds of independent companion solves share immutable indexes and
        # one empty status file. Keep caches private to this build, not disabled.
        "Dir::Cache::pkgcache": str(state / "pkgcache.bin"),
        "Dir::Cache::srcpkgcache": str(state / "srcpkgcache.bin"),
        "APT::Architecture": "amd64",
        "APT::Install-Recommends": "true" if request["with_recommends"] else "false",
        "APT::Install-Suggests": "false",
        "APT::Get::Always-Include-Phased-Updates": "true",
        "APT::Update::Error-Mode": "any",
        "Acquire::Languages": "none",
        "Acquire::Retries": "2",
        "Acquire::http::Timeout": "30",
        "Acquire::https::Timeout": "30",
        "APT::Sandbox::User": "root",
    }
    config = state / "apt.conf"
    config.write_text("".join(f'{key} "{value}";\n' for key, value in configuration.items()))
    os.environ["APT_CONFIG"] = str(config)
    os.environ["LC_ALL"] = "C"
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "update"])
    # Import only after APT_CONFIG is set: importing apt initializes libapt.
    import apt
    import apt_pkg
    from apt_compatibility import (PackageVersion, expand_companions, expand_alternatives,
                                   native_provider_choices, parse_simulation)
    from validation_plan import create_plan, version_for

    apt_pkg.init()
    cache = apt.Cache()
    selections = select_packages(cache, names)
    targets = list(dict.fromkeys(f"{s['name']}={s['version']}" for s in selections))
    print('Indexing exact-version companion packages from authenticated Ubuntu metadata...', flush=True)
    available = {}
    for package in cache:
        if ':' in package.name:
            continue
        for version in package.versions:
            if version.architecture not in {'amd64', 'all'}:
                continue
            exact = []
            for field in ('Depends', 'Pre-Depends'):
                for alternatives in apt_pkg.parse_depends(version.record.get(field, '')):
                    for dependency, required_version, relation in alternatives:
                        name, _, qualifier = dependency.partition(':')
                        if relation == '=' and qualifier in {'', 'any', 'native', 'amd64'}:
                            exact.append((name, required_version))
            value = PackageVersion(package.name, version.version, version.architecture,
                                   version.source_name, version.source_version, version.size, tuple(sorted(set(exact))))
            available[(value.name, value.version)] = value

    def resolve_targets(values, options=(), exact=True):
        # APT simulation against the empty status database includes every package,
        # including dependencies already installed in the tooling image.
        plan = run(['apt-get', '--simulate', '--no-remove', *options, 'install', *values],
                   stdout=subprocess.PIPE, text=True).stdout
        return parse_simulation(plan, values if exact else [], available)

    def alternatives(package):
        version = version_for(cache, package)
        for group in version.get_dependencies('Depends', 'PreDepends'):
            yield native_provider_choices(group.target_versions, available, apt_pkg.version_compare)

    initial = resolve_targets(targets)
    expanded, alternative_reasons, providers = expand_alternatives(initial, alternatives,
                                         lambda peer: resolve_targets([f'{peer.name}={peer.version}']))
    resolved, reasons = expand_companions(expanded, available.values(),
                                         lambda peer: resolve_targets([f'{peer.name}={peer.version}']))
    for name, values in alternative_reasons.items():
        reasons[name] = sorted(set(reasons[name]) | set(values))
    if not request.get('skip_test', False):
        validation = create_plan(bundle, cache, providers, resolve_targets)
        (bundle / 'validation-plan.json').write_text(json.dumps(validation, indent=2, sort_keys=True) + '\n')
        print(f'Prepared {len(validation["scenarios"])} required compatibility scenarios.', flush=True)
    required = sum(p.size_bytes for p in resolved)
    compatibility_bytes = required - sum(p.size_bytes for p in initial)
    print('OFFLINE_APT_COMPATIBILITY_BYTES=' + str(compatibility_bytes), flush=True)
    print("OFFLINE_APT_DOWNLOAD_BYTES=" + str(required), flush=True)
    if shutil.disk_usage(repo).free < required * 2 + 64 * 1024**2:
        raise ValueError(f"Insufficient workspace disk space for {required:,} downloaded bytes plus indexing/packaging overhead.")
    # `download` does not require these repository alternatives to be co-installed.
    # Every downloaded byte is checked against the authenticated indexes below.
    downloads = [f'{p.name}={p.version}' for p in resolved]
    for offset in range(0, len(downloads), 100):
        run(['apt-get', 'download', *downloads[offset:offset + 100]], cwd=repo)
    planned = {p.name: p for p in resolved}
    packages = []
    seen = set()
    for path in sorted(repo.glob("*.deb")):
        metadata = subprocess.check_output(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"], text=True)
        fields = dict(line.split(": ", 1) for line in metadata.splitlines())
        name, version, arch = fields["Package"], fields["Version"], fields["Architecture"]
        if arch not in {"amd64", "all"} or name in seen:
            raise ValueError(f"Mixed architecture or duplicate package/version collision: {path.name}")
        seen.add(name)
        if name not in planned or (version, arch) != (planned[name].version, planned[name].architecture):
            raise ValueError(f'Downloaded package differs from the resolved inventory: {path.name}')
        available = [v for v in cache[name].versions if v.version == version and v.architecture == arch]
        if not available:
            raise ValueError(f"Downloaded package absent from authenticated indexes: {path.name}")
        expected = available[0]
        checksum = digest(path)
        if checksum != expected.sha256 or path.stat().st_size != expected.size:
            raise ValueError(f"Downloaded package hash/size differs from authenticated Ubuntu index: {path.name}")
        packages.append({"name": name, "version": version, "architecture": arch, "filename": path.name,
                         "size_bytes": path.stat().st_size, "sha256": checksum,
                         "source_name": expected.source_name, "source_version": expected.source_version,
                         "inclusion_reasons": reasons[name],
                         "provides": expected.record.get("Provides", ""),
                         "depends": expected.record.get("Depends", ""),
                         "pre_depends": expected.record.get("Pre-Depends", "")})
    if seen != set(planned):
        raise ValueError('Downloaded inventory does not contain every resolved package')
    shutil.rmtree(repo / "partial")
    (repo / "lock").unlink(missing_ok=True)
    with (repo / "Packages").open("wb") as output:
        run(["dpkg-scanpackages", ".", "/dev/null"], cwd=repo, stdout=output)
    index = (repo / "Packages").read_bytes()
    (repo / "Packages.gz").write_bytes(gzip.compress(index, mtime=0))
    (repo / "Packages.xz").write_bytes(lzma.compress(index))
    releases = bundle / "repository-metadata"
    releases.mkdir()
    shutil.copyfile("/etc/apt/sources.list.d/ubuntu.sources", releases / "ubuntu.sources")
    metadata = []
    for path in sorted((state / "lists").glob("*InRelease")):
        shutil.copyfile(path, releases / path.name)
        metadata.append({"filename": path.name, "sha256": digest(path)})
    if len(metadata) != 3:
        raise ValueError("Expected authenticated release metadata for noble, noble-updates, and noble-security")
    manifest = {"schema_version": 1, "requested": names, "architecture": "amd64", "ubuntu_release": "24.04",
                "with_recommends": request["with_recommends"], "selections": selections, "packages": packages,
                "compatibility": {"strategy": "companions-and-alternative-providers-v2",
                                  "package_count": len(resolved) - len(initial),
                                  "size_bytes": compatibility_bytes},
                "repositories": {"suites": ["noble", "noble-updates", "noble-security"],
                                 "components": ["main", "restricted", "universe", "multiverse"],
                                 "release_metadata": metadata}}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (bundle / "install-targets.txt").write_text("\n".join(targets) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RESOLUTION FAILED: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        # Restrict ownership repair to this build's mounted workspace.
        uid, gid = os.environ.get("BUILDER_UID"), os.environ.get("BUILDER_GID")
        if uid and gid:
            for root, dirs, files in os.walk("/work"):
                for name in dirs + files:
                    os.chown(os.path.join(root, name), int(uid), int(gid), follow_symlinks=False)
            os.chown("/work", int(uid), int(gid))
