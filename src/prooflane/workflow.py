"""Requirements → execution → defect evidence → supplied fix → regression."""

import difflib
import json
import platform
import shutil
from pathlib import Path

from prooflane.artifacts import digest, write_json
from prooflane.demo import payment_broken, payment_fixed
from prooflane.execution import execute_case
from prooflane.process import checkout_service
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
    """Compatibility entry point; all stage selection is delegated to the dispatcher."""
    from prooflane.dispatcher import run_task

    return run_task(
        store,
        "完成下单支付完整闭环",
        output,
        requirements=requirements,
        target="regress",
        planner=planner,
        browser_enabled=browser_enabled,
        channel=channel,
    )
