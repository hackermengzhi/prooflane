"""Explicitly configured JSON model transport, independent of business profiles."""

import json
import os
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

WIRE_ENDPOINTS = {"chat": "/chat/completions", "responses": "/responses"}
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
# Reasoning models can spend minutes on one decision over a large task context.
DEFAULT_TIMEOUT = 180
DEFAULT_MAX_RETRIES = 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 60
MAX_RESPONSE_BYTES = 1024 * 1024


class ModelOutputError(ValueError):
    """The provider answered, but not with a complete JSON object the caller can act on."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        # A provider redirect must not forward the API key or the requirement document.
        raise HTTPError(request.full_url, code, "Redirects are not allowed", headers, file)


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    wire_api: str
    model: str
    api_key: str = ""
    reasoning_effort: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES

    @property
    def endpoint(self) -> str:
        return self.base_url + WIRE_ENDPOINTS[self.wire_api]


def check_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Model URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model URL must not contain credentials, query parameters or fragments")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote model endpoints require HTTPS")
    return base_url.rstrip("/")


def settings_from_file(path: Path) -> ModelSettings:
    """Codex-style TOML: model, model_provider and [model_providers.<name>] tables."""
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    name = config.get("model_provider")
    provider = config.get("model_providers", {}).get(name)
    if not isinstance(config.get("model"), str) or not isinstance(provider, dict):
        raise ValueError("Config requires model, model_provider and its [model_providers] table")
    if provider.get("wire_api", "chat") not in WIRE_ENDPOINTS:
        raise ValueError("wire_api must be 'chat' or 'responses'")
    if "api_key" in provider:
        raise ValueError("Keep API keys out of config files; name the variable with env_key")
    env_key = provider.get("env_key")
    api_key = os.environ.get(env_key, "") if env_key else ""
    if env_key and not api_key:
        raise ValueError(f"Environment variable {env_key} named by env_key is not set")
    effort = config.get("reasoning_effort")
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning_effort must be one of {sorted(REASONING_EFFORTS)}")
    timeout = provider.get("timeout", DEFAULT_TIMEOUT)
    retries = provider.get("max_retries", DEFAULT_MAX_RETRIES)
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise ValueError("timeout must be 1..900 seconds")
    if type(retries) is not int or not 0 <= retries <= 10:
        raise ValueError("max_retries must be 0..10")
    return ModelSettings(
        base_url=check_base_url(provider.get("base_url", "")),
        wire_api=provider.get("wire_api", "chat"),
        model=config["model"],
        api_key=api_key,
        reasoning_effort=effort,
        timeout=timeout,
        max_retries=retries,
    )


def settings_from_environment() -> ModelSettings:
    endpoint = os.environ.get("PROOFLANE_LLM_URL", "")
    model = os.environ.get("PROOFLANE_LLM_MODEL", "")
    if not endpoint or not model:
        raise ValueError(
            "Set PROOFLANE_CONFIG, or PROOFLANE_LLM_URL (full chat/completions or responses URL) "
            "and PROOFLANE_LLM_MODEL"
        )
    for wire_api, suffix in WIRE_ENDPOINTS.items():
        if endpoint.rstrip("/").endswith(suffix):
            base_url = endpoint.rstrip("/").removesuffix(suffix)
            return ModelSettings(
                base_url=check_base_url(base_url),
                wire_api=wire_api,
                model=model,
                api_key=os.environ.get("PROOFLANE_LLM_API_KEY", ""),
            )
    raise ValueError("PROOFLANE_LLM_URL must end with /chat/completions or /responses")


def load_settings() -> ModelSettings:
    # Only explicitly named configuration is read, so documents never reach an unintended provider.
    config = os.environ.get("PROOFLANE_CONFIG")
    return settings_from_file(Path(config)) if config else settings_from_environment()


def request_body(settings: ModelSettings, system_prompt: str, content: str) -> dict:
    if settings.wire_api == "responses":
        body = {
            "model": settings.model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_object"}},
            "store": False,
        }
        if settings.reasoning_effort:
            body["reasoning"] = {"effort": settings.reasoning_effort}
        return body
    body = {
        "model": settings.model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    }
    if settings.reasoning_effort:
        body["reasoning_effort"] = settings.reasoning_effort
    return body


def chat_text(result: dict) -> str:
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
        raise ModelOutputError("Model did not return a completed JSON response")
    return choice["message"]["content"]


def responses_text(result: dict) -> str:
    # "incomplete" means truncated output, which must not be parsed as a decision.
    if result["status"] != "completed":
        raise ModelOutputError("Model did not return a completed JSON response")
    parts = [
        part for item in result["output"] if item["type"] == "message" for part in item["content"]
    ]
    if not parts or any(part["type"] != "output_text" for part in parts):
        raise ModelOutputError("Model did not return a completed JSON response")
    return "".join(part["text"] for part in parts)


def backoff_seconds(attempt: int, error: HTTPError | None) -> float:
    retry_after = error.headers.get("Retry-After") if error is not None else None
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), MAX_BACKOFF_SECONDS)
    return min(2 ** (attempt + 1), MAX_BACKOFF_SECONDS)


def send(settings: ModelSettings, url: str, body: dict | None = None) -> dict:
    """Call the provider, retrying rate limits, gateway failures and timeouts with backoff."""
    # Some provider gateways reject the default Python-urllib agent outright.
    headers = {"Content-Type": "application/json", "User-Agent": "prooflane"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    data = None if body is None else json.dumps(body).encode()
    for attempt in range(settings.max_retries + 1):
        retry_error = None
        try:
            request = Request(url, data=data, headers=headers)
            with build_opener(NoRedirect()).open(request, timeout=settings.timeout) as response:
                return json.loads(response.read(MAX_RESPONSE_BYTES))
        except HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt == settings.max_retries:
                raise RuntimeError(
                    f"Model endpoint returned HTTP {error.code}; no fallback plan was used"
                ) from None
            retry_error = error
        except (URLError, TimeoutError):
            if attempt == settings.max_retries:
                raise RuntimeError(
                    "Model endpoint unavailable; no fallback plan was used"
                ) from None
        time.sleep(backoff_seconds(attempt, retry_error))


def request_json(system_prompt: str, payload: dict) -> tuple[dict, str]:
    settings = load_settings()
    body = request_body(settings, system_prompt, json.dumps(payload, ensure_ascii=False))
    result = send(settings, settings.endpoint, body)
    try:
        text = responses_text(result) if settings.wire_api == "responses" else chat_text(result)
        plan = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ModelOutputError("Model returned an invalid JSON response") from error
    if not isinstance(plan, dict):
        raise ModelOutputError("Model must return a JSON object")
    return plan, settings.model


def list_models() -> list[str]:
    settings = load_settings()
    result = send(settings, settings.base_url + "/models")
    try:
        return sorted(item["id"] for item in result["data"])
    except (KeyError, TypeError) as error:
        raise ValueError("Provider returned an invalid model list") from error
