import copy
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from prooflane.demo import service
from prooflane.planning import fixture_plan, model_plan, validate_plan
from prooflane.requirements import import_requirements


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.document = import_requirements(Path(service.__file__).with_name("requirements.md"))

    def test_source_line_and_requirement_coverage(self):
        self.assertEqual(self.document["requirements"][0]["line"], 3)
        plan = fixture_plan(self.document)
        self.assertFalse(plan["model_called"])
        self.assertEqual(len(plan["cases"]), 10)
        self.assertEqual(
            {case["requirement_id"] for case in plan["cases"]}, {f"R{i}" for i in range(1, 8)}
        )

    def test_missing_unknown_and_remapped_scenarios_rejected(self):
        plan = fixture_plan(self.document)
        for alteration in ["missing", "unknown", "mapping", "duplicate"]:
            with self.subTest(alteration=alteration):
                changed = copy.deepcopy(plan)
                if alteration == "missing":
                    changed["cases"].pop()
                elif alteration == "unknown":
                    changed["cases"][0]["scenario"] = "execute_shell"
                elif alteration == "mapping":
                    changed["cases"][0]["requirement_id"] = "R7"
                else:
                    changed["cases"].append(changed["cases"][0])
                with self.assertRaises(ValueError):
                    validate_plan(changed, self.document)

    def test_duplicate_document_identifiers_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requirements.md"
            path.write_text("1. first\n1. second\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                import_requirements(path)

    def test_model_requires_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "PROOFLANE_LLM_URL"):
                model_plan(self.document)

    def test_truncated_or_invented_model_plan_cannot_pass_validation(self):
        for response in [
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": '{"cases": []}'}}]},
        ]:
            with self.subTest(response=response):
                opener = MagicMock()
                opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
                    response
                ).encode()
                with (
                    patch.dict(
                        os.environ,
                        {
                            "PROOFLANE_LLM_URL": "http://127.0.0.1:12345/v1/chat/completions",
                            "PROOFLANE_LLM_MODEL": "contract-stub",
                        },
                        clear=True,
                    ),
                    patch("prooflane.planning.build_opener", return_value=opener),
                ):
                    with self.assertRaises(ValueError):
                        model_plan(self.document)

    def test_model_http_contract_with_local_stub_not_a_real_model(self):
        expected = fixture_plan(self.document)
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                payload = {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(expected)}}
                    ]
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(
                    os.environ,
                    {
                        "PROOFLANE_LLM_URL": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                        "PROOFLANE_LLM_MODEL": "local-contract-stub",
                    },
                    clear=True,
                ):
                    plan = model_plan(self.document)
                self.assertTrue(plan["model_called"])
                self.assertEqual(plan["cases"], expected["cases"])
                self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
            finally:
                server.shutdown()
                thread.join(timeout=2)
