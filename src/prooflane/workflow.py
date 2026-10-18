"""Generic execution plumbing: run a service revision, execute cases, correlate logs."""

import json
from pathlib import Path

from prooflane.artifacts import write_json
from prooflane.execution import execute_case
from prooflane.process import checkout_service


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
