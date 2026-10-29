"""
modules/module_4/llm.py
=======================
Small chat client for the copilot's language model.

Default: a local Ollama server (no API fees, nothing leaves the machine). Any
OpenAI-compatible server can be used instead by setting MODULE4_LLM_PROVIDER=openai
and MODULE4_LLM_BASE_URL. Standard library only, so the app needs no SDK.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


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
        if provider not in ("ollama", "openai"):
            raise ValueError("MODULE4_LLM_PROVIDER must be 'ollama' or 'openai'")
        return cls(
            provider=provider,
            base_url=env.get("MODULE4_LLM_BASE_URL", cls.base_url).rstrip("/"),
            model=env.get("MODULE4_LLM_MODEL", cls.model),
            timeout_s=float(env.get("MODULE4_LLM_TIMEOUT_S", cls.timeout_s)),
            api_key=env.get("MODULE4_LLM_API_KEY") or None,
        )


class ChatModel:
    """chat(messages) -> text. Temperature 0 so the same question gives the same SQL."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()

    @property
    def name(self) -> str:
        return f"{self.config.model} ({self.config.provider})"

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(self.config.base_url + path, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            # Class name only: never echo URLs, keys or payloads into the UI.
            raise LLMUnavailable(f"Language model unavailable ({type(error).__name__})") from error

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
        path = "/api/tags" if self.config.provider == "ollama" else "/v1/models"
        try:
            request = urllib.request.Request(self.config.base_url + path, method="GET")
            if self.config.api_key:
                request.add_header("Authorization", f"Bearer {self.config.api_key}")
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status == 200
        except Exception:  # noqa: BLE001 - reachability probe only
            return False
