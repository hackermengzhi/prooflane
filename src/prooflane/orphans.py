"""Find and stop process groups that a crashed session left running.

A record exists only while its group may still run: the session deletes it once the group is
stopped, so records left behind mean the session ended before cleaning up.
"""

import json
import os
import signal
import subprocess
from pathlib import Path

RECORD_NAME = "process_group.json"


def start_time(pid: int) -> str | None:
    """Return the kernel's start time for a live process, or None once it has exited."""
    # A fixed locale and zone keep the text comparable between the recording and checking session.
    environment = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "TZ": "UTC"}
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None


def record_group(directory: Path, pgid: int, argv: list[str]) -> Path | None:
    """Save the identity of a new process group leader so a later session can verify it."""
    started = start_time(pgid)
    if started is None:
        # The leader already exited, so a later session could not tell its group from a reused id.
        return None
    record = directory / RECORD_NAME
    record.write_text(json.dumps({"pgid": pgid, "argv": argv, "started": started}))
    return record


def owns_group(record: dict) -> bool:
    """Whether the recorded leader still leads its group and started when it was recorded."""
    pgid = record.get("pgid")
    # Process group 0 and 1 signal the caller's own group and init, never a recorded command.
    if type(pgid) is not int or pgid <= 1 or pgid == os.getpgrp():
        return False
    try:
        if os.getpgid(pgid) != pgid:
            return False
    except ProcessLookupError:
        return False
    return start_time(pgid) == record.get("started")


def reap_groups(evidence: Path) -> list[dict]:
    """Stop the recorded groups under a run directory whose leaders are verifiably the same.

    A leader that exited, or an id now held by a process started at another time, is left alone:
    its group cannot be told apart from an unrelated one.
    """
    groups = []
    for path in sorted(evidence.rglob(RECORD_NAME)):
        record = json.loads(path.read_text())
        outcome = "not_verified"
        if owns_group(record):
            try:
                os.killpg(record["pgid"], signal.SIGKILL)
                outcome = "stopped"
            except ProcessLookupError:
                pass
        groups.append({"pgid": record.get("pgid"), "argv": record.get("argv"), "outcome": outcome})
    return groups
