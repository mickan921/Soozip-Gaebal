"""UI-independent build orchestration. Host APT is never used."""

from datetime import datetime, timezone
from importlib.resources import files
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile
import uuid

from . import __version__
from .archive import sevenzip
from .inputs import validate_packages
from .models import BuildError, BuildRequest, BuildResult, ProgressCallback, ProgressEvent
from .packaging import package_bundle
from .repository import sha256, verify_repository, write_checksums, write_json
from .runner import Runner, redact
from .validator import validate

MIN_FREE_BYTES = 256 * 1024**2


def check_host(arch: str) -> None:
    if arch != "amd64":
        raise BuildError("This release supports --arch amd64 only.")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise BuildError("Builds require an Ubuntu 24.04 amd64 workstation. macOS and cross-architecture builds are unsupported.")
    try:
        release = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values = shlex.split(value)
                release[key] = values[0] if values else ""
    except (OSError, ValueError) as exc:
        raise BuildError(f"Cannot read Ubuntu release information: {exc}") from exc
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise BuildError("The online builder must run Ubuntu 24.04.")
    try:
        native_arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildError("Cannot determine the builder's dpkg architecture.") from exc
    if native_arch != "amd64":
        raise BuildError("The online builder's dpkg architecture must be amd64.")
    if not shutil.which("docker"):
        raise BuildError("Docker Engine is required. Follow the Docker setup instructions in README.md; the builder never runs sudo automatically.")


