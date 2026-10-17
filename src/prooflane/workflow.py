"""Requirements → execution → defect evidence → supplied fix → regression."""

import difflib
import json
import platform
import shutil
from pathlib import Path

from prooflane.artifacts import digest, manifest, write_json
from prooflane.demo import payment_broken, payment_fixed
from prooflane.execution import execute_case
from prooflane.planning import fixture_plan, model_plan
from prooflane.process import checkout_service
from prooflane.reporting import write_report
from prooflane.requirements import import_requirements
from prooflane.store import Store


def compare_sources(directory: Path) -> None:
    paths = [Path(payment_broken.__file__), Path(payment_fixed.__file__)]
    versions = {}
    for name, path in zip(["broken", "fixed"], paths):
        target = directory / f"payment_{name}.py"
        shutil.copyfile(path, target)
        versions[name] = {"file": target.name, "sha256": digest(target)}
    write_json(directory / "source-versions.json", versions)
    source_root = Path(__file__).parent
    write_json(
        directory / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source_files": [
                {"path": str(path.relative_to(source_root)), "sha256": digest(path)}
                for path in sorted(source_root.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".html", ".md"}
            ],
        },
    )
    difference = difflib.unified_diff(
        paths[0].read_text().splitlines(keepends=True),
        paths[1].read_text().splitlines(keepends=True),
        fromfile="payment_broken.py",
        tofile="payment_fixed.py",
    )
    (directory / "source-comparison.diff").write_text("".join(difference), encoding="utf-8")


def correlate_logs(directory: Path, results: list[dict]) -> None:
    log_path = directory / "service.jsonl"
    logs = (
        [json.loads(line) for line in log_path.read_text().splitlines()]
        if log_path.exists()
        else []
    )
    for result in results:
        case_dir = directory / result["scenario"]
        traces = []
        for filename in ["http.json", "browser-network.json"]:
            path = case_dir / filename
            if path.exists():
                traces.extend(entry.get("trace_id") for entry in json.loads(path.read_text()))
        matched = [entry for entry in logs if entry["trace_id"] in traces]
        write_json(case_dir / "service-trace.json", matched)
        matched_ids = {entry["trace_id"] for entry in matched}
        if result["status"] != "blocked" and (
            not traces or any(trace not in matched_ids for trace in traces)
        ):
            result.update(status="blocked", error="Missing correlated service evidence")
        result["evidence"].append("service-trace.json")
        write_json(case_dir / "result.json", result)


def run_phase(
    directory: Path, revision: str, plan: dict, browser_enabled: bool, channel: str | None
) -> list[dict]:
    results = []
    with checkout_service(directory, revision) as environment:
        for case in plan["cases"]:
            results.append(
                execute_case(
                    case,
                    environment,
                    directory / case["scenario"],
                    browser_enabled=browser_enabled,
                    channel=channel,
                )
            )
    # The child has exited, so no further log writes can race evidence collection.
    correlate_logs(directory, results)
    write_json(directory / "results.json", results)
    return results


def run_closed_loop(
    store: Store,
    requirements: Path,
    output: Path,
    *,
    planner: str = "fixture",
    browser_enabled: bool = True,
    channel: str | None = None,
) -> dict:
    task_id = store.create("下单支付：需求到缺陷与回归闭环")
    directory = output.resolve() / task_id
    directory.mkdir(parents=True)
    summary = {
        "task_id": task_id,
        "status": "pending",
        "closed_loop": False,
        "baseline": [],
        "regression": [],
        "defects": [],
        "error": None,
    }
    try:
        store.transition(task_id, "planning", {"planner": planner})
        shutil.copyfile(requirements, directory / "requirements.md")
        summary["document"] = import_requirements(directory / "requirements.md")
        write_json(directory / "document.json", summary["document"])
        if planner not in {"fixture", "llm"}:
            raise ValueError("Unknown planner")
        summary["plan"] = (
            fixture_plan(summary["document"])
            if planner == "fixture"
            else model_plan(summary["document"])
        )
        write_json(directory / "plan.json", summary["plan"])
        plan_hash = digest(directory / "plan.json")
        compare_sources(directory)
        store.transition(task_id, "running", {"revision": "broken", "plan_sha256": plan_hash})
        summary["baseline"] = run_phase(
            directory / "baseline", "broken", summary["plan"], browser_enabled, channel
        )
        store.transition(task_id, "investigating", {"baseline_results": "baseline/results.json"})
        summary["defects"] = [
            {
                "id": f"D{index}",
                "scenario": case["scenario"],
                "requirement_id": case["requirement_id"],
                "observation": case["error"],
                "evidence": f"baseline/{case['scenario']}/result.json",
                "origin": "seeded-demo-defect",
                "repair": "pre-supplied fixed revision",
            }
            for index, case in enumerate(
                [case for case in summary["baseline"] if case["status"] == "failed"], start=1
            )
        ]
        write_json(directory / "defects.json", summary["defects"])
        store.transition(task_id, "regressing", {"revision": "fixed", "plan_sha256": plan_hash})
        summary["regression"] = run_phase(
            directory / "regression", "fixed", summary["plan"], browser_enabled, channel
        )
        fixed_results = {case["scenario"]: case for case in summary["regression"]}
        for defect in summary["defects"]:
            defect["regression_status"] = fixed_results[defect["scenario"]]["status"]
            defect["regression_evidence"] = f"regression/{defect['scenario']}/result.json"
        all_results = summary["baseline"] + summary["regression"]
        blocked = any(case["status"] == "blocked" for case in all_results)
        fixed_passed = all(case["status"] == "passed" for case in summary["regression"])
        same_plan = digest(directory / "plan.json") == plan_hash
        summary["closed_loop"] = (
            bool(summary["defects"]) and not blocked and fixed_passed and same_plan
        )
        summary["status"] = (
            "completed" if summary["closed_loop"] else "blocked" if blocked else "failed"
        )
        summary["plan_sha256"] = plan_hash
        store.transition(task_id, summary["status"], {"closed_loop": summary["closed_loop"]})
    except KeyboardInterrupt:
        summary.update(status="cancelled", error="Execution cancelled by user")
        store.transition(task_id, "cancelled", {})
    except Exception as error:
        summary.update(status="blocked", error=f"{type(error).__name__}: {error}")
        store.transition(task_id, "blocked", {"error": summary["error"]})
    summary["events"] = store.events(task_id)
    write_json(directory / "defects.json", summary["defects"])
    write_json(directory / "summary.json", summary)
    report = write_report(directory, summary)
    write_json(directory / "manifest.json", manifest(directory))
    for path in [report, directory / "summary.json", directory / "manifest.json"]:
        store.attach(task_id, path.as_uri(), path.name)
    return {**summary, "report": str(report), "directory": str(directory)}
