"""Ollama and OpenAI-compatible HTTP clients."""

from __future__ import annotations

import httpx

from .base import LLMClient, LLMError, Message


class OllamaClient(LLMClient):
    """Talks to a local Ollama server via /api/chat."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def chat(self, messages: list[Message]) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            resp = self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc


class OpenAICompatClient(LLMClient):
    """Any server exposing /v1/chat/completions."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    def chat(self, messages: list[Message]) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            resp = self._http.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"openai-compatible request failed: {exc}") from exc
