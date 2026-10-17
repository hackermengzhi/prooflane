import argparse
import json
from pathlib import Path

from .dispatcher import STAGES, run_task
from .store import Store
from .workflow import run_closed_loop


def main() -> int:
    parser = argparse.ArgumentParser(description="Prooflane requirements-to-regression testing")
    parser.add_argument("--db", default=".prooflane/state.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("list")
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
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == "init":
        print(json.dumps({"database": args.db}))
    elif args.command == "create":
        print(store.create(args.title))
    elif args.command == "list":
        print(json.dumps(store.tasks(), ensure_ascii=False, indent=2))
    elif args.command == "attach":
        print(store.attach(args.task_id, args.uri, args.description))
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
