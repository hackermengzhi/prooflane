"""Read-only snapshot of the run's environment, for reproducibility rather than evasion.

The snapshot records facts that let a result be reproduced and cross-checked: the
platform, interpreter, the workspace's code version, requested package versions,
environment variable names and the wall/monotonic clock. It never probes for
sandboxing or monitoring, changes behaviour by environment, or reads variable
values, which could carry secrets.
"""

import importlib.metadata as metadata
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GIT_TIMEOUT_SECONDS = 5


def package_versions(names: list[str]) -> dict:
    versions = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def environment_keys(prefixes: list[str] | None) -> list[str]:
    """Return variable NAMES only; values may hold secrets and are never read."""
    keys = list(os.environ.keys())
    if prefixes:
        keys = [key for key in keys if any(key.startswith(prefix) for prefix in prefixes)]
    return sorted(keys)


def git_revision(workspace: Path) -> dict | None:
    """The committed version of the code under test, or None when it is not a repo."""

    def run(arguments: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(workspace), *arguments],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = run(["rev-parse", "--short", "HEAD"])
    if commit is None:
        return None
    status = run(["status", "--porcelain"])
    return {"commit": commit, "dirty": bool(status)}


def snapshot(workspace: Path, packages: list[str], env_prefixes: list[str] | None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "workspace": {
            "path": str(workspace),
            "git": git_revision(workspace),
        },
        "packages": package_versions(packages),
        "env_keys": environment_keys(env_prefixes),
        "time": {
            "utc": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "timezone": time.strftime("%Z"),
            "monotonic": time.monotonic(),
        },
    }
