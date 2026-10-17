"""Score testing and automation agents on seeded cases with graders hidden from the agent."""

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prooflane.agent import run_agent
from prooflane.artifacts import write_json
from prooflane.harness import run_harness
from prooflane.models import usage_snapshot
from prooflane.store import Store
from prooflane.tools import ToolPolicy
from prooflane.trials import COPY_IGNORE, mutate, run_trial

RUNNERS = {"harness": run_harness, "agent": run_agent}


def prepare_workspace(case_dir: Path, grading: dict, destination: Path) -> Path:
    shutil.copytree(case_dir / "workspace", destination, ignore=COPY_IGNORE)
    if grading["kind"] == "mutation":
        # The defective target is derived from the reference so seeded bugs cannot drift.
        reference = (case_dir / "grader" / grading["reference"]).read_text(encoding="utf-8")
        seeded = [item["replace"] for item in grading["mutants"] if item["id"] in grading["seeded"]]
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


GRADERS = {"mutation": grade_mutation, "hidden": grade_hidden}


def run_case(case_dir: Path, store: Store, output: Path, *, mode: str, reference: bool) -> dict:
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
        run = RUNNERS[mode](
            store, case["goal"], policy, case_output / "runs", max_steps=case["max_steps"]
        )
    grade = GRADERS[case["grading"]["kind"]](case_dir, case["grading"], workspace)
    return {
        "case": case_dir.name,
        "battleground": case["battleground"],
        "status": run["status"],
        "error": run["error"],
        "steps": len(run["steps"]),
        "usage": run.get("usage"),
        "seconds": round(time.monotonic() - started, 1),
        "report": run["report"],
        "score": grade["score"],
        # Claiming completion while hidden grading disagrees is the costliest agent failure.
        "false_completion": run["status"] == "completed" and grade["score"] < 1,
        "grade": grade,
    }


def run_evaluation(
    cases_root: Path,
    store: Store,
    output: Path,
    *,
    names: list[str] | None = None,
    mode: str = "harness",
    reference: bool = False,
) -> dict:
    case_dirs = sorted(path.parent for path in cases_root.glob("*/case.json"))
    if names:
        unknown = set(names) - {path.name for path in case_dirs}
        if unknown:
            raise ValueError(f"Unknown evaluation cases: {sorted(unknown)}")
        case_dirs = [path for path in case_dirs if path.name in names]
    directory = output.resolve() / time.strftime("%Y%m%d-%H%M%S")
    results = [
        run_case(case_dir, store, directory, mode=mode, reference=reference)
        for case_dir in case_dirs
    ]
    summary = {
        "mode": "reference" if reference else mode,
        "directory": str(directory),
        "mean_score": sum(item["score"] for item in results) / len(results),
        "false_completions": sum(item["false_completion"] for item in results),
        "usage": {
            key: sum((item["usage"] or {}).get(key, 0) for item in results)
            for key in usage_snapshot()
        },
        "cases": results,
    }
    write_json(directory / "evaluation.json", summary)
    return summary
