"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

LOG_LINE = re.compile(
    r'\S+ - \S+ \[[^\]\r\n]+\] "[A-Z]+ (?P<target>/[^\s"]*) '
    r'HTTP/[0-9]+\.[0-9]+" (?P<status>[1-5][0-9]{2}) (?:[0-9]+|-)'
)
STATUS_FILTERS = ("1xx", "2xx", "3xx", "4xx", "5xx")


def analyze(path, status_filter):
    statuses, paths = Counter(), Counter()
    skipped = 0
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            match = LOG_LINE.fullmatch(line.rstrip("\n"))
            if match is None:
                skipped += 1
                continue
            status = match["status"]
            if status_filter and status[0] != status_filter[0]:
                continue
            statuses[status] += 1
            paths[match["target"].split("?", 1)[0]] += 1
    report = {
        "total_requests": sum(statuses.values()),
        "status_counts": dict(sorted(statuses.items())),
        "paths": [
            {"path": path, "count": count}
            for path, count in sorted(paths.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
    return report, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile")
    parser.add_argument("--out", required=True)
    parser.add_argument("--status", choices=STATUS_FILTERS)
    args = parser.parse_args()
    try:
        report, skipped = analyze(args.logfile, args.status)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as error:
        print(f"analysis failed: {error}", file=sys.stderr)
        return 2
    print(f"skipped_lines: {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
