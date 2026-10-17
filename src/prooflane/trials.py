"""Text mutations and disposable command trials shared by grading and agent self-checks."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TRIAL_TIMEOUT = 120
OUTPUT_TAIL = 3000
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def mutate(source: str, replacements: list[list[str]]) -> str:
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError(f"Mutation anchor must occur exactly once: {old!r}")
        source = source.replace(old, new)
    return source


def run_trial(
    workspace: Path, overlay: dict[str, str], argv: list[str], timeout: int = TRIAL_TIMEOUT
) -> dict:
    """Run a command on a disposable copy of the agent's final workspace."""
    with tempfile.TemporaryDirectory() as temporary:
        trial = Path(temporary) / "workspace"
        shutil.copytree(workspace, trial, ignore=COPY_IGNORE)
        for relative, content in overlay.items():
            (trial / relative).write_text(content, encoding="utf-8")
        command = [sys.executable if item == "$PYTHON" else item for item in argv]
        try:
            completed = subprocess.run(
                command,
                cwd=trial,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "returncode": None, "output": "trial timeout"}
    output = completed.stdout + completed.stderr
    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output[-OUTPUT_TAIL:],
    }
