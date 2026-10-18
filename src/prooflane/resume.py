"""Rebuild what a new session inherits from an interrupted agent task's recorded events."""

from pathlib import Path

from prooflane.store import Store

# Completed and failed tasks reached a verdict; an active one may still have a live owner.
RESUMABLE_STATUSES = {"blocked", "cancelled"}
STEP_STAGES = {"agent.step", "agent.rejected", "harness.step", "harness.rejected"}


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
    if task["status"] not in RESUMABLE_STATUSES:
        raise ValueError(f"Only blocked or cancelled tasks can be resumed, not {task['status']}")
    if goal not in {None, task["title"]}:
        raise ValueError("A resumed task keeps its original goal")
    events = store.events(task_id)
    start = events[0]["detail"]
    if start.get("engine") != engine or start.get("workspace") != str(workspace.resolve()):
        raise ValueError("Interrupted task was not recorded for this engine and workspace")
    session = {
        "task_id": task_id,
        "goal": task["title"],
        "error": events[-1]["detail"].get("error"),
        "checks": None,
        "checks_origin": None,
        "inherited": {},
        "steps": [],
    }
    for event in events:
        if event["stage"] == "session.resumed":
            session["inherited"] = event["detail"]
            session["steps"] = event["detail"]["prior_steps"]
        elif event["stage"] == "checks.accepted":
            session["checks"] = event["detail"]["checks"]
            session["checks_origin"] = event["detail"]["origin"]
        elif event["stage"] in STEP_STAGES:
            session["steps"].append(event["detail"])
    if session["checks"] is not None and checks is not None:
        raise ValueError("Acceptance checks are immutable; the interrupted task already fixed them")
    return session
