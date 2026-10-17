import argparse
import json
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Prooflane local task registry")
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


if __name__ == "__main__":
    main()
