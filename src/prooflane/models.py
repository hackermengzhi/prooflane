"""Explicitly configured JSON model transport, independent of business profiles."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Reasoning models can spend minutes on one decision over a large task context.
MODEL_TIMEOUT = 180
MAX_RESPONSE_BYTES = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        # A provider redirect must not forward the API key or the requirement document.
        raise HTTPError(request.full_url, code, "Redirects are not allowed", headers, file)


def chat_text(result: dict) -> str:
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
        raise ValueError("Model did not return a completed JSON response")
    return choice["message"]["content"]


def responses_text(result: dict) -> str:
    # "incomplete" means truncated output, which must not be parsed as a decision.
    if result["status"] != "completed":
        raise ValueError("Model did not return a completed JSON response")
    parts = [
        part for item in result["output"] if item["type"] == "message" for part in item["content"]
    ]
    if not parts or any(part["type"] != "output_text" for part in parts):
        raise ValueError("Model did not return a completed JSON response")
    return "".join(part["text"] for part in parts)


def request_json(system_prompt: str, payload: dict) -> tuple[dict, str]:
    # Explicit environment configuration avoids silently sending documents to a provider.
    endpoint = os.environ.get("PROOFLANE_LLM_URL", "")
    model = os.environ.get("PROOFLANE_LLM_MODEL", "")
    key = os.environ.get("PROOFLANE_LLM_API_KEY", "")
    parsed = urlparse(endpoint)
    if not model or parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError(
            "Set PROOFLANE_LLM_URL (full chat/completions or responses URL) and PROOFLANE_LLM_MODEL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model URL must not contain credentials, query parameters or fragments")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote model endpoints require HTTPS")
    content = json.dumps(payload, ensure_ascii=False)
    # The URL selects the wire protocol: Responses endpoints end with /responses.
    uses_responses = parsed.path.rstrip("/").endswith("/responses")
    if uses_responses:
        body = {
            "model": model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_object"}},
            "store": False,
        }
    else:
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
    # Some provider gateways reject the default Python-urllib agent outright.
    headers = {"Content-Type": "application/json", "User-Agent": "prooflane"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(endpoint, data=json.dumps(body).encode(), headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=MODEL_TIMEOUT) as response:
            result = json.loads(response.read(MAX_RESPONSE_BYTES))
        text = responses_text(result) if uses_responses else chat_text(result)
        plan = json.loads(text)
    except HTTPError as error:
        raise RuntimeError(
            f"Model endpoint returned HTTP {error.code}; no fallback plan was used"
        ) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Model endpoint unavailable; no fallback plan was used") from None
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Model returned an invalid JSON response") from error
    if not isinstance(plan, dict):
        raise ValueError("Model must return a JSON object")
    return plan, model
