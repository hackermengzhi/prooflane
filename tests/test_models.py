import json
import os
import unittest
from unittest.mock import MagicMock, patch

from prooflane.models import request_json

RESPONSES_URL = "http://127.0.0.1:12345/v1/responses"


def respond_with(result):
    """Return an opener stub that answers like a model endpoint; not a real model."""
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(result).encode()
    return opener


def message(*parts):
    return {"type": "message", "content": list(parts)}


class ResponsesProtocolTests(unittest.TestCase):
    def request(self, result):
        opener = respond_with(result)
        with (
            patch.dict(
                os.environ,
                {"PROOFLANE_LLM_URL": RESPONSES_URL, "PROOFLANE_LLM_MODEL": "contract-stub"},
                clear=True,
            ),
            patch("prooflane.models.build_opener", return_value=opener),
        ):
            decision = request_json("Return one JSON action.", {"goal": "测试"})
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.get_header("User-agent"), "prooflane")
        return decision, json.loads(sent.data)

    def test_responses_url_sends_responses_body_and_reads_message_text(self):
        result = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                message({"type": "output_text", "text": '{"action": '}),
                message({"type": "output_text", "text": '"finish"}'}),
            ],
        }
        (decision, model), body = self.request(result)
        self.assertEqual((decision, model), ({"action": "finish"}, "contract-stub"))
        self.assertEqual(body["instructions"], "Return one JSON action.")
        self.assertEqual(body["input"][0]["content"], '{"goal": "测试"}')
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertIs(body["store"], False)
        self.assertNotIn("messages", body)

    def test_truncated_or_refused_responses_are_rejected(self):
        for result in [
            {"status": "incomplete", "output": [message({"type": "output_text", "text": "{}"})]},
            {"status": "completed", "output": [message({"type": "refusal", "refusal": "no"})]},
            {"status": "completed", "output": [{"type": "reasoning", "summary": []}]},
        ]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.request(result)
