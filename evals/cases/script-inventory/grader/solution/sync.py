"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import argparse
import csv
import json
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_ATTEMPTS = 3
RETRYABLE_STATUS = 503


def read_local(path):
    totals = {}
    with open(path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["sku", "qty"]:
            raise ValueError("CSV header must be sku,qty")
        for row in reader:
            if not row["sku"] or not row["qty"].isdigit():
                raise ValueError(f"Invalid row: {row}")
            totals[row["sku"]] = totals.get(row["sku"], 0) + int(row["qty"])
    return totals


def call(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                raise


def read_remote(base_url):
    totals, cursor = {}, None
    while True:
        query = "" if cursor is None else "?cursor=" + quote(cursor)
        page = call(f"{base_url}/stock{query}")
        for item in page["items"]:
            totals[item["sku"]] = item["qty"]
        cursor = page["next"]
        if cursor is None:
            return totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    try:
        local = read_local(args.csv)
    except (OSError, ValueError, KeyError, AttributeError) as error:
        print(f"invalid inventory: {error}", file=sys.stderr)
        return 2
    try:
        remote = read_remote(base_url)
        adjustments = []
        for sku in sorted(local.keys() | remote.keys()):
            delta = local.get(sku, 0) - remote.get(sku, 0)
            if delta == 0:
                continue
            if not args.dry_run:
                key = f"{sku}:{delta}"
                call(f"{base_url}/adjust", {"sku": sku, "delta": delta, "idempotency_key": key})
            adjustments.append({"sku": sku, "delta": delta})
    except (OSError, ValueError, KeyError) as error:
        print(f"sync failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"adjustments": adjustments, "dry_run": args.dry_run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
