"""Run the shipped installer in an immutable-base, disconnected container."""

from pathlib import Path

from .models import BuildError
from .repository import write_json
from .runner import Runner, redact


def validate(runner: Runner, bundle: Path, base_image: str, container: str,
             selections: list[dict], report_path: Path) -> dict:
    report = {"status": "FAILED", "network_mode": "none", "base_image": base_image,
              "installer": "install.sh", "installed_versions": {}, "dependency_check": False}
    # create + inspect makes the network constraint observable before execution.
    output = ""
    try:
        runner.run(["docker", "create", "--name", container, "--pull", "never", "--network", "none",
                    "--platform", "linux/amd64", "--volume", f"{bundle}:/bundle:ro", base_image,
                    "bash", "/bundle/install.sh", "--yes", "--allow-upgrades"])
        network = runner.run(["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", container], capture=True).strip()
        if network != "none":
            raise BuildError(f"Validation refused: unexpected Docker network mode {network!r}")
        # docker start --attach can itself return zero when the container failed.
        output = runner.run(["docker", "start", "--attach", container], capture=True)
        exit_code = int(runner.run(["docker", "inspect", "--format", "{{.State.ExitCode}}", container], capture=True).strip())
        report["exit_code"] = exit_code
        (report_path.parent / "validation.log").write_text(redact(output), encoding="utf-8")
        if exit_code:
            raise BuildError(f"Offline installer failed in the network-disabled container (exit {exit_code}). See validation.log; hardware/systemd-dependent packages may require VM validation.")
        installed = {}
        for line in output.splitlines():
            if line.startswith("INSTALLED\t"):
                _, name, version = line.split("\t")
                installed[name] = version
        expected = {s["name"]: s["version"] for s in selections}
        if any(installed.get(name) != version for name, version in expected.items()):
            raise BuildError("Offline validation did not confirm every requested package/provider at its resolved version.")
        if "SUCCESS: requested packages and providers installed; APT dependency check passed." not in output:
            raise BuildError("Offline validation did not confirm APT dependency consistency.")
        report.update(status="VALIDATED", installed_versions=installed, dependency_check=True)
        return report
    except BaseException as exc:
        report["error"] = redact(str(exc)) or type(exc).__name__
        raise
    finally:
        write_json(report_path, report)
