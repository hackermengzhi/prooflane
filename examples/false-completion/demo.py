"""Replay fixed deliverables through Prooflane's real hidden evaluation path."""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from prooflane.artifacts import write_json
from prooflane.evaluation import run_case
from prooflane.store import Store
from prooflane.trials import COPY_IGNORE

ASSETS = Path(__file__).resolve().parent
DELIVERABLES = ("correct", "false-completion")


def run_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output.resolve()))
    store = Store(str(directory / "state.db"))
    results = []
    for name in DELIVERABLES:
        case_dir = directory / "cases" / name
        shutil.copytree(ASSETS / "case", case_dir, ignore=COPY_IGNORE)
        shutil.copyfile(
            ASSETS / "deliverables" / name / "shipping.py",
            case_dir / "workspace" / "shipping.py",
        )
        results.append(
            run_case(
                case_dir,
                store,
                directory / "runs",
                mode="external",
                reference=False,
                agent_argv=[sys.executable, "claim.py"],
                agent_timeout=10,
            )
        )
    summary = {"directory": str(directory), "cases": results}
    write_json(directory / "evaluation.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="万测王：离线拆穿虚假完成")
    parser.add_argument("--output", type=Path, default=Path(".prooflane/rigor-demo"))
    args = parser.parse_args()
    summary = run_demo(args.output)
    rows = [["deliverable", "status", "score", "false_completion"]]
    for result in summary["cases"]:
        rows.append(
            [
                result["case"],
                result["status"],
                f"{result['score']:.3f}",
                str(result["false_completion"]).lower(),
            ]
        )
    widths = [max(len(value) for value in column) for column in zip(*rows)]
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
    correct, false_completion = summary["cases"]
    verified = (
        correct["status"] == "completed"
        and correct["score"] == 1
        and not correct["false_completion"]
        and false_completion["false_completion"]
    )
    if verified:
        print("万测王用独立执行证据证明：#2 自称‘完成’，但隐藏验收未通过，其‘完成’是假的。")
    else:
        print("演示未达到预期，请检查评分证据。")
    print(f"Evidence: {summary['directory']}/evaluation.json")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
