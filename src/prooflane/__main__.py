import argparse
import json
from pathlib import Path

from .agent import run_agent
from .dispatcher import STAGES, run_task
from .evaluation import DEFAULT_AGENT_TIMEOUT, compare_runs, run_evaluation
from .harness import run_harness
from .models import list_models
from .store import Store
from .tools import ToolPolicy
from .workflow import run_closed_loop


def _print_comparison(comparison: dict) -> None:
    labels = comparison["labels"]
    rows = [["case", *labels]]
    for case in comparison["cases"]:
        scores = [case["runs"][label]["score"] for label in labels]
        rows.append(
            [case["case"], *[f"{score:.3f}" if score is not None else "-" for score in scores]]
        )
    rows.append(
        ["mean_score", *[f"{comparison['summary'][label]['mean_score']:.3f}" for label in labels]]
    )
    widths = [max(len(value) for value in column) for column in zip(*rows)]
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Prooflane requirements-to-regression testing")
    parser.add_argument("--db", default=".prooflane/state.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("list")
    commands.add_parser("models", help="List models offered by the configured provider")
    create = commands.add_parser("create")
    create.add_argument("title")
    attach = commands.add_parser("attach")
    attach.add_argument("task_id")
    attach.add_argument("uri")
    attach.add_argument("description")
    demo = commands.add_parser("demo", help="Run the complete local checkout testing loop")
    demo.add_argument(
        "--requirements", type=Path, default=Path(__file__).parent / "demo" / "requirements.md"
    )
    demo.add_argument("--output", type=Path, default=Path(".prooflane/runs"))
    demo.add_argument("--planner", choices=["fixture", "llm"], default="fixture")
    demo.add_argument(
        "--no-browser", action="store_true", help="Run API coverage only; full loop remains blocked"
    )
    demo.add_argument(
        "--browser-channel", choices=["chrome", "msedge"], help="Use an installed browser"
    )
    run = commands.add_parser("run", help="Dispatch a goal from any supported testing stage")
    run.add_argument("goal", help="User goal, such as '调查支付失败原因' or '完成完整闭环'")
    run.add_argument("--requirements", type=Path)
    run.add_argument("--from-run", type=Path)
    run.add_argument("--entry", choices=list(STAGES))
    run.add_argument("--until", choices=list(STAGES))
    run.add_argument("--scheduler", choices=["rules", "llm"], default="rules")
    run.add_argument("--planner", choices=["fixture", "llm"], default="fixture")
    run.add_argument("--output", type=Path, default=Path(".prooflane/runs"))
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--browser-channel", choices=["chrome", "msedge"])
    agent = commands.add_parser("agent", help="General testing, coding and automation work")
    agent.add_argument("goal", nargs="?", help="Omit when resuming; the task keeps its goal")
    agent.add_argument("--resume", metavar="TASK_ID", help="Continue a blocked or cancelled task")
    agent.add_argument("--workspace", type=Path, required=True)
    agent.add_argument(
        "--recipe", type=Path, help="Optional reproducible tool-action recipe; otherwise uses LLM"
    )
    agent.add_argument("--checks", type=Path, help="Optional immutable acceptance checks JSON list")
    agent.add_argument(
        "--allow-process", action="store_true", help="Enable process execution in the workspace"
    )
    agent.add_argument(
        "--allow-origin", action="append", default=[], help="HTTP/browser origin; repeat as needed"
    )
    agent.add_argument("--browser-channel", choices=["chrome", "msedge"])
    agent.add_argument("--max-steps", type=int, default=20)
    agent.add_argument("--output", type=Path, default=Path(".prooflane/agent-runs"))
    harness = commands.add_parser("harness", help="Leader/product/developer/tester organization")
    harness.add_argument("goal", nargs="?", help="Omit when resuming; the task keeps its goal")
    harness.add_argument("--resume", metavar="TASK_ID", help="Continue a blocked or cancelled task")
    harness.add_argument("--workspace", type=Path, required=True)
    harness.add_argument("--checks", type=Path)
    harness.add_argument("--allow-process", action="store_true")
    harness.add_argument("--allow-origin", action="append", default=[])
    harness.add_argument("--browser-channel", choices=["chrome", "msedge"])
    harness.add_argument("--max-steps", type=int, default=30)
    harness.add_argument("--output", type=Path, default=Path(".prooflane/harness-runs"))
    evaluation = commands.add_parser(
        "eval", help="Score agents on seeded cases with hidden graders"
    )
    evaluation.add_argument("--cases", type=Path, default=Path("evals/cases"))
    evaluation.add_argument("--case", action="append", help="Case directory name; repeat as needed")
    evaluation.add_argument("--mode", choices=["harness", "agent", "external"], default="harness")
    evaluation.add_argument(
        "--agent-command", help="External agent command, split into argv without a shell"
    )
    evaluation.add_argument(
        "--agent-timeout",
        type=float,
        default=DEFAULT_AGENT_TIMEOUT,
        help="Seconds per external case",
    )
    evaluation.add_argument(
        "--reference", action="store_true", help="Grade bundled reference answers to validate cases"
    )
    evaluation.add_argument("--output", type=Path, default=Path(".prooflane/eval-runs"))
    comparison = commands.add_parser("eval-compare", help="Compare saved evaluation results")
    comparison.add_argument("runs", type=Path, nargs="+", help="Two or more evaluation.json paths")
    comparison.add_argument("--json", action="store_true", help="Print structured comparison JSON")
    args = parser.parse_args()
    if args.command == "eval-compare":
        if len(args.runs) < 2:
            parser.error("eval-compare requires at least two evaluation.json paths")
        try:
            runs = [json.loads(path.read_text(encoding="utf-8")) for path in args.runs]
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        result = compare_runs(runs)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_comparison(result)
        return 0
    if args.command == "eval" and args.mode == "external" and not args.agent_command:
        parser.error("--mode external requires --agent-command")
    if args.command in {"agent", "harness"} and args.goal is None and args.resume is None:
        parser.error(f"{args.command} requires a goal or --resume TASK_ID")
    store = Store(args.db)
    if args.command == "init":
        print(json.dumps({"database": args.db}))
    elif args.command == "create":
        print(store.create(args.title))
    elif args.command == "models":
        print(json.dumps(list_models(), indent=2))
    elif args.command == "list":
        print(json.dumps(store.tasks(), ensure_ascii=False, indent=2))
    elif args.command == "attach":
        print(store.attach(args.task_id, args.uri, args.description))
    elif args.command == "eval":
        summary = run_evaluation(
            args.cases,
            store,
            args.output,
            names=args.case,
            mode=args.mode,
            reference=args.reference,
            agent_command=args.agent_command,
            agent_timeout=args.agent_timeout,
        )
        rows = [
            {key: case[key] for key in ["case", "status", "score", "false_completion", "steps"]}
            for case in summary["cases"]
        ]
        print(
            json.dumps(
                {
                    "mean_score": summary["mean_score"],
                    "false_completions": summary["false_completions"],
                    "usage": summary["usage"],
                    "cases": rows,
                    "details": str(Path(summary["directory"]) / "evaluation.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "harness":
        try:
            result = run_harness(
                store,
                args.goal,
                ToolPolicy(
                    args.workspace,
                    args.allow_process,
                    tuple(args.allow_origin),
                    args.browser_channel,
                ),
                args.output,
                checks=json.loads(args.checks.read_text()) if args.checks else None,
                max_steps=args.max_steps,
                resume=args.resume,
            )
        except ValueError as error:
            # Raised before a task exists, for input such as an unresumable task.
            parser.error(str(error))
        print(
            json.dumps(
                {key: result[key] for key in ["task_id", "status", "report", "error"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result["status"] == "completed" else 2
    elif args.command == "agent":
        try:
            result = run_agent(
                store,
                args.goal,
                ToolPolicy(
                    args.workspace,
                    args.allow_process,
                    tuple(args.allow_origin),
                    args.browser_channel,
                ),
                args.output,
                recipe=json.loads(args.recipe.read_text()) if args.recipe else None,
                checks=json.loads(args.checks.read_text()) if args.checks else None,
                max_steps=args.max_steps,
                resume=args.resume,
            )
        except ValueError as error:
            # Raised before a task exists, for input such as an unresumable task.
            parser.error(str(error))
        print(
            json.dumps(
                {key: result[key] for key in ["task_id", "status", "report", "error"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return (
            0 if result["status"] == "completed" else 130 if result["status"] == "cancelled" else 2
        )
    elif args.command in {"demo", "run"}:
        if args.command == "demo":
            result = run_closed_loop(
                store,
                args.requirements,
                args.output,
                planner=args.planner,
                browser_enabled=not args.no_browser,
                channel=args.browser_channel,
            )
        else:
            result = run_task(
                store,
                args.goal,
                args.output,
                requirements=args.requirements,
                entry=args.entry,
                target=args.until,
                prior_run=args.from_run,
                scheduler=args.scheduler,
                planner=args.planner,
                browser_enabled=not args.no_browser,
                channel=args.browser_channel,
            )
        print(
            json.dumps(
                {
                    "task_id": result["task_id"],
                    "status": result["status"],
                    "closed_loop": result["closed_loop"],
                    "report": result["report"],
                    "error": result["error"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return (
            0 if result["status"] == "completed" else 130 if result["status"] == "cancelled" else 2
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
