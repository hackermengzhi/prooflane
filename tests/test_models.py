import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from prooflane.models import list_models, load_settings, request_json, usage_since, usage_snapshot

RESPONSES_URL = "http://127.0.0.1:12345/v1/responses"
CONFIG = """
model = "gpt-test"
model_provider = "relay"
reasoning_effort = "high"

[model_providers.relay]
base_url = "http://127.0.0.1:12345/v1/"
wire_api = "responses"
env_key = "RELAY_KEY"
max_retries = 2
"""


def reply(result):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(result).encode()
    return response


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return HTTPError(RESPONSES_URL, code, "error", headers, io.BytesIO(b"{}"))


def message(*parts):
    return {"type": "message", "content": list(parts)}


COMPLETED = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        message({"type": "output_text", "text": '{"action": '}),
        message({"type": "output_text", "text": '"finish"}'}),
    ],
}


class ModelTransportTests(unittest.TestCase):
    """Contract stubs stand in for the provider; no real model is called."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / "config.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.sleeps = []
        sleep = patch("prooflane.models.time.sleep", side_effect=self.sleeps.append)
        sleep.start()
        self.addCleanup(sleep.stop)

    def call(self, environment, responses):
        opener = MagicMock()
        opener.open.side_effect = responses
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("prooflane.models.build_opener", return_value=opener),
        ):
            decision = request_json("Return one JSON action.", {"goal": "测试"})
        return decision, [call.args[0] for call in opener.open.call_args_list]

    def environment(self, **extra):
        return {"PROOFLANE_LLM_URL": RESPONSES_URL, "PROOFLANE_LLM_MODEL": "stub", **extra}

    def configured(self):
        return {"PROOFLANE_CONFIG": str(self.config), "RELAY_KEY": "secret"}

    def test_responses_url_sends_responses_body_and_reads_message_text(self):
        (decision, model), sent = self.call(self.environment(), [reply(COMPLETED)])
        self.assertEqual((decision, model), ({"action": "finish"}, "stub"))
        body = json.loads(sent[0].data)
        self.assertEqual(sent[0].full_url, RESPONSES_URL)
        self.assertEqual(sent[0].get_header("User-agent"), "prooflane")
        self.assertEqual(body["instructions"], "Return one JSON action.")
        self.assertEqual(body["input"][0]["content"], '{"goal": "测试"}')
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertIs(body["store"], False)
        self.assertNotIn("messages", body)

    def test_usage_is_accounted_for_both_protocols(self):
        before = usage_snapshot()
        completed = {**COMPLETED, "usage": {"input_tokens": 120, "output_tokens": 30}}
        self.call(self.environment(), [reply(completed)])
        chat = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5},
        }
        chat_url = "http://127.0.0.1:12345/v1/chat/completions"
        self.call(self.environment(PROOFLANE_LLM_URL=chat_url), [reply(chat)])
        self.call(self.environment(), [reply(COMPLETED)])
        self.assertEqual(
            usage_since(before), {"requests": 3, "input_tokens": 170, "output_tokens": 35}
        )

    def test_truncated_or_refused_responses_are_rejected(self):
        for result in [
            {"status": "incomplete", "output": [message({"type": "output_text", "text": "{}"})]},
            {"status": "completed", "output": [message({"type": "refusal", "refusal": "no"})]},
            {"status": "completed", "output": [{"type": "reasoning", "summary": []}]},
        ]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.call(self.environment(), [reply(result)])

    def test_fenced_or_prefixed_json_is_recovered(self):
        for text in [
            '```json\n{"action": "finish"}\n```',
            'Here is the decision:\n{"action": "finish"}',
            '{"action": "finish"}\nThat completes the task.',
            '{"action": "finish", "reason": "a } inside a string"}',
        ]:
            with self.subTest(text=text):
                result = {
                    "status": "completed",
                    "output": [message({"type": "output_text", "text": text})],
                }
                (decision, _), _ = self.call(self.environment(), [reply(result)])
                self.assertEqual(decision["action"], "finish")

    def test_text_without_any_json_object_is_rejected(self):
        result = {
            "status": "completed",
            "output": [message({"type": "output_text", "text": "no json here"})],
        }
        with self.assertRaises(ValueError):
            self.call(self.environment(), [reply(result)])

    def test_config_file_selects_provider_key_and_reasoning_effort(self):
        (decision, model), sent = self.call(self.configured(), [reply(COMPLETED)])
        self.assertEqual(model, "gpt-test")
        self.assertEqual(sent[0].full_url, "http://127.0.0.1:12345/v1/responses")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(sent[0].data)["reasoning"], {"effort": "high"})

    def test_config_rejects_inline_keys_and_missing_key_variable(self):
        with patch.dict(os.environ, {"PROOFLANE_CONFIG": str(self.config)}, clear=True):
            with self.assertRaisesRegex(ValueError, "RELAY_KEY"):
                load_settings()
            self.config.write_text(CONFIG + 'api_key = "sk-inline"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "env_key"):
                load_settings()

    def test_rate_limits_and_gateway_errors_are_retried_with_backoff(self):
        responses = [http_error(429, retry_after="7"), http_error(502), reply(COMPLETED)]
        (decision, _), sent = self.call(self.environment(), responses)
        self.assertEqual(decision, {"action": "finish"})
        self.assertEqual((len(sent), self.sleeps), (3, [7, 4]))

    def test_cloudflare_origin_timeout_is_retried(self):
        # 524 (and other Cloudflare origin-side codes) are transient, not fatal.
        responses = [http_error(524), reply(COMPLETED)]
        (decision, _), sent = self.call(self.environment(), responses)
        self.assertEqual(decision, {"action": "finish"})
        self.assertEqual(len(sent), 2)

    def test_retries_stop_at_budget_and_skip_client_errors(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.call(self.configured(), [http_error(503)] * 3)
        self.assertEqual(len(self.sleeps), 2)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            self.call(self.configured(), [http_error(401), reply(COMPLETED)])
        self.assertEqual(len(self.sleeps), 2)

    def test_list_models_reads_provider_catalog(self):
        opener = MagicMock()
        opener.open.return_value = reply({"data": [{"id": "b"}, {"id": "a"}]})
        with (
            patch.dict(os.environ, self.environment(), clear=True),
            patch("prooflane.models.build_opener", return_value=opener),
        ):
            self.assertEqual(list_models(), ["a", "b"])
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.full_url, "http://127.0.0.1:12345/v1/models")
