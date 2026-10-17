"""Verify prior evidence before importing it into a new scheduling context."""

import json
import shutil
from pathlib import Path

from prooflane.artifacts import digest
from prooflane.planning import validate_fixture_contract, validate_plan
from prooflane.requirements import import_requirements

EXECUTION_SOURCES = {
    "execution.py",
    "workflow.py",
    "process.py",
    "artifacts.py",
    "planning.py",
    "demo/service.py",
    "demo/checkout.html",
    "demo/payment_broken.py",
    "demo/payment_fixed.py",
}


def verify_run(directory: Path) -> dict:
    directory = directory.resolve()
    files = [path for path in directory.rglob("*") if path.is_file()]
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError("Prior run contains symbolic links")
    manifest = json.loads((directory / "manifest.json").read_text())
    listed = set()
    for item in manifest:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid evidence path in prior run")
        path = directory / relative
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"Prior evidence integrity check failed: {relative}")
        listed.add(str(relative))
    actual = {str(path.relative_to(directory)) for path in files if path.name != "manifest.json"}
    if listed != actual:
        raise ValueError("Prior run manifest does not cover all evidence files")
    return json.loads((directory / "summary.json").read_text())


def import_prior(
    source: Path, destination: Path, summary: dict, requirements: Path, prior: dict
) -> set[str]:
    document = import_requirements(source / "requirements.md")
    if digest(requirements) != document["sha256"]:
        summary["reuse_notes"].append("需求版本发生变化，旧计划与执行证据未复用。")
        return set()
    validate_fixture_contract(document)
    summary["document"] = document
    if "review" in prior:
        summary["review"] = prior["review"]
    completed = {"review"}
    if "plan" not in prior:
        return completed
    if json.loads((source / "plan.json").read_text()) != prior["plan"]:
        raise ValueError("Prior summary disagrees with its plan")
    plan = {**prior["plan"], **validate_plan(prior["plan"], document)}
    summary["plan"] = plan
    completed.add("design")
    versions_path = source / "environment.json"
    if not versions_path.exists():
        summary["reuse_notes"].append("旧执行缺少源码版本记录，仅复用需求与计划。")
        return completed
    versions = {
        item["path"]: item["sha256"]
        for item in json.loads(versions_path.read_text())["source_files"]
    }
    root = Path(__file__).parent
    if any(versions.get(name) != digest(root / name) for name in EXECUTION_SOURCES):
        summary["reuse_notes"].append("执行器或被测业务代码已变化，旧执行结果必须重跑。")
        return completed
    baseline = prior.get("baseline", [])
    if not baseline:
        return completed
    cases = {case["scenario"] for case in plan["cases"]}
    if {case["scenario"] for case in baseline} != cases or len(baseline) != len(cases):
        raise ValueError("Prior execution does not match the validated plan")
    if json.loads((source / "baseline/results.json").read_text()) != baseline:
        raise ValueError("Prior summary disagrees with execution evidence")
    for case in baseline:
        if case["status"] not in {"passed", "failed", "blocked"}:
            raise ValueError("Invalid prior execution status")
        result = source / "baseline" / case["scenario"] / "result.json"
        if json.loads(result.read_text()) != case:
            raise ValueError("Prior case evidence disagrees with summary")
    shutil.copytree(source / "baseline", destination / "baseline")
    summary["baseline"] = baseline
    completed.add("execute")
    summary["reuse_notes"].append("原版执行证据通过摘要和版本校验；修复版回归始终重新执行。")
    return completed
