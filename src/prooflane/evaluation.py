"""Score testing and automation agents on seeded cases with graders hidden from the agent."""

import json
import math
import os
import shlex
import shutil
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prooflane.agent import run_agent
from prooflane.artifacts import write_json
from prooflane.harness import run_harness
from prooflane.models import usage_snapshot
from prooflane.resume import run_sessions
from prooflane.store import Store
from prooflane.tools import ToolPolicy
from prooflane.trials import COPY_IGNORE, OUTPUT_TAIL, mutate, run_trial

RUNNERS = {"harness": run_harness, "agent": run_agent}
DEFAULT_AGENT_TIMEOUT = 1800


def compare_runs(runs: list[dict]) -> dict:
    """Compare recorded scores, preserving missing cases and all tied winners."""
    labels = []
    for run in runs:
        base = run.get("agent_command") or run["mode"]
        label = base
        suffix = 2
        while label in labels:
            label = f"{base} ({suffix})"
            suffix += 1
        labels.append(label)

    indexed_cases = [{case["case"]: case for case in run["cases"]} for run in runs]
    names = sorted({name for cases in indexed_cases for name in cases})
    cases = []
    per_case_best = {}
    for name in names:
        results = {}
        for label, index in zip(labels, indexed_cases):
            case = index.get(name)
            results[label] = {
                "score": case["score"] if case is not None else None,
                "false_completion": case["false_completion"] if case is not None else None,
            }
        scores = {label: result["score"] for label, result in results.items()}
        best = max(score for score in scores.values() if score is not None)
        per_case_best[name] = [label for label, score in scores.items() if score == best]
        cases.append({"case": name, "runs": results})

    return {
        "labels": labels,
        "cases": cases,
        "summary": {
            label: {
                "mean_score": run["mean_score"],
                "false_completions": run["false_completions"],
                "case_count": len(run["cases"]),
            }
            for label, run in zip(labels, runs)
        },
        "per_case_best": per_case_best,
    }


