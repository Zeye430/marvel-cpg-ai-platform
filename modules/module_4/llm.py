"""
modules/module_4/llm.py
=======================
Small chat client for the copilot's language model.

Default: a local Ollama server (no API fees, nothing leaves the machine). A deployed app, where no
local model runs, uses a hosted model instead: MODULE4_LLM_PROVIDER=openai with MODULE4_LLM_API_KEY
calls OpenAI (gpt-4.1-mini by default); MODULE4_LLM_BASE_URL points it at any other OpenAI-compatible
server. Standard library only, so the app needs no SDK.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

# provider -> (default base URL, default model)
DEFAULTS = {"ollama": ("http://127.0.0.1:11434", "qwen2.5:7b"),
            "openai": ("https://api.openai.com", "gpt-4.1-mini")}
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "host.docker.internal")
RETRY_STATUS = (429, 500, 502, 503, 504)          # busy or rate limited: worth one more try
RETRY_WAIT_S = 2.0
HTTP_REASONS = {400: "request rejected", 401: "API key rejected", 403: "API key has no access to this model",
                404: "model or address not found", 429: "rate limit or usage limit reached"}


class LLMUnavailable(RuntimeError):
    """The model server could not be reached or returned an unusable reply."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b"
    timeout_s: float = 120.0
    api_key: str | None = None

    @classmethod
    def from_env(cls, environ=None) -> "LLMConfig":
        env = os.environ if environ is None else environ
        provider = env.get("MODULE4_LLM_PROVIDER", "ollama").strip().lower()
        if provider not in DEFAULTS:
            raise ValueError("MODULE4_LLM_PROVIDER must be 'ollama' or 'openai'")
        base_url, model = DEFAULTS[provider]
        return cls(
            provider=provider,
            base_url=env.get("MODULE4_LLM_BASE_URL", base_url).rstrip("/"),
            model=env.get("MODULE4_LLM_MODEL", model),
            timeout_s=float(env.get("MODULE4_LLM_TIMEOUT_S", cls.timeout_s)),
            api_key=env.get("MODULE4_LLM_API_KEY") or None,
        )

    @property
    def hosted(self) -> bool:
        """True when questions and the rows behind answers go to an outside service, not this machine."""
        return (urllib.parse.urlparse(self.base_url).hostname or "") not in LOCAL_HOSTS


class ChatModel:
    """chat(messages) -> text. Temperature 0 so the same question gives the same SQL."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()

    @property
    def name(self) -> str:
        return f"{self.config.model} ({self.config.provider})"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        if self.config.hosted and not self.config.api_key:
            raise LLMUnavailable("Language model unavailable (MODULE4_LLM_API_KEY is not set)")
        data = json.dumps(payload).encode()
        for attempt in (1, 2):
            request = urllib.request.Request(self.config.base_url + path, data=data, headers=self._headers(),
                                             method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as error:
                if error.code in RETRY_STATUS and attempt == 1:
                    time.sleep(RETRY_WAIT_S)
                    continue
                reason = HTTP_REASONS.get(error.code, "service error")
                # The provider's own message helps whoever runs the app; it never contains the key.
                log.warning("Language model call failed: HTTP %s, %s: %s", error.code, reason, _error_detail(error))
                raise LLMUnavailable(f"Language model unavailable ({reason}, HTTP {error.code})") from error
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
                # Class name only: never echo URLs, keys or payloads into the UI.
                raise LLMUnavailable(f"Language model unavailable ({type(error).__name__})") from error
        raise AssertionError("unreachable")

    def chat(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 700) -> str:
        if self.config.provider == "ollama":
            payload = {"model": self.config.model, "messages": messages, "stream": False,
                       "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192}}
            if json_mode:
                payload["format"] = "json"
            reply = self._post("/api/chat", payload)
            text = (reply.get("message") or {}).get("content")
        else:
            payload = {"model": self.config.model, "messages": messages, "temperature": 0,
                       "max_tokens": max_tokens}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            reply = self._post("/v1/chat/completions", payload)
            choices = reply.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMUnavailable("Language model returned an empty reply")
        return text.strip()

    def available(self) -> bool:
        """Cheap reachability check for the status line (does not load the model)."""
        if self.config.hosted and not self.config.api_key:
            return False
        path = "/api/tags" if self.config.provider == "ollama" else "/v1/models"
        try:
            request = urllib.request.Request(self.config.base_url + path, headers=self._headers(), method="GET")
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status == 200
        except Exception:  # noqa: BLE001 - reachability probe only
            return False


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode() or "{}")
    except Exception:  # noqa: BLE001 - best effort, for the log only
        return ""
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail or "")[:200]
