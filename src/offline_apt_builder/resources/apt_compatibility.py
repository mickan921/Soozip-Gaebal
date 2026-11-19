"""Expand an APT repository without turning companion packages into targets.

This module also runs standalone in the builder container. No host APT state is
used: callers supply authenticated metadata and an empty-status APT resolver.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PackageVersion:
    name: str
    version: str
    architecture: str
    source_name: str
    source_version: str
    size_bytes: int
    exact_dependencies: tuple[tuple[str, str], ...] = ()


def parse_simulation(output, targets, available):
    """Extract selections; APT may append transient broken-package annotations."""
    resolved = []
    for line in output.splitlines():
        if line.startswith('Remv '):
            raise ValueError('Unexpected removal in empty-status APT resolution')
        if line.startswith('Inst '):
            match = re.match(r'^Inst ([a-z0-9][a-z0-9+.:_-]*)(?: \[[^\]]*\])? '
                             r'\(([a-zA-Z0-9.+:~_-]+)(?: |\))', line)
            if not match:
                raise ValueError(f'Cannot parse APT package selection: {line}')
            identity = (match[1].removesuffix(':amd64'), match[2])
            if identity not in available:
                raise ValueError(f'APT selected a package absent from authenticated metadata: {identity}')
            resolved.append(available[identity])
    if not resolved or not set(targets).issubset({f'{p.name}={p.version}' for p in resolved}):
        raise ValueError('APT did not resolve every exact installation target')
    return resolved


def expand_companions(initial, available, resolve):
    """Follow reverse same-source exact dependencies and forward APT closures.

Each missing companion is solved on its own. Repository contents may therefore
include mutually exclusive options; they are never one forced transaction.
"""
    reverse = defaultdict(list)
    for package in available:
        for name, version in package.exact_dependencies:
            reverse[(package.source_name, package.source_version, name, version)].append(package)
    for peers in reverse.values():
        peers.sort(key=lambda p: (p.name, p.version, p.architecture))
    selected = {}
    reasons = defaultdict(set)
    pending = deque()

    def include(package, reason):
        previous = selected.get(package.name)
        if previous is not None and previous != package:
            raise ValueError(f"Compatibility resolution requires conflicting versions of {package.name}: "
                             f"{previous.version}/{previous.architecture} and {package.version}/{package.architecture}.")
        reasons[package.name].add(reason)
        if previous is None:
            selected[package.name] = package
            pending.append(package)

    for package in sorted(initial, key=lambda p: p.name):
        include(package, 'requested package or dependency')
    while pending:
        package = pending.popleft()
        key = (package.source_name, package.source_version, package.name, package.version)
        for peer in reverse.get(key, ()):
            reason = f'exact-version companion of {package.name}={package.version}'
            if peer.name in selected:
                include(peer, reason)
                continue
            closure = tuple(resolve(peer))
            if peer not in closure:
                raise ValueError(f"APT omitted compatibility package {peer.name}={peer.version} from its plan.")
            for dependency in sorted(closure, key=lambda p: p.name):
                include(dependency, reason if dependency == peer else
                        f'dependency of compatibility package {peer.name}={peer.version}')
    return (tuple(selected[name] for name in sorted(selected)),
            {name: sorted(reasons[name]) for name in sorted(selected)})


def expand_alternatives(initial, alternatives, resolve):
    """Follow alternative branches of the requested graph, independently.

    The callback uses libapt's versioned/virtual dependency matching. Companion
    packages are deliberately expanded later: an optional companion's unrelated
    alternatives must not turn every small request into the entire archive.
    """
    selected = {}
    reasons = defaultdict(set)
    pending = deque()
    providers = {}

    def include(package, reason):
        previous = selected.get(package.name)
        if previous is not None and previous != package:
            raise ValueError(f'Alternative resolution requires conflicting versions of {package.name}: '
                             f'{previous.version} and {package.version}.')
        reasons[package.name].add(reason)
        if previous is None:
            selected[package.name] = package
            pending.append(package)

    for package in initial:
        include(package, 'requested package or dependency')
    while pending:
        parent = pending.popleft()
        for group in alternatives(parent):
            choices = {p.name: p for p in group}
            if len(choices) < 2:
                continue
            for peer in sorted(choices.values(), key=lambda p: p.name):
                providers[peer.name] = peer
                reason = f'alternative provider for a dependency of {parent.name}={parent.version}'
                if peer.name in selected:
                    include(peer, reason)
                    continue
                closure = tuple(resolve(peer))
                if peer not in closure:
                    raise ValueError(f'APT omitted alternative provider {peer.name}={peer.version}')
                for dependency in closure:
                    include(dependency, reason)
    return (tuple(selected[name] for name in sorted(selected)),
            {name: sorted(values) for name, values in reasons.items()},
            tuple(providers[name] for name in sorted(providers)))


def native_provider_choices(matching_versions, available, compare):
    """Choose one native version per provider from libapt's matching targets.

    libapt performs versioned virtual-Provides matching before this function.
    Prefer its candidate when allowed by the dependency, otherwise the highest
    matching version. Never replace an allowed candidate due to iteration order.
    """
    choices = {}
    for target in matching_versions:
        name = target.package.name
        if ':' in name or (name, target.version) not in available:
            continue
        previous = choices.get(name)
        candidate = target.package.candidate
        if (previous is None or target == candidate or
                (previous != candidate and compare(target.version, previous.version) > 0)):
            choices[name] = target
    return tuple(available[(name, value.version)] for name, value in sorted(choices.items()))