def prepare_workspace(case_dir: Path, grading: dict, destination: Path) -> Path:
    shutil.copytree(case_dir / "workspace", destination, ignore=COPY_IGNORE)
    if grading["kind"] in {"mutation", "reproduce"}:
        # The defective target is derived from the reference so seeded bugs cannot drift.
        reference = (case_dir / "grader" / grading["reference"]).read_text(encoding="utf-8")
        if grading["kind"] == "reproduce":
            seeded = [grading["defect"]]
        else:
            seeded = [
                item["replace"] for item in grading["mutants"] if item["id"] in grading["seeded"]
            ]
        target = destination / grading["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(mutate(reference, seeded), encoding="utf-8")
    return destination


def score_findings(path: Path, expected: set[str]) -> dict:
    try:
        reported = {item["requirement"] for item in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError, KeyError):
        reported = set()
    confirmed = reported & expected
    recall = len(confirmed) / len(expected)
    precision = len(confirmed) / len(reported) if reported else 0.0
    f1 = 2 * precision * recall / (precision + recall) if confirmed else 0.0
    return {
        "expected": sorted(expected),
        "reported": sorted(reported),
        "recall": recall,
        "precision": precision,
        "f1": f1,
    }


def grade_mutation(case_dir: Path, grading: dict, workspace: Path) -> dict:
    """A useful suite passes on the reference and fails on each defective variant."""
    reference = (case_dir / "grader" / grading["reference"]).read_text(encoding="utf-8")
    target, suite = grading["target"], grading["suite"]
    gate = run_trial(workspace, {target: reference}, suite)
    variants = [{target: mutate(reference, [item["replace"]])} for item in grading["mutants"]]
    # Trials use separate copies and ephemeral ports, so they can run concurrently.
    with ThreadPoolExecutor() as pool:
        trials = list(pool.map(lambda overlay: run_trial(workspace, overlay, suite), variants))
    mutants = [
        {"id": item["id"], "requirement": item["requirement"], "killed": not trial["passed"]}
        for item, trial in zip(grading["mutants"], trials)
    ]
    kill_rate = sum(item["killed"] for item in mutants) / len(mutants)
    # A suite that rejects correct behaviour is a false alarm, so its kills earn nothing.
    suite_score = kill_rate if gate["passed"] else 0.0
    expected = {
        item["requirement"] for item in grading["mutants"] if item["id"] in grading["seeded"]
    }
    findings = score_findings(workspace / grading["findings"], expected)
    return {
        "reference_passed": gate["passed"],
        "reference_output": None if gate["passed"] else gate["output"],
        "mutants": mutants,
        "kill_rate": kill_rate,
        "findings": findings,
        "score": (suite_score + findings["f1"]) / 2,
    }


def grade_reproduce(case_dir: Path, grading: dict, workspace: Path) -> dict:
    """A focused reproduction fails before the fix and passes after unrelated changes."""
    reference = (case_dir / "grader" / grading["reference"]).read_text(encoding="utf-8")
    target, suite = grading["target"], grading["suite"]
    before = run_trial(workspace, {target: mutate(reference, [grading["defect"]])}, suite)
    after = run_trial(workspace, {target: reference}, suite)
    variants = [{target: mutate(reference, [item["replace"]])} for item in grading["decoys"]]
    with ThreadPoolExecutor() as pool:
        trials = list(pool.map(lambda overlay: run_trial(workspace, overlay, suite), variants))
    decoys = [
        {
            "id": item["id"],
            "passed": trial["passed"],
            "output": None if trial["passed"] else trial["output"],
        }
        for item, trial in zip(grading["decoys"], trials)
    ]
    failure_rate = sum(not item["passed"] for item in decoys) / len(decoys) if decoys else 0.0
    fails_before = not before["passed"]
    passes_after = after["passed"]
    return {
        "fails_before": fails_before,
        "passes_after": passes_after,
        "defect_output": before["output"] if fails_before else None,
        "reference_output": None if passes_after else after["output"],
        "decoys": decoys,
        "decoy_failure_rate": failure_rate,
        "score": 1 - 0.5 * failure_rate if fails_before and passes_after else 0.0,
    }


def grade_hidden(case_dir: Path, grading: dict, workspace: Path) -> dict:
    """Run each hidden acceptance test separately against the agent's deliverable."""
    tests_file = grading["tests_file"]
    overlay = {tests_file: (case_dir / "grader" / tests_file).read_text(encoding="utf-8")}
    tests = []
    for test_id in grading["tests"]:
        trial = run_trial(workspace, overlay, ["$PYTHON", "-m", "unittest", test_id])
        tests.append(
            {
                "id": test_id,
                "passed": trial["passed"],
                "output": None if trial["passed"] else trial["output"],
            }
        )
    return {"tests": tests, "score": sum(item["passed"] for item in tests) / len(tests)}


GRADERS = {"mutation": grade_mutation, "reproduce": grade_reproduce, "hidden": grade_hidden}


def run_external(
    goal: str, policy: ToolPolicy, directory: Path, argv: list[str], timeout: float
) -> dict:
    # The external agent runs outside Prooflane's tool policy, so allow_process does not apply.
    if os.name != "posix":
        raise ValueError("Managed process execution currently supports macOS/Linux")
    workspace = policy.workspace.resolve()
    environment = {
        **os.environ,
        "PROOFLANE_GOAL": goal,
        "PROOFLANE_WORKSPACE": str(workspace),
    }
    logs = {name: directory / f"{name}.txt" for name in ("stdout", "stderr")}
    started = time.monotonic()
    timed_out = False
    returncode = None
    error = None
    with logs["stdout"].open("wb") as stdout, logs["stderr"].open("wb") as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as exc:
            error = str(exc)
        else:
            try:
                process.communicate(goal.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = f"External agent timed out after {timeout} seconds"
            finally:
                # Include descendants even when the agent exits before its children.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
                process.stdin.close()
            returncode = process.returncode
    external = {
        "argv": argv,
        "returncode": returncode,
        "seconds": round(time.monotonic() - started, 3),
        "timed_out": timed_out,
    }
    for name, path in logs.items():
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - OUTPUT_TAIL))
            external[name] = stream.read(OUTPUT_TAIL).decode("utf-8", errors="replace")
        external[f"{name}_file"] = str(path)
    return {
        "status": "timeout" if timed_out else "completed" if returncode == 0 else "failed",
        "error": error,
        "steps": [],
        "report": None,
        "external": external,
    }


