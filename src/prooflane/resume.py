"""Rebuild what a new session inherits from an interrupted agent task's recorded events."""

import os
import socket
from pathlib import Path

from prooflane.orphans import reap_groups
from prooflane.store import Store

# Completed and failed tasks reached a verdict; active ones resume only once their owner exited.
RESUMABLE_STATUSES = {"blocked", "cancelled"}
STEP_STAGES = {"agent.step", "agent.rejected", "harness.step", "harness.rejected"}


def owner() -> dict:
    """Identify the process that runs a session, so a later one can tell if it crashed."""
    return {"pid": os.getpid(), "host": socket.gethostname()}


def owner_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def abandon(store: Store, task_id: str, start: dict) -> None:
    """Close an active task whose recorded owner process no longer exists on this host.

    A reused process id keeps the task active, which refuses a resume rather than risking two
    sessions on one workspace. Process groups the owner left running are stopped first.
    """
    recorded = start.get("owner")
    if not recorded or recorded["host"] != socket.gethostname() or owner_alive(recorded["pid"]):
        raise ValueError("Task is still active or its owner cannot be confirmed to have exited")
    # Tasks recorded before run directories were noted have no process group records to check.
    groups = reap_groups(Path(start["evidence"])) if "evidence" in start else []
    store.transition(
        task_id,
        "blocked",
        {"error": "Owner process exited without closing the task", "process_groups": groups},
    )


def load_session(
    store: Store,
    task_id: str,
    engine: str,
    workspace: Path,
    goal: str | None,
    checks: list | None,
) -> dict:
    """Return the goal, acceptance checks and step history a resumed session continues from.

    Steps are leads for the model, not evidence: the new session must verify the current state.
    """
    task = store.task(task_id)
    if task["status"] not in RESUMABLE_STATUSES | {"active"}:
        raise ValueError(f"Only blocked, cancelled or abandoned tasks resume, not {task['status']}")
    if goal not in {None, task["title"]}:
        raise ValueError("A resumed task keeps its original goal")
    events = store.events(task_id)
    start = events[0]["detail"] if events else {}
    if start.get("engine") != engine or start.get("workspace") != str(workspace.resolve()):
        raise ValueError("Interrupted task was not recorded for this engine and workspace")
    if task["status"] == "active":
        abandon(store, task_id, start)
        events = store.events(task_id)
    session = {
        "task_id": task_id,
        "goal": task["title"],
        "error": events[-1]["detail"].get("error"),
        "process_groups": events[-1]["detail"].get("process_groups", []),
        "checks": None,
        "checks_origin": None,
        "inherited": {},
        "snapshot": None,
        "steps": [],
    }
    for event in events:
        if event["stage"] == "session.resumed":
            session["inherited"] = event["detail"]
            session["steps"] = event["detail"]["prior_steps"]
        elif event["stage"] == "checks.accepted":
            session["checks"] = event["detail"]["checks"]
            session["checks_origin"] = event["detail"]["origin"]
        elif event["stage"] == "workspace.snapshot":
            session["snapshot"] = event["detail"]["files"]
        elif event["stage"] in STEP_STAGES:
            session["steps"].append(event["detail"])
    if session["checks"] is not None and checks is not None:
        raise ValueError("Acceptance checks are immutable; the interrupted task already fixed them")
    return session


MAX_SESSIONS = 20


def run_sessions(runner, sessions: int, store: Store, goal, policy, output, **options) -> dict:
    """Run a task and keep resuming it while a session only ran out of steps.

    Every session starts with a fresh step budget and inherits the acceptance checks, recent
    steps and workspace notes, so a long task advances without repeating its research.
    """
    if type(sessions) is not int or not 1 <= sessions <= MAX_SESSIONS:
        raise ValueError(f"Sessions must be 1..{MAX_SESSIONS}")
    resume = options.pop("resume", None)
    results = [runner(store, goal, policy, output, resume=resume, **options)]
    # Later sessions inherit the checks the first one fixed.
    options.pop("checks", None)
    while len(results) < sessions and results[-1]["budget_exhausted"]:
        results.append(
            runner(store, None, policy, output, resume=results[-1]["task_id"], **options)
        )
    return {
        **results[-1],
        "sessions": [result["task_id"] for result in results],
        "total_steps": sum(len(result["steps"]) for result in results),
        "usage": {
            key: sum(result["usage"][key] for result in results) for key in results[-1]["usage"]
        },
    }
