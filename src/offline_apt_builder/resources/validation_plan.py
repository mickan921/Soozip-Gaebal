"""Reference definitions generated exclusively from authenticated Ubuntu indexes."""
import hashlib

POLICY = 'ubuntu-desktop-providers-v1'
OLD_CORE = ('systemd', 'systemd-dev', 'systemd-sysv', 'libsystemd0', 'libsystemd-shared',
            'udev', 'libudev1', 'libpam-systemd', 'libnss-systemd', 'systemd-resolved',
            'systemd-timesyncd', 'systemd-oomd', 'mesa-va-drivers', 'mesa-vdpau-drivers',
            'mesa-vulkan-drivers', 'libgl1-mesa-dri', 'libglx-mesa0', 'libegl-mesa0',
            'libgbm1', 'libglapi-mesa')
DESKTOP = ('ubuntu-desktop-minimal', 'alsa-base', 'xserver-xorg',
           'mesa-va-drivers', 'mesa-vdpau-drivers', 'mesa-vulkan-drivers')

STATUS_FIELDS = frozenset(('Package', 'Source', 'Version', 'Architecture', 'Essential',
    'Protected', 'Multi-Arch', 'Section', 'Priority', 'Installed-Size', 'Maintainer',
    'Depends', 'Pre-Depends', 'Recommends', 'Suggests', 'Breaks', 'Conflicts',
    'Replaces', 'Provides', 'Description', 'Homepage', 'Built-Using'))


def status_record(record):
    # Packages indexes contain acquisition fields forbidden in dpkg's status DB.
    return 'Status: install ok installed\n' + ''.join(
        f'{key}: {record[key]}\n' for key in record if key in STATUS_FIELDS)


def version_for(cache, package):
    return next(v for v in cache[package.name].versions
                if v.version == package.version and v.architecture == package.architecture)


def create_plan(bundle, cache, providers, resolve):
    references = bundle / 'validation-references'
    references.mkdir()
    scenarios = [{'id': 'fresh', 'kind': 'installation', 'seed': []}]
    old = []
    for name in OLD_CORE:
        version = next((v for v in cache[name].versions if any(
            o.archive == 'noble' and o.trusted for o in v.origins)), None)
        if version is None:
            raise ValueError(f'Required original Ubuntu reference unavailable: {name}')
        old.append(f'{name}={version.version}')
    scenarios.append({'id': 'older-core', 'kind': 'installation',
                      'seed': old + ['alsa-base', 'xserver-xorg-core']})
    # -t noble also matches Codename=noble in updates/security. Pin the archive
    # pocket explicitly so the original desktop model cannot silently be current.
    preferences = bundle.parent / 'apt/desktop-release.pref'
    preferences.write_text('Package: *\nPin: release a=noble\nPin-Priority: 1001\n\n'
                           'Package: *\nPin: release a=noble-updates\nPin-Priority: -1\n\n'
                           'Package: *\nPin: release a=noble-security\nPin-Priority: -1\n')
    for label, options in (('desktop-release', ['-o', 'Dir::Etc::preferences=' + str(preferences)]),
                           ('desktop-current', [])):
        selected = resolve(list(DESKTOP), options + ['--install-recommends'], exact=False)
        stanzas = []
        for package in selected:
            version = version_for(cache, package)
            if label == 'desktop-release' and not any(o.archive == 'noble' and o.trusted for o in version.origins):
                raise ValueError(f'Original desktop reference selected a non-release package: {package.name}={package.version}')
            stanzas.append(status_record(version.record))
        status = '\n'.join(stanzas).encode()
        path = references / (label + '.status')
        path.write_bytes(status)
        scenarios.append({'id': label, 'kind': 'dependency-simulation',
                          'reference_archive': 'noble' if label == 'desktop-release' else 'current-pockets',
                          'status_file': path.relative_to(bundle).as_posix(),
                          'reference_sha256': hashlib.sha256(status).hexdigest(),
                          'package_count': len(selected)})
    seeds = set()
    for provider in providers:
        # Runtime-only states expose the JACK2 bug that preinstalled headers hide.
        runtime = []
        for name, required in provider.exact_dependencies:
            if name not in cache:
                continue
            version = next((v for v in cache[name].versions if v.version == required), None)
            if version and (version.source_name, version.source_version) == (
                    provider.source_name, provider.source_version):
                runtime.append(f'{name}={required}')
        for seed in (tuple(sorted(set(runtime))), (f'{provider.name}={provider.version}',)):
            if not seed or seed in seeds:
                continue
            seeds.add(seed)
            identity = hashlib.sha256('\n'.join(seed).encode()).hexdigest()[:16]
            scenarios.append({'id': 'provider-' + identity, 'kind': 'installation',
                              'seed': list(seed), 'provider': provider.name})
    return {'policy': POLICY, 'scenarios': scenarios}
