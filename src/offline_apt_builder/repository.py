"""Independent verification of the downloaded repository and bundle checksums."""

import gzip
import hashlib
import json
import lzma
import re
from pathlib import Path

from .models import BuildError


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_index(content: str) -> list[dict[str, str]]:
    entries = []
    current: dict[str, str] = {}
    field = ""
    for line in (content + "\n\n").splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            field = ""
        elif line[0].isspace() and field:
            current[field] += "\n" + line
        elif ": " in line:
            field, value = line.split(": ", 1)
            if field in current:
                raise BuildError(f"Duplicate repository index field: {field}")
            current[field] = value
        else:
            raise BuildError(f"Malformed repository index line: {line!r}")
    return entries


def verify_repository(bundle: Path, manifest: dict) -> None:
    repo = bundle / "repo"
    packages = manifest.get("packages", [])
    if not packages:
        raise BuildError("Resolver produced an empty repository.")
    records = {}
    identities = set()
    for package in packages:
        filename = package["filename"]
        if Path(filename).name != filename or not filename.endswith(".deb"):
            raise BuildError(f"Unsafe package filename: {filename}")
        identity = package["name"]
        if identity in identities or filename in records:
            raise BuildError(f"Duplicate package/version collision: {filename}")
        identities.add(identity)
        if package["architecture"] not in {"amd64", "all"}:
            raise BuildError(f"Unsupported package architecture: {filename}")
        path = repo / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size != package["size_bytes"] or sha256(path) != package["sha256"]:
            raise BuildError(f"Package missing or checksum/size mismatch: {filename}")
        records[filename] = package
    if {p.name for p in repo.glob("*.deb")} != set(records):
        raise BuildError("Repository contains unrecorded packages.")
    index = (repo / "Packages").read_bytes()
    if gzip.decompress((repo / "Packages.gz").read_bytes()) != index or lzma.decompress((repo / "Packages.xz").read_bytes()) != index:
        raise BuildError("Compressed repository indexes disagree.")
    indexed = set()
    for entry in parse_index(index.decode("utf-8")):
        filename = entry.get("Filename", "")
        if filename.startswith("./"):
            filename = filename[2:]
        if filename not in records or filename in indexed:
            raise BuildError(f"Missing, unsafe, or duplicate index filename: {filename}")
        indexed.add(filename)
        package = records[filename]
        expected = {"Package": package["name"], "Version": package["version"], "Architecture": package["architecture"],
                    "SHA256": package["sha256"], "Size": str(package["size_bytes"])}
        if any(entry.get(k) != v for k, v in expected.items()):
            raise BuildError(f"Repository index/manifest mismatch: {filename}")
        if 'source_name' in package or 'source_version' in package:
            source = re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)(?: \(([^()\s]+)\))?',
                                  entry.get('Source', entry['Package']))
            if (not source or package.get('source_name') != source[1]
                    or package.get('source_version') != (source[2] or entry['Version'])):
                raise BuildError(f'Repository source identity/manifest mismatch: {filename}')
        if 'inclusion_reasons' in package:
            reasons = package['inclusion_reasons']
            if not isinstance(reasons, list) or not reasons or not all(isinstance(r, str) and r for r in reasons):
                raise BuildError(f'Invalid package inclusion reasons: {filename}')
    if indexed != set(records):
        raise BuildError("Repository index omits downloaded packages.")
    targets = []
    if [s["requested"] for s in manifest["selections"]] != manifest.get("requested"):
        raise BuildError("Requested package/provider mappings are incomplete or out of order.")
    for selection in manifest["selections"]:
        matches = [p for p in packages if p["name"] == selection["name"] and p["version"] == selection["version"]]
        if len(matches) != 1:
            raise BuildError(f"Requested package/provider absent: {selection['requested']}")
        if selection.get("virtual"):
            provided = {value.strip().split(" ", 1)[0] for value in matches[0].get("provides", "").split(",")}
            if selection["requested"] not in provided:
                raise BuildError(f"Selected provider does not provide {selection['requested']}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", selection["name"], re.ASCII) or not re.fullmatch(r"[a-zA-Z0-9.+:~_-]+", selection["version"], re.ASCII):
            raise BuildError("Unsafe resolved installation target.")
        targets.append(f"{selection['name']}={selection['version']}")
    if (bundle / "install-targets.txt").read_text().splitlines() != list(dict.fromkeys(targets)):
        raise BuildError("Installer targets disagree with the resolution manifest.")
    for metadata in manifest.get("repositories", {}).get("release_metadata", []):
        name = metadata["filename"]
        if Path(name).name != name or sha256(bundle / "repository-metadata" / name) != metadata["sha256"]:
            raise BuildError("Repository release metadata differs from the manifest.")


def write_checksums(bundle: Path) -> None:
    lines = []
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"Unexpected symlink in bundle: {path}")
        if path.is_file() and path.name != "SHA256SUMS":
            relative = path.relative_to(bundle).as_posix()
            if "\n" in relative or "\\" in relative:
                raise BuildError("Unsupported checksum filename.")
            lines.append(f"{sha256(path)}  ./{relative}\n")
    (bundle / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
