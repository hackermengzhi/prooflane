"""Detect changing suite outcomes on independent disposable workspace copies."""

from pathlib import Path

from prooflane.trials import OUTPUT_TAIL, run_trial

MAX_RUNS = 20


def check_flaky(workspace: Path, suite: list[str], runs: int, timeout: int) -> dict:
    if not isinstance(suite, list) or not suite or not all(isinstance(arg, str) for arg in suite):
        raise ValueError("suite must be a nonempty list of strings")
    if type(runs) is not int or not 2 <= runs <= MAX_RUNS:
        raise ValueError(f"runs must be an integer in 2..{MAX_RUNS}")

    result = {"runs": runs, "pass_count": 0, "fail_count": 0, "results": []}
    for index in range(1, runs + 1):
        trial = run_trial(workspace, {}, suite, timeout)
        result["results"].append(
            {"index": index, "passed": trial["passed"], "returncode": trial["returncode"]}
        )
        if trial["passed"]:
            result["pass_count"] += 1
        else:
            result["fail_count"] += 1
            if result["fail_count"] == 1:
                result["first_failure_output"] = trial["output"][-OUTPUT_TAIL:]

    if result["fail_count"] == 0:
        result["verdict"] = "stable_pass"
    elif result["pass_count"] == 0:
        result["verdict"] = "stable_fail"
    else:
        result["verdict"] = "flaky"
    return result