def run_case(
    case_dir: Path,
    store: Store,
    output: Path,
    *,
    mode: str,
    reference: bool,
    agent_argv: list[str] | None = None,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    sessions: int = 1,
) -> dict:
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    case_output = output / case_dir.name
    workspace = prepare_workspace(case_dir, case["grading"], case_output / "workspace")
    started = time.monotonic()
    if reference:
        # Validates the case itself: the bundled answer must earn a full score.
        shutil.copytree(case_dir / "grader" / "solution", workspace, dirs_exist_ok=True)
        run = {"status": "reference-solution", "error": None, "steps": [], "report": None}
    else:
        policy = ToolPolicy(workspace, allow_process=case["allow_process"])
        if mode == "external":
            run = run_external(case["goal"], policy, case_output, agent_argv, agent_timeout)
        else:
            # Splitting one case budget across sessions measures what each resumption loses.
            run = run_sessions(
                RUNNERS[mode],
                sessions,
                store,
                case["goal"],
                policy,
                case_output / "runs",
                max_steps=math.ceil(case["max_steps"] / sessions),
            )
    grade = GRADERS[case["grading"]["kind"]](case_dir, case["grading"], workspace)
    result = {
        "case": case_dir.name,
        "battleground": case["battleground"],
        "status": run["status"],
        "error": run["error"],
        "steps": run.get("total_steps", len(run["steps"])),
        "sessions": len(run.get("sessions", [])),
        "usage": run.get("usage"),
        "seconds": round(time.monotonic() - started, 1),
        "report": run["report"],
        "score": grade["score"],
        # Claiming completion while hidden grading disagrees is the costliest agent failure.
        "false_completion": run["status"] == "completed" and grade["score"] < 1,
        "grade": grade,
    }
    if "external" in run:
        result["external"] = run["external"]
    return result


def run_evaluation(
    cases_root: Path,
    store: Store,
    output: Path,
    *,
    names: list[str] | None = None,
    mode: str = "harness",
    reference: bool = False,
    agent_command: str | None = None,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    sessions: int = 1,
) -> dict:
    if mode == "external" and sessions != 1:
        raise ValueError("External agents manage their own sessions; use --sessions 1")
    agent_argv = None
    if mode == "external":
        agent_argv = shlex.split(agent_command or "")
        if not agent_argv:
            raise ValueError("External mode requires a nonempty --agent-command")
        if not math.isfinite(agent_timeout) or agent_timeout <= 0:
            raise ValueError("--agent-timeout must be a positive, finite number of seconds")
    case_dirs = sorted(path.parent for path in cases_root.glob("*/case.json"))
    if names:
        unknown = set(names) - {path.name for path in case_dirs}
        if unknown:
            raise ValueError(f"Unknown evaluation cases: {sorted(unknown)}")
        case_dirs = [path for path in case_dirs if path.name in names]
    directory = output.resolve() / time.strftime("%Y%m%d-%H%M%S")
    results = [
        run_case(
            case_dir,
            store,
            directory,
            mode=mode,
            reference=reference,
            agent_argv=agent_argv,
            agent_timeout=agent_timeout,
            sessions=sessions,
        )
        for case_dir in case_dirs
    ]
    summary = {
        "mode": "reference" if reference else mode,
        "sessions": sessions,
        "directory": str(directory),
        "mean_score": sum(item["score"] for item in results) / len(results),
        "false_completions": sum(item["false_completion"] for item in results),
        "usage": {
            key: sum((item["usage"] or {}).get(key, 0) for item in results)
            for key in usage_snapshot()
        },
        "cases": results,
    }
    if mode == "external" and not reference:
        summary["agent_command"] = agent_command
        summary["usage"] = None
    write_json(directory / "evaluation.json", summary)
    return summary