def build(request: BuildRequest, progress: ProgressCallback | None = None,
          emit_log=None) -> BuildResult:
    packages = validate_packages(request.packages)
    check_host(request.arch)
    sevenzip()
    output = request.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < MIN_FREE_BYTES or shutil.disk_usage("/tmp").free < MIN_FREE_BYTES:
        raise BuildError("At least 256 MiB free is required in the output filesystem and /tmp; actual builds can need several GiB.")
    created = datetime.now(timezone.utc)
    build_id = created.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
    stem = f"ubuntu24.04-amd64-offline-{build_id}"
    archive_name = stem + ".7z"
    log_path = output / (stem + ".build.log")
    report_path = output / (stem + ".report.json")
    runner = Runner(log_path, emit_log)
    work: Path | None = None
    containers = []
    tooling_image = f"offline-apt-builder-tooling:{build_id}"
    tooling_id = None
    tooling_build_started = False
    published_paths = []
    resolver_started = False
    resolver_finished = False
    report = {"schema_version": 1, "build_id": build_id, "status": "FAILED", "requested": packages,
              "created_utc": created.isoformat(), "tool_version": __version__, "log": str(log_path)}

    def event(stage: str, message: str) -> None:
        runner.write(f"[{stage}] {message}\n")
        if progress:
            progress(ProgressEvent(stage, message))

    try:
        event("preflight", "Checking Docker access and available workspace.")
        try:
            docker_os = runner.run(["docker", "info", "--format", "{{.OSType}}"], capture=True).strip()
            if docker_os != "linux":
                raise BuildError("Docker must provide a Linux container engine.")
        except BuildError as exc:
            guidance = "Start Docker Engine and check this user's Docker permissions."
            if "permission denied" in str(exc).lower() and "docker.sock" in str(exc):
                guidance = (
                    "This session cannot access the Docker socket. Run Soozip's install.sh to configure Docker access, "
                    "or run these commands in your normal user's terminal:\n"
                    'sudo groupadd --force docker\nsudo usermod -aG docker "$(id -un)"\n'
                    "Then fully log out of Ubuntu and log back in, or reboot. Opening a new terminal alone is not enough. "
                    "Verify docker info works without sudo before reopening Soozip. "
                    "The docker group grants administrator-equivalent access."
                )
            raise BuildError("Docker is unavailable. " + guidance + " No automatic sudo is attempted.\n" + str(exc)) from exc
        work = Path(tempfile.mkdtemp(prefix="offline-apt-", dir="/tmp"))
        if request.keep_workdir:
            report["workdir"] = str(work)
        bundle = work / "offline-apt"
        (bundle / "repo").mkdir(parents=True)
        context = work / "container-context"
        context.mkdir()
        resources = files("offline_apt_builder").joinpath("resources")
        for name in ("Dockerfile.builder", "resolve.py", "apt_compatibility.py", "ubuntu.sources"):
            (context / name).write_bytes(resources.joinpath(name).read_bytes())
        (bundle / "install.sh").write_bytes(resources.joinpath("install.sh").read_bytes())
        (bundle / "install.sh").chmod(0o755)
        (bundle / "packages.txt").write_text("\n".join(packages) + "\n")
        (bundle / "architecture.txt").write_text("amd64\n")
        (bundle / "with-recommends.txt").write_text(f"{int(request.with_recommends)}\n")
        write_json(work / "request.json", {"packages": packages, "with_recommends": request.with_recommends})
        event("prepare", "Pulling the Ubuntu amd64 base and preparing APT tooling.")
        runner.run(["docker", "pull", "--platform", "linux/amd64", "ubuntu:24.04"])
        image = json.loads(runner.run(["docker", "image", "inspect", "--format",
                                      '{{json .}}', "ubuntu:24.04"], capture=True))
        if image["Architecture"] != "amd64" or image["Os"] != "linux":
            raise BuildError("Docker returned a base image for an unsupported platform.")
        # The content-addressed local image ID fixes both stages to identical bytes.
        base_image = image["Id"]
        digests = image.get("RepoDigests", [])
        if not digests:
            raise BuildError("Ubuntu base has no immutable repository digest after pull.")
        base_ref = digests[0]
        report["base_image"] = {"id": base_image, "repo_digests": image.get("RepoDigests", [])}
        tooling_build_started = True
        runner.run(["docker", "build", "--pull=false", "--platform", "linux/amd64",
                    "--build-arg", f"BASE_IMAGE={base_ref}", "--file", str(context / "Dockerfile.builder"),
                    "--tag", tooling_image, str(context)])
        tooling_id = runner.run(["docker", "image", "inspect", "--format", "{{.Id}}", tooling_image], capture=True).strip()
        event("resolve", "Resolving exact package names and downloading the complete dependency set.")
        resolver_container = "offline-apt-resolve-" + build_id
        containers.append(resolver_container)

        def resolver_progress(line: str) -> None:
            if line.startswith('OFFLINE_APT_COMPATIBILITY_BYTES='):
                amount = int(line.strip().split('=', 1)[1])
                event('compatibility', f'Included {amount / 1024**2:,.1f} MiB of matching compatibility packages. '
                      'Gaebal installs only those needed by the reviewed transaction.')
            if line.startswith("OFFLINE_APT_DOWNLOAD_BYTES="):
                amount = int(line.strip().split("=", 1)[1])
                label = "Large download" if amount >= 1024**3 else "Download"
                event("download", f"{label}: {amount / 1024**2:,.1f} MiB of packages; additional disk space is needed for indexes and testing.")

        resolver_started = True
        runner.run(["docker", "run", "--rm", "--pull", "never", "--name", resolver_container,
                    "--platform", "linux/amd64", "--env", f"BUILDER_UID={os.getuid()}",
                    "--env", f"BUILDER_GID={os.getgid()}", "--volume", f"{work}:/work", tooling_id],
                   on_line=resolver_progress)
        resolver_finished = True
        containers.remove(resolver_container)  # --rm removed the completed resolver.
        manifest = json.loads((bundle / "manifest.json").read_text())
        if manifest.get("requested") != list(packages):
            raise BuildError("Resolver manifest does not match the requested packages.")
        manifest.update(schema_version=2, build_id=build_id, created_utc=created.isoformat(), tool_version=__version__,
                        base_image=report["base_image"], tooling_image=tooling_id, validation_status="PENDING")
        event("index", "Independently verifying repository indexes, manifests, and hashes.")
        verify_repository(bundle, manifest)
        write_json(bundle / "manifest.json", manifest)
        (bundle / "README-offline.txt").write_text(resources.joinpath("README-offline.txt").read_text().replace("@STATUS@", "PENDING").replace("@ARCHIVE@", archive_name))
        (bundle / "build-info.txt").write_text(f"Build: {build_id}\nTool: {__version__}\nUbuntu: 24.04 amd64\nBuilt UTC: {created.isoformat()}\nStatus: PENDING\n")
        write_json(bundle / "test-report.json", {"status": "PENDING"})
        write_checksums(bundle)
        # Freeze the installer and install payload before testing. Only reports,
        # checksums, and descriptive status metadata are finalized afterward.
        frozen = {p.relative_to(bundle).as_posix(): sha256(p) for p in bundle.rglob("*")
                  if p.is_file() and (p.parent == bundle / "repo" or p.name in {"install.sh", "install-targets.txt", "packages.txt", "architecture.txt", "with-recommends.txt"})}
        if request.skip_test:
            event("test", "UNTESTED: offline validation was explicitly skipped.")
            test_report = {"status": "UNTESTED", "reason": "--skip-test", "dependency_check": False}
        else:
            event("test", "Running the shipped installer read-only in a fresh container with network=none.")
            validator_container = "offline-apt-test-" + build_id
            containers.append(validator_container)
            test_report = validate(runner, bundle, base_image, validator_container, manifest["selections"], work / "test-report.json")
        status = test_report["status"]
        report.update(status=status, test=test_report, package_count=len(manifest["packages"]))
        if 'compatibility' in manifest:
            report['compatibility'] = manifest['compatibility']
        manifest["validation_status"] = status
        manifest["test_report"] = "test-report.json"
        write_json(bundle / "manifest.json", manifest)
        write_json(bundle / "test-report.json", test_report)
        (bundle / "README-offline.txt").write_text(resources.joinpath("README-offline.txt").read_text().replace("@STATUS@", status).replace("@ARCHIVE@", archive_name))
        information = (bundle / "build-info.txt").read_text().replace("Status: PENDING", f"Status: {status}")
        (bundle / "build-info.txt").write_text(information + f"Base image: {base_image}\nRecommends: {request.with_recommends}\n")
        logs = bundle / "logs"
        logs.mkdir()
        if (work / "validation.log").exists():
            shutil.copyfile(work / "validation.log", logs / "validation.log")
        event("package", f"Packing {len(manifest['packages'])} packages as {status}.")
        shutil.copyfile(log_path, logs / "build.log")
        for relative, expected_hash in frozen.items():
            if sha256(bundle / relative) != expected_hash:
                raise BuildError(f"Tested installation payload changed before packaging: {relative}")
        verify_repository(bundle, manifest)
        write_checksums(bundle)
        required = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()) + 64 * 1024**2
        if shutil.disk_usage(output).free < required:
            raise BuildError(f"Output filesystem needs approximately {required:,} free bytes to publish the archive safely.")
        archive, checksum, digest = package_bundle(bundle, output, archive_name)
        published_paths = [checksum, archive]
        report.update(archive=str(archive), checksum=str(checksum), sha256=digest, size_bytes=archive.stat().st_size)
        write_json(report_path, report)
        event("complete", f"{status}: {archive}")
        return BuildResult(status, archive, checksum, digest, len(manifest["packages"]), archive.stat().st_size,
                           report_path, work if request.keep_workdir else None)
    except BaseException as exc:
        for path in published_paths:
            path.unlink(missing_ok=True)
        report.update(status="FAILED", error=redact(str(exc)) or type(exc).__name__)
        if work:
            for name in ("test-report.json", "validation.log"):
                if (work / name).exists():
                    destination = output / (stem + "." + name)
                    shutil.copyfile(work / name, destination)
                    report[name] = str(destination)
            if request.keep_workdir:
                report["workdir"] = str(work)
        try:
            write_json(report_path, report)
        except OSError as report_error:
            runner.write(f"Could not save failure report: {report_error}\n")
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise BuildError(redact(str(exc)), report_path if report_path.exists() else None) from exc
    finally:
        for container in containers:
            try:
                runner.run(["docker", "rm", "--force", container], check=False)
            except BuildError:
                pass
        if work and tooling_id and resolver_started and not resolver_finished:
            try:
                runner.run(["docker", "run", "--rm", "--pull", "never", "--network", "none",
                            "--volume", f"{work}:/work", "--entrypoint", "chown", tooling_id,
                            "-R", "--no-dereference", f"{os.getuid()}:{os.getgid()}", "/work"])
            except BuildError as exc:
                runner.write(f"Ownership cleanup failed for {work}: {exc}\n")
        if work and not request.keep_workdir:
            try:
                shutil.rmtree(work)
            except PermissionError:
                # A killed resolver may not have reached its ownership cleanup.
                if tooling_id:
                    try:
                        runner.run(["docker", "run", "--rm", "--pull", "never", "--network", "none",
                                    "--volume", f"{work}:/work", "--entrypoint", "chown", tooling_id,
                                    "-R", "--no-dereference", f"{os.getuid()}:{os.getgid()}", "/work"])
                        shutil.rmtree(work)
                    except (BuildError, OSError) as exc:
                        runner.write(f"Cleanup could not remove {work}: {exc}\n")
            except OSError as exc:
                runner.write(f"Cleanup could not remove {work}: {exc}. Remove this build workspace after stopping its containers.\n")
        if tooling_build_started:
            try:
                runner.run(["docker", "image", "rm", tooling_image], check=False)
            except BuildError:
                pass
        runner.close()
