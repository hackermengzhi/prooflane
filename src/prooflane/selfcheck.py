"""Check an agent's tests against its own mutations on disposable workspace copies."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prooflane.trials import OUTPUT_TAIL, mutate, run_trial

MAX_MUTANTS = 30
TRIAL_WORKERS = 4


def check_mutations(
    workspace: Path,
    target: str,
    source: str,
    suite: list[str],
    mutants: list[dict],
    timeout: int,
) -> dict:
    if not isinstance(suite, list) or not suite or not all(isinstance(arg, str) for arg in suite):
        raise ValueError("suite must be a nonempty list of strings")
    if not isinstance(mutants, list) or len(mutants) > MAX_MUTANTS:
        raise ValueError(f"mutants must be a list of at most {MAX_MUTANTS} items")
    identifiers = set()
    for item in mutants:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("Each mutant requires a nonempty string id")
        if item["id"] in identifiers:
            raise ValueError("Mutant ids must be unique")
        identifiers.add(item["id"])
        replacement = item.get("replace")
        if (
            not isinstance(replacement, list)
            or len(replacement) != 2
            or not all(isinstance(text, str) for text in replacement)
            or not replacement[0]
        ):
            raise ValueError("Mutant replace must contain nonempty old text and replacement text")

    baseline = run_trial(workspace, {}, suite, timeout)
    result = {
        "baseline_passed": baseline["passed"],
        "baseline_output": None if baseline["passed"] else baseline["output"],
        "mutants": [],
        "kill_rate": 0.0,
        "survivors": [],
    }
    if not baseline["passed"]:
        return result

    def check_mutant(item):
        try:
            content = mutate(source, [item["replace"]])
        except ValueError as error:
            return {"id": item["id"], "status": "invalid", "reason": str(error)[-OUTPUT_TAIL:]}
        trial = run_trial(workspace, {target: content}, suite, timeout)
        if trial["returncode"] is None:
            status = "timeout"
        else:
            status = "survived" if trial["passed"] else "killed"
        return {
            "id": item["id"],
            "status": status,
            "output": None if trial["passed"] else trial["output"],
        }

    with ThreadPoolExecutor(max_workers=TRIAL_WORKERS) as pool:
        result["mutants"] = list(pool.map(check_mutant, mutants))
    valid = [item for item in result["mutants"] if item["status"] != "invalid"]
    # A timeout is valid but is not evidence that the suite killed the mutation.
    if valid:
        result["kill_rate"] = sum(item["status"] == "killed" for item in valid) / len(valid)
    result["survivors"] = [item["id"] for item in result["mutants"] if item["status"] == "survived"]
    return result
