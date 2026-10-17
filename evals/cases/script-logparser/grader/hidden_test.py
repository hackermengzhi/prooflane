"""Hidden acceptance tests: copied next to the agent's analyze.py only after it finishes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("analyze.py")


def log_line(target, status, *, method="GET", size="12"):
    return (
        f"192.0.2.1 - alice [18/Oct/2026:10:00:00 +0000] "
        f'"{method} {target} HTTP/1.1" {status} {size}\n'
    )


LOCAL_LOG = (
    log_line("/z", 503)
    + log_line("/health", 200)
    + log_line("/api?sort=asc", 201, method="POST")
    + log_line("/api?page=2", 500, size="-")
    + log_line("/z", 404)
    + log_line("/health", 200)
    + log_line("/a", 302)
    + log_line("/health", 204)
)
EMPTY_REPORT = {"total_requests": 0, "status_counts": {}, "paths": []}


class LogParserAcceptance(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=SCRIPT.parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "requests with spaces.log"
        self.out = self.root / "summary with spaces.json"
        self.log.write_text(LOCAL_LOG, encoding="utf-8")

    def run_analysis(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.log), "--out", str(self.out), *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def read_report(self, *extra):
        result = self.run_analysis(*extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        return json.loads(self.out.read_text(encoding="utf-8"))

    def test_counts_requests_by_status(self):
        report = self.read_report()
        self.assertEqual(report["total_requests"], 8)
        self.assertEqual(
            report["status_counts"],
            {"200": 2, "201": 1, "204": 1, "302": 1, "404": 1, "500": 1, "503": 1},
        )
        self.assertEqual(self.log.read_text(encoding="utf-8"), LOCAL_LOG)

    def test_sorts_paths_and_groups_query_strings(self):
        report = self.read_report()
        self.assertEqual(
            report["paths"],
            [
                {"path": "/health", "count": 3},
                {"path": "/api", "count": 2},
                {"path": "/z", "count": 2},
                {"path": "/a", "count": 1},
            ],
        )
        self.log.write_text(
            log_line("/A", 200) + log_line("/%41?x=1", 200) + log_line("/a?", 200),
            encoding="utf-8",
        )
        self.assertEqual(
            self.read_report()["paths"],
            [{"path": path, "count": 1} for path in ["/%41", "/A", "/a"]],
        )

    def test_filters_status_ranges_inclusively(self):
        statuses = [100, 199, 200, 299, 300, 399, 400, 499, 500, 599]
        self.log.write_text(
            "".join(log_line(f"/status/{status}", status) for status in reversed(statuses)),
            encoding="utf-8",
        )
        for group in range(1, 6):
            with self.subTest(group=group):
                low, high = group * 100, group * 100 + 99
                self.assertEqual(
                    self.read_report("--status", f"{group}xx"),
                    {
                        "total_requests": 2,
                        "status_counts": {str(low): 1, str(high): 1},
                        "paths": [
                            {"path": f"/status/{low}", "count": 1},
                            {"path": f"/status/{high}", "count": 1},
                        ],
                    },
                )
        self.log.write_text(log_line("/only-success", 200), encoding="utf-8")
        self.assertEqual(self.read_report("--status", "5xx"), EMPTY_REPORT)

    def test_writes_deterministic_json_to_output(self):
        lines = [log_line("/日志", 500), log_line("/a", 200), log_line("/a?x=1", 200)]
        expected = (
            '{\n  "total_requests": 3,\n  "status_counts": {\n    "200": 2,\n'
            '    "500": 1\n  },\n  "paths": [\n    {\n      "path": "/a",\n'
            '      "count": 2\n    },\n    {\n      "path": "/日志",\n'
            '      "count": 1\n    }\n  ]\n}\n'
        ).encode("utf-8")
        for records in [lines, list(reversed(lines)), lines]:
            self.log.write_text("".join(records), encoding="utf-8")
            self.out.write_text("stale output must be replaced\n" * 50, encoding="utf-8")
            result = self.run_analysis()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "skipped_lines: 0\n")
            self.assertEqual(self.out.read_bytes(), expected)

    def test_skips_and_reports_malformed_lines(self):
        valid = log_line("/ok", 200)
        invalid = [
            "\n",
            "not a log line\n",
            log_line("/bad", "oops"),
            log_line("/bad", "099"),
            log_line("/bad", "600"),
            log_line("/bad", 200, size="-1"),
            log_line("/bad", 200, method="get"),
            log_line("relative", 200),
            valid.rstrip("\n") + " trailing garbage\n",
            '192.0.2.1 - - [time] "GET /bad HTTP/1.1" 200\n',
        ]
        self.log.write_text(valid + "".join(invalid) + valid.rstrip("\n"), encoding="utf-8")
        result = self.run_analysis()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, f"skipped_lines: {len(invalid)}\n")
        self.assertEqual(
            json.loads(self.out.read_text(encoding="utf-8")),
            {
                "total_requests": 2,
                "status_counts": {"200": 2},
                "paths": [{"path": "/ok", "count": 2}],
            },
        )

    def test_reports_invalid_lines_before_status_filtering(self):
        self.log.write_text(
            log_line("/ok", 200) + "broken 200\n" + log_line("/error", 503) + "broken 503\n",
            encoding="utf-8",
        )
        result = self.run_analysis("--status", "5xx")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "skipped_lines: 2\n")
        self.assertEqual(
            json.loads(self.out.read_text(encoding="utf-8")),
            {
                "total_requests": 1,
                "status_counts": {"503": 1},
                "paths": [{"path": "/error", "count": 1}],
            },
        )

    def test_empty_and_invalid_only_inputs(self):
        for content, skipped in [("", 0), ("\nbroken\n", 2)]:
            with self.subTest(content=content):
                self.log.write_text(content, encoding="utf-8")
                result = self.run_analysis()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, f"skipped_lines: {skipped}\n")
                self.assertEqual(json.loads(self.out.read_text(encoding="utf-8")), EMPTY_REPORT)

    def test_missing_input_exits_2(self):
        # A missing script or unknown option also exits 2, so first prove valid input works.
        self.read_report()
        self.log.unlink()
        self.out.unlink()
        result = self.run_analysis()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())
        self.assertFalse(self.out.exists())

    def test_invalid_status_filter_exits_2(self):
        self.read_report()
        for status in ["500", "6xx", "5XX", "oops"]:
            with self.subTest(status=status):
                result = self.run_analysis("--status", status)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.strip())


if __name__ == "__main__":
    unittest.main()
