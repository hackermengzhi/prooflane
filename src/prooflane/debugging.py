"""Debugger adapters; commands run through the existing managed process capability."""

import importlib.util
import shutil
import sys
from pathlib import Path

from prooflane.artifacts import digest

BACKENDS = ("lldb", "gdb", "frida", "cheatengine")


def capabilities() -> dict:
    return {
        "lldb": {"executable": shutil.which("lldb"), "mode": "batch command file"},
        "gdb": {"executable": shutil.which("gdb"), "mode": "batch command file"},
        "frida": {
            "python_available": importlib.util.find_spec("frida") is not None,
            "mode": "local PID and workspace JavaScript; bounded observation",
        },
        "cheatengine": {
            "mode": "Lua script export for Cheat Engine console",
            "execution": "external; export is not proof of execution",
        },
    }


def run_debugger(
    tools,
    directory: Path,
    backend: str,
    script: str,
    target: str | None = None,
    pid: int | None = None,
    seconds: int = 2,
    timeout: int = 15,
) -> dict:
    if backend not in BACKENDS:
        raise ValueError("Unknown debugger backend")
    if not tools.policy.allow_process:
        raise ValueError("Debugging requires --allow-process")
    source = tools.path(script)
    if not source.is_file() or source.stat().st_size > 200000:
        raise ValueError("Provide a bounded workspace debugger script")
    if backend == "cheatengine":
        if source.suffix.lower() != ".lua":
            raise ValueError("Cheat Engine expects a Lua script")
        exported = directory / "cheatengine.lua"
        exported.write_bytes(source.read_bytes())
        return {
            "backend": backend,
            "execution": "not_executed",
            "tool_status": "blocked",
            "script": str(exported),
            "sha256": digest(exported),
            "instruction": "Run this Lua file inside the installed Cheat Engine Lua console.",
        }
    if backend in {"lldb", "gdb"}:
        executable = shutil.which(backend)
        if not executable:
            raise ValueError(f"{backend} is not installed")
        if (target is None) == (pid is None):
            raise ValueError("Specify exactly one workspace executable target or PID")
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError("PID must be a positive integer")
        if backend == "lldb":
            argv = [executable, "--no-lldbinit", "--batch"]
            argv += ["--attach-pid", str(pid)] if pid else ["--file", str(tools.path(target))]
            argv += ["--source", str(source)]
        else:
            argv = [executable, "--nx", "--batch", "-iex", "set auto-load safe-path /nonexistent"]
            argv += ["--pid", str(pid)] if pid else [str(tools.path(target))]
            argv += ["--command", str(source)]
    else:
        if type(pid) is not int or pid <= 0:
            raise ValueError("Frida requires an explicit positive local PID")
        if type(seconds) is not int or not 1 <= seconds <= 20 or timeout < seconds + 2:
            raise ValueError(
                "Use 1..20 observation seconds and a timeout at least two seconds longer"
            )
        if importlib.util.find_spec("frida") is None:
            raise ValueError("Frida Python package missing; install the debug extra")
        worker = Path(__file__).with_name("frida_worker.py")
        argv = [sys.executable, str(worker), str(pid), str(source), str(seconds)]
    result = tools.run_process(directory=directory, argv=argv, timeout=timeout)
    return {**result, "backend": backend, "script_sha256": digest(source)}
