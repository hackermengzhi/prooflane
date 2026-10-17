"""Own a fixture process from startup through guaranteed cleanup."""

import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def checkout_service(directory: Path, revision: str, startup_timeout: float = 10):
    directory.mkdir(parents=True, exist_ok=True)
    ready_path = directory / "ready.json"
    ready_path.unlink(missing_ok=True)
    secret = secrets.token_hex(32)
    environment = os.environ.copy()
    environment["PROOFLANE_DEMO_SECRET"] = secret
    source = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [source, environment.get("PYTHONPATH")])
    )
    command = [
        sys.executable,
        "-m",
        "prooflane.demo.service",
        "--directory",
        str(directory),
        "--revision",
        revision,
    ]
    with (directory / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + startup_timeout
            while not ready_path.exists():
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Checkout fixture exited; inspect {directory / 'process.log'}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("Checkout fixture startup timed out")
                time.sleep(0.02)
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            yield {**ready, "secret": secret, "database": directory / "checkout.db"}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
